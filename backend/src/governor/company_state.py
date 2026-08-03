"""company_state.py — P3 Governor v3 Bayesian Decision Framework.

WHY: Replaces hard-coded IF/THEN with 8-node Bayesian Weight-of-Evidence.
      Giai đoạn 7 (ModelRegistry BMA) adds competing-hypothesis fusion at
      0.15 weight, penalising P(Gain) when M1_MACRO dominates (macro stress).

Evidence nodes (v3):
  P0: MacroState          P1: TransmissionState    P1: SectorState
  P2: HealthArchetype     L3: ValuationZone        L4: BehaviorZone
  GĐ4: CapitalAllocation  GĐ7: ModelRegistry BMA

Output layers (CSI Inverted Pyramid):
  Tầng 1: verdict + 3 core reasons (end-investor reads first)
  Tầng 2: action ranking table with human-readable business status
  Tầng 3: developer audit trail (BMA weights, EU ranking, context)
"""

import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────────────
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

FINANCIAL_DB = DATA_DIR / "financial_facts.db"
SCREENER_DB = DATA_DIR / "screener_cache.db"
MACRO_DIR = DATA_DIR / "macro"

# =========================================================================
# 1. BAYESIAN INFERENCE — Weight of Evidence
# =========================================================================

# ── Prior ──────────────────────────────────────────────────────
# Long-run fraction of up-days on VNINDEX ~53%
PRIOR_PROB_GAIN = 0.53
PRIOR_ODDS = PRIOR_PROB_GAIN / (1.0 - PRIOR_PROB_GAIN)


def _symbol_sector(symbol: str) -> Optional[str]:
    """Resolve per-symbol ICB sector (icb_name2) — NHẤT QUÁN với
    SectorStateEngine._load_icb_mapping().

    WHY: Trước đây print_report chỉ in top_sector của TOÀN THỊ TRƯỜNG
    (rotation chain) trong Context line — khi chạy --symbols BCM lại hiện
    "Viễn thông" (top sector market), khiến operator tưởng BCM thuộc ngành
    Viễn thông. Hàm này trả sector THẬT của từng symbol. Fallback cuối:
    map archetype → sector (REAL_ESTATE_DEVELOPER → "Bất động sản"),
    KHÔNG bao giờ trả sector market chung làm sector per-symbol.
    """
    try:
        conn = sqlite3.connect(str(SCREENER_DB))
        cur = conn.cursor()
        cur.execute(
            "SELECT icb_name2 FROM symbol_industry WHERE symbol = ? AND icb_name2 IS NOT NULL",
            (symbol.upper(),),
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return row[0].strip()
    except Exception:
        pass
    # Fallback: archetype → sector (chỉ cho symbol ĐÃ BIẾT trong BASELINE_MAP.
    # _classify_by_ratios nay đã ICB-aware (BĐS → REAL_ESTATE_DEVELOPER),
    # nhưng các archetype khác vẫn có thể đoán sai sector cho symbol lạ.)
    try:
        from src.business.archetype import BASELINE_MAP, ArchetypeEngine

        if symbol.upper().strip() not in BASELINE_MAP:
            return None
        arch = ArchetypeEngine().classify(symbol.upper())
        arch_name = getattr(arch, "archetype", str(arch))
        if arch_name == "REAL_ESTATE_DEVELOPER":
            return "Bất động sản"
        if arch_name == "REIT_COMMERCIAL":
            return "Bất động sản"
        if arch_name in ("FRANCHISE_BANK", "ASSET_BANK"):
            return "Ngân hàng"
        if arch_name == "RETAIL_PLATFORM":
            return "Bán lẻ"
        if arch_name == "TECHNOLOGY":
            return "Công nghệ Thông tin"
    except Exception:
        pass
    return None


# Archetype-aware priors (Giai đoạn 1 + Giai đoạn 4 insights)
# COMPOUNDERs have structural ROIC > WACC → higher baseline odds
# CYCLICAL_HEAVY and REAL_ESTATE have earnings risk → lower baseline
PRIOR_BY_ARCHETYPE = {
    "COMPOUNDER": 0.58,
    "FRANCHISE_BANK": 0.55,
    "STEADY_EARNER": 0.54,
    "REGULATED_UTILITY": 0.52,
    "RETAIL_PLATFORM": 0.50,
    "REIT_COMMERCIAL": 0.48,
    "ASSET_BANK": 0.48,
    "EXPORT_MANUFACTURER": 0.47,
    "CYCLICAL_HEAVY": 0.45,
    "REAL_ESTATE_DEVELOPER": 0.42,
    "UNKNOWN": 0.53,
}

# ── Evidence weights v4 (9 nodes, sum = 1.0) ──────────────────
# WHY v3→v4 shift: recovery_authenticity (#9) added at 0.08; all
# prior nodes reduced proportionally (×0.92 factor) to keep sum=1.0.
# Node #9 fuses RDS (relative demand), ΔBreadth_5D momentum and
# ΔCredit shift to validate whether a bounce is genuine or a
# dead-cat / liquidity-driven trap.
EVIDENCE_WEIGHTS = {
    "macro": 0.18,
    "transmission": 0.12,
    "sector": 0.09,
    "health": 0.10,
    "capital_allocation": 0.12,
    "valuation": 0.08,
    "behavior": 0.08,
    "model_registry": 0.15,
    "recovery_authenticity": 0.08,
}

# ── Likelihood Ratios ─────────────────────────────────────────
# LR > 1 → gain more likely; LR < 1 → gain less likely

LR_MACRO = {
    "CREDIT_STRESS": 0.25,
    "AI_BOOM": 2.50,
    "LIQUIDITY_EXPANSION": 1.60,
    "INFLATION_SHOCK": 0.35,
    "RECOVERY": 1.80,
    "STABLE": 1.20,
    "RISK_OFF": 0.20,
    "PRE_CREDIT_EXPANSION": 1.40,
}

LR_TRANSMISSION = {
    "LIQUIDITY_TRAP": 0.45,
    "CREDIT_CRUNCH": 0.15,
    "HEALTHY_TRANSMISSION": 1.80,
    "OVERHEATING": 0.60,
    "RISK_OFF_FLIGHT": 0.25,
    "FRAGILE_STABILITY": 0.70,
}

LR_SECTOR = {
    "EARLY": 1.40,
    "MID": 1.20,
    "LATE": 0.60,
    "WEAKENING": 0.35,
    "NEUTRAL": 1.00,
}

LR_HEALTH = {
    "HIGH_QUALITY_COMPOUNDER": 1.70,
    "STEADY_EARNER": 1.30,
    "CYCLICAL": 0.80,
    "DISTRESSED": 0.15,
    "TURNAROUND": 0.90,
    "LOW_QUALITY": 0.35,
}

LR_VALUATION = {
    "ULTRA_CHEAP": 1.60,
    "CHEAP": 1.40,
    "FAIR": 1.00,
    "EXPENSIVE": 0.60,
    "ULTRA_EXPENSIVE": 0.30,
}

# ── MoS Zone taxonomy (CSI v2 — Absolute Value First) ───────────
# WHY: MoS (Margin of Safety) from FairMultipleEngine is the PRIMARY
#   valuation signal. Z-Score is demoted to secondary context tag.
#   LR mapping converts MOS zone → internal LR_VALUATION key so
#   the Bayesian inference uses the correct likelihood ratio.
MOS_ZONE_UNDERVALUED = "MOS_UNDERVALUED"  # MoS > +20%
MOS_ZONE_FAIR_VALUE = "MOS_FAIR_VALUE"  # MoS 0% to +20%
MOS_ZONE_OVERVALUED = "MOS_OVERVALUED"  # MoS < 0%
MOS_ZONE_NO_DATA = "MOS_NO_DATA"

# Map MOS zone → internal LR_VALUATION key (for compute_gain_probability)
MOS_TO_LR_KEY = {
    MOS_ZONE_UNDERVALUED: "CHEAP",
    MOS_ZONE_FAIR_VALUE: "FAIR",
    MOS_ZONE_OVERVALUED: "EXPENSIVE",
    MOS_ZONE_NO_DATA: "FAIR",  # fallback
}

MOS_ZONE_THRESHOLDS = [
    (MOS_ZONE_UNDERVALUED, 20.0),  # MoS > +20% → HẤP DẪN
    (MOS_ZONE_FAIR_VALUE, 0.0),  # MoS >= 0% → HỢP LÝ
    (MOS_ZONE_OVERVALUED, float("-inf")),  # MoS < 0% → QUÁ GIÁ
]


def _compute_mos_zone(mos_pct: Optional[float]) -> str:
    """Map MoS % to zone. MoS is PRIMARY valuation signal (CSI v2)."""
    if mos_pct is None:
        return MOS_ZONE_NO_DATA
    for zone, threshold in MOS_ZONE_THRESHOLDS:
        if mos_pct > threshold:
            return zone
    return MOS_ZONE_OVERVALUED


def _format_market_context_tag(val: dict) -> str:
    """Format Z-Score context as secondary tag.

    CSI v2: Z-Score is demoted to a Market Context Tag.
    Tag format: "{mode}: {label}"
      mode = TS / CS / Peer
      label = Premium / Discount / Fair
    """
    tsz = val.get("overall_zone_ts", "NO_DATA")
    csz = val.get("overall_zone", "NO_DATA")
    has_ts = val.get("has_ts", False)
    # Determine which mode is active (TS preferred, fallback CS)
    if has_ts and tsz != "NO_DATA":
        mode = "TS"
        base_zone = tsz
    else:
        mode = "CS"
        base_zone = csz
    # Map zone to Premium/Discount label
    if base_zone in ("ULTRA_EXPENSIVE", "EXPENSIVE"):
        label = "Phí trội (Premium)"
    elif base_zone in ("ULTRA_CHEAP", "CHEAP"):
        label = "Chiết khấu (Discount)"
    else:
        label = "Hợp lý (Market Fair)"
    return f"{mode}: {label}"


LR_BEHAVIOR = {
    "IN_VA_DEMAND": 1.60,
    "IN_VA": 1.15,
    "BELOW_VA_DEMAND": 1.30,
    "BELOW_VA": 0.85,
    "ABOVE_VA_DEMAND": 0.70,
    "ABOVE_VA": 0.50,
    # P1: decision fusion modifiers
    "IN_VA_DEMAND_LOCKDOWN": 0.60,
    "IN_VA_LOCKDOWN": 0.45,
    "BELOW_VA_LOCKDOWN": 0.40,
    "ABOVE_VA_LOCKDOWN": 0.25,
    "ABOVE_VA_DEMAND_LOCKDOWN": 0.35,
    "BELOW_VA_DEMAND_LOCKDOWN": 0.55,
}

# ── Recovery Authenticity LR (Node #9) ─────────────────────────
# Validates whether a bounce is genuine (RDS + ΔBreadth + ΔCredit)
# or a liquidity-driven dead-cat / bull trap.
LR_RECOVERY_AUTHENTICITY = {
    # Combo states keyed "<relative_demand>|<breadth_momentum>|<credit_shift>"
    "HIGH|POSITIVE|EASING": 1.60,  # active demand + broad + credit easing → genuine
    "HIGH|POSITIVE|TIGHT": 1.25,
    "HIGH|NEUTRAL|EASING": 1.30,
    "HIGH|NEUTRAL|TIGHT": 1.00,
    "HIGH|NEGATIVE|EASING": 1.10,
    "HIGH|NEGATIVE|TIGHT": 0.70,
    "LOW|POSITIVE|EASING": 0.90,
    "LOW|POSITIVE|TIGHT": 0.55,
    "LOW|NEUTRAL|EASING": 0.70,
    "LOW|NEUTRAL|TIGHT": 0.40,
    "LOW|NEGATIVE|EASING": 0.50,
    "LOW|NEGATIVE|TIGHT": 0.30,
    "NEUTRAL": 1.00,
}


def compute_recovery_authenticity_lr(
    relative_demand: float = 1.0,
    breadth_momentum_5d: float = 0.0,
    credit_shift: float = 0.0,
) -> float:
    """Compute Node #9 likelihood ratio from the 3 dynamic indicators.

    RDS (Relative Demand):   vol_up / vol_down * vol_20d / vol_60d
    ΔBreadth_5D:             B_20(t) - B_20(t-5)
    ΔCredit:                 CSI(t) - CSI(t-10)

    Per the domain formulation:
      RDS < 0.8 → weak demand (LR penalty); RDS > 1.2 → strong demand
      ΔBreadth < 0 → green-shell-red-core (penalty); > +15% → broad
      ΔCredit > 0 → liquidity still tightening; ≤ 0 → easing
    """
    if relative_demand > 1.2:
        demand = "HIGH"
    elif relative_demand < 0.8:
        demand = "LOW"
    else:
        demand = "NEUTRAL"

    if breadth_momentum_5d > 15.0:
        breadth = "POSITIVE"
    elif breadth_momentum_5d < 0.0:
        breadth = "NEGATIVE"
    else:
        breadth = "NEUTRAL"

    if credit_shift <= 0.0:
        credit = "EASING"
    else:
        credit = "TIGHT"

    key = f"{demand}|{breadth}|{credit}"
    return LR_RECOVERY_AUTHENTICITY.get(key, LR_RECOVERY_AUTHENTICITY["NEUTRAL"])


# ── Capital Allocation LR (Giai đoạn 4) ───────────────────────
LR_CAPITAL_ALLOCATION = {
    "VALUE_CREATOR": 1.60,
    "EFFICIENT_ALLOCATOR": 1.30,
    "CAPITAL_HOARDER": 0.85,
    "LEVERAGED_OPTIMIZER": 0.70,
    "TRANSITIONAL": 0.95,
    "VALUE_DESTROYER": 0.20,
}


# ── ModelRegistry LR (Giai đoạn 7) ───────────────────────────
# LR = 1.0 - 0.5 * P(M1_MACRO | D, context)
# WHY: When M1_MACRO dominates (e.g. 0.48 under CREDIT_STRESS),
#      LR=0.76 < 1 → penalises P(Gain) because macro-driven markets
#      are harder to predict. When micro models dominate (STABLE),
#      LR→1.0 (neutral). Clamped [0.30, 1.20] to avoid extreme.
#      Linear slope 0.5 chosen so 50% M1 weight → LR=0.75 (moderate).
def compute_model_registry_lr(bma_posterior: dict) -> float:
    """Compute LR from BMA posterior weights.

    Higher M1_MACRO posterior = macro-driven uncertainty → penalize (LR < 1).
    Higher M2/M3 posterior = micro/behavior-driven → neutral/boost (LR ~ 1).
    """
    m1_w = float(bma_posterior.get("M1_MACRO", 0.333))
    return max(0.30, min(1.20, 1.0 - 0.5 * m1_w))


def _lookup_lr(table: dict, key: str, default: float = 1.0) -> float:
    """Safe LR lookup with logging-unfriendly fallback."""
    return table.get(str(key).strip().upper(), default)


def compute_gain_probability(
    macro_state: str,
    transmission_phase: str,
    sector_phase: str,
    health_archetype: str,
    valuation_zone: str,
    behavior_position: str,
    capital_allocation: str = "TRANSITIONAL",
    macro_entropy: float = 0.0,
    transmission_credit: float = 50.0,
    archetype_prior_key: str = "UNKNOWN",
    lr_macro_override: Optional[float] = None,
    evidence_weights: Optional[Dict[str, float]] = None,
    model_registry_lr: Optional[float] = None,
    lr_val_override: Optional[float] = None,
    recovery_authenticity_lr: Optional[float] = None,
) -> Tuple[float, float, float]:
    """Bayesian Weight-of-Evidence v3 → P(Gain | Evidence).

    LAW-004: accepts evidence_weights dict from EvidenceEngine
    for dynamically weighted log-LR fusion.

    Giai đoạn 7: model_registry_lr from BMA competition posterior.

    So với v1:
      - Thêm capital_allocation node (Giai đoạn 4)
      - Archetype-aware prior (Giai đoạn 1 + 4)
      - dynamic LR macro override (Giai đoạn 2)
      - dynamic evidence weights (LAW-004)
      - model_registry BMA evidence (Giai đoạn 7)

    Returns:
      (posterior_prob, log_posterior_odds, calibration_penalty)
    """
    # Archetype-aware prior
    prior_prob = PRIOR_BY_ARCHETYPE.get(archetype_prior_key, PRIOR_PROB_GAIN)
    prior_odds = prior_prob / (1.0 - prior_prob)

    # Dynamic evidence weights (LAW-004) or fallback to static
    w = evidence_weights if evidence_weights is not None else EVIDENCE_WEIGHTS

    # Dynamic LR override from Factor Exposure Matrix (Giai đoạn 2)
    lr_macro = lr_macro_override if lr_macro_override is not None else _lookup_lr(LR_MACRO, macro_state)
    lr_trans = _lookup_lr(LR_TRANSMISSION, transmission_phase)
    lr_sector = _lookup_lr(LR_SECTOR, sector_phase)
    lr_health = _lookup_lr(LR_HEALTH, health_archetype)
    lr_val = lr_val_override if lr_val_override is not None else _lookup_lr(LR_VALUATION, valuation_zone)
    lr_beh = _lookup_lr(LR_BEHAVIOR, behavior_position)
    lr_cap = _lookup_lr(LR_CAPITAL_ALLOCATION, capital_allocation)
    lr_model = model_registry_lr if model_registry_lr is not None else 1.0

    lr_recovery = (
        recovery_authenticity_lr if recovery_authenticity_lr is not None else _lookup_lr(LR_RECOVERY_AUTHENTICITY, "NEUTRAL")
    )

    log_prior = math.log(prior_odds)
    log_lr = (
        w.get("macro", EVIDENCE_WEIGHTS["macro"]) * math.log(max(lr_macro, 0.01))
        + w.get("transmission", EVIDENCE_WEIGHTS["transmission"]) * math.log(max(lr_trans, 0.01))
        + w.get("sector", EVIDENCE_WEIGHTS["sector"]) * math.log(max(lr_sector, 0.01))
        + w.get("health", EVIDENCE_WEIGHTS["health"]) * math.log(max(lr_health, 0.01))
        + w.get("capital_allocation", EVIDENCE_WEIGHTS["capital_allocation"]) * math.log(max(lr_cap, 0.01))
        + w.get("valuation", EVIDENCE_WEIGHTS["valuation"]) * math.log(max(lr_val, 0.01))
        + w.get("behavior", EVIDENCE_WEIGHTS["behavior"]) * math.log(max(lr_beh, 0.01))
        + w.get("model_registry", EVIDENCE_WEIGHTS["model_registry"]) * math.log(max(lr_model, 0.01))
        + w.get("recovery_authenticity", EVIDENCE_WEIGHTS["recovery_authenticity"]) * math.log(max(lr_recovery, 0.01))
    )

    log_posterior_odds = log_prior + log_lr
    posterior_prob = 1.0 / (1.0 + math.exp(-log_posterior_odds))

    # Calibration penalty based on macro entropy + transmission credit
    entropy_penalty = max(0.0, min(1.0, macro_entropy / 3.0))
    credit_penalty = max(0.0, min(1.0, (50.0 - transmission_credit) / 50.0))
    calibration_penalty = 0.5 * entropy_penalty + 0.5 * credit_penalty

    return posterior_prob, log_posterior_odds, calibration_penalty


# =========================================================================
# 2. EXPECTED UTILITY MATRIX
# =========================================================================

# Rows: 7 actions. Cols: [U(Gain), U(No Gain)]
# Domain expert calibration:
#   VETO/AVOID → conservative (penalize missing gains, reward avoiding losses)
#   OPEN/SCALE_IN → aggressive (reward gains, penalize losses)
#   HOLD/WAIT → neutral
#   REDUCE → moderately defensive
UTILITY_MATRIX = {
    "VETO": [-1.0, 0.9],
    "AVOID": [-0.8, 0.7],
    "REDUCE": [-0.3, 0.6],
    "WAIT": [0.0, 0.0],
    "HOLD": [0.3, -0.3],
    "SCALE_IN": [0.7, -0.6],
    "OPEN": [1.0, -1.0],
}


def compute_expected_utilities(p_gain: float) -> List[Tuple[str, float]]:
    """Return (action, EU) sorted descending."""
    p_loss = 1.0 - p_gain
    results = []
    for action, (u_gain, u_loss) in UTILITY_MATRIX.items():
        eu = p_gain * u_gain + p_loss * u_loss
        results.append((action, eu))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def pick_best_action(eu_list: List[Tuple[str, float]]) -> Tuple[str, float]:
    """Highest-EU action. Ties broken by priority."""
    priority = ["VETO", "AVOID", "REDUCE", "WAIT", "HOLD", "SCALE_IN", "OPEN"]
    best = eu_list[0][0]
    best_eu = eu_list[0][1]
    for action, eu in eu_list:
        if abs(eu - best_eu) < 0.01:
            # Tie → pick lower-priority (more decisive) action
            if priority.index(action) > priority.index(best):
                best = action
        elif eu > best_eu:
            best = action
            best_eu = eu
    return best, best_eu


# =========================================================================
# 3. KELLY CRITERION POSITION SIZING
# =========================================================================

# Assumed win/loss ratio for Vietnamese equities
# Typical: upside 20%, downside 10% → b = 2.0
KELLY_B = 2.0

# Maximum allowable allocation per symbol
MAX_ALLOC_PCT = 20.0


def kelly_allocation(p_gain: float, calibration_penalty: float, macro_entropy: float) -> float:
    """Compute Kelly-optimal allocation, scaled by uncertainty."""
    p_loss = 1.0 - p_gain
    # Kelly fraction: f* = (p * b - q) / b
    kelly_f = (p_gain * KELLY_B - p_loss) / KELLY_B
    kelly_f = max(0.0, min(kelly_f, 0.25))  # cap at 25% of capital

    # Scale by calibration penalty
    scaled = kelly_f * (1.0 - calibration_penalty)
    # Additional entropy penalty if macro is highly uncertain
    if macro_entropy > 2.0:
        entropy_scale = max(0.0, 1.0 - (macro_entropy - 2.0) / 3.0)
        scaled *= entropy_scale

    return round(min(scaled * 100, MAX_ALLOC_PCT), 1)


# =========================================================================
# 4. DATA LOADERS (L2, L3, L4 — existing infrastructure)
# =========================================================================


def _safe_div(a, b):
    if b is None or b == 0:
        return None
    try:
        return a / b
    except ZeroDivisionError, TypeError:
        return None


class L2HealthLoader:
    """Legacy health loader — kept for base ratio access."""

    def __init__(self):
        self.conn = sqlite3.connect(str(FINANCIAL_DB))

    def get_latest_ratios(self, symbol: str) -> Dict:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT ratio_name, ratio_value, interpretation
            FROM health_ratios
            WHERE symbol = ? AND period = (
                SELECT MAX(period) FROM health_ratios WHERE symbol = ?
            )
        """,
            (symbol.upper(), symbol.upper()),
        )
        rows = cur.fetchall()
        return {r[0]: {"value": r[1], "interpretation": r[2]} for r in rows}

    def close(self):
        self.conn.close()


class L3ValuationLoader:
    """Layer 3 — Valuation Z-Scores."""

    def __init__(self):
        self.conn = sqlite3.connect(str(FINANCIAL_DB))

    def get_latest_valuation(self, symbol: str) -> Dict:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT ratio_name, ratio_value, z_score, percentile, zone,
                   COALESCE(z_score_peer, z_score) AS z_score_peer,
                   COALESCE(zone_peer, zone) AS zone_peer,
                   peer_group,
                   z_score_ts, zone_ts, mean_5y, std_5y, count_5y
            FROM valuation_scores
            WHERE symbol = ? AND period = (
                SELECT MAX(period) FROM valuation_scores WHERE symbol = ?
            )
            ORDER BY ratio_name
        """,
            (symbol.upper(), symbol.upper()),
        )
        rows = cur.fetchall()
        return {
            r[0]: {
                "value": r[1],
                "z_score": r[2],
                "percentile": r[3],
                "zone": r[4],
                "z_score_peer": r[5],
                "zone_peer": r[6],
                "peer_group": r[7],
                "z_score_ts": r[8],
                "zone_ts": r[9],
                "mean_5y": r[10],
                "std_5y": r[11],
                "count_5y": r[12],
            }
            for r in rows
        }

    def score_valuation(self, symbol: str) -> Dict:
        vals = self.get_latest_valuation(symbol)
        if not vals:
            return {
                "score": 0,
                "grade": "NO_DATA",
                "overall_zone": "NO_DATA",
                "overall_zone_peer": "NO_DATA",
                "overall_zone_ts": "NO_DATA",
                "lowest_z": 0,
                "peer_group": None,
                "raw_values": {},
            }

        zone_scores = {"ULTRA_CHEAP": 2, "CHEAP": 1, "FAIR": 0, "EXPENSIVE": -1, "ULTRA_EXPENSIVE": -2}
        z_scores = []
        z_scores_peer = []
        z_scores_ts = []
        scores = []
        peer_group = None
        raw_values = {}
        has_ts = False
        for rname, rinfo in vals.items():
            z_scores.append(rinfo.get("z_score", 0))
            z_scores_peer.append(rinfo.get("z_score_peer", rinfo.get("z_score", 0)))
            scores.append(zone_scores.get(rinfo.get("zone", "FAIR"), 0))
            if rinfo.get("peer_group"):
                peer_group = rinfo["peer_group"]
            if rname in ("PE", "PB", "ROE", "PEG", "PB_TO_ROE"):
                raw_values[rname] = rinfo.get("value", None)
            # Time-series z-score
            zts = rinfo.get("z_score_ts")
            if zts is not None:
                z_scores_ts.append(zts)
                has_ts = True

        def _zone_from_avg(avg: float) -> str:
            if avg <= -1.5:
                return "ULTRA_CHEAP"
            if avg <= -0.5:
                return "CHEAP"
            if avg >= 1.5:
                return "ULTRA_EXPENSIVE"
            if avg >= 0.5:
                return "EXPENSIVE"
            return "FAIR"

        avg_z = sum(z_scores) / len(z_scores) if z_scores else 0
        avg_z_peer = sum(z_scores_peer) / len(z_scores_peer) if z_scores_peer else 0
        avg_z_ts = sum(z_scores_ts) / len(z_scores_ts) if z_scores_ts else None

        result = {
            "score": round(sum(scores) / len(scores), 2) if scores else 0,
            "grade": _zone_from_avg(avg_z),
            "overall_zone": _zone_from_avg(avg_z),
            "overall_zone_peer": _zone_from_avg(avg_z_peer),
            "overall_zone_ts": _zone_from_avg(avg_z_ts) if avg_z_ts is not None else "NO_DATA",
            "lowest_z": round(min(z_scores), 2) if z_scores else 0,
            "peer_group": peer_group,
            "raw_values": raw_values,
            "has_ts": has_ts,
        }
        # architectural rule: when time-series data exists, use TS zone as primary evidence
        # for the Bayesian Governor (time-series = intrinsic valuation, cross-sectional = fallback)
        if has_ts and avg_z_ts is not None:
            result["overall_zone"] = _zone_from_avg(avg_z_ts)
        return result

    def close(self):
        self.conn.close()


class L4BehaviorLoader:
    """Layer 4 — Market Behavior (Volume Profile + Active Demand)."""

    def __init__(self):
        self.conn = sqlite3.connect(str(FINANCIAL_DB))

    def get_volume_profile(self, symbol: str) -> Optional[Dict]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT price_current, poc, vah, val, volume_ratio,
                   price_ma20, price_ma50
            FROM volume_profile
            WHERE symbol = ?
            ORDER BY date DESC LIMIT 1
        """,
            (symbol.upper(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "price": row[0],
            "poc": row[1],
            "vah": row[2],
            "val": row[3],
            "volume_ratio": row[4],
            "ma20": row[5],
            "ma50": row[6],
        }

    def get_active_demand_count(self, symbol: str, days: int = 20) -> int:
        cur = self.conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cur.execute(
            """
            SELECT COUNT(*) FROM active_demand
            WHERE symbol = ? AND date >= ?
        """,
            (symbol.upper(), cutoff),
        )
        return cur.fetchone()[0]

    def score_behavior(self, symbol: str, fusion_action: str = "") -> Dict:
        """Score behavior from Volume Profile + optional decision fusion context.

        WHY fusion_action (P1 bridge):
          decision_fusion.arbitrate() output enriches the Volume Profile
          position. When fusion says EXECUTE (consensus/trend/mean-reversion
          confirmed), we append "_FUSION" to position → higher LR.
          When fusion says FORCE_CASH (macro crisis lockdown), we append
          "_LOCKDOWN" → lower LR. This integrates Model A/B arbitration
          into L4_BEHAVIOR without duplicating the policy matrix.
        """
        vp = self.get_volume_profile(symbol)
        if not vp:
            return {"score": 0, "grade": "NO_DATA", "position": "UNKNOWN", "signals": 0}

        price = vp["price"]
        val = vp["val"]
        vah = vp["vah"]
        vol_ratio = vp["volume_ratio"]
        signals = self.get_active_demand_count(symbol)

        if price < val:
            position = "BELOW_VA"
        elif price > vah:
            position = "ABOVE_VA"
        else:
            position = "IN_VA"

        if signals > 0:
            position += "_DEMAND"

        if position.startswith("IN_VA_DEMAND"):
            score = 2.0
        elif position.startswith("IN_VA"):
            score = 1.0
        elif position == "BELOW_VA_DEMAND":
            score = 0.5
        elif position == "BELOW_VA":
            score = -0.5
        elif position == "ABOVE_VA_DEMAND":
            score = 0.0
        else:
            score = -1.0

        if vol_ratio >= 1.5 and position.startswith("IN_VA"):
            score += 0.5

        if score >= 1.5:
            grade = "STRONG"
        elif score >= 0.0:
            grade = "NEUTRAL"
        else:
            grade = "WEAK"

        # P1: enrich position with decision fusion context
        if fusion_action == "EXECUTE" and "_DEMAND" not in position:
            position += "_DEMAND"  # upgrade: pretend active demand confirmed
        elif fusion_action == "FORCE_CASH":
            position += "_LOCKDOWN"  # downgrade: crisis lockdown overrides

        return {
            "score": round(score, 2),
            "grade": grade,
            "position": position,
            "signals": signals,
            "vol_ratio": round(vol_ratio, 2),
        }

    def close(self):
        self.conn.close()


# =========================================================================
# 5. PERCEPTION LAYER LOADERS (P0, P1, P2)
# =========================================================================


class PerceptionLoader:
    """Load P0/P1/P2 states for Bayesian inference."""

    def load_macro_state(self) -> dict:
        """Load latest P0 MacroState from persistence."""
        try:
            path = MACRO_DIR / "macro_state_history.json"
            if not path.exists():
                return {"state": "STABLE", "posterior": 0.5, "entropy": 1.5}
            with open(path, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not history:
                return {"state": "STABLE", "posterior": 0.5, "entropy": 1.5}
            latest = history[-1]
            return {
                "state": latest.get("macro_state", "STABLE"),
                "posterior": latest.get("posterior", 0.5),
                "entropy": latest.get("entropy", 1.5),
            }
        except Exception:
            return {"state": "STABLE", "posterior": 0.5, "entropy": 1.5}

    def load_transmission(self) -> dict:
        """Load latest P1 TransmissionState from persistence."""
        try:
            path = MACRO_DIR / "transmission_history.json"
            if not path.exists():
                return {"phase": "FRAGILE_STABILITY", "liquidity": 50, "credit": 50, "confidence": 50}
            with open(path, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not history:
                return {"phase": "FRAGILE_STABILITY", "liquidity": 50, "credit": 50, "confidence": 50}
            latest = history[-1]
            return {
                "phase": latest.get("transmission_phase", "FRAGILE_STABILITY"),
                "liquidity": latest.get("liquidity", 50),
                "credit": latest.get("credit", 50),
                "confidence": latest.get("confidence", 50),
            }
        except Exception:
            return {"phase": "FRAGILE_STABILITY", "liquidity": 50, "credit": 50, "confidence": 50}

    def load_sector(self) -> dict:
        """Load latest P1 SectorRotationReport from persistence."""
        try:
            path = MACRO_DIR / "sector_rotation_latest.json"
            if not path.exists():
                return {"top_sector": "UNKNOWN", "n_healthy": 0, "chain": "NEUTRAL"}
            with open(path, "r", encoding="utf-8") as f:
                report = json.load(f)
            chain = report.get("rotation_chain", [])
            top_phase = chain[-1] if chain else "NEUTRAL"
            return {
                "top_sector": report.get("top_sector", "UNKNOWN"),
                "n_healthy": report.get("n_sectors_healthy", 0),
                "n_weak": report.get("n_sectors_weak", 19),
                "chain": chain,
                "top_phase": top_phase,
            }
        except Exception:
            return {"top_sector": "UNKNOWN", "n_healthy": 0, "chain": [], "top_phase": "NEUTRAL"}

    def load_health(self, symbol: str) -> dict:
        """Load P2 HealthLatentState for a symbol — dynamic import to avoid circular."""
        try:
            from src.financial.company_health_v2 import CompanyHealthV2

            engine = CompanyHealthV2()
            state = engine.analyze(symbol)
            if state is None:
                return {"archetype": "STEADY_EARNER", "vector": [0.5, 0.5, 0.5, 0.5, 0.5], "confidence": 0.5, "periods": 0}
            return {
                "archetype": state.archetype,
                "archetype_label": state.archetype_label,
                "vector": state.organs.vector,
                "confidence": state.archetype_confidence,
                "periods": state.data_periods,
            }
        except Exception:
            return {"archetype": "STEADY_EARNER", "vector": [0.5, 0.5, 0.5, 0.5, 0.5], "confidence": 0.5, "periods": 0}


# =========================================================================
# 6. BAYESIAN GOVERNOR ENGINE
# =========================================================================


@dataclass
class BayesianMandate:
    """Output of the P3 Bayesian Governor v2 for one symbol."""

    symbol: str
    action: str
    action_vn: str
    expected_utility: float
    p_gain: float
    calibration_penalty: float
    allocation_pct: float
    conviction: float
    macro_state: str
    transmission_phase: str
    sector_phase: str
    health_archetype: str
    valuation_zone: str
    valuation_zone_peer: str
    valuation_zone_ts: str
    behavior_position: str
    pe_raw: Optional[float] = None
    pb_raw: Optional[float] = None

    # FairMultipleEngine fields (absolute intrinsic valuation vs current price)
    fair_pe: Optional[float] = None
    fair_pb: Optional[float] = None
    margin_of_safety: Optional[float] = None
    fair_ke: Optional[float] = None
    fair_g: Optional[float] = None
    fair_sector: str = ""
    fair_status: str = ""

    # CSI v2 — MoS Zone (PRIMARY signal) + Market Context Tag (secondary)
    mos_zone: str = MOS_ZONE_NO_DATA
    market_context_tag: str = "NO_DATA"

    # v2 fields
    capital_allocation_archetype: str = ""
    capital_allocation_score: float = 0.0
    archetype_prior: str = "UNKNOWN"
    lr_macro_dynamic: float = 1.0
    contextual_health_score: float = 0.5
    eu_ranking: List[Tuple[str, float]] = field(default_factory=list)

    # Circuit breaker
    circuit_breaker_level: int = 0
    circuit_breaker_label: str = "BÌNH_THƯỜNG"
    circuit_breaker_trigger: str = ""

    # Giai đoạn 7: ModelRegistry BMA competition
    # WHY: bma_posterior caches P(M_k|D,context) once per batch →
    #      all symbols share the same BMA weights (market-level).
    #      model_registry_lr = f(bma_posterior) feeds into
    #      compute_gain_probability() as evidence node 8.
    bma_posterior: Dict[str, float] = field(default_factory=dict)
    dominant_model: str = ""
    model_registry_lr: float = 1.0


ACTION_VN = {
    "VETO": "Cấm tuyệt đối",
    "AVOID": "Tránh xa",
    "REDUCE": "Giảm vị thế",
    "WAIT": "Chờ đợi",
    "HOLD": "Nắm giữ",
    "SCALE_IN": "Tích lũy",
    "OPEN": "Mở vị thế",
}


class BayesianGovernor:
    """P3 Governor v3: Bayesian Expected Utility + Giai đoạn 1-7 integration.

    So với v2:
      - Archetype-aware prior (Giai đoạn 1)
      - Dynamic LR macro from Factor Exposure Matrix (Giai đoạn 2)
      - Contextual Health score (Giai đoạn 3)
      - Capital Allocation evidence node (Giai đoạn 4)
      - Dynamic evidence weights from EvidenceEngine (Giai đoạn 5 / LAW-004)
      - CausalEdge propagation (Giai đoạn 6 / Sprint 3)
      - ModelRegistry BMA competition evidence (Giai đoạn 7 / Sprint 4)
    """

    def __init__(self):
        self.perception = PerceptionLoader()
        self.valuation = L3ValuationLoader()
        self.behavior = L4BehaviorLoader()

        # FairMultipleEngine: absolute intrinsic valuation via Gordon Growth
        self._fair_engine = None

        # Giai đoạn 2: Factor Exposure
        self._factor_engine = None

        # Giai đoạn 3: Contextual Health
        self._context_engine = None

        # Giai đoạn 4: Capital Allocation
        self._capital_engine = None

        # Giai đoạn 7: ModelRegistry (BMA competition)
        self._model_registry = None
        self._bma_posterior = None
        self._dominant_model = None

        # Load market-level context once
        self._macro = self.perception.load_macro_state()
        self._transmission = self.perception.load_transmission()
        self._sector = self.perception.load_sector()

        # Circuit breaker cache (lazy-loaded once per instance)
        self._cb_state = None

    def _get_factor_engine(self):
        if self._factor_engine is None:
            from src.business.factor_exposure import FactorExposureEngine, compute_lr_adjustment

            self._factor_engine = FactorExposureEngine()
            self._compute_lr_adjust = compute_lr_adjustment
        return self._factor_engine

    def _get_context_engine(self):
        if self._context_engine is None:
            from src.financial.company_health import ContextualHealthEngine

            self._context_engine = ContextualHealthEngine()
        return self._context_engine

    def _get_capital_engine(self):
        if self._capital_engine is None:
            from src.business.capital_allocation import CapitalAllocationEngine

            self._capital_engine = CapitalAllocationEngine()
        return self._capital_engine

    def _get_archetype_prior(self, symbol: str) -> str:
        """Map symbol to archetype prior key via Giai đoạn 1 classification."""
        try:
            from src.business.archetype import ArchetypeEngine

            arch = ArchetypeEngine().classify(symbol)
            return arch.archetype if arch else "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _get_fair_engine(self):
        if self._fair_engine is None:
            from src.governor.fair_multiple_engine import compute_fair_multiple

            self._fair_engine = compute_fair_multiple
        return self._fair_engine

    def _get_model_registry(self):
        # WHY lazy-init: ModelRegistry opens calibration.db connection;
        #   delay until first assess() call to avoid cold-start penalty.
        if self._model_registry is None:
            from calibration.model_registry import ModelRegistry

            self._model_registry = ModelRegistry()
        return self._model_registry

    def _check_circuit_breaker(self):
        """Check calibration degradation and cache circuit breaker state.

        Chỉ check 1 lần per Governor instance (lazy-loaded). Kết quả được
        áp dụng cho mọi symbol trong cùng batch analyze().
        """
        if self._cb_state is not None:
            return self._cb_state
        try:
            from calibration.prediction_log import check_circuit_breaker_auto

            self._cb_state = check_circuit_breaker_auto(days=90)
        except Exception:
            self._cb_state = {"level": 0, "label": "BÌNH_THƯỜNG", "active": 0, "reason": "CHECK_FAILED"}
        return self._cb_state

    def assess(self, symbol: str) -> BayesianMandate:
        """Compute Bayesian mandate v2 for a single symbol."""
        # Per-symbol evidence
        health = self.perception.load_health(symbol)
        val = self.valuation.score_valuation(symbol)

        # ── Fair Multiple Engine (absolute intrinsic valuation) ──
        fair = {}
        try:
            raw = val.get("raw_values", {})
            roe_val = raw.get("ROE")
            pe_val = raw.get("PE")
            pb_val = raw.get("PB")
            if roe_val is None:
                try:
                    from src.financial.company_health_v2 import CompanyHealthV2

                    ch = CompanyHealthV2()
                    ratios = ch._load_ratios(self.valuation.conn, symbol)
                    roe_series = ratios.get("ROE")
                    if roe_series:
                        last_roe = roe_series[-1][1]
                        if last_roe is not None:
                            roe_val = last_roe * 4.0 if last_roe < 0.25 else last_roe
                except Exception:
                    pass
            if roe_val is not None and pe_val is not None and pb_val is not None:
                fair = self._get_fair_engine()(
                    symbol=symbol,
                    roe=roe_val / 100.0 if roe_val > 1 else roe_val,
                    pe_current=pe_val,
                    pb_current=pb_val,
                    archetype=health.get("archetype", "UNKNOWN"),
                )
        except Exception:
            pass

        # ── CSI v2: MoS Zone replaces Z-Score as PRIMARY valuation signal ──
        mos_zone = _compute_mos_zone(fair.get("margin_of_safety_pct"))
        market_context_tag = _format_market_context_tag(val)
        lr_val_override = None
        if mos_zone != MOS_ZONE_NO_DATA:
            lr_key = MOS_TO_LR_KEY.get(mos_zone, "FAIR")
            lr_val_override = _lookup_lr(LR_VALUATION, lr_key)
        val["_fair"] = fair
        val["_mos_zone"] = mos_zone
        val["_market_context_tag"] = market_context_tag
        val["_lr_val_override"] = lr_val_override

        # P1: decision_fusion bridge → L4_BEHAVIOR
        # WHY: arbitrate() uses REGIME_POLICY matrix to determine
        #   whether Model A (Momentum) and Model B (Mean Reversion)
        #   signals are valid in current regime. We derive per-symbol
        #   Model A/B signals from Volume Profile (VAH/VAL/vol_ratio)
        #   and feed fusion_action into score_behavior() as modifier.
        fusion_action = ""
        recov_vr = 1.0
        try:
            from src.portfolio.decision_fusion import arbitrate

            vp = self.behavior.get_volume_profile(symbol)
            if vp:
                price = vp["price"]
                vah = vp["vah"]
                val_vp = vp["val"]
                vr = vp["volume_ratio"]
                recov_vr = vr
                # Model A (Momentum) trigger: price in upper VA + volume expansion
                a_buy = price >= (val_vp + vah) / 2 and vr >= 1.3
                # Model B (Mean Reversion) trigger: price near/below VAL + contraction
                b_buy = price <= val_vp + (vah - val_vp) * 0.3 and vr <= 0.7
                _regime_map = {
                    "CREDIT_STRESS": "CRISIS",
                    "AI_BOOM": "TRENDING",
                    "LIQUIDITY_EXPANSION": "TRENDING",
                    "INFLATION_SHOCK": "CRISIS",
                    "RECOVERY": "RECOVERY",
                    "STABLE": "RANGING",
                    "RISK_OFF": "CRISIS",
                    "PRE_CREDIT_EXPANSION": "RECOVERY",
                }
                df_regime = _regime_map.get(self._macro.get("state", "STABLE"), "RANGING")
                fusion = arbitrate(
                    "TRIGGER_BUY" if a_buy else "NOBUY",
                    "TRIGGER_BUY" if b_buy else "NOBUY",
                    df_regime,
                )
                fusion_action = fusion.get("action", "")
        except Exception:
            pass
        beh = self.behavior.score_behavior(symbol, fusion_action=fusion_action)

        # ── Giai đoạn 1: Archetype-aware prior ──────────────
        arch_prior_key = self._get_archetype_prior(symbol)

        # ── Giai đoạn 2: Dynamic LR from Factor Exposure ────
        lr_macro_dynamic = 1.0
        try:
            factor_eng = self._get_factor_engine()
            matrix = factor_eng.compute(symbol)
            lr_mult = self._compute_lr_adjust(matrix, self._macro["state"], self._transmission["phase"])
            # Scale base LR by per-symbol multiplier
            lr_macro_dynamic = _lookup_lr(LR_MACRO, self._macro["state"]) * lr_mult
            lr_macro_dynamic = max(0.05, min(5.0, lr_macro_dynamic))
        except Exception:
            pass

        # ── Giai đoạn 3: Contextual Health ──────────────────
        contextual_health_score = 0.5
        try:
            ctx_eng = self._get_context_engine()
            ctx = ctx_eng.assess(symbol)
            if ctx:
                contextual_health_score = ctx.overall_score
        except Exception:
            pass

        # ── Giai đoạn 4: Capital Allocation ─────────────────
        capital_arch = "TRANSITIONAL"
        capital_score = 0.0
        try:
            cap_eng = self._get_capital_engine()
            cap = cap_eng.assess(symbol)
            if cap:
                capital_arch = cap.archetype
                capital_score = cap.quality_score
        except Exception:
            pass

        # Derive sector phase
        sector_phase = self._sector.get("top_phase", "NEUTRAL")

        # ── Giai đoạn 5: Dynamic evidence weights (LAW-004) ──
        dynamic_weights = None
        try:
            from calibration.evidence_engine import get_dynamic_evidence_weights

            _ms = self._macro.get("state", "STABLE")
            _sp = sector_phase
            _en = float(self._macro.get("entropy", 0.0))
            dw = get_dynamic_evidence_weights(_ms, _sp, _en)
            if dw:
                dynamic_weights = dw
        except Exception:
            pass

        # ── Giai đoạn 6: CausalEdge propagation (Sprint 3) ──
        _causal_conf = None
        _causal_lag = None
        try:
            from calibration.causal_edge import CausalGraph

            _arch = self._get_archetype_prior(symbol)
            _cg = CausalGraph()
            _results = _cg.propagate(_ms, _arch, max_hops=3)
            if _results:
                _causal_conf = max(r["confidence"] for r in _results)
                _causal_lag = max(r["lag_max"] for r in _results)
        except Exception:
            pass

        # ── Giai đoạn 7: ModelRegistry BMA competition (Sprint 4) ──
        # WHY: BMA weights are computed ONCE per batch (lazy-cached via
        #   self._bma_posterior). All symbols share the same market-level
        #   model competition — computing per-symbol would be redundant.
        model_registry_lr = None
        try:
            mr = self._get_model_registry()
            if self._bma_posterior is None:
                _ms = self._macro.get("state", "STABLE")
                self._bma_posterior = mr.bma_posterior(_ms, arch_prior_key)
                self._dominant_model = mr.select_best(_ms, arch_prior_key)
            model_registry_lr = compute_model_registry_lr(self._bma_posterior)
        except Exception:
            pass

        # Bayesian inference v3 with Giai đoạn 7 ModelRegistry LR
        #   + FairMultipleEngine lr_val_override (MoS-modulated valuation LR)
        #   + Node #9 RecoveryAuthenticity (RDS + ΔBreadth + ΔCredit)
        try:
            _credit_shift = 50.0 - float(self._transmission.get("credit", 50.0))
            recovery_authenticity_lr = compute_recovery_authenticity_lr(
                relative_demand=float(recov_vr),
                breadth_momentum_5d=0.0,
                credit_shift=_credit_shift,
            )
        except Exception:
            recovery_authenticity_lr = None
        p_gain, log_odds, calib_penalty = compute_gain_probability(
            macro_state=self._macro["state"],
            transmission_phase=self._transmission["phase"],
            sector_phase=sector_phase,
            health_archetype=health["archetype"],
            valuation_zone=val.get("overall_zone", "FAIR"),
            behavior_position=beh.get("position", "IN_VA"),
            capital_allocation=capital_arch,
            macro_entropy=self._macro["entropy"],
            transmission_credit=self._transmission["credit"],
            archetype_prior_key=arch_prior_key,
            lr_macro_override=lr_macro_dynamic,
            evidence_weights=dynamic_weights,
            model_registry_lr=model_registry_lr,
            lr_val_override=val.get("_lr_val_override"),
            recovery_authenticity_lr=recovery_authenticity_lr,
        )

        # Expected utility
        eu_list = compute_expected_utilities(p_gain)
        best_action, best_eu = pick_best_action(eu_list)

        # Kelly sizing
        allocation = kelly_allocation(p_gain, calib_penalty, self._macro["entropy"])
        if best_action in ("VETO", "AVOID"):
            allocation = 0.0
        elif best_action == "REDUCE":
            allocation = -min(allocation, 10.0)

        conviction = p_gain * (1.0 - calib_penalty)

        # ── Circuit Breaker — override action nếu calibration degradation ──
        cb = self._check_circuit_breaker()
        cb_level = cb.get("level", 0)
        cb_label = cb.get("label", "BÌNH_THƯỜNG")
        cb_reason = cb.get("reason", "")
        cb_active = cb.get("active", 0)

        if cb_active and best_action in ("OPEN", "SCALE_IN", "HOLD", "REDUCE", "WAIT", "AVOID"):
            from calibration.prediction_log import CB_ACTION_MAP

            overrides = CB_ACTION_MAP.get(cb_level, {})
            if best_action in overrides:
                new_action, capped_alloc = overrides[best_action]
                best_action = new_action
                if capped_alloc is not None:
                    allocation = capped_alloc

        # P4 logging — combined BMA prediction
        try:
            _ensure_calib()
            from calibration.prediction_log import insert_prediction

            insert_prediction(
                date_str=str(date.today()),
                symbol=symbol,
                p_gain=round(p_gain, 4),
                eu=round(best_eu, 4),
                kelly_alloc=round(allocation, 1),
                action=best_action,
                macro_state=self._macro["state"],
                transmission_phase=self._transmission["phase"],
                sector_phase=sector_phase,
                health_archetype=health["archetype"],
                valuation_zone=val.get("overall_zone", "FAIR"),
                behavior_position=beh.get("position", "UNKNOWN"),
            )
            # P0: per-model predictions for BMA calibration
            # WHY: store 3 separate rows (M1/M2/M3) so Step 11c can
            #   resolve per-model outcomes independently, avoiding
            #   Brier Score blur from feeding aggregate accuracy.
            model_weights_map = {
                "M1_MACRO": {"macro": 0.20, "transmission": 0.13, "sector": 0.10},
                "M2_FUNDAMENTAL": {"health": 0.11, "capital_allocation": 0.13, "valuation": 0.09},
                "M3_BEHAVIORAL": {"behavior": 0.09},
            }
            for mid, mw in model_weights_map.items():
                total_w = sum(mw.values())
                renormed = {k: v / total_w for k, v in mw.items()}
                mp, _, _ = compute_gain_probability(
                    macro_state=self._macro["state"],
                    transmission_phase=self._transmission["phase"],
                    sector_phase=sector_phase,
                    health_archetype=health["archetype"],
                    valuation_zone=val.get("overall_zone", "FAIR"),
                    behavior_position=beh.get("position", "UNKNOWN"),
                    capital_allocation=capital_arch,
                    macro_entropy=self._macro["entropy"],
                    transmission_credit=self._transmission["credit"],
                    archetype_prior_key=arch_prior_key,
                    lr_macro_override=lr_macro_dynamic,
                    evidence_weights=renormed,
                    model_registry_lr=None,
                )
                insert_prediction(
                    date_str=str(date.today()),
                    symbol=symbol,
                    model_id=mid,
                    p_gain=round(mp, 4),
                    eu=round(best_eu, 4),
                    kelly_alloc=round(allocation, 1),
                    action=best_action,
                    macro_state=self._macro["state"],
                    transmission_phase=self._transmission["phase"],
                    sector_phase=sector_phase,
                    health_archetype=health["archetype"],
                    valuation_zone=val.get("overall_zone", "FAIR"),
                    behavior_position=beh.get("position", "UNKNOWN"),
                )
        except Exception:
            pass

        return BayesianMandate(
            symbol=symbol,
            action=best_action,
            action_vn=ACTION_VN.get(best_action, best_action),
            expected_utility=round(best_eu, 4),
            p_gain=round(p_gain, 4),
            calibration_penalty=round(calib_penalty, 4),
            allocation_pct=round(allocation, 1),
            conviction=round(conviction, 4),
            macro_state=self._macro["state"],
            transmission_phase=self._transmission["phase"],
            sector_phase=sector_phase,
            health_archetype=health["archetype"],
            valuation_zone=val.get("overall_zone", "FAIR"),
            valuation_zone_peer=val.get("overall_zone_peer", "NO_DATA"),
            valuation_zone_ts=val.get("overall_zone_ts", "NO_DATA"),
            pe_raw=val.get("raw_values", {}).get("PE"),
            pb_raw=val.get("raw_values", {}).get("PB"),
            # FairMultipleEngine fields
            fair_pe=fair.get("fair_pe") if fair.get("status") == "OK" else None,
            fair_pb=fair.get("fair_pb") if fair.get("status") == "OK" else None,
            margin_of_safety=fair.get("margin_of_safety_pct") if fair.get("status") == "OK" else None,
            fair_ke=fair.get("ke"),
            fair_g=fair.get("g"),
            fair_sector=fair.get("sector", ""),
            fair_status=fair.get("status", ""),
            # CSI v2 — MoS Zone (PRIMARY) + Market Context Tag (secondary)
            mos_zone=val.get("_mos_zone", MOS_ZONE_NO_DATA),
            market_context_tag=val.get("_market_context_tag", "NO_DATA"),
            behavior_position=beh.get("position", "UNKNOWN"),
            capital_allocation_archetype=capital_arch,
            capital_allocation_score=capital_score,
            archetype_prior=arch_prior_key,
            lr_macro_dynamic=round(lr_macro_dynamic, 3),
            contextual_health_score=round(contextual_health_score, 3),
            eu_ranking=eu_list,
            circuit_breaker_level=cb_level,
            circuit_breaker_label=cb_label,
            circuit_breaker_trigger=cb_reason,
            bma_posterior=self._bma_posterior or {},
            dominant_model=(self._dominant_model or {}).get("model_id", ""),
            model_registry_lr=round(model_registry_lr, 4) if model_registry_lr else 1.0,
        )

    def analyze(self, symbols: List[str]) -> Dict:
        results = {}
        for sym in symbols:
            results[sym] = self.assess(sym)
        return {
            "date": str(date.today()),
            "macro_state": self._macro,
            "transmission": self._transmission,
            "sector": self._sector,
            "symbols": len(symbols),
            "results": results,
        }

    def close(self):
        self.valuation.close()
        self.behavior.close()


# Legacy alias
GovernorEngine = BayesianGovernor


# =========================================================================
# 7. REPORTING
# =========================================================================


# P4 Calibration hook — lazy init
_CALIB_INITED = False


def _ensure_calib():
    global _CALIB_INITED
    if not _CALIB_INITED:
        try:
            from calibration.prediction_log import init_schema

            init_schema()
            _CALIB_INITED = True
        except Exception:
            pass


ARROW_MAP = {
    "VETO": "⛔",
    "AVOID": "🚫",
    "REDUCE": "⬇",
    "WAIT": "⏳",
    "HOLD": "➡",
    "SCALE_IN": "📈",
    "OPEN": "🚀",
}


# WHY: Human-readable business status for Tầng 2 action ranking table.
#   Maps P2 HealthArchetype EN keys to "VI (EN)" format.
#   Used directly (not via _()) in print_report() to avoid double-wrapping.
#   Ốp 12 archetypes into 4 tiers: Tốt / Trung bình / Rủi ro / Yếu.
BUSINESS_STATUS_MAP = {
    "HIGH_QUALITY_COMPOUNDER": "Tốt (High Quality Compounder)",
    "STEADY_EARNER": "Tốt (Steady Earner)",
    "FRANCHISE_BANK": "Tốt (Franchise Bank)",
    "RETAIL_PLATFORM": "Trung bình (Retail Platform)",
    "REIT_COMMERCIAL": "Trung bình (Commercial REIT)",
    "REGULATED_UTILITY": "Tốt (Regulated Utility)",
    "ASSET_BANK": "Trung bình (Asset Bank)",
    "EXPORT_MANUFACTURER": "Trung bình (Export Mfr)",
    "CYCLICAL_HEAVY": "Rủi ro chu kỳ (Cyclical Heavy)",
    "REAL_ESTATE_DEVELOPER": "Rủi ro (RE Developer)",
    "DISTRESSED": "Yếu (Distressed)",
    "TURNAROUND": "Phục hồi (Turnaround)",
    "LOW_QUALITY": "Kém (Low Quality)",
    "UNKNOWN": "Không rõ (Unknown)",
}

# ── CSI v2 — MoS Zone display (PRIMARY valuation signal) ─────
# WHY: MoS (Margin of Safety) from FairMultipleEngine replaces
#   Z-Score as the primary valuation signal in Tầng 2.
#   Z-Score is demoted to "Market Context Tag" (secondary).
#   Display is inline in print_report(), not via VALUATION_DISPLAY.
#   This block kept for reference only; actual display is in
#   print_report() using MOS_EMOJI/MOS_ABBR/MOS_ZONE_* constants.


def print_report(analysis: Dict):
    """Inverted Pyramid 3-Tầng CSI report.

    WHY: End-investor reads Tầng 1 (verdict + 3 reasons) first,
         skims Tầng 2 (ranking table), and ignores Tầng 3 (dev audit).
         Developers read Tầng 3 for debugging. Separates concerns.
    """
    try:
        from src.core.canonical_output_adapter import localize_label

        # WHY: _() returns "VI (EN)" format — VI comes first for
        #   Vietnamese readers, EN in parentheses for bilingual reference.
        #   localize_label("full") returns the VI value from CLI_LABEL_MAP.
        def _(x):
            vi = localize_label(x, "full")
            return f"{vi} ({x})" if vi != x else x
    except Exception:

        def _(x):
            return x

    m = analysis["macro_state"]
    t = analysis["transmission"]
    s = analysis["sector"]
    first_r = list(analysis["results"].values())[0]
    results = analysis["results"]
    n = len(results)

    # ── Determine overall verdict ────────────────────────────────────
    has_buy = any(r.action in ("OPEN", "SCALE_IN") for r in results.values())
    all_reduce = all(r.action in ("REDUCE", "AVOID", "VETO") for r in results.values())
    if has_buy:
        verdict = "CÓ CƠ HỘI MUA (SCALE_IN/OPEN)"
        capital_verdict = f"{_('Alloc')} > 0%"
        signal_icon = "🟢"
    elif all_reduce:
        verdict = "KHÔNG ĐẦU TƯ / DỪNG GIẢI NGÂN MỚI"
        capital_verdict = f"{_('Alloc')} = 0%"
        signal_icon = "🔴"
    else:
        verdict = "THẬN TRỌNG / QUAN SÁT"
        capital_verdict = f"{_('Alloc')} = 0%"
        signal_icon = "🟡"

    deploy_symbols = [sym for sym, r in results.items() if r.action in ("OPEN", "SCALE_IN")]
    deploy_count = len(deploy_symbols)
    buy_ratio = f"{deploy_count}/{n}"

    # ══════════════════════════════════════════════════════════════════
    # TẦNG 1 — KẾT LUẬN CHÍNH (INVERTED PYRAMID TOP)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n  {'=' * 90}")
    print(f"  🎯 {_('INVESTMENT DECISION REPORT')} — PTCK GOVERNOR V3 ({_('T+30D Forward')}) — {analysis['date']}")
    print(f"  {'=' * 90}")
    print(f"  {signal_icon} {_('OVERALL VERDICT')}: {verdict} ({_('CAPITAL RATIO')}: {capital_verdict})")
    _symbols = ", ".join(deploy_symbols) if deploy_symbols else "KHÔNG CÓ"
    print(f"  👉 {_('Priority Symbols')}: {_symbols} ({buy_ratio} {_('qualifying symbols')})")
    _directive = "HẠ TỶ TRỌNG / THU HỒI SỨC MUA (REDUCE ALL)" if all_reduce else "GIỮ / TÍCH LŨY CHỌN LỌC"
    print(f"  👉 {_('Portfolio Directive')}: {_directive}")
    print(f"  {'─' * 90}")
    print(f"  💡 {_('CORE REASONS')}:")
    macro_state_str = m.get("state", "?")
    macro_entropy = float(m.get("entropy", 0))
    macro_p = float(m.get("posterior", 0))
    trans_phase = t.get("phase", "?")
    bma_dict = first_r.bma_posterior
    dom_model = first_r.dominant_model if bma_dict else "N/A"
    dom_pct = max(bma_dict.values()) if bma_dict else 0
    print(f"    1. {_('Macro')} {_('tightening')} : {macro_state_str} (P={macro_p:.0%}, H={macro_entropy:.2f})")
    print(f"    2. {_('Liquidity Trap')}: {trans_phase} ({_('P(Gain)')} T+30D {_('compressed below 45%')})")
    print(f"    3. {_('Dominant Model')}: {dom_model} ({dom_pct:.0%} {_('BMA weight')}) -> {_('Veto micro buy signals')}")

    # Circuit breaker
    cb_active = first_r.circuit_breaker_level > 0
    if cb_active:
        print(f"  ⛔ {_('CIRCUIT BREAKER')}: {first_r.circuit_breaker_label} — {first_r.circuit_breaker_trigger}")

    # ══════════════════════════════════════════════════════════════════
    # TẦNG 2 — BẢNG XẾP HẠNG HÀNH ĐỘNG (MIDDLE TIER)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n  {'=' * 95}")
    print(f"  📊 {_('ACTION RANKING')} ({_('SORTED BY P(Gain) DESC')}):")
    print(f"  {'=' * 95}")

    # CSI v2 — Absolute Value First: MoS Zone is PRIMARY
    MOS_EMOJI = {MOS_ZONE_UNDERVALUED: "🟢", MOS_ZONE_FAIR_VALUE: "🟡", MOS_ZONE_OVERVALUED: "🔴", MOS_ZONE_NO_DATA: "⚪"}
    MOS_ABBR = {MOS_ZONE_UNDERVALUED: "HD", MOS_ZONE_FAIR_VALUE: "HL", MOS_ZONE_OVERVALUED: "QG", MOS_ZONE_NO_DATA: "??"}
    print(
        f"  {'Mã':<5} {'Ngành':<18} {'Hành động':<18} {'Vốn%':<7} {'DN (Business)':<22} "
        f"{'Định giá Giá trị (MoS)':<34} {'Bối cảnh Thị trường':<30}"
    )
    print(f"  {'─' * 140}")

    sorted_symbols = sorted(results.items(), key=lambda x: x[1].p_gain, reverse=True)
    for sym, r in sorted_symbols:
        arrow = ARROW_MAP.get(r.action, "?")
        status = BUSINESS_STATUS_MAP.get(r.health_archetype, r.health_archetype)

        # Per-symbol sector (KHÔNG phải top_sector market — tránh hiểu lầm)
        sym_sector = _symbol_sector(sym) or "?"
        sym_sector = sym_sector[:16]

        # Primary: MoS Zone
        mz = r.mos_zone
        mos_label = f"{MOS_EMOJI.get(mz, '⚪')} {MOS_ABBR.get(mz, mz):>3}"
        if r.margin_of_safety is not None:
            mos_label += f" (MoS: {r.margin_of_safety:+.1f}%)"
        else:
            mos_label += " (MoS: N/A)"

        # Secondary: Market Context Tag (Z-Score)
        ctx = r.market_context_tag
        if "Premium" in ctx:
            ctx_emoji = "🔺"
        elif "Discount" in ctx:
            ctx_emoji = "📉"
        else:
            ctx_emoji = "➡️"
        ctx_display = f"{ctx_emoji} {ctx}"

        print(
            f"  {arrow} {sym:<4} {sym_sector:<18} {r.action_vn:<18} {r.allocation_pct:>+6.1f}% "
            f"{status:<22} {mos_label:<34} {ctx_display:<30}"
        )

    # ══════════════════════════════════════════════════════════════════
    # TẦNG 2.5 — BẢNG ĐIỂM TỔNG HỢP COMPOSITE SCORE (0 – 100 SCALE)
    # ══════════════════════════════════════════════════════════════════
    try:
        from src.governor.composite_score_projector import CompositeScoreProjector, print_composite_dashboard

        projector = CompositeScoreProjector()
        comp_results = projector.project_batch(list(results.values()))
        print_composite_dashboard(comp_results)
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════
    # TẦNG 3 — KIỂM TOÁN THUẬT TOÁN (BOTTOM TIER — DEVELOPER)
    # ══════════════════════════════════════════════════════════════════
    print(f"\n  {'=' * 90}")
    print(f"  🔍 {_('TECHNICAL AUDIT TRAIL')}:")
    print(f"  {'=' * 90}")

    # BMA + Model LR
    if bma_dict:
        bma_str = ", ".join(f"{k}={v:.0%}" for k, v in sorted(bma_dict.items(), key=lambda x: x[1], reverse=True))
        print(f"  • {_('BMA Weights')}  : {bma_str} | {_('Model LR')} = {first_r.model_registry_lr:.3f}")

    # Top symbol EU ranking
    top_sym, top_r = sorted_symbols[0]
    top_eu = top_r.eu_ranking[:4]  # top 4 actions
    eu_str = " | ".join(f"{a}: {eu:+.3f}" for a, eu in top_eu)
    print(f"  • {_('Top Expected Utility')} ({top_sym}) : {eu_str}")
    print(
        f"  • {_('Context')}: {_('Macro')}={macro_state_str}, {_('Transmission')}={trans_phase}, "
        f"{_('Top Sector')}={s.get('top_sector', '?')} ({s.get('top_phase', '?')}), "
        f"{_('Healthy')}={s.get('n_healthy', 0)}/19"
    )

    # Prediction count
    print(
        f"  • {_('Symbols Analyzed')}: {n} {_('symbol')} | {_('Most Common Action')}: "
        f"{max(set(r.action for r in results.values()), key=lambda a: sum(1 for r in results.values() if r.action == a))}"
    )

    # FairMultipleEngine audit
    ok = [r for r in results.values() if r.fair_status == "OK"]
    if ok:
        print(f"  • {_('FairMultipleEngine')} (Gordon Growth PB=(ROE-g)/(Ke-g)):")
        for r in ok:
            mos_s = f"{r.margin_of_safety:+.1f}%" if r.margin_of_safety is not None else "N/A"
            pe_s = f"{r.pe_raw:.1f}" if r.pe_raw is not None else "N/A"
            pb_s = f"{r.pb_raw:.1f}" if r.pb_raw is not None else "N/A"
            fpe_s = f"{r.fair_pe:.1f}" if r.fair_pe is not None else "N/A"
            fpb_s = f"{r.fair_pb:.1f}" if r.fair_pb is not None else "N/A"
            print(
                f"      {r.symbol}: P/E={pe_s} P/B={pb_s} → Fair PE={fpe_s} Fair PB={fpb_s} "
                f"MoS={mos_s} Ke={r.fair_ke:.2%} g={r.fair_g:.2%} Sector={r.fair_sector}"
            )
    else:
        n_err = sum(1 for r in results.values() if r.fair_status != "" and r.fair_status != "OK")
        if n_err:
            print(f"  • {_('FairMultipleEngine')}: {n_err}/{n} {_('symbol')} {_('skipped')} (missing ROE/PE/PB or divergence)")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="P3 Governor — Bayesian Expected Utility")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=[
            "FPT",
            "ACB",
            "HDB",
            "MBB",
            "VCB",
            "HPG",
            "VHM",
            "DGC",
            "MWG",
            "GAS",
        ],
        help="Danh sach symbol",
    )
    parser.add_argument("--output", choices=["report", "json"], default="report")
    args = parser.parse_args()

    engine = BayesianGovernor()
    analysis = engine.analyze(args.symbols)
    engine.close()

    if args.output == "json":
        # Serialize dataclass fields
        def _ser(obj):
            if hasattr(obj, "__dataclass_fields__"):
                return {f: getattr(obj, f) for f in obj.__dataclass_fields__}
            return str(obj)

        print(json.dumps(analysis, indent=2, ensure_ascii=False, default=_ser))
    else:
        print_report(analysis)


if __name__ == "__main__":
    main()
