from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RegimeEventType = Literal["ACCUMULATION", "EXPANSION", "DISTRIBUTION", "CRISIS", "RECOVERY"]

TOLERANCE_DEFAULT = 5
MEMORY_ISOLATION_KEYS = ["_dbe_history", "_ttl_history", "_prev_regime", "_prev_trend_quality"]


class RegimeEvent(BaseModel):
    name: str = Field(description="Unique event label (e.g. 2023_Q2_UPTREND)")
    start: str = Field(description="Event start date (YYYY-MM-DD)")
    end: str = Field(description="Event end date (YYYY-MM-DD)")
    event_type: RegimeEventType = Field(description="Classification of the regime phase")
    description: str = Field(default="", description="Optional human-readable context")


class EvaluationWindowRule(BaseModel):
    tolerance_days: int = Field(
        default=TOLERANCE_DEFAULT, ge=0, le=20, description="Max days a TTL detection can deviate from ground truth"
    )
    require_type_match: bool = Field(default=True, description="Whether TTL transition type must match event type")
    grace_period_days: int = Field(default=2, ge=0, description="Days after start before expecting detection")


class MemoryIsolationRule(BaseModel):
    reset_modules: list[str] = Field(
        default_factory=lambda: list(MEMORY_ISOLATION_KEYS), description="Module-level globals to reset per run"
    )
    clear_dpl_on_start: bool = Field(default=True, description="Clear DPL history each run")
    clear_ttl_on_start: bool = Field(default=True, description="Clear TTL history each run")


class BacktestContract(BaseModel):
    name: str = Field(default="State Transition Reconstruction Test")
    regime_events: list[RegimeEvent] = Field(description="Ground truth regime events")
    evaluation_rule: EvaluationWindowRule = Field(default_factory=EvaluationWindowRule)
    memory_rule: MemoryIsolationRule = Field(default_factory=MemoryIsolationRule)


_REPLAY_START = "2023-01-01"
_REPLAY_END = "2026-06-01"


def _default_regime_events_2023_2026() -> list[RegimeEvent]:
    return [
        RegimeEvent(
            name="2023_Q1_ACCUMULATION",
            start="2023-01-01",
            end="2023-03-31",
            event_type="ACCUMULATION",
            description="VNINDEX tích lũy đáy sau 2022 crash",
        ),
        RegimeEvent(
            name="2023_Q2_RECOVERY",
            start="2023-04-01",
            end="2023-06-30",
            event_type="RECOVERY",
            description="Phục hồi từ vùng đáy, breadth cải thiện dần",
        ),
        RegimeEvent(
            name="2023_H2_EXPANSION",
            start="2023-07-01",
            end="2023-12-31",
            event_type="EXPANSION",
            description="Dòng tiền lan tỏa, VNINDEX trending up",
        ),
        RegimeEvent(
            name="2024_H1_DISTRIBUTION",
            start="2024-01-01",
            end="2024-06-30",
            event_type="DISTRIBUTION",
            description="Phân phối đỉnh, breadth divergence, flow yếu dần",
        ),
        RegimeEvent(
            name="2024_H2_ACCUMULATION",
            start="2024-07-01",
            end="2024-12-31",
            event_type="ACCUMULATION",
            description="Tích lũy lại, chờ catalyst mới",
        ),
        RegimeEvent(
            name="2025_H1_EXPANSION",
            start="2025-01-01",
            end="2025-06-30",
            event_type="EXPANSION",
            description="Tăng trưởng trở lại, flow mạnh",
        ),
        RegimeEvent(
            name="2025_H2_CRISIS",
            start="2025-07-01",
            end="2025-12-31",
            event_type="CRISIS",
            description="Khủng hoảng/correction mạnh",
        ),
        RegimeEvent(
            name="2026_Q1_RECOVERY",
            start="2026-01-01",
            end="2026-03-31",
            event_type="RECOVERY",
            description="Phục hồi từ đáy 2025",
        ),
    ]


def _default_event_rules() -> dict[str, EvaluationWindowRule]:
    return {
        "2023_Q1_ACCUMULATION": EvaluationWindowRule(tolerance_days=7),
        "2023_H2_EXPANSION": EvaluationWindowRule(tolerance_days=5),
        "2024_H1_DISTRIBUTION": EvaluationWindowRule(tolerance_days=5),
        "2025_H2_CRISIS": EvaluationWindowRule(tolerance_days=3),
    }


def default_contract() -> BacktestContract:
    return BacktestContract(
        name="VNINDEX 2023-2026 State Transition Test",
        regime_events=_default_regime_events_2023_2026(),
        evaluation_rule=EvaluationWindowRule(tolerance_days=TOLERANCE_DEFAULT),
        memory_rule=MemoryIsolationRule(),
    )


def get_event_by_name(contract: BacktestContract, name: str) -> RegimeEvent | None:
    for e in contract.regime_events:
        if e.name == name:
            return e
    return None


def get_events_by_type(contract: BacktestContract, event_type: RegimeEventType) -> list[RegimeEvent]:
    return [e for e in contract.regime_events if e.event_type == event_type]


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def tolerance_for_event(contract: BacktestContract, event_name: str) -> int:
    event_rules = _default_event_rules()
    if event_name in event_rules:
        return event_rules[event_name].tolerance_days
    return contract.evaluation_rule.tolerance_days
