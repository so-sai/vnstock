"""Test `migrate_annual_effective_date.py` — gán `effective_date` cho `financial_facts_annual`.

[WHY]
  `financial_facts_annual` (28.730 dòng, 693 mã, FY2009–2025) **không có** `effective_date` ⇒ bảng BCTC Năm
  không PIT được. Consumer thật: `quant/vn20_quant_filter._annual_verification()` đọc theo `fiscal_year`
  cố định (`_VERIFY_YEAR = 2025`), không có khái niệm ngày ⇒ không thể gác PIT và sẽ "veto ngầm" khi sang
  năm tài chính mới (mọi mã trả `''`).

[ROOT CAUSE]
  Hạng mục A mới chỉ gán `effective_date` cho bảng QUÝ (`financial_facts`, 653.764 dòng). Bảng năm chưa
  được migrate nên không có mốc thời gian hợp pháp.

[INVARIANT]
  1. Công thức = `31/12 của fiscal_year + 90 ngày` (TT96/2020: 90 ngày kể từ ngày kết thúc năm tài chính),
     **dùng lại `LAG_DAYS[4]` của migration quý** — một luật duy nhất cho cả hai bảng.
     Hệ quả bắt buộc: năm nhuận (2012/2016/2020/2024) ⇒ **30/03**, năm thường ⇒ **31/03**.
  2. `effective_date > period_end` cho 100% dòng; không NULL; format ISO.
  3. Idempotent: chạy lại không đổi giá trị, không nhân đôi cột.
  4. Fail-closed: `fiscal_year` NULL/dị thường ⇒ LỖI và **không ghi gì** (không đoán năm).
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "migrate_annual_effective_date",
    Path(__file__).resolve().parents[1] / "scripts" / "migrate_annual_effective_date.py",
)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
sys.modules["migrate_annual_effective_date"] = mod
_SPEC.loader.exec_module(mod)


# [WHY] Năm thường: 31/12 + 90 ngày = 31/03. Đây là ca phổ biến nhất (13/17 năm dữ liệu).
@pytest.mark.parametrize(
    "fy,expected",
    [
        (2021, "2022-03-31"),
        (2022, "2023-03-31"),
        (2024, "2025-03-31"),
        (2025, "2026-03-31"),
    ],
)
def test_nam_thuong_ra_31_03(fy, expected):
    assert mod.annual_effective_date(fy).isoformat() == expected


# [WHY] Năm nhuận phía SAU năm tài chính: 31/12 + 90 ngày = 30/03 (vì tháng 2 có 29 ngày). Đây là 7.514
# dòng (26,2%) — nếu sai thành 31/03 thì bảng năm lệch 1 ngày so với Q4 cùng kỳ của bảng quý.
@pytest.mark.parametrize(
    "fy,expected",
    [(2011, "2012-03-30"), (2015, "2016-03-30"), (2019, "2020-03-30"), (2023, "2024-03-30")],
)
def test_nam_nhuan_ra_30_03(fy, expected):
    assert mod.annual_effective_date(fy).isoformat() == expected


# [WHY] DRY: hằng số 90 ngày phải lấy từ migration QUÝ, không được chép lại — nếu ai đó sửa luật ở một
# nơi, hai bảng phải đổi cùng nhau.
def test_lag_lay_tu_migration_quy():
    assert mod.ANNUAL_LAG_DAYS == 90
    assert mod.BASIS == "STATUTORY_LAG"


# [WHY] Fail-closed: `fiscal_year` NULL hoặc ngoài dải hợp lý là dữ liệu bẩn — không được đoán năm.
@pytest.mark.parametrize("bad", [None, 1999, 3000, "abc"])
def test_fiscal_year_di_thuong_bi_tu_choi(bad):
    with pytest.raises(ValueError):
        mod.annual_effective_date(bad)


@pytest.fixture()
def db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE financial_facts_annual (symbol TEXT, fiscal_year INTEGER, statement_type TEXT,"
        " metric TEXT, value REAL, unit TEXT, source TEXT, is_synthetic INTEGER, integrity_flags TEXT,"
        " ingested_at TEXT, verification_status TEXT)"
    )
    for fy, n in ((2019, 2), (2023, 3), (2025, 1)):
        for i in range(n):
            c.execute(
                "INSERT INTO financial_facts_annual (symbol, fiscal_year, statement_type, metric, value)"
                " VALUES (?,?,?,?,?)",
                (f"TST{i}", fy, "IS", "REVENUE", 100.0),
            )
    c.commit()
    return c


# [WHY] Mapping phải phủ đủ mọi năm CÓ dữ liệu và đếm đúng số dòng — nếu thiếu năm nào thì phần lớn bảng
# sẽ NULL sau khi apply (đúng lỗi mà hạng mục này sinh ra để dẹp).
def test_build_mapping_phu_du_moi_nam(db):
    rows, errs = mod.build_mapping(db)
    assert errs == []
    assert [r["fiscal_year"] for r in rows] == [2019, 2023, 2025]
    assert {r["fiscal_year"]: r["rows"] for r in rows} == {2019: 2, 2023: 3, 2025: 1}
    assert {r["fiscal_year"]: r["effective_date"] for r in rows} == {
        2019: "2020-03-30",
        2023: "2024-03-30",
        2025: "2026-03-31",
    }


# [WHY] Fail-closed trên dữ liệu bẩn: một năm NULL/3000 trong bảng ⇒ trả LỖI để caller DỪNG, không ghi
# nửa vời (một nửa bảng có ngày, một nửa NULL = tệ hơn không làm gì).
def test_nam_di_thuong_lam_mapping_bao_loi(db):
    db.execute("INSERT INTO financial_facts_annual (symbol, fiscal_year, metric) VALUES ('BAD', 3000, 'X')")
    db.execute("INSERT INTO financial_facts_annual (symbol, fiscal_year, metric) VALUES ('BAD2', NULL, 'X')")
    db.commit()
    rows, errs = mod.build_mapping(db)
    assert len(errs) == 2
    assert {r["fiscal_year"] for r in rows} == {2019, 2023, 2025}


# [WHY] Idempotent: chạy 2 lần phải ra cùng kết quả và KHÔNG nhân đôi cột (ALTER TABLE lần 2 sẽ lỗi nếu
# không kiểm PRAGMA trước). Đây là điều kiện để chạy lại an toàn trong pipeline/cron.
def test_apply_idempotent(db):
    rows, _ = mod.build_mapping(db)
    n1 = mod.apply_mapping(db, rows)
    n2 = mod.apply_mapping(db, rows)
    assert n1 == 6 and n2 == 6
    cols = [r[1] for r in db.execute("PRAGMA table_info(financial_facts_annual)")]
    assert cols.count("effective_date") == 1 and cols.count("effective_date_basis") == 1
    got = dict(db.execute("SELECT fiscal_year, effective_date FROM financial_facts_annual").fetchall())
    assert got == {2019: "2020-03-30", 2023: "2024-03-30", 2025: "2026-03-31"}


# [WHY] Bất biến PIT: ngày hiệu lực phải SAU ngày kết thúc năm, nếu không dữ liệu được phép đọc trước khi
# tồn tại (look-ahead). Nếu test này đỏ thì cả hạng mục vô nghĩa.
def test_effective_date_sau_period_end(db):
    rows, _ = mod.build_mapping(db)
    mod.apply_mapping(db, rows)
    for fy, eff in db.execute("SELECT fiscal_year, effective_date FROM financial_facts_annual").fetchall():
        assert eff > f"{fy}-12-31", (fy, eff)
