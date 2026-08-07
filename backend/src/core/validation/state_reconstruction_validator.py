from datetime import datetime

from core.presentation import CausalAttributionReport
from core.presentation.direction_persistence_layer import (
    clear_history as clear_dpl_history,
)
from core.presentation.direction_persistence_layer import (
    compute_direction_persistence,
)
from core.presentation.directional_bias_extractor import compute_directional_bias
from core.presentation.transition_trigger_layer import (
    clear_ttl_history,
    compute_transition_trigger,
)
from pydantic import BaseModel, Field

from .backtest_contract import (
    BacktestContract,
    RegimeEvent,
    parse_date,
    tolerance_for_event,
)

_DBE_SIGN_MAP = {
    "BULLISH": 1.0,
    "TRANSITIONAL": 1.0,
    "NEUTRAL": 0.0,
    "FRACTURED": 0.0,
    "BEARISH": -1.0,
}


REQUIRED_FIELDS = [
    "date",
    "regime_status",
    "trade_state_level",
    "breadth_health",
    "lcr_pct",
    "bdi_signal",
    "flow_bias_score",
    "ssi_score",
    "dcl_verdict",
    "dcl_score",
    "compensations_triggered",
]


class DailySnapshot(BaseModel):
    date: str
    regime_status: str = "UNKNOWN"
    trade_state_level: str = "PROHIBITED"
    breadth_health: float = 0.0
    lcr_pct: float = 30.0
    bdi_signal: str = "CAN_BANG"
    flow_bias_score: float = 0.0
    flow_label: str = "UNKNOWN"
    ssi_score: float = 0.5
    dcl_verdict: str = "NO_TRADE"
    dcl_score: float = 0.0
    compensations_triggered: int = 0


def validate_snapshot(snapshot: dict) -> str | None:
    for field in REQUIRED_FIELDS:
        if field not in snapshot:
            return f"Missing field: {field}"
    return None


def _sign(code: str) -> float:
    return _DBE_SIGN_MAP.get(code, 0.0)


def _reset_memory():
    clear_dpl_history()
    clear_ttl_history()


def _event_overlaps(event: RegimeEvent, ttl_detection_date: str, tolerance_days: int) -> bool:
    ed = parse_date(event.start)
    end_d = parse_date(event.end)
    td = parse_date(ttl_detection_date)
    window_start = ed - __import__("datetime").timedelta(days=tolerance_days)
    window_end = end_d + __import__("datetime").timedelta(days=tolerance_days)
    return window_start <= td <= window_end


class SuiteAMetrics(BaseModel):
    total_days: int = 0
    flip_count: int = 0
    flip_rate: float = 0.0
    mean_confidence: float = 0.0
    mean_strength: float = 0.0


class SuiteBMetrics(BaseModel):
    persistent_days: int = 0
    transitional_days: int = 0
    flickering_days: int = 0
    flicker_pct: float = 0.0
    mean_stability: float = 0.0
    mean_stability_when_persistent: float = 0.0
    mean_stability_when_flickering: float = 0.0


class TransitionMatch(BaseModel):
    event_name: str
    event_type: str
    ttl_date: str
    ttl_type: str
    delay_days: int
    is_hit: bool
    causal_attribution: CausalAttributionReport | None = None


class SuiteCMetrics(BaseModel):
    total_events: int = 0
    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    mean_delay_days: float = 0.0
    false_positive_rate: float = 0.0
    total_ttl_triggers: int = 0
    matches: list[TransitionMatch] = []


class SRVReport(BaseModel):
    contract_name: str = ""
    run_date: str = ""
    total_days_processed: int = 0
    date_range: str = ""
    suite_a: SuiteAMetrics = Field(default_factory=SuiteAMetrics)
    suite_b: SuiteBMetrics = Field(default_factory=SuiteBMetrics)
    suite_c: SuiteCMetrics = Field(default_factory=SuiteCMetrics)
    regime_type_summary: dict = Field(default_factory=dict)
    regime_sequence: list[str] = Field(default_factory=list)


def run_srv(
    snapshots: list[dict],
    contract: BacktestContract | None = None,
) -> SRVReport:
    if contract is None:
        from .backtest_contract import default_contract

        contract = default_contract()

    _reset_memory()

    validated = []
    for s in snapshots:
        err = validate_snapshot(s)
        if err:
            continue
        validated.append(DailySnapshot(**s))

    if not validated:
        return SRVReport(contract_name=contract.name, total_days_processed=0)

    suite_a = SuiteAMetrics(total_days=len(validated))
    suite_b = SuiteBMetrics()
    suite_c = SuiteCMetrics(total_events=len(contract.regime_events))
    suite_c.total_ttl_triggers = 0

    dbe_log: list[dict] = []
    dpl_log: list[dict] = []
    ttl_log: list[dict] = []

    for snap in validated:
        dbe = compute_directional_bias(
            regime_status=snap.regime_status,
            trade_state_level=snap.trade_state_level,
            breadth_health=snap.breadth_health,
            lcr_pct=snap.lcr_pct,
            bdi_signal=snap.bdi_signal,
            flow_bias_score=snap.flow_bias_score,
            flow_label=snap.flow_label,
            ssi_score=snap.ssi_score,
            dcl_verdict=snap.dcl_verdict,
            dcl_score=snap.dcl_score,
            compensations_triggered=snap.compensations_triggered,
        )
        dpl = compute_direction_persistence(dbe)
        ttl = compute_transition_trigger(
            dpl=dpl,
            dbe=dbe,
            regime_status=snap.regime_status,
        )

        dbe_log.append(
            {
                "date": snap.date,
                "bias_code": dbe.bias_code,
                "bias_strength": dbe.bias_strength,
                "bias_confidence": dbe.bias_confidence,
                "dominant_force": dbe.dominant_force,
                "bias_drivers": dbe.bias_drivers,
            }
        )
        dpl_log.append(
            {
                "date": snap.date,
                "trend_quality": dpl.trend_quality_code,
                "stability": dpl.dbe_stability_score,
                "flicker_risk": dpl.flicker_risk_code,
                "windows": dpl.windows_available,
            }
        )

        if ttl.transition_state == "TRIGGERED":
            from core.presentation.causal_binding_layer import compute_causal_attribution

            ttl.causal_attribution = compute_causal_attribution(
                transition_type=ttl.transition_type, dbe_history=dbe_log, lookback_days=5
            )

        ttl_log.append(
            {
                "date": snap.date,
                "transition_state": ttl.transition_state,
                "transition_type": ttl.transition_type,
                "trigger_confidence": ttl.trigger_confidence,
                "transitions_24h": ttl.transitions_24h,
                "causal_attribution": ttl.causal_attribution.model_dump() if ttl.causal_attribution else None,
            }
        )

    # ── Suite A: DBE Stability ──────────────────────────────────────────
    signs = [_sign(e["bias_code"]) for e in dbe_log]
    flip_count = 0
    for i in range(1, len(signs)):
        if signs[i] * signs[i - 1] < 0:
            flip_count += 1
    suite_a.flip_count = flip_count
    suite_a.flip_rate = round(flip_count / max(1, len(signs) - 1), 4)
    suite_a.mean_confidence = round(sum(e["bias_confidence"] for e in dbe_log) / len(dbe_log), 4)
    suite_a.mean_strength = round(sum(e["bias_strength"] for e in dbe_log) / len(dbe_log), 4)

    # ── Suite B: DPL Persistence ────────────────────────────────────────
    persistent = [e for e in dpl_log if e["trend_quality"] == "PERSISTENT"]
    transitional = [e for e in dpl_log if e["trend_quality"] == "TRANSITIONAL"]
    flickering = [e for e in dpl_log if e["trend_quality"] == "FLICKERING"]
    suite_b.persistent_days = len(persistent)
    suite_b.transitional_days = len(transitional)
    suite_b.flickering_days = len(flickering)
    suite_b.flicker_pct = round(len(flickering) / max(1, len(dpl_log)), 4)
    suite_b.mean_stability = round(sum(e["stability"] for e in dpl_log) / len(dpl_log), 4)
    suite_b.mean_stability_when_persistent = (
        round(sum(e["stability"] for e in persistent) / max(1, len(persistent)), 4) if persistent else 0.0
    )
    suite_b.mean_stability_when_flickering = (
        round(sum(e["stability"] for e in flickering) / max(1, len(flickering)), 4) if flickering else 0.0
    )

    # ── Suite C: TTL Transition ─────────────────────────────────────────
    triggers = [e for e in ttl_log if e["transition_state"] == "TRIGGERED"]
    suite_c.total_ttl_triggers = len(triggers)
    false_positives = 0
    matched_events = set()

    for trigger in triggers:
        ttl_date = trigger["date"]
        ttl_type = trigger["transition_type"]
        best_event: RegimeEvent | None = None
        best_delay = 999
        for event in contract.regime_events:
            if event.name in matched_events:
                continue
            tol = tolerance_for_event(contract, event.name)
            ed = parse_date(event.start)
            td = parse_date(ttl_date)
            delay = abs((td - ed).days)
            if delay <= tol:
                if best_event is None or delay < best_delay:
                    best_event = event
                    best_delay = delay
        if best_event is not None:
            suite_c.hits += 1
            matched_events.add(best_event.name)
            suite_c.matches.append(
                TransitionMatch(
                    event_name=best_event.name,
                    event_type=best_event.event_type,
                    ttl_date=ttl_date,
                    ttl_type=ttl_type,
                    delay_days=best_delay,
                    is_hit=True,
                    causal_attribution=trigger.get("causal_attribution"),
                )
            )

        else:
            false_positives += 1

    suite_c.misses = len(contract.regime_events) - suite_c.hits
    suite_c.hit_rate = round(suite_c.hits / max(1, len(contract.regime_events)), 4)
    suite_c.false_positive_rate = round(false_positives / max(1, len(triggers)), 4) if triggers else 0.0
    suite_c.mean_delay_days = (
        round(sum(m.delay_days for m in suite_c.matches) / max(1, len(suite_c.matches)), 1) if suite_c.matches else 0.0
    )

    # ── Regime type summary ────────────────────────────────────────────
    regime_types = {}
    regime_sequence = []
    for snap in validated:
        rt = snap.regime_status
        regime_types[rt] = regime_types.get(rt, 0) + 1
        regime_sequence.append(rt)

    report = SRVReport(
        contract_name=contract.name,
        run_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        total_days_processed=len(validated),
        date_range=f"{validated[0].date} → {validated[-1].date}",
        suite_a=suite_a,
        suite_b=suite_b,
        suite_c=suite_c,
        regime_type_summary=regime_types,
        regime_sequence=regime_sequence,
    )
    return report


def export_srv_report(report: SRVReport, path: str) -> None:
    import json
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(), f, indent=2, ensure_ascii=False)
    print(f"  [SRV] Report saved → {p}")
