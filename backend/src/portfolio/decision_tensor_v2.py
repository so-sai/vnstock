"""
Decision Tensor v2.0 — Cognitive Expansion Layer (Phase 10.2).
Adds counterfactual awareness, hierarchical rationale, override tracking, adaptive weights.
Extends v1 determinism with institutional decision depth.
"""

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def _hydrate_path():
    if getattr(sys, "frozen", False):
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

from src.engine.breakout_continuation import get_breakout_market_context
from src.engine.flow_decay_engine import (
    get_decayed_foreign_summary,
    get_decayed_liquidity_health,
    get_decayed_rotation_beta,
)
from src.engine.liquidity_wave import get_market_liquidity_health
from src.engine.regime_engine import detect_regime
from src.engine.sector_rotation_graph import get_rotation_beta
from src.portfolio import exposure_engine, memory_engine
from src.portfolio.decision_tensor import (
    MAX_HEAT,
    WEIGHT_DAMPENER,
    WEIGHT_HEAT,
    WEIGHT_MEMORY,
    WEIGHT_REGIME,
    WEIGHT_SIGNAL,
    DecisionTensorInput,
    _dampener_score,
    _heat_score,
    _memory_score,
    _reason_compressed,
    _regime_score,
    _resolve_action,
    _resolve_constraint,
    _resolve_risk_state,
    _signal_score,
)
from src.telemetry.recorder import record_decision

logger = logging.getLogger(__name__)

ALL_ACTIONS = ["ENTER", "SCALE_IN", "HOLD", "REDUCE", "EXIT", "STAND_DOWN"]
DECISIONS_FILE = PROJECT_ROOT / "backend" / "data" / "decision_history.json"

# Phase 11 — Asia Market Adaptation weights (rebalanced from v1)
ASIA_WEIGHT_LIQUIDITY = 0.12
ASIA_WEIGHT_SECTOR = 0.05
ASIA_WEIGHT_BREAKOUT = 0.05
# v1 weights reduced accordingly:
V2_WEIGHT_REGIME = 0.25
V2_WEIGHT_HEAT = 0.20
V2_WEIGHT_SIGNAL = 0.15
V2_WEIGHT_DAMPENER = 0.10
V2_WEIGHT_MEMORY = 0.08
# total = 1.00


@dataclass
class AlternativeAction:
    action: str
    score: float
    reason_blocked: str = ""
    blocked_by: str = ""


@dataclass
class RationaleNode:
    label: str
    detail: str
    score_contribution: float | None = None
    children: list = field(default_factory=list)


@dataclass
class CognitiveDecision:
    action: str
    confidence: int
    risk_state: str
    constraint: str
    suggested_size_mult: float
    reason: str
    alternatives: list = field(default_factory=list)
    rationale_tree: list = field(default_factory=list)
    decision_id: str = ""
    override_state: str = "PENDING"
    override_action: str | None = None
    override_reason: str | None = None
    calibrated_weights: dict = field(default_factory=dict)


def _evaluate_action_score(inp: DecisionTensorInput, target_action: str) -> tuple:
    action_score_map = {
        "ENTER": 0.75,
        "SCALE_IN": 0.60,
        "HOLD": 0.45,
        "REDUCE": 0.30,
        "EXIT": 0.15,
        "STAND_DOWN": 0.05,
    }
    ideal_score = action_score_map.get(target_action, 0.5)

    r_score = _regime_score(inp.regime, inp.regime_confidence)
    h_score = _heat_score(inp.portfolio_heat)
    s_score = _signal_score(inp.model_a_signal, inp.model_b_signal, inp.model_a_score, inp.model_b_score, inp.regime)
    d_score = _dampener_score(inp.dampener_result["net_dampener"])
    active_model = "MODEL_A" if inp.regime in ("TRENDING",) else "MODEL_B"
    m_score = _memory_score(inp.memory_perf_a, inp.memory_perf_b, inp.regime, active_model)

    raw_score = (
        WEIGHT_REGIME * r_score
        + WEIGHT_HEAT * h_score
        + WEIGHT_SIGNAL * s_score
        + WEIGHT_DAMPENER * d_score
        + WEIGHT_MEMORY * m_score
    )

    risk_state = _resolve_risk_state(inp.portfolio_heat, inp.throttle_state, inp.dampener_result["net_dampener"])
    action = _resolve_action(
        raw_score, risk_state, inp.regime, inp.portfolio_heat, inp.dampener_result["net_dampener"], inp.net_exposure_pct
    )

    delta = abs(raw_score - ideal_score)
    feasibility = 1.0 - delta
    return raw_score, feasibility, action, risk_state


def _generate_alternatives(inp: DecisionTensorInput, chosen_action: str, chosen_score: float) -> list:
    results = []
    for action in ALL_ACTIONS:
        if action == chosen_action:
            continue
        score, feasibility, suggested_action, risk_state = _evaluate_action_score(inp, action)
        blocked_by = ""
        reason = ""

        if risk_state == "LOCKED" and action in ("ENTER", "SCALE_IN"):
            blocked_by = "RISK_LOCK"
            reason = "Hệ thống đang khóa rủi ro (LOCKED), không thể mở mới"
        elif risk_state == "STRESS" and action in ("ENTER", "SCALE_IN"):
            blocked_by = "HEAT_STRESS"
            reason = f"Nhiệt rủi ro {inp.portfolio_heat:.0f}% — vượt ngưỡng STRESS"
        elif action == "EXIT" and inp.net_exposure_pct <= 0:
            blocked_by = "NO_EXPOSURE"
            reason = "Không có vị thế để thoát"
        elif action == "SCALE_IN" and inp.dampener_result["net_dampener"] <= 0.5:
            blocked_by = "DAMPENER_LOCK"
            reason = "Bộ giảm chấn đang hạn chế, không thể scale-in"
        elif score > chosen_score:
            blocked_by = "HIERARCHY"
            reason = f"Bị ghi đè bởi {chosen_action} — điểm {score:.2f} vs {chosen_score:.2f}"
        else:
            blocked_by = "SCORE_RANK"
            reason = f"Xếp hạng thấp hơn — điểm {score:.2f} (tối đa: {chosen_score:.2f})"

        results.append(
            {
                "action": action,
                "score": round(score * 100),
                "reason_blocked": reason,
                "blocked_by": blocked_by,
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:4]


def _build_rationale_tree(
    inp: DecisionTensorInput,
    r_score: float,
    h_score: float,
    s_score: float,
    d_score: float,
    m_score: float,
    action_score: float,
    risk_state: str,
    action: str,
    constraint: str,
    size_mult: float,
    l_score: float = 0.5,
    sec_score: float = 0.5,
    b_score: float = 0.5,
) -> list:
    factor_weights = {
        "Regime": (V2_WEIGHT_REGIME, r_score),
        "Heat": (V2_WEIGHT_HEAT, h_score),
        "Signal": (V2_WEIGHT_SIGNAL, s_score),
        "Dampener": (V2_WEIGHT_DAMPENER, d_score),
        "Memory": (V2_WEIGHT_MEMORY, m_score),
        "Liquidity": (ASIA_WEIGHT_LIQUIDITY, l_score),
        "Sector": (ASIA_WEIGHT_SECTOR, sec_score),
        "Breakout": (ASIA_WEIGHT_BREAKOUT, b_score),
    }

    root = RationaleNode(
        label=f"Quyết định: {action} (độ tin cậy {action_score:.0f}%)",
        detail=f"Điểm tổng hợp từ {len(factor_weights)} nhân tố",
        score_contribution=action_score / 100,
        children=[],
    )

    for name, (weight, score) in sorted(factor_weights.items(), key=lambda x: -x[1][0] * x[1][1]):
        contribution = weight * score
        detail_map = {
            "Regime": f"Chế độ thị trường: {inp.regime} (độ tin cậy {inp.regime_confidence:.0%})",
            "Heat": f"Nhiệt danh mục: {inp.portfolio_heat:.1f}/{MAX_HEAT:.0f}",
            "Signal": f"Tín hiệu A: {inp.model_a_signal} | B: {inp.model_b_signal}",
            "Dampener": f"Bộ giảm chấn: {inp.dampener_result['net_dampener']:.2f}x",
            "Memory": f"Win rate: {inp.memory_perf_a.get('win_rate', 0):.0%}",
            "Liquidity": "Sóng thanh khoản (retail chase + volume accel)",
            "Sector": "Xoay vòng ngành (rotation phase + flow alignment)",
            "Breakout": "Breakout continuation (multi-TF + liquidity confirm)",
        }
        child = RationaleNode(
            label=f"{name}: {contribution:.2f} (w={weight})",
            detail=detail_map.get(name, ""),
            score_contribution=round(contribution, 3),
        )
        root.children.append(asdict(child))

    risk_child = RationaleNode(
        label=f"Trạng thái rủi ro: {risk_state}",
        detail=f"Constraint: {constraint} (size_mult: {size_mult:.1f}x)",
        score_contribution=action_score / 100,
    )
    root.children.append(asdict(risk_child))

    return [asdict(root)]


def _load_decision_history() -> list:
    try:
        if DECISIONS_FILE.exists():
            with open(DECISIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Cannot load decision history: {e}")
    return []


def _save_decision_history(history: list):
    try:
        DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DECISIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-200:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save decision history: {e}")


def _calibrate_weights(history: list) -> dict:
    recent = [d for d in history if d.get("outcome") is not None][-30:]
    if len(recent) < 5:
        return {
            "regime": V2_WEIGHT_REGIME,
            "heat": V2_WEIGHT_HEAT,
            "signal": V2_WEIGHT_SIGNAL,
            "dampener": V2_WEIGHT_DAMPENER,
            "memory": V2_WEIGHT_MEMORY,
            "liquidity": ASIA_WEIGHT_LIQUIDITY,
            "sector": ASIA_WEIGHT_SECTOR,
            "breakout": ASIA_WEIGHT_BREAKOUT,
        }

    factor_names = ["regime", "heat", "signal", "dampener", "memory", "liquidity", "sector", "breakout"]
    base_weights = {
        "regime": V2_WEIGHT_REGIME,
        "heat": V2_WEIGHT_HEAT,
        "signal": V2_WEIGHT_SIGNAL,
        "dampener": V2_WEIGHT_DAMPENER,
        "memory": V2_WEIGHT_MEMORY,
        "liquidity": ASIA_WEIGHT_LIQUIDITY,
        "sector": ASIA_WEIGHT_SECTOR,
        "breakout": ASIA_WEIGHT_BREAKOUT,
    }

    correct_by_factor = {f: 0 for f in factor_names}
    total_correct = 0

    for entry in recent:
        if entry.get("outcome") == "CORRECT":
            total_correct += 1
            for factor in correct_by_factor:
                correct_by_factor[factor] += 1

    if total_correct == 0:
        return base_weights.copy()

    adjusted = {}
    for factor in factor_names:
        accuracy = correct_by_factor[factor] / total_correct
        adjusted[factor] = base_weights[factor] * (0.5 + 0.5 * accuracy)
    total = sum(adjusted.values())
    for factor in adjusted:
        adjusted[factor] = round(adjusted[factor] / total, 3)
    return adjusted


def _check_asia_weight_drift(calibrated: dict) -> str:
    drift_lq = abs(calibrated.get("liquidity", ASIA_WEIGHT_LIQUIDITY) - ASIA_WEIGHT_LIQUIDITY)
    drift_sec = abs(calibrated.get("sector", ASIA_WEIGHT_SECTOR) - ASIA_WEIGHT_SECTOR)
    drift_bo = abs(calibrated.get("breakout", ASIA_WEIGHT_BREAKOUT) - ASIA_WEIGHT_BREAKOUT)
    parts = []
    if drift_lq > 0.02:
        parts.append(f"Liquidity: {ASIA_WEIGHT_LIQUIDITY:.0%}→{calibrated['liquidity']:.0%}")
    if drift_sec > 0.02:
        parts.append(f"Sector: {ASIA_WEIGHT_SECTOR:.0%}→{calibrated['sector']:.0%}")
    if drift_bo > 0.02:
        parts.append(f"Breakout: {ASIA_WEIGHT_BREAKOUT:.0%}→{calibrated['breakout']:.0%}")
    return " | ".join(parts) if parts else ""


def _liquidity_wave_score() -> float:
    try:
        health = get_market_liquidity_health()
        if health.get("status") == "INSUFFICIENT_DATA":
            return 0.5
        phase = health.get("liquidity_phase", "NEUTRAL")
        vol_trend = health.get("volume_trend_5d", 0)
        concentration = health.get("top10_concentration_pct", 50)
        if phase == "EXPANDING":
            base = 0.7 + min(0.3, max(0, vol_trend * 2))
        elif phase == "CONTRACTING":
            base = 0.3 + max(0, 0.2 + vol_trend * 2)
        else:
            base = 0.5 + min(0.2, max(-0.2, vol_trend))
        if concentration > 70:
            base -= 0.15
        return max(0.0, min(1.0, base))
    except Exception as e:
        logger.warning(f"Liquidity wave score failed: {e}")
        return 0.5


def _sector_rotation_score() -> float:
    try:
        beta = get_rotation_beta()
        regime = beta.get("rotation_regime", "NEUTRAL")
        score = beta.get("rotation_score", 0)
        alignment = beta.get("flow_alignment_pct", 50)
        regime_map = {
            "HEALTHY_ROTATION": 0.75,
            "BROAD_ROTATION": 0.65,
            "DIVERGENT": 0.45,
            "NARROW_LEADERSHIP": 0.30,
        }
        base = regime_map.get(regime, 0.5)
        adj = (score * 0.5) + ((alignment / 100) * 0.3)
        return max(0.0, min(1.0, base + adj))
    except Exception as e:
        logger.warning(f"Sector rotation score failed: {e}")
        return 0.5


def _breakout_continuation_score() -> float:
    try:
        context = get_breakout_market_context()
        if context.get("status") == "NO_DATA":
            return 0.5
        ctx = context.get("breakout_context", "LOW_BREAKOUT_ACTIVITY")
        density = context.get("breakout_density", 0)
        avg_score = context.get("avg_score", 50)
        ctx_map = {
            "HIGH_BREAKOUT_ACTIVITY": 0.75,
            "MODERATE_BREAKOUT_ACTIVITY": 0.55,
            "LOW_BREAKOUT_ACTIVITY": 0.30,
        }
        base = ctx_map.get(ctx, 0.5)
        adj = min(0.2, density * 0.5) + min(0.15, (avg_score - 50) / 200)
        return max(0.0, min(1.0, base + adj))
    except Exception as e:
        logger.warning(f"Breakout continuation score failed: {e}")
        return 0.5


def _liquidity_wave_decayed_score() -> float:
    try:
        health = get_decayed_liquidity_health()
        if health.get("status") == "INSUFFICIENT_DATA":
            return 0.5
        phase = health.get("liquidity_phase_decayed", "NEUTRAL")
        vol_trend = health.get("volume_trend_decayed", 0)
        persistence = health.get("vol_persistence", 0.5)
        instability = health.get("vol_instability", 0.5)
        if phase == "EXPANDING":
            base = 0.7 + min(0.3, max(0, vol_trend * 0.1))
        elif phase == "CONTRACTING":
            base = 0.3 + max(0, 0.2 + vol_trend * 0.1)
        else:
            base = 0.5 + min(0.2, max(-0.2, vol_trend * 0.05))
        # Adjust for signal quality
        if persistence >= 0.7 and instability < 0.8:
            base += 0.1
        elif instability > 1.5:
            base -= 0.15
        return max(0.0, min(1.0, base))
    except Exception as e:
        logger.warning(f"Decayed liquidity score failed: {e}")
        return 0.5


def _sector_rotation_decayed_score() -> float:
    try:
        beta = get_decayed_rotation_beta()
        regime = beta.get("rotation_regime_decayed", "UNKNOWN")
        score = beta.get("rotation_score_decayed", 0)
        regime_map = {
            "HEALTHY_ROTATION": 0.75,
            "BROAD_ROTATION": 0.65,
            "DIVERGENT": 0.45,
            "NARROW_LEADERSHIP": 0.30,
        }
        base = regime_map.get(regime, 0.5)
        adj = score * 0.5
        return max(0.0, min(1.0, base + adj))
    except Exception as e:
        logger.warning(f"Decayed sector score failed: {e}")
        return 0.5


def _foreign_flow_decayed_score() -> float:
    try:
        summary = get_decayed_foreign_summary()
        total = summary.get("total_net_decayed_bn_vnd", 0)
        pressure = summary.get("market_pressure_decayed", "NEUTRAL")
        magnitude = min(1.0, abs(total) / 1000)
        if pressure == "ACCUMULATING":
            return 0.5 + magnitude * 0.4
        elif pressure == "DISTRIBUTING":
            return 0.5 - magnitude * 0.4
        return 0.5
    except Exception as e:
        logger.warning(f"Foreign flow decayed score failed: {e}")
        return 0.5


def compute_v2_decayed(override_input: DecisionTensorInput | None = None) -> dict:
    """
    Decision Tensor v2 with decay-aware flow signals.
    Uses EWMA-weighted liquidity, sector, and foreign scores instead of raw SMA.
    """
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
    s_score = _signal_score(inp.model_a_signal, inp.model_b_signal, inp.model_a_score, inp.model_b_score, inp.regime)
    d_score = _dampener_score(inp.dampener_result["net_dampener"])
    active_model = "MODEL_A" if inp.regime in ("TRENDING",) else "MODEL_B"
    m_score = _memory_score(inp.memory_perf_a, inp.memory_perf_b, inp.regime, active_model)
    l_score = _liquidity_wave_decayed_score()
    sec_score = _sector_rotation_decayed_score()
    f_score = _foreign_flow_decayed_score()
    b_score = _breakout_continuation_score()

    raw_score = (
        V2_WEIGHT_REGIME * r_score
        + V2_WEIGHT_HEAT * h_score
        + V2_WEIGHT_SIGNAL * s_score
        + V2_WEIGHT_DAMPENER * d_score
        + V2_WEIGHT_MEMORY * m_score
        + ASIA_WEIGHT_LIQUIDITY * l_score
        + ASIA_WEIGHT_SECTOR * sec_score
        + ASIA_WEIGHT_BREAKOUT * b_score
    )
    action_score = round(max(0.0, min(1.0, raw_score)) * 100)
    risk_state = _resolve_risk_state(inp.portfolio_heat, inp.throttle_state, inp.dampener_result["net_dampener"])
    action = _resolve_action(
        raw_score, risk_state, inp.regime, inp.portfolio_heat, inp.dampener_result["net_dampener"], inp.net_exposure_pct
    )
    constraint, size_mult = _resolve_constraint(
        inp.portfolio_heat,
        inp.dampener_result["net_dampener"],
        inp.regime,
        inp.throttle_state,
        inp.dampener_result["streak_losses"],
    )
    asia_reason = f"| D-LQ:{l_score:.2f} D-SEC:{sec_score:.2f} FX:{f_score:.2f} BO:{b_score:.2f}"
    reason = (
        _reason_compressed(
            action,
            risk_state,
            inp.regime,
            inp.portfolio_heat,
            inp.dampener_result["net_dampener"],
            action_score,
            inp.dampener_result["streak_losses"],
            inp.throttle_state,
        )
        + asia_reason
    )

    alternatives = _generate_alternatives(inp, action, raw_score)
    rationale_tree = _build_rationale_tree(
        inp,
        r_score,
        h_score,
        s_score,
        d_score,
        m_score,
        action_score,
        risk_state,
        action,
        constraint,
        size_mult,
        l_score,
        sec_score,
        b_score,
    )

    history = _load_decision_history()
    calibrated = _calibrate_weights(history)

    decision_id = str(uuid4())[:8]
    _check_asia_weight_drift(calibrated)

    decision = CognitiveDecision(
        action=action,
        confidence=action_score,
        risk_state=risk_state,
        constraint=constraint,
        suggested_size_mult=size_mult,
        reason=reason,
        alternatives=alternatives,
        rationale_tree=rationale_tree,
        decision_id=decision_id,
        calibrated_weights=calibrated,
    )

    record = asdict(decision)
    record["timestamp"] = datetime.now().isoformat()
    record["outcome"] = None
    record["decayed_flow"] = True
    record["engine_scores"] = {
        "regime": round(r_score, 3),
        "heat": round(h_score, 3),
        "signal": round(s_score, 3),
        "dampener": round(d_score, 3),
        "memory": round(m_score, 3),
        "liquidity": round(l_score, 3),
        "sector": round(sec_score, 3),
        "breakout": round(b_score, 3),
    }
    history.append(record)
    _save_decision_history(history)

    try:
        record_decision(record)
    except Exception:
        pass

    return asdict(decision)


def compute_v2(override_input: DecisionTensorInput | None = None) -> dict:
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
    s_score = _signal_score(inp.model_a_signal, inp.model_b_signal, inp.model_a_score, inp.model_b_score, inp.regime)
    d_score = _dampener_score(inp.dampener_result["net_dampener"])
    active_model = "MODEL_A" if inp.regime in ("TRENDING",) else "MODEL_B"
    m_score = _memory_score(inp.memory_perf_a, inp.memory_perf_b, inp.regime, active_model)
    l_score = _liquidity_wave_score()
    sec_score = _sector_rotation_score()
    b_score = _breakout_continuation_score()

    raw_score = (
        V2_WEIGHT_REGIME * r_score
        + V2_WEIGHT_HEAT * h_score
        + V2_WEIGHT_SIGNAL * s_score
        + V2_WEIGHT_DAMPENER * d_score
        + V2_WEIGHT_MEMORY * m_score
        + ASIA_WEIGHT_LIQUIDITY * l_score
        + ASIA_WEIGHT_SECTOR * sec_score
        + ASIA_WEIGHT_BREAKOUT * b_score
    )
    action_score = round(max(0.0, min(1.0, raw_score)) * 100)
    risk_state = _resolve_risk_state(inp.portfolio_heat, inp.throttle_state, inp.dampener_result["net_dampener"])
    action = _resolve_action(
        raw_score, risk_state, inp.regime, inp.portfolio_heat, inp.dampener_result["net_dampener"], inp.net_exposure_pct
    )
    constraint, size_mult = _resolve_constraint(
        inp.portfolio_heat,
        inp.dampener_result["net_dampener"],
        inp.regime,
        inp.throttle_state,
        inp.dampener_result["streak_losses"],
    )
    asia_reason = f"| LQ:{l_score:.2f} SEC:{sec_score:.2f} BO:{b_score:.2f}"
    reason = (
        _reason_compressed(
            action,
            risk_state,
            inp.regime,
            inp.portfolio_heat,
            inp.dampener_result["net_dampener"],
            action_score,
            inp.dampener_result["streak_losses"],
            inp.throttle_state,
        )
        + asia_reason
    )

    alternatives = _generate_alternatives(inp, action, raw_score)
    rationale_tree = _build_rationale_tree(
        inp,
        r_score,
        h_score,
        s_score,
        d_score,
        m_score,
        action_score,
        risk_state,
        action,
        constraint,
        size_mult,
        l_score,
        sec_score,
        b_score,
    )

    history = _load_decision_history()
    calibrated = _calibrate_weights(history)

    decision_id = str(uuid4())[:8]
    _check_asia_weight_drift(calibrated)

    decision = CognitiveDecision(
        action=action,
        confidence=action_score,
        risk_state=risk_state,
        constraint=constraint,
        suggested_size_mult=size_mult,
        reason=reason,
        alternatives=alternatives,
        rationale_tree=rationale_tree,
        decision_id=decision_id,
        calibrated_weights=calibrated,
    )

    record = asdict(decision)
    record["timestamp"] = datetime.now().isoformat()
    record["outcome"] = None
    record["engine_scores"] = {
        "regime": round(r_score, 3),
        "heat": round(h_score, 3),
        "signal": round(s_score, 3),
        "dampener": round(d_score, 3),
        "memory": round(m_score, 3),
        "liquidity": round(l_score, 3),
        "sector": round(sec_score, 3),
        "breakout": round(b_score, 3),
    }
    history.append(record)
    _save_decision_history(history)

    try:
        record_decision(record)
    except Exception:
        pass

    # ── Return ──
    # Inject DDI + params_hash from final_decision.json so the frontend
    # DecisionStripV2 can render them without a separate API call.
    result = asdict(decision)
    try:
        _fp = PROJECT_ROOT / "backend" / "data" / "output" / "final_decision.json"
        if _fp.exists():
            _fd = json.loads(_fp.read_text(encoding="utf-8"))
            _ddi = _fd.get("delta_divergence")
            if _ddi:
                result["ddi_data"] = _ddi
            _ph = _fd.get("params_hash")
            if _ph:
                result["params_hash"] = _ph
    except Exception:
        pass
    return result


def log_override(decision_id: str, override_action: str, override_reason: str) -> dict:
    history = _load_decision_history()
    for entry in history:
        if entry.get("decision_id") == decision_id:
            _prev = entry.get("action", "N/A")
            entry["override_state"] = "OVERRIDDEN"
            entry["override_action"] = override_action
            entry["override_reason"] = override_reason
            _save_decision_history(history)
            # Audit: human override
            try:
                from src.portfolio.decision_audit import log_transition

                log_transition(
                    prev_state=_prev,
                    new_state=override_action,
                    decision_id=decision_id,
                    params_hash=entry.get("params_hash", entry.get("engine_scores", {}).get("regime", "unresolved")),
                    confidence=entry.get("confidence", 0) / 100.0,
                    delta_sa=entry.get("ddi_data", {}).get("delta_sa", 0.0),
                    regime=entry.get("engine_scores", {}).get("regime", "N/A"),
                    trigger="human:admin",
                )
            except Exception:
                pass
            return {"status": "logged", "decision_id": decision_id}
    return {"status": "not_found", "decision_id": decision_id}


def log_confirm(decision_id: str) -> dict:
    history = _load_decision_history()
    for entry in history:
        if entry.get("decision_id") == decision_id:
            entry["override_state"] = "CONFIRMED"
            _save_decision_history(history)
            # Audit: human confirm (no state change, log as confirm event)
            try:
                from src.portfolio.decision_audit import log_transition

                log_transition(
                    prev_state=entry.get("action", "N/A"),
                    new_state=entry.get("action", "N/A"),
                    decision_id=decision_id,
                    params_hash=entry.get("params_hash", entry.get("engine_scores", {}).get("regime", "unresolved")),
                    confidence=entry.get("confidence", 0) / 100.0,
                    delta_sa=entry.get("ddi_data", {}).get("delta_sa", 0.0),
                    regime=entry.get("engine_scores", {}).get("regime", "N/A"),
                    trigger="human:admin",
                )
            except Exception:
                pass
            return {"status": "confirmed", "decision_id": decision_id}
    return {"status": "not_found", "decision_id": decision_id}


def get_decision_history(limit: int = 20) -> list:
    history = _load_decision_history()
    return history[-limit:]
