"""Vietnamese Localizer for Market State Presentation.

This module maps raw English enum values from engine output to Vietnamese
labels for presentation layer. Zero architecture change — presentation only.

Usage:
    from core.presentation.vi_localizer import localize_market_state
    vi_state = localize_market_state(raw_state)
"""
from __future__ import annotations
from typing import Any

# === ENUM MAPPINGS ===

REGIME_MAP = {
    "TRENDING": "Xu hướng rõ ràng",
    "RANGING": "Đi ngang",
    "CRISIS": "Khủng hoảng",
    "SIDEWAYS": "Đi ngang",
    "BULL": "Tăng",
    "BEAR": "Giảm",
    "UNKNOWN": "Không xác định",
}

FLOW_STATE_MAP = {
    "THU_HẸP": "Thu hẹp",
    "THU_HEP": "Thu hẹp",
    "MỞ_RỘNG": "Mở rộng",
    "MO_RONG": "Mở rộng",
    "BROAD_EXPANSION": "Mở rộng diện rộng",
    "NARROWING": "Thu hẹp",
    "CONCENTRATING": "Tập trung",
    "DISPERSING": "Phân tán",
    "ROTATING": "Luân chuyển",
    "Dòng tiền thu hẹp": "Thu hẹp",
    "Dòng tiền mở rộng": "Mở rộng",
}

CONVICTION_MAP = {
    "HIGH": "Cao",
    "MEDIUM": "Trung bình",
    "LOW": "Thấp",
    "VERY_HIGH": "Rất cao",
    "VERY_LOW": "Rất thấp",
}

SECTOR_LABELS = {
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

FIELD_LABELS = {
    "market_regime": "Trạng thái thị trường",
    "flow_state": "Trạng thái dòng tiền",
    "flow_velocity": "Tốc độ dòng tiền",
    "rotation_velocity": "Tốc độ luân chuyển",
    "classification": "Phân loại dòng tiền",
    "displacement_conviction": "Độ tin cậy dịch chuyển vốn",
    "leading_sectors": "Ngành dẫn dắt",
    "lagging_sectors": "Ngành tụt lại",
    "sector_share": "Phân bổ dòng tiền theo ngành",
    "sector_performance": "Hiệu suất ngành hôm nay",
    "health_score": "Điểm sức khỏe thị trường",
    "advancers": "Số mã tăng",
    "decliners": "Số mã giảm",
    "unchanged": "Số mã đứng giá",
    "total_active": "Tổng mã hoạt động",
    "nh10_count": "Số mã phá MA10",
    "status": "Mã trạng thái",
    "status_vi": "Diễn giải",
    "active_model": "Model hoạt động",
    "consensus": "Đồng thuận",
    "confidence": "Độ tin cậy",
    "score": "Điểm số",
    "bull_count": "Số mã tăng (RSI)",
    "bear_count": "Số mã giảm (RSI)",
    "total_scanned": "Tổng đã quét",
    "habitat_distribution": "Phân bố môi trường RSI",
}


def _translate_enum(value: str, mapping: dict) -> str:
    """Translate enum value using mapping dict, fallback to original."""
    return mapping.get(value, value)


def _translate_sector(sector_code: str) -> str:
    """Translate sector code to Vietnamese label."""
    return SECTOR_LABELS.get(sector_code, sector_code)


def _localize_regime(regime: dict) -> dict:
    """Localize market regime state."""
    if not regime:
        return {}
    result = dict(regime)
    if "status" in result:
        result["status_vi"] = _translate_enum(result["status"], REGIME_MAP)
    return result


def _localize_flow_state(flow: dict) -> dict:
    """Localize flow state."""
    if not flow:
        return {}
    result = dict(flow)
    if "status" in result:
        result["status_vi"] = _translate_enum(result["status"], FLOW_STATE_MAP)
    if "classification" in result:
        result["classification_vi"] = _translate_enum(
            result["classification"], FLOW_STATE_MAP
        )
    if "displacement_conviction" in result:
        result["displacement_conviction_vi"] = _translate_enum(
            result["displacement_conviction"], CONVICTION_MAP
        )
    if "leading_sectors" in result:
        result["leading_sectors_vi"] = [
            _translate_sector(s) for s in result["leading_sectors"]
        ]
    if "lagging_sectors" in result:
        result["lagging_sectors_vi"] = [
            _translate_sector(s) for s in result["lagging_sectors"]
        ]
    return result


def _localize_breadth(breadth: dict) -> dict:
    """Localize breadth state."""
    if not breadth:
        return {}
    result = dict(breadth)
    return result


def localize_market_state(state: dict) -> dict:
    """Localize full market state to Vietnamese.

    Args:
        state: Raw market state dict from coordinator.

    Returns:
        Localized dict with _vi fields added.
    """
    if not state:
        return {}

    result = dict(state)

    if "market_regime" in result:
        result["market_regime"] = _localize_regime(result["market_regime"])

    if "flow_state" in result:
        result["flow_state"] = _localize_flow_state(result["flow_state"])

    if "breadth_state" in result:
        result["breadth_state"] = _localize_breadth(result["breadth_state"])

    return result


def render_market_report(state: dict) -> str:
    """Render a fully Vietnamese market report from localized state.

    Args:
        state: Raw or localized market state dict.

    Returns:
        Formatted Vietnamese report string.
    """
    vi = localize_market_state(state)
    lines = []

    # === HEADER ===
    lines.append("=" * 60)
    lines.append("  BÁO CÁO THỊ TRƯỜNG")
    lines.append("=" * 60)
    lines.append("")

    # === MARKET REGIME ===
    regime = vi.get("market_regime", {})
    status = regime.get("status", "N/A")
    status_vi = regime.get("status_vi", "N/A")
    confidence = regime.get("confidence", "N/A")
    lines.append(f"Mã trạng thái: {status}")
    lines.append(f"Diễn giải: {status_vi}")
    lines.append(f"Điểm số: {regime.get('score', 'N/A')}")
    lines.append(f"Độ tin cậy: {confidence}")
    lines.append(f"Model: {regime.get('active_model', 'N/A')}")
    lines.append(f"Đồng thuận: {regime.get('consensus', 'N/A')}")
    lines.append("")

    # === FLOW STATE ===
    flow = vi.get("flow_state", {})
    if flow:
        lines.append("-" * 60)
        lines.append("DÒNG TIỀN")
        lines.append("-" * 60)
        flow_status = flow.get("status", "N/A")
        flow_vi = flow.get("status_vi", "N/A")
        lines.append(f"Trạng thái dòng tiền: {flow_vi}")
        lines.append(f"Mã trạng thái: {flow_status}")
        lines.append(f"Tốc độ dòng tiền: {flow.get('flow_velocity', 'N/A')}")
        lines.append(f"Tốc độ luân chuyển: {flow.get('rotation_velocity', 'N/A')}")
        lines.append(
            f"Phân loại: {flow.get('classification_vi', flow.get('classification', 'N/A'))}"
        )
        lines.append(
            f"Độ tin cậy dịch chuyển vốn: {flow.get('displacement_conviction_vi', flow.get('displacement_conviction', 'N/A'))}"
        )
        lines.append("")

        # Leading sectors
        leading = flow.get("leading_sectors_vi", flow.get("leading_sectors", []))
        if leading:
            lines.append("Ngành dẫn dắt:")
            for s in leading:
                share = flow.get("sector_share", {}).get(
                    s.replace("Ngân hàng", "BANK")
                    .replace("Bất động sản", "RE")
                    .replace("Chứng khoán", "SEC"),
                    "N/A",
                )
                lines.append(f"  - {s} ({share}%)")
            lines.append("")

        # Lagging sectors
        lagging = flow.get("lagging_sectors_vi", flow.get("lagging_sectors", []))
        if lagging:
            lines.append("Ngành tụt lại:")
            for s in lagging:
                lines.append(f"  - {s}")
            lines.append("")

        # Sector performance
        perf = flow.get("sector_performance", {})
        if perf:
            lines.append("Hiệu suất ngành hôm nay:")
            for sector, chg in sorted(perf.items(), key=lambda x: -x[1]):
                sector_vi = _translate_sector(sector)
                sign = "+" if chg >= 0 else ""
                lines.append(f"  {sector_vi:20s} {sign}{chg:.2f}%")
            lines.append("")

    # === BREADTH ===
    breadth = vi.get("breadth_state", {})
    if breadth:
        lines.append("-" * 60)
        lines.append("ĐỘ RỘNG THỊ TRƯỜNG")
        lines.append("-" * 60)
        lines.append(
            f"Điểm sức khỏe: {breadth.get('health_score', 'N/A')}"
        )
        lines.append(
            f"Tổng mã hoạt động: {breadth.get('total_active', 'N/A')}"
        )
        lines.append(f"Số mã tăng: {breadth.get('advancers', 'N/A')}")
        lines.append(f"Số mã giảm: {breadth.get('decliners', 'N/A')}")
        lines.append(f"Số mã đứng giá: {breadth.get('unchanged', 'N/A')}")
        lines.append(f"Số mã phá MA10: {breadth.get('nh10_count', 'N/A')}")
        lines.append("")

    # === CONCLUSION ===
    lines.append("-" * 60)
    lines.append("KẾT LUẬN")
    lines.append("-" * 60)
    flow_status = flow.get("status", "")
    regime_status = regime.get("status", "")
    advancers = breadth.get("advancers", 0)
    decliners = breadth.get("decliners", 0)

    if regime_status == "TRENDING":
        regime_text = "đang trong xu hướng rõ ràng"
    elif regime_status == "RANGING":
        regime_text = "đang đi ngang"
    elif regime_status == "CRISIS":
        regime_text = "đang trong giai đoạn khủng hoảng"
    else:
        regime_text = f"đang ở trạng thái {regime_status}"

    flow_text = flow.get("status_vi", flow_status).lower()
    leading = flow.get("leading_sectors_vi", flow.get("leading_sectors", []))
    leading_text = ", ".join(leading) if leading else "chưa rõ"

    if advancers > decliners:
        breadth_text = f"độ rộng thị trường vẫn tích cực ({advancers} tăng vs {decliners} giảm)"
    else:
        breadth_text = f"độ rộng thị trường đang yếu ({advancers} tăng vs {decliners} giảm)"

    lines.append(
        f"Dòng tiền đang {flow_text} nhưng {breadth_text}. "
        f"Thị trường {regime_text}. "
        f"Các ngành dẫn dắt hiện tại: {leading_text}."
    )
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
