"""decision_budget.py — Decision Budget Policy (Governor ≠ Policy).

Three-layer separation:
  MODEL OUTPUT → Governor assessment → POLICY outcome

Policy v1 chỉ định nghĩa ONE budget type tiêu slot: NEW_CAPITAL_DEPLOYMENT
(BUY/SCALE_IN = triển khai vốn mới). WATCH/REJECT/HOLD/REDUCE/EXIT không tiêu
slot — không giới hạn khả năng quan sát/suy luận của hệ thống.

`decide()` là PURE (không đọc/ghi DB): caller nạp `remaining` từ ledger
(get_budget_usage) rồi gọi. Chỉ EXECUTE tiêu slot.
"""

from __future__ import annotations

from calibration.evidence_ledger import CAPITAL_DEPLOYMENT_ACTIONS

POLICY_VERSION = "budget-v1"

# Decision outcome sau policy.
EXECUTE = "EXECUTE"
WATCH = "WATCH"
REJECT = "REJECT"

# Lý do không EXECUTE.
REASON_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
REASON_LOW_CONVICTION = "LOW_CONVICTION"


def decide(
    action: str,
    evidence_quality: str,
    remaining: int,
    *,
    strict_quality: bool = False,
) -> dict:
    """Áp Decision Budget policy lên 1 candidate Governor.

    Args:
        action: Governor khuyến nghị (BUY/SCALE_IN/HOLD/REDUCE/EXIT/...).
        evidence_quality: Strong/Mixed/Weak (từ evidence_clustering).
        remaining: số slot còn lại của budget_type trong năm.
        strict_quality: True → chỉ EXECUTE khi Strong (Mixed trở thành WATCH).

    Returns:
        {"decision": "EXECUTE"|"WATCH"|"REJECT"|action,
         "reason": str|None,
         "slot_consumed": bool}
    """
    up = (action or "").strip().upper()

    if up not in CAPITAL_DEPLOYMENT_ACTIONS:
        # Không phải triển khai vốn mới → không tiêu slot, policy không chặn.
        return {"decision": up, "reason": None, "slot_consumed": False}

    quality = (evidence_quality or "").strip().title()
    if quality not in {"Strong", "Mixed", "Weak"}:
        quality = "Weak"

    if remaining <= 0:
        return {"decision": WATCH, "reason": REASON_BUDGET_EXHAUSTED, "slot_consumed": False}

    if quality == "Weak":
        return {"decision": REJECT, "reason": REASON_LOW_CONVICTION, "slot_consumed": False}

    if strict_quality and quality != "Strong":
        return {"decision": WATCH, "reason": "EVIDENCE_INSUFFICIENT", "slot_consumed": False}

    return {"decision": EXECUTE, "reason": None, "slot_consumed": True}


def log_decision_candidate(
    *,
    date: str,
    symbol: str,
    action: str,
    p_gain: float | None = None,
    eu: float | None = None,
    kelly_alloc: float | None = None,
    macro_state: str | None = None,
    transmission_phase: str | None = None,
    sector_phase: str | None = None,
    health_archetype: str | None = None,
    valuation_zone: str | None = None,
    behavior_position: str | None = None,
    shock_band: str | None = None,
    coverage: float | None = None,
    transmission_provenance: str | None = None,
    transmission_coverage: float | None = None,
    sector_provenance: str | None = None,
    sector_coverage: float | None = None,
    decision_budget: int = 20,
    db_path: str | None = None,
    strict_quality: bool = False,
    conn=None,
    commit: bool = True,
) -> int:
    """Ghi 1 decision candidate đầy đủ vào Evidence Ledger (integration point).

    Chạy đủ pipeline:
      evidence_states → cluster_evidence → n_independent + decision_quality
      → decide(action, quality, remaining) → insert_decision

    conn/commit: replay gom nhiều candidate vào 1 transaction (giảm write I/O).
    """
    from calibration.evidence_clustering import cluster_evidence
    from calibration.evidence_ledger import (
        get_budget_usage,
        init_schema,
        insert_decision,
    )

    if conn is None:
        init_schema(db_path)
    cluster = cluster_evidence(
        {
            "macro_state": macro_state,
            "transmission_phase": transmission_phase,
            "sector_phase": sector_phase,
            "health_archetype": health_archetype,
            "valuation_zone": valuation_zone,
            "behavior_position": behavior_position,
        },
        shock_band=shock_band,
        coverage=coverage,
    )
    year = int(date[:4])
    remaining = get_budget_usage(db_path, year, conn=conn)["remaining"]
    pol = decide(action, cluster["decision_quality"], remaining, strict_quality=strict_quality)

    return insert_decision(
        db_path=db_path,
        date=date,
        symbol=symbol,
        action=action,
        decision=pol["decision"],
        decision_reason=pol["reason"],
        decision_quality=cluster["decision_quality"],
        n_independent_evidence=cluster["n_independent_evidence"],
        evidence_clusters=cluster,
        p_gain=p_gain,
        eu=eu,
        kelly_alloc=kelly_alloc,
        decision_budget_year=year,
        slot_consumed=pol["slot_consumed"],
        decision_budget=decision_budget,
        evidence_cluster_version=cluster["version"],
        transmission_provenance=transmission_provenance,
        transmission_coverage=transmission_coverage,
        sector_provenance=sector_provenance,
        sector_coverage=sector_coverage,
        conn=conn,
        commit=commit,
    )
