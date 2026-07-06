
import json
from datetime import datetime
from pathlib import Path


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    import sys
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
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config
from src.database.timeline_manager import calculate_breadth_velocity
from src.engine.meanrev_engine import run_meanrev_scan
from src.engine.recovery_engine import evaluate_recovery_status
from src.engine.regime_engine import detect_regime


def modulate_conviction(
    base_conviction: float,
    driver_state: dict | None = None,
    drift_assessment: dict | None = None,
    explain_validation: dict | None = None,
) -> tuple[float, list[str]]:
    """Adjust conviction using cognitive signals.

    Multiplicative modifiers — each under 1.0 reduces conviction, over 1.0 boosts.
    Returns (adjusted_conviction, list_of_reasons).
    """
    if not any([driver_state, drift_assessment, explain_validation]):
        return base_conviction, ["no cognitive signals — base only"]

    mult = 1.0
    reasons = []

    # 1. Driver confidence — low confidence = weak dominance
    if driver_state:
        dc = driver_state.get("confidence", 0.5)
        if dc < 0.3:
            mult *= 0.6
            reasons.append("driver confidence low (%.2f)" % dc)
        elif dc < 0.5:
            mult *= 0.85
            reasons.append("driver confidence moderate (%.2f)" % dc)
        elif dc > 0.7:
            mult *= 1.1
            reasons.append("driver confidence high (%.2f)" % dc)

    # 2. Drift — high drift = system unreliable
    if drift_assessment:
        ds = drift_assessment.get("drift_status", "NONE")
        if ds == "HIGH" or ds == "CRITICAL":
            mult *= 0.3
            reasons.append("drift %s" % ds)
        elif ds == "MEDIUM":
            mult *= 0.6
            reasons.append("drift medium")

    # 3. ETS — narrative reliability
    if explain_validation:
        ets = explain_validation.get("ets_score", 0.5)
        if ets < 0.3:
            mult *= 0.5
            reasons.append("ETS low (%.2f)" % ets)
        elif ets < 0.5:
            mult *= 0.8
            reasons.append("ETS moderate (%.2f)" % ets)
        elif ets > 0.7:
            mult *= 1.1
            reasons.append("ETS high (%.2f)" % ets)

    # 4. Flow rotation — risk-off pattern
    if drift_assessment:
        rotation = drift_assessment.get("flow_rotation")
        rotation_text = drift_assessment.get("narrative_truth_gap") or ""
        if rotation and "risk_off" in rotation:
            mult *= 0.4
            reasons.append("risk-off flow rotation")
        elif rotation_text and "flow" in rotation_text.lower():
            mult *= 0.7
            reasons.append("flow rotation detected")

    mult = max(0.05, mult)
    return max(0.0, min(1.0, base_conviction * mult)), reasons


def merge_decisions(
    model_a_verdict=None,
    target_date=None,
    driver_state: dict | None = None,
    drift_assessment: dict | None = None,
    explain_validation: dict | None = None,
):
    """
    Decision Engine: The Boardroom (v1.0 Institutional).
    Merges Regime, Model A (Momentum), and Model B (Mean Rev).
    Includes [LOCK 4] Macro Confidence Dampening.
    Supports Point-in-time accuracy via target_date.
    """
    print("\n" + "="*50)
    print(f"DECISION ENGINE: {'HISTORICAL REPLAY' if target_date else 'BOARDROOM CONSENSUS'}")
    print("="*50)

    # 1. Get Regime
    regime = detect_regime(target_date=target_date)
    rs = regime['regime_score']
    status = regime['status']

    # 1.1 Calculate Breadth Velocity (5D)
    breadth_velocity = calculate_breadth_velocity(regime['details']['breadth_pct'], days=5)
    regime['breadth_velocity'] = breadth_velocity

    # 1.2 Evaluate Recovery Status
    recovery = evaluate_recovery_status(regime, breadth_velocity, target_date=target_date)
    regime['recovery_active'] = recovery['is_recovery']

    # 2. Get Model B Picks
    model_b_picks = run_meanrev_scan(regime, target_date=target_date)

    # 3. Model A Data (From Sentinel Alert Verdict or provided)
    if model_a_verdict is None:
        sentinel_path = Path(src.config.DATA_DIR) / "output" / "sentinel_verdict.json"
        if sentinel_path.exists():
            with open(sentinel_path, "r", encoding="utf-8") as f:
                model_a_verdict = json.load(f)
        else:
            model_a_verdict = {"final_status": "UNKNOWN", "layer1_mom_expansion": {"status": "FAIL"}}

    # 4. [LOCK 4] Macro Confidence Dampening
    # Check China Risk (Simulated for now, can fetch from a global state file)
    china_risk_flag = False
    china_nexus_path = Path(src.config.DATA_DIR) / "output" / "china_sensitivity.json"
    if china_nexus_path.exists():
        # In a real scenario, we'd check if SSEC is crashing or USDCNH is spiking
        china_risk_flag = False # Logic placeholder

    # Calculate Confidence
    # confidence = RS * SignalStrength (0.5-1.0)
    base_confidence = rs * 1.0
    if china_risk_flag:
        print("[LOCK 4] MACRO DAMPENING: China Risk Flag High. Reducing confidence.")
        base_confidence *= 0.7

    # [COGNITIVE MODULATION] — inject driver/drift/ETS into conviction
    modulated_confidence, mod_reasons = modulate_conviction(
        base_confidence,
        driver_state=driver_state,
        drift_assessment=drift_assessment,
        explain_validation=explain_validation,
    )
    if mod_reasons:
        print("[COGNITIVE MODULATION] " + " | ".join(mod_reasons))
        base_confidence = modulated_confidence

    # 5. Dominance Logic
    active_model = "NONE"
    consensus = "CASH / STANDBY"

    if status == "TRENDING":
        active_model = "A (MOMENTUM)"
        if model_a_verdict.get("final_status", "").startswith("GREEN"):
            consensus = "CONVICTION BUY"
        else:
            consensus = "HOLD / CAUTIOUS"
    elif status == "RANGING":
        active_model = "B (MEAN REVERSION)"
        if model_b_picks:
            consensus = "CAUTIOUS BUY (PULLBACK)"
        else:
            consensus = "WAIT FOR NICHES"
    else: # CRISIS
        if recovery['is_recovery']:
            active_model = "B (PILOT RECOVERY)"
            consensus = "PILOT BUY (OVERSOLD REBOUND)"
            base_confidence = 0.4 # Moderate confidence for recovery start
        elif recovery['status'] == "PILOT_ABORT":
            active_model = "NONE"
            consensus = "PILOT ABORT (EXIT IMMEDIATELY)"
            base_confidence = 0.0
        else:
            active_model = "NONE"
            consensus = "CASH (PROTECT CAPITAL)"
            base_confidence = 0.0 # Force zero confidence in crisis

    # 6. Final Verdict
    board_decision = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if not target_date else target_date,
        "date": regime['date'],
        "regime_score": rs,
        "market_status": status,
        "active_model": active_model,
        "consensus": consensus,
        "confidence": round(base_confidence, 2),
        "breadth_velocity": round(regime.get('breadth_velocity', 0.0), 2),
        "details": regime['details'],
        "model_a": {
            "status": model_a_verdict.get("final_status", "UNKNOWN"),
            "mom_expansion": model_a_verdict.get("layer1_mom_expansion", {}).get("value", 0)
        },
        "model_b": {
            "picks_count": len(model_b_picks),
            "top_picks": model_b_picks[:5],
            "context": regime.get('details', {}).get('vnindex_vs_ma200', 'UNKNOWN'),
            "breadth_pct": regime.get('details', {}).get('breadth_pct', 0),
            "breadth_std": regime.get('details', {}).get('breadth_std_10d', 0),
            "breadth_velocity": round(regime.get('breadth_velocity', 0.0), 2),
        },
        "recovery": recovery,
        "cognitive_modulation": {
            "mod_reasons": mod_reasons,
            "driver_confidence": driver_state.get("confidence") if driver_state else None,
            "drift_status": drift_assessment.get("drift_status") if drift_assessment else None,
            "ets_score": explain_validation.get("ets_score") if explain_validation else None,
        },
    }

    # Output
    output_path = Path(src.config.DATA_DIR) / "output" / "decision_board.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(board_decision, f, indent=4, ensure_ascii=False)

    print("\n--- FINAL CONSENSUS ---")
    print(f"REGIME:     {status} ({rs:.2f})")
    print(f"DOMINANCE:  {active_model}")
    print(f"VERDICT:    {consensus}")
    print(f"CONFIDENCE: {board_decision['confidence']}")
    print("-" * 30)

    return board_decision

if __name__ == "__main__":
    merge_decisions()
