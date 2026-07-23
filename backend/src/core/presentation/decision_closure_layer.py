from .models import (
    DCLReport, DCLVerdictLevel, MarketIntentMode,
    GateScore, CompensationApplied, GateReasonCode, CompCode,
)

# ── Gate definitions ────────────────────────────────────────────────────

GATE_DEFS: list[dict] = [
    {
        "name": "sentinel",
        "weight": 10,
        "pass_if": lambda s: s.get("sentinel_green", False),
        "raw_score_fn": lambda s: 1.0 if s.get("sentinel_green", False) else 0.0,
        "reason_pass": "GATE_SENTINEL_PASS",
        "reason_fail": "GATE_SENTINEL_FAIL",
    },
    {
        "name": "flow",
        "weight": 8,
        "pass_if": lambda s: s.get("flow_bias_score", 0) >= 0.35,
        "raw_score_fn": lambda s: min(1.0, s.get("flow_bias_score", 0) + 0.2),
        "reason_pass": "GATE_FLOW_PASS",
        "reason_fail": "GATE_FLOW_FAIL",
    },
    {
        "name": "breadth",
        "weight": 7,
        "pass_if": lambda s: s.get("breadth_health", 0) >= 30,
        "raw_score_fn": lambda s: min(1.0, s.get("breadth_health", 0) / 100 * 1.3),
        "reason_pass": "GATE_BREADTH_PASS",
        "reason_fail": "GATE_BREADTH_FAIL",
    },
    {
        "name": "structure",
        "weight": 6,
        "pass_if": lambda s: not (s.get("lcr_pct", 30) > 35 and s.get("bdi_signal", "CAN_BANG") != "CAN_BANG"),
        "raw_score_fn": lambda s: 0.0 if (s.get("lcr_pct", 30) > 35 and s.get("bdi_signal", "CAN_BANG") != "CAN_BANG") else (0.5 if s.get("lcr_pct", 30) > 35 else 1.0),
        "reason_pass": "GATE_STRUCTURE_PASS",
        "reason_fail": "GATE_STRUCTURE_FAIL",
    },
    {
        "name": "ssi",
        "weight": 8,
        "pass_if": lambda s: s.get("ssi_score", 0) >= 0.35,
        "raw_score_fn": lambda s: s.get("ssi_score", 0.5),
        "reason_pass": "GATE_SSI_PASS",
        "reason_fail": "GATE_SSI_FAIL",
    },
    {
        "name": "trade_state",
        "weight": 9,
        "pass_if": lambda s: s.get("trade_state_level", "PROHIBITED") not in ("PROHIBITED", "RESTRICTED"),
        "raw_score_fn": lambda s: s.get("trade_state_score", 0.0),
        "reason_pass": "GATE_TRADE_STATE_PASS",
        "reason_fail": "GATE_TRADE_STATE_FAIL",
    },
]

# ── Compensation rules (CRL core) ───────────────────────────────────────

COMPENSATION_RULES: list[dict] = [
    {
        "target": "sentinel",
        "conditions": [
            ("flow_bias_score", 0.65),
            ("ssi_score", 0.35),
        ],
        "trade_state_min": "SELECTIVE",
        "override_weight": 0.60,
        "comp_code": "COMP_SENTINEL_BY_FLOW_SSI",
    },
    {
        "target": "sentinel",
        "conditions": [
            ("flow_bias_score", 0.65),
            ("breadth_health", 40),
        ],
        "trade_state_min": "SELECTIVE",
        "override_weight": 0.50,
        "comp_code": "COMP_SENTINEL_BY_FLOW_BREADTH",
    },
    {
        "target": "breadth",
        "conditions": [
            ("sentinel_green", True),
            ("flow_bias_score", 0.65),
        ],
        "override_weight": 0.50,
        "comp_code": "COMP_BREADTH_BY_SENTINEL_FLOW",
    },
    {
        "target": "breadth",
        "conditions": [
            ("sentinel_green", True),
            ("ssi_score", 0.50),
        ],
        "override_weight": 0.40,
        "comp_code": "COMP_BREADTH_BY_SENTINEL_SSI",
    },
    {
        "target": "ssi",
        "conditions": [
            ("flow_bias_score", 0.65),
            ("breadth_health", 40),
        ],
        "trade_state_min": "SELECTIVE",
        "override_weight": 0.40,
        "comp_code": "COMP_SSI_BY_FLOW_BREADTH",
    },
    {
        "target": "ssi",
        "conditions": [
            ("sentinel_green", True),
            ("trade_state_score", 0.35),
        ],
        "override_weight": 0.35,
        "comp_code": "COMP_SSI_BY_SENTINEL_TRADE_STATE",
    },
    {
        "target": "flow",
        "conditions": [
            ("sentinel_green", True),
            ("ssi_score", 0.50),
        ],
        "trade_state_min": "ACTIVE",
        "override_weight": 0.50,
        "comp_code": "COMP_FLOW_BY_SENTINEL_SSI",
    },
    {
        "target": "flow",
        "conditions": [
            ("breadth_health", 50),
            ("trade_state_score", 0.55),
        ],
        "override_weight": 0.40,
        "comp_code": "COMP_FLOW_BY_BREADTH_TRADE_STATE",
    },
    {
        "target": "structure",
        "conditions": [
            ("sentinel_green", True),
            ("flow_bias_score", 0.65),
            ("breadth_health", 30),
        ],
        "override_weight": 0.40,
        "comp_code": "COMP_STRUCTURE_BY_SENTINEL_FLOW_BREADTH",
    },
    {
        "target": "trade_state",
        "conditions": [
            ("sentinel_green", True),
            ("flow_bias_score", 0.65),
            ("ssi_score", 0.50),
        ],
        "override_weight": 0.50,
        "comp_code": "COMP_TRADE_STATE_BY_SENTINEL_FLOW_SSI",
    },
]


def _resolve_market_intent(gates: dict[str, GateScore], score: float,
                           compensations: list) -> MarketIntentMode:
    sentinel_gate = gates.get("sentinel")
    flow_gate = gates.get("flow")
    ssi_gate = gates.get("ssi")
    sentinel_ok = sentinel_gate and sentinel_gate.effective_score >= 0.6
    flow_ok = flow_gate and flow_gate.effective_score >= 0.6
    ssi_ok = ssi_gate and ssi_gate.effective_score >= 0.5
    has_comp = len(compensations) > 0

    if sentinel_ok and flow_ok and score >= 0.65:
        return "ACCUMULATION"
    if has_comp and score >= 0.45:
        return "ACCUMULATION"
    if not sentinel_ok and not flow_ok:
        return "DISTRIBUTION"
    if score >= 0.35 and not has_comp:
        return "WAITING"
    if ssi_ok and not sentinel_ok:
        return "WAITING"
    return "PRESERVATION"


def _score_to_verdict(score: float) -> tuple[DCLVerdictLevel, bool]:
    if score >= 0.65:
        return ("ACTIONABLE", True)
    elif score >= 0.35:
        return ("OBSERVE", True)
    return ("NO_TRADE", True)


def _check_compensation_rule(rule: dict, state: dict) -> bool:
    for key, min_val in rule.get("conditions", []):
        actual = state.get(key)
        if actual is None:
            return False
        if isinstance(min_val, bool):
            if actual != min_val:
                return False
        elif isinstance(min_val, (int, float)):
            if actual < min_val:
                return False
    ts_min = rule.get("trade_state_min")
    if ts_min:
        ts_level = state.get("trade_state_level", "PROHIBITED")
        ts_order = ["PROHIBITED", "RESTRICTED", "SELECTIVE", "ACTIVE", "AGGRESSIVE"]
        if ts_order.index(ts_level) < ts_order.index(ts_min):
            return False
    return True


def compute_dcl(
    *,
    sentinel_green: bool = False,
    sentinel_label: str = "RED",
    flow_bias_score: float = 0.0,
    flow_label: str = "UNKNOWN",
    breadth_health: float = 0.0,
    lcr_pct: float = 30.0,
    bdi_signal: str = "CAN_BANG",
    ssi_score: float = 0.5,
    trade_state_level: str = "PROHIBITED",
    trade_state_score: float = 0.0,
) -> DCLReport:
    state = {
        "sentinel_green": sentinel_green,
        "sentinel_label": sentinel_label,
        "flow_bias_score": flow_bias_score,
        "flow_label": flow_label,
        "breadth_health": breadth_health,
        "lcr_pct": lcr_pct,
        "bdi_signal": bdi_signal,
        "ssi_score": ssi_score,
        "trade_state_level": trade_state_level,
        "trade_state_score": trade_state_score,
    }

    gates: dict[str, GateScore] = {}
    for g in GATE_DEFS:
        name = g["name"]
        raw_pass = g["pass_if"](state)
        raw_score = g["raw_score_fn"](state)
        reason_code = g["reason_pass"] if raw_pass else g["reason_fail"]

        gates[name] = GateScore(
            name=name,
            weight=g["weight"],
            raw_pass=raw_pass,
            raw_score=min(1.0, raw_score),
            compensation_applied=0.0,
            effective_score=min(1.0, raw_score),
            reason_code=reason_code,
            reason_params={},
            comp_code=None,
        )

    compensations_applied: list[CompensationApplied] = []
    for rule in COMPENSATION_RULES:
        target = rule["target"]
        gate = gates.get(target)
        if gate is None or gate.raw_pass:
            continue
        if _check_compensation_rule(rule, state):
            compensated_by = [c[0] for c in rule.get("conditions", [])]
            gate.compensation_applied = max(gate.compensation_applied, rule["override_weight"])
            gate.effective_score = min(1.0, gate.raw_score + gate.compensation_applied)
            gate.comp_code = rule["comp_code"]
            compensations_applied.append(CompensationApplied(
                target_gate=target,
                compensated_by=compensated_by,
                override_weight=rule["override_weight"],
                comp_code=rule["comp_code"],
            ))

    total_weight = sum(g.weight for g in gates.values())
    weighted_sum = sum(g.weight * g.effective_score for g in gates.values())
    final_score = weighted_sum / total_weight if total_weight > 0 else 0.0
    final_score = round(min(1.0, max(0.0, final_score)), 3)

    verdict, _ = _score_to_verdict(final_score)
    intent = _resolve_market_intent(gates, final_score, compensations_applied)

    return DCLReport(
        verdict=verdict,
        score=final_score,
        gates=gates,
        compensations_applied=compensations_applied,
        compensations_possible=len(COMPENSATION_RULES),
        compensations_triggered=len(compensations_applied),
        market_intent=intent,
    )
