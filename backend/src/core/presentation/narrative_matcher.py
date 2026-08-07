from .models import NarrativeTemplate, TimeHorizon

# ───────────────────────────────
#  Narrative template catalog
# ───────────────────────────────

NARRATIVE_CATALOG: list[NarrativeTemplate] = [
    # ── Risk dominant ──
    NarrativeTemplate(
        template_id="risk_lockdown",
        conditions={"risk_state": ["LOCKDOWN"]},
        template_vi=(
            "Thị trường đang trong trạng thái phòng thủ tuyệt đối. "
            "Rủi ro hệ thống ở mức cao nhất, khuyến nghị giảm toàn bộ vị thế rủi ro."
        ),
        priority=100,
        required_context=["risk_state"],
    ),
    NarrativeTemplate(
        template_id="risk_high_stress_conflict",
        conditions={"risk_state": ["HIGH_STRESS"], "truth_status": ["CONFLICTED"]},
        template_vi="Áp lực rủi ro cao và tín hiệu thị trường chưa đồng thuận. Cần thận trọng, chưa vội thay đổi vị thế lớn.",
        priority=80,
        required_context=["risk_state", "truth_status"],
    ),
    # ── Flow + Risk composition ──
    NarrativeTemplate(
        template_id="flow_expanding_risk_high",
        conditions={"flow_state": ["EXPANDING"], "risk_state": ["HIGH_STRESS"]},
        template_vi="Dòng tiền đang mở rộng nhưng rủi ro hệ thống vẫn cao. Tín hiệu hiện chưa đồng thuận hoàn toàn.",
        priority=60,
        required_context=["flow_state", "risk_state"],
    ),
    NarrativeTemplate(
        template_id="flow_contraction_risk_neutral",
        conditions={"flow_state": ["CONTRACTION"], "risk_state": ["NEUTRAL", "RISK_ON"]},
        template_vi="Thanh khoản co hẹp, thị trường chưa thực sự hấp dẫn để gia tăng tỷ trọng.",
        priority=50,
        required_context=["flow_state", "risk_state"],
    ),
    NarrativeTemplate(
        template_id="flow_expansion_all_clear",
        conditions={"flow_state": ["EXPANDING"], "risk_state": ["RISK_ON"]},
        template_vi="Dòng tiền mở rộng trên nền rủi ro thấp. Môi trường thuận lợi cho giao dịch.",
        priority=40,
        required_context=["flow_state", "risk_state"],
    ),
    # ── Phase-based ──
    NarrativeTemplate(
        template_id="phase_crisis",
        conditions={"market_phase": ["CRISIS"]},
        template_vi="Thị trường đang trong pha khủng hoảng. Ưu tiên bảo toàn vốn, hạn chế mua mới.",
        priority=90,
        required_context=["market_phase"],
    ),
    NarrativeTemplate(
        template_id="phase_bull_trend",
        conditions={"market_phase": ["BULL_TRENDING"]},
        template_vi="Xu hướng tăng đang chi phối thị trường. Duy trì vị thế, tận dụng nhịp điều chỉnh.",
        priority=30,
        required_context=["market_phase"],
    ),
    # ── Entropy dominant ──
    NarrativeTemplate(
        template_id="entropy_high_noise",
        conditions={"entropy_state": ["NHIỄU_CAO"]},
        template_vi="Thị trường đang nhiễu cao. Tín hiệu các engine chưa đủ tin cậy để ra quyết định.",
        priority=70,
        required_context=["entropy_state"],
    ),
    NarrativeTemplate(
        template_id="entropy_convergence",
        conditions={"entropy_state": ["HỘI_TỤ"]},
        template_vi="Đa phần tín hiệu đang hội tụ về cùng một hướng. Độ tin cậy của nhận định hiện tại ở mức cao.",
        priority=35,
        required_context=["entropy_state"],
    ),
    # ── Default fallback ──
    NarrativeTemplate(
        template_id="generic_lockdown",
        conditions={},
        template_vi="Thị trường đang trong trạng thái cần theo dõi chặt chẽ.",
        priority=1,
        required_context=[],
    ),
    # ── LCI-based narratives ──
    NarrativeTemplate(
        template_id="lci_extreme_concentration",
        conditions={"lci_quality": ["CO_CUM_CUC_DOAN"]},
        template_vi=(
            "Cảnh báo: thanh khoản đang co cụm cực đoan vào nhóm dẫn dắt. Độ lan tỏa thị trường suy giảm nghiêm trọng."
        ),
        priority=85,
        required_context=["lci_quality"],
    ),
    NarrativeTemplate(
        template_id="lci_strong_concentration",
        conditions={"lci_quality": ["CO_CUM_MANH"]},
        template_vi="Thanh khoản tập trung mạnh vào nhóm đầu. Cần thận trọng với các mã ngoài nhóm dẫn dắt.",
        priority=65,
        required_context=["lci_quality"],
    ),
    NarrativeTemplate(
        template_id="lci_good_breadth",
        conditions={"lci_quality": ["LAN_TOA_THAT"]},
        template_vi="Thanh khoản đang lan tỏa đều trên thị trường. Độ rộng thực chất, tín hiệu risk-on đáng tin cậy.",
        priority=45,
        required_context=["lci_quality"],
    ),
    NarrativeTemplate(
        template_id="lci_concentration_conflict",
        conditions={"lci_quality": ["CO_CUM_VUA", "CO_CUM_MANH"], "flow_state": ["EXPANDING"]},
        template_vi="Dòng tiền mở rộng nhưng đang co cụm. Tăng trưởng thiếu bền vững — theo dõi phân hóa.",
        priority=70,
        required_context=["lci_quality", "flow_state"],
    ),
]


def match_templates(state: dict[str, str], horizon: TimeHorizon = "NGẮN_HẠN") -> list[NarrativeTemplate]:
    """
    Given a flat state dict like {"risk_state": "HIGH_STRESS", "flow_state": "EXPANDING"},
    return all matching NarrativeTemplates sorted by priority (descending).
    """
    matches: list[NarrativeTemplate] = []
    for tmpl in NARRATIVE_CATALOG:
        if tmpl.time_horizon != horizon and tmpl.conditions:
            continue
        all_match = all(state.get(key) in allowed_values for key, allowed_values in tmpl.conditions.items())
        if all_match:
            matches.append(tmpl)
    matches.sort(key=lambda t: t.priority, reverse=True)
    return matches


def render_best_template(state: dict[str, str], horizon: TimeHorizon = "NGẮN_HẠN") -> str:
    matches = match_templates(state, horizon)
    if matches:
        return matches[0].template_vi
    return "Không có nhận định phù hợp cho trạng thái thị trường hiện tại."
