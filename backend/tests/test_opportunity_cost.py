"""test_opportunity_cost.py — TDD cho counterfactual measurement."""

import sqlite3
from datetime import timedelta

import pytest

from calibration.evidence_ledger import init_schema, insert_decision
from calibration.opportunity_cost import compute_opportunity_cost, resolve_outcomes


@pytest.fixture
def ledger_db(tmp_path):
    p = tmp_path / "ledger.db"
    init_schema(str(p))
    return str(p)


def _make_dates(n: int, start: str = "2026-01-05"):
    """Tạo n ngày giao dịch liên tiếp (skip cuối tuần)."""
    from datetime import datetime

    dt = datetime.strptime(start, "%Y-%m-%d")
    out = []
    while len(out) < n:
        if dt.weekday() < 5:
            out.append(dt.strftime("%Y-%m-%d"))
        dt += timedelta(days=1)
    return out


@pytest.fixture
def price_db(tmp_path):
    p = tmp_path / "prices.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE daily_ohlcv (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)"
    )
    # A: entry 100 → phiên thứ 20 sau = 110 (+10%), phiên 60 = 130, phiên 120 = 170
    # B: entry 100 → phiên 20 = 120 (+20%); C: chỉ 5 phiên → skip (không đủ H=20).
    dates = _make_dates(130)
    rows = []
    for i, d in enumerate(dates, start=0):
        rows.append(("VNINDEX", d, 1000, 1001, 999, 1000, 100000))
        rows.append(("A", d, 100, 101, 99, 100 + i, 1000))
        rows.append(("B", d, 100, 101, 99, 100 + 2 * i, 1000))
        if i <= 5:
            rows.append(("C", d, 100, 101, 99, 100 + i, 1000))
    conn.executemany(
        "INSERT INTO daily_ohlcv (symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(p)


def _buy(ledger_db, *, date="2026-01-05", symbol="A", decision="EXECUTE",
         p_gain=0.8, consumed=True, budget_year=2026):
    return insert_decision(
        db_path=ledger_db, date=date, symbol=symbol, action="BUY",
        decision=decision, p_gain=p_gain, decision_quality="Strong",
        n_independent_evidence=3, slot_consumed=consumed,
        decision_budget_year=budget_year,
    )


def test_resolve_outcomes_computes_return_pct(ledger_db, price_db):
    _buy(ledger_db, symbol="A")
    r = resolve_outcomes(db_path=ledger_db, price_db=price_db, hold_days=20)
    assert r["n_resolved"] == 1
    assert r["details"][0]["return_pct"] == pytest.approx(20.0)  # close phiên 20 = 120


def test_resolve_outcomes_multi_horizon(ledger_db, price_db):
    from calibration.evidence_ledger import get_decisions

    _buy(ledger_db, symbol="A")
    resolve_outcomes(db_path=ledger_db, price_db=price_db, hold_days=20)
    row = get_decisions(ledger_db)[0]
    assert row["return_pct"] == pytest.approx(20.0)      # close phiên 20 = 100+20 = 120
    assert row["return_pct_60"] == pytest.approx(60.0)   # close phiên 60 = 160
    assert row["return_pct_120"] == pytest.approx(120.0) # close phiên 120 = 220
    assert row["outcome_status"] == "RESOLVED"


def test_skip_when_no_exit_price(ledger_db, price_db):
    _buy(ledger_db, symbol="C")
    r = resolve_outcomes(db_path=ledger_db, price_db=price_db, hold_days=20)
    assert r["n_skipped"] == 1
    assert r["n_resolved"] == 0


def test_opportunity_cost_uses_best_eligible_rejected(ledger_db, price_db):
    _buy(ledger_db, symbol="A", decision="EXECUTE", p_gain=0.8, consumed=True)
    _buy(ledger_db, symbol="B", decision="WATCH", p_gain=0.75, consumed=False)
    _buy(ledger_db, symbol="C", decision="REJECT", p_gain=0.4, consumed=False)
    r = compute_opportunity_cost(db_path=ledger_db, price_db=price_db, hold_days=20)
    assert r["n_opportunity_cost_updated"] == 1
    assert r["n_no_alternative"] == 0
    from calibration.evidence_ledger import get_decisions
    exe = [d for d in get_decisions(ledger_db) if d["decision"] == "EXECUTE"][0]
    assert exe["counterfactual_symbol"] == "B"
    assert exe["counterfactual_return"] == pytest.approx(40.0)   # B phiên 20 = 140
    assert exe["opportunity_cost"] == pytest.approx(20.0)  # 40 - 20


def test_no_alternative_gives_null_not_zero(ledger_db, price_db):
    _buy(ledger_db, symbol="A", decision="EXECUTE", p_gain=0.8, consumed=True)
    r = compute_opportunity_cost(db_path=ledger_db, price_db=price_db, hold_days=20)
    assert r["n_opportunity_cost_updated"] == 0
    assert r["n_no_alternative"] == 1
    from calibration.evidence_ledger import get_decisions
    exe = get_decisions(ledger_db)[0]
    assert exe["opportunity_cost"] is None


def test_low_pgain_rejected_not_eligible(ledger_db, price_db):
    # B reject với p_gain < 0.5 → không đủ điều kiện alternative → OC = NULL
    _buy(ledger_db, symbol="A", decision="EXECUTE", p_gain=0.8, consumed=True)
    _buy(ledger_db, symbol="B", decision="WATCH", p_gain=0.49, consumed=False)
    r = compute_opportunity_cost(db_path=ledger_db, price_db=price_db, hold_days=20)
    assert r["n_no_alternative"] == 1
    assert r["n_opportunity_cost_updated"] == 0


def test_hold_action_not_an_alternative(ledger_db, price_db):
    _buy(ledger_db, symbol="A", decision="EXECUTE", p_gain=0.8, consumed=True)
    insert_decision(
        db_path=ledger_db, date="2026-01-05", symbol="B", action="HOLD",
        decision="HOLD", p_gain=0.9, decision_quality="Strong",
        n_independent_evidence=3, slot_consumed=False, decision_budget_year=2026,
    )
    r = compute_opportunity_cost(db_path=ledger_db, price_db=price_db, hold_days=20)
    assert r["n_no_alternative"] == 1
