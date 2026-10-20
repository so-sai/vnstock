"""Test wire `target_date` xuyên suốt pipeline VN20 (backtest PIT, không rò rỉ).

[WHY]
  Sau `e1675a4`, cổng kiểm toán annual đã PIT, nhưng **chính các chỉ số đầu vào** thì chưa: `period` vẫn lấy
  `MAX(period)` toàn bảng (2026Q3) và `_liquid_universe` vẫn đọc mọi bar trong `daily_ohlcv`. Nghĩa là một
  backtest gọi `run_vn20_filter(target_date="2022-06-30")` vẫn "nhìn thấy" ROE/GM/CFO của 2026 và thanh khoản
  của 2026 — look-ahead lớn hơn nhiều so với cờ kiểm toán. PIT ở đáy mà không PIT ở đầu vào là vô nghĩa.

[ROOT CAUSE]
  Pipeline không có tham số "tính đến ngày"; mọi truy vấn mặc định lấy mốc mới nhất trong DB.

[INVARIANT]
  1. `target_date` phải đi từ `run_vn20_filter` → `_latest_period(asof=)` → `periods` → `tier1_buffett_quality`
     → `_annual_verification`.
  2. Kỳ quý chỉ được dùng khi **đã công bố** tính đến `target_date` (lag luật định Q1 30d · Q2 45d · Q3 30d ·
     Q4 90d). Ngày kết thúc kỳ KHÔNG phải ngày công bố — dùng nó là rò rỉ 30–90 ngày.
  3. `_liquid_universe(asof=)` chỉ tính trên bar ≤ asof.
  4. Bộ lag ở module này phải KHỚP `migrate_financial_facts_effective_date.LAG_DAYS` (test đồng bộ).
"""

from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.quant import vn20_quant_filter as vf

SRC = BACKEND / "src" / "quant" / "vn20_quant_filter.py"


def _migration_lag() -> dict:
    spec = importlib.util.spec_from_file_location(
        "migrate_ff_effective_date", BACKEND / "scripts" / "migrate_financial_facts_effective_date.py"
    )
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["migrate_ff_effective_date"] = m
    spec.loader.exec_module(m)
    return m.LAG_DAYS


# [WHY] Ngày công bố = ngày kết thúc kỳ + lag luật định, KHÔNG phải ngày kết thúc kỳ. Q2/2022 kết thúc
# 30/06 nhưng phải 14/08 mới công bố (45 ngày) — chênh 45 ngày chính là cửa sổ look-ahead.
@pytest.mark.parametrize(
    "period,expected",
    [("2022Q1", "2022-04-30"), ("2022Q2", "2022-08-14"), ("2022Q3", "2022-10-30"), ("2022Q4", "2023-03-31")],
)
def test_ngay_cong_bo_theo_lag(period, expected):
    assert vf._quarter_published_on(period) == expected


# [WHY] Chống trôi: bảng lag ở consumer phải khớp nguồn sự thật của migration (nếu ai sửa luật một nơi,
# test này đỏ ngay thay vì hai bảng lệch nhau âm thầm).
def test_lag_khop_voi_migration_quy():
    assert vf._QUARTER_LAG_DAYS == _migration_lag()


@pytest.fixture()
def db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE health_ratios (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)")
    for p in ("2022Q1", "2022Q2", "2022Q3", "2022Q4"):
        c.execute("INSERT INTO health_ratios VALUES ('AAA', ?, 'ROE', 0.05)", (p,))
    c.commit()
    return c


# [WHY] Bốn ca biên quanh ngày công bố: 13/08 chưa thấy Q2, 14/08 thấy; 29/10 chưa thấy Q3, 30/10 thấy.
def test_latest_period_ton_trong_ngay_cong_bo(db):
    assert vf._latest_period(db, asof="2022-08-13") == "2022Q1"
    assert vf._latest_period(db, asof="2022-08-14") == "2022Q2"
    assert vf._latest_period(db, asof="2022-10-29") == "2022Q2"
    assert vf._latest_period(db, asof="2022-10-30") == "2022Q3"
    assert vf._latest_period(db) == "2022Q4"  # live: không giới hạn


# [WHY] Không kỳ nào đã công bố (hỏi về quá khứ trước kỳ đầu tiên) ⇒ '' chứ không được lấy bừa kỳ đầu.
def test_latest_period_truoc_khi_co_ky_nao_cong_bo(db):
    assert vf._latest_period(db, asof="2022-01-01") == ""


# [WHY] Thanh khoản cũng là dữ liệu tương lai: ADV_20D của 2026 không được dùng để lọc universe năm 2022.
def test_liquid_universe_ton_trong_asof():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, volume REAL)")
    for i in range(6):
        c.execute("INSERT INTO daily_ohlcv VALUES ('EARLY', ?, 120000)", (f"2022-06-0{i + 1}",))
        c.execute("INSERT INTO daily_ohlcv VALUES ('LATE', ?, 120000)", (f"2026-06-0{i + 1}",))
    c.commit()
    assert vf._liquid_universe(c, asof="2022-06-30") == {"EARLY"}
    assert vf._liquid_universe(c) == {"EARLY", "LATE"}


# [WHY] `strptime` chấp nhận '2026-6-30' nhưng cột ngày là CHUỖI ISO: '2026-6' > '2026-08' ⇒ so sánh sai.
# Ngày sai dạng phải nổ ngay tại tầng hàm (backtest gọi trực tiếp cũng được bảo vệ, không chỉ CLI).
@pytest.mark.parametrize("bad", ["2026-6-30", "2026-13-01", "2026-02-30", "30/06/2026", "", "2026-06-3"])
def test_target_date_sai_dang_bi_tu_choi(bad):
    with pytest.raises(ValueError):
        vf._validate_target_date(bad)


def test_target_date_dung_dang_duoc_chap_nhan():
    assert vf._validate_target_date("2026-06-30") == "2026-06-30"


def _fn(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"không thấy hàm {name}")


# [WHY] Test cấu trúc: bắt đúng lỗi "quên truyền" — tham số tồn tại nhưng không đi xuống đáy thì backtest
# vẫn rò rỉ mà không test hành vi nào phát hiện (vì mặc định vẫn chạy được).
def test_wire_target_date_xuong_day_pipeline():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    run = _fn(tree, "run_vn20_filter")
    assert "target_date" in [a.arg for a in run.args.args + run.args.kwonlyargs]

    calls = [n for n in ast.walk(run) if isinstance(n, ast.Call)]
    tier1 = [n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "tier1_buffett_quality"]
    assert tier1, "run_vn20_filter phải gọi tier1_buffett_quality"
    ok = [
        any(k.arg == "target_date" and isinstance(k.value, ast.Name) and k.value.id == "target_date" for k in c.keywords)
        for c in tier1
    ]
    assert all(ok), "tier1_buffett_quality phải nhận target_date=target_date"

    latest = [n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "_latest_period"]
    assert latest and all(any(k.arg == "asof" for k in c.keywords) for c in latest), "_latest_period phải nhận asof"

    liquid = [n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "_liquid_universe"]
    assert liquid and all(any(k.arg == "asof" for k in c.keywords) for c in liquid), "_liquid_universe phải nhận asof"
