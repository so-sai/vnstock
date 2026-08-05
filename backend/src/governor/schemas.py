"""schemas.py — Pydantic v2 Boundary Validation Layer (Data Membrane).

Purpose: Intercept untrusted data at the boundary between Crawler/DB and Governor.
Every raw dict, tuple, or JSON payload must pass through these schemas before
entering the Governor engine. This prevents:
  - Silent None propagation
  - Type mismatch (str where float expected)
  - Missing required keys
  - Extra keys leaking in (data pollution)

Usage:
    raw = {"date": "2026-08-05", "variable": "US10Y", "value": 4.25}
    record = MacroRecordSchema.model_validate(raw)  # raises ValidationError if invalid
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


# ── Base Config ──────────────────────────────────────────────────────────────
class StrictBaseModel(BaseModel):
    """Immutable, extra-forbid base for all boundary schemas.

    - frozen=True:   objects cannot be mutated after creation
    - extra='forbid': unknown keys raise ValidationError (no silent pollution)
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=False,
    )


# ── Macro Data ───────────────────────────────────────────────────────────────
class MacroRecordSchema(StrictBaseModel):
    """Validates a single row from macro_history table.

    SQLite schema: macro_history(variable TEXT, date TEXT, value REAL, source TEXT)
    """

    variable: str = Field(min_length=1, max_length=50)
    date: str
    value: float
    source: str = "vnstock"
    is_synthetic: int = Field(default=0, ge=0, le=1)

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError, TypeError:
            raise ValueError(f"Date must be YYYY-MM-DD format, got: {v!r}")
        return v


class MacroVectorSchema(StrictBaseModel):
    """Validates a macro state vector (5-component normalized [0,1])."""

    us10y: float = Field(ge=0.0, le=1.0)
    vnd_usd: float = Field(ge=0.0, le=1.0)
    interbank: float = Field(ge=0.0, le=1.0)
    fii_flow: float = Field(ge=0.0, le=1.0)
    breadth: float = Field(ge=0.0, le=1.0)


# ── Financial Health Ratios ──────────────────────────────────────────────────
class HealthRatioSchema(StrictBaseModel):
    """Validates a single row from health_ratios table.

    SQLite schema: health_ratios(symbol TEXT, ratio_name TEXT, ratio_value REAL, period TEXT)
    This schema validates AFTER the row is unpacked into named fields.
    """

    symbol: str = Field(min_length=1, max_length=10)
    period: str
    roe: Optional[float] = None
    capital_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    nim: Optional[float] = Field(default=None, ge=-0.1, le=1.0)
    npl_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    debt_to_equity: Optional[float] = Field(default=None, ge=0.0)
    volume: float = Field(default=0.0, ge=0.0)

    @field_validator("period")
    @classmethod
    def validate_period_format(cls, v: str) -> str:
        """Period must be YYYYQN format (e.g., 2025Q3)."""
        if not v or len(v) < 5:
            raise ValueError(f"Period must be YYYYQN format, got: {v!r}")
        if v[4] != "Q":
            raise ValueError(f"Period must contain 'Q' separator, got: {v!r}")
        return v


class CompanyHealthSnapshot(StrictBaseModel):
    """Aggregated health data for a company at a point in time.

    Used by VN20 Gate and BayesianMandate to validate input data integrity.
    """

    symbol: str = Field(min_length=1, max_length=10)
    target_date: str
    roe_annual: float = Field(ge=-2.0, le=5.0)
    is_bank: bool = False
    capital_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    nim_annual: Optional[float] = Field(default=None, ge=-0.4, le=4.0)
    npl_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    debt_to_equity: Optional[float] = Field(default=None, ge=0.0)
    volume: float = Field(ge=0.0)

    @field_validator("target_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError, TypeError:
            raise ValueError(f"Date must be YYYY-MM-DD format, got: {v!r}")
        return v


# ── Policy Event (delta_params validation) ───────────────────────────────────
class DeltaParamsSchema(StrictBaseModel):
    """Validates delta_params inside a PolicyEvent.

    Fields are Optional because not all event types use all parameters.
    Pydantic validates: if a field IS present, it must be the correct type/range.
    """

    ldr_relief_bps: Optional[float] = Field(default=None, ge=-1000.0, le=2000.0)
    cof_relief_bps: Optional[float] = Field(default=None, ge=-500.0, le=500.0)
    interbank_shock_pct: Optional[float] = Field(default=None, ge=-5.0, le=5.0)
    nim_boost_bps: Optional[float] = Field(default=None, ge=-100.0, le=200.0)
    room_boost_pct: Optional[float] = Field(default=None, ge=0.0, le=20.0)
    rate_cut_pct: Optional[float] = Field(default=None, ge=-5.0, le=5.0)
    rrr_change_pct: Optional[float] = Field(default=None, ge=-5.0, le=5.0)
    gate_relaxation: Optional[Dict[str, float]] = None


class PolicyEventInputSchema(StrictBaseModel):
    """Validates raw dict before constructing PolicyEvent dataclass.

    Catches: missing required fields, wrong types, expired dates,
    and invalid delta_params at the boundary.
    """

    id: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    effective_date: str
    expiry_date: str
    event_type: str = Field(min_length=1)
    half_life_days: float = Field(gt=0.0, le=365.0)
    decay_window_days: int = Field(default=90, ge=0, le=730)
    transmission_lag_days: int = Field(default=0, ge=0, le=365)
    affected_variables: list[str] = Field(default_factory=list)
    clusters: Dict[str, float] = Field(default_factory=dict)
    delta_params: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    source: str = ""

    @field_validator("effective_date", "expiry_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError, TypeError:
            raise ValueError(f"Date must be YYYY-MM-DD format, got: {v!r}")
        return v

    @field_validator("delta_params")
    @classmethod
    def validate_delta_params(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validate delta_params content through DeltaParamsSchema."""
        if v:
            DeltaParamsSchema.model_validate(v)
        return v


# ── VN20 Gate Input ──────────────────────────────────────────────────────────
class VN20GateInput(StrictBaseModel):
    """Validates inputs to _vn20_gate before the branching logic.

    Prevents: None propagation, type mismatch, missing keys in the
    dict returned by SQLite fetchone().
    """

    symbol: str = Field(min_length=1, max_length=10)
    target_date: str
    roe_quarterly: float = Field(ge=-1.0, le=2.0)
    capital_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    nim_quarterly: Optional[float] = Field(default=None, ge=-0.25, le=1.0)
    npl_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    debt_to_equity: Optional[float] = Field(default=None, ge=0.0)
    volume: float = Field(default=0.0, ge=0.0)

    @field_validator("target_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError, TypeError:
            raise ValueError(f"Date must be YYYY-MM-DD format, got: {v!r}")
        return v


# ── Helper: Safe Parsing ─────────────────────────────────────────────────────
def safe_validate(schema_class: type[StrictBaseModel], data: dict[str, Any], label: str = "") -> Optional[StrictBaseModel]:
    """Parse untrusted data through Pydantic schema; return None on failure.

    WHY: At boundary points, a validation failure should degrade gracefully
    (return None / default) rather than crash the Governor. The caller logs
    the ValidationError for debugging.
    """
    try:
        return schema_class.model_validate(data)
    except ValidationError:
        logger.warning(
            "[SCHEMA] Validation failed for %s: keys=%s",
            label or schema_class.__name__,
            list(data.keys()) if isinstance(data, dict) else type(data).__name__,
        )
        return None


def validate_finite_float(value: Any, label: str = "", min_val: float = -1e10, max_val: float = 1e10) -> Optional[float]:
    """Validate that a value is a finite float within range.

    Returns the validated float or None if invalid.
    Used at SQLite boundary points where only a scalar value is available
    (not enough for a full Pydantic schema).
    """
    if value is None:
        return None
    try:
        f = float(value)
    except ValueError, TypeError:
        logger.warning("[SCHEMA] Non-numeric value at %s: %r", label, value)
        return None
    import math

    if math.isnan(f) or math.isinf(f):
        logger.warning("[SCHEMA] NaN/Inf rejected at %s: %r", label, f)
        return None
    if f < min_val or f > max_val:
        logger.warning("[SCHEMA] Out-of-range value at %s: %r not in [%s, %s]", label, f, min_val, max_val)
        return None
    return f
