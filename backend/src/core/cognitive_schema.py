"""
cognitive_schema.py — Vietnamese Cognitive Schema Layer v1.0

Single source of truth for ALL English→Vietnamese UI mappings.
Kernel (engine) stays English. UI layer is 100% Vietnamese.

Architecture:
    Kernel (English) → cognitive_schema.py (mapping) → UI (Vietnamese)

Usage:
    from core.cognitive_schema import translate, drift_to_vi, DRIVER_VI, REGIME_VI

Rule:
    - engine/*.py        → uses English kernel terms
    - core/cognitive*.py → maps English → Vietnamese
    - api/*, frontend/   → receives only Vietnamese
"""

from typing import Optional

# ====================================================================
# 1. DRIFT — Cognitive Drift Mapping
# ====================================================================
# User-facing names for drift types (never show ETS_DRIFT etc to user)

DRIFT_SOURCE_VI: dict[str, str] = {
    "ETS_DRIFT": "sai lệch độ chính xác giải thích",
    "DOMINANCE_MISMATCH": "sai lệch lực dẫn dắt",
    "RISK_TONE_MISMATCH": "sai lệch nhận thức rủi ro",
    "FLOW_ROTATION_BLIND": "mù luân chuyển dòng tiền",
}

DRIFT_STATUS_VI: dict[str, str] = {
    "NONE": "không có",
    "LOW": "thấp",
    "MEDIUM": "trung bình",
    "HIGH": "cao",
}

# Flow rotation patterns → Vietnamese cognitive descriptions
FLOW_ROTATION_VI: dict[str, str] = {
    "broadsweep: flow+breadth both elevated": "dòng tiền đang lan tỏa ra nhiều nhóm cổ phiếu cùng lúc",
    "risk_off: flow concentrating into safe-haven (macro↑)": "dòng tiền đang rút khỏi tài sản rủi ro, chuyển sang nhóm phòng thủ",
    "concentration: flow↑ breadth↓ — money narrowing": "dòng tiền đang thu hẹp vào nhóm nhỏ cổ phiếu",
    "speculative_spread: breadth↑ flow↓ — money diffusing": "dòng tiền đang lan tỏa mang tính đầu cơ",
    "panic_flow: volatility↑ with flow↑ — possible capitulation": "dòng tiền đang tháo chạy mạnh, có dấu hiệu đầu hàng",
    "uncertainty: volatility dominating — no clear flow": "thị trường đang dao động mạnh, chưa xác định được dòng tiền",
    "safe_haven_rotation: macro↑ flow↓ — capital leaving equities": "dòng tiền đang chuyển sang tài sản an toàn, rút khỏi cổ phiếu",
}

DRIFT_GAP_TEMPLATES: dict[str, str] = {
    "DOMINANCE_MISMATCH": 'narrative cho rằng {explained} dẫn dắt nhưng thực tế {correct} đang chi phối',
    "RISK_TONE_MISMATCH": 'narrative đánh giá rủi ro trái ngược với tín hiệu hazard/thị trường',
    "FLOW_ROTATION_BLIND": 'narrative không nhận diện được luân chuyển dòng tiền: {rotation}',
}


# ====================================================================
# 2. REGIME — Market Regime Mapping
# ====================================================================

REGIME_VI: dict[str, str] = {
    "TRENDING": "có xu hướng rõ ràng",
    "RANGING": "đi ngang",
    "CRISIS": "rủi ro cao",
    "SIDEWAYS": "đi ngang",
    "BULL": "tăng",
    "BEAR": "giảm",
    "UNKNOWN": "không xác định",
}

REGIME_NOUN_VI: dict[str, str] = {
    "TRENDING": "xu hướng",
    "RANGING": "đi ngang",
    "CRISIS": "rủi ro",
    "UNKNOWN": "không xác định",
}

# Capitalized form for labels
REGIME_LABEL_VI: dict[str, str] = {
    "TRENDING": "Xu hướng rõ ràng",
    "RANGING": "Đi ngang",
    "CRISIS": "Khủng hoảng",
    "SIDEWAYS": "Đi ngang",
    "BULL": "Tăng",
    "BEAR": "Giảm",
    "UNKNOWN": "Không xác định",
}


# ====================================================================
# 3. DRIVER — Market Driver Mapping
# ====================================================================

DRIVER_VI: dict[str, str] = {
    "BREADTH": "Độ rộng thị trường",
    "FLOW": "Dòng tiền",
    "STRUCTURE": "Cấu trúc thị trường",
    "VOLATILITY": "Biến động",
    "MOMENTUM": "Đà tăng",
    "MACRO": "Yếu tố vĩ mô",
    "UNKNOWN": "Không xác định",
}

# Lowercase form for narrative text
DRIVER_VI_LOWER: dict[str, str] = {
    "BREADTH": "độ rộng thị trường",
    "FLOW": "dòng tiền",
    "STRUCTURE": "cấu trúc thị trường",
    "VOLATILITY": "biến động",
    "MOMENTUM": "đà tăng",
    "MACRO": "yếu tố vĩ mô",
    "UNKNOWN": "không xác định",
}


# ====================================================================
# 4. FLOW STATE — Capital Flow State Mapping
# ====================================================================

FLOW_STATE_VI: dict[str, str] = {
    "THU_HẸP": "Thu hẹp",
    "THU_HEP": "Thu hẹp",
    "MỞ_RỘNG": "Mở rộng",
    "MO_RONG": "Mở rộng",
    "BROAD_EXPANSION": "Mở rộng diện rộng",
    "NARROWING": "Thu hẹp",
    "CONCENTRATING": "Tập trung",
    "DISPERSING": "Phân tán",
    "ROTATING": "Luân chuyển",
    "DUY_TRÌ": "Duy trì",
    "PHÂN_HÓA": "Phân hóa",
    "UNKNOWN": "Không xác định",
}


# ====================================================================
# 5. CONVICTION / CONFIDENCE Mapping
# ====================================================================

CONVICTION_VI: dict[str, str] = {
    "VERY_HIGH": "Rất cao",
    "HIGH": "Cao",
    "MEDIUM": "Trung bình",
    "LOW": "Thấp",
    "VERY_LOW": "Rất thấp",
}


# ====================================================================
# 6. SECTOR — Industry Sector Mapping
# ====================================================================

SECTOR_VI: dict[str, str] = {
    "BANK": "Ngân hàng",
    "RE": "Bất động sản",
    "SEC": "Chứng khoán",
    "STEEL": "Thép",
    "OIL": "Dầu khí",
    "TRANS": "Vận tải",
    "CONSUMER": "Tiêu dùng",
    "TECH": "Công nghệ",
    "UTILITY": "Tiện ích",
    "CONST": "Xây dựng",
    "FOOD": "Thực phẩm",
    "OTHER": "Khác",
}


# ====================================================================
# 7. FIELD LABELS — UI Field Label Mapping
# ====================================================================

FIELD_LABELS: dict[str, str] = {
    # Market
    "regime": "Trạng thái thị trường",
    "market_state": "Trạng thái thị trường",
    "market_regime": "Trạng thái thị trường",
    "status_vi": "Diễn giải",
    "active_model": "Model hoạt động",
    "consensus": "Đồng thuận",
    # Flow
    "flow_state": "Trạng thái dòng tiền",
    "flow_velocity": "Tốc độ dòng tiền",
    "rotation_velocity": "Tốc độ luân chuyển",
    "flow_dispersion": "Độ phân tán dòng tiền",
    "classification": "Phân loại dòng tiền",
    "displacement_conviction": "Độ tin cậy dịch chuyển vốn",
    "leading_sectors": "Ngành dẫn dắt",
    "lagging_sectors": "Ngành tụt lại",
    "sector_share": "Phân bổ dòng tiền theo ngành",
    "sector_performance": "Hiệu suất ngành hôm nay",
    # Breadth
    "health_score": "Điểm sức khỏe thị trường",
    "advancers": "Số mã tăng",
    "decliners": "Số mã giảm",
    "unchanged": "Số mã đứng giá",
    "total_active": "Tổng mã hoạt động",
    "nh10_count": "Số mã phá MA10",
    # Capital Displacement
    "classification_capital": "Phân loại dịch chuyển vốn",
    "conviction": "Độ tin cậy",
    "top1_symbol": "Cổ phiếu tập trung lớn nhất",
    "top1_concentration": "Mức tập trung Top1",
    "top3_concentration": "Mức tập trung Top3",
    "sector_breadth": "Độ rộng ngành",
    # Macro
    "gold": "Vàng",
    "trust": "Tín nhiệm CAO",
    "data_quality": "Chất lượng dữ liệu",
    # API
    "api_routes": "API",
    "api_contract": "Hợp đồng API",
    "api_contract_hash": "Mã băm hợp đồng API",
    "semantic_contract_hash": "Mã băm hợp đồng ngữ nghĩa",
    # System
    "snapshot": "Bản ghi trạng thái",
    "routes": "Endpoint",
    "hash": "Mã băm",
    "git_commit": "Commit git",
    "timestamp": "Thời gian",
    "weekly_report": "Báo cáo tuần",
    # RSI
    "bull_count": "Số mã tăng (RSI)",
    "bear_count": "Số mã giảm (RSI)",
    "total_scanned": "Tổng đã quét",
    "habitat_distribution": "Phân bố môi trường RSI",
    # CAGL
    "prefix_dup": "Tiền tố trùng lặp",
    "phantom": "Endpoint ảo",
    "missing": "Thiếu endpoint",
    "unregistered": "Chưa đăng ký",
    "undocumented": "Không có tài liệu",
    "prefix_mismatch": "Sai lệch tiền tố",
}


# ====================================================================
# 8. STATUS / SEVERITY Mapping
# ====================================================================

STATUS_VI: dict[str, str] = {
    "consistent": "ổn định",
    "stable": "ổn định",
    "drift": "biến động",
    "match": "khớp",
    "mismatch": "không khớp",
    "unchanged": "không thay đổi",
    "changed": "có thay đổi",
    "valid": "hợp lệ",
    "invalid": "không hợp lệ",
    "passed": "đạt",
    "failed": "không đạt",
    "healthy": "bình thường",
    "degraded": "suy giảm",
}

SEVERITY_LABEL_VI: dict[str, str] = {
    "clean": "ổn định",
    "mild_noise": "nhiễu nhẹ",
    "degraded": "suy giảm",
    "caution": "thận trọng",
    "unstable": "không ổn định",
    "caution_severe": "cảnh báo cao",
    "block_promotion": "chặn kích hoạt",
}

# Overall report severity labels
OVERALL_SEVERITY_VI: list[tuple[float, str]] = [
    (0.15, "ổn định"),
    (0.35, "thận trọng"),
    (0.55, "cảnh báo nhẹ"),
    (0.75, "cảnh báo"),
    (1.01, "rủi ro cao"),
]


# ====================================================================
# 9. NARRATIVE KEYWORDS (bidirectional: VN → EN for validator)
# ====================================================================
# Must match DRIVER_VI_LOWER for consistency

VI_KEYWORDS: dict[str, str] = {
    "dòng tiền": "FLOW",
    "độ rộng": "BREADTH",
    "cấu trúc": "STRUCTURE",
    "biến động": "VOLATILITY",
    "đà tăng": "MOMENTUM",
    "vĩ mô": "MACRO",
}

VI_PHRASES: dict[str, str] = {
    "dòng tiền đang dẫn dắt": "FLOW",
    "dòng tiền đang chi phối": "FLOW",
    "dòng tiền đang vượt trội": "FLOW",
    "độ rộng thị trường đang dẫn dắt": "BREADTH",
    "độ rộng thị trường đang chi phối": "BREADTH",
    "độ rộng thị trường đang vượt trội": "BREADTH",
    "cấu trúc thị trường đang dẫn dắt": "STRUCTURE",
    "cấu trúc thị trường đang chi phối": "STRUCTURE",
    "cấu trúc thị trường đang vượt trội": "STRUCTURE",
    "biến động đang dẫn dắt": "VOLATILITY",
    "biến động đang chi phối": "VOLATILITY",
    "đà tăng đang dẫn dắt": "MOMENTUM",
    "đà tăng đang chi phối": "MOMENTUM",
    "yếu tố vĩ mô đang dẫn dắt": "MACRO",
    "yếu tố vĩ mô đang chi phối": "MACRO",
}

# Risk/safety keywords for narrative tone analysis
RISK_KEYWORDS: list[str] = ["rủi ro", "thận trọng", "bảo toàn", "suy yếu", "hỗn loạn", "dao động"]
SAFETY_KEYWORDS: list[str] = ["ổn định", "thoải mái", "tăng trưởng", "rõ ràng", "bám xu hướng", "tín hiệu"]


# ====================================================================
# 10. HELPERS — Mapping Functions
# ====================================================================


def translate(term: str, mapping: Optional[dict[str, str]] = None) -> str:
    """Translate a single English term to Vietnamese using given or default mappings.

    Tries DRIFT_SOURCE_VI → DRIFT_STATUS_VI → REGIME_VI → STATUS_VI → FIELD_LABELS → original.
    """
    if mapping is not None:
        return mapping.get(term, term)

    for m in (DRIFT_SOURCE_VI, DRIFT_STATUS_VI, FLOW_ROTATION_VI,
              REGIME_VI, STATUS_VI, FIELD_LABELS):
        if term in m:
            return m[term]
    return term


def field_label(field: str) -> str:
    """Get Vietnamese label for a field name."""
    return FIELD_LABELS.get(field, field.replace("_", " ").title())


def overall_severity_label(severity: float) -> str:
    """Map numeric severity to Vietnamese label."""
    for threshold, label in OVERALL_SEVERITY_VI:
        if severity < threshold:
            return label
    return "rủi ro cao"


def status_text(is_stable: bool) -> str:
    return "ổn định" if is_stable else "có biến động"


def status_icon(is_stable: bool) -> str:
    return "+" if is_stable else "x"


# ====================================================================
# 11. DRIFT LAYER — Kernel → Vietnamese UI Schema
# ====================================================================


def drift_source_to_vi(source: str) -> str:
    """Map a kernel drift source to Vietnamese UI name."""
    return DRIFT_SOURCE_VI.get(source, source)


def drift_status_to_vi(status: str) -> str:
    """Map a kernel drift status to Vietnamese UI level."""
    return DRIFT_STATUS_VI.get(status, status)


def flow_rotation_to_vi(rotation: Optional[str]) -> Optional[str]:
    """Map a kernel flow rotation pattern to Vietnamese description.

    Returns None if rotation is None or unmapped.
    """
    if rotation is None:
        return None
    return FLOW_ROTATION_VI.get(rotation, rotation)


def drift_to_vi(drift_result: dict) -> dict:
    """Transform kernel drift_prevention.assess_drift() output → pure Vietnamese UI schema.

    Input (kernel English):
        {
            "drift_score": 0.25,
            "drift_status": "LOW",
            "drift_sources": ["ETS_DRIFT"],
            "narrative_truth_gap": "...",
            "flow_rotation": "broadsweep: flow+breadth both elevated",
            "risk_tone_mismatch": False,
        }

    Output (UI Vietnamese):
        {
            "đánh_giá_drift": {
                "mức_độ_drift": "thấp",
                "nguồn_drift": ["sai lệch độ chính xác giải thích"],
                "khoảng_cách_nhận_thức": "...",
                "luân_chuyển_dòng_tiền": "dòng tiền đang lan tỏa ra nhiều nhóm cổ phiếu cùng lúc",
                "sai_lệch_rủi_ro": False,
            }
        }
    """
    raw = drift_result or {}

    sources_vi = [drift_source_to_vi(s) for s in raw.get("drift_sources", [])]
    rotation_vi = flow_rotation_to_vi(raw.get("flow_rotation"))
    gap = raw.get("narrative_truth_gap")
    risk_mismatch = raw.get("risk_tone_mismatch", False)
    status_vi = drift_status_to_vi(raw.get("drift_status", "NONE"))

    return {
        "đánh_giá_drift": {
            "mức_độ_drift": status_vi,
            "nguồn_drift": sources_vi,
            "khoảng_cách_nhận_thức": gap,
            "luân_chuyển_dòng_tiền": rotation_vi,
            "sai_lệch_rủi_ro": risk_mismatch,
        }
    }


def driver_to_vi(driver_key: str, lower: bool = False) -> str:
    """Map kernel driver key to Vietnamese."""
    m = DRIVER_VI_LOWER if lower else DRIVER_VI
    return m.get(driver_key, driver_key)


def regime_to_vi(regime: str, form: str = "label") -> str:
    """Map kernel regime to Vietnamese.

    Args:
        regime: Kernel regime key (e.g. "TRENDING")
        form: "label" (capitalized), "verb" (lowercase), "noun" (noun form)
    """
    if form == "label":
        return REGIME_LABEL_VI.get(regime, regime)
    elif form == "noun":
        return REGIME_NOUN_VI.get(regime, regime)
    return REGIME_VI.get(regime, regime)


def conviction_to_vi(level: str) -> str:
    """Map conviction level to Vietnamese."""
    return CONVICTION_VI.get(level, level)


def sector_to_vi(code: str) -> str:
    """Map sector code to Vietnamese."""
    return SECTOR_VI.get(code, code)


def flow_state_to_vi(state: str) -> str:
    """Map flow state to Vietnamese."""
    return FLOW_STATE_VI.get(state, state)


# ====================================================================
# 12. ETS — Explain Truth Score Mapping
# ====================================================================

ETS_LABEL_VI: list[tuple[float, str]] = [
    (0.3, "thấp"),
    (0.5, "trung bình"),
    (0.7, "khá"),
    (1.01, "cao"),
]

ETS_STATUS_VI: dict[str, str] = {
    "aligned": "khớp",
    "misaligned": "lệch",
}


def ets_to_vi(score: float) -> str:
    """Map ETS numeric score to Vietnamese label."""
    for threshold, label in ETS_LABEL_VI:
        if score < threshold:
            return label
    return "cao"


# ====================================================================
# 13. EARLY WARNING — Temporal Stability Mapping
# ====================================================================

EARLY_WARNING_VI: dict[str, str] = {
    "clean": "an toàn",
    "caution": "thận trọng",
    "warning": "cảnh báo",
}

DRIFT_TREND_VI: dict[str, str] = {
    "stable": "ổn định",
    "rising": "đang tăng",
    "falling": "đang giảm",
}


def early_warning_to_vi(signal: str) -> str:
    """Map early warning signal to Vietnamese label."""
    return EARLY_WARNING_VI.get(signal, signal)


def drift_trend_to_vi(trend: str) -> str:
    """Map drift trend to Vietnamese label."""
    return DRIFT_TREND_VI.get(trend, trend)
