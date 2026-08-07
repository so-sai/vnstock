"""
HoldingsView Builder v1.0
Semantic compression layer — biến exposure metrics thành Vietnamese cognition.
TUYỆT ĐỐI orthogonal với OpportunityView: không recommendation, không scoring.
"""

from core.holdings.exposure_engine import compute_exposure_summary
from core.holdings.models import HoldingsView


def build_holdings_view() -> HoldingsView:
    summary = compute_exposure_summary()
    sectors = summary.get("sector_exposure", [])
    concentration = summary.get("concentration", {})
    liquidity = summary.get("liquidity_fragility", {})
    beta_exp = summary.get("beta_exposure", {})
    positions = summary.get("positions", [])
    cash = summary.get("cash", 0)

    if not positions:
        return HoldingsView(
            portfolio_health_vi="Danh mục trống. Chưa có vị thế nào.",
            stress_level="THẤP",
            dominant_exposure_vi="Không có rủi ro danh mục.",
            hidden_concentration_vi=None,
            liquidity_fragility_vi="Không có tài sản cần thanh khoản.",
            regime_alignment_vi="Chưa thể đánh giá.",
            suggested_adjustment_vi="Theo dõi cơ hội từ OpportunityView.",
            confidence_vi="CAO",
        )

    n_pos = concentration.get("n_positions", 0)
    top3 = concentration.get("top3_weight", 0)
    illiquid_w = liquidity.get("illiquid_weight", 0)
    fragile_w = liquidity.get("fragile_weight", 0)
    high_beta_w = beta_exp.get("high_beta_weight", 0)

    # ── Stress level ──
    stressors = 0
    if top3 > 60:
        stressors += 1
    if illiquid_w > 30:
        stressors += 1
    if high_beta_w > 60:
        stressors += 1
    if fragile_w > 15:
        stressors += 1

    if stressors >= 3:
        stress_level = "NGUY_HIỂM"
    elif stressors == 2:
        stress_level = "CAO"
    elif stressors == 1:
        stress_level = "TRUNG_BÌNH"
    else:
        stress_level = "THẤP"

    # ── Dominant exposure ──
    if sectors:
        top_sector = sectors[0]
        dominant = f"{top_sector['sector']} chiếm {top_sector['weight']:.0f}% danh mục ({top_sector['position_count']} mã)"
    else:
        dominant = "Chưa xác định phân bổ ngành"

    # ── Hidden concentration ──
    hidden = None
    if top3 > 70:
        hidden = f"Top 3 vị thế chiếm {top3:.0f}% danh mục — rủi ro tập trung rất cao"
    elif top3 > 50:
        hidden = f"Top 3 vị thế chiếm {top3:.0f}% danh mục — cần theo dõi phân bổ"

    # ── Liquidity fragility ──
    if fragile_w > 20:
        lq = f"{fragile_w:.0f}% danh mục khó thoát trong điều kiện stress — rủi ro thanh khoản cao"
    elif illiquid_w > 30:
        lq = f"{illiquid_w:.0f}% danh mục thanh khoản thấp — có thể khó giảm vị thế nhanh"
    elif fragile_w > 0:
        lq = f"{fragile_w:.0f}% danh mục có độ thanh khoản hạn chế"
    else:
        lq = "Thanh khoản danh mục ở mức kiểm soát"

    # ── Regime alignment ──
    from core.macro.gold_regime_engine import analyze_gold_regime

    gold = analyze_gold_regime()
    gold_bias = gold.get("macro_bias", "NEUTRAL")
    if high_beta_w > 50 and gold_bias == "DEFENSIVE":
        regime_alignment = "Xung đột: danh mục beta cao nhưng thị trường đang phòng thủ"
    elif high_beta_w < 30 and gold_bias == "RISK_ON":
        regime_alignment = "Thiếu sóng: thị trường risk-on nhưng danh mục beta thấp"
    else:
        regime_alignment = "Phân bổ phù hợp với trạng thái thị trường hiện tại"

    # ── Suggested adjustment (NOT a recommendation) ──
    if stress_level == "NGUY_HIỂM":
        adjustment = "Cần rà soát danh mục: giảm tập trung và cải thiện thanh khoản"
    elif stress_level == "CAO":
        adjustment = "Xem xét giảm vị thế tập trung và tăng tỷ trọng tiền mặt"
    elif stress_level == "TRUNG_BÌNH":
        adjustment = "Theo dõi rủi ro tập trung, duy trì kỷ luật cắt lỗ"
    else:
        adjustment = "Danh mục ở trạng thái cân bằng, duy trì chiến lược hiện tại"

    # ── Confidence ──
    confidence = "CAO" if n_pos >= 3 else "TRUNG BÌNH"

    return HoldingsView(
        portfolio_health_vi=_build_health_text(stress_level, n_pos, top3, cash),
        stress_level=stress_level,
        dominant_exposure_vi=dominant,
        hidden_concentration_vi=hidden,
        liquidity_fragility_vi=lq,
        regime_alignment_vi=regime_alignment,
        suggested_adjustment_vi=adjustment,
        confidence_vi=confidence,
    )


def _build_health_text(stress_level: str, n_pos: int, top3: float, cash: float) -> str:
    cash_bn = cash / 1e9
    base = f"Danh mục {n_pos} vị thế, tiền mặt {cash_bn:.1f} tỷ. "
    if stress_level == "NGUY_HIỂM":
        return base + "Mức độ tập trung rất cao — cần hành động giảm rủi ro ngay."
    if stress_level == "CAO":
        return base + f"Top 3 chiếm {top3:.0f}% — rủi ro tập trung đáng kể."
    if stress_level == "TRUNG_BÌNH":
        return base + "Một số rủi ro tập trung cần theo dõi."
    return base + "Phân bổ danh mục đang ở trạng thái khỏe mạnh."
