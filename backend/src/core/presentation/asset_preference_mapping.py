from .models import AssetBias, AssetBiasEntry, AssetPreferenceMap

_BIAS_LABELS: dict[AssetBias, str] = {
    "STRONG_PREFER": "Ưu tiên cao",
    "PREFER": "Ưu tiên",
    "NEUTRAL": "Trung tính",
    "AVOID": "Hạn chế",
    "STRONG_AVOID": "Tránh",
}


def _entry(asset_class: str, bias: AssetBias, rationale: str) -> AssetBiasEntry:
    return AssetBiasEntry(
        asset_class=asset_class,
        bias=bias,
        label_vi=_BIAS_LABELS[bias],
        rationale_vi=rationale,
    )


def _expansion_map() -> AssetPreferenceMap:
    return AssetPreferenceMap(
        dominant_bias_vi="Dòng tiền lan tỏa rộng — ưu tiên cổ phiếu tăng trưởng và mid-cap",
        entries=[
            _entry("Large-cap dẫn dắt", "PREFER", "Đầu kéo chính của thị trường, thanh khoản tốt"),
            _entry("Mid-cap tăng trưởng", "STRONG_PREFER", "Dòng tiền lan tỏa — mid-cap thường outperforms trong expansion"),
            _entry("Cổ phiếu phòng thủ", "NEUTRAL", "Không cần phòng thủ trong expansion, nhưng vẫn an toàn"),
            _entry("Vàng", "NEUTRAL", "Không có áp lực risk-off, vàng đi ngang"),
            _entry("Tiền mặt", "AVOID", "Chi phí cơ hội cao khi thị trường mở rộng"),
        ],
        note_vi="Đây là bias xác suất dựa trên state thị trường, không phải khuyến nghị đầu tư",
    )


def _concentration_map() -> AssetPreferenceMap:
    return AssetPreferenceMap(
        dominant_bias_vi="Thị trường phụ thuộc vài trụ — ưu tiên large-cap dẫn dắt, thận trọng mid-cap",
        entries=[
            _entry("Large-cap dẫn dắt", "PREFER", "Nhóm trụ đang kéo index — cơ hội ngắn hạn nếu còn flow"),
            _entry("Mid-cap tăng trưởng", "AVOID", "Thiếu dòng tiền lan tỏa — mid-cap không theo kịp"),
            _entry("Cổ phiếu phòng thủ", "PREFER", "Phòng thủ được ưa chuộng khi thị trường tập trung hẹp"),
            _entry("Vàng", "NEUTRAL", "Chưa có tín hiệu risk-off rõ rệt"),
            _entry("Tiền mặt", "NEUTRAL", "Giữ một phần tiền mặt để chờ cơ hội khi cấu trúc rõ lại"),
        ],
        note_vi="Đây là bias xác suất dựa trên state thị trường, không phải khuyến nghị đầu tư",
    )


def _fragile_map() -> AssetPreferenceMap:
    return AssetPreferenceMap(
        dominant_bias_vi="Cấu trúc yếu, rủi ro hệ thống tăng — ưu tiên tài sản phòng thủ",
        entries=[
            _entry("Large-cap dẫn dắt", "AVOID", "Đầu kéo có nguy cơ sụp đổ nếu breadth tiếp tục yếu"),
            _entry("Mid-cap tăng trưởng", "STRONG_AVOID", "Rủi ro thanh khoản cao nhất khi cấu trúc yếu"),
            _entry("Cổ phiếu phòng thủ", "PREFER", "Phòng thủ là nơi trú ẩn trong fragile state"),
            _entry("Vàng", "PREFER", "Hedge rủi ro hệ thống — vàng thường tăng khi cấu trúc lệch"),
            _entry("Tiền mặt", "STRONG_PREFER", "Bảo toàn vốn là ưu tiên số một"),
        ],
        note_vi="Đây là bias xác suất dựa trên state thị trường, không phải khuyến nghị đầu tư",
    )


def _stress_map() -> AssetPreferenceMap:
    return AssetPreferenceMap(
        dominant_bias_vi="Risk-off toàn diện — ưu tiên bảo toàn vốn tuyệt đối",
        entries=[
            _entry("Large-cap dẫn dắt", "STRONG_AVOID", "Ngay cả trụ cũng chịu áp lực bán trong stress state"),
            _entry("Mid-cap tăng trưởng", "STRONG_AVOID", "Thanh khoản khô, mid-cap giảm sâu nhất"),
            _entry("Cổ phiếu phòng thủ", "NEUTRAL", "Phòng thủ giảm ít hơn nhưng vẫn không an toàn tuyệt đối"),
            _entry("Vàng", "STRONG_PREFER", "Nơi trú ẩn truyền thống trong khủng hoảng"),
            _entry("Tiền mặt", "STRONG_PREFER", "Ưu tiên bảo toàn vốn — tiền mặt là vua"),
        ],
        note_vi="Đây là bias xác suất dựa trên state thị trường, không phải khuyến nghị đầu tư",
    )


def _liquidity_driven_map() -> AssetPreferenceMap:
    return AssetPreferenceMap(
        dominant_bias_vi="Tiền là biến số chính — ưu tiên tài sản rủi ro beta cao",
        entries=[
            _entry("Large-cap dẫn dắt", "STRONG_PREFER", "Dòng tiền đổ vào large-cap mạnh nhất"),
            _entry("Mid-cap tăng trưởng", "PREFER", "Beta cao — mid-cap hưởng lợi khi thanh khoản dồi dào"),
            _entry("Cổ phiếu phòng thủ", "AVOID", "Phòng thủ underperform khi liquidity-driven"),
            _entry("Vàng", "NEUTRAL", "Vàng không có tín hiệu rõ trong liquidity-driven state"),
            _entry("Tiền mặt", "STRONG_AVOID", "Chi phí cơ hội rất cao khi liquidity đang lái thị trường"),
        ],
        note_vi="Đây là bias xác suất dựa trên state thị trường, không phải khuyến nghị đầu tư",
    )


def _neutral_map() -> AssetPreferenceMap:
    return AssetPreferenceMap(
        dominant_bias_vi="Không có state rõ ràng — quan sát, không hành động",
        entries=[
            _entry("Large-cap dẫn dắt", "NEUTRAL", "Không đủ tín hiệu để đánh giá"),
            _entry("Mid-cap tăng trưởng", "NEUTRAL", "Không đủ tín hiệu để đánh giá"),
            _entry("Cổ phiếu phòng thủ", "NEUTRAL", "Không đủ tín hiệu để đánh giá"),
            _entry("Vàng", "NEUTRAL", "Không đủ tín hiệu để đánh giá"),
            _entry("Tiền mặt", "NEUTRAL", "Không đủ tín hiệu để đánh giá"),
        ],
        note_vi="Đây là bias xác suất dựa trên state thị trường, không phải khuyến nghị đầu tư",
    )


def compute_asset_preference(
    *,
    trade_state_level: str = "SELECTIVE",
    regime_status: str = "UNKNOWN",
    drift_label: str = "NONE",
    lcr_pct: float = 30.0,
    breadth_health: float = 50.0,
    bdi_signal: str = "CAN_BANG",
) -> AssetPreferenceMap:
    is_expansion = (
        trade_state_level in ("ACTIVE", "AGGRESSIVE")
        and regime_status == "TRENDING"
        and breadth_health >= 50
        and lcr_pct <= 30
    )
    is_concentration = (
        trade_state_level in ("ACTIVE", "SELECTIVE") and lcr_pct > 30 and breadth_health < 50 and bdi_signal == "PHAN_KY_DUONG"
    )
    is_fragile = (
        trade_state_level in ("SELECTIVE", "RESTRICTED")
        and breadth_health < 30
        and lcr_pct > 35
        and "STRUCTURAL" in drift_label.upper()
    )
    is_stress = trade_state_level in ("RESTRICTED", "PROHIBITED") or regime_status == "CRISIS"
    is_liquidity_driven = (
        trade_state_level == "ACTIVE" and regime_status == "TRENDING" and breadth_health >= 50 and lcr_pct > 30
    )

    if is_stress:
        return _stress_map()
    if is_fragile:
        return _fragile_map()
    if is_expansion:
        return _expansion_map()
    if is_liquidity_driven:
        return _liquidity_driven_map()
    if is_concentration:
        return _concentration_map()

    return _neutral_map()
