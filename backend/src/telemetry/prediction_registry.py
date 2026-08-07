"""Prediction Registry — append-only log of RS audit predictions + outcome tracking."""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
import src.config
from src.database.db_core import get_connection

LOG_FILENAME = "prediction_log.jsonl"
LOG_PATH = src.config.DATA_DIR / "output" / LOG_FILENAME

HORIZONS = [5, 20, 60]


def _ensure_log_file():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        with open(LOG_PATH, "w", encoding="utf-8"):
            pass


def _read_lines() -> list[dict]:
    _ensure_log_file()
    if LOG_PATH.stat().st_size == 0:
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _append_line(entry: dict):
    _ensure_log_file()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _classification_key(vi_label: str) -> str:
    mapping = {
        "ĐƯỢC XÁC NHẬN": "DUOC_XAC_NHAN",
        "DẪN DẮT ĐƠN ĐỘC": "DAN_DAT_DON_DOC",
        "TẬP TRUNG VỐN HÓA": "TAP_TRUNG_VON_HOA",
        "CHƯA ĐỦ DỮ LIỆU": "CHUA_DU_DU_LIEU",
    }
    return mapping.get(vi_label, vi_label)


def _get_price_at_date(symbol: str, target_date: str) -> float | None:
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT close FROM daily_ohlcv WHERE symbol = ? AND date = ?", (symbol, target_date)).fetchone()
            if row and row[0] is not None:
                return float(row[0])
    except Exception as e:
        logger.warning("[PR] Price lookup fail %s @ %s: %s", symbol, target_date, e)
    return None


def _get_nearest_price(symbol: str, target_date: str, before: bool = True) -> float | None:
    try:
        op = "<=" if before else ">="
        with get_connection() as conn:
            order = "DESC" if before else "ASC"
            row = conn.execute(
                f"SELECT close FROM daily_ohlcv WHERE symbol = ? AND date {op} ? ORDER BY date {order} LIMIT 1",
                (symbol, target_date),
            ).fetchone()
            if row and row[0] is not None:
                return float(row[0])
    except Exception as e:
        logger.warning("[PR] Nearest price lookup fail %s @ %s: %s", symbol, target_date, e)
    return None


def log_predictions(target_date: str | None = None) -> int:
    """Run RS audit + log today's predictions to prediction_log.jsonl."""
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    from src.engine.rs_audit import run_rs_audit

    results = run_rs_audit(top_n=9999)
    if not results:
        logger.warning("[PR] No RS audit results to log.")
        return 0

    count = 0
    for r in results:
        entry = {
            "event": "prediction",
            "date": target_date,
            "symbol": r["symbol"],
            "rs": r["rs_rating"],
            "diem_xac_nhan": r["diem_xac_nhan"],
            "phan_loai": _classification_key(r["ket_luan"]),
            "price_t0": r["price"],
            "sector": r["sector"],
            "logged_at": datetime.now().isoformat(),
        }
        _append_line(entry)
        count += 1

    logger.info("[PR] Logged %d predictions for %s", count, target_date)
    return count


def update_outcomes() -> int:
    """Find predictions old enough to evaluate, append outcome entries."""
    lines = _read_lines()

    predictions = [e for e in lines if e.get("event") == "prediction" and e.get("date")]
    existing_keys = set()
    for e in lines:
        if e.get("event") == "outcome":
            existing_keys.add((e.get("date"), e.get("symbol"), e.get("horizon")))

    today = datetime.now().strftime("%Y-%m-%d")
    appended = 0

    for p in predictions:
        p_date = p["date"]
        symbol = p["symbol"]
        price_t0 = p.get("price_t0")

        for horizon in HORIZONS:
            key = (p_date, symbol, horizon)
            if key in existing_keys:
                continue

            target_dt = datetime.strptime(p_date, "%Y-%m-%d") + timedelta(days=horizon)
            target_str = target_dt.strftime("%Y-%m-%d")
            if target_str > today:
                continue

            exit_price = _get_price_at_date(symbol, target_str)
            if exit_price is None:
                exit_price = _get_nearest_price(symbol, target_str, before=True)

            if exit_price is None or price_t0 is None or price_t0 <= 0:
                continue

            return_pct = round((exit_price - price_t0) / price_t0 * 100, 2)

            outcome = {
                "event": "outcome",
                "date": p_date,
                "symbol": symbol,
                "horizon": horizon,
                "return_pct": return_pct,
                "exit_price": exit_price,
                "evaluated_at": datetime.now().isoformat(),
            }
            _append_line(outcome)
            existing_keys.add(key)
            appended += 1

    if appended:
        logger.info("[PR] Appended %d outcome entries", appended)
    return appended


def get_registry_stats() -> dict:
    """Compute average returns by classification group."""
    lines = _read_lines()

    predictions = {}
    for e in lines:
        if e.get("event") == "prediction":
            key = (e["date"], e["symbol"])
            predictions[key] = e

    outcomes_by_pred = {}
    for e in lines:
        if e.get("event") == "outcome":
            key = (e["date"], e["symbol"])
            outcomes_by_pred.setdefault(key, {})[e["horizon"]] = e["return_pct"]

    groups = {}
    for key, pred in predictions.items():
        kl = pred.get("phan_loai", "UNKNOWN")
        if kl not in groups:
            groups[kl] = {"count": 0, "horizons": {h: [] for h in HORIZONS}}
        groups[kl]["count"] += 1
        outs = outcomes_by_pred.get(key, {})
        for h in HORIZONS:
            if h in outs:
                groups[kl]["horizons"][h].append(outs[h])

    stats = {}
    for kl, data in groups.items():
        h_stats = {}
        for h in HORIZONS:
            vals = data["horizons"][h]
            h_stats[f"return_{h}d"] = {
                "n": len(vals),
                "avg": round(sum(vals) / len(vals), 2) if vals else None,
                "max": round(max(vals), 2) if vals else None,
                "min": round(min(vals), 2) if vals else None,
            }
        stats[kl] = {
            "count": data["count"],
            **h_stats,
        }

    return stats


def get_raw_entries(limit: int = 50) -> list[dict]:
    lines = _read_lines()
    return lines[-limit:]


def run_registry_update(target_date: str | None = None):
    """Full pipeline: log predictions + update outcomes. Called from daily_updater."""
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    n_pred = log_predictions(target_date)
    n_out = update_outcomes()
    return {"predictions_logged": n_pred, "outcomes_appended": n_out}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Prediction Registry")
    parser.add_argument("action", choices=["log", "update", "stats", "list"], default="stats", nargs="?")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=20, help="Entries to show (default 20)")
    args = parser.parse_args()

    if args.action == "log":
        n = log_predictions(args.date)
        print(f"  [PR] Logged {n} predictions.")
    elif args.action == "update":
        n = update_outcomes()
        print(f"  [PR] Updated {n} outcomes.")
    elif args.action == "stats":
        s = get_registry_stats()
        print("=" * 60)
        print("  PREDICTION REGISTRY STATISTICS")
        print("=" * 60)
        for kl, data in sorted(s.items()):
            print(f"\n  [{kl}] ({data['count']} mã)")
            for h in HORIZONS:
                hk = f"return_{h}d"
                d = data[hk]
                if d["n"] > 0:
                    print(f"    {h:2d} ngày: avg={d['avg']:+.2f}%  max={d['max']:+.2f}%  min={d['min']:+.2f}%  (n={d['n']})")
                else:
                    print(f"    {h:2d} ngày: chưa có dữ liệu")
        print("=" * 60)
    elif args.action == "list":
        entries = get_raw_entries(args.limit)
        print("=" * 100)
        print(f"  PREDICTION REGISTRY — {len(entries)} entries gần nhất")
        print("=" * 100)
        for e in reversed(entries):
            if e.get("event") == "prediction":
                print(
                    f"  PREDICT {e['date']} {e['symbol']:6s} | RS={e['rs']:3d} score={e['diem_xac_nhan']:.2f} {e['phan_loai']:<20s} price={e['price_t0']:>8.1f}"
                )
            elif e.get("event") == "outcome":
                print(
                    f"  OUTCOME {e['date']} {e['symbol']:6s} | {e['horizon']:2d}ngày return={e['return_pct']:+.2f}% exit={e['exit_price']:>8.1f}"
                )
        print("=" * 100)
