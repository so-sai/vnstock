"""test_replay_ledger_2022.py — TDD cho Decision System Replay harness."""

import sqlite3

import pytest

from backtest.replay_ledger_2022 import (
    get_trading_days,
    get_universe_adv20,
    run_day,
)


@pytest.fixture
def price_db(tmp_path):
    """DB giả với 3 symbol, ADV đủ lớn, 5 phiên."""
    p = tmp_path / "prices.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)"
    )
    rows = []
    for d in ["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07"]:
        rows.append(("VNINDEX", d, 100, 101, 99, 100, 100000))
        for sym in ("A", "B", "C"):
            rows.append((sym, d, 10, 11, 9, 10, 100000))
    conn.executemany(
        "INSERT INTO daily_ohlcv (symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(p)


def test_get_trading_days(price_db):
    days = get_trading_days("2022-01-01", "2022-12-31", db_path=price_db)
    assert days == ["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07"]


def test_get_universe_adv20(price_db):
    uni = get_universe_adv20("2022-01-07", db_path=price_db)
    assert set(uni) == {"A", "B", "C"}
    # VNINDEX không thuộc universe (chỉ số, không phải cổ phiếu)
    assert "VNINDEX" not in uni


def test_run_day_writes_ledger(tmp_path, price_db):
    """Contract hiện hành (refactor in-memory queue, 836b4bb):

    run_day ghi vào SQLite :memory: và trả rows qua stats["_rows"]/_cols;
    việc ghi file ledger do write_ledger_once() đảm nhận (1 executemany,
    worker không đụng disk → không lock contention khi multiprocessing).

    Test phủ đủ pipeline: run_day (in-memory) → write_ledger_once (file)
    → get_decisions đọc lại đúng PIT date.
    """
    from calibration.evidence_ledger import get_decisions, init_schema
    from backtest.replay_ledger_2022 import write_ledger_once

    ledger = str(tmp_path / "ledger.db")
    init_schema(ledger)
    st = run_day("2022-01-07", ["A", "B", "C"], ledger, budget=20)
    assert st["date"] == "2022-01-07"
    assert st["n_symbols"] == 3
    # In-memory stage: run_day đã thu được 3 rows nhưng CHƯA ghi file
    assert st["n_recorded"] == 3
    assert len(st["_rows"]) == 3

    assert get_decisions(ledger, date_from="2022-01-07", date_to="2022-01-07") == []

    # File stage: main process ghi 1 lần rồi áp budget constraint
    write_ledger_once([st], ledger, budget=20)

    rows = get_decisions(ledger, date_from="2022-01-07", date_to="2022-01-07")
    assert len(rows) == 3
    for r in rows:
        assert r["date"] == "2022-01-07"  # PIT: đúng ngày replay
