"""Test `vn20_quant_filter` annual verification PHẢI là point-in-time (Hạng mục C, tầng consumer).

[WHY]
  `_annual_verification()` từng đọc bằng **hằng số tay** `_VERIFY_YEAR = 2025`:
  `SELECT verification_status FROM financial_facts_annual WHERE symbol=? AND fiscal_year=?`
  Hai hệ quả:
    (1) Backtest look-ahead — chạy lại kỳ 2021 vẫn dùng cờ xác minh của FY2025 (dữ liệu chưa tồn tại
        tại thời điểm đó).
    (2) "Veto ngầm" khi sang năm tài chính mới — FY2026 có cờ nhưng hằng số không ai bump ⇒ mọi mã
        trả '' ⇒ chặn toàn bộ bộ lọc, im lặng, không log.

[ROOT CAUSE]
  Bảng `financial_facts_annual` trước 2026-10-19 **không có cột ngày hiệu lực** nên consumer không có
  cách nào biết "kỳ nào đã công bố tính đến ngày X" ⇒ phải dựa vào một hằng số biên dịch.

[INVARIANT]
  1. Không còn hằng số năm: chỉ có `effective_date <= target_date`.
  2. Chỉ xét **năm tài chính mới nhất ĐÃ công bố** tính đến target; trong năm đó MISMATCH thắng VERIFIED
     (giữ nguyên hợp đồng cũ) — TUYỆT ĐỐI không `LIMIT 1` (lấy đại 1 metric ⇒ MISMATCH có thể bị nuốt).
  3. Không có kỳ nào đã công bố (hoặc thiếu bảng/cột) ⇒ '' (fail-closed, veto).
  4. `target_date` mặc định suy ra từ `periods` (max của ngày kết thúc kỳ) để backtest không thể vô tình
     look-ahead; live-run dùng chính kỳ mới nhất đã báo cáo.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.quant import vn20_quant_filter as vf

V = vf.VERIFIED_STATUS
M = vf.MISMATCH_STATUS


def make_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE health_ratios (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)")
    c.execute("CREATE TABLE financial_facts (symbol TEXT, period TEXT, metric TEXT, value REAL)")
    c.execute(
        "CREATE TABLE financial_facts_annual (symbol TEXT, fiscal_year INTEGER, statement_type TEXT,"
        " metric TEXT, value REAL, source TEXT, verification_status TEXT DEFAULT '',"
        " effective_date TEXT, effective_date_basis TEXT)"
    )
    return c


def seed(c, symbol: str, year: int, eff: str, status: str, metrics=("REVENUE", "NET_INCOME")) -> None:
    for m in metrics:
        c.execute(
            "INSERT INTO financial_facts_annual (symbol, fiscal_year, statement_type, metric, value,"
            " verification_status, effective_date, effective_date_basis)"
            " VALUES (?,?,?,?,1,?,?,'STATUTORY_LAG')",
            (symbol, year, "IS", m, status, eff),
        )
    c.commit()


def status_at(conn, symbol: str, target: str) -> str:
    return vf._annual_verification(conn, symbol, target_date=target)


# [WHY] Ca biên năm nhuận (FY2023 → 2024-03-30): ngày 29/03/2024 thị trường CHƯA được biết BCTC 2023 ⇒
# phải dùng FY2022. Nếu trả FY2023 ở đây là look-ahead 1 ngày — đủ để một backtest "ăn" thông tin chưa có.
def test_fy2023_nam_nhuan_chua_mo_khoa_ngay_29_03():
    c = make_conn()
    seed(c, "AAA", 2022, "2023-03-31", V)
    seed(c, "AAA", 2023, "2024-03-30", V)
    assert status_at(c, "AAA", "2024-03-29") == V  # của FY2022
    rows = c.execute(
        "SELECT MAX(fiscal_year) FROM financial_facts_annual WHERE effective_date <= '2024-03-29'"
    ).fetchone()[0]
    assert rows == 2022


# [WHY] Đúng ngày 30/03/2024 (90 ngày, năm nhuận) FY2023 mở khoá ⇒ ca này khoá bất biến "+90 ngày" của
# Hạng mục C vào tầng consumer: nếu ai đổi sang 31/03, test đỏ ngay.
def test_fy2023_nam_nhuan_mo_khoa_dung_ngay_30_03():
    c = make_conn()
    seed(c, "AAA", 2022, "2023-03-31", V)
    seed(c, "AAA", 2023, "2024-03-30", V)
    seed(c, "AAA", 2022, "x", "")  # nhiễu: bản ghi trạng thái rỗng của năm cũ không được chen vào
    assert status_at(c, "AAA", "2024-03-30") == V
    newest = c.execute(
        "SELECT MAX(fiscal_year) FROM financial_facts_annual WHERE effective_date <= '2024-03-30'"
    ).fetchone()[0]
    assert newest == 2023


# [WHY] Năm thường (FY2024 → 2025-03-31): 30/03/2025 vẫn là FY2023, 31/03 mới là FY2024. Cặp này chứng
# minh quy tắc không phải "đầu năm sau là thấy" mà đúng 90 ngày.
def test_fy2024_nam_thuong_bien_30_va_31_03():
    c = make_conn()
    seed(c, "BBB", 2023, "2024-03-30", V)
    seed(c, "BBB", 2024, "2025-03-31", V)
    assert status_at(c, "BBB", "2025-03-30") == V
    assert (
        c.execute("SELECT MAX(fiscal_year) FROM financial_facts_annual WHERE effective_date <= '2025-03-30'").fetchone()[0]
        == 2023
    )
    assert (
        c.execute("SELECT MAX(fiscal_year) FROM financial_facts_annual WHERE effective_date <= '2025-03-31'").fetchone()[0]
        == 2024
    )


# [WHY] BẪY `LIMIT 1`: năm mới nhất có 1 metric VERIFIED + 1 metric MISMATCH ⇒ phải trả MISMATCH (veto).
# Nếu ai "tối ưu" truy vấn thành `ORDER BY ... LIMIT 1` thì tuỳ dòng rơi ra mà MISMATCH bị nuốt = fail-open.
def test_mismatch_trong_nam_moi_nhat_thang_verified():
    c = make_conn()
    seed(c, "CCC", 2024, "2025-03-31", V, metrics=("REVENUE",))
    seed(c, "CCC", 2024, "2025-03-31", M, metrics=("NET_INCOME",))
    assert status_at(c, "CCC", "2026-01-01") == M


# [WHY] Kỳ cũ hơn bị MISMATCH không được veto kỳ mới hơn đã VERIFIED — hợp đồng cũ chỉ xét MỘT năm; nay
# là "năm mới nhất đã công bố". Nếu test này đỏ thì việc sửa sẽ chặn oan mọi mã từng có lỗi lịch sử.
def test_mismatch_nam_cu_khong_veto_nam_moi_da_verified():
    c = make_conn()
    seed(c, "DDD", 2023, "2024-03-30", M)
    seed(c, "DDD", 2024, "2025-03-31", V)
    assert status_at(c, "DDD", "2026-01-01") == V
    assert status_at(c, "DDD", "2024-12-31") == M  # tại thời điểm đó năm mới nhất đã công bố là FY2023


# [WHY] Chưa có kỳ nào công bố tính đến target ⇒ '' (veto ngay). Đây là fail-closed: không được coi
# "chưa có dữ liệu" là "đạt chuẩn".
def test_chua_co_ky_nao_cong_bo_thi_veto():
    c = make_conn()
    seed(c, "EEE", 2024, "2025-03-31", V)
    assert status_at(c, "EEE", "2025-01-01") == ""


# [WHY] Thiếu bảng/cột (DB chưa migrate) ⇒ '' chứ không được nổ exception giữa pipeline. Giữ nguyên
# hợp đồng cũ (broad except → fail-closed) nhưng nay là hệ quả của cột ngày, không phải hằng số năm.
def test_thieu_cot_effective_date_thi_fail_closed():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE financial_facts_annual (symbol TEXT, fiscal_year INTEGER, verification_status TEXT)")
    c.execute("INSERT INTO financial_facts_annual VALUES ('FFF', 2024, ?)", (V,))
    assert status_at(c, "FFF", "2026-01-01") == ""


# [WHY] Hằng số tay phải BIẾN MẤT — nếu còn, nó lại là nguồn veto ngầm khi sang năm tài chính mới.
def test_khong_con_hang_so_verify_year():
    assert not hasattr(vf, "_VERIFY_YEAR")


# [WHY] Mặc định an toàn cho backtest: không truyền target_date thì suy từ `periods` (max ngày kết thúc kỳ),
# KHÔNG dùng "hôm nay" — nếu dùng hôm nay thì backtest kỳ 2024 sẽ thấy FY2025 (look-ahead im lặng).
def test_mac_dinh_target_suy_tu_periods_khong_phai_hom_nay():
    c = make_conn()
    seed(c, "GGG", 2024, "2025-03-31", V)
    assert vf._annual_verification(c, "GGG", periods=["2024Q1"]) == ""  # 2024-03-31 < 2025-03-31
    assert vf._annual_verification(c, "GGG", periods=["2025Q2"]) == V  # 2025-06-30 ≥ 2025-03-31


# [WHY] Tích hợp: tier1 phải dùng đúng cờ của kỳ đã công bố tại thời điểm periods — chứng minh tham số
# target_date thực sự đi tới tầng quyết định, không chỉ tồn tại ở hàm phụ.
def test_tier1_dung_target_date_truyen_vao():
    c = make_conn()
    seed(c, "HHH", 2022, "2023-03-31", V)
    seed(c, "HHH", 2023, "2024-03-30", M)
    r = vf.tier1_buffett_quality(c, "HHH", "STANDARD", ["2023Q4"], target_date="2024-03-29")
    assert r["verification_status"] == V
    r2 = vf.tier1_buffett_quality(c, "HHH", "STANDARD", ["2024Q1"], target_date="2024-03-30")
    assert r2["verification_status"] == M
    assert r2["pass"] is False
    assert any("VETO_UNVERIFIED_DATA" in x for x in r2["reasons"])
