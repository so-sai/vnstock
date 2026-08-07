from .models import ActionConstraint, TradeStateLevel, TradeStatePolicy

_TRADE_STATE_DEFS: dict[TradeStateLevel, TradeStatePolicy] = {
    "PROHIBITED": TradeStatePolicy(
        level="PROHIBITED",
        label_vi="Không giao dịch",
        color="red",
        score=0.0,
        max_exposure_pct=0.0,
        max_position_pct=0.0,
        allowed_actions=["CASH_ONLY", "EXIT_ALL"],
        require_confirmation=False,
        narrative_vi="Thị trường đang trong trạng thái khủng hoảng hoặc rủi ro hệ thống. Không mua mới, ưu tiên bảo toàn vốn.",
        action_rule_vi="Chỉ giữ tiền mặt. Không giải ngân. Đóng vị thế rủi ro.",
    ),
    "RESTRICTED": TradeStatePolicy(
        level="RESTRICTED",
        label_vi="Giao dịch hạn chế",
        color="orange",
        score=0.15,
        max_exposure_pct=15.0,
        max_position_pct=5.0,
        allowed_actions=["HOLD", "REDUCE", "EXIT"],
        require_confirmation=True,
        narrative_vi=(
            "Thị trường yếu nền, dòng tiền tập trung hẹp. Chỉ giữ hoặc giảm vị thế, không mua mới nếu không có xác nhận mạnh."
        ),
        action_rule_vi="Không mua mới. Giảm vị thế yếu. Chỉ giữ leader thực sự nếu MSM=MANH + RS mạnh.",
    ),
    "SELECTIVE": TradeStatePolicy(
        level="SELECTIVE",
        label_vi="Giao dịch có chọn lọc",
        color="yellow",
        score=0.35,
        max_exposure_pct=35.0,
        max_position_pct=10.0,
        allowed_actions=["BUY_SELECTIVE", "HOLD", "ROTATE"],
        require_confirmation=True,
        narrative_vi="Tín hiệu trung bình, cần chọn lọc kỹ. Chỉ mua mã có xác nhận đầy đủ (MSM=MANH + volume + RS).",
        action_rule_vi="Mua có chọn lọc. Yêu cầu xác nhận tín hiệu. Không chase. Không bắt đáy nhóm rò rỉ.",
    ),
    "ACTIVE": TradeStatePolicy(
        level="ACTIVE",
        label_vi="Giao dịch chủ động",
        color="green",
        score=0.55,
        max_exposure_pct=60.0,
        max_position_pct=15.0,
        allowed_actions=["BUY", "HOLD", "ROTATE", "ADD"],
        require_confirmation=False,
        narrative_vi="Xu hướng rõ + độ rộng tốt. Có thể giao dịch chủ động với quản lý rủi ro chuẩn.",
        action_rule_vi="Mua chủ động theo tín hiệu. Duy trì tỷ trọng hợp lý. Cắt lỗ nếu breadth suy yếu.",
    ),
    "AGGRESSIVE": TradeStatePolicy(
        level="AGGRESSIVE",
        label_vi="Giao dịch mở rộng",
        color="blue",
        score=0.8,
        max_exposure_pct=80.0,
        max_position_pct=20.0,
        allowed_actions=["BUY", "HOLD", "ADD", "LEVERAGE"],
        require_confirmation=False,
        narrative_vi="Tất cả tín hiệu đồng thuận. Thị trường mạnh nền, thanh khoản lan tỏa. Mở rộng danh mục.",
        action_rule_vi="Mở rộng danh mục. Tăng tỷ trọng các vị thế mạnh. Tận dụng cơ hội từ screener.",
    ),
}


def _score_regime(status: str, score: float) -> float:
    if status == "TRENDING":
        return 1.0 * min(score * 1.5, 1.0)
    elif status == "RANGING":
        return 0.5 * score
    else:
        return 0.1 * score


def _score_breadth(health: float) -> float:
    if health >= 60:
        return 1.0
    elif health >= 45:
        return 0.7
    elif health >= 30:
        return 0.4
    elif health >= 15:
        return 0.2
    return 0.0


def _score_lcr(lcr: float) -> float:
    if lcr <= 20:
        return 1.0
    elif lcr <= 30:
        return 0.7
    elif lcr <= 40:
        return 0.4
    elif lcr <= 50:
        return 0.2
    return 0.0


def _score_flow(flow_status: str) -> float:
    mapping = {
        "MỞ_RỘNG": 1.0,
        "MỞ_RỘNG_TÍCH_CỰC": 1.0,
        "DUY_TRÌ": 0.6,
        "ỔN_ĐỊNH": 0.6,
        "TRUNG_TÍNH": 0.5,
        "PHÂN_HÓA": 0.3,
        "THU_HẸP": 0.15,
        "YẾU": 0.1,
        "KÉM": 0.0,
        "UNKNOWN": 0.4,
    }
    return mapping.get(flow_status, 0.4)


def _score_risk(governor: str) -> float:
    mapping = {
        "NORMAL": 1.0,
        "DEFENSIVE": 0.5,
        "RESTRICTED": 0.2,
        "LOCKDOWN": 0.0,
        "UNKNOWN": 0.5,
    }
    return mapping.get(governor, 0.5)


def _score_bdi(bdi_signal: str) -> float:
    mapping = {
        "CAN_BANG": 1.0,
        "PHAN_KY_DUONG": 0.5,
        "PHAN_KY_AM": 0.3,
    }
    return mapping.get(bdi_signal, 0.5)


def _score_to_level(score: float, veto: bool) -> TradeStateLevel:
    if veto:
        return "PROHIBITED"
    if score >= 0.8:
        return "AGGRESSIVE"
    elif score >= 0.55:
        return "ACTIVE"
    elif score >= 0.35:
        return "SELECTIVE"
    elif score >= 0.15:
        return "RESTRICTED"
    return "PROHIBITED"


def compute_trade_state(
    *,
    regime_status: str = "UNKNOWN",
    regime_score: float = 0.0,
    breadth_health: float = 0.0,
    lcr_pct: float | None = None,
    flow_status: str = "UNKNOWN",
    risk_governor: str = "NORMAL",
    bdi_signal: str = "CAN_BANG",
) -> TradeStatePolicy:
    w_regime = 0.25
    w_breadth = 0.25
    w_lcr = 0.15
    w_flow = 0.15
    w_risk = 0.10
    w_bdi = 0.10

    s_regime = _score_regime(regime_status, regime_score)
    s_breadth = _score_breadth(breadth_health)
    s_lcr = _score_lcr(lcr_pct if lcr_pct is not None else 30.0)
    s_flow = _score_flow(flow_status)
    s_risk = _score_risk(risk_governor)
    s_bdi = _score_bdi(bdi_signal)

    total = w_regime * s_regime + w_breadth * s_breadth + w_lcr * s_lcr + w_flow * s_flow + w_risk * s_risk + w_bdi * s_bdi

    veto = risk_governor == "LOCKDOWN"
    veto_reason = None
    if veto:
        veto_reason = "Risk governor đang LOCKDOWN — phòng thủ tuyệt đối"

    level = _score_to_level(total, veto)
    base = _TRADE_STATE_DEFS[level]

    return TradeStatePolicy(
        level=level,
        label_vi=base.label_vi,
        color=base.color,
        score=round(total, 3),
        max_exposure_pct=base.max_exposure_pct,
        max_position_pct=base.max_position_pct,
        allowed_actions=list(base.allowed_actions),
        require_confirmation=base.require_confirmation,
        narrative_vi=base.narrative_vi,
        action_rule_vi=base.action_rule_vi,
        veto_active=veto,
        veto_reason_vi=veto_reason,
    )


_ACTION_POLICIES: dict[TradeStateLevel, ActionConstraint] = {
    "PROHIBITED": ActionConstraint(
        allowed_order_types=["LIMIT"],
        forbidden_patterns=["BUY_NEW", "BREAKOUT_CHASE", "LEAKAGE_CATCH", "PULLBACK_ENTRY", "DIP_BUY"],
        position_sizing_rule="ZERO",
        confirmation_sources=[],
        max_daily_trades=0,
        cooldown_bars=999,
        allow_short=False,
        allow_margin=False,
        sector_concentration_limit=0.0,
        label_vi="Không được mở vị thế mới. Chỉ được đóng vị thế hiện tại qua lệnh LIMIT.",
    ),
    "RESTRICTED": ActionConstraint(
        allowed_order_types=["LIMIT"],
        forbidden_patterns=["BUY_NEW", "BREAKOUT_CHASE", "LEAKAGE_CATCH", "PULLBACK_ENTRY", "DIP_BUY", "MARGIN"],
        position_sizing_rule="EXIT_ONLY",
        confirmation_sources=["RS", "VOLUME", "MSM"],
        max_daily_trades=1,
        cooldown_bars=5,
        allow_short=False,
        allow_margin=False,
        sector_concentration_limit=0.05,
        label_vi="Chỉ giảm vị thế. Không mua mới. Lệnh LIMIT, không MARKET.",
    ),
    "SELECTIVE": ActionConstraint(
        allowed_order_types=["LIMIT"],
        forbidden_patterns=["BREAKOUT_CHASE", "LEAKAGE_CATCH", "FULL_PORTFOLIO", "MARGIN"],
        position_sizing_rule="FIXED_PCT",
        confirmation_sources=["RS", "VOLUME", "MSM"],
        max_daily_trades=3,
        cooldown_bars=3,
        allow_short=False,
        allow_margin=False,
        sector_concentration_limit=0.15,
        label_vi="Chỉ mua có chọn lọc. Yêu cầu xác nhận từ RS + Volume + MSM. Không chase. Lệnh LIMIT.",
    ),
    "ACTIVE": ActionConstraint(
        allowed_order_types=["MARKET", "LIMIT"],
        forbidden_patterns=["FULL_PORTFOLIO", "MARGIN_OVER_50"],
        position_sizing_rule="FIXED_PCT",
        confirmation_sources=["RS", "VOLUME"],
        max_daily_trades=5,
        cooldown_bars=1,
        allow_short=False,
        allow_margin=True,
        sector_concentration_limit=0.25,
        label_vi="Mua chủ động. Có thể dùng MARKET cho vị thế thanh khoản cao. Margin ≤ 50% giá trị ký quỹ.",
    ),
    "AGGRESSIVE": ActionConstraint(
        allowed_order_types=["MARKET", "LIMIT", "STOP"],
        forbidden_patterns=["FULL_PORTFOLIO_SINGLE"],
        position_sizing_rule="EQUAL_WEIGHT",
        confirmation_sources=["RS"],
        max_daily_trades=10,
        cooldown_bars=0,
        allow_short=True,
        allow_margin=True,
        sector_concentration_limit=0.35,
        label_vi="Mở rộng danh mục. Có thể dùng MARKET, STOP. Margin và short được phép. Phân bổ đều.",
    ),
}


def compile_action_policy(trade_state_level: TradeStateLevel) -> ActionConstraint:
    return _ACTION_POLICIES.get(trade_state_level, _ACTION_POLICIES["PROHIBITED"])
