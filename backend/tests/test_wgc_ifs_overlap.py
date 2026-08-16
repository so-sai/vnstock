"""test_wgc_ifs_overlap.py — Gate O1 unit tests (in-memory DB, no network).

Kiểm tra:
  - latest_at / first_available resolve đúng PIT (không nhìn future).
  - paired_frame chỉ pair obs có CẢ HAI, không forward-fill.
  - diagnostics: pearson/spearman/sign-agreement/confusion đúng.
  - rolling_grid sinh monthly marks, skip t không có pair.
  - exclude_anomaly loại obs spike 1-vintage khỏi pair.
  - revision_stability: first vs final delta, sign flips.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.gold_h2_series import init_table, insert_vintage
from src.research.wgc_ifs_overlap import (
    IFS_SERIES,
    WGC_ENTITY,
    WGC_SERIES,
    coverage,
    detect_transient,
    diagnostics,
    final_observed_view,
    first_available,
    first_available_view,
    latest_at,
    paired_frame,
    revision_stability,
    rolling_grid,
)


def _seed_wgc(conn, obs_pub_val):
    for obs, pub, val in obs_pub_val:
        insert_vintage(
            conn, series=WGC_SERIES, entity=WGC_ENTITY,
            observation_date=obs, publication_date=pub, value=val,
            unit="tonnes", source="WGC_CB_BLOG", provenance="TEST",
        )


def _seed_ifs(conn, obs_pub_val):
    for obs, pub, val in obs_pub_val:
        insert_vintage(
            conn, series=IFS_SERIES, entity=WGC_ENTITY,
            observation_date=obs, publication_date=pub, value=val,
            unit="tonnes", source="IMF_IFS", provenance="TEST",
        )


# ── latest_at / first_available ─────────────────────────────────────────────

def test_latest_at_respects_publication_deadline():
    rows = [
        ("2024-01-31", "2024-03-05", 10.0),
        ("2024-01-31", "2024-04-02", 12.0),  # revision
        ("2024-02-29", "2024-05-03", 15.0),
    ]
    at_mar = latest_at(rows, "2024-03-31")
    assert at_mar == {"2024-01-31": ("2024-03-05", 10.0)}  # revision chưa tới
    at_apr = latest_at(rows, "2024-04-30")
    assert at_apr["2024-01-31"] == ("2024-04-02", 12.0)
    # 2024-02-29 có pub 05/2024 → chưa visible tại 04/2024
    assert "2024-02-29" not in at_apr


def test_first_available_takes_min_publication():
    rows = [
        ("2024-01-31", "2024-04-02", 12.0),
        ("2024-01-31", "2024-03-05", 10.0),  # first pub
        ("2024-02-29", "2024-05-03", 15.0),
    ]
    fa = first_available(rows)
    assert fa["2024-01-31"] == ("2024-03-05", 10.0)


# ── paired_frame ────────────────────────────────────────────────────────────

def test_paired_frame_only_common_obs_no_forward_fill():
    wgc = {"2024-01-31": ("2024-03-05", 10.0), "2024-02-29": ("2024-05-03", 15.0)}
    ifs = {"2024-01-31": ("2024-04-02", 12.0)}  # thiếu 2024-02
    pairs = paired_frame(wgc, ifs)
    assert [p["obs"] for p in pairs] == ["2024-01-31"]
    assert len(pairs) == 1


# ── diagnostics ─────────────────────────────────────────────────────────────

def test_diagnostics_pearson_spearman_sign():
    pairs = [
        {"obs": "2024-01-31", "wgc": 10.0, "ifs": 9.0},
        {"obs": "2024-02-29", "wgc": 12.0, "ifs": 11.5},
        {"obs": "2024-03-31", "wgc": 15.0, "ifs": 14.0},
        {"obs": "2024-04-30", "wgc": 18.0, "ifs": 17.5},
    ]
    d = diagnostics(pairs)
    assert d["n"] == 4
    assert d["pearson"] is not None and d["pearson"] > 0.95
    assert d["spearman"] is not None and d["spearman"] > 0.9
    assert d["sign_agreement"] == 1.0
    assert d["confusion"] == {"TP": 4, "FP": 0, "FN": 0, "TN": 0}


def test_diagnostics_sign_confusion_matrix():
    pairs = [
        {"obs": "2024-01-31", "wgc": 10.0, "ifs": 8.0},   # TP
        {"obs": "2024-02-29", "wgc": -5.0, "ifs": -3.0},  # TN
        {"obs": "2024-03-31", "wgc": 7.0, "ifs": -2.0},   # FN (WGC+ IFS-)
        {"obs": "2024-04-30", "wgc": -4.0, "ifs": 6.0},   # FP (WGC- IFS+)
    ]
    d = diagnostics(pairs)
    assert d["sign_agreement"] == 0.5
    assert d["confusion"] == {"TP": 1, "FP": 1, "FN": 1, "TN": 1}


def test_diagnostics_short_series_returns_none_corr():
    d = diagnostics([{"obs": "a", "wgc": 1.0, "ifs": 2.0}])
    assert d["n"] == 1
    assert d["pearson"] is None
    assert d["spearman"] is None


# ── rolling_grid ────────────────────────────────────────────────────────────

def test_rolling_grid_monthly_and_skip_no_pair():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed_wgc(conn, [("2024-01-31", "2024-03-05", 10.0)])
    _seed_ifs(conn, [("2024-01-31", "2024-03-05", 12.0)])
    grid = rolling_grid(conn)
    assert grid, "phải có ít nhất 1 mốc có pair"
    # các mốc trước 2024-03-05 không có pair → skip
    assert all(r["t"] >= "2024-03-05" for r in grid)
    # mốc đầu tiên = 2024-03-05 (WGC pub đầu tiên), n=1
    assert grid[0]["t"] == "2024-03-05"
    assert grid[0]["n"] == 1
    conn.close()


# ── exclude_anomaly ─────────────────────────────────────────────────────────

def test_exclude_anomaly_drops_spike_obs():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed_wgc(conn, [
        ("2024-07-31", "2024-09-03", 37.0),
        ("2024-08-31", "2024-10-03", 20.0),
    ])
    _seed_ifs(conn, [
        ("2024-07-31", "2024-09-03", 37.0),
        ("2024-07-31", "2024-10-03", -2349.18),  # spike
        ("2024-07-31", "2024-11-01", 41.72),
        ("2024-08-31", "2024-10-03", 20.0),
    ])
    raw = final_observed_view(conn, "2026-08-31", exclude_anomaly=False)
    excl = final_observed_view(conn, "2026-08-31", exclude_anomaly=True)
    assert len(raw) == 2
    assert len(excl) == 1
    assert excl[0]["obs"] == "2024-08-31"
    conn.close()


def test_first_available_view_pairs_real_time_values():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed_wgc(conn, [("2024-07-31", "2024-09-03", 37.0)])
    _seed_ifs(conn, [
        ("2024-07-31", "2024-09-03", 37.0),
        ("2024-07-31", "2024-11-01", 41.72),  # revision sau — không nên xuất hiện
    ])
    pairs = first_available_view(conn)
    assert len(pairs) == 1
    assert pairs[0]["ifs"] == 37.0
    assert pairs[0]["ifs_pub"] == "2024-09-03"
    conn.close()


# ── revision_stability / coverage ───────────────────────────────────────────

def test_revision_stability_first_vs_final():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed_ifs(conn, [
        ("2024-05-31", "2024-08-02", 13.0),
        ("2024-05-31", "2024-11-01", 25.0),  # delta 12
        ("2024-06-30", "2024-08-02", 5.0),
        ("2024-06-30", "2024-11-01", -3.0),  # delta 8, sign flip
    ])
    r = revision_stability(conn)
    assert r["n_obs_with_vintage"] == 2
    assert r["mean_abs_delta"] == 10.0
    assert r["sign_flips"] == 1
    conn.close()


def test_coverage_overlap_wgc_only_ifs_only():
    conn = sqlite3.connect(":memory:")
    init_table(conn)
    _seed_wgc(conn, [
        ("2024-01-31", "2024-03-05", 10.0),   # overlap
        ("2024-02-29", "2024-05-03", 15.0),   # WGC-only (IFS chưa seed obs này)
    ])
    _seed_ifs(conn, [
        ("2024-01-31", "2024-03-05", 12.0),
        ("2023-11-30", "2024-01-05", 8.0),    # IFS-only
    ])
    c = coverage(conn)
    assert c["overlap_months"] == 1
    assert c["wgc_only"] == 1
    assert c["ifs_only"] == 1
    assert c["by_year"]["2024"]["overlap"] == 1
    conn.close()


# ── detect_transient ─────────────────────────────────────────────────────────

def test_detect_transient_flags_outlier_mark():
    grid = [
        {"t": "2024-08-05", "n": 4, "pearson": 0.80, "sign_agreement": 1.0},
        {"t": "2024-09-05", "n": 5, "pearson": 0.80, "sign_agreement": 1.0},
        {"t": "2024-10-05", "n": 5, "pearson": -0.70, "sign_agreement": 0.2},  # transient
        {"t": "2024-11-05", "n": 6, "pearson": 0.79, "sign_agreement": 1.0},
        {"t": "2024-12-05", "n": 7, "pearson": 0.86, "sign_agreement": 1.0},
    ]
    flags = detect_transient(grid)
    assert len(flags) == 1
    assert flags[0]["t"] == "2024-10-05"
    assert flags[0]["pearson"] == -0.70


def test_detect_transient_stable_returns_empty():
    grid = [
        {"t": "2024-08-05", "n": 4, "pearson": 0.85, "sign_agreement": 1.0},
        {"t": "2024-09-05", "n": 5, "pearson": 0.86, "sign_agreement": 1.0},
        {"t": "2024-10-05", "n": 5, "pearson": 0.87, "sign_agreement": 1.0},
        {"t": "2024-11-05", "n": 6, "pearson": 0.88, "sign_agreement": 1.0},
        {"t": "2024-12-05", "n": 7, "pearson": 0.86, "sign_agreement": 1.0},
    ]
    assert detect_transient(grid) == []
