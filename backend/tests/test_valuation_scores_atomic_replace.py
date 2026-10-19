"""Test atomic replace per-symbol của `valuation_scores` (chống GHOST ROW).

[WHY]
  Sau khi vá vị từ giá (commit `c1df061`), `_get_price_with_flag("DDG","2026-06-30")` trả `None` — engine
  KHÔNG THỂ sinh lại hàng ratio kỳ đó. Nhưng đo trên bảng thật vẫn thấy `DDG 2026Q2 / PB = 226,18` với
  `price = 800.000` (dòng đã cách ly) và 3 dòng của `BII` (2020Q1 PB + EV_EBITDA, 2023Q4 PB) có cùng tính
  chất: dòng engine không thể sinh lại nhưng vẫn nằm trong DB.

[ROOT CAUSE]
  `compute_valuation()` chỉ `INSERT OR REPLACE` mà KHÔNG `DELETE`: kỳ nào engine mới bỏ (vì `_no_price`)
  thì hàng cũ không bị ghi đè (không có khoá để ghi đè) ⇒ tồn tại vĩnh viễn như "xác chết" của lần chạy cũ.

[INVARIANT]
  1. Recompute = THAY THẾ: trước khi ghi điểm của một mã, mọi dòng cũ của mã đó phải bị xoá TRONG CÙNG
     transaction (DELETE đứng trước INSERT đầu tiên).
  2. Xoá đúng phạm vi: chỉ dòng của mã được tính lại, không đụng mã khác.
  3. Ca `NO_PRICE` cũng phải dọn ghost (mã không còn kỳ nào tính được).
"""

from __future__ import annotations

import inspect
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.financial.valuation_engine import ValuationEngine


@pytest.fixture(scope="module")
def engine() -> ValuationEngine:
    return ValuationEngine()


@pytest.fixture()
def mem() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE valuation_scores (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL,"
        " price REAL, PRIMARY KEY (symbol, period, ratio_name))"
    )
    for sym, per, rn, rv in [
        ("DDG", "2026Q2", "PB", 226.18),
        ("DDG", "2026Q1", "PB", 209.35),
        ("BII", "2020Q1", "EV_EBITDA", 86455.99),
        ("HPG", "2024Q2", "PE", 9.1),
    ]:
        c.execute("INSERT INTO valuation_scores VALUES (?,?,?,?,?)", (sym, per, rn, rv, None))
    c.commit()
    return c


# [WHY] Bất biến phạm vi: xoá điểm cũ của ĐÚNG mã được tính lại, không được xoá lan (một lệnh DELETE thiếu
# điều kiện sẽ làm mất toàn bộ bảng 58.889 dòng).
def test_clear_scores_chi_xoa_dung_ma(engine, mem):
    assert engine._clear_scores(mem, "DDG") == 2
    mem.commit()
    left = {r[0] for r in mem.execute("SELECT DISTINCT symbol FROM valuation_scores")}
    assert left == {"BII", "HPG"}, left


# [WHY] Chống tái diễn đúng lớp lỗi ghost: DELETE phải nằm TRƯỚC INSERT đầu tiên trong nguồn của
# `compute_valuation` — nếu ai đó đảo thứ tự hoặc gỡ DELETE, test này đỏ ngay (không cần DB thật).
def test_delete_dung_truoc_insert_trong_nguon(engine):
    src = inspect.getsource(ValuationEngine.compute_valuation)
    d = src.find("_clear_scores(conn")
    i = src.find("INSERT OR REPLACE INTO valuation_scores")
    assert d != -1, "compute_valuation không còn dọn ghost ⇒ GHOST ROW sẽ tái xuất"
    assert i != -1, "không tìm thấy INSERT (đổi cơ chế ghi?) — cập nhật test"
    assert d < i, "DELETE phải đứng TRƯỚC INSERT (cùng transaction, thay thế chứ không upsert)"


# [WHY] Ca NO_PRICE: mã không còn kỳ tính được vẫn phải dọn ghost — nếu chỉ xử lý nhánh có kết quả thì
# đúng loại "xác chết" này tồn tại vĩnh viễn (DDG/BII là ca thật).
def test_nhanh_no_price_co_don_ghost(engine):
    src = inspect.getsource(ValuationEngine.compute_valuation)
    head = src.split("for rname in ratio_names")[0]
    assert "_clear_scores(_c" in head, "nhánh NO_PRICE chưa dọn ghost"
