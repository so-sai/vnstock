"""test_gold_h2_series.py — Adversarial PIT contract tests cho gold_h2_series.

Phạm vi: schema + PIT resolver + invariant checks. KHÔNG đụng DB thật
(screener_cache) — chạy trên in-memory SQLite cách ly.

Trọng tâm (bài học look-ahead từ valuation/zone_ts):
  - feature usable at t  <=>  publication_date <= t
  - KHÔNG dùng observation_date <= t cho PIT.
  - Revision/vintage được bảo toàn, không overwrite lịch sử.
"""

import sqlite3

import pytest

from src.research.gold_h2_series import (
    init_table,
    insert_vintage,
    latest_value_at,
    resolve_pit,
    validate_contract,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_table(c)
    yield c
    c.close()


def _insert_cb(conn, obs, pub, value, source="WGC_TEST", provenance="TEST"):
    insert_vintage(
        conn,
        series="CB_GOLD_PURCHASES",
        entity="GLOBAL",
        observation_date=obs,
        publication_date=pub,
        value=value,
        unit="tonnes",
        source=source,
        provenance=provenance,
    )


# ── 1. PIT look-ahead bị chặn ─────────────────────────────────────────────


def test_pit_excludes_future_publication(conn):
    """Observation tháng 6 nhưng publication tháng 7 -> ngày 20/6 KHÔNG thấy."""
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.0)
    pit = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-06-20")
    assert pit == {}
    assert latest_value_at(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-06-20") is None


def test_pit_sees_publication_at_or_before_t(conn):
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.0)
    pit = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-07-10")
    assert pit.get("2024-06-30") == ("2024-07-10", 12.0)
    got = latest_value_at(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-07-10")
    assert got == ("2024-06-30", 12.0)


def test_pit_does_not_use_observation_date(conn):
    """Chỉ publication_date quyết định PIT, KHÔNG observation_date."""
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.0)
    # observation <= t nhưng publication > t -> phải KHÔNG thấy
    pit = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-07-01")
    assert pit == {}


# ── 2. Vintage / revision được bảo toàn ───────────────────────────────────


def test_vintage_overwrite_does_not_erase_history(conn):
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.0)
    _insert_cb(conn, "2024-06-30", "2024-08-05", 15.0)  # v2 revision

    # backtest 20/7 chỉ thấy 12
    pit = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-07-20")
    assert pit["2024-06-30"] == ("2024-07-10", 12.0)

    # backtest sau v2 chỉ thấy 15 (latest vintage)
    pit = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-08-10")
    assert pit["2024-06-30"] == ("2024-08-05", 15.0)

    # cả 2 vintage vẫn còn trong DB
    n = conn.execute(
        "SELECT COUNT(*) FROM gold_h2_series WHERE observation_date='2024-06-30'"
    ).fetchone()[0]
    assert n == 2


def test_same_vintage_revision_is_replace(conn):
    """Cùng (series, entity, obs, source, pub) -> REPLACE (revision cùng vintage)."""
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.0)
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.5)  # cùng pub date
    n = conn.execute("SELECT COUNT(*) FROM gold_h2_series").fetchone()[0]
    assert n == 1
    pit = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-07-10")
    assert pit["2024-06-30"] == ("2024-07-10", 12.5)


# ── 3. Duplicate & NULL publication ────────────────────────────────────────


def test_duplicate_obs_source_blocks(conn):
    """Duplicate (series, entity, obs, source) -> UNIQUE index reject.

    Vì cùng obs+source+pub NULL -> cùng khóa COALESCE('') -> chỉ 1 dòng.
    """
    _insert_cb(conn, "2024-06-30", None, 12.0)
    _insert_cb(conn, "2024-06-30", None, 14.0)
    n = conn.execute("SELECT COUNT(*) FROM gold_h2_series").fetchone()[0]
    assert n == 1


def test_null_publication_excluded_from_pit(conn):
    _insert_cb(conn, "2024-06-30", None, 12.0)  # publication không rõ
    pit = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2030-01-01")
    assert pit == {}


def test_publication_before_observation_rejected(conn):
    """CHECK constraint: publication_date >= observation_date."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_cb(conn, "2024-06-30", "2024-06-15", 12.0)


# ── 4. Contract validation ─────────────────────────────────────────────────


def test_validate_contract_clean(conn):
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.0)
    checks = validate_contract(conn)
    assert all(ok for ok, _ in checks.values()), checks


def test_validate_contract_catches_bad_pub(conn):
    """Trực tiếp chèn vi phạm pub<obs (bảng không CHECK) -> validate FAIL."""
    conn.execute("DROP TABLE gold_h2_series")
    conn.execute(
        "CREATE TABLE gold_h2_series ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " series TEXT NOT NULL, entity TEXT NOT NULL,"
        " observation_date TEXT NOT NULL, publication_date TEXT,"
        " value REAL NOT NULL, unit TEXT NOT NULL,"
        " source TEXT NOT NULL, provenance TEXT NOT NULL,"
        " ingested_at TEXT DEFAULT (datetime('now')), metadata TEXT)"
    )
    conn.execute(
        "INSERT INTO gold_h2_series (series, entity, observation_date, "
        "publication_date, value, unit, source, provenance) "
        "VALUES ('CB_GOLD_PURCHASES','GLOBAL','2024-06-30','2024-06-15',"
        "12.0,'tonnes','RAW','RAW')"
    )
    conn.commit()
    checks = validate_contract(conn)
    assert checks["pub_ge_obs"][0] is False


def test_validate_contract_requires_provenance(conn):
    conn.execute(
        "INSERT INTO gold_h2_series (series, entity, observation_date, "
        "publication_date, value, unit, source, provenance) "
        "VALUES ('CB_GOLD_PURCHASES','GLOBAL','2024-06-30','2024-07-10',"
        "12.0,'tonnes','WGC','')"
    )
    conn.commit()
    checks = validate_contract(conn)
    assert checks["source_provenance"][0] is False


def test_multiple_series_isolated(conn):
    _insert_cb(conn, "2024-06-30", "2024-07-10", 12.0)
    insert_vintage(
        conn,
        series="ETF_FLOWS",
        entity="GLD",
        observation_date="2024-06-28",
        publication_date="2024-07-05",
        value=100.0,
        unit="tonnes",
        source="WGC_TEST",
        provenance="TEST",
    )
    cb = resolve_pit(conn, "CB_GOLD_PURCHASES", "GLOBAL", "2024-07-10")
    etf = resolve_pit(conn, "ETF_FLOWS", "GLD", "2024-07-10")
    assert "2024-06-30" in cb
    assert "2024-06-28" in etf
    assert "2024-06-28" not in cb
