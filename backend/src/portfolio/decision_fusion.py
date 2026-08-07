"""
Decision Fusion Engine v1.0
Deterministic conflict resolver between Model A and Model B signals.
No AI, no probability — pure regime-based policy matrix.
"""

import sys
from pathlib import Path


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

# Policy matrix: which engine gets priority per regime
# (target, muted) — the muted engine's buy signals are blocked
REGIME_POLICY = {
    "CRISIS": {"target": None, "muted": ["MODEL_A", "MODEL_B"], "action": "FORCE_CASH"},
    "RECOVERY": {"target": "BOTH", "muted": [], "action": "CONSENSUS_ONLY"},
    "RANGING": {"target": "MODEL_B", "muted": ["MODEL_A"], "action": "MEAN_REVERSION"},
    "TRENDING": {"target": "MODEL_A", "muted": ["MODEL_B"], "action": "TREND_FOLLOWING"},
}


def arbitrate(model_a_verdict: str, model_b_verdict: str, active_regime: str) -> dict:
    policy = REGIME_POLICY.get(active_regime)
    if not policy:
        return {"action": "STAND_DOWN", "reason": "UNKNOWN_REGIME", "target_engine": None, "confidence_multiplier": 0.0}

    if policy["action"] == "FORCE_CASH":
        return {
            "action": "FORCE_CASH",
            "reason": "REGIME_CRISIS_LOCKDOWN",
            "target_engine": None,
            "confidence_multiplier": 0.0,
        }

    a_buy = model_a_verdict == "TRIGGER_BUY"
    b_buy = model_b_verdict == "TRIGGER_BUY"

    if policy["action"] == "CONSENSUS_ONLY":
        if a_buy and b_buy:
            return {
                "action": "EXECUTE",
                "reason": "BOTH_CONFIRM_RECOVERY",
                "target_engine": "BOTH",
                "confidence_multiplier": 1.2,
            }
        return {"action": "STAND_DOWN", "reason": "RECOVERY_NEEDS_BOTH", "target_engine": None, "confidence_multiplier": 0.0}

    if policy["action"] == "TREND_FOLLOWING":
        if a_buy and not b_buy:
            return {
                "action": "EXECUTE",
                "reason": "TREND_CONFIRMED_MODEL_A",
                "target_engine": "MODEL_A",
                "confidence_multiplier": 1.0,
            }
        return {"action": "MUTED", "reason": "MODEL_B_MUTED_IN_TREND", "target_engine": None, "confidence_multiplier": 0.0}

    if policy["action"] == "MEAN_REVERSION":
        if b_buy and not a_buy:
            return {
                "action": "EXECUTE",
                "reason": "PULLBACK_CONFIRMED_MODEL_B",
                "target_engine": "MODEL_B",
                "confidence_multiplier": 1.0,
            }
        return {"action": "MUTED", "reason": "MODEL_A_MUTED_IN_RANGE", "target_engine": None, "confidence_multiplier": 0.0}

    return {"action": "STAND_DOWN", "reason": "NO_POLICY_MATCH", "target_engine": None, "confidence_multiplier": 0.0}
