"""Shadow CAO — Hardening Layer.

Production-safety guarantees:
1. Crash-proof hook wrapper — shadow exceptions NEVER propagate to production
2. Async event bus isolation — hooks run non-blocking, never block production
3. Timestamp integrity validator — temporal alignment between decision/outcome/ablation
4. Replay engine — validate correctness on historical snapshots (benchmark-grade)
"""

import functools
import json
import logging
import queue
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


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
    return root_path


PROJECT_ROOT = _hydrate_path()


# ====================================================================
# 1. CRASH-PROOF HOOK WRAPPER
# ====================================================================


def safe_hook(hook_name: str):
    """Decorator: shadow exceptions NEVER propagate to production.

    Every shadow CAO hook MUST use this decorator.
    If the hook crashes (any exception), it logs and returns None.
    Production pipeline is NEVER affected.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error("[SHADOW_CAO] HOOK CRASH [%s]: %s", hook_name, e, exc_info=True)
                return None

        return wrapper

    return decorator


# ====================================================================
# 2. ASYNC EVENT BUS
# ====================================================================


class ShadowEventBus:
    """Async event bus for Shadow CAO hooks.

    Production pipeline posts events and returns immediately.
    A background thread processes the queue asynchronously.
    Never blocks the production caller.

    Usage:
        bus = ShadowEventBus()
        bus.start()
        bus.post("decision_recorded", {"decision_id": "abc123"})
        bus.stop()
    """

    def __init__(self, max_workers: int = 1, queue_timeout: float = 0.01):
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._thread: threading.Thread | None = None
        self._handlers: dict[str, list[Callable]] = {}
        self._max_workers = max_workers
        self._queue_timeout = queue_timeout
        self._processed_count = 0
        self._error_count = 0

    def register(self, event_type: str, handler: Callable):
        """Register a handler for an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def post(self, event_type: str, data: dict | None = None):
        """Post an event to the async queue. NEVER blocks.

        Safe to call from production pipeline — returns immediately
        even if the queue is full (drops events under backpressure).
        """
        if not self._running:
            return
        try:
            self._queue.put_nowait((event_type, data or {}))
        except queue.Full:
            logger.warning("[SHADOW_EVENT_BUS] Queue full, dropping %s event", event_type)

    def start(self):
        """Start the background event processing thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("[SHADOW_EVENT_BUS] Started (daemon thread)")

    def stop(self, wait: bool = True):
        """Stop the event bus."""
        self._running = False
        if wait and self._thread:
            self._thread.join(timeout=5.0)

    @property
    def stats(self) -> dict:
        return {
            "queue_size": self._queue.qsize(),
            "processed": self._processed_count,
            "errors": self._error_count,
            "running": self._running,
        }

    def _run_loop(self):
        """Background event processing loop."""
        while self._running:
            try:
                event_type, data = self._queue.get(timeout=self._queue_timeout)
            except queue.Empty:
                continue
            handlers = self._handlers.get(event_type, [])
            for handler in handlers:
                try:
                    handler(**data)
                    self._processed_count += 1
                except Exception as e:
                    self._error_count += 1
                    logger.error(
                        "[SHADOW_EVENT_BUS] Handler %s failed for %s: %s",
                        handler.__name__,
                        event_type,
                        e,
                        exc_info=True,
                    )


# Singleton event bus (shared across hooks)
_event_bus = ShadowEventBus()


def get_event_bus() -> ShadowEventBus:
    """Get the singleton Shadow CAO event bus."""
    return _event_bus


def start_event_bus():
    """Start the singleton event bus (call once at system init)."""
    bus = get_event_bus()
    if not bus._running:
        bus.start()
    return bus


# ====================================================================
# 3. TIMESTAMP INTEGRITY VALIDATOR
# ====================================================================


class TimestampValidator:
    """Validates temporal alignment between decision, outcome, and ablation.

    Rules:
        - decision_timestamp <= outcome_timestamp (decision happens before outcome)
        - outcome_timestamp - decision_timestamp >= horizon_days (minimum horizon)
        - ablation runs on same decision_t as baseline (no temporal drift)
    """

    def __init__(self, max_drift_hours: float = 24.0):
        self.max_drift_hours = max_drift_hours
        self._violations: list[dict] = []

    def validate_decision_outcome(
        self,
        decision_id: str,
        decision_timestamp: str,
        outcome_timestamp: str,
        horizon_days: int,
    ) -> bool:
        """Validate temporal alignment between a decision and its outcome.

        Returns True if valid, False if violation detected.
        """
        try:
            dt = self._parse(decision_timestamp)
            ot = self._parse(outcome_timestamp)
        except (ValueError, TypeError) as e:
            self._log_violation(decision_id, "parse_error", str(e))
            return False
        if ot < dt:
            self._log_violation(
                decision_id,
                "outcome_before_decision",
                f"outcome({ot}) < decision({dt})",
            )
            return False
        min_horizon = timedelta(days=horizon_days)
        actual_gap = ot - dt
        if actual_gap < min_horizon:
            self._log_violation(
                decision_id,
                "horizon_too_short",
                f"gap={actual_gap.days}d < horizon={horizon_days}d",
            )
            return False
        return True

    def validate_ablation_alignment(
        self,
        decision_id: str,
        ablation_timestamp: str,
        decision_timestamp: str,
    ) -> bool:
        """Validate that ablation runs on same temporal baseline as decision.

        Ablation must happen close to decision time (same batch).
        """
        try:
            dt = self._parse(decision_timestamp)
            at = self._parse(ablation_timestamp)
        except ValueError, TypeError:
            return False
        drift = abs((at - dt).total_seconds() / 3600)
        if drift > self.max_drift_hours:
            self._log_violation(
                decision_id,
                "ablation_drift",
                f"ablation lags decision by {drift:.1f}h (max={self.max_drift_hours}h)",
            )
            return False
        return True

    @property
    def violations(self) -> list[dict]:
        return list(self._violations)

    @property
    def is_clean(self) -> bool:
        return len(self._violations) == 0

    def reset(self):
        self._violations.clear()

    def _parse(self, ts: str) -> datetime:
        if "T" in ts:
            return datetime.fromisoformat(ts)
        return datetime.strptime(ts, "%Y-%m-%d")

    def _log_violation(self, decision_id: str, rule: str, detail: str):
        entry = {
            "decision_id": decision_id,
            "rule": rule,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        }
        self._violations.append(entry)
        logger.warning("[SHADOW_TIMESTAMP] Violation: %s | %s | %s", decision_id, rule, detail)


# ====================================================================
# 4. REPLAY ENGINE
# ====================================================================


class ReplayEngine:
    """Deterministic replay of historical snapshots through Shadow CAO.

    Validates shadow correctness against historical data.
    Records replay results in shadow_cao.db (never touches production).
    """

    def __init__(self):
        self._validator = TimestampValidator()

    def replay_snapshot(self, snapshot: dict) -> dict:
        """Replay a single historical snapshot through Shadow CAO.

        Steps:
            1. Validate timestamp integrity
            2. Log decision (if not already logged)
            3. Run ablation
            4. If outcome exists, run dry-run attribution

        Returns replay result dict (never affects production).
        """
        from src.shadow_cao.ablation import run_decision_ablation
        from src.shadow_cao.attribution import run_dry_run_attribution
        from src.shadow_cao.logger import record_from_snapshot
        from src.shadow_cao.storage import (
            get_ablations_for_decision,
            get_decision_log,
            save_ablation_result,
            save_outcome_log,
        )

        decision_id = snapshot.get("decision_id", "unknown")
        timestamp = snapshot.get("timestamp", "")
        existing = get_decision_log(decision_id)
        if existing:
            replay_type = "replay_skip"
        else:
            entry = record_from_snapshot(snapshot)
            if entry is None:
                return {"decision_id": decision_id, "status": "failed", "error": "log_failed"}
            replay_type = "replay_new"
        ablations = get_ablations_for_decision(decision_id)
        if not ablations:
            raw_scores = snapshot.get("engine_scores")
            engine_scores = {}
            if raw_scores:
                if isinstance(raw_scores, str):
                    engine_scores = json.loads(raw_scores)
                elif isinstance(raw_scores, dict):
                    engine_scores = raw_scores
            if engine_scores:
                from src.shadow_cao.models import ShadowDecisionLog

                weights_raw = snapshot.get("decision_weights")
                decision_weights = {}
                if weights_raw:
                    if isinstance(weights_raw, str):
                        decision_weights = json.loads(weights_raw)
                    elif isinstance(weights_raw, dict):
                        decision_weights = weights_raw
                entry = ShadowDecisionLog(
                    decision_id=decision_id,
                    timestamp=timestamp,
                    posture=snapshot.get("posture", "HOLD"),
                    risk_level=snapshot.get("risk_level", "SAFE"),
                    confidence=float(snapshot.get("confidence", 50)),
                    engine_scores=engine_scores,
                    decision_weights=decision_weights,
                    market_regime=snapshot.get("market_regime", "UNKNOWN"),
                    regime_score=0.0,
                    vnindex_level=float(snapshot.get("vnindex_level", 0)),
                )
                results = run_decision_ablation(entry)
                for r in results:
                    save_ablation_result(r, decision_id)
                ablations = [r for r in results]
        raw_scores = snapshot.get("engine_scores")
        engine_scores = {}
        if raw_scores:
            if isinstance(raw_scores, str):
                engine_scores = json.loads(raw_scores)
            elif isinstance(raw_scores, dict):
                engine_scores = raw_scores
        from src.telemetry.storage import get_outcomes

        outcomes = get_outcomes(decision_id)
        if outcomes and engine_scores:
            from src.shadow_cao.models import AblationResult

            ablation_objs = (
                [
                    AblationResult(
                        engine_removed=a["engine_removed"],
                        baseline_action=a["baseline_action"],
                        baseline_confidence=a["baseline_confidence"],
                        ablated_action=a["ablated_action"],
                        ablated_confidence=a["ablated_confidence"],
                        action_changed=bool(a["action_changed"]),
                        confidence_delta=a["confidence_delta"],
                        decision_flip=bool(a["decision_flip"]),
                    )
                    for a in ablations
                ]
                if ablations
                else None
            )
            for outcome in outcomes:
                horizon = outcome["horizon_days"]
                market_return = outcome["vnindex_return"]
                success = bool(outcome["success"])
                self._validator.validate_decision_outcome(
                    decision_id,
                    timestamp,
                    outcome.get("evaluated_date", timestamp),
                    horizon,
                )
                save_outcome_log(decision_id, horizon, market_return, success)
                run_dry_run_attribution(
                    decision_id,
                    horizon,
                    engine_scores,
                    market_return,
                    ablation_objs,
                )
        replay_id = f"replay_{decision_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        from src.shadow_cao.storage import save_belief_value

        save_belief_value(
            replay_id,
            json.dumps(
                {
                    "decision_id": decision_id,
                    "type": replay_type,
                    "timestamp": datetime.now().isoformat(),
                    "outcomes_found": len(outcomes) if outcomes else 0,
                    "violations": self._validator.violations,
                }
            ),
        )
        return {
            "decision_id": decision_id,
            "status": "ok",
            "type": replay_type,
            "outcomes_processed": len(outcomes) if outcomes else 0,
            "ablations_run": len(ablations) if ablations else 0,
            "violations": len(self._validator.violations),
        }

    def batch_replay(self, snapshots: list[dict]) -> list[dict]:
        """Replay multiple snapshots (deterministic, isolated)."""
        results = []
        self._validator.reset()
        for snap in snapshots:
            result = self.replay_snapshot(snap)
            results.append(result)
        return results

    def batch_replay_from_telemetry(self, limit: int = 200) -> list[dict]:
        """Replay historical snapshots directly from telemetry storage."""
        from src.telemetry.storage import get_all_snapshots

        snapshots = get_all_snapshots(limit=limit)
        return self.batch_replay(snapshots)


# ====================================================================
# 5. HOOK WRAPPER (HIGH-LEVEL)
# ====================================================================


class SafeHookWrapper:
    """Production-safe hook wrapper with backpressure control.

    Guarantees:
        - NEVER blocks production pipeline
        - NEVER propagates exceptions
        - Runs in async event bus (daemon thread)
        - Drops events under backpressure (queue full)
    """

    def __init__(self, bus: ShadowEventBus = None):
        self.bus = bus or _event_bus
        self._validator = TimestampValidator()

    def post_decision(self, snapshot: dict):
        """Post a decision-recorded event asynchronously."""
        self.bus.post("decision_recorded", {"snapshot": snapshot})

    def post_outcome(
        self,
        decision_id: str,
        horizon_days: int,
        realized_return: float,
        success: bool,
        engine_scores: dict,
    ):
        """Post an outcome-evaluated event asynchronously."""
        self.bus.post(
            "outcome_evaluated",
            {
                "decision_id": decision_id,
                "horizon_days": horizon_days,
                "realized_return": realized_return,
                "success": success,
                "engine_scores": engine_scores,
            },
        )

    def validate_timestamp(
        self,
        decision_id: str,
        decision_ts: str,
        outcome_ts: str,
        horizon: int,
    ) -> bool:
        """Validate temporal alignment (can be called synchronously)."""
        return self._validator.validate_decision_outcome(
            decision_id,
            decision_ts,
            outcome_ts,
            horizon,
        )
