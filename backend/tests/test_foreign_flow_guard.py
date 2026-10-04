"""Tests cho foreign_flow_guard (F3) — chốt chặn market_foreign_history."""

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.database.foreign_flow_guard import (  # noqa: E402
    DAILY_FLOW_FILTER,
    MAX_MARKET_NET_VALUE_TY,
    ForeignFlowViolation,
    guard_foreign_frame,
    unit_invariant_ok,
)

COLS = ["symbol", "date", "foreign_vol", "net_vol", "net_value", "close"]


def _frame(rows):
    return pd.DataFrame(rows, columns=COLS)


def test_normal_daily_frame_kept():
    price_pnl = 500 * 25000 / 1e9
    df = _frame(
        [
            ["HPG", "2026-10-01", 1000, 500, price_pnl, 25000.0],
            ["SSI", "2026-10-01", 200, -100, -100 * 30000 / 1e9, 30000.0],
        ]
    )
    clean, rep = guard_foreign_frame(df, "2026-10-01")
    assert rep["kept"] == 2
    assert len(clean) == 2
    assert abs(rep["session_total_ty"]) < MAX_MARKET_NET_VALUE_TY


def test_level_signature_raises_on_input():
    """Dữ liệu 'mức tích lũy' (legacy) phải bị chặn trước khi ghi."""
    df = _frame([[f"S{i}", "2026-06-01", 1, -387271626, -2718.6, 7020.0] for i in range(30)])
    with pytest.raises(ForeignFlowViolation):
        guard_foreign_frame(df, "2026-06-01")


def test_bad_unit_row_dropped():
    df = _frame([["XXX", "2026-10-01", 1000, 500, 999.0, 25000.0]])  # net_value lệch xa
    clean, rep = guard_foreign_frame(df, "2026-10-01")
    assert rep["dropped_unit"] == 1
    assert len(clean) == 0


def test_over_bound_row_dropped():
    df = _frame([["YYY", "2026-10-01", 1000, 500, 6000.0, 25000.0]])  # > MAX_SYMBOL_NET_VALUE_TY
    clean, rep = guard_foreign_frame(df, "2026-10-01")
    assert rep["dropped_bound"] == 1
    assert len(clean) == 0


def test_empty_frame_ok():
    clean, rep = guard_foreign_frame(_frame([]), "2026-10-01")
    assert rep["kept"] == 0


def test_unit_invariant_helper():
    assert unit_invariant_ok(500, 500 * 25000 / 1e9, 25000.0)
    assert not unit_invariant_ok(500, 500 * 25000 / 1e9 * 1000, 25000.0)  # lỗi ×1000
    assert unit_invariant_ok(500, 0.0, 0.0)  # thiếu giá → không phản biện


def test_daily_filter_constant():
    assert DAILY_FLOW_FILTER == "flow_kind = 'daily'"
