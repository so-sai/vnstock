"""test_pit_boundary.py — Test Contract 2: PIT Boundary Guard.

Quy tắc: mọi query nạp BCTC/chỉ số phải CHẶN DỨT dữ liệu tương lai tại mốc t:
  - financial_facts: period > anchor_quarter → loại; ingested_at > t → loại
  - health_ratios  : period > anchor_quarter → loại (không có ingested_at)

Run: python -m pytest backend/tests/test_pit_boundary.py -q
"""

import sqlite3

from src.database.pit_queries import date_to_quarter, fetch_financial_facts_pit, fetch_health_ratios_pit


def _facts_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE financial_facts "
        "(symbol TEXT, period TEXT, metric TEXT, value REAL, ingested_at TEXT)"
    )
    # Các mẫu: hợp lệ / quý sau (leak theo period) / nạp sau mốc (leak theo ingested_at)
    conn.executemany(
        "INSERT INTO financial_facts VALUES (?,?,?,?,?)",
        [
            ("VCB", "2024Q3", "NET_PROFIT", 1e12, "2024-12-01"),   # hợp lệ
            ("VCB", "2024Q4", "NET_PROFIT", 1.2e12, "2025-01-28"),  # ingested SAU mốc 2025-01-02
            ("VCB", "2025Q1", "NET_PROFIT", 1.3e12, "2025-01-28"),  # period SAU anchor 2024Q4
            ("VCB", "2024Q4", "NET_PROFIT", 1.1e12, "2024-12-20"),  # hợp lệ (ingested trước mốc)
        ],
    )
    conn.commit()
    return conn


def _hr_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE health_ratios (symbol TEXT, period TEXT, ratio_name TEXT, ratio_value REAL)")
    conn.executemany(
        "INSERT INTO health_ratios VALUES (?,?,?,?)",
        [
            ("VCB", "2024Q4", "ROE", 0.12),
            ("VCB", "2025Q1", "ROE", 0.13),  # period SAU anchor → phải loại
        ],
    )
    conn.commit()
    return conn


class TestDateToQuarter:
    def test_date_to_quarter_basic(self):
        assert date_to_quarter("2025-06-30") == "2025Q2"
        assert date_to_quarter("2024-12-31") == "2024Q4"
        assert date_to_quarter("2025-01-02") == "2025Q1"

    def test_date_to_quarter_invalid_falls_back(self):
        assert date_to_quarter("") == "0000Q1"
        assert date_to_quarter(None) == "0000Q1"


class TestFinancialFactsPitBoundary:
    def test_future_period_excluded(self):
        """period > anchor_quarter (2025Q1 > 2024Q4) phải bị loại dù ingested_at NULL."""
        conn = _facts_conn()
        rows = fetch_financial_facts_pit(conn, "VCB", "2025-01-02")
        conn.close()
        assert [r["period"] for r in rows] == ["2024Q3", "2024Q4"]
        assert len(rows) == 2  # 2025Q1 bị loại

    def test_ingested_after_as_of_excluded(self):
        """BCTC 2024Q4 ingested 2025-01-28 (sau mốc 2025-01-02) phải bị loại."""
        conn = _facts_conn()
        rows = fetch_financial_facts_pit(conn, "VCB", "2025-01-02")
        conn.close()
        ingested = {r["ingested_at"] for r in rows}
        assert "2025-01-28" not in ingested, "BCTC nạp sau mốc không được lọt vào kết quả"
        # Bản ghi ingested 2025-01-28 thuộc 2024Q4 phải bị loại (chỉ giữ 1 bản 2024Q4 ingested 2024-12-20)
        assert sum(1 for r in rows if r["period"] == "2024Q4") == 1

    def test_quarter_end_boundary_2024_12_31(self):
        """Ngày CUỐI quý 2024-12-31 → anchor = 2024Q4. BCTC 2024Q4 chưa kết thúc/
        chưa công bố tại mốc này → phải bị chặn (period < 2024Q4 chỉ cho 2024Q3)."""
        conn = _facts_conn()
        rows = fetch_financial_facts_pit(conn, "VCB", "2024-12-31")
        conn.close()
        assert [r["period"] for r in rows] == ["2024Q3"]
        # Bản 2024Q4 ingested 2024-12-20 KHÔNG được lọt (period == anchor, Q4 chưa đóng)
        assert all(r["period"] != "2024Q4" for r in rows)

    def test_quarter_start_boundary_2025_01_01(self):
        """Ngày ĐẦU quý 2025-01-01 → anchor = 2025Q1. Chỉ các quý ĐÃ ĐÓNG
        (<= 2024Q4, ingested trước mốc) mới hợp lệ — 2024Q4 ingested 2024-12-20
        được giữ, bản ingested 2025-01-28 bị loại."""
        conn = _facts_conn()
        rows = fetch_financial_facts_pit(conn, "VCB", "2025-01-01")
        conn.close()
        assert [r["period"] for r in rows] == ["2024Q3", "2024Q4"]
        assert len(rows) == 2  # bản 2024Q4 ingested 2025-01-28 bị loại theo ingested_at
        assert {r["ingested_at"] for r in rows} == {"2024-12-01", "2024-12-20"}

    def test_metric_filter_respected(self):
        conn = _facts_conn()
        rows = fetch_financial_facts_pit(conn, "VCB", "2025-01-02", metrics=["TOTAL_DEBT"])
        conn.close()
        assert rows == []  # metric không tồn tại → không trả về gì (không leak)

    def test_unknown_symbol_empty(self):
        conn = _facts_conn()
        rows = fetch_financial_facts_pit(conn, "UNKNOWN", "2025-01-02")
        conn.close()
        assert rows == []


class TestHealthRatiosPitBoundary:
    def test_future_period_excluded(self):
        conn = _hr_conn()
        rows = fetch_health_ratios_pit(conn, "VCB", "2025-01-02")
        conn.close()
        assert [r["period"] for r in rows] == ["2024Q4"]
        assert len(rows) == 1  # 2025Q1 bị loại

    def test_ratio_filter_respected(self):
        conn = _hr_conn()
        rows = fetch_health_ratios_pit(conn, "VCB", "2025-01-02", ratio_name="ROE")
        conn.close()
        assert len(rows) == 1

    def test_quarter_end_boundary_2024_12_31(self):
        """health_ratios không có ingested_at → chỉ chặn theo period.
        Tại 2024-12-31 (anchor 2024Q4), 2024Q4 phải bị loại (Q4 chưa công bố)."""
        conn = _hr_conn()
        rows = fetch_health_ratios_pit(conn, "VCB", "2024-12-31")
        conn.close()
        assert rows == []  # cả 2024Q4 lẫn 2025Q1 đều bị loại tại mốc cuối Q3
