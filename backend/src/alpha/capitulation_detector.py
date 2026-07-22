"""
capitulation_detector.py — Phase 4 CAS-DSM Capitulation Detector

Two-Step State Machine:
  1. P_cap > 0.85 → State Activation (QUAN_SAT → CANH_MUA)
  2. S_struct → Dynamic Position Sizing Scaler

Sub-modules:
  - MultiTrancheScaler: sigmoid scale-in with atomic lock + gamma skew
  - AbortionProtocol: freeze → cooldown → graduated exit
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path


PROJECT_ROOT = _hydrate_path()

# ── Default params (overridden by params_registry.lookup_params) ──
DEFAULT_PARAMS = {
    "w1": 0.30,
    "w2": 0.30,
    "w3": 0.25,
    "w_vol": 0.15,
    "w_s1": 0.40,
    "w_s2": 0.35,
    "w_s3": 0.25,
    "pcap_threshold": 0.85,
    "pcap_freeze_threshold": 0.50,
    "sstruct_entry_threshold": 0.70,
    "tranches": 6,
    "alpha_base": 0.5,
    "gamma_base": 0.0,
    "cooldown_hours": 72,
    "graduation_period_days": 5,
    "v_shape_threshold": 0.15,
    "bdi_extreme_threshold": -0.50,
    "capitulation_vol_multiplier": 3.0,
}


def _sigma(x: float, alpha: float = 1.0) -> float:
    """Sigmoid function."""
    z = alpha * x
    if z > 0:
        return 1.0 / (1.0 + np.exp(-z))
    return np.exp(z) / (1.0 + np.exp(z))


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ═══════════════════════════════════════════════════════════════
# 1. CAPITULATION DETECTOR — P_cap + S_struct
# ═══════════════════════════════════════════════════════════════

@dataclass
class PcapResult:
    value: float
    components: dict
    params_used: dict


@dataclass
class SstructResult:
    value: float
    components: dict
    params_used: dict


class CapitulationDetector:
    """Cảm biến kích hoạt trạng thái + điều tốc quy mô vốn."""

    D = DEFAULT_PARAMS

    def _load_params(self, regime: str) -> dict:
        """Tra cứu params từ registry, fallback về DEFAULT_PARAMS."""
        try:
            from src.portfolio.params_registry import lookup_params
            entry = lookup_params(regime)
            if entry["params_hash"] != "conservative_default":
                return entry.get("capitulation_params", self.D)
        except Exception:
            pass
        return dict(self.D)

    def compute_p_cap(
        self,
        bdi: float,
        bdi_prev: float,
        entropy: float,
        entropy_prev: float,
        delta_sa: float,
        delta_sa_prev: float,
        volume_zscore: float = 0.0,
        regime: str = "RANGING",
        params: Optional[dict] = None,
    ) -> PcapResult:
        """P_cap = σ(w1·ΔBDI_accel + w2·dE/dt + w3·Δ_SA_rate + v(t)·Vol_Zscore)"""
        p = params or self._load_params(regime)

        # Đạo hàm bậc 1
        bdi_deriv = bdi - bdi_prev
        entropy_deriv = entropy - entropy_prev
        delta_sa_deriv = delta_sa - delta_sa_prev

        # ΔBDI_accel = đạo hàm bậc 2 (từ bdi_prev, bdi hiện tại)
        bdi_accel = bdi - 2.0 * bdi_prev if bdi_prev != 0 else bdi

        # d(Entropy)/dt — tốc độ thay đổi entropy
        d_entropy_dt = entropy_deriv

        # Δ_SA_rate — đạo hàm của delta_sa
        d_delta_sa_rate = delta_sa_deriv

        # v(t)·Vol_Zscore — trọng số volume động
        vol_term = p["w_vol"] * volume_zscore

        raw = (
            p["w1"] * _clamp(bdi_accel, -3, 3) / 3.0
            + p["w2"] * _clamp(d_entropy_dt, -2, 2) / 2.0
            + p["w3"] * _clamp(d_delta_sa_rate * 5, -2, 2) / 2.0
            + vol_term
        )
        value = round(_sigma(raw, 2.0), 4)

        return PcapResult(
            value=value,
            components={
                "bdi_accel": round(bdi_accel, 4),
                "entropy_deriv": round(d_entropy_dt, 4),
                "delta_sa_deriv": round(d_delta_sa_rate, 4),
                "vol_zscore": round(volume_zscore, 4),
                "raw_sum": round(raw, 4),
            },
            params_used=p,
        )

    def compute_s_struct(
        self,
        pillars_active: int,
        total_pillars: int = 3,
        ac_latency: float = 0.1,
        entropy: float = 0.0,
        max_entropy: float = 3.5,
        regime: str = "RANGING",
        params: Optional[dict] = None,
    ) -> SstructResult:
        """S_struct = σ(w_s1·Trụ/3 + w_s2·AC_latency + w_s3·(1-Entropy_norm))"""
        p = params or self._load_params(regime)

        pillar_ratio = pillars_active / max(total_pillars, 1)
        ac_norm = _clamp(ac_latency / 2.0, 0, 1)
        entropy_norm = _clamp(entropy / max_entropy, 0, 1)

        raw = (
            p["w_s1"] * pillar_ratio
            + p["w_s2"] * ac_norm
            + p["w_s3"] * (1.0 - entropy_norm)
        )
        value = round(_sigma(raw, 3.0), 4)

        return SstructResult(
            value=value,
            components={
                "pillar_ratio": round(pillar_ratio, 4),
                "ac_latency_norm": round(ac_norm, 4),
                "entropy_norm": round(entropy_norm, 4),
                "raw_sum": round(raw, 4),
            },
            params_used=p,
        )

    def compute_p_cap_final(self, p_cap: PcapResult, s_struct: SstructResult) -> float:
        """P_cap_final = P_cap · S_struct"""
        return round(p_cap.value * s_struct.value, 4)


# ═══════════════════════════════════════════════════════════════
# 2. MULTI-TRANCHE SIGMOID SCALE-IN
# ═══════════════════════════════════════════════════════════════

@dataclass
class Tranche:
    index: int
    pct: float          # % của MaxPos_campaign
    cumulative: float   # cumulative %
    state: str = "PENDING"  # PENDING | FILLED | SKIPPED | REVERTED


@dataclass
class ScaleInCampaign:
    campaign_id: str = "v1"
    max_pos: float = 0.0
    s_struct_t0: float = 0.0
    max_pos_campaign: float = 0.0  # frozen at t0
    tranches: list[Tranche] = field(default_factory=list)
    isolated_stale_tranches: list[Tranche] = field(default_factory=list)
    alpha: float = 0.5
    gamma: float = 0.0
    n_tranches: int = 6
    start_time: Optional[datetime] = None
    active: bool = True


class MultiTrancheScaler:
    """Multi-Tranche Sigmoid Scale-In with Atomic Lock + Asymmetric Skew."""

    def __init__(self, max_pos: float, s_struct_t0: float, n: int = 6,
                 alpha: float = 0.5, gamma: float = 0.0):
        if n < 2:
            n = 2
        self.n = n
        self.campaign = ScaleInCampaign(
            max_pos=max_pos,
            s_struct_t0=s_struct_t0,
            max_pos_campaign=round(max_pos * _clamp(s_struct_t0, 0, 1), 2),
            alpha=alpha,
            gamma=gamma,
            n_tranches=n,
            start_time=datetime.now(),
            active=True,
        )
        sizes = self._compute_sizes()
        cum = 0.0
        for i in range(n):
            cum += sizes[i]
            self.campaign.tranches.append(Tranche(
                index=i + 1,
                pct=round(sizes[i], 4),
                cumulative=round(cum, 4),
            ))

    def _compute_sizes(self) -> list[float]:
        """Pos_k = [σ(k) - σ(k-1)] / Z, normalized to sum=1."""
        n = self.n
        alpha = self.campaign.alpha
        gamma = self.campaign.gamma

        sigmas = []
        for k in range(n + 1):
            x = k - n / 2.0 - gamma
            sigmas.append(_sigma(x, alpha))

        raw = [sigmas[k] - sigmas[k - 1] for k in range(1, n + 1)]
        total = sum(raw)
        if total <= 0:
            return [1.0 / n] * n
        return [r / total for r in raw]

    def get_tranche(self, k: int) -> Optional[Tranche]:
        for t in self.campaign.tranches:
            if t.index == k:
                return t
        return None

    def compute_index_volatility(self, vnindex_prices: list[float]) -> float:
        """Tính index velocity (v_idx) cho gamma adaptation."""
        if len(vnindex_prices) < 2:
            return 0.0
        returns = [abs(vnindex_prices[i] - vnindex_prices[i - 1]) / vnindex_prices[i - 1]
                   for i in range(1, len(vnindex_prices))]
        return round(np.mean(returns), 4) if returns else 0.0

    def adapt_gamma(self, v_idx: float, v_shape_threshold: float = 0.15) -> float:
        """Điều chỉnh gamma dựa trên index velocity."""
        if v_idx > v_shape_threshold:
            self.campaign.gamma = -1.0
        else:
            self.campaign.gamma = 0.0
        return self.campaign.gamma


# ═══════════════════════════════════════════════════════════════
# 3. ABORTION PROTOCOL — Freeze → Cooldown → Graduated Exit
# ═══════════════════════════════════════════════════════════════

class AbortionState:
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    COOLDOWN = "COOLDOWN"
    DOUBLE_SIGNAL = "DOUBLE_SIGNAL"  # P_cap_final > 0.85 lần 2 → new campaign
    EXITING = "EXITING"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


@dataclass
class AbortionStatus:
    state: str = AbortionState.ACTIVE
    filled_pct: float = 0.0
    hours_in_state: float = 0.0
    cooldown_remaining: float = 0.0
    exit_trades: int = 0
    exit_total_pct: float = 0.0
    last_pcap: float = 0.0
    last_sstruct: float = 0.0
    isolated_stale_pct: float = 0.0
    double_signal_count: int = 0


class AbortionProtocol:
    """3-Step Abortion Protocol — Double Signal compliant.

    Step 1 — FREEZE: ngay khi P_cap_final < freeze_threshold
    Step 2 — COOLDOWN: 72h chờ kiểm chứng
      → Nếu P_cap_final > 0.85 trong COOLDOWN: DOUBLE_SIGNAL (campaign cũ bị khóa)
      → Nếu COOLDOWN hết giờ + S_struct ≥ 0.5: ACTIVE trở lại
      → Nếu COOLDOWN hết giờ + S_struct < 0.5: GRADUATED EXIT
    Step 3 — GRADUATED EXIT: TWAP bán trong graduation_days
    """

    def __init__(self, params: Optional[dict] = None):
        p = params or dict(DEFAULT_PARAMS)
        self.freeze_threshold = p["pcap_freeze_threshold"]
        self.double_signal_threshold = p.get("double_signal_threshold", 0.85)
        self.cooldown_hours = p["cooldown_hours"]
        self.graduation_days = p["graduation_period_days"]
        self.status = AbortionStatus()

    def evaluate(
        self,
        p_cap_final: float,
        s_struct: float,
        campaign: ScaleInCampaign,
        dt_hours: float = 0.0,
    ) -> AbortionStatus:
        """State machine transition.

        Returns AbortionStatus. If state == DOUBLE_SIGNAL, caller MUST:
        1. Record isolated_stale_pct into portfolio risk tracking
        2. Create new campaign via campaign_factory()
        3. Reset AbortionProtocol for new campaign
        """
        s = self.status
        s.last_pcap = p_cap_final
        s.last_sstruct = s_struct
        s.hours_in_state += dt_hours

        filled = [t for t in campaign.tranches if t.state == "FILLED"]
        s.filled_pct = sum(t.pct for t in filled)

        if s.state == AbortionState.ACTIVE:
            if p_cap_final < self.freeze_threshold:
                s.state = AbortionState.FROZEN
                s.hours_in_state = 0.0
                for t in campaign.tranches:
                    if t.state == "PENDING":
                        t.state = "SKIPPED"

        elif s.state == AbortionState.FROZEN:
            s.state = AbortionState.COOLDOWN
            s.hours_in_state = 0.0

        elif s.state == AbortionState.COOLDOWN:
            s.cooldown_remaining = max(0.0, self.cooldown_hours - s.hours_in_state)

            # DOUBLE SIGNAL: P_cap_final > threshold lần 2 trong Cooldown
            if p_cap_final > self.double_signal_threshold:
                s.isolated_stale_pct = s.filled_pct
                s.double_signal_count += 1
                for t in campaign.tranches:
                    if t.state == "FILLED":
                        campaign.isolated_stale_tranches.append(t)
                        t.state = "REVERTED"
                s.state = AbortionState.DOUBLE_SIGNAL
                s.hours_in_state = 0.0
                return s

            if s.cooldown_remaining <= 0:
                if s_struct >= 0.5:
                    s.state = AbortionState.ACTIVE
                    s.hours_in_state = 0.0
                else:
                    s.state = AbortionState.EXITING
                    s.hours_in_state = 0.0

        elif s.state == AbortionState.EXITING:
            total_trades = self.graduation_days
            trades_done = s.exit_trades
            pct_per_trade = s.filled_pct / total_trades if total_trades > 0 else 0
            s.exit_trades = min(total_trades, trades_done + 1)
            s.exit_total_pct = round(s.exit_trades * pct_per_trade, 4)
            if s.exit_total_pct >= s.filled_pct:
                s.state = AbortionState.ABORTED

        return s


# ── Campaign Factory ───────────────────────────────────────

def campaign_factory(
    max_pos: float,
    s_struct_v2: float,
    campaign_id: str = "v1",
    n_tranches: int = 6,
    accumulated_stale_pct: float = 0.0,
    max_drawdown_pct: float = 0.25,
    new_campaign_risk_pct: float | None = None,
) -> ScaleInCampaign | None:
    """Tạo chiến dịch giải ngân mới (thường dùng sau DOUBLE_SIGNAL).

    Hard Shutdown gate — chỉ kích hoạt nếu new_campaign_risk_pct được cung cấp:
        accumulated_stale_pct + new_campaign_risk_pct > max_drawdown_pct

    new_campaign_risk_pct là % tổng vốn mà campaign này sẽ tiêu thụ
    (thường = max_pos * s_struct_v2 / total_capital).

    Phase 4.2 (StalePositionManager) sẽ chịu trách nhiệm write-off
    stale positions để giải phóng drawdown buffer.
    """
    if new_campaign_risk_pct is not None:
        stale_pct = _clamp(accumulated_stale_pct, 0, 1)
        if stale_pct + _clamp(new_campaign_risk_pct, 0, 1) > _clamp(max_drawdown_pct, 0, 1):
            return None  # HARD SHUTDOWN
    scaler = MultiTrancheScaler(max_pos, s_struct_v2, n=n_tranches)
    scaler.campaign.campaign_id = campaign_id
    return scaler.campaign


# ── Max Drawdown Limit Calculator — Linear Accumulation ────

def compute_max_drawdown_limit(
    total_capital: float,
    stale_pcts: list[float] | None = None,
    max_drawdown_pct: float = 0.25,
) -> dict:
    """Tính giới hạn drawdown — LINEAR accumulation cho mọi stale.

    Args:
        total_capital: Tổng vốn danh mục (VND)
        stale_pcts: Danh sách % vốn bị khóa từ mỗi chiến dịch cũ.
                     Cộng dồn tuyến tính — không suy giảm.
                     Mặc định [].
        max_drawdown_pct: % drawdown tối đa cho phép (mặc định 25%)

    Returns:
        dict với:
        - stale_capital: tổng vốn bị khóa (VND)
        - total_stale_pct: tổng % vốn bị khóa
        - remaining_capital: vốn còn lại
        - max_drawdown_limit: hạn mức drawdown (VND)
        - stale_drawdown_risk: rủi ro từ stale = stale_capital
        - available_drawdown: drawdown còn lại
        - risk_ratio: stale_drawdown_risk / max_drawdown_limit
        - hard_shutdown: True nếu stale_drawdown_risk >= max_drawdown_limit
    """
    if stale_pcts is None:
        stale_pcts = []
    total_stale_pct = sum(_clamp(p, 0, 1) for p in stale_pcts)  # LINEAR
    stale_capital = total_capital * _clamp(total_stale_pct, 0, 1)
    remaining_capital = total_capital - stale_capital
    max_drawdown_limit = total_capital * _clamp(max_drawdown_pct, 0, 1)
    stale_drawdown_risk = stale_capital
    available_drawdown = max(0.0, max_drawdown_limit - stale_drawdown_risk)
    risk_ratio = round(stale_drawdown_risk / max_drawdown_limit, 4) if max_drawdown_limit > 0 else 0
    hard_shutdown = stale_drawdown_risk >= max_drawdown_limit
    return {
        "stale_capital": round(stale_capital, 2),
        "total_stale_pct": round(total_stale_pct, 4),
        "remaining_capital": round(remaining_capital, 2),
        "max_drawdown_limit": round(max_drawdown_limit, 2),
        "stale_drawdown_risk": round(stale_drawdown_risk, 2),
        "available_drawdown": round(available_drawdown, 2),
        "risk_ratio": risk_ratio,
        "hard_shutdown": hard_shutdown,
    }


# ═══════════════════════════════════════════════════════════════
# 4. INTEGRATION helper
# ═══════════════════════════════════════════════════════════════

def compute_capitulation_status(snapshot: dict) -> dict:
    """Tính toàn bộ trạng thái capitulation từ market snapshot.

    Args:
        snapshot: output từ tao_anh_chup()

    Returns:
        dict với p_cap, s_struct, p_cap_final, campaign_status
    """
    det = CapitulationDetector()
    c = snapshot.get("cau_truc", {})
    r = snapshot.get("regime", {})
    ddi = snapshot.get("delta_divergence", {})

    regime = r.get("trang_thai", "RANGING")
    pillars = c.get("so_tru_con_lai", 0)
    total_pillars = c.get("tong_so_tru", 3)
    entropy = c.get("entropy", 0.0)
    bdi = c.get("bdi", 0.0)
    ac_latency = ddi.get("ac_latency", 0.1)
    delta_sa = ddi.get("delta_sa", 0.0)

    p_cap = det.compute_p_cap(
        bdi=bdi, bdi_prev=bdi - 0.05,
        entropy=entropy, entropy_prev=entropy - 0.1,
        delta_sa=delta_sa, delta_sa_prev=delta_sa - 0.02,
        volume_zscore=0.0,
        regime=regime,
    )
    s_struct = det.compute_s_struct(
        pillars_active=pillars,
        total_pillars=total_pillars,
        ac_latency=ac_latency,
        entropy=entropy,
        regime=regime,
    )
    p_cap_final = det.compute_p_cap_final(p_cap, s_struct)

    return {
        "p_cap": p_cap.value,
        "p_cap_components": p_cap.components,
        "s_struct": s_struct.value,
        "s_struct_components": s_struct.components,
        "p_cap_final": p_cap_final,
        "canh_mua": p_cap_final > 0.70,
        "regime": regime,
    }


def in_bao_cao(snapshot: dict):
    """In báo cáo Phase 4 CAS-DSM ra console."""
    status = compute_capitulation_status(snapshot)
    print("\n" + "=" * 55)
    print("  PHASE 4 — CAPITULATION DETECTOR (CAS-DSM)")
    print("=" * 55)
    print(f"  P_cap:          {status['p_cap']:.4f}  (ngưỡng >0.85 → CANH_MUA)")
    print(f"    ΔBDI_accel:   {status['p_cap_components']['bdi_accel']:.4f}")
    print(f"    dE/dt:        {status['p_cap_components']['entropy_deriv']:.4f}")
    print(f"    Δ_SA_rate:    {status['p_cap_components']['delta_sa_deriv']:.4f}")
    print(f"    Vol_Zscore:   {status['p_cap_components']['vol_zscore']:.4f}")
    print(f"  S_struct:       {status['s_struct']:.4f}  (tỷ lệ giải ngân)")
    print(f"    Trụ:          {status['s_struct_components']['pillar_ratio']:.4f}")
    print(f"    AC_latency:   {status['s_struct_components']['ac_latency_norm']:.4f}")
    print(f"    Entropy_norm: {status['s_struct_components']['entropy_norm']:.4f}")
    print(f"  P_cap_final:    {status['p_cap_final']:.4f}  (>0.70 → CANH_MUA)")
    badge = "🟢 CANH_MUA" if status['canh_mua'] else "🔴 QUAN_SAT"
    print(f"  Trạng thái:     {badge}" if status['canh_mua'] else f"  Trạng thái:     {badge}")
    print("=" * 55)
