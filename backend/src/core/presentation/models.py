from typing import Literal, Optional
from pydantic import BaseModel, Field
from enum import Enum


ActionBias = Literal["BUY", "SELL", "HOLD", "REDUCE_RISK", "WAIT"]
TimeHorizon = Literal["NGẮN_HẠN", "TRUNG_HẠN", "DÀI_HẠN"]
RecommendedPosture = Literal["TĂNG_TỶ_TRỌNG", "GIỮ_VỊ_THẾ", "GIẢM_RỦI_RO", "QUAN_SÁT"]
RiskLevel = Literal["CAO", "TRUNG_BINH", "THẤP"]
EntropyState = Literal["HỘI_TỤ", "PHÂN_KỲ", "NHIỄU_CAO", "MẤT_ỔN_ĐỊNH"]
SymbolAction = Literal["MUA", "NẮM_GIỮ", "GIẢM", "TRÁNH"]
TradeStateLevel = Literal["PROHIBITED", "RESTRICTED", "SELECTIVE", "ACTIVE", "AGGRESSIVE"]
SSILevel = Literal["HIGH", "MEDIUM", "LOW"]
AssetBias = Literal["STRONG_PREFER", "PREFER", "NEUTRAL", "AVOID", "STRONG_AVOID"]


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


class TradeStatePolicy(BaseModel):
    level: TradeStateLevel = Field(description="Trạng thái giao dịch tổng quát")
    label_vi: str = Field(description="Nhãn tiếng Việt cho UI")
    color: str = Field(description="Màu UI (green/yellow/orange/red)")
    score: float = Field(ge=0.0, le=1.0, description="Điểm tổng hợp 0-1")
    max_exposure_pct: float = Field(ge=0.0, le=100.0, description="Tỷ lệ danh mục tối đa (%)")
    max_position_pct: float = Field(ge=0.0, le=100.0, description="Tỷ trọng tối đa một vị thế (%)")
    allowed_actions: list[str] = Field(description="Các hành động được phép")
    require_confirmation: bool = Field(description="Có cần xác nhận tín hiệu trước khi hành động?")
    narrative_vi: str = Field(description="Giải thích ngắn bằng tiếng Việt")
    action_rule_vi: str = Field(description="Nguyên tắc hành động ngắn gọn")
    veto_active: bool = Field(default=False, description="Có veto đang kích hoạt hay không")
    veto_reason_vi: str | None = Field(default=None, description="Lý do veto nếu có")


class AxisScore(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="Điểm trục 0-1")
    label_vi: str = Field(description="Nhãn tiếng Việt")
    detail_vi: str = Field(description="Giải thích ngắn cho điểm này")


class SSIReport(BaseModel):
    level: SSILevel = Field(description="SSI tổng quát")
    score: float = Field(ge=0.0, le=1.0, description="Điểm SSI 0-1")
    label_vi: str = Field(description="Nhãn tiếng Việt")
    color: str = Field(description="Màu UI")
    summary_vi: str = Field(description="Tổng quan độ ổn định")
    state_consistency: AxisScore = Field(description="Trục 1: nhất quán nội tại")
    breadth_confirmation: AxisScore = Field(description="Trục 2: độ lan tỏa")
    drift_alignment: AxisScore = Field(description="Trục 3: khớp drift")
    flow_stability: AxisScore = Field(description="Trục 4: ổn định dòng tiền")


class AssetBiasEntry(BaseModel):
    asset_class: str = Field(description="Loại tài sản (e.g. Large-cap, Mid-cap, Gold, Cash)")
    bias: AssetBias = Field(description="Xu hướng hành vi trong state hiện tại")
    label_vi: str = Field(description="Nhãn tiếng Việt (e.g. Ưu tiên, Trung tính, Tránh)")
    rationale_vi: str = Field(description="Lý do ngắn — tại sao bias này xuất hiện trong state hiện tại")


class AssetPreferenceMap(BaseModel):
    dominant_bias_vi: str = Field(description="Mô tả tổng quan xu hướng tài sản trong state này")
    entries: list[AssetBiasEntry] = Field(description="Ánh xạ chi tiết từng loại tài sản")
    note_vi: str = Field(description="Cảnh báo: đây là bias xác suất, không phải khuyến nghị")


class ActionConstraint(BaseModel):
    allowed_order_types: list[str] = Field(description="Các loại lệnh được phép (MARKET, LIMIT, STOP, ...)")
    forbidden_patterns: list[str] = Field(description="Các mẫu hình giao dịch bị cấm")
    position_sizing_rule: str = Field(description="Phương pháp xác định khối lượng (FIXED_PCT, KELLY, EQUAL_WEIGHT)")
    confirmation_sources: list[str] = Field(description="Các nguồn xác nhận bắt buộc")
    max_daily_trades: int = Field(description="Số lệnh tối đa mỗi ngày")
    cooldown_bars: int = Field(description="Số nến chờ tối thiểu giữa các lệnh")
    allow_short: bool = Field(description="Cho phép bán khống?")
    allow_margin: bool = Field(description="Cho phép sử dụng margin?")
    sector_concentration_limit: float = Field(ge=0.0, le=1.0, description="Giới hạn tỷ trọng tối đa một ngành (0-1)")
    label_vi: str = Field(description="Mô tả ngắn chính sách hành động")


class VerdictFlowTransparency(BaseModel):
    trang_thai: str
    flow_score: float
    flow_momentum: float
    thanh_khoan: float
    ap_suat_thanh_khoan: str
    kiem_toan: str
    nhap_xu_theo_ma: str
    tier: str


class VerdictPriceTransparency(BaseModel):
    cau_truc: str
    bien_dong: str
    ssi_rating: str
    sentinel_rating: str


class VerdictReason(BaseModel):
    vi_sao_dau_tu: str
    ly_do_tu_chao: list[str]


class VerdictHistoryFuture(BaseModel):
    lich_su_so_sanh: str
    xu_huong_tuong_lai: str


class InvestmentVerdict(BaseModel):
    symbol: str
    timestamp: str
    phanduyet_chinh: str
    nhan_hanh_dong: str
    cho_phep_giao_dich: bool
    so_luong_phu_quyet: int
    so_luong_canh_bao: int
    minh_bach_dong_tien: VerdictFlowTransparency
    minh_bach_gia_ca: VerdictPriceTransparency
    ly_do_hanh_dong: VerdictReason
    canh_bao_kem: list[str]
    doi_chieu_lich_su_va_tuong_lai: VerdictHistoryFuture


class VerdictSummary(BaseModel):
    timestamp: str
    tong_so_ma: int
    so_ma_cho_phep: int
    so_ma_tu_choi: int
    phanduyet: list[InvestmentVerdict]


DCLVerdictLevel = Literal["ACTIONABLE", "OBSERVE", "NO_TRADE"]
MarketIntentMode = Literal["ACCUMULATION", "WAITING", "DISTRIBUTION", "PRESERVATION"]
GateReasonCode = Literal[
    "GATE_SENTINEL_PASS", "GATE_SENTINEL_FAIL",
    "GATE_FLOW_PASS", "GATE_FLOW_FAIL",
    "GATE_BREADTH_PASS", "GATE_BREADTH_FAIL",
    "GATE_STRUCTURE_PASS", "GATE_STRUCTURE_FAIL",
    "GATE_SSI_PASS", "GATE_SSI_FAIL",
    "GATE_TRADE_STATE_PASS", "GATE_TRADE_STATE_FAIL",
]
CompCode = Literal[
    "COMP_SENTINEL_BY_FLOW_SSI", "COMP_SENTINEL_BY_FLOW_BREADTH",
    "COMP_BREADTH_BY_SENTINEL_FLOW", "COMP_BREADTH_BY_SENTINEL_SSI",
    "COMP_SSI_BY_FLOW_BREADTH", "COMP_SSI_BY_SENTINEL_TRADE_STATE",
    "COMP_FLOW_BY_SENTINEL_SSI", "COMP_FLOW_BY_BREADTH_TRADE_STATE",
    "COMP_STRUCTURE_BY_SENTINEL_FLOW_BREADTH",
    "COMP_TRADE_STATE_BY_SENTINEL_FLOW_SSI",
]


class GateScore(BaseModel):
    name: str = Field(description="Gate code (sentinel, flow, breadth, structure, ssi, trade_state)")
    weight: int = Field(ge=1, le=10, description="Gate weight (1-10)")
    raw_pass: bool = Field(description="Raw PASS/FAIL before compensation")
    raw_score: float = Field(ge=0.0, le=1.0, description="Raw score 0-1")
    compensation_applied: float = Field(default=0.0, ge=0.0, le=1.0, description="Compensation boost received")
    effective_score: float = Field(ge=0.0, le=1.0, description="Post-compensation score = min(1, raw + compensation)")
    reason_code: GateReasonCode = Field(description="Code identifying the gate status reason")
    reason_params: dict = Field(default_factory=dict, description="Parameters for reason template rendering")
    comp_code: CompCode | None = Field(default=None, description="Compensation reason code if override active")


class CompensationApplied(BaseModel):
    target_gate: str = Field(description="Gate being overridden")
    compensated_by: list[str] = Field(description="Compensating signal codes")
    override_weight: float = Field(ge=0.0, le=1.0, description="Override weight 0-1")
    comp_code: CompCode = Field(description="Compensation rule code for localization")


class DCLReport(BaseModel):
    verdict: DCLVerdictLevel = Field(description="Final DCL verdict code")
    score: float = Field(ge=0.0, le=1.0, description="Composite DCL score 0-1")
    gates: dict[str, GateScore] = Field(description="Per-gate evaluation results")
    compensations_applied: list[CompensationApplied] = Field(description="Active overrides")
    compensations_possible: int = Field(description="Total compensation rules available")
    compensations_triggered: int = Field(description="Number of overrides triggered")
    market_intent: MarketIntentMode = Field(description="Market intent mode code")


DirectionalBiasCode = Literal["BULLISH", "NEUTRAL", "BEARISH", "TRANSITIONAL", "FRACTURED"]


class BiasDriver(BaseModel):
    source: str = Field(description="Source name (FLOW, BREADTH, STRUCTURE, REGIME, SSI, TRADE_STATE)")
    impact: float = Field(description="Signed impact on bias score (-1 to 1)")


class DirectionalBiasReport(BaseModel):
    bias_code: DirectionalBiasCode = Field(description="Final directional bias classification")
    bias_strength: float = Field(ge=0.0, le=1.0, description="Composite bias score 0-1")
    bias_confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the bias assessment")
    bias_drivers: list[BiasDriver] = Field(description="Per-source contribution breakdown")
    dominant_force: str = Field(description="Source with highest absolute impact")
    label_vi: str = Field(default="", description="Vietnamese label for UI")
    color: str = Field(default="", description="UI color token")
    summary_vi: str = Field(default="", description="Vietnamese summary of bias state")


TrendQualityCode = Literal["PERSISTENT", "TRANSITIONAL", "FLICKERING"]
FlickerRiskCode = Literal["LOW", "MEDIUM", "HIGH"]


class DirectionPersistenceReport(BaseModel):
    trend_quality_code: TrendQualityCode = Field(description="Temporal stability of DBE direction over window")
    flicker_risk_code: FlickerRiskCode = Field(description="Risk that current DBE is noise rather than signal")
    dbe_stability_score: float = Field(ge=0.0, le=1.0, description="Composite stability score 0-1")
    windows_available: int = Field(description="Number of DBE snapshots in current window")
    label_vi: str = Field(default="", description="Vietnamese label for UI")
    color: str = Field(default="", description="UI color token")
    summary_vi: str = Field(default="", description="Vietnamese explanation of persistence")


TransitionStateCode = Literal["STABLE", "BREWING", "TRIGGERED"]
TransitionTypeCode = Literal[
    "NONE", "FLICKER_TO_TREND", "TREND_TO_FLICKER",
    "REGIME_SHIFT", "BIAS_FLIP",
]


class CausalFactor(BaseModel):
    source: str = Field(description="Source driver name, e.g. FLOW, BREADTH, STRUCTURE, REGIME, SSI, TRADE_STATE")
    delta: float = Field(description="Delta impact of this driver over the transition window")
    direction: int = Field(description="Direction of change: 1 (support) or -1 (pressure/drag)")
    contribution_pct: float = Field(description="Attribution percentage weight")


class CausalAttributionReport(BaseModel):
    transition_type: str = Field(description="Type of transition, matching TransitionTypeCode")
    primary_cause: str = Field(description="The source with largest absolute impact delta")
    factors: list[CausalFactor] = Field(default_factory=list, description="List of contributors ordered by absolute delta DESC")
    summary_vi: str = Field(description="Vietnamese synthesis explanation of the transition causes")


class TransitionTriggerReport(BaseModel):
    transition_state: TransitionStateCode = Field(description="Current transition alert level")
    transition_type: TransitionTypeCode = Field(description="Type of transition most recently detected")
    trigger_confidence: float = Field(ge=0.0, le=1.0, description="Confidence that transition is real (not noise)")
    transitions_24h: int = Field(default=0, description="Number of transitions detected in recent window")
    label_vi: str = Field(default="", description="Vietnamese label for UI")
    color: str = Field(default="", description="UI color token")
    summary_vi: str = Field(default="", description="Vietnamese explanation of transition state")
    causal_attribution: Optional[CausalAttributionReport] = Field(default=None, description="Detailed causal analysis if transition is triggered")




