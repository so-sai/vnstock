"""opportunity_cost.py — Counterfactual measurement cho Evidence Ledger.

OC_i = R_best eligible rejected − R_executed   (cùng horizon H, cùng ngày)

Quy tắc chống hindsight bias (theo design đã chốt):
  - "best eligible" xác định tại decision-time: chỉ xét candidate có
    action ∈ {BUY, SCALE_IN} (đủ điều kiện thay thế) và p_gain >= NGƯỠNG_ELIGIBLE.
  - Không dùng return tương lai để lọc alternative (không "kể lại câu chuyện").
  - Nếu không có alternative đủ điều kiện → opportunity_cost = NULL (không ép 0).
  - Horizon cố định (mặc định 20 phiên giao dịch) cho MỌI candidate cùng lần đo.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from calibration.evidence_ledger import (
    CAPITAL_DEPLOYMENT_ACTIONS,
    get_conn,
    get_decisions,
)

# Ngưỡng p_gain tối thiểu để một candidate đủ điều kiện là "alternative".
NGUONG_ELIGIBLE = 0.50


def _hydrate_price_db() -> Path:
    _candidate = Path(sys.executable).resolve().parent
    if Path(sys.executable).stem.lower().startswith("python"):
        _p = Path(__file__).resolve().parent.parent.parent.parent
        for _par in [_p] + list(_p.parents):
            if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
                _candidate = _par
                break
    return _candidate / "backend" / "data" / "screener_cache.db"


def _get_price_conn(price_db: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(price_db or _hydrate_price_db()))
    conn.row_factory = sqlite3.Row
    return conn


HORIZONS = (20, 60, 120)


def _forward_close(conn, symbol: str, start_date: str, hold_days: int) -> float | None:
    """Close price tại phiên giao dịch thứ `hold_days` SAU start_date.

    Tính theo TRADING-DAY (không phải ngày lịch): dùng ROW_NUMBER trên
    daily_ohlcv của symbol, chọn close của phiên thứ `hold_days` sau start_date.
    """
    row = conn.execute(
        """
        WITH after_start AS (
            SELECT date, close FROM daily_ohlcv
            WHERE symbol = ? AND date > ?
            ORDER BY date
        ),
        numbered AS (
            SELECT date, close, ROW_NUMBER() OVER (ORDER BY date) AS rn
            FROM after_start
        )
        SELECT close FROM numbered WHERE rn = ?
        """,
        (symbol, start_date, hold_days),
    ).fetchone()
    return row["close"] if row else None


def resolve_outcomes(
    db_path: str | None = None,
    price_db: str | None = None,
    hold_days: int = 20,
    decision_ids: list[int] | None = None,
) -> dict:
    """Tính return_pct (H=20) + return_pct_60 + return_pct_120 + outcome_status.

    Multi-horizon: ngoài hold_days mặc định (20), luôn tính thêm H=60, H=120
    (các phiên giao dịch sau ngày quyết định). Lưu vào 3 cột riêng.
    Returns: {"n_resolved": int, "n_skipped": int, "details": [...]}
    """
    conn = get_conn(db_path)
    price = _get_price_conn(price_db)
    try:
        decisions = get_decisions(db_path)
        if decision_ids is not None:
            decisions = [d for d in decisions if d["decision_id"] in decision_ids]

        horizons = sorted(set((hold_days,) + HORIZONS))
        resolved = 0
        skipped = 0
        details = []
        for d in decisions:
            if d["outcome_status"] != "UNRESOLVED":
                continue
            entry = d.get("entry_price")
            if entry is None:
                row = price.execute(
                    "SELECT close FROM daily_ohlcv WHERE symbol=? AND date=?",
                    (d["symbol"], d["date"]),
                ).fetchone()
                if not row:
                    skipped += 1
                    continue
                entry = row["close"]
            if not entry:
                skipped += 1
                continue

            returns = {}
            exit_prices = {}
            all_found = True
            for h in horizons:
                exit_price = _forward_close(price, d["symbol"], d["date"], h)
                if exit_price is None:
                    all_found = False
                    break
                returns[str(h)] = round((exit_price - entry) / entry * 100.0, 4)
                exit_prices[str(h)] = exit_price
            if not all_found:
                skipped += 1
                continue

            main_h = str(min(horizons))
            conn.execute(
                "UPDATE decision_ledger SET entry_price=?, exit_price=?, return_pct=?, "
                "return_pct_60=?, return_pct_120=?, outcome_status='RESOLVED', resolved_at=? "
                "WHERE decision_id=?",
                (
                    entry,
                    exit_prices[main_h],
                    returns[str(20)] if "20" in returns else returns[main_h],
                    returns.get("60"),
                    returns.get("120"),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    d["decision_id"],
                ),
            )
            resolved += 1
            details.append({"decision_id": d["decision_id"], "return_pct": returns.get("20")})
        conn.commit()
        return {"n_resolved": resolved, "n_skipped": skipped, "details": details}
    finally:
        conn.close()
        price.close()


def compute_opportunity_cost(
    db_path: str | None = None,
    hold_days: int = 20,
    price_db: str | None = None,
) -> dict:
    """Tính opportunity cost cho từng EXECUTE trong ledger (multi-horizon).

    OC = R_best_eligible_rejected − R_executed (cùng ngày, cùng H).
    Tính cho H=20 (chính), H=60, H=120. Trước tiên resolve outcomes.
    """
    resolve_outcomes(db_path=db_path, price_db=price_db, hold_days=hold_days)

    decisions = get_decisions(db_path)
    by_date: dict[str, list[dict]] = {}
    for d in decisions:
        by_date.setdefault(d["date"], []).append(d)

    updated = 0
    none_no_alt = 0
    conn = get_conn(db_path)
    try:
        for date, group in by_date.items():
            # Best eligible REJECTED/WATCH alternative per horizon (decision-time eligible).
            best_alt_by_h: dict[str, dict] = {}
            for h in HORIZONS:
                best_alt_by_h[str(h)] = None
            for d in group:
                if d["decision"] not in ("WATCH", "REJECT"):
                    continue
                if (d.get("action") or "").upper() not in CAPITAL_DEPLOYMENT_ACTIONS:
                    continue
                if (d.get("p_gain") or 0.0) < NGUONG_ELIGIBLE:
                    continue
                for h in HORIZONS:
                    key = str(h)
                    r = d.get(f"return_pct_{h}" if h != 20 else "return_pct")
                    if r is None:
                        continue
                    cur = best_alt_by_h[key]
                    if cur is None or r > cur["return_pct"]:
                        best_alt_by_h[key] = {"symbol": d["symbol"], "return_pct": r, "decision_id": d["decision_id"]}

            for d in group:
                if d["decision"] != "EXECUTE":
                    continue
                r_exec = d.get("return_pct")
                if r_exec is None:
                    continue
                best20 = best_alt_by_h["20"]
                if best20 is None or best20["decision_id"] == d["decision_id"]:
                    # Không có alternative đủ điều kiện → NULL, không ép 0.
                    none_no_alt += 1
                    conn.execute(
                        "UPDATE decision_ledger SET counterfactual_symbol=NULL, "
                        "counterfactual_return=NULL, opportunity_cost=NULL, "
                        "opportunity_cost_60=NULL, opportunity_cost_120=NULL WHERE decision_id=?",
                        (d["decision_id"],),
                    )
                    continue
                oc20 = round(best20["return_pct"] - r_exec, 4)

                # OC cho H=60, H=120 (nếu cả 2 có return).
                oc60 = oc120 = None
                r60 = d.get("return_pct_60")
                r120 = d.get("return_pct_120")
                b60 = best_alt_by_h["60"]
                b120 = best_alt_by_h["120"]
                if r60 is not None and b60 is not None and b60["decision_id"] != d["decision_id"]:
                    oc60 = round(b60["return_pct"] - r60, 4)
                if r120 is not None and b120 is not None and b120["decision_id"] != d["decision_id"]:
                    oc120 = round(b120["return_pct"] - r120, 4)

                conn.execute(
                    "UPDATE decision_ledger SET counterfactual_symbol=?, "
                    "counterfactual_return=?, opportunity_cost=?, "
                    "opportunity_cost_60=?, opportunity_cost_120=? WHERE decision_id=?",
                    (best20["symbol"], best20["return_pct"], oc20, oc60, oc120, d["decision_id"]),
                )
                updated += 1
        conn.commit()
        return {"n_opportunity_cost_updated": updated, "n_no_alternative": none_no_alt}
    finally:
        conn.close()
