"""
canonical_output_adapter.py — Final Output Gate Contract v1.0

SINGLE EXIT RULE:
    ALL output MUST pass through localize_output() BEFORE reaching the API.
    NO RAW ENGLISH TOKEN IN FINAL REPORT.

Architecture:
    Engine (English) → cognitive_schema (mapping) → adapter (gate) → API (Vietnamese)

Usage:
    from core.canonical_output_adapter import localize_output
    return localize_output(build_weekly_report())

Contract:
    1. Every string value is checked against the master EN→VI mapping
    2. Exact-match enum values (TRENDING, CRISIS, PROMOTABLE, ...) → always translated
    3. UPPERCASE tokens embedded in narrative text → replaced via word-boundary regex
    4. Unknown values → pass through unchanged
    5. Non-string values → pass through unchanged
"""
from __future__ import annotations
import re
from typing import Any

from .cognitive_schema import (
    REGIME_VI, REGIME_LABEL_VI, REGIME_NOUN_VI,
    DRIVER_VI, DRIVER_VI_LOWER,
    DRIFT_SOURCE_VI, DRIFT_STATUS_VI,
    FLOW_STATE_VI, FLOW_ROTATION_VI,
    CONVICTION_VI, SECTOR_VI,
    STATUS_VI, SEVERITY_LABEL_VI,
    FIELD_LABELS,
    EARLY_WARNING_VI, DRIFT_TREND_VI,
    ETS_STATUS_VI,
)


# ====================================================================
# MASTER MAPPING — aggregate ALL known EN→VI pairs from the system
# ====================================================================

_EXTRA_MAP: dict[str, str] = {
    # Trust / CAO
    "PROMOTABLE": "Có thể kích hoạt",
    "BLOCKED": "Bị chặn",
    "NO_DATA": "Không có dữ liệu",
    # Gold regime
    "BULLISH": "Tăng",
    "BEARISH": "Giảm",
    # Premium regime
    "NORMAL": "Bình thường",
    "SURGE": "Tăng nóng",
    "DANGER": "Nguy hiểm",
    "DISCOUNT": "Chiết khấu",
    # General
    "NEUTRAL": "Trung tính",
    "live": "trực tiếp",
    "UNKNOWN": "Không xác định",
    # LCI / Flow
    "EXPANDING": "Mở rộng",
    "CONTRACTING": "Thu hẹp",
    "HEALTHY_ROTATION": "Luân chuyển lành mạnh",
    "DIVERGENT": "Phân kỳ",
    "HIGH_BREAKOUT_ACTIVITY": "Nhiều phá vỡ",
    "MODERATE_BREAKOUT_ACTIVITY": "Phá vỡ vừa phải",
    "LOW_BREAKOUT_ACTIVITY": "Ít phá vỡ",
    # Risk / Appetite
    "RISK_ON": "Chấp nhận rủi ro",
    "RISK_OFF": "Phòng thủ",
    # Constraint
    "ALLOWED": "Được phép",
    "PARTIAL": "Một phần",
    "BLOCKED": "Bị chặn",
    # Opportunity bias
    "BULLISH_BIAS": "Thiên hướng tăng",
    "BEARISH_BIAS": "Thiên hướng giảm",
    "DEFENSIVE": "Phòng thủ",
    # Market phase
    "ACCUMULATION": "Tích lũy",
    "DISTRIBUTION": "Phân phối",
    "MARKUP": "Tăng giá",
    "MARKDOWN": "Giảm giá",
    "WAITING": "Chờ đợi",
    "PRESERVATION": "Bảo toàn",
    # System
    "ok": "tốt",
    "ACTIVE": "Hoạt động",
    "INACTIVE": "Không hoạt động",
    # ACCELERATION patterns (used in scanner output)
    "ACCELERATION": "Tăng tốc",
    "DECELERATION": "Chậm lại",
    "CONTINUATION": "Tiếp diễn",
    "STABLE": "Ổn định",
    # Additional cognitive
    "NONE": "Không",
    "aligned": "khớp",
    "misaligned": "lệch",
    # Regime tags (lowercase, from reputation ledger)
    "trending": "Xu hướng rõ",
    "ranging": "Đi ngang",
    "crisis": "Khủng hoảng",
    # Driver keys (reputation ledger output, matching cognitive_schema)
    "VOLATILITY": "Biến động",

    # ============================================================
    # DECISION ACTIONS — mapped from decision_tensor
    # ============================================================
    "HOLD": "Giữ",
    "BUY": "Mua",
    "SELL": "Bán",
    "STRONG_BUY": "Mua mạnh",
    "STRONG_SELL": "Bán mạnh",
    "ENTER": "Mở vị thế",
    "EXIT": "Thoát",
    "REDUCE": "Giảm",
    "STAND_DOWN": "Đứng ngoài",
    "WATCH": "Theo dõi",
    "OBSERVE": "Quan sát",

    # ============================================================
    # RISK STATES — mapped from decision_tensor risk_state
    # ============================================================
    "SAFE": "An toàn",
    "CAUTION": "Thận trọng",
    "STRESS": "Căng thẳng",
    "LOCKED": "Khóa",

    # ============================================================
    # COACH TONE — mapped from portfolio coach tone
    # ============================================================
    "WARNING": "Cảnh báo",
    "CRITICAL": "Nghiêm trọng",
    "OPPORTUNITY": "Cơ hội",
    "BALANCED": "Cân bằng",

    # ============================================================
    # MARKET PRESSURE — mapped from foreign flow
    # ============================================================
    "ACCUMULATING": "Tích lũy",
    "DISTRIBUTING": "Phân phối",

    # ============================================================
    # RETAIL CHASE LABELS — mapped from liquidity wave
    # ============================================================
    "EXTREME": "Cực đoan",
    "MODERATE": "Vừa phải",

    # ============================================================
    # REPLAY EVENT CODES — mapped from replay timeline
    # ============================================================
    "ATR_SHOCK": "Sốc biến động",
    "BREADTH_COLLAPSE": "Sụp đổ độ rộng",
    "LOW_PARTICIPATION": "Tham gia thấp",
    "REGIME_CRISIS": "Khủng hoảng thị trường",
    "REGIME_FLIP": "Đảo chiều chế độ",
    "RECOVERY_FIRE": "Kích hoạt phục hồi",
    "BLOCKED": "Bị chặn",
    "PICK": "Chọn",
    "PRIMARY_MA200": "MA200 chính",
    "STRONG_TREND": "Xu hướng mạnh",
    "Pullback recovery setup": "Thiết lập phục hồi sau điều chỉnh",
    "Momentum continuation": "Tiếp diễn đà tăng",
    "Recovery detector activated": "Bộ phát hiện phục hồi đã kích hoạt",

    # ============================================================
    # OPPORTUNITY REASON STRINGS — mapped from scanner
    # ============================================================
    "volume confirmed": "xác nhận khối lượng",
    "strong retail chase": "dòng tiền bán lẻ mạnh",
    "moderate inflow": "dòng tiền vào vừa phải",
    "high continuation probability": "xác suất tiếp diễn cao",
    "Monitoring": "Đang theo dõi",

    # ============================================================
    # ============================================================
    # FLOW MAP (CrossMarketFlowMap) — Bản đồ dòng vốn 4 tầng
    # ============================================================
    "CASH_SHELTER": "Hầm trú ẩn tiền mặt",
    "HARD_ASSET_SHELTER": "Hầm trú ẩn tài sản cứng",
    "EQUITY_EXPANSION": "Bung xõa cổ phiếu",
    "TRANSITION_STATE": "Luân chuyển ngầm",
    "HIGH_CONFIDENCE": "Độ tin cậy cao",
    "LOW_CONFIDENCE_MACRO_VN": "Độ tin cậy vĩ mô trong nước thấp",
    "LATE-CYCLE OBSERVABILITY GAP: ADX spike trong regime RANGING": "Khoảng cách quan sát cuối chu kỳ: ADX tăng đột biến trong khi regime vẫn ở trạng thái đi ngang",

    # ============================================================
    # MIXED-CASE FIXES — catch non-uppercase variants
    # ============================================================
    "Unknown": "Không xác định",
    "NOT_FOUND": "Không tìm thấy",
}


def _build_master_map() -> dict[str, str]:
    merged: dict[str, str] = {}
    sources = [
        REGIME_LABEL_VI, REGIME_VI, REGIME_NOUN_VI,
        DRIVER_VI, DRIVER_VI_LOWER,
        DRIFT_SOURCE_VI, DRIFT_STATUS_VI,
        FLOW_STATE_VI, FLOW_ROTATION_VI,
        CONVICTION_VI, SECTOR_VI,
        STATUS_VI, SEVERITY_LABEL_VI,
        FIELD_LABELS,
        EARLY_WARNING_VI, DRIFT_TREND_VI,
        ETS_STATUS_VI,
        _EXTRA_MAP,
    ]
    for m in sources:
        merged.update(m)
    return merged


_MASTER_MAP: dict[str, str] = _build_master_map()

# Pre-compile word-boundary regex for UPPERCASE tokens (kernel enum convention)
# This handles embedded English tokens in narrative text without false positives
_UPPER_TOKENS: dict[str, str] = {
    k: v for k, v in _MASTER_MAP.items()
    if k.isupper() and len(k) > 1
}
_NARRATIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf'\b{re.escape(k)}\b'), v) for k, v in _UPPER_TOKENS.items()
]


# ====================================================================
# RECURSIVE LOCALIZER
# ====================================================================

def _localize_value(value: Any) -> Any:
    if isinstance(value, str):
        if value in _MASTER_MAP:
            return _MASTER_MAP[value]
        for pattern, vi in _NARRATIVE_PATTERNS:
            if pattern.search(value):
                value = pattern.sub(vi, value)
        return value
    if isinstance(value, dict):
        return {k: _localize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_localize_value(item) for item in value]
    return value


# ====================================================================
# PUBLIC API — Single Exit Gate
# ====================================================================

def localize_output(data: dict) -> dict:
    """Final Output Gate — localize ALL string values in a report dict.

    This is the LAST transformation before data leaves the backend.
    Recursively walks the entire dict and maps known English kernel tokens
    to Vietnamese using the aggregated master mapping from cognitive_schema.

    Algorithm:
        1. Exact-match enum values → always translated
        2. UPPERCASE tokens in narrative strings → word-boundary regex replacement
        3. Unknown values → pass through unchanged

    Usage:
        from core.canonical_output_adapter import localize_output

        @router.get("/report")
        async def get_report():
            return localize_output(build_weekly_report())
    """
    return _localize_value(data)


# ====================================================================
# ASSERT — Verify the contract at import time
# ====================================================================

def assert_no_english_tokens(data: dict, path: str = "") -> None:
    """Recursively assert that no known English tokens remain in data.

    Raises AssertionError if any known EN token is still present.
    Useful for tests and CI/CD gates.
    """
    for key, value in data.items():
        current = f"{path}.{key}" if path else key
        if isinstance(value, str) and value in _MASTER_MAP:
            raise AssertionError(
                f"EN token leak at {current!r}: {value!r} → should be {_MASTER_MAP[value]!r}"
            )
        if isinstance(value, dict):
            assert_no_english_tokens(value, current)
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    assert_no_english_tokens(item, f"{current}[{i}]")
                elif isinstance(item, str) and item in _MASTER_MAP:
                    raise AssertionError(
                        f"EN token leak at {current}[{i}]: {item!r} → should be {_MASTER_MAP[item]!r}"
                    )
