from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EpistemicState(str, Enum):
    VERIFIED = "VERIFIED"
    PROBABILISTIC = "PROBABILISTIC"
    CONFLICTED = "CONFLICTED"
    DEGRADED = "DEGRADED"
    INVALIDATED = "INVALIDATED"


class SignalValue(BaseModel):
    metric: str = Field(description="The specific metric measured (e.g., net_flow)")
    direction: str | None = Field(None, description="Positive / Negative / Neutral")
    magnitude: float | None = None
    raw: dict[str, Any] | None = None


class TransformationStep(BaseModel):
    step: str = Field(description="Name of the transformation function (e.g., z_score_normalization)")
    params: dict[str, Any] = Field(default_factory=dict)


class SignalQuality(BaseModel):
    latency_ms: int | None = None
    freshness: float = Field(..., ge=0.0, le=1.0, description="Recency score (1.0 is immediate)")
    noise: float = Field(..., ge=0.0, le=1.0, description="Estimate of internal noise/variance")
    completeness: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="Ratio of available data points to expected ones (1.0 is perfect)",
    )
    stability: float = Field(0.5, ge=0.0, le=1.0, description="Statistical stability measure")


class SignalContext(BaseModel):
    market_regime: str = Field(description="Macro market phase (e.g., TRENDING, CRISIS)")
    liquidity_state: str = Field(description="Overall liquidity environment (e.g., EXPANDING, CONTRACTION)")
    volatility_regime: str | None = Field(None, description="Specific volatility regime")


class SignalProvenanceNode(BaseModel):
    node_id: str = Field(description="Unique ID for this signal instance.")
    signal_id: str = Field(description="Canonical identifier for the signal type (e.g., flow.bank.ACB)")
    engine: str = Field(description="The source engine that generated this node (e.g., capital_flow_engine).")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Time of node creation.")
    type: str = Field(description="Node type: RAW | DERIVED | COMPOSITE")

    value: SignalValue
    transformations: list[TransformationStep] = Field(default_factory=list)

    context: SignalContext
    quality: SignalQuality

    truth_status: EpistemicState = Field(
        default=EpistemicState.PROBABILISTIC, description="Current epistemic validity state of this signal node."
    )
    confidence_hint: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Initial confidence hint upon creation (prior to propagation)."
    )

    ttl_seconds: int | None = Field(
        default=None, description="Time-to-live for automatic node expiry in high-frequency graphs."
    )
