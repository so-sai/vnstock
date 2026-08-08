"""test_decision_budget.py — TDD cho Decision Budget Policy."""

import pytest

from calibration.decision_budget import (
    EXECUTE,
    REASON_BUDGET_EXHAUSTED,
    REASON_LOW_CONVICTION,
    REJECT,
    WATCH,
    decide,
)


def test_strong_evidence_executes_and_consumes_slot():
    r = decide("BUY", "Strong", remaining=15)
    assert r["decision"] == EXECUTE
    assert r["slot_consumed"] is True
    assert r["reason"] is None


def test_budget_exhausted_blocks_buy_as_watch():
    r = decide("BUY", "Strong", remaining=0)
    assert r["decision"] == WATCH
    assert r["reason"] == REASON_BUDGET_EXHAUSTED
    assert r["slot_consumed"] is False


def test_weak_evidence_rejected_without_slot():
    r = decide("BUY", "Weak", remaining=20)
    assert r["decision"] == REJECT
    assert r["reason"] == REASON_LOW_CONVICTION
    assert r["slot_consumed"] is False


def test_mixed_evidence_executes_by_default():
    r = decide("BUY", "Mixed", remaining=5)
    assert r["decision"] == EXECUTE
    assert r["slot_consumed"] is True


def test_strict_quality_blocks_mixed_as_watch():
    r = decide("BUY", "Mixed", remaining=5, strict_quality=True)
    assert r["decision"] == WATCH
    assert r["reason"] == "EVIDENCE_INSUFFICIENT"
    assert r["slot_consumed"] is False


def test_non_capital_actions_never_consume_slot():
    for action in ("HOLD", "REDUCE", "EXIT", "WAIT"):
        r = decide(action, "Strong", remaining=0)
        assert r["decision"] == action
        assert r["slot_consumed"] is False
        assert r["reason"] is None


def test_scale_in_is_capital_deployment():
    r = decide("SCALE_IN", "Strong", remaining=2)
    assert r["decision"] == EXECUTE
    assert r["slot_consumed"] is True


def test_open_is_capital_deployment():
    # Governor trả OPEN cho lệnh mở vị thế — phải tiêu slot.
    r = decide("OPEN", "Strong", remaining=5)
    assert r["decision"] == EXECUTE
    assert r["slot_consumed"] is True


def test_case_insensitive_action():
    r = decide("buy", "Strong", remaining=3)
    assert r["decision"] == EXECUTE


def test_unknown_quality_treated_as_weak():
    r = decide("BUY", "???", remaining=10)
    assert r["decision"] == REJECT


def _six_states():
    return {
        "macro_state": "EXPANSION",
        "transmission_phase": "TRANSMITTING",
        "sector_phase": "ACCELERATING",
        "health_archetype": "GROWTH",
        "valuation_zone": "OVERVALUED",
        "behavior_position": "FOMO",
    }


def test_log_decision_candidate_strong_executes(tmp_path):
    from calibration.decision_budget import log_decision_candidate
    from calibration.evidence_ledger import get_decisions

    db = str(tmp_path / "ledger.db")
    did = log_decision_candidate(
        date="2026-01-05", symbol="ACB", action="BUY", p_gain=0.8,
        decision_budget=20, db_path=db, **_six_states(),
    )
    assert isinstance(did, int) and did > 0
    rows = get_decisions(db)
    assert len(rows) == 1
    assert rows[0]["decision"] == EXECUTE
    assert rows[0]["slot_consumed"] == 1
    assert rows[0]["n_independent_evidence"] == 4
    assert rows[0]["decision_quality"] == "Strong"


def test_log_decision_candidate_weak_rejected(tmp_path):
    from calibration.decision_budget import log_decision_candidate
    from calibration.evidence_ledger import get_decisions

    db = str(tmp_path / "ledger.db")
    log_decision_candidate(
        date="2026-01-05", symbol="ACB", action="BUY", p_gain=0.3,
        decision_budget=20, db_path=db, macro_state="EXPANSION",
    )
    rows = get_decisions(db)
    assert rows[0]["decision"] == REJECT
    assert rows[0]["slot_consumed"] == 0
