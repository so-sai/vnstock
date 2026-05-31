from typing import Literal, Optional
from pydantic import BaseModel, Field
from enum import Enum


ActionBias = Literal["BUY", "SELL", "HOLD", "REDUCE_RISK", "WAIT"]
TimeHorizon = Literal["NGẮN_HẠN", "TRUNG_HẠN", "DÀI_HẠN"]
RecommendedPosture = Literal["TĂNG_TỶ_TRỌNG", "GIỮ_VỊ_THẾ", "GIẢM_RỦI_RO", "QUAN_SÁT"]
RiskLevel = Literal["CAO", "TRUNG_BINH", "THẤP"]
EntropyState = Literal["HỘI_TỤ", "PHÂN_KỲ", "NHIỄU_CAO", "MẤT_ỔN_ĐỊNH"]
SymbolAction = Literal["MUA", "NẮM_GIỮ", "GIẢM", "TRÁNH"]


class PresentationLabel(BaseModel):
    label_vi: str = Field(description="Vietnamese label for UI rendering")
    color: str = Field(description="UI color token (e.g. green, red, orange)")
    icon: str | None = Field(None, description="Icon name for UI toolkit")
    priority: int = Field(description="Ordering weight: higher = more alarming")
    severity_score: float = Field(default=0.5, ge=0.0, le=1.0, description="Alert/heatmap intensity (0=low, 1=critical)")
    action_bias: ActionBias = Field(description="Default action posture for this state")


class NarrativeTemplate(BaseModel):
    template_id: str = Field(description="Unique identifier for the template")
    conditions: dict[str, list[str]] = Field(
        description="Structured matching conditions, e.g. {'risk_state': ['HIGH_STRESS'], 'truth_status': ['CONFLICTED']}"
    )
    template_vi: str = Field(description="Render string with {placeholder} substitution")
    priority: int = Field(default=1, description="Match priority (higher = preferred)")
    time_horizon: TimeHorizon = Field(default="NGẮN_HẠN", description="Applicable time horizon")
    required_context: list[str] = Field(default_factory=list, description="Fields that must be present for this template")


class DecisionView(BaseModel):
    recommended_posture: RecommendedPosture
    risk_level: RiskLevel
    risk_color: str = Field(description="Derived UI color from dominant risk state")
    dominant_signal_vi: str
    signal_alignment: float = Field(ge=0.0, le=1.0, description="Cross-engine convergence (0=dispersed, 1=fully aligned)")
    confidence_summary_vi: str = Field(description="Trust summary in Vietnamese")
    primary_conflict_vi: str | None = Field(None, description="Single most important contradiction in natural VN")
    short_explanation_vi: str = Field(description="One-line decision rationale")
    time_horizon: TimeHorizon = Field(default="NGẮN_HẠN")
    decision_urgency: float = Field(default=0.5, ge=0.0, le=1.0, description="Urgency for execution/alert (0=observe, 1=act now)")
    entropy_state: EntropyState = Field(default="HỘI_TỤ", description="Aggregate epistemic entropy level")


class SymbolDecisionView(BaseModel):
    symbol: str = Field(description="Mã cổ phiếu")
    action: SymbolAction = Field(description="Hành động đề xuất cho mã này")
    conviction: float = Field(ge=0.0, le=1.0, description="Mức độ tin cậy (0-1, projection từ conviction gốc)")
    rationale_vi: str = Field(description="Luận điểm ngắn cho mã này")
    sector: str = Field(description="Ngành")
    tier: str = Field(description="Phân hạng: core / rotation / opportunity")


class OpportunityView(BaseModel):
    posture_alignment_note_vi: str = Field(description="Market posture → cơ hội alignment")
    top_picks: list[SymbolDecisionView] = Field(description="Cơ hội nổi bật hôm nay (không phải danh mục thật)")
    watchlist: list[SymbolDecisionView] = Field(description="Danh sách theo dõi")
    sector_allocation_hint_vi: str = Field(description="Gợi ý phân bổ theo ngành")
    risk_notes_vi: str = Field(description="Lưu ý rủi ro cho cơ hội")
    capital_allocation_hint_vi: str = Field(description="Gợi ý tỷ lệ phân bổ vốn")
