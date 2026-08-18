"""
Tests for symbol_audit_service.build_symbol_audit (4-gate deep-dive).

WHY: symbol-audit phải hội tụ 4 cổng — (1) Hàng hóa/Ngành, (2) Chất lượng & Định
giá VN20, (3) Dòng tiền & Volume Profile, (4) Kỹ thuật & Động lượng — 100% từ DB
sống. Test này dùng temp SQLite (deterministic, không mạng) và assert từng cổng
trả đúng dữ liệu đã seed. NO_DATA (thiếu dữ liệu) phải được tôn trọng, không bịa.
"""

import sqlite3
from datetime import date

import pytest
from src.services.symbol_audit_service import build_symbol_audit

FIN_SCHEMA = {
    "valuation_scores": """
        CREATE TABLE valuation_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            period TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            entity_type TEXT NOT NULL,
            ratio_name TEXT NOT NULL,
            ratio_value REAL,
            z_score REAL,
            percentile REAL,
            mean REAL,
            std REAL,
            count INTEGER,
            zone TEXT,
            price REAL
        )
    """,
    "health_ratios": """
        CREATE TABLE health_ratios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            period TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            entity_type TEXT NOT NULL,
            ratio_name TEXT NOT NULL,
            ratio_value REAL,
            category TEXT,
            interpretation TEXT,
            metadata TEXT
        )
    """,
    "volume_profile": """
        CREATE TABLE volume_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            price_current REAL,
            poc REAL,
            vah REAL,
            val REAL,
            poc_volume REAL,
            total_volume REAL,
            value_area_volume REAL,
            bin_size REAL,
            hvns TEXT,
            price_ma20 REAL,
            price_ma50 REAL,
            price_ma200 REAL,
            volume_ma20 REAL,
            volume_ratio REAL,
            range_pct REAL
        )
    """,
    "financial_facts": """
        CREATE TABLE financial_facts (
            symbol TEXT NOT NULL,
            period TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            entity_type TEXT NOT NULL,
            statement_type TEXT,
            metric TEXT NOT NULL,
            value REAL,
            unit TEXT,
            source TEXT,
            reported_at TEXT,
            ingested_at TEXT,
            integrity_flags TEXT,
            is_synthetic INTEGER
        )
    """,
}

SCREENER_SCHEMA = {
    "symbol_industry": """
        CREATE TABLE symbol_industry (
            symbol TEXT PRIMARY KEY,
            icb_name2 TEXT,
            icb_name3 TEXT,
            icb_name4 TEXT
        )
    """,
    "daily_ohlcv": """
        CREATE TABLE daily_ohlcv (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adj_close REAL,
            volume INTEGER CHECK (volume >= 0),
            source TEXT,
            is_stale INTEGER,
            PRIMARY KEY (symbol, date)
        )
    """,
    "macro_history": """
        CREATE TABLE macro_history (
            date TEXT NOT NULL,
            variable TEXT NOT NULL,
            value REAL,
            is_stale INTEGER,
            source TEXT
        )
    """,
}


@pytest.fixture
def dbs(tmp_path):
    """Hai temp DB: fin (financial_facts) + screener (screener_cache)."""
    fin = sqlite3.connect(tmp_path / "fin.db")
    scr = sqlite3.connect(tmp_path / "scr.db")
    for sql in FIN_SCHEMA.values():
        fin.execute(sql)
    for sql in SCREENER_SCHEMA.values():
        scr.execute(sql)

    # ── Seed: symbol ZZAUD (hàng hóa: thép — để test crack spread) ──
    scr.execute(
        "INSERT INTO symbol_industry VALUES (?,?,?,?)",
        ("ZZAUD", "Tài nguyên Cơ bản", "Kim loại", "Sản xuất thép"),
    )

    # macro_history: HRC_CFR + IRON_ORE_62 + COKING_COAL_HCC đủ 3 biến
    scr.executemany(
        "INSERT INTO macro_history (date, variable, value, is_stale, source) VALUES (?,?,?,?,?)",
        [
            ("2026-08-14", "HRC_CFR", 546.0, 0, "test"),
            ("2026-08-14", "IRON_ORE_62", 95.05, 0, "test"),
            ("2026-08-14", "COKING_COAL_HCC", 222.0, 0, "test"),
        ],
    )

    # valuation_scores: PB (EXPENSIVE) + ROE (FAIR) kỳ 2026Q2; thêm kỳ cũ 2025Q4
    # để khóa contract "chỉ trả 1 kỳ mới nhất" (Zero-Hallucination: không lẫn lịch sử).
    fin.executemany(
        "INSERT INTO valuation_scores "
        "(symbol, period, fiscal_year, fiscal_quarter, entity_type, ratio_name, "
        "ratio_value, z_score, percentile, zone, price) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "ZZAUD",
                "2026Q2",
                2026,
                2,
                "STANDARD",
                "PB",
                1.39,
                1.27,
                91.18,
                "EXPENSIVE",
                21000.0,
            ),
            (
                "ZZAUD",
                "2026Q2",
                2026,
                2,
                "STANDARD",
                "ROE",
                0.18,
                -0.18,
                56.25,
                "FAIR",
                21000.0,
            ),
            (
                "ZZAUD",
                "2025Q4",
                2025,
                4,
                "STANDARD",
                "PB",
                0.95,
                -1.20,
                5.0,
                "CHEAP",
                15000.0,
            ),
            (
                "ZZAUD",
                "2025Q4",
                2025,
                4,
                "STANDARD",
                "ROE",
                0.12,
                -1.00,
                10.0,
                "CHEAP",
                15000.0,
            ),
        ],
    )

    # health_ratios: ROE + CFO_TO_NET_INCOME (kỳ mới + kỳ cũ để test "chỉ kỳ mới")
    fin.executemany(
        "INSERT INTO health_ratios "
        "(symbol, period, fiscal_year, fiscal_quarter, entity_type, ratio_name, ratio_value) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("ZZAUD", "2026Q2", 2026, 2, "STANDARD", "ROE", 0.18),
            ("ZZAUD", "2026Q2", 2026, 2, "STANDARD", "CFO_TO_NET_INCOME", 1.88),
            ("ZZAUD", "2025Q4", 2025, 4, "STANDARD", "ROE", 0.12),
        ],
    )

    # volume_profile
    fin.execute(
        "INSERT INTO volume_profile "
        "(symbol, date, price_current, poc, vah, val, volume_ratio, price_ma20, price_ma50) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "ZZAUD",
            "2026-08-18",
            21000.0,
            23400.0,
            24400.0,
            21600.0,
            0.65,
            21472.5,
            22440.0,
        ),
    )

    # financial_facts: CFO
    fin.execute(
        "INSERT INTO financial_facts "
        "(symbol, period, fiscal_year, fiscal_quarter, entity_type, metric, value, unit, source) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("ZZAUD", "2026Q2", 2026, 2, "STANDARD", "CFO", 12.07e12, "VND", "test"),
    )

    # daily_ohlcv: 130 phiên, đóng cửa tăng dần từ 18000 → 21000 (ngày liên tiếp)
    from datetime import timedelta

    rows = []
    close = 18000.0
    start = date(2026, 3, 1)
    for i in range(130):
        d = start + timedelta(days=i)
        rows.append(
            (
                "ZZAUD",
                d.isoformat(),
                close,
                close + 50,
                close - 50,
                close,
                close,
                100000,
                "test",
                0,
            )
        )
        close += 25.0
    scr.executemany(
        "INSERT INTO daily_ohlcv "
        "(symbol, date, open, high, low, close, adj_close, volume, source, is_stale) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )

    fin.commit()
    scr.commit()
    yield fin, scr
    fin.close()
    scr.close()


class TestSymbolAuditGates:
    """4 cổng phải đọc đúng dữ liệu đã seed, NO_DATA khi thiếu."""

    def test_gate1_sector_and_crack_spread(self, dbs):
        """Cổng 1: ngành (icb) + crack spread tính được từ 3 biến macro."""
        fin, scr = dbs
        audit = build_symbol_audit("ZZAUD", fin, scr)

        g1 = audit["gate1"]
        assert g1["icb_name2"] == "Tài nguyên Cơ bản"
        assert g1["icb_name3"] == "Kim loại"
        assert g1["crack_spread"] is not None
        # Spread = HRC - 1.6*ore - 0.5*coal = 546 - 152.08 - 111 = 282.92
        assert abs(g1["crack_spread"]["crack_spread"] - 282.92) < 0.01
        assert g1["crack_spread"]["hrc_price"] == 546.0

    def test_gate2_valuation_and_health(self, dbs):
        """Cổng 2: valuation (z-score/zone) + health ratios — CHỈ kỳ mới nhất."""
        fin, scr = dbs
        audit = build_symbol_audit("ZZAUD", fin, scr)

        g2 = audit["gate2"]
        ratios = {r["ratio_name"]: r for r in g2["valuation"]}
        assert ratios["PB"]["ratio_value"] == pytest.approx(1.39)
        assert ratios["PB"]["zone"] == "EXPENSIVE"
        assert ratios["PB"]["z_score"] == pytest.approx(1.27)
        assert ratios["ROE"]["zone"] == "FAIR"
        # Zero-Hallucination: kỳ 2025Q4 (CHEAP) không được lẫn vào bản mới nhất
        assert all(r["period"] == "2026Q2" for r in g2["valuation"])
        assert len(g2["valuation"]) == 2

        health = {h["ratio_name"]: h for h in g2["health"]}
        assert health["CFO_TO_NET_INCOME"]["ratio_value"] == pytest.approx(1.88)
        assert all(h["period"] == "2026Q2" for h in g2["health"])
        assert len(g2["health"]) == 2

    def test_gate3_cashflow_and_volume_profile(self, dbs):
        """Cổng 3: CFO + volume profile (POC/VAL/VAH)."""
        fin, scr = dbs
        audit = build_symbol_audit("ZZAUD", fin, scr)

        g3 = audit["gate3"]
        assert g3["volume_profile"]["poc"] == pytest.approx(23400.0)
        assert g3["volume_profile"]["vah"] == pytest.approx(24400.0)
        assert g3["volume_profile"]["val"] == pytest.approx(21600.0)
        assert g3["cfo"]["value"] == pytest.approx(12.07e12)

    def test_gate4_technical_from_ohlcv(self, dbs):
        """Cổng 4: kỹ thuật tính từ daily_ohlcv (close/MA/RSI)."""
        fin, scr = dbs
        audit = build_symbol_audit("ZZAUD", fin, scr)

        g4 = audit["gate4"]
        assert g4["close"] is not None
        assert g4["ma20"] is not None
        assert g4["ma50"] is not None
        assert g4["close"] > g4["ma20"], "uptrend seeded → close phải trên MA20"
        assert g4["rsi14"] is not None and 0 <= g4["rsi14"] <= 100

    def test_no_data_symbol(self, dbs):
        """Symbol không có dữ liệu → các cổng phải báo NO_DATA, không crash, không bịa."""
        fin, scr = dbs
        audit = build_symbol_audit("NODATA999", fin, scr)

        assert audit["gate1"]["icb_name2"] is None
        assert audit["gate1"]["crack_spread"] is None
        assert audit["gate2"]["valuation"] == []
        assert audit["gate3"]["volume_profile"] is None
        assert audit["gate4"]["close"] is None
