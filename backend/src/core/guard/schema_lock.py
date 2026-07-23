from pydantic import BaseModel, Field
from datetime import datetime, timezone

# Increment this when DecisionView schema changes
DECISION_VIEW_SCHEMA_VERSION = "1.0.0"

ALLOWED_KEYS = frozenset({
    "recommended_posture", "risk_level", "risk_color", "dominant_signal_vi",
    "signal_alignment", "confidence_summary_vi", "primary_conflict_vi",
    "short_explanation_vi", "time_horizon", "decision_urgency", "entropy_state",
})


class SchemaLockState(BaseModel):
    version: str = Field(default=DECISION_VIEW_SCHEMA_VERSION)
    locked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_locked: bool = Field(default=False)


_schema_lock = SchemaLockState()


def lock_schema():
    _schema_lock.is_locked = True
    _schema_lock.locked_at = datetime.now(timezone.utc)


def unlock_schema():
    _schema_lock.is_locked = False


def validate_decision_view_keys(data: dict) -> list[str]:
    extra_keys = set(data.keys()) - ALLOWED_KEYS
    if extra_keys:
        return [f"Unexpected key '{k}' not in DecisionView schema v{DECISION_VIEW_SCHEMA_VERSION}" for k in extra_keys]
    return []


def check_schema_version(data_version: str) -> bool:
    return data_version == DECISION_VIEW_SCHEMA_VERSION
