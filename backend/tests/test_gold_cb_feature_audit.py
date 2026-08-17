"""test_gold_cb_feature_audit.py — Unit tests Gate M2 CB audit module.

No network, no DB thật. Kiểm tra:
  - _pit_latest_step: PIT step đúng (latest vintage per obs, pub<=t, không look-ahead).
  - build_cb_features: level + zscore expanding (PIT-safe) + sum3.
  - _redundancy_cb_m1: corr trả đúng key.
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research.gold_cb_feature_audit import (
    _pit_latest_step,
    _redundancy_cb_m1,
    _spearman,
    build_cb_features,
)

# ── Fixtures ────────────────────────────────────────────────────────────────

DATES = pd.date_range("2026-01-05", periods=8, freq="D")  # 01-05..01-12


def _mk_rows():
    """2 obs, obs 2 có 2 vintage (revision)."""
    return [
        ("2025-12-31", 10.0, "2026-01-01"),  # obs1, pub sớm
        ("2026-01-31", 20.0, "2026-02-01"),  # obs2 v1 (chưa pub trong DATES)
        ("2026-01-31", 25.0, "2026-02-15"),  # obs2 v2 (revision, pub sau)
        ("2025-12-31", 12.0, "2026-01-08"),  # obs1 revision, pub 01-08
    ]


def test_pit_latest_step_no_lookahead():
    out = _pit_latest_step(_mk_rows(), DATES)
    # 01-05: chỉ obs1 pub 01-01 -> 10.0
    assert out[0] == 10.0
    # 01-06, 01-07: vẫn 10.0
    assert out[1] == 10.0 and out[2] == 10.0
    # 01-08: obs1 revision (pub 01-08) -> 12.0
    assert out[3] == 12.0
    # 01-09..01-12: vẫn 12.0 (obs2 pub 02-01 > t)
    assert out[4] == 12.0 and out[7] == 12.0


def test_pit_latest_step_obs2_after_pub():
    dates = pd.date_range("2026-02-01", periods=4, freq="D")
    out = _pit_latest_step(_mk_rows(), dates)
    # 02-01: obs2 v1 (pub 02-01) -> 20.0
    assert out[0] == 20.0
    # 02-02..02-04: vẫn 20.0 (revision pub 02-15 chưa tới)
    assert out[1] == 20.0 and out[3] == 20.0


def test_pit_latest_step_revision_takes_over():
    dates = pd.date_range("2026-02-15", periods=2, freq="D")
    out = _pit_latest_step(_mk_rows(), dates)
    # 02-15: obs2 v2 (pub 02-15) -> 25.0
    assert out[0] == 25.0 and out[1] == 25.0


def test_pit_latest_step_empty_returns_nan():
    out = _pit_latest_step([], DATES)
    assert np.all(np.isnan(out))


def test_build_cb_features_columns_and_pit():
    # DB in-memory: insert IFS rows có variation trong DATES để std > 0
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE gold_h2_series (series TEXT, entity TEXT, observation_date TEXT, "
        "publication_date TEXT, value REAL)"
    )
    rows = [
        ("2025-12-31", 10.0, "2026-01-05"),
        ("2026-01-31", 20.0, "2026-02-01"),
        ("2026-01-31", 25.0, "2026-02-15"),
        ("2025-12-31", 12.0, "2026-01-08"),
        ("2025-12-31", 15.0, "2026-01-10"),
        ("2025-12-31", 18.0, "2026-01-12"),
    ]
    for obs, val, pub in rows:
        conn.execute(
            "INSERT INTO gold_h2_series (series, entity, observation_date, publication_date, value) "
            "VALUES ('IFS_GOLD_RESERVE_CHANGE', 'GLOBAL', ?, ?, ?)",
            (obs, pub, val),
        )
    df = pd.DataFrame(index=pd.date_range("2026-01-05", periods=25, freq="D"))
    feats = build_cb_features(df, conn)
    assert set(feats.columns) == {"CB_IFS_level", "CB_IFS_z", "CB_IFS_sum3"}
    # level khớp _pit_latest_step
    expected = _pit_latest_step(rows, df.index)
    assert np.allclose(feats["CB_IFS_level"].values, expected)
    # z: expanding zscore, có giá trị sau warmup (>=20 obs, std > 0)
    assert feats["CB_IFS_z"].notna().any()
    assert feats["CB_IFS_z"].notna().sum() >= 5
    # sum3: trailing rolling (không look-ahead): tại 01-05 chỉ obs1 -> 10
    assert feats["CB_IFS_sum3"].iloc[0] == 10.0
    conn.close()


def test_redundancy_cb_m1_keys():
    panel = pd.DataFrame({"DXY_z": [1.0] * 5, "TIP_mom5": [0.01] * 5})
    feats = pd.DataFrame({"CB_IFS_level": [1.0, 2.0, 3.0, 4.0, 5.0]})
    out = _redundancy_cb_m1(panel, feats)
    assert "spearman_CB_IFS_level_vs_DXY_z" in out
    assert "spearman_CB_IFS_level_vs_TIP_mom5" in out


def test_spearman():
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    assert abs(_spearman(a, b) - 1.0) < 1e-9
    assert np.isnan(_spearman(a, np.full(5, np.nan)))
