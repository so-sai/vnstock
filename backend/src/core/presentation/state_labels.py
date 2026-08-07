from .models import PresentationLabel

# ───────────────────────────────
#  Epistemic states
# ───────────────────────────────

EPISTEMIC_LABELS: dict[str, PresentationLabel] = {
    "VERIFIED": PresentationLabel(
        label_vi="Đã xác thực",
        color="green",
        icon="check-circle",
        priority=1,
        severity_score=0.1,
        action_bias="HOLD",
    ),
    "PROBABILISTIC": PresentationLabel(
        label_vi="Xác suất cao",
        color="blue",
        icon="bar-chart",
        priority=2,
        severity_score=0.3,
        action_bias="HOLD",
    ),
    "CONFLICTED": PresentationLabel(
        label_vi="Tín hiệu mâu thuẫn",
        color="orange",
        icon="alert-triangle",
        priority=3,
        severity_score=0.55,
        action_bias="WAIT",
    ),
    "DEGRADED": PresentationLabel(
        label_vi="Độ tin cậy suy giảm",
        color="yellow",
        icon="trending-down",
        priority=4,
        severity_score=0.75,
        action_bias="REDUCE_RISK",
    ),
    "INVALIDATED": PresentationLabel(
        label_vi="Không còn hiệu lực",
        color="red",
        icon="x-circle",
        priority=5,
        severity_score=0.95,
        action_bias="REDUCE_RISK",
    ),
}

# ───────────────────────────────
#  Risk states
# ───────────────────────────────

RISK_LABELS: dict[str, PresentationLabel] = {
    "LOCKDOWN": PresentationLabel(
        label_vi="Phòng thủ tuyệt đối",
        color="red",
        icon="shield-off",
        priority=5,
        severity_score=0.95,
        action_bias="REDUCE_RISK",
    ),
    "HIGH_STRESS": PresentationLabel(
        label_vi="Áp lực rủi ro cao",
        color="orange",
        icon="alert-circle",
        priority=4,
        severity_score=0.80,
        action_bias="REDUCE_RISK",
    ),
    "DEFENSIVE": PresentationLabel(
        label_vi="Phòng thủ",
        color="yellow",
        icon="shield",
        priority=3,
        severity_score=0.60,
        action_bias="HOLD",
    ),
    "NEUTRAL": PresentationLabel(
        label_vi="Trung tính",
        color="gray",
        icon="minus",
        priority=2,
        severity_score=0.30,
        action_bias="HOLD",
    ),
    "RISK_ON": PresentationLabel(
        label_vi="Chấp nhận rủi ro",
        color="green",
        icon="activity",
        priority=1,
        severity_score=0.15,
        action_bias="BUY",
    ),
}

# ───────────────────────────────
#  Flow / Liquidity states
# ───────────────────────────────

LIQUIDITY_LABELS: dict[str, PresentationLabel] = {
    "EXPANDING": PresentationLabel(
        label_vi="Thanh khoản mở rộng",
        color="green",
        icon="trending-up",
        priority=1,
        severity_score=0.2,
        action_bias="BUY",
    ),
    "STABLE": PresentationLabel(
        label_vi="Thanh khoản ổn định",
        color="blue",
        icon="minus",
        priority=2,
        severity_score=0.3,
        action_bias="HOLD",
    ),
    "FRAGMENTED": PresentationLabel(
        label_vi="Dòng tiền phân hóa",
        color="yellow",
        icon="git-branch",
        priority=3,
        severity_score=0.5,
        action_bias="WAIT",
    ),
    "CONTRACTION": PresentationLabel(
        label_vi="Thanh khoản co hẹp",
        color="orange",
        icon="trending-down",
        priority=4,
        severity_score=0.7,
        action_bias="REDUCE_RISK",
    ),
}

# ───────────────────────────────
#  Market phase
# ───────────────────────────────

MARKET_PHASE_LABELS: dict[str, PresentationLabel] = {
    "BULL_TRENDING": PresentationLabel(
        label_vi="Xu hướng tăng",
        color="green",
        priority=1,
        severity_score=0.1,
        action_bias="BUY",
    ),
    "BEAR_TRENDING": PresentationLabel(
        label_vi="Xu hướng giảm",
        color="red",
        priority=4,
        severity_score=0.7,
        action_bias="REDUCE_RISK",
    ),
    "SIDEWAYS": PresentationLabel(
        label_vi="Đi ngang tích lũy",
        color="gray",
        priority=2,
        severity_score=0.3,
        action_bias="HOLD",
    ),
    "PHAN_HOA": PresentationLabel(
        label_vi="Phân hóa mạnh",
        color="yellow",
        priority=3,
        severity_score=0.6,
        action_bias="WAIT",
    ),
    "CRISIS": PresentationLabel(
        label_vi="Khủng hoảng",
        color="red",
        priority=5,
        severity_score=0.95,
        action_bias="REDUCE_RISK",
    ),
}

# ───────────────────────────────
#  Entropy states
# ───────────────────────────────

ENTROPY_LABELS: dict[str, PresentationLabel] = {
    "HỘI_TỤ": PresentationLabel(
        label_vi="Tín hiệu hội tụ",
        color="green",
        priority=1,
        severity_score=0.1,
        action_bias="HOLD",
    ),
    "PHÂN_KỲ": PresentationLabel(
        label_vi="Tín hiệu phân kỳ",
        color="yellow",
        priority=3,
        severity_score=0.5,
        action_bias="WAIT",
    ),
    "NHIỄU_CAO": PresentationLabel(
        label_vi="Nhiễu thị trường cao",
        color="orange",
        priority=4,
        severity_score=0.7,
        action_bias="WAIT",
    ),
    "MẤT_ỔN_ĐỊNH": PresentationLabel(
        label_vi="Mất ổn định nhận thức",
        color="red",
        priority=5,
        severity_score=0.9,
        action_bias="REDUCE_RISK",
    ),
}

# ───────────────────────────────
#  Gold regime states
# ───────────────────────────────

GOLD_REGIME_LABELS: dict[str, PresentationLabel] = {
    "RISK_OFF": PresentationLabel(
        label_vi="Áp lực phòng thủ tăng mạnh",
        color="orange",
        icon="shield",
        priority=4,
        severity_score=0.75,
        action_bias="REDUCE_RISK",
    ),
    "DEFENSIVE": PresentationLabel(
        label_vi="Dòng tiền phòng thủ",
        color="yellow",
        icon="shield",
        priority=3,
        severity_score=0.55,
        action_bias="HOLD",
    ),
    "NEUTRAL": PresentationLabel(
        label_vi="Vàng trung tính",
        color="gray",
        icon="minus",
        priority=2,
        severity_score=0.30,
        action_bias="HOLD",
    ),
    "RISK_ON": PresentationLabel(
        label_vi="Dòng tiền chấp nhận rủi ro",
        color="green",
        icon="activity",
        priority=1,
        severity_score=0.15,
        action_bias="BUY",
    ),
}

# ───────────────────────────────
#  LCI / Breadth quality states
# ───────────────────────────────

LCI_LABELS: dict[str, PresentationLabel] = {
    "LAN_TOA_THAT": PresentationLabel(
        label_vi="Thanh khoản lan tỏa thực",
        color="green",
        icon="activity",
        priority=1,
        severity_score=0.15,
        action_bias="BUY",
    ),
    "CO_CUM_VUA": PresentationLabel(
        label_vi="Thanh khoản co cụm nhẹ",
        color="yellow",
        icon="alert-triangle",
        priority=3,
        severity_score=0.45,
        action_bias="WAIT",
    ),
    "CO_CUM_MANH": PresentationLabel(
        label_vi="Thanh khoản co cụm mạnh",
        color="orange",
        icon="trending-up",
        priority=4,
        severity_score=0.70,
        action_bias="REDUCE_RISK",
    ),
    "CO_CUM_CUC_DOAN": PresentationLabel(
        label_vi="Thanh khoản co cụm cực đoan",
        color="red",
        icon="shield-off",
        priority=5,
        severity_score=0.90,
        action_bias="REDUCE_RISK",
    ),
}

# ───────────────────────────────
#  Helper accessor
# ───────────────────────────────

CATALOG: dict[str, dict[str, PresentationLabel]] = {
    "epistemic": EPISTEMIC_LABELS,
    "risk": RISK_LABELS,
    "liquidity": LIQUIDITY_LABELS,
    "market_phase": MARKET_PHASE_LABELS,
    "entropy": ENTROPY_LABELS,
    "gold_regime": GOLD_REGIME_LABELS,
    "lci": LCI_LABELS,
}


def get_label(category: str, key: str) -> PresentationLabel:
    return CATALOG.get(category, {}).get(key)
