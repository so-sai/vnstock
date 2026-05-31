"""Shadow CAO — Safe Pre-Learning Simulation Layer.

Cognitive stress-testing system that runs alongside production.
Does NOT touch production tables — operates in shadow_cao.db namespace.

Principles:
    - Data is fixed (market truth is frozen)
    - Only attribution is perturbed
    - NO synthetic outcomes
    - NO weight updates to production
    - Only ablation + replay

Hardening (see hardening.py):
    - Crash-proof: exceptions NEVER propagate to production
    - Async event bus: hooks run non-blocking, never block production
    - Timestamp validator: temporal alignment enforced
    - Replay engine: historical validation available

Usage:
    from src.shadow_cao import run_daily_batch, run_gate_recheck, run_replay
    summary = run_daily_batch()
    verdict = run_gate_recheck()
    replay_results = run_replay(limit=200)
"""
from src.shadow_cao.scheduler import run_daily_batch, run_gate_recheck
from src.shadow_cao.hooks import on_decision_recorded, on_outcome_evaluated, on_attribution_complete
from src.shadow_cao.hooks import run_replay
from src.shadow_cao.belief import get_belief_state
from src.shadow_cao.storage import get_shadow_stats
from src.shadow_cao.hardening import TimestampValidator, ReplayEngine, safe_hook, ShadowEventBus

__all__ = [
    "run_daily_batch",
    "run_gate_recheck",
    "run_replay",
    "on_decision_recorded",
    "on_outcome_evaluated",
    "on_attribution_complete",
    "get_belief_state",
    "get_shadow_stats",
    "TimestampValidator",
    "ReplayEngine",
    "safe_hook",
    "ShadowEventBus",
]
