"""
Decision Tensor v1.0 — Decision Abstraction Engine (Phase 10.1).
Compresses 5 engine layers → 1 action vector.
Semantic compression layer, NOT a signal generator.
"""
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.engine.regime_engine import detect_regime
from src.portfolio import exposure_engine, memory_engine

logger = logging.getLogger(__name__)

MAX_HEAT = 10.0


@dataclass
class DecisionAction:
    action: str  # ENTER / HOLD / REDUCE / EXIT / STAND_DOWN
    confidence: int  # 0–100
    risk_state: str  # SAFE / CAUTION / STRESS / LOCKED
    reason: str = ""
    constraint: str = "ALLOWED"  # ALLOWED / BLOCKED / PARTIAL
    suggested_size_mult: float = 1.0  # 0.0–1.0


@dataclass
class DecisionTensorInput:
    model_a_signal: str = "NOBUY"
    model_a_score: float = 0.0
    model_b_signal: str = "NOBUY"
    model_b_score: float = 0.0
    regime: str = "RANGING"
    regime_confidence: float = 0.5
    portfolio_heat: float = 0.0
    net_exposure_pct: float = 0.0
    dampener_result: dict = field(default_factory=lambda: {
        "net_dampener": 1.0, "legacy_dampener": 1.0,
        "decay_factor": 1.0, "boost_factor": 1.0, "regime_bias": 1.0,
        "reason": "NORMAL", "streak_losses": 0, "win_rate_10d": None,
    })
    throttle_state: str = "NORMAL"
    throttle_multiplier: float = 1.0
    memory_perf_a: dict = field(default_factory=lambda: {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "expectancy": 0.0})
    memory_perf_b: dict = field(default_factory=lambda: {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "expectancy": 0.0})


WEIGHT_REGIME = 0.30
WEIGHT_HEAT = 0.25
WEIGHT_SIGNAL = 0.20
WEIGHT_DAMPENER = 0.15
WEIGHT_MEMORY = 0.10


def _regime_score(regime: str, confidence: float) -> float:
    """Higher score = more favorable for action. CRISIS → 0.0, TRENDING → 1.0."""
    base = {"CRISIS": 0.0, "RECOVERY": 0.4, "RANGING": 0.5, "TRENDING": 1.0}
    return base.get(regime, 0.3) * confidence


def _heat_score(heat: float) -> float:
    """0 = safe (low heat), 1 = danger (max heat)."""
    return max(0.0, 1.0 - (heat / MAX_HEAT))


def _signal_score(model_a_signal: str, model_b_signal: str,
                  model_a_score: float, model_b_score: float, regime: str) -> float:
    from src.portfolio.decision_fusion import arbitrate
    fusion = arbitrate(model_a_signal, model_b_signal, regime)
    if fusion["action"] in ("STAND_DOWN", "FORCE_CASH", "MUTED"):
        return 0.0
    cm = fusion.get("confidence_multiplier", 1.0)
    base = max(model_a_score, model_b_score)
    return min(base * cm, 1.0)


def _dampener_score(net_dampener: float) -> float:
    """0.25x dampener → 0.25 score, 1.0 → 1.0 score."""
    return net_dampener


def _memory_score(perf_a: dict, perf_b: dict, regime: str, active_model: str) -> float:
    perf = perf_a if active_model == "MODEL_A" else perf_b
    if perf["trades"] < 3:
        return 0.5
    wr = perf.get("win_rate", 0.0)
    exp = perf.get("expectancy", 0.0)
    base = 0.5
    if wr > 0.55:
        base += 0.25
    if wr > 0.65:
        base += 0.15
    if exp > 0.3:
        base += 0.1
    if wr < 0.35:
        base -= 0.3
    if exp < -0.3:
        base -= 0.2
    return max(0.0, min(1.0, base))


def _resolve_risk_state(heat: float, throttle_state: str, dampener: float, drawdown: float = 0.0) -> str:
    if throttle_state == "EMERGENCY_LOCK" or heat >= MAX_HEAT:
        return "LOCKED"
    if heat >= MAX_HEAT * 0.7 or drawdown >= 5.0:
        return "STRESS"
    if heat >= MAX_HEAT * 0.4 or dampener <= 0.5:
        return "CAUTION"
    return "SAFE"


def _resolve_action(action_score: float, risk_state: str,
                    regime: str, heat: float, dampener: float,
                    net_exposure: float) -> str:
    if risk_state == "LOCKED":
        return "EXIT" if net_exposure > 0 else "STAND_DOWN"
    if risk_state == "STRESS":
        return "REDUCE" if net_exposure > 5 else "HOLD"
    if regime == "CRISIS":
        return "STAND_DOWN"
    if dampener <= 0.5 and net_exposure > 0:
        return "REDUCE"
    if action_score <= 0.2:
        return "STAND_DOWN"
    if action_score <= 0.4:
        return "HOLD"
    if action_score >= 0.65:
        return "ENTER"
    return "HOLD"


def _resolve_constraint(heat: float, dampener: float, regime: str,
                        throttle_state: str, streak_losses: int) -> tuple:
    if throttle_state == "EMERGENCY_LOCK":
        return "BLOCKED", 0.0
    if heat >= MAX_HEAT * 0.7 or regime == "CRISIS":
        return "BLOCKED", 0.0
    if dampener <= 0.5:
        return "PARTIAL", 0.5
    if heat >= MAX_HEAT * 0.4:
        return "PARTIAL", 0.7
    if streak_losses >= 4:
        return "PARTIAL", 0.5
    return "ALLOWED", 1.0


def _reason_compressed(action: str, risk_state: str, regime: str,
                       heat: float, dampener: float, action_score: float,
                       streak: int, throttle_state: str) -> str:
    parts = []
    parts.append(f"Regime: {regime}")
    if risk_state != "SAFE":
        parts.append(f"Risk: {risk_state} ({heat:.1f}% heat)")
    if dampener < 0.8:
        parts.append(f"Dampener: {dampener:.2f}x")
    if streak >= 3:
        parts.append(f"Streak: {streak}L")
    if throttle_state != "NORMAL":
        parts.append(f"Throttle: {throttle_state}")
    if action_score < 0.5 and action in ("HOLD", "STAND_DOWN"):
        parts.append(f"Score: {action_score:.0f}/100")
    return " | ".join(parts)


def compute(override_input: Optional[DecisionTensorInput] = None) -> dict:
    if override_input:
        inp = override_input
    else:
        regime_verdict = detect_regime()
        regime = regime_verdict.get("status", "RANGING")
        regime_score_raw = regime_verdict.get("regime_score", 50)
        regime_confidence = abs(regime_score_raw - 50) / 50.0

        dampener_result = exposure_engine.calculate_total_dampener("MODEL_A", regime)
        heat = exposure_engine.get_portfolio_heat()
        throttle = exposure_engine.evaluate_global_risk_throttle(heat)
        mem_a = memory_engine.get_model_regime_performance("MODEL_A", regime)
        mem_b = memory_engine.get_model_regime_performance("MODEL_B", regime)

        inp = DecisionTensorInput(
            regime=regime,
            regime_confidence=regime_confidence,
            portfolio_heat=heat,
            dampener_result=dampener_result,
            throttle_state=throttle.get("risk_state", "NORMAL"),
            throttle_multiplier=throttle.get("multiplier", 1.0),
            memory_perf_a=mem_a,
            memory_perf_b=mem_b,
        )

    r_score = _regime_score(inp.regime, inp.regime_confidence)
    h_score = _heat_score(inp.portfolio_heat)
    s_score = _signal_score(inp.model_a_signal, inp.model_b_signal,
                            inp.model_a_score, inp.model_b_score, inp.regime)
    d_score = _dampener_score(inp.dampener_result["net_dampener"])
    active_model = "MODEL_A" if inp.regime in ("TRENDING",) else "MODEL_B"
    m_score = _memory_score(inp.memory_perf_a, inp.memory_perf_b, inp.regime, active_model)

    raw_score = (
        WEIGHT_REGIME * r_score +
        WEIGHT_HEAT * h_score +
        WEIGHT_SIGNAL * s_score +
        WEIGHT_DAMPENER * d_score +
        WEIGHT_MEMORY * m_score
    )
    action_score = round(max(0.0, min(1.0, raw_score)) * 100)

    risk_state = _resolve_risk_state(
        inp.portfolio_heat, inp.throttle_state,
        inp.dampener_result["net_dampener"]
    )
    action = _resolve_action(
        action_score / 100, risk_state,
        inp.regime, inp.portfolio_heat,
        inp.dampener_result["net_dampener"],
        inp.net_exposure_pct,
    )
    constraint, size_mult = _resolve_constraint(
        inp.portfolio_heat, inp.dampener_result["net_dampener"],
        inp.regime, inp.throttle_state,
        inp.dampener_result["streak_losses"],
    )
    reason = _reason_compressed(
        action, risk_state, inp.regime,
        inp.portfolio_heat, inp.dampener_result["net_dampener"],
        action_score, inp.dampener_result["streak_losses"],
        inp.throttle_state,
    )

    decision = DecisionAction(
        action=action,
        confidence=action_score,
        risk_state=risk_state,
        reason=reason,
        constraint=constraint,
        suggested_size_mult=size_mult,
    )
    return asdict(decision)
