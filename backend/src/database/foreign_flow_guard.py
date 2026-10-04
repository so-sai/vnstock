"""foreign_flow_guard.py — F3: chốt chặn cho market_foreign_history (audit 2026-10-04).

Mục đích: KHÔNG để dữ liệu dạng "level" (tích lũy) hoặc sai đơn vị lọt vào bảng flow,
và bảo vệ bất biến đơn vị `net_value ≈ net_vol * close / 1e9` (tỷ đồng).

Nguyên tắc: missing > fake. Row vi phạm bị LOẠI; nếu CẢ PHIÊN vi phạm tổng mức
(level signature) → raise để pipeline dừng, KHÔNG ghi.

Dùng:
    from src.database.foreign_flow_guard import guard_foreign_frame, DAILY_FLOW_FILTER, validate_session_total

    clean, report = guard_foreign_frame(df_save, target_date)   # trước khi save
    ...
    # trong query consumer:
    f"... WHERE {DAILY_FLOW_FILTER} AND ..."
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

# ── Ngưỡng chốt chặn ─────────────────────────────────────────────
MAX_SYMBOL_NET_VALUE_TY = 5_000.0  # tỷ đồng / mã / phiên
MAX_SYMBOL_NET_VOL = 1_000_000_000  # cổ phiếu / mã / phiên
MAX_MARKET_NET_VALUE_TY = 50_000.0  # tỷ đồng / toàn thị trường / phiên
UNIT_INVARIANT_ATOL = 1e-3  # dung sai tương đối cho bất biến đơn vị (0.1%)
UNIT_INVARIANT_FLOOR = 1.0  # sàn tuyệt đối (tỷ đồng)

# Nhãn ngữ nghĩa (sinh bởi backend/scripts/migrate_foreign_flow_kind.py)
FLOW_DAILY = "daily"
FLOW_LEGACY = "legacy_level"
DAILY_FLOW_FILTER = "flow_kind = 'daily'"


class ForeignFlowViolation(Exception):
    """Dữ liệu vi phạm chốt chặn — pipeline phải dừng, không ghi."""


def unit_invariant_ok(net_vol, net_value, close) -> bool:
    """net_value (tỷ) phải ≈ net_vol * close / 1e9 (VND)."""
    try:
        nv = float(net_vol or 0)
        nval = float(net_value or 0)
        c = float(close or 0)
    except TypeError, ValueError:
        return False
    if c <= 0:
        return True  # không đủ dữ liệu để phản biện
    expected = nv * c / 1e9
    return abs(nval - expected) <= max(UNIT_INVARIANT_FLOOR, abs(expected) * UNIT_INVARIANT_ATOL)


def guard_foreign_frame(df, target_date: str | None = None):
    """Kiểm duyệt frame trước khi ghi vào market_foreign_history.

    Returns (clean_df, report). Raise ForeignFlowViolation nếu tổng phiên vi phạm.
    Row vi phạm (vượt ngưỡng / sai bất biến đơn vị) bị loại khỏi clean_df.
    """
    import pandas as pd

    report = {
        "target_date": target_date,
        "input_rows": int(len(df)) if df is not None else 0,
        "dropped_bound": 0,
        "dropped_unit": 0,
        "kept": 0,
        "session_total_ty": 0.0,
    }
    if df is None or len(df) == 0:
        return df, report

    work = df.copy()
    has_close = "close" in work.columns

    # Chặn sớm trên INPUT: bất kỳ phiên nào có tổng vượt ngưỡng đều là "level signature"
    if "net_value" in work.columns:
        raw_total = float(work["net_value"].fillna(0).sum())
        report["input_total_ty"] = raw_total
        if abs(raw_total) > MAX_MARKET_NET_VALUE_TY:
            raise ForeignFlowViolation(
                f"LEVEL_SIGNATURE target={target_date}: |Σ net_value(input)|={abs(raw_total):.1f} tỷ "
                f"> {MAX_MARKET_NET_VALUE_TY} tỷ — nghi dữ liệu mức tích lũy, TỪ CHỐI ghi"
            )

    keep_mask = []
    for _, row in work.iterrows():
        nv = row.get("net_vol")
        nval = row.get("net_value")
        ok = True
        if nval is not None and abs(float(nval)) > MAX_SYMBOL_NET_VALUE_TY:
            ok = False
            report["dropped_bound"] += 1
        elif nv is not None and abs(float(nv)) > MAX_SYMBOL_NET_VOL:
            ok = False
            report["dropped_bound"] += 1
        elif has_close and nval is not None and not unit_invariant_ok(nv, nval, row.get("close")):
            ok = False
            report["dropped_unit"] += 1
        keep_mask.append(ok)

    clean = work[pd.Series(keep_mask, index=work.index)].copy() if keep_mask else work.iloc[0:0].copy()
    report["kept"] = int(len(clean))

    total = 0.0
    if "net_value" in clean.columns and len(clean):
        total = float(clean["net_value"].fillna(0).sum())
    report["session_total_ty"] = total

    if abs(total) > MAX_MARKET_NET_VALUE_TY:
        raise ForeignFlowViolation(
            f"LEVEL_SIGNATURE target={target_date}: |Σ net_value|={abs(total):.1f} tỷ "
            f"> {MAX_MARKET_NET_VALUE_TY} tỷ — nghi dữ liệu mức tích lũy, TỪ CHỐI ghi"
        )
    if report["dropped_bound"] or report["dropped_unit"]:
        logger.warning(
            "FOREIGN_FLOW_GUARD_DROP target=%s bound=%d unit=%d kept=%d",
            target_date,
            report["dropped_bound"],
            report["dropped_unit"],
            report["kept"],
        )
    return clean, report


def validate_session_total(conn: sqlite3.Connection, date: str) -> float:
    """Kiểm tra tổng một phiên đã ghi; raise nếu là 'level signature'."""
    row = conn.execute(
        "SELECT COALESCE(SUM(net_value), 0) FROM market_foreign_history WHERE date = ? AND flow_kind = 'daily'",
        (date,),
    ).fetchone()
    total = float(row[0]) if row and row[0] is not None else 0.0
    if abs(total) > MAX_MARKET_NET_VALUE_TY:
        raise ForeignFlowViolation(f"SESSION_TOTAL_VIOLATION date={date}: {total:.1f} tỷ > {MAX_MARKET_NET_VALUE_TY} tỷ")
    return total
