"""Vietnamese semantic mapping layer for system reports.

Translates PSR / CAGL / CAO raw output into Vietnamese.
Zero architecture change — presentation only.
"""
from __future__ import annotations

TERMS: dict[str, str] = {
    # Status
    "consistent": "ổn định",
    "drift": "biến động",
    "stable": "ổn định",
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

    # Regime
    "RANGING": "đi ngang",
    "TRENDING": "xu hướng rõ",
    "CRISIS": "khủng hoảng",
    "SIDEWAYS": "đi ngang",
    "BULL": "tăng",
    "BEAR": "giảm",
    "neutral": "trung tính",
    "UNKNOWN": "không xác định",

    # Flow State
    "THU_HẸP": "Thu hẹp",
    "THU_HEP": "Thu hẹp",
    "MỞ_RỘNG": "Mở rộng",
    "MO_RONG": "Mở rộng",
    "BROAD_EXPANSION": "Mở rộng diện rộng",
    "NARROWING": "Thu hẹp",
    "CONCENTRATING": "Tập trung",
    "DISPERSING": "Phân tán",
    "ROTATING": "Luân chuyển",

    # Conviction / Confidence
    "HIGH": "Cao",
    "MEDIUM": "Trung bình",
    "LOW": "Thấp",
    "VERY_HIGH": "Rất cao",
    "VERY_LOW": "Rất thấp",

    # Leading / Lagging
    "LEADING": "Dẫn dắt",
    "LAGGING": "Tụt lại",
    "leading": "dẫn dắt",
    "lagging": "tụt lại",

    # Sector Performance
    "ACCELERATION": "Tăng tốc",
    "DECELERATION": "Chậm lại",
    "CONTINUATION": "Tiếp diễn",
    "STABLE": "Ổn định",
    "NEUTRAL": "Trung tính",

    # Capital Displacement
    "MODERATE_TOP1_10%": "Trung bình, Top1 tập trung ~10%",
    "BANK_DOMINANT_40%": "Ngân hàng chi phối ~40%",
    "SECTOR_BREADTH_82%_POSITIVE": "Độ rộng ngành 82% tăng",

    # Fields
    "regime": "trạng thái thị trường",
    "market_state": "trạng thái thị trường",
    "gold": "vàng",
    "trust": "tín nhiệm CAO",
    "api_routes": "API",
    "data_quality": "chất lượng dữ liệu",
    "api_contract": "hợp đồng API",

    # System
    "snapshot": "bản ghi trạng thái",
    "routes": "endpoint",
    "hash": "mã băm",
    "api_contract_hash": "mã băm hợp đồng API",
    "semantic_contract_hash": "mã băm hợp đồng ngữ nghĩa",
    "git_commit": "commit git",
    "timestamp": "thời gian",

    # CAGL
    "prefix_dup": "tiền tố trùng lặp",
    "phantom": "endpoint ảo",
    "missing": "thiếu endpoint",
    "unregistered": "chưa đăng ký",
    "undocumented": "không có tài liệu",
    "prefix_mismatch": "sai lệch tiền tố",

    # General
    "error": "lỗi",
    "warning": "cảnh báo",
    "info": "thông tin",
    "none": "không có",
    "total": "tổng cộng",
}

FIELD_LABELS: dict[str, str] = {
    "regime": "Trạng thái thị trường",
    "market_state": "Trạng thái thị trường",
    "gold": "Vàng",
    "trust": "Tín nhiệm CAO",
    "data_quality": "Chất lượng dữ liệu",
    "api_routes": "API",
    "api_contract": "Hợp đồng API",
    "weekly_report": "Báo cáo tuần",

    # Flow State
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

    # Market Overview
    "market_regime": "Trạng thái thị trường",
    "status_vi": "Diễn giải",
    "active_model": "Model hoạt động",
    "consensus": "Đồng thuận",

    # RSI
    "bull_count": "S mã tăng (RSI)",
    "bear_count": "S mã giảm (RSI)",
    "total_scanned": "Tổng đã quét",
    "habitat_distribution": "Phân bố môi trường RSI",
}


def translate(term: str) -> str:
    """Translate a single term to Vietnamese."""
    return TERMS.get(term, term)


def status_icon(matched: bool) -> str:
    return "+" if matched else "x"


def status_text(matched: bool) -> str:
    return "ổn định" if matched else "có biến động"


def field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " ").title())


def format_drift_line(field: str, matched: bool) -> str:
    icon = status_icon(matched)
    label = field_label(field)
    status = status_text(matched)
    return f"  {icon} {label}: {status}"


def format_scan_summary(
    snapshot_id: str,
    route_count: int,
    api_hash: str,
    snapshot_hash: str,
    timestamp: str,
) -> str:
    return (
        f"CAGL SNAPSHOT {snapshot_id}\n"
        f"  API: {route_count} endpoint\n"
        f"  Mã băm hợp đồng API: {api_hash}\n"
        f"  Mã băm trạng thái: {snapshot_hash}\n"
        f"  Thời gian: {timestamp}"
    )


def format_freeze_summary(
    version: str,
    api_hash: str,
    semantic_hash: str,
    commit: str,
    created_at: str,
) -> str:
    return (
        f"CAGL FREEZE {version}\n"
        f"  Mã băm hợp đồng API:     {api_hash}\n"
        f"  Mã băm hợp đồng ngữ nghĩa: {semantic_hash}\n"
        f"  Commit git:              {commit}\n"
        f"  Thời gian:               {created_at}"
    )


def format_verify_result(
    routes_scanned: int,
    errors: int,
    warnings: int,
    is_valid: bool,
    findings: list,
) -> str:
    status = "HỢP LỆ" if is_valid else "KHÔNG HỢP LỆ"
    lines = [
        "CAGL VERIFY - Kiểm tra hợp đồng API",
        f"  Endpoint đã quét: {routes_scanned}",
        f"  Lỗi:              {errors}",
        f"  Cảnh báo:         {warnings}",
        f"  Trạng thái:       {status}",
        "",
    ]
    if findings:
        for f in findings:
            severity_vi = translate(f.severity)
            category_vi = translate(f.category)
            lines.append(f"  [{severity_vi}] {category_vi}: {f.path}")
            lines.append(f"    {f.message}")
    else:
        lines.append("  + Không có vấn đề -- tất cả endpoint đều ổn định.")
    return "\n".join(lines)


def format_drift_report(snapshot_id: str, match: bool, diffs: list, duration_ms: float) -> str:
    status = "KHÔNG CÓ SAI LỆCH" if match else "CÓ SAI LỆCH"
    lines = [
        f"PSR - So sánh trạng thái {snapshot_id}",
        f"  Kết quả:         {status}",
        f"  Thời gian phân tích: {duration_ms:.0f}ms",
        "",
        "  Chi tiết từng tầng:",
    ]
    for d in diffs:
        lines.append(format_drift_line(d.field, d.match))
    lines.append("")
    if match:
        lines.append("  + Toàn bộ hệ thống ổn định, không có sai lệch.")
    else:
        drift_fields = [d.field for d in diffs if not d.match]
        lines.append(f"  x Tầng có biến động: {', '.join(drift_fields)}")
        lines.append("    (biến động dữ liệu thị trường là bình thường)")
    return "\n".join(lines)


def format_system_status(data: dict) -> str:
    """Format a full system status report from a dictionary."""
    lines = ["=" * 50, "TRẠNG THÁI HỆ THỐNG", "=" * 50, ""]
    for key, value in data.items():
        label = FIELD_LABELS.get(key, key.replace("_", " ").title())
        if isinstance(value, bool):
            status = "ổn định" if value else "có biến động"
            lines.append(f"  {label}: {status}")
        elif isinstance(value, dict):
            lines.append(f"  {label}:")
            for k, v in value.items():
                lines.append(f"    {k}: {v}")
        else:
            lines.append(f"  {label}: {value}")
    return "\n".join(lines)
