"""Shadow CAO Logger — captures decision snapshots at decision time (REAL data only)."""

import json
import logging
import sys
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
from src.shadow_cao.ablation import run_decision_ablation
from src.shadow_cao.models import ShadowDecisionLog
from src.shadow_cao.storage import (
    initialize_shadow_database,
    save_ablation_result,
    save_decision_log,
)


def log_decision(
    decision_id: str,
    timestamp: str,
    posture: str,
    risk_level: str,
    confidence: float,
    engine_scores: dict,
    decision_weights: dict,
    market_regime: str | None = None,
    regime_score: float = 0.0,
    vnindex_level: float = 0.0,
    run_ablations: bool = True,
) -> ShadowDecisionLog | None:
    """Capture a decision into Shadow CAO (REAL data only, no synthetic outcomes).

    Called from the telemetry recorder after a decision is saved.
    Does NOT modify production data — writes to shadow_cao.db namespace.
    """
    try:
        initialize_shadow_database()
    except Exception:  # noqa: BLE001, S110 - cố ý bắt rộng & bỏ qua phụ (fallback/phòng thủ)
        pass
    entry = ShadowDecisionLog(
        decision_id=decision_id,
        timestamp=timestamp,
        posture=posture,
        risk_level=risk_level,
        confidence=confidence,
        engine_scores=engine_scores,
        decision_weights=decision_weights or {},
        market_regime=market_regime or "UNKNOWN",
        regime_score=regime_score,
        vnindex_level=vnindex_level,
    )
    saved = save_decision_log(entry)
    if not saved:
        logger.warning("[SHADOW_CAO] Failed to persist decision %s", decision_id)
        return None
    if run_ablations:
        try:
            run_pipeline_ablations(entry)
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            logger.error("[SHADOW_CAO] Ablation run failed for %s: %s", decision_id, e)
    return entry


def run_pipeline_ablations(entry: ShadowDecisionLog):
    """Run engine ablation simulation on a single decision.

    Perturbs the DECISION SPACE only — keeps market outcome fixed.
    For each engine: zero its score, recompute action + confidence.
    This is an ablation test, NOT synthetic data generation.
    """
    results = run_decision_ablation(entry)
    for result in results:
        save_ablation_result(result, entry.decision_id)


def record_from_snapshot(snapshot: dict) -> ShadowDecisionLog | None:
    """Convenience wrapper: ingest from telemetry snapshot dict."""
    raw_scores = snapshot.get("engine_scores")
    engine_scores = {}
    if raw_scores:
        if isinstance(raw_scores, str):
            try:
                engine_scores = json.loads(raw_scores)
            except json.JSONDecodeError, TypeError:
                pass
        elif isinstance(raw_scores, dict):
            engine_scores = raw_scores
    raw_weights = snapshot.get("decision_weights")
    decision_weights = {}
    if raw_weights:
        if isinstance(raw_weights, str):
            try:
                decision_weights = json.loads(raw_weights)
            except json.JSONDecodeError, TypeError:
                pass
        elif isinstance(raw_weights, dict):
            decision_weights = raw_weights
    regime = snapshot.get("market_regime")
    return log_decision(
        decision_id=snapshot.get("decision_id", "unknown"),
        timestamp=snapshot.get("timestamp", ""),
        posture=snapshot.get("posture", "HOLD"),
        risk_level=snapshot.get("risk_level", "SAFE"),
        confidence=float(snapshot.get("confidence", 50)),
        engine_scores=engine_scores,
        decision_weights=decision_weights,
        market_regime=regime,
        vnindex_level=float(snapshot.get("vnindex_level", 0)),
    )
