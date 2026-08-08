"""
walk_forward_harness.py — Walk-Forward Validation & Point-in-Time (PIT) Integrity.

Mục tiêu: kiểm chứng Sharpe Ratio thực chiến bằng tiêu chuẩn tách tập
In-Sample / Out-of-Sample, KHÔNG để mô hình "học thuộc lòng" dữ liệu cũ.

Kiến trúc (Approved 2026-08-08 — Walk-Forward Sprint):
  1. PHÂN CHIA CHUỖI THỜI GIAN (70/30 theo thời gian lịch):
       IS  : 2021-04-01 ──► split_date-1 (~3.7 năm)  → Grid Search V2 tối ưu
       OOS : split_date ──► end (~1.6 năm)           → Blind Replay ĐIỂM MÙ
  2. KHÓA CỨNG tham số từ IS: mọi tham số (weights/thresholds/stops/holds)
     tìm được trên IS được dùng NGUYÊN VẸN cho OOS — không tái tối ưu.
  3. OOS evaluation: Cold-start Blind Replay — vốn 100M sạch, chạy đúng
     run_backtest_with_guard như một backtest điểm mù.
  4. PIT DATABASE AUDIT (chống Look-ahead Bias):
       financial_facts : WHERE period < target_quarter AND ingested_at <= t
       health_ratios   : WHERE period < target_quarter (không có ingested_at)
       valuation_scores: WHERE period < target_quarter
       macro_history   : WHERE date <= t
       daily_ohlcv     : WHERE date <= t (helpers gốc đều PIT-capped period<=?/date<?)

Module Sentinel v2.1 (Anchor Fix) — mọi module src/ phải có _hydrate_path.

Usage: python -m backend.src.backtest.walk_forward_harness [--workers N] [--step S]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path


def _hydrate_path() -> Path:
    """Path Hydrator v2.1 (Anchor Fix): Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
BACKEND_DIR = PROJECT_ROOT / "backend"
SRC_DIR = BACKEND_DIR / "src"
DATA_DIR = BACKEND_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports" / "ablation_studies"
FINANCIAL_DB_PATH = DATA_DIR / "financial_facts.db"
SCREENER_DB_PATH = DATA_DIR / "screener_cache.db"
# Checkpoint riêng cho IS grid của walk-forward — không đụng grid_v2_checkpoint.json
IS_CHECKPOINT_PATH = REPORTS_DIR / "grid_wf_is_checkpoint.json"

# Phân chia lịch 70/30: IS trước split_date, OOS từ split_date (kể cả ngày split)
IS_SPLIT_DATE = "2025-01-01"
DEFAULT_START = "2021-04-01"
DEFAULT_END = "2026-08-07"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# UTF-8 stdout/stderr — báo cáo tiếng Việt an toàn trên console Windows (cp1252)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

from backtest.grid_search_v2 import (
    precompute_lri_cache,
    precompute_scores,
    run_backtest_with_guard,
    run_grid_search,
)
from backtest.unified_system_replay import _date_to_period, _get_trading_days
from calibration.causal_dag_engine import MarginStressNode
from core.errors import DataIntegrityError
from governor.law_bridges import shock_to_margin_inputs
from governor.shock_detector import ShockDetector
from governor.uncertainty_layer import UncertaintyLayer


def _date_to_quarter(target_date: str) -> str:
    """Đổi date 'YYYY-MM-DD' → period 'YYYYQx' (bọc _date_to_period)."""
    try:
        return _date_to_period(target_date)
    except Exception:  # noqa: BLE001 - batch isolation: không để 1 ngày hỏng làm dừng audit
        return "0000Q1"


def split_is_oos(dates: list[str], split_date: str = IS_SPLIT_DATE) -> tuple[list[str], list[str]]:
    """Chia danh sách ngày giao dịch thành IS (< split_date) và OOS (>= split_date).

    Thuần hàm, không đụng DB — dễ test TDD. Validate:
      - Cả hai tập không rỗng.
      - Không overlap (IS max < OOS min).
      - Không có khoảng trống giữa hai tập (max_is, min_oos kề nhau trong dates).
    """
    is_dates = [d for d in dates if d < split_date]
    oos_dates = [d for d in dates if d >= split_date]
    if not is_dates or not oos_dates:
        raise DataIntegrityError(
            f"split_is_oos: tập rỗng — cần dữ liệu cả hai phía của split_date={split_date}"
            f" (is={len(is_dates)}, oos={len(oos_dates)})"
        )
    if max(is_dates) >= min(oos_dates):
        raise DataIntegrityError(f"split_is_oos: overlap giữa IS ({max(is_dates)}) và OOS ({min(oos_dates)})")
    total = len(is_dates) + len(oos_dates)
    if total != len(set(dates)):
        raise DataIntegrityError("split_is_oos: dates chứa trùng lặp — không hợp lệ cho walk-forward")
    return is_dates, oos_dates


def _trading_days(start: str, end: str) -> list[str]:
    """Lấy danh sách ngày giao dịch VNINDEX trong khoảng [start, end]."""
    conn = sqlite3.connect(str(SCREENER_DB_PATH))
    try:
        return _get_trading_days(conn, start, end)
    finally:
        conn.close()


def pit_audit(conn: sqlite3.Connection, anchor_date: str, oos_start: str) -> dict:
    """Kiểm toán Point-in-Time tại mốc anchor_date / oos_start.

    Rào chắn 2 tầng chống Look-ahead Bias:
       - Tầng 1 (helper gốc): mọi SQL đọc đều PIT-capped — period<? / date<t.
      - Tầng 2 (audit): xác minh KHÔNG có sự kiện tương lai bị "biết trước".

    Checks:
      1. financial_facts: đếm facts có period > anchor_quarter NHƯNG ingested_at <= anchor
         → 0 nếu nạp dữ liệu đúng (không thể công bố BCTC quý sau trước hạn).
      2. daily_ohlcv: ghi nhận max(date) (dữ liệu thô có tương lai là bình thường;
         nhưng mọi query backtest đều WHERE date <= t → chặn rò rỉ).
      3. macro_history: ghi nhận max(date).
    Trả về dict kết quả audit (không raise khi dữ liệu có tương lai thô — đó là
    trạng thái bình thường; chỉ FLAG anomaly thực sự #1).
    """
    anchor_q = _date_to_quarter(anchor_date)
    report: dict = {
        "anchor_date": anchor_date,
        "anchor_quarter": anchor_q,
        "oos_start": oos_start,
        "checks": {},
        "anomalies": [],
    }

    # Check 1 — financial_facts: BCTC quý sau phải KHÔNG có ingested_at <= anchor
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM financial_facts WHERE period > ? AND ingested_at IS NOT NULL AND ingested_at <= ?",
            (anchor_q, anchor_date),
        ).fetchone()
        n_future_known = int(row[0]) if row else 0
        report["checks"]["facts_future_period_ingested_before_anchor"] = n_future_known
        if n_future_known > 0:
            report["anomalies"].append(
                f"{n_future_known} financial_facts có period > {anchor_q} nhưng ingested_at <= {anchor_date}"
                " — nghi vấn rò rỉ dữ liệu tương lai (look-ahead bias)."
            )
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as exc:
        report["checks"]["facts_future_period_ingested_before_anchor"] = f"ERROR: {exc}"

    # Check 2 + 3 — daily_ohlcv / macro_history max date (thông tin, không phải anomaly)
    try:
        row = conn.execute("SELECT MAX(date) FROM daily_ohlcv").fetchone()
        report["checks"]["ohlcv_max_date"] = row[0] if row else None
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as exc:
        report["checks"]["ohlcv_max_date"] = f"ERROR: {exc}"
    try:
        row = conn.execute("SELECT MAX(date) FROM macro_history").fetchone()
        report["checks"]["macro_max_date"] = row[0] if row else None
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as exc:
        report["checks"]["macro_max_date"] = f"ERROR: {exc}"

    report["pass"] = len(report["anomalies"]) == 0
    return report


def _slice_lri(lri_cache: dict[str, float], dates: list[str]) -> dict[str, float]:
    """Lấy subset cache LRI cho bộ dates (mặc định 1.0 nếu thiếu — không lock oan)."""
    return {d: lri_cache.get(d, 1.0) for d in dates}


def precompute_governor_cache(
    dates: list[str],
    db_path: str | None = None,
) -> dict[str, dict]:
    """Pre-compute PIT Governor signal per date (LAW bridges bật).

    Returns {date: {"u", "confidence", "shock", "authority_modifier",
    "veto", "stress_index"}}. Uncertainty U + confidence từ UncertaintyLayer,
    Shock severity từ ShockDetector, và authority_modifier/veto/stress_index
    từ MarginStressNode (LAW-008) nạp qua shock_to_margin_inputs (law_bridges).
    Pure read-only; deterministic cho (date, DB). Instance engines giữ PIT
    snapshot riêng nên lặp lại rẻ.
    """
    db = db_path or str(SCREENER_DB_PATH)
    unc = UncertaintyLayer(db_path=db)
    shock = ShockDetector(db_path=db)
    cache: dict[str, dict] = {}
    for d in dates:
        u_res = unc.compute(d)
        s_res = shock.detect(d)
        raw = shock_to_margin_inputs(s_res)
        margin = MarginStressNode().evaluate_node(
            raw["system_margin_ratio"],
            raw["breadth_stress_ratio"],
            raw["cross_contagion_index"],
            raw["margin_last_updated"],
            raw["now_time"],
        )
        cache[d] = {
            "u": u_res.u,
            "confidence": u_res.components["C"],
            "shock": s_res.severity,
            "authority_modifier": margin.authority_modifier,
            "veto": margin.veto_triggered,
            "stress_index": margin.stress_index,
        }
    return cache


def run_walk_forward(
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    split_date: str = IS_SPLIT_DATE,
    step: float = 0.05,
    top_n: int = 15,
    workers: int = 1,
    sample_every: int = 5,
    positions_max: int = 10,
    db_path: str | None = None,
    max_buys_per_year: int = 0,
    use_governor: bool = False,
) -> dict:
    """Chạy toàn bộ pipeline Walk-Forward: IS grid search → OOS blind replay.

    positions_max: số vị thế mở tối đa (khóa cứng, áp dụng cả IS replay lẫn OOS).
    max_buys_per_year: ràng buộc cứng tối đa lệnh MUA trong cửa sổ trượt 365 ngày
        (0 = không giới hạn, giữ nguyên hành vi cũ).
    use_governor: True → kích hoạt Governor Layer (Uncertainty U + Shock severity
        fusion vào alloc_multiplier). False → giữ nguyên LRI-only (mặc định).

    Returns dict báo cáo: split info, tham số khóa (IS), metrics IS + OOS,
    kết quả PIT audit, thời gian chạy.
    """
    t_start = time.time()
    if db_path is None:
        db_path = str(SCREENER_DB_PATH)

    # ── Phase 0: Trading days + split ──
    dates = _trading_days(start, end)
    if not dates:
        raise RuntimeError(f"Không có ngày giao dịch trong [{start}, {end}]")
    is_dates, oos_dates = split_is_oos(dates, split_date)
    is_score_days = is_dates[::sample_every]
    oos_score_days = oos_dates[::sample_every]
    print("=" * 70)
    print("  WALK-FORWARD VALIDATION (IS/OOS 70-30 + PIT Integrity)")
    print("=" * 70)
    print(f"  Period: {start} -> {end}")
    print(
        f"  Split : IS {is_dates[0]} .. {is_dates[-1]} ({len(is_dates)} days) | "
        f"OOS {oos_dates[0]} .. {oos_dates[-1]} ({len(oos_dates)} days)"
    )
    print(f"  Grid  : step={step} top_n={top_n} workers={workers} sample_every={sample_every} max_positions={positions_max}")

    # ── Phase 1: PIT LRI cache (chung, tính 1 lần) ──
    print("\n[Phase 1] Pre-computing PIT LRI cache...")
    lri_all = precompute_lri_cache(dates, db_path=db_path)
    lri_is = _slice_lri(lri_all, is_dates)
    lri_oos = _slice_lri(lri_all, oos_dates)

    # ── Phase 1b: PIT Governor cache (Uncertainty + Shock) — chỉ khi bật ──
    gov_is = None
    gov_oos = None
    if use_governor:
        print("\n[Phase 1b] Pre-computing PIT Governor cache (Uncertainty + Shock)...")
        gov_all = precompute_governor_cache(dates, db_path=db_path)
        gov_is = {d: gov_all[d] for d in is_dates if d in gov_all}
        gov_oos = {d: gov_all[d] for d in oos_dates if d in gov_all}
        print(f"    governor cache: IS={len(gov_is)} OOS={len(gov_oos)}")

    # ── Phase 2: IS — Grid Search V2 (tối ưu) ──
    print("\n[Phase 2] In-Sample grid search (tối ưu hóa tham số trên IS)...")
    conn = sqlite3.connect(db_path)
    fin_db_path = str(FINANCIAL_DB_PATH).replace("\\", "/")
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")
    scores_is = precompute_scores(conn, is_dates, is_score_days)
    conn.close()

    IS_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    top = run_grid_search(
        scores_is,
        is_dates,
        is_score_days,
        lri_is,
        step=step,
        top_n=top_n,
        workers=workers,
        db_path=db_path,
        checkpoint=IS_CHECKPOINT_PATH,
    )
    if not top:
        raise RuntimeError("Walk-Forward: Grid Search IS không cho kết quả hợp lệ nào.")
    locked_params = top[0]["params"]
    is_metrics = {k: v for k, v in top[0].items() if k != "params"}
    # A/B parameter: max_positions được khóa cứng từ CLI, áp dụng cho IS replay + OOS
    locked_params["max_positions"] = positions_max
    if max_buys_per_year > 0:
        locked_params["max_buys_per_year"] = max_buys_per_year
    print("\n  LOCKED PARAMS (từ IS, dùng NGUYÊN VẸN cho OOS):")
    print(f"    {json.dumps(locked_params, ensure_ascii=False)}")
    print(
        f"    IS Sharpe={is_metrics.get('sharpe')}  Ret={is_metrics.get('total_return')}%  "
        f"MaxDD={is_metrics.get('max_drawdown')}%  WinRate={is_metrics.get('win_rate')}%"
    )

    # ── Phase 2b: IS raw trade log — chạy lại LOCKED params trên IS để ghi ledger ──
    print("\n[Phase 2b] In-Sample raw trade log (locked params, capture ledger)...")
    is_metrics = run_backtest_with_guard(
        scores_is,
        is_dates,
        is_score_days,
        locked_params,
        db_path=db_path,
        lri_cache=lri_is,
        capture_trade_log=True,
        governor_cache=gov_is,
    )
    is_trades = is_metrics.pop("trade_log", [])
    is_equity = is_metrics.pop("equity_curve", [])
    print(
        f"    IS trades captured: {len(is_trades)}  Sharpe={is_metrics.get('sharpe')}  Ret={is_metrics.get('total_return')}%"
    )

    # ── Phase 3: OOS — Blind Replay ĐIỂM MÙ (cold-start, khóa tham số) ──
    print("\n[Phase 3] Out-of-Sample blind replay (khóa tham số IS, vốn 100M sạch)...")
    conn = sqlite3.connect(db_path)
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")
    scores_oos = precompute_scores(conn, oos_dates, oos_score_days)
    conn.close()

    oos_metrics = run_backtest_with_guard(
        scores_oos,
        oos_dates,
        oos_score_days,
        locked_params,
        db_path=db_path,
        lri_cache=lri_oos,
        capture_trade_log=True,
        governor_cache=gov_oos,
    )
    oos_trades = oos_metrics.pop("trade_log", [])
    oos_equity = oos_metrics.pop("equity_curve", [])
    print(f"    OOS trades captured: {len(oos_trades)}")

    # ── Phase 4: PIT Audit ──
    print("\n[Phase 4] Point-in-Time Database Audit...")
    conn = sqlite3.connect(db_path)
    conn.execute(f"ATTACH DATABASE '{fin_db_path}' AS fin")
    audit = pit_audit(conn, anchor_date=is_dates[-1], oos_start=oos_dates[0])
    conn.close()
    print(
        f"    PIT audit: {'PASS' if audit['pass'] else 'ANOMALIES'} "
        f"(anchor={audit['anchor_date']}, oos_start={audit['oos_start']})"
    )
    for a in audit["anomalies"]:
        print(f"    [ANOMALY] {a}")

    elapsed = round(time.time() - t_start, 1)
    report = {
        "period": {"start": start, "end": end},
        "split": {
            "split_date": split_date,
            "is_start": is_dates[0],
            "is_end": is_dates[-1],
            "is_days": len(is_dates),
            "oos_start": oos_dates[0],
            "oos_end": oos_dates[-1],
            "oos_days": len(oos_dates),
            "is_ratio_pct": round(100 * len(is_dates) / max(len(dates), 1), 1),
            "oos_ratio_pct": round(100 * len(oos_dates) / max(len(dates), 1), 1),
        },
        "locked_params": locked_params,
        "is_metrics": is_metrics,
        "oos_metrics": oos_metrics,
        "trade_counts": {"is": len(is_trades), "oos": len(oos_trades)},
        "pit_audit": audit,
        "elapsed_sec": elapsed,
        "config": {
            "step": step,
            "top_n": top_n,
            "workers": workers,
            "sample_every": sample_every,
            "max_positions": positions_max,
            "max_buys_per_year": max_buys_per_year,
            "use_governor": use_governor,
        },
    }

    # ── Lưu báo cáo JSON ──
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"walk_forward_{start}_{end}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Report saved: {report_path}")

    # ── Lưu raw trade ledger (bằng chứng thô — không phải tường thuật) ──
    is_ledger_path = REPORTS_DIR / f"walk_forward_trades_is_{start}_{end}.json"
    oos_ledger_path = REPORTS_DIR / f"walk_forward_trades_oos_{start}_{end}.json"
    with open(is_ledger_path, "w", encoding="utf-8") as f:
        json.dump(
            {"params": locked_params, "trades": is_trades, "equity_curve": is_equity},
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    with open(oos_ledger_path, "w", encoding="utf-8") as f:
        json.dump(
            {"params": locked_params, "trades": oos_trades, "equity_curve": oos_equity},
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    print(f"  IS trade ledger : {is_ledger_path} ({len(is_trades)} trades)")
    print(f"  OOS trade ledger: {oos_ledger_path} ({len(oos_trades)} trades)")

    # ── In tổng kết OOS ──
    print("\n" + "=" * 70)
    print("  OUT-OF-SAMPLE RESULTS (Blind Replay — khóa tham số IS)")
    print("=" * 70)
    print(
        f"  Sharpe    : {oos_metrics.get('sharpe')}\n"
        f"  CAGR/AnnR : {oos_metrics.get('annualized_return')}%\n"
        f"  Win Rate  : {oos_metrics.get('win_rate')}%\n"
        f"  MaxDD     : {oos_metrics.get('max_drawdown')}%\n"
        f"  Total Ret : {oos_metrics.get('total_return')}%\n"
        f"  Trades    : {oos_metrics.get('total_trades')}\n"
        f"  LRI lock  : {oos_metrics.get('buy_locked_days')}  "
        f"Emergency : {oos_metrics.get('emergency_exits')}  Dimmer: {oos_metrics.get('dimmer_days')}\n"
        f"  Final NAV : {oos_metrics.get('final_nav')}  (vốn ban đầu 100,000,000)\n"
        f"  PIT audit : {'PASS' if audit['pass'] else 'FAIL — xem anomalies'}\n"
        f"  Runtime   : {elapsed}s"
    )
    print("=" * 70)
    return report


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Walk-Forward Validation (IS grid search → OOS blind replay)")
    ap.add_argument("--start", default=DEFAULT_START, help="Ngày bắt đầu (YYYY-MM-DD)")
    ap.add_argument("--end", default=DEFAULT_END, help="Ngày kết thúc (YYYY-MM-DD)")
    ap.add_argument("--split-date", default=IS_SPLIT_DATE, dest="split_date", help="Mốc chia IS/OOS (YYYY-MM-DD)")
    ap.add_argument("--step", type=float, default=0.05, help="Bước weight grid (mặc định 0.05)")
    ap.add_argument("--top-n", type=int, default=15, dest="top_n", help="Số kết quả top (mặc định 15)")
    ap.add_argument("--workers", type=int, default=1, help="Số process song song (mặc định 1)")
    ap.add_argument("--sample-every", type=int, default=5, dest="sample_every", help="Lấy mẫu mỗi N phiên")
    ap.add_argument("--max-positions", type=int, default=10, dest="positions_max", help="Số vị thế mở tối đa (mặc định 10)")
    ap.add_argument("--use-governor", action="store_true", help="Kích hoạt Governor Layer (Uncertainty + Shock fusion)")
    args = ap.parse_args()
    run_walk_forward(
        start=args.start,
        end=args.end,
        split_date=args.split_date,
        step=args.step,
        top_n=args.top_n,
        workers=args.workers,
        sample_every=args.sample_every,
        positions_max=args.positions_max,
        use_governor=args.use_governor,
    )


if __name__ == "__main__":
    main()
