"""test_evidence_ledger.py — TDD cho Evidence Ledger.

Bối cảnh: ledger ghi MỌI decision candidate (EXECUTE/WATCH/REJECT) phân biệt
`action` (Governor khuyến nghị) vs `decision` (Policy cuối cùng). Chỉ
NEW_CAPITAL_DEPLOYMENT (BUY/SCALE_IN) tiêu Decision Slot.
"""

import pytest

from calibration.evidence_ledger import (
    CAPITAL_DEPLOYMENT_ACTIONS,
    insert_decision,
    get_budget_usage,
    get_decisions,
    init_schema,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "ledger_test.db"
    init_schema(str(p))
    return str(p)


def _rec(db, *, date="2026-01-05", symbol="DGC", action="BUY", decision="EXECUTE",
         quality="Strong", n_ind=3, consumed=True, budget=20, **kw):
    return insert_decision(
        db_path=db, date=date, symbol=symbol, action=action, decision=decision,
        decision_quality=quality, n_independent_evidence=n_ind,
        decision_budget_year=int(date[:4]), slot_consumed=consumed,
        decision_budget=budget,
        evidence_clusters={"macro": {"states": ["US_LIQUIDITY"], "sources": ["regional_influence_engine"]}},
        **kw,
    )


def test_schema_init_creates_table(db):
    conn = __import__("sqlite3").connect(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='decision_ledger'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_insert_returns_id(db):
    assert _rec(db) >= 1


def test_slot_index_auto_increment(db):
    _rec(db, date="2026-01-05", symbol="DGC")
    _rec(db, date="2026-01-06", symbol="HPG")
    _rec(db, date="2026-01-07", symbol="GAS")
    decisions = get_decisions(db, slot_consumed_only=True, budget_year=2026)
    assert [d["slot_index"] for d in decisions] == [1, 2, 3]


def test_watch_reject_do_not_consume_slot(db):
    _rec(db, date="2026-01-05", symbol="DGC", decision="EXECUTE", consumed=True)
    _rec(db, date="2026-01-06", symbol="HPG", action="BUY", decision="WATCH", consumed=False)
    _rec(db, date="2026-01-07", symbol="GAS", action="BUY", decision="REJECT", consumed=False)
    usage = get_budget_usage(db, 2026)
    assert usage["used"] == 1
    assert usage["remaining"] == 19


def test_budget_usage_year_isolation(db):
    _rec(db, date="2025-12-30", symbol="A")
    _rec(db, date="2026-01-05", symbol="DGC")
    assert get_budget_usage(db, 2025)["used"] == 1
    assert get_budget_usage(db, 2026)["used"] == 1


def test_action_decision_distinction(db):
    _rec(db, date="2026-01-06", symbol="HPG", action="BUY", decision="WATCH",
         consumed=False, decision_reason="BUDGET_EXHAUSTED")
    rows = get_decisions(db, decision="WATCH")
    assert len(rows) == 1
    assert rows[0]["action"] == "BUY"
    assert rows[0]["decision"] == "WATCH"
    assert rows[0]["decision_reason"] == "BUDGET_EXHAUSTED"
    assert rows[0]["slot_consumed"] == 0


def test_evidence_clusters_json_roundtrip(db):
    _rec(db)
    rows = get_decisions(db)
    assert isinstance(rows[0]["evidence_clusters"], dict)
    assert rows[0]["evidence_clusters"]["macro"]["states"] == ["US_LIQUIDITY"]


def test_metadata_versions_persisted(db):
    _rec(db)
    rows = get_decisions(db)
    assert rows[0]["policy_version"] == "budget-v1"
    assert rows[0]["governor_version"] == "company_state.v1"
    assert rows[0]["evidence_cluster_version"] == "clustering-v1"
    assert rows[0]["decision_budget"] == 20


def test_capital_deployment_actions_definition():
    # OPEN là action mở vị thế của Governor — phải tiêu budget.
    assert CAPITAL_DEPLOYMENT_ACTIONS == {"BUY", "SCALE_IN", "OPEN"}


def test_filter_by_decision_and_year(db):
    _rec(db, date="2026-01-05", symbol="A", decision="EXECUTE", consumed=True)
    _rec(db, date="2026-02-01", symbol="B", action="BUY", decision="WATCH", consumed=False)
    _rec(db, date="2025-03-01", symbol="C", decision="EXECUTE", consumed=True)
    assert len(get_decisions(db, decision="EXECUTE")) == 2
    assert len(get_decisions(db, decision="EXECUTE", budget_year=2026)) == 1
