import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))

from src.research.capitulation_paper_ledger import (  # noqa: E402
    TABLE,
    forward_metrics,
    init_ledger,
    insert_snapshot,
    report,
    settle,
    trading_maturity,
    zone_bucket,
)


def prices(n=35):
    dates = pd.date_range("2025-01-02", periods=n, freq="B")
    close = [100 + i for i in range(n)]
    return pd.DataFrame({"date": dates, "close": close, "low": [x - 2 for x in close], "high": [x + 3 for x in close]})


def test_zones_and_calendar():
    assert zone_bucket(.30) == "030_050"
    assert zone_bucket(.50) == "050_070"
    assert zone_bucket(.70) == "GE_070"
    p = prices()
    assert trading_maturity(p.date, str(p.date.iloc[0]), 5) == str(p.date.iloc[5].date())


def test_forward_metrics_uses_future_sessions_only():
    p = prices()
    m = forward_metrics(p, str(p.date.iloc[0]))
    assert round(m["ret_h5"], 6) == round(5 / 100 * 100, 6)
    assert round(m["mae_h20"], 6) == -1.0
    assert m["mfe_h20"] == 23.0


def test_append_only_and_settlement(tmp_path):
    db = tmp_path / "ledger.db"
    p = prices()
    init_ledger(db)
    snap = {"snapshot_date": str(p.date.iloc[0]), "p_cap_raw": .6, "p_cap_final": .65, "regime_score": .2, "breadth_pct": 15, "structure_status": "WEAK", "stability_pass": 0, "provenance": "test", "source_ts": "2025-01-02T16:00:00"}
    assert insert_snapshot(snap, p.date, db)
    assert not insert_snapshot(snap, p.date, db)
    assert settle(db, p) == 3
    conn = sqlite3.connect(db)
    row = conn.execute(f"SELECT zone,ret_h5,ret_h20,ret_h30,mae_h20,mfe_h20 FROM {TABLE}").fetchone()
    conn.close()
    assert row[0] == "050_070" and row[1] is not None and round(row[4], 6) == -1.0
    assert report(db)[0]["n"] == 1


def test_pit_same_day_not_in_mae():
    p = prices()
    p.loc[0, "low"] = 1
    assert round(forward_metrics(p, str(p.date.iloc[0]))["mae_h20"], 6) == -1.0
