"""bilingual_schema.py — HCI Localization Schema v1.0

Cấu trúc song ngữ EN/VI cho mọi metric, tín hiệu, và trạng thái.
Mỗi metric có: name (tên), tooltip (giải thích), color_code (màu UI).

Cung cấp Pydantic models cho Reference-based Localization (NormalizedPayload).
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel


class LocalizationDict(BaseModel):
    name: str
    tooltip: str


class BilingualRef(BaseModel):
    vi: LocalizationDict
    en: LocalizationDict


T = TypeVar("T")


class NormalizedPayload(BaseModel, Generic[T]):
    """Payload chuẩn hóa cho batch endpoints.

    localizations: từ điển các BilingualRef, key = ref token.
    data: mảng các item, mỗi item dùng *ref key thay vì nhúng localization.
    """

    localizations: dict[str, BilingualRef]
    data: list[T]


def build_normalized_payload(
    items: list[dict], signal_field: str, signal_schema: dict[str, dict], model_class: type
) -> NormalizedPayload:
    """Xây dựng NormalizedPayload từ danh sách item.

    Args:
        items: Danh sách dict (kết quả từ service layer).
        signal_field: Tên trường chứa signal (VD: 'signalV1').
        signal_schema: Bảng tra cứu bilingual (VD: SIGNAL_V1_CLASSES).
        model_class: Lớp Pydantic cho mỗi item.

    Returns:
        NormalizedPayload[model_class] với localizations chứa tất cả
        BilingualRef duy nhất.
    """
    refs: dict[str, BilingualRef] = {}
    normalized: list = []

    for item in items:
        signal = item.get(signal_field, "UNKNOWN")
        ref_key = f"{signal_field}_{signal}"

        if ref_key not in refs:
            entry = signal_schema.get(signal, {})
            refs[ref_key] = BilingualRef(
                vi=LocalizationDict(
                    name=entry.get("vi", {}).get("name", signal),
                    tooltip=entry.get("vi", {}).get("tooltip", ""),
                ),
                en=LocalizationDict(
                    name=entry.get("en", {}).get("name", signal),
                    tooltip=entry.get("en", {}).get("tooltip", ""),
                ),
            )

        mapped = {k: v for k, v in item.items() if k != signal_field}
        mapped[f"{signal_field}_ref"] = ref_key
        normalized.append(mapped)

    return NormalizedPayload(
        localizations=refs,
        data=[model_class(**m) for m in normalized],
    )


# ── VQA Classifications ─────────────────────────────────────────────
VQA_CLASSES: dict[str, dict[str, Any]] = {
    "CASCADE_LIQUIDATION": {
        "color_code": "#FF4444",
        "vi": {
            "name": "Thanh lý thác đổ",
            "tooltip": "Lực bán chủ động áp đảo, không có lực đỡ từ dòng tiền lớn. Nguy cơ giảm sâu.",
        },
        "en": {
            "name": "Cascade Liquidation",
            "tooltip": "Aggressive selling overwhelms bid liquidity. High risk of deep decline.",
        },
    },
    "MARGIN_AVERAGE_DOWN": {
        "color_code": "#FF8C00",
        "vi": {
            "name": "Cưa chân bàn",
            "tooltip": "Giằng co bắt đáy nội phiên nhưng cuối phiên vẫn sát giá thấp. Nguy cơ tiếp diễn.",
        },
        "en": {
            "name": "Margin Average Down",
            "tooltip": "Intraday dip-buying struggle, closing near lows. Continuation risk.",
        },
    },
    "INSTITUTIONAL_ACCUMULATION": {
        "color_code": "#44BB44",
        "vi": {
            "name": "Tích lũy tổ chức",
            "tooltip": "Tổ chức gom hàng chủ động, giá hồi phục mạnh từ vùng thấp. Tín hiệu tích cực.",
        },
        "en": {
            "name": "Institutional Accumulation",
            "tooltip": "Institutional buying drives strong recovery from lows. Bullish signal.",
        },
    },
    "MARKET_MAKING_CHURN": {
        "color_code": "#FFD700",
        "vi": {
            "name": "Nhiễu tạo lập",
            "tooltip": "Thanh khoản trung bình, không có áp lực mua/bán rõ ràng. Trạng thái trung tính.",
        },
        "en": {
            "name": "Market Making Churn",
            "tooltip": "Average liquidity, no clear buying/selling pressure. Neutral state.",
        },
    },
    "LOW_LIQUIDITY_NOISE": {
        "color_code": "#888888",
        "vi": {
            "name": "Nhiễu thanh khoản thấp",
            "tooltip": "Khối lượng giao dịch thấp hơn ngưỡng Z-Score, không đủ dữ liệu để phân loại.",
        },
        "en": {
            "name": "Low Liquidity Noise",
            "tooltip": "Volume below Z-Score threshold, insufficient data for classification.",
        },
    },
}

# ── Absorption Phases ──────────────────────────────────────────────
ABSORPTION_PHASES: dict[str, dict[str, Any]] = {
    "PANIC": {
        "color_code": "#FF4444",
        "vi": {
            "name": "Hoảng loạn",
            "tooltip": "SDI vượt ngưỡng panic. Thị trường đang trong trạng thái bán tháo mất kiểm soát.",
        },
        "en": {"name": "Panic", "tooltip": "SDI above panic threshold. Market in uncontrolled sell-off."},
    },
    "ABSORPTION_ACTIVE": {
        "color_code": "#FF8C00",
        "vi": {
            "name": "Hấp thụ nội",
            "tooltip": "Lực mua nội đang hấp thụ áp lực bán. SDI bắt đầu hội tụ nhưng chưa hoàn tất.",
        },
        "en": {
            "name": "Absorption Active",
            "tooltip": "Domestic buying absorbing selling pressure. SDI converging but not complete.",
        },
    },
    "EQUILIBRIUM": {
        "color_code": "#44BB44",
        "vi": {"name": "Cân bằng", "tooltip": "SDI hội tụ dưới ngưỡng, volume profile xác nhận. Thị trường đã hấp thụ xong."},
        "en": {
            "name": "Equilibrium",
            "tooltip": "SDI converged below threshold, volume profile confirmed. Absorption complete.",
        },
    },
    "MONITORING": {
        "color_code": "#4488FF",
        "vi": {"name": "Giám sát", "tooltip": "Không có panic, thị trường vận hành bình thường. Chờ tín hiệu mới."},
        "en": {"name": "Monitoring", "tooltip": "No panic detected, market operating normally. Awaiting new signals."},
    },
    "MONITORING_VQA_OVERRIDE": {
        "color_code": "#FF8C00",
        "vi": {
            "name": "Giám sát (VQA ghi đè)",
            "tooltip": "VQA phát hiện phân phối, ghi đè trạng thái MONITORING. Giữ nguyên HDR.",
        },
        "en": {
            "name": "Monitoring (VQA Override)",
            "tooltip": "VQA detects distribution, overrides MONITORING state. HDR unchanged.",
        },
    },
}

# ── Regime States ─────────────────────────────────────────────────
REGIME_STATES: dict[str, dict[str, Any]] = {
    "TRENDING": {
        "color_code": "#44BB44",
        "vi": {"name": "Xu hướng rõ", "tooltip": "Thị trường có xu hướng tăng/giảm rõ rệt, ADX cao, độ rộng lan tỏa."},
        "en": {"name": "Trending", "tooltip": "Clear trend direction, high ADX, broad participation."},
    },
    "RANGING": {
        "color_code": "#FFD700",
        "vi": {"name": "Đi ngang", "tooltip": "Thị trường dao động trong biên độ hẹp, không có xu hướng rõ."},
        "en": {"name": "Ranging", "tooltip": "Market oscillating in a narrow band, no clear trend."},
    },
    "CRISIS": {
        "color_code": "#FF4444",
        "vi": {"name": "Khủng hoảng", "tooltip": "Thị trường suy yếu nghiêm trọng, độ rộng thấp, biến động cao."},
        "en": {"name": "Crisis", "tooltip": "Severe market weakness, low breadth, high volatility."},
    },
    "SILENT_DISTRIBUTION_HEAD": {
        "color_code": "#FF8C00",
        "vi": {
            "name": "Đỉnh phân phối ngầm",
            "tooltip": "Pha phân phối đầu chu kỳ: độ rộng bắt đầu thu hẹp trong khi chỉ số vẫn tăng.",
        },
        "en": {
            "name": "Silent Distribution Head",
            "tooltip": "Early distribution phase: breadth narrowing while index still rising.",
        },
    },
    "RE_ACCUMULATION_BOTTOM": {
        "color_code": "#4488FF",
        "vi": {
            "name": "Đáy tích lũy lại",
            "tooltip": "Pha tích lũy cuối chu kỳ: lực bán cạn, dòng tiền lớn bắt đầu gom hàng.",
        },
        "en": {
            "name": "Re-Accumulation Bottom",
            "tooltip": "Late-cycle accumulation: selling exhausted, smart money accumulating.",
        },
    },
}

# ── Governor / HDR States ─────────────────────────────────────────
GOVERNOR_STATES: dict[str, dict[str, Any]] = {
    "RISK_ON": {
        "color_code": "#44BB44",
        "vi": {"name": "Chấp nhận rủi ro", "tooltip": "Governor mở van giải ngân. HDR thấp, cho phép mở vị thế mới."},
        "en": {"name": "Risk On", "tooltip": "Governor unlocks deployment. Low HDR, new positions allowed."},
    },
    "RISK_OFF": {
        "color_code": "#FF8C00",
        "vi": {"name": "Phòng thủ", "tooltip": "Governor hạn chế rủi ro. HDR cao, chỉ cho phép duy trì vị thế."},
        "en": {"name": "Risk Off", "tooltip": "Governor limits risk. High HDR, only position maintenance allowed."},
    },
    "LOCKED": {
        "color_code": "#FF4444",
        "vi": {"name": "Khóa", "tooltip": "Governor khóa toàn bộ. HDR=1.0, cash-only, không giải ngân."},
        "en": {"name": "Locked", "tooltip": "Governor fully locked. HDR=1.0, cash-only, no deployment."},
    },
}

# ── Early Warning Signals ──────────────────────────────────────────
EARLY_WARNING: dict[str, dict[str, Any]] = {
    "ATR_SHOCK": {
        "color_code": "#FF4444",
        "vi": {"name": "Đột biến ATR", "tooltip": "ATR tăng đột biến, báo hiệu chuyển pha mạnh sắp xảy ra."},
        "en": {"name": "ATR Spike", "tooltip": "ATR spike warning of imminent strong phase transition."},
    },
    "BREADTH_COLLAPSE": {
        "color_code": "#FF8C00",
        "vi": {"name": "Thu hẹp độ rộng", "tooltip": "Độ rộng thị trường thu hẹp nhanh, số mã giảm áp đảo."},
        "en": {"name": "Breadth Narrowing", "tooltip": "Market breadth narrowing rapidly, decliners overwhelm advancers."},
    },
    "REGIME_FLIP": {
        "color_code": "#FFD700",
        "vi": {"name": "Đảo chiều chế độ", "tooltip": "Regime score đảo chiều đột ngột, thị trường chuyển pha."},
        "en": {"name": "Regime Flip", "tooltip": "Regime score reverses abruptly, market phase shifting."},
    },
    "LOW_PARTICIPATION": {
        "color_code": "#FFD700",
        "vi": {"name": "Tham gia thấp", "tooltip": "Tỷ lệ mã tham gia dưới ngưỡng, thiếu động lực tăng bền vững."},
        "en": {"name": "Low Participation", "tooltip": "Participation ratio below threshold, lacking sustainable momentum."},
    },
}


# ── Screener Signal V1 ──────────────────────────────────────────────
SIGNAL_V1_CLASSES: dict[str, dict[str, Any]] = {
    "Breakout": {
        "color_code": "#44BB44",
        "vi": {
            "name": "Phá vỡ",
            "tooltip": "Mã cổ phiếu phá vỡ kháng cự kỹ thuật với khối lượng xác nhận. Tín hiệu mua tiềm năng.",
        },
        "en": {
            "name": "Breakout",
            "tooltip": "Stock breaks through technical resistance with confirming volume. Potential buy signal.",
        },
    },
    "RS-High": {
        "color_code": "#4488FF",
        "vi": {
            "name": "RS Cao",
            "tooltip": "Mã cổ phiếu có RS Rating cao nhất thị trường trong điều kiện CRISIS. Dẫn dắt tiềm năng.",
        },
        "en": {"name": "RS High", "tooltip": "Stock with highest RS Rating in CRISIS market conditions. Potential leader."},
    },
}


def localize_signal(canonical_key: str, schema: dict[str, dict[str, Any]], lang: str = "vi") -> str:
    """Tra cứu tên hiển thị cho một tín hiệu."""
    entry = schema.get(canonical_key)
    if entry is None:
        return canonical_key
    return entry.get(lang, {}).get("name", canonical_key)


def to_hci(metric: str, value: Any, signal: str, schema: dict[str, dict[str, Any]]) -> dict:
    """Chuyển đổi metric thành định dạng HCI song ngữ.

    Args:
        metric: Tên metric (VD: "VQA", "SDI")
        value: Giá trị số
        signal: Tín hiệu phân loại (VD: "CASCADE_LIQUIDATION")
        schema: Bảng tra cứu song ngữ cho signal

    Returns:
        Dict theo chuẩn HCI song ngữ
    """
    entry = schema.get(signal, {})
    return {
        "metric": metric,
        "value": value,
        "signal": signal,
        "color_code": entry.get("color_code", "#888888"),
        "localization": {
            "vi": {
                "name": entry.get("vi", {}).get("name", signal),
                "tooltip": entry.get("vi", {}).get("tooltip", ""),
            },
            "en": {
                "name": entry.get("en", {}).get("name", signal),
                "tooltip": entry.get("en", {}).get("tooltip", ""),
            },
        },
    }
