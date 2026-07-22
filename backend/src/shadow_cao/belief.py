"""Shadow CAO — Belief State Store.

Accumulates ablation statistics over time.
NO weight updates — only drift tracking and readiness monitoring.
"""
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


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
    return root_path

PROJECT_ROOT = _hydrate_path()
from src.shadow_cao.models import BeliefState, EngineAblationProfile
from src.shadow_cao.storage import (
    get_belief_value,
    initialize_shadow_database,
    save_belief_value,
    save_engine_profile,
)

CANONICAL_ENGINES = ["regime", "liquidity", "sector", "breakout", "heat", "signal", "memory", "dampener"]


def update_engine_profiles() -> dict[str, EngineAblationProfile]:
    """Recalculate engine ablation profiles from shadow_ablations table.

    NO weight updates — only accumulates statistics for readiness monitoring.
    """
    from src.shadow_cao.storage import get_shadow_connection
    try:
        initialize_shadow_database()
    except Exception:
        pass
    stats = defaultdict(lambda: {
        "total": 0, "flips": 0, "conf_deltas": [], "action_changes": 0,
    })
    with get_shadow_connection() as conn:
        rows = conn.execute(
            "SELECT engine_removed, action_changed, confidence_delta, decision_flip "
            "FROM shadow_ablations"
        ).fetchall()
    for r in rows:
        eng = r["engine_removed"]
        stats[eng]["total"] += 1
        stats[eng]["conf_deltas"].append(r["confidence_delta"])
        if r["action_changed"]:
            stats[eng]["action_changes"] += 1
        if r["decision_flip"]:
            stats[eng]["flips"] += 1
    profiles = {}
    for eng in CANONICAL_ENGINES:
        s = stats.get(eng, {"total": 0, "flips": 0, "conf_deltas": [], "action_changes": 0})
        total = s["total"]
        if total == 0:
            profiles[eng] = EngineAblationProfile(
                engine=eng, total_decisions=0, flip_count=0,
                avg_confidence_delta=0.0, action_change_ratio=0.0, stability_score=0.0,
            )
            continue
        avg_delta = sum(s["conf_deltas"]) / total if s["conf_deltas"] else 0.0
        action_ratio = s["action_changes"] / total
        stability = 1.0 - (s["flips"] / total) if total > 0 else 0.0
        profiles[eng] = EngineAblationProfile(
            engine=eng,
            total_decisions=total,
            flip_count=s["flips"],
            avg_confidence_delta=round(avg_delta, 4),
            action_change_ratio=round(action_ratio, 4),
            stability_score=round(stability, 4),
        )
        save_engine_profile(profiles[eng])
    return profiles


def compute_stability_index() -> float:
    """Compute overall system stability from engine profiles.

    Higher = more stable decision boundary under ablation.
    Range: [0, 1].
    """
    profiles = update_engine_profiles()
    if not profiles:
        return 0.0
    scores = [p.stability_score for p in profiles.values() if p.total_decisions > 0]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def get_belief_state() -> BeliefState:
    """Get current accumulated belief state (READ-ONLY, no weight effect)."""
    profiles = update_engine_profiles()
    stability = compute_stability_index()
    entropy_raw = get_belief_value("regime_entropy_trace")
    from src.shadow_cao.storage import get_shadow_stats
    stats = get_shadow_stats()
    return BeliefState(
        engine_profiles=profiles,
        regime_entropy_trace=json.loads(entropy_raw) if entropy_raw else [],
        stability_index=stability,
        total_decisions_logged=stats.get("decision_logs", 0),
        total_outcomes_logged=stats.get("outcomes", 0),
    )


def persist_stability_trace(stability: float, entropy: float = None):
    """Persist stability trace for long-term monitoring."""
    save_belief_value("current_stability", str(stability))
    if entropy is not None:
        save_belief_value("current_entropy", str(entropy))
    existing_raw = get_belief_value("stability_trace")
    trace = json.loads(existing_raw) if existing_raw else []
    trace.append({"stability": stability, "entropy": entropy})
    if len(trace) > 1000:
        trace = trace[-1000:]
    save_belief_value("stability_trace", json.dumps(trace, ensure_ascii=False))
