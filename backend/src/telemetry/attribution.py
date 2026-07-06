"""Attribution Engine v1 (Sprint 2 canonical) — deterministic engine contribution decomposition"""
import json
import logging
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

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

from src.database.db_core import get_connection
from src.telemetry.models import DecisionAttributionSummary, EngineAttribution, EnginePerformanceView
from src.telemetry.storage import (
    get_attribution_summary,
    get_attributions,
    get_snapshot,
    save_engine_attribution,
    save_engine_performance,
)

ENGINES = ["regime", "liquidity", "sector", "breakout", "heat", "signal", "memory", "dampener"]


def _get_vnindex_level_at(entry_date: str, lookback: int = 0) -> float:
    try:
        with get_connection() as conn:
            if lookback > 0:
                target = (datetime.strptime(entry_date, "%Y-%m-%d") - timedelta(days=lookback)).strftime("%Y-%m-%d")
                row = conn.execute(
                    "SELECT close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date DESC LIMIT 1",
                    (target,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date <= ? ORDER BY date DESC LIMIT 1",
                    (entry_date,)
                ).fetchone()
            if row:
                return float(row[0])
    except Exception as e:
        logger.warning("[ATTRIBUTION] Cannot fetch VNINDEX: %s", e)
    return 0.0


def _compute_correlation(values_a: list[float], values_b: list[float]) -> float:
    if len(values_a) < 3 or len(values_b) < 3:
        return 0.0
    n = len(values_a)
    mean_a = sum(values_a) / n
    mean_b = sum(values_b) / n
    num = sum((a - mean_a) * (b - mean_b) for a, b in zip(values_a, values_b))
    den = math.sqrt(sum((a - mean_a) ** 2 for a in values_a)) * math.sqrt(sum((b - mean_b) ** 2 for b in values_b))
    return num / den if den != 0 else 0.0


def _get_engine_signal_stability(decision_ids: list[str], engine: str) -> float:
    signals = []
    for did in decision_ids[-20:]:
        snap = get_snapshot(did)
        if snap and snap.get("engine_scores"):
            try:
                raw = snap["engine_scores"]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                scores = json.loads(raw)
                if engine in scores:
                    signals.append(scores[engine])
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                continue
    if len(signals) < 3:
        return 0.5
    mean_s = sum(signals) / len(signals)
    variance = sum((s - mean_s) ** 2 for s in signals) / len(signals)
    std = math.sqrt(variance)
    return max(0.0, min(1.0, 1.0 - std))


def _get_historical_market_returns(decision_ids: list[str], horizon_days: int) -> list[float]:
    returns = []
    for did in decision_ids[-30:]:
        snap = get_snapshot(did)
        if not snap:
            continue
        entry_date = snap["timestamp"][:10] if isinstance(snap["timestamp"], str) else str(snap["timestamp"])[:10]
        entry_price = snap["vnindex_level"]
        if entry_price <= 0:
            continue
        target_date = (datetime.strptime(entry_date, "%Y-%m-%d") + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
        exit_price = _get_vnindex_level_at(target_date)
        if exit_price <= 0:
            continue
        returns.append((exit_price - entry_price) / entry_price)
    return returns


def _compute_tp_fp(signal: float, market_return: float, decision_posture: str) -> tuple[float, float]:
    """Compute true positive and false positive contribution for an engine signal."""
    signal_bullish = signal >= 0.5
    market_up = market_return > 0
    posture_bullish = decision_posture.upper() in ("ENTER", "SCALE_IN", "HOLD")

    if signal_bullish and market_up:
        return (min(signal, abs(market_return) * 2), 0.0)
    elif signal_bullish and not market_up:
        return (0.0, signal * abs(market_return))
    elif not signal_bullish and not market_up:
        return (min(1.0 - signal, abs(market_return) * 2), 0.0)
    else:
        return (0.0, (1.0 - signal) * abs(market_return))


def _generate_primary_reason_vi(
    dominant_engine: str, contribution: float,
    market_return: float, outcome_label: str,
) -> str:
    if outcome_label == "DUNG":
        return (
            f"{dominant_engine.upper()} là engine đóng góp nhiều nhất "
            f"({contribution:.0%}) vào quyết định đúng"
        )
    else:
        return (
            f"{dominant_engine.upper()} là engine gây thiệt hại nhiều nhất "
            f"({contribution:.0%}) — tín hiệu lệch pha thị trường"
        )


def _generate_secondary_reasons_vi(attributions: list) -> list[str]:
    reasons = []
    for a in attributions[:3]:
        tp = getattr(a, "true_positive_contribution", 0)
        fp = getattr(a, "false_positive_contribution", 0)
        if tp > fp:
            reasons.append(f"{a.engine.upper()}: tín hiệu đúng ({tp:.2f})")
        else:
            reasons.append(f"{a.engine.upper()}: nhiễu ({fp:.2f})")
    return reasons


def decompose_attribution(
    decision_id: str,
    horizon_days: int,
    engine_scores: dict[str, float],
) -> list[EngineAttribution]:
    """Canonical decomposition — contribution, TP/FP, correlation, precision."""
    snap = get_snapshot(decision_id)
    if not snap or not engine_scores:
        logger.warning("[ATTRIBUTION] No snapshot or scores for %s", decision_id)
        return []

    entry_date = snap["timestamp"][:10] if isinstance(snap["timestamp"], str) else str(snap["timestamp"])[:10]
    entry_price = snap["vnindex_level"]
    target_date = (datetime.strptime(entry_date, "%Y-%m-%d") + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    exit_price = _get_vnindex_level_at(target_date)
    if exit_price <= 0 or entry_price <= 0:
        return []

    market_return = (exit_price - entry_price) / entry_price
    market_direction = 1 if market_return >= 0 else -1

    from src.telemetry.storage import get_all_snapshots
    all_snaps = get_all_snapshots(50)
    recent_ids = [s["decision_id"] for s in all_snaps]

    results = []
    total_weighted = 0.0
    raw_contribs = {}

    for engine, signal in engine_scores.items():
        historical_returns = _get_historical_market_returns(recent_ids, horizon_days)
        historical_signals = []
        for did in recent_ids[-20:]:
            s = get_snapshot(did)
            if s and s.get("engine_scores"):
                try:
                    raw = s["engine_scores"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    scores = json.loads(raw)
                    if engine in scores:
                        historical_signals.append(scores[engine])
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    continue

        correlation = _compute_correlation(
            historical_signals[:len(historical_returns)],
            historical_returns[:len(historical_signals)]
        ) if historical_signals and historical_returns else 0.0

        stability = _get_engine_signal_stability(recent_ids, engine)
        signal_direction = 1 if signal >= 0.5 else -1 if signal <= 0.4 else 0
        alignment = 1.0 if signal_direction == market_direction else -1.0 if signal_direction != 0 else 0.0

        contribution = max(0.0, correlation) * stability * max(0.0, alignment)
        raw_contribs[engine] = contribution
        total_weighted += contribution

    epsilon = 1e-6
    if total_weighted > epsilon:
        for engine, raw in raw_contribs.items():
            normalized = raw / total_weighted
            signal = engine_scores.get(engine, 0.5)
            direction_str = "BULLISH" if signal >= 0.5 else "BEARISH"
            accuracy_val = max(0.0, min(1.0, (correlation or 0) * 0.5 + 0.5))
            tp, fp = _compute_tp_fp(signal, market_return, snap.get("posture", "HOLD"))
            precision_val = tp / (tp + fp + 1e-8)

            result = EngineAttribution(
                decision_id=decision_id,
                horizon_days=horizon_days,
                engine=engine,
                signal_at_decision=signal,
                contribution=round(normalized, 4),
                true_positive_contribution=round(tp, 4),
                false_positive_contribution=round(fp, 4),
                direction=direction_str,
                correlation=round(correlation or 0, 4),
                accuracy=round(accuracy_val, 4),
                precision=round(precision_val, 4),
            )
            results.append(result)
            save_engine_attribution(result)
    else:
        equal = 1.0 / max(len(engine_scores), 1)
        for engine, signal in engine_scores.items():
            direction_str = "BULLISH" if signal >= 0.5 else "BEARISH"
            tp, fp = _compute_tp_fp(signal, market_return, snap.get("posture", "HOLD"))
            result = EngineAttribution(
                decision_id=decision_id,
                horizon_days=horizon_days,
                engine=engine,
                signal_at_decision=signal,
                contribution=round(equal, 4),
                true_positive_contribution=round(tp, 4),
                false_positive_contribution=round(fp, 4),
                direction=direction_str,
                correlation=0.0,
                accuracy=0.5,
                precision=0.5,
            )
            results.append(result)
            save_engine_attribution(result)

    results.sort(key=lambda r: r.contribution, reverse=True)
    logger.info(
        "[ATTRIBUTION] %s | %dd | dominant=%s contrib=%.2f",
        decision_id, horizon_days,
        results[0].engine if results else "?",
        results[0].contribution if results else 0,
    )
    return results


def generate_summary_vi(decision_id: str, horizon_days: int) -> Optional[DecisionAttributionSummary]:
    """Generate Vietnamese UI summary from stored attribution data."""
    raw = get_attribution_summary(decision_id, horizon_days)
    if not raw:
        return None

    from src.telemetry.storage import get_outcomes
    outcome = get_outcomes(decision_id)
    total_return = raw["total_return"]
    outcome_label = "DUNG" if total_return >= 0 else "SAI"

    return DecisionAttributionSummary(
        decision_id=decision_id,
        horizon_days=horizon_days,
        outcome_label=outcome_label,
        primary_reason_vi=_generate_primary_reason_vi(
            raw["dominant_engine"],
            raw["engine_scorecard"].get(raw["dominant_engine"], 0),
            total_return,
            outcome_label,
        ),
        secondary_reasons_vi=raw.get("secondary_reasons_vi", []),
        engine_scorecard=raw["engine_scorecard"],
        dominant_engine=raw["dominant_engine"],
        total_return=total_return,
        alpha_return=raw["alpha_return"],
        confidence_recalibration=raw["confidence_recalibration"],
    )


def update_engine_performance(window_days: int = 30):
    """Canonical rolling performance with precision tracking."""
    from src.telemetry.storage import get_all_snapshots
    all_snaps = get_all_snapshots(200)
    if not all_snaps:
        return []

    cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()
    recent = [s for s in all_snaps if s.get("timestamp", "") >= cutoff]

    results = []
    for engine in ENGINES:
        attribs = []
        for snap in recent[:50]:
            aid = snap["decision_id"]
            rows = get_attributions(aid)
            for r in rows:
                if r["engine"] == engine:
                    attribs.append(r)

        if not attribs:
            continue

        total_contrib = sum(a["contribution"] for a in attribs)
        accuracy_sum = sum(a["accuracy"] for a in attribs)
        precision_sum = sum(a.get("precision", 0.5) for a in attribs)
        contribs_list = [a["contribution"] for a in attribs]
        mean_contrib = total_contrib / len(attribs) if attribs else 0
        variance = sum((c - mean_contrib) ** 2 for c in contribs_list) / len(contribs_list) if contribs_list else 0
        stability = max(0.0, min(1.0, 1.0 - math.sqrt(variance)))

        perf = EnginePerformanceView(
            engine=engine,
            window_days=window_days,
            accuracy=round(accuracy_sum / len(attribs), 4),
            precision=round(precision_sum / len(attribs), 4),
            avg_contribution=round(mean_contrib, 4),
            stability=round(stability, 4),
            decisions_count=len(attribs),
            last_updated=datetime.now().isoformat(),
        )
        save_engine_performance(perf)
        results.append(perf)

    logger.info("[ATTRIBUTION] Engine performance updated for %d engines (%dd window)", len(results), window_days)
    return results


def run_attribution_for_outcomes(outcome_records: list) -> int:
    """Run attribution for a list of newly evaluated outcomes."""
    count = 0
    from src.telemetry.storage import get_snapshot
    for outcome in outcome_records:
        did = outcome.decision_id
        horizon = outcome.horizon_days
        snap = get_snapshot(did)
        if not snap or not snap.get("engine_scores"):
            continue
        try:
            raw = snap["engine_scores"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            scores = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            continue
        if not scores:
            continue
        try:
            decompose_attribution(did, horizon, scores)
            count += 1
        except Exception as e:
            logger.error("[ATTRIBUTION] Failed for %s/%dd: %s", did, horizon, e)
    if count:
        update_engine_performance(window_days=30)
    return count
