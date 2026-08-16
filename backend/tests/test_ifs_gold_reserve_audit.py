"""test_ifs_gold_reserve_audit.py — Unit tests cho IFS PIT audit (in-memory DB).

Chỉ test các hàm audit trên DB in-memory cách ly (KHÔNG đụng gold_h2.db thật):
  - coverage: first_pub, lag_months, n_vintages.
  - revision: max_delta đúng; anomaly candidate khi revision > 20t.
  - PIT usability: đếm obs công bố tại các mốc thời gian.
  - revision anomaly 1-vintage (spike) được flag nhưng KHÔNG tự động loại.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.gold_h2_series import init_table, insert_vintage
from src.research.ifs_gold_reserve_audit import (
    ANOM_REVISION,
    ANOM_SOURCE,
    SERIES,
    audit,
)


def _seed(conn, obs_pub_vals):
    for obs, pub, val in obs_pub_vals:
        insert_vintage(
            conn,
            series=SERIES,
            entity="GLOBAL",
            observation_date=obs,
            publication_date=pub,
            value=val,
            unit="tonnes",
            source="IMF_IFS",
            provenance="TEST",
        )


def test_audit_coverage_lag():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed(
        conn,
        [
            ("2024-05-31", "2024-08-02", 13.0),   # lag 3
            ("2024-05-31", "2024-09-03", 14.0),
            ("2026-01-31", "2026-03-03", 5.0),    # lag 2
        ],
    )
    res = audit(conn)
    cov = res["coverage"]
    assert cov["2024-05-31"]["first_pub"] == "2024-08-02"
    assert cov["2024-05-31"]["lag_months"] == 3
    assert cov["2024-05-31"]["n_vintages"] == 2
    assert cov["2026-01-31"]["lag_months"] == 2
    conn.close()


def test_audit_revision_magnitude():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed(
        conn,
        [
            ("2024-06-30", "2024-08-02", 12.0),
            ("2024-06-30", "2024-11-01", 25.0),
        ],
    )
    res = audit(conn)
    assert res["revisions"]["2024-06-30"] == 13.0  # |12-25|
    conn.close()


def test_audit_anomaly_candidate_flagged_not_dropped():
    """Spike 1-vintage (file Oct2024 -2349) → anomaly candidate, KHÔNG bị loại."""
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed(
        conn,
        [
            ("2024-07-31", "2024-09-03", 37.0),
            ("2024-07-31", "2024-10-03", -2349.18),  # data error trong nguồn
            ("2024-07-31", "2024-11-01", 41.72),
        ],
    )
    res = audit(conn)
    assert len(res["anomalies"]) == 1
    a = res["anomalies"][0]
    assert a["obs"] == "2024-07-31"
    assert a["max_revision"] > 2000
    assert a["anomaly_type"] == ANOM_SOURCE
    assert a["spike_vintages"] == ["2024-10-03"]
    # vintage spike VẪN còn trong DB (adapter không tự ý loại — PIT trung thực)
    n = conn.execute("SELECT COUNT(*) FROM gold_h2_series").fetchone()[0]
    assert n == 3
    conn.close()


def test_audit_classifies_consistent_big_value_as_revision_not_source():
    """2015-06 ~638t nhất quán mọi vintage → REVISION_LARGE, không SOURCE_ANOMALY."""
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed(
        conn,
        [
            ("2015-06-30", "2020-01-09", 637.68),
            ("2015-06-30", "2020-03-03", 637.68),
            ("2015-06-30", "2020-04-02", 600.0),
        ],
    )
    res = audit(conn)
    assert len(res["anomalies"]) == 1
    assert res["anomalies"][0]["anomaly_type"] == ANOM_REVISION
    # 3 vintage đều >500t → không phải spike 1-vintage
    assert len(res["anomalies"][0]["spike_vintages"]) == 3
    conn.close()


def test_audit_classifies_revision_large_without_spike():
    """Revision lớn nhưng không giá trị nào >500t → REVISION_LARGE."""
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed(
        conn,
        [
            ("2024-05-31", "2024-08-02", 13.23),
            ("2024-05-31", "2024-10-03", -156.25),
            ("2024-05-31", "2024-11-01", 13.34),
        ],
    )
    res = audit(conn)
    assert len(res["anomalies"]) == 1
    assert res["anomalies"][0]["anomaly_type"] == ANOM_REVISION
    conn.close()


def test_audit_pit_usable_count():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed(
        conn,
        [
            ("2024-01-31", "2024-03-05", 38.0),
            ("2024-06-30", "2024-08-02", 12.0),
            ("2026-04-30", "2026-06-03", 18.0),
        ],
    )
    res = audit(conn)
    # tại 2025-01-05: obs 2024-01 (pub 03/2024) + 2024-06 (pub 08/2024) usable
    assert res["pit_usable"]["2025-01-05"] == 2
    # mốc cố định 2024-01-05: chưa obs nào công bố (first pub = 2024-03-05)
    assert res["pit_usable"]["2024-01-05"] == 0
    conn.close()


def test_audit_single_vintage_zero_revision():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed(conn, [("2025-01-31", "2025-03-03", 30.0)])
    res = audit(conn)
    assert res["revisions"]["2025-01-31"] == 0.0
    assert res["anomalies"] == []
    conn.close()
