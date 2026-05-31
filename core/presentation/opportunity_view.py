"""
opportunity_view.py — Pure projection layer.
Maps existing portfolio_recommendations.json → OpportunityView (UI-friendly).
NO scoring, NO signal generation, NO re-interpretation.
Shows market opportunities, NOT actual portfolio holdings.
"""

from .models import OpportunityView, SymbolDecisionView


def _conviction_to_action(conviction: float) -> str:
    if conviction >= 70:
        return "MUA"
    elif conviction >= 50:
        return "NẮM_GIỮ"
    elif conviction >= 30:
        return "GIẢM"
    return "TRÁNH"


def _compress_rationale(rationale: str) -> str:
    if not rationale:
        return "Không có luận điểm"
    return rationale[:80]


def _normalize_conviction(raw: float) -> float:
    return round(min(max(raw / 100.0, 0.0), 1.0), 2)


def _build_sector_allocation(top_picks: list[SymbolDecisionView]) -> str:
    sector_counts = {}
    for s in top_picks:
        sector_counts[s.sector] = sector_counts.get(s.sector, 0) + 1
    if not sector_counts:
        return "Chưa có dữ liệu phân bổ ngành"
    sorted_sectors = sorted(sector_counts.items(), key=lambda x: -x[1])
    parts = [f"{sec} ({cnt} mã)" for sec, cnt in sorted_sectors[:3]]
    return "Tập trung: " + ", ".join(parts)


def _build_risk_notes(recs: dict, meta_state: dict | None = None) -> str:
    regime_status = (meta_state or {}).get("market_regime", {}).get("status", "")
    risk_gov = (meta_state or {}).get("risk_state", {}).get("governor_state", "")

    notes = []
    core_count = recs.get("core_count", 0)
    rotation_count = recs.get("rotation_count", 0)

    if core_count == 0:
        notes.append("Không có mã đủ điều kiện ở nhóm Ổn Định")
    if rotation_count == 0:
        notes.append("Dòng tiền dẫn sóng chưa hình thành rõ")
    if regime_status == "CRISIS":
        notes.append("Thị trường đang trong pha CRISIS — ưu tiên bảo toàn vốn")
    if risk_gov in ("LOCKDOWN", "RESTRICTED"):
        notes.append("Risk governor đang hạn chế giao dịch — chỉ giữ vị thế hiện tại")

    return "; ".join(notes) if notes else "Rủi ro danh mục ở mức kiểm soát"


def _build_capital_hint(recs: dict, meta_state: dict | None = None) -> str:
    risk_appetite = (meta_state or {}).get("meta_state", {}).get("risk_appetite", "")
    confidence = (meta_state or {}).get("meta_state", {}).get("confidence", 0.5)

    if risk_appetite == "ĐÓNG":
        return "Không giải ngân mới — chỉ nắm giữ vị thế hiện tại"
    if confidence < 0.4:
        return "Hạn chế giải ngân — tối đa 20% danh mục cho vị thế mới"
    if confidence < 0.6:
        return "Giải ngân thận trọng — tối đa 40% danh mục"
    return "Có thể giải ngân — duy trì 60-70% danh mục"


def _build_posture_alignment(posture: str) -> str:
    alignment_map = {
        "TĂNG_TỶ_TRỌNG": "Posture mở rộng — top picks phù hợp để gia tăng vị thế",
        "GIỮ_VỊ_THẾ": "Posture duy trì — top picks cho thấy cơ hội nắm giữ",
        "GIẢM_RỦI_RO": "Posture phòng thủ — top picks cần đánh giá lại rủi ro",
        "QUAN_SÁT": "Posture quan sát — top picks chưa đủ tín hiệu hành động",
    }
    return alignment_map.get(posture, f"Posture {posture} — cần đối chiếu với top picks")


def build_opportunity_view(
    recommendations: dict,
    meta_state: dict | None = None,
    decision_posture: str | None = None,
) -> OpportunityView:
    core_items = recommendations.get("core", [])
    rotation_items = recommendations.get("rotation", [])

    top_picks = []
    for item in core_items[:8]:
        top_picks.append(SymbolDecisionView(
            symbol=item.get("symbol", "UNKNOWN"),
            action=_conviction_to_action(item.get("conviction", 0)),
            conviction=_normalize_conviction(item.get("conviction", 0)),
            rationale_vi=_compress_rationale(item.get("rationale", "")),
            sector=item.get("sector", ""),
            tier="core",
        ))

    watchlist = []
    for item in rotation_items[:8]:
        watchlist.append(SymbolDecisionView(
            symbol=item.get("symbol", "UNKNOWN"),
            action=_conviction_to_action(item.get("conviction", 0)),
            conviction=_normalize_conviction(item.get("conviction", 0)),
            rationale_vi=_compress_rationale(item.get("rationale", "")),
            sector=item.get("sector", ""),
            tier="rotation",
        ))

    sector_hint = _build_sector_allocation(top_picks)
    risk_notes = _build_risk_notes(recommendations, meta_state)
    capital_hint = _build_capital_hint(recommendations, meta_state)
    alignment = _build_posture_alignment(decision_posture or "QUAN_SÁT")

    return OpportunityView(
        posture_alignment_note_vi=alignment,
        top_picks=top_picks,
        watchlist=watchlist,
        sector_allocation_hint_vi=sector_hint,
        risk_notes_vi=risk_notes,
        capital_allocation_hint_vi=capital_hint,
    )