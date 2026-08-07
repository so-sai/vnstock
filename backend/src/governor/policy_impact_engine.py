"""policy_impact_engine.py — He Che Ban Chuan Hoa Tac Dong Chinh Sach Vi Mo (PolicyImpactEngine).

4-STAGE POLICY PIPELINE (2026-08-05):
  [ Policy Event ] -> 1. Variable Mapping (bieu so tac dong)
                    -> 2. Transmission & Lag (LAW-009)
                    -> 3. Beneficiary Clustering (Cum huong loi)
                    -> 4. Dynamic Gate & Governor Calibration

Moi su kien phap ly / tien te duoc dong goi thanh PolicyEvent:
  - Co tinh thoi gian (effective_date -> expiry_date), tu dong het han.
  - Co truyen dan theo thoi gian (transmission_lag_days, LAW-009).
  - Phan hoa theo cum (clusters) de tranh ro ri loi ich vao nhom khong duoc huong.

Vi du thuc te: Quyet dinh 1743/QD-NHNN (30/07/2026, hieu luc 01/08/2026)
  - Tang ty le tien gui KBNN tinh vao LDR tu 20% len 50%
  - Big4 (VCB/CTG/BID) nam 99.59% tien gui KBNN -> Cluster A
"""

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _hydrate_path() -> Path:
    """Path Hydrator v2.2: Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
logger = logging.getLogger(__name__)

DEFAULT_EVENTS_PATH = PROJECT_ROOT / "backend" / "data" / "policy_events.json"


# ── Cum huong loi (Beneficiary Clusters) ────────────────────────────────
# Moi cum co he so huong loi [0, 1]:
#   A (1.0)  : Truc tiep huong loi (Big4 nam 99.59% tien gui KBNN)
#   B (0.2)  : Trung tinh / gian tiep (NHTM CP lon)
#   C (0.0)  : Khong huong loi / ri ro (NHTM CP nho, khong du quy mo)
CLUSTER_A = "CLUSTER_A"
CLUSTER_B = "CLUSTER_B"
CLUSTER_C = "CLUSTER_C"

# Phan loai mac dinh theo do lon / so huu nha nuoc
BIG4_BANKS = {"VCB", "CTG", "BID", "AGR"}
# Big3: 3 ngan hang nam 554.000 ty tien gui co ky han KBNN (QĐ 1743)
BIG3_BANKS = {"VCB", "CTG", "BID"}
STATE_BANKS = BIG4_BANKS | {"MBB", "VPB", "HDB", "SHB"}
LARGE_PRIVATE_BANKS = {
    "TCB",
    "ACB",
    "MBB",
    "VPB",
    "HDB",
    "STB",
    "LPB",
    "TPB",
    "VIB",
    "SHB",
    "MSB",
    "OCB",
}


# ── Cac loai su kien chinh sach chuan hoa ───────────────────────────────
EVENT_TYPE_KBNN_LDR = "KBNN_LDR_ADJUSTMENT"
EVENT_TYPE_NPL_EXTENSION = "NPL_EXTENSION"
EVENT_TYPE_ROOM_INCREASE = "CREDIT_ROOM_INCREASE"
EVENT_TYPE_RATE_CUT = "SBV_RATE_CUT"
EVENT_TYPE_RRR_CHANGE = "RRR_CHANGE"


@dataclass
class PolicyEvent:
    """Mot su kien chinh sach vi mo duoc chuan hoa thanh tham so dinh luong."""

    id: str
    title: str
    effective_date: str  # YYYY-MM-DD
    expiry_date: str  # YYYY-MM-DD
    event_type: str  # EVENT_TYPE_*
    affected_variables: list[str] = field(default_factory=list)  # e.g. ["LDR", "INTERBANK", "COF"]
    transmission_lag_days: int = 3  # LAW-009: lag truyen dan (ngay)
    half_life_days: float = 15.0  # LAW-009: thoi gian ban phan huy
    decay_window_days: int = 90  # Policy Cliff: so ngay truoc expiry bat dau phan ra
    clusters: dict[str, float] = field(default_factory=dict)  # {cluster_name: benefit_ratio}
    delta_params: dict[str, Any] = field(default_factory=dict)  # cac bien dinh luong
    description: str = ""
    source: str = ""

    @property
    def is_active(self) -> bool:
        today = date.today().isoformat()
        return self.effective_date <= today <= self.expiry_date

    def is_active_on(self, target_date: str) -> bool:
        return self.effective_date <= target_date <= self.expiry_date

    def get_benefit_ratio(self, symbol: str) -> float:
        """Xac dinh he so huong loi cua mot co phieu tu cac cum.

        Uu tien khop symbol cu the truoc; neu khong co thi dung cum
        theo loai hinh (Big4 / TMCP lon / con lai).
        """
        for cluster_name, ratio in self.clusters.items():
            members = _cluster_members(cluster_name)
            if symbol in members:
                return ratio
        return 0.0

    def cluster_for(self, symbol: str) -> str:
        """Tra ve ten cum cua symbol (hoac 'OTHER' neu khong khop)."""
        for cluster_name in self.clusters:
            members = _cluster_members(cluster_name)
            if symbol in members:
                return cluster_name
        return "OTHER"


def _cluster_members(cluster_name: str) -> set:
    """Tra ve tap hop symbol thuoc cum. Bo sung logic theo loai hinh."""
    name = cluster_name.upper()
    if "BIG3" in name:
        return BIG3_BANKS
    if "BIG4" in name or name == "A":
        return BIG4_BANKS
    if "LARGE" in name or "TMCP" in name:
        return LARGE_PRIVATE_BANKS
    return set()


@dataclass
class PolicyImpactResult:
    """Ket qua dinh luong tac dong cua chinh sach len mot co phieu."""

    symbol: str
    date: str
    active_events: list[dict] = field(default_factory=list)
    total_impact_score: float = 0.0  # [-1, +1] tong hop
    ldr_relief_bps: float = 0.0  # muc giam LDR (basis points)
    cof_relief_bps: float = 0.0  # muc giam chi phi von
    interbank_shock: float = 0.0  # thay doi lai suat lien NH (%-point)
    nim_boost: float = 0.0  # muc tang NIM uoc tinh
    gate_relaxation: dict[str, float] = field(default_factory=dict)  # cac nguong noi long
    cluster: str = "OTHER"
    transmission_factor: float = 1.0  # LAW-009: muc do da truyen toi (0-1)

    def to_dict(self) -> dict:
        return asdict(self)


class PolicyImpactEngine:
    """May tinh tac dong chinh sach: PolicyEvent -> PolicyImpactResult.

    Pipeline:
      1. Loc su kien dang hieu luc tai target_date.
      2. Xac dinh cum huong loi cua symbol.
      3. Tinh transmission_factor theo LAW-009 (exp decay theo half_life).
      4. Tong hop impact score tu cac delta_params * benefit_ratio * transmission.
    """

    def __init__(self, events_path: Path | None = None):
        self.events_path = events_path or DEFAULT_EVENTS_PATH
        self._events: dict[str, PolicyEvent] = {}
        self._load_events()

    # ── Event lifecycle ──────────────────────────────────────────────
    def _load_events(self) -> None:
        """Nap cac su kien tu file JSON (de chung toi thieu vao ban dau)."""
        if not self.events_path.exists():
            logger.info("policy_events.json chua ton tai — tao moi.")
            self._save_events()
            return
        try:
            with open(self.events_path, encoding="utf-8") as f:
                raw = json.load(f)
            skipped = 0
            for item in raw:
                from .schemas import PolicyEventInputSchema, safe_validate

                validated = safe_validate(PolicyEventInputSchema, item, label=f"policy_event:{item.get('id', '?')}")
                if validated is None:
                    skipped += 1
                    continue
                evt = PolicyEvent(**item)
                self._events[evt.id] = evt
            if skipped:
                logger.warning("[POLICY_SHIELD] Bo qua %d su kien chinh sach khong hop le", skipped)
            logger.info("Nap %d policy events tu %s", len(self._events), self.events_path)
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.error("Loi doc policy_events.json: %s", exc)

    def _save_events(self) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.events_path, "w", encoding="utf-8") as f:
            json.dump(
                [asdict(e) for e in self._events.values()],
                f,
                ensure_ascii=False,
                indent=2,
            )

    def register_event(self, event: PolicyEvent) -> None:
        """Dang ky mot su kien chinh sach moi (hoac cap nhat event cu)."""
        self._events[event.id] = event
        self._save_events()

    def remove_event(self, event_id: str) -> None:
        self._events.pop(event_id, None)
        self._save_events()

    def get_event(self, event_id: str) -> PolicyEvent | None:
        return self._events.get(event_id)

    def get_active_events(self, target_date: str | None = None) -> list[PolicyEvent]:
        """Tra ve cac su kien dang hieu luc tai target_date (mac dinh: hom nay)."""
        target_date = target_date or date.today().isoformat()
        return [e for e in self._events.values() if e.is_active_on(target_date)]

    def list_events(self) -> list[PolicyEvent]:
        return list(self._events.values())

    # ── Clustering ───────────────────────────────────────────────────
    def cluster_for(self, symbol: str) -> str:
        """Xac dinh cum huong loi cua symbol dua tren cac event active."""
        for evt in self._events.values():
            c = evt.cluster_for(symbol)
            if c != "OTHER":
                return c
        return "OTHER"

    def get_benefit_ratio(self, symbol: str, event: PolicyEvent | None = None) -> float:
        """He so huong loi [0,1] cua symbol cho mot event (hoac event dau khop)."""
        if event is not None:
            return event.get_benefit_ratio(symbol)
        for evt in self._events.values():
            ratio = evt.get_benefit_ratio(symbol)
            if ratio > 0:
                return ratio
        return 0.0

    # ── Transmission (LAW-009) ───────────────────────────────────────
    @staticmethod
    def _transmission_factor(elapsed_days: int, half_life: float) -> float:
        """Exp decay theo LAW-009: factor = 1 - 0.5^(elapsed/half_life).

        elapsed = 0 -> 0 (chua truyen toi)
        elapsed = half_life -> 0.5 (da truyen nua)
        elapsed = 3*half_life -> ~0.875 (gan nhu day du)
        """
        if elapsed_days <= 0:
            return 0.0
        return float(max(0.0, min(1.0, 1.0 - 0.5 ** (elapsed_days / half_life))))

    @staticmethod
    def _decay_factor(days_remaining: int, decay_window_days: int) -> float:
        """Policy Cliff decal: he so phan ra truoc khi het han.

        Policy Decay Audit (2026-08-05) — chong "Cu soc Vach da" (Cliff Effect):
          days_remaining >= decay_window  -> 1.0 (vung binh on)
          0 < days_remaining < decay_window -> days_remaining/decay_window (giam tuyen tinh)
          days_remaining <= 0              -> 0.0 (het han hoan toan)

        Why: Khong de chinh sach dot ngot 'sac' ve 0 vao ngay expiry.
        Banks chu dong thoat KBNN 3-6 thang truoc het han -> f_decay giam em ai.
        """
        if days_remaining <= 0:
            return 0.0
        if days_remaining >= decay_window_days:
            return 1.0
        return max(0.0, min(1.0, days_remaining / decay_window_days))

    def _lifecycle_factor(self, event: PolicyEvent, today: date) -> float:
        """Tong hop vong doi chinh sach: f_lifecycle = f_transmission * f_decay.

        - f_transmission: LAW-009 build-up tu effective_date.
        - f_decay:        Policy Cliff phase-out truoc expiry_date.
        """
        effective = datetime.strptime(event.effective_date, "%Y-%m-%d").date()
        expiry = datetime.strptime(event.expiry_date, "%Y-%m-%d").date()
        elapsed = (today - effective).days
        days_remaining = (expiry - today).days
        f_trans = self._transmission_factor(elapsed, event.half_life_days)
        f_decay = self._decay_factor(days_remaining, event.decay_window_days)
        return f_trans * f_decay

    # ── Core compute ─────────────────────────────────────────────────
    def compute_impact(self, symbol: str, target_date: str | None = None) -> PolicyImpactResult:
        """Tinh tac dong tong hop cua cac policy event len symbol."""
        target_date = target_date or date.today().isoformat()
        result = PolicyImpactResult(symbol=symbol, date=target_date)
        active = self.get_active_events(target_date)
        if not active:
            return result

        result.cluster = self.cluster_for(symbol)
        today = datetime.strptime(target_date, "%Y-%m-%d").date()

        for evt in active:
            benefit = evt.get_benefit_ratio(symbol)
            if benefit <= 0:
                continue
            # Policy Decay Audit: f_lifecycle = f_transmission * f_decay
            life = self._lifecycle_factor(evt, today)
            if life <= 0:
                continue
            f_decay = self._decay_factor(
                (datetime.strptime(evt.expiry_date, "%Y-%m-%d").date() - today).days,
                evt.decay_window_days,
            )

            event_impact = 0.0
            delta = evt.delta_params

            if evt.event_type == EVENT_TYPE_KBNN_LDR:
                ldr_relief = delta.get("ldr_relief_bps", 0.0) * benefit * life
                cof_relief = delta.get("cof_relief_bps", 0.0) * benefit * life
                interbank_shock = delta.get("interbank_shock_pct", 0.0) * benefit * life
                nim_boost = delta.get("nim_boost_bps", 0.0) * benefit * life
                result.ldr_relief_bps += ldr_relief
                result.cof_relief_bps += cof_relief
                result.interbank_shock += interbank_shock
                result.nim_boost += nim_boost
                # Weight: LDR relief la tin hieu tich cuc nhat -> 0.6
                event_impact = 0.6 * (ldr_relief / 500.0 if delta.get("ldr_relief_bps") else 0.0)
                event_impact += 0.25 * nim_boost / 10.0 if delta.get("nim_boost_bps") else 0.0
                event_impact += 0.15 * interbank_shock / 1.0 if delta.get("interbank_shock_pct") else 0.0

            elif evt.event_type == EVENT_TYPE_NPL_EXTENSION:
                # Gia han nhom no: giam phan tich luong, duong nhu khong dat
                # > 0 neu ho tro: benefit * life, cap tai +0.5
                event_impact = 0.5 * benefit * life

            elif evt.event_type == EVENT_TYPE_ROOM_INCREASE:
                room_boost = delta.get("room_boost_pct", 0.0) * benefit * life
                event_impact = 0.7 * room_boost / 5.0

            elif evt.event_type == EVENT_TYPE_RATE_CUT:
                rate_cut = delta.get("rate_cut_pct", 0.0) * benefit * life
                event_impact = 0.4 * rate_cut / 0.5

            elif evt.event_type == EVENT_TYPE_RRR_CHANGE:
                rrr = delta.get("rrr_change_pct", 0.0) * benefit * life
                event_impact = 0.3 * rrr / 1.0

            # Tich luy gate relaxation (neu co)
            for gate_var, threshold in delta.get("gate_relaxation", {}).items():
                relaxed = threshold * benefit * life
                result.gate_relaxation[f"{evt.id}:{gate_var}"] = relaxed

            result.total_impact_score += max(-1.0, min(1.0, event_impact))
            result.active_events.append(
                {
                    "id": evt.id,
                    "title": evt.title,
                    "event_type": evt.event_type,
                    "benefit_ratio": benefit,
                    "transmission_factor": round(life, 4),
                    "decay_factor": round(f_decay, 4),
                    "impact_score": round(max(-1.0, min(1.0, event_impact)), 4),
                }
            )

        result.total_impact_score = max(-1.0, min(1.0, result.total_impact_score))
        # Transmission tong hop: lay transmission cao nhat trong cac event active
        if result.active_events:
            result.transmission_factor = max(e["transmission_factor"] for e in result.active_events)
        return result

    # ── Gate application ─────────────────────────────────────────────
    def apply_to_gate(self, symbol: str, base_gate_result: bool, target_date: str | None = None) -> bool:
        """Ap dung chinh sach len VN20 Gate.

        Quy tac: Neu co policy event nao co gate_relaxation cho symbol
        (thuoc Cluster A/B), nang ket qua gate len PASS (neu hien tai FAIL).
        Cluster C khong bao gio duoc noi long.
        """
        impact = self.compute_impact(symbol, target_date)
        if impact.cluster == "OTHER":
            return base_gate_result
        if impact.gate_relaxation and not base_gate_result:
            logger.info(
                "PolicyImpact: noi long gate cho %s (%s) — %.2f",
                symbol,
                impact.cluster,
                impact.total_impact_score,
            )
            return True
        return base_gate_result


def compute_policy_impact(symbol: str, target_date: str | None = None, events_path: Path | None = None) -> PolicyImpactResult:
    """Convenience function — tinh policy impact cho mot symbol."""
    engine = PolicyImpactEngine(events_path=events_path)
    return engine.compute_impact(symbol, target_date)
