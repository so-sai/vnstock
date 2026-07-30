"""company_state.py — P3 Governor Expected Utility (Bayesian Decision Framework)

Replaces hard-coded IF/THEN rules with Bayesian Weight-of-Evidence.
Connects 6 evidence nodes into P(Gain|Evidence), Expected Utility, Kelly sizing.

Evidence nodes:
  P0: MacroState (discrete + entropy)
  P1: TransmissionState (discrete + L/C/K)
  P1: SectorState (discrete + n_healthy)
  P2: HealthArchetype (discrete + 5-organ vector)
  L3: ValuationZone (discrete + z-scores)
  L4: BehaviorZone (discrete + signals)
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

# Archetype-aware priors (Giai đoạn 1 + Giai đoạn 4 insights)
# COMPOUNDERs have structural ROIC > WACC → higher baseline odds
# CYCLICAL_HEAVY and REAL_ESTATE have earnings risk → lower baseline
PRIOR_BY_ARCHETYPE = {
    "COMPOUNDER": 0.58,
    "FRANCHISE_BANK": 0.55,
    "STEADY_EARNER": 0.54,
    "REGULATED_UTILITY": 0.52,
    "RETAIL_PLATFORM": 0.50,
    "ASSET_BANK": 0.48,
    "EXPORT_MANUFACTURER": 0.47,
    "CYCLICAL_HEAVY": 0.45,
    "REAL_ESTATE_DEVELOPER": 0.42,
    "UNKNOWN": 0.53,
}

# ── Evidence weights v2 (7 nodes, sum = 1.0) ──────────────────
# Capital Allocation (Giai đoạn 4) added at 0.15, shifted from macro/transmission/sector/health
EVIDENCE_WEIGHTS = {
    "macro": 0.25,
    "transmission": 0.15,
    "sector": 0.12,
    "health": 0.13,
    "capital_allocation": 0.15,  # NEW: management capital allocation quality
    "valuation": 0.10,
    "behavior": 0.10,
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

LR_BEHAVIOR = {
    "IN_VA_DEMAND": 1.60,
    "IN_VA": 1.15,
    "BELOW_VA_DEMAND": 1.30,
    "BELOW_VA": 0.85,
    "ABOVE_VA_DEMAND": 0.70,
    "ABOVE_VA": 0.50,
}

# ── Capital Allocation LR (Giai đoạn 4) ───────────────────────
LR_CAPITAL_ALLOCATION = {
    "VALUE_CREATOR": 1.60,
    "EFFICIENT_ALLOCATOR": 1.30,
    "CAPITAL_HOARDER": 0.85,
    "LEVERAGED_OPTIMIZER": 0.70,
    "TRANSITIONAL": 0.95,
    "VALUE_DESTROYER": 0.20,
}


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
) -> Tuple[float, float, float]:
    """Bayesian Weight-of-Evidence v2 → P(Gain | Evidence).

    LAW-004: accepts evidence_weights dict from EvidenceEngine
    for dynamically weighted log-LR fusion.

    So với v1:
      - Thêm capital_allocation node (Giai đoạn 4)
      - Archetype-aware prior (Giai đoạn 1 + 4)
      - dynamic LR macro override (Giai đoạn 2)
      - dynamic evidence weights (LAW-004)

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
    lr_val = _lookup_lr(LR_VALUATION, valuation_zone)
    lr_beh = _lookup_lr(LR_BEHAVIOR, behavior_position)
    lr_cap = _lookup_lr(LR_CAPITAL_ALLOCATION, capital_allocation)

    log_prior = math.log(prior_odds)
    log_lr = (
        w.get("macro", EVIDENCE_WEIGHTS["macro"]) * math.log(max(lr_macro, 0.01))
        + w.get("transmission", EVIDENCE_WEIGHTS["transmission"]) * math.log(max(lr_trans, 0.01))
        + w.get("sector", EVIDENCE_WEIGHTS["sector"]) * math.log(max(lr_sector, 0.01))
        + w.get("health", EVIDENCE_WEIGHTS["health"]) * math.log(max(lr_health, 0.01))
        + w.get("capital_allocation", EVIDENCE_WEIGHTS["capital_allocation"]) * math.log(max(lr_cap, 0.01))
        + w.get("valuation", EVIDENCE_WEIGHTS["valuation"]) * math.log(max(lr_val, 0.01))
        + w.get("behavior", EVIDENCE_WEIGHTS["behavior"]) * math.log(max(lr_beh, 0.01))
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
    "VETO":     [-1.0,  0.9],
    "AVOID":    [-0.8,  0.7],
    "REDUCE":   [-0.3,  0.6],
    "WAIT":     [ 0.0,  0.0],
    "HOLD":     [ 0.3, -0.3],
    "SCALE_IN": [ 0.7, -0.6],
    "OPEN":     [ 1.0, -1.0],
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


def kelly_allocation(
    p_gain: float, calibration_penalty: float, macro_entropy: float
) -> float:
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
    except (ZeroDivisionError, TypeError):
        return None


class L2HealthLoader:
    """Legacy health loader — kept for base ratio access."""

    def __init__(self):
        self.conn = sqlite3.connect(str(FINANCIAL_DB))

    def get_latest_ratios(self, symbol: str) -> Dict:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT ratio_name, ratio_value, interpretation
            FROM health_ratios
            WHERE symbol = ? AND period = (
                SELECT MAX(period) FROM health_ratios WHERE symbol = ?
            )
        """, (symbol.upper(), symbol.upper()))
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
        cur.execute("""
            SELECT ratio_name, ratio_value, z_score, percentile, zone
            FROM valuation_scores
            WHERE symbol = ? AND period = (
                SELECT MAX(period) FROM valuation_scores WHERE symbol = ?
            )
            ORDER BY ratio_name
        """, (symbol.upper(), symbol.upper()))
        rows = cur.fetchall()
        return {r[0]: {"value": r[1], "z_score": r[2], "percentile": r[3], "zone": r[4]}
                for r in rows}

    def score_valuation(self, symbol: str) -> Dict:
        vals = self.get_latest_valuation(symbol)
        if not vals:
            return {"score": 0, "grade": "NO_DATA", "overall_zone": "FAIR", "lowest_z": 0}

        zone_scores = {"ULTRA_CHEAP": 2, "CHEAP": 1, "FAIR": 0, "EXPENSIVE": -1, "ULTRA_EXPENSIVE": -2}
        z_scores = []
        scores = []
        for rinfo in vals.values():
            z_scores.append(rinfo.get("z_score", 0))
            scores.append(zone_scores.get(rinfo.get("zone", "FAIR"), 0))

        avg_z = sum(z_scores) / len(z_scores) if z_scores else 0
        if avg_z <= -1.5:
            overall = "ULTRA_CHEAP"
        elif avg_z <= -0.5:
            overall = "CHEAP"
        elif avg_z >= 1.5:
            overall = "ULTRA_EXPENSIVE"
        elif avg_z >= 0.5:
            overall = "EXPENSIVE"
        else:
            overall = "FAIR"

        return {
            "score": round(sum(scores) / len(scores), 2) if scores else 0,
            "grade": overall,
            "overall_zone": overall,
            "lowest_z": round(min(z_scores), 2) if z_scores else 0,
        }

    def close(self):
        self.conn.close()


class L4BehaviorLoader:
    """Layer 4 — Market Behavior (Volume Profile + Active Demand)."""

    def __init__(self):
        self.conn = sqlite3.connect(str(FINANCIAL_DB))

    def get_volume_profile(self, symbol: str) -> Optional[Dict]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT price_current, poc, vah, val, volume_ratio,
                   price_ma20, price_ma50
            FROM volume_profile
            WHERE symbol = ?
            ORDER BY date DESC LIMIT 1
        """, (symbol.upper(),))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "price": row[0], "poc": row[1], "vah": row[2],
            "val": row[3], "volume_ratio": row[4],
            "ma20": row[5], "ma50": row[6],
        }

    def get_active_demand_count(self, symbol: str, days: int = 20) -> int:
        cur = self.conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cur.execute("""
            SELECT COUNT(*) FROM active_demand
            WHERE symbol = ? AND date >= ?
        """, (symbol.upper(), cutoff))
        return cur.fetchone()[0]

    def score_behavior(self, symbol: str) -> Dict:
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
                return {"archetype": "STEADY_EARNER", "vector": [0.5, 0.5, 0.5, 0.5, 0.5],
                        "confidence": 0.5, "periods": 0}
            return {
                "archetype": state.archetype,
                "archetype_label": state.archetype_label,
                "vector": state.organs.vector,
                "confidence": state.archetype_confidence,
                "periods": state.data_periods,
            }
        except Exception:
            return {"archetype": "STEADY_EARNER", "vector": [0.5, 0.5, 0.5, 0.5, 0.5],
                    "confidence": 0.5, "periods": 0}


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
    behavior_position: str

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
    """P3 Governor v2: Bayesian Expected Utility + Giai đoạn 1-4 integration.

    So với v1:
      - Archetype-aware prior (Giai đoạn 1)
      - Dynamic LR macro from Factor Exposure Matrix (Giai đoạn 2)
      - Contextual Health score (Giai đoạn 3)
      - Capital Allocation evidence node (Giai đoạn 4)
    """

    def __init__(self):
        self.perception = PerceptionLoader()
        self.valuation = L3ValuationLoader()
        self.behavior = L4BehaviorLoader()

        # Giai đoạn 2: Factor Exposure
        self._factor_engine = None

        # Giai đoạn 3: Contextual Health
        self._context_engine = None

        # Giai đoạn 4: Capital Allocation
        self._capital_engine = None

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
            self._cb_state = {"level": 0, "label": "BÌNH_THƯỜNG",
                              "active": 0, "reason": "CHECK_FAILED"}
        return self._cb_state

    def assess(self, symbol: str) -> BayesianMandate:
        """Compute Bayesian mandate v2 for a single symbol."""
        # Per-symbol evidence
        health = self.perception.load_health(symbol)
        val = self.valuation.score_valuation(symbol)
        beh = self.behavior.score_behavior(symbol)

        # ── Giai đoạn 1: Archetype-aware prior ──────────────
        arch_prior_key = self._get_archetype_prior(symbol)

        # ── Giai đoạn 2: Dynamic LR from Factor Exposure ────
        lr_macro_dynamic = 1.0
        try:
            factor_eng = self._get_factor_engine()
            matrix = factor_eng.compute(symbol)
            lr_mult = self._compute_lr_adjust(
                matrix, self._macro["state"], self._transmission["phase"]
            )
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
            dw = get_dynamic_evidence_weights()
            if dw:
                dynamic_weights = dw
        except Exception:
            pass

        # Bayesian inference v2 with optional dynamic weights (LAW-004)
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
        )

        # Expected utility
        eu_list = compute_expected_utilities(p_gain)
        best_action, best_eu = pick_best_action(eu_list)

        # Kelly sizing
        allocation = kelly_allocation(
            p_gain, calib_penalty, self._macro["entropy"]
        )
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

        if cb_active and best_action in (
            "OPEN", "SCALE_IN", "HOLD", "REDUCE", "WAIT", "AVOID"
        ):
            from calibration.prediction_log import CB_ACTION_MAP
            overrides = CB_ACTION_MAP.get(cb_level, {})
            if best_action in overrides:
                new_action, capped_alloc = overrides[best_action]
                best_action = new_action
                if capped_alloc is not None:
                    allocation = capped_alloc

        # P4 logging
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

from src.core.report_i18n_mapper import translate

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
    "VETO": "⛔", "AVOID": "🚫", "REDUCE": "⬇",
    "WAIT": "⏳", "HOLD": "➡", "SCALE_IN": "📈", "OPEN": "🚀", 
}


def print_report(analysis: Dict):
    print(f"\n  {'='*88}")
    print(f"  P3 GOVERNOR v2 — BAYESIAN EXPECTED UTILITY — {analysis['date']}")
    print(f"  {'='*88}")

    # Market context
    m = analysis["macro_state"]
    t = analysis["transmission"]
    s = analysis["sector"]
    print(f"  🌐 Macro:       {m['state']} (P={m['posterior']:.0%}, H={m['entropy']:.2f})")
    print(f"  🔄 Transmission: {t['phase']} (L={t['liquidity']:.0f} C={t['credit']:.0f} K={t['confidence']:.0f})")
    print(f"  🏭 Sector:      Top={s.get('top_sector','?')} | healthy={s.get('n_healthy',0)}/19 | phase={s.get('top_phase','?')}")

    # Circuit breaker status
    first_r = list(analysis["results"].values())[0]
    cb_active = first_r.circuit_breaker_level > 0
    if cb_active:
        print(f"  ⛔ CIRCUIT BREAKER: {first_r.circuit_breaker_label} "
              f"— {first_r.circuit_breaker_trigger}")
    
    print(f"  {'='*88}")
    
    # Table
    print(f"  {'Mã':<6} {'Hành động':<14} {'EU':>6} {'P(Gain)':>8} {'Alloc%':>7} {'Tin cậy':>8} {'Prior':<8} {'Cap.Alloc':<14} {'Macro LR':>8}")
    print(f"  {'-'*88}")

    for sym, r in sorted(analysis["results"].items()):
        arrow = ARROW_MAP.get(r.action, "?")
        print(f"  {arrow} {sym:<5} {r.action_vn:<14} {r.expected_utility:>+6.3f} {r.p_gain:>7.1%} "
              f"{r.allocation_pct:>+6.1f}% {r.conviction:>7.1%} "
              f"{r.archetype_prior:<8} {r.capital_allocation_archetype:<14} {r.lr_macro_dynamic:>7.3f}")

    print(f"\n  {'='*88}")
    print(f"  PHÂN PHỐI QUYẾT ĐỊNH")
    print(f"  {'='*88}")
    counts: Dict[str, int] = {}
    for r in analysis["results"].values():
        counts[r.action] = counts.get(r.action, 0) + 1
    for action in ["OPEN", "SCALE_IN", "HOLD", "WAIT", "REDUCE", "AVOID", "VETO"]:
        if action in counts:
            arrow = ARROW_MAP.get(action, "?")
            print(f"  {arrow} {ACTION_VN.get(action, action):<14}: {counts[action]} mã")

    print(f"\n  {'='*88}")
    print(f"  CHI TIẾT TỪNG MÃ — v2 (Giai đoạn 1→4 tích hợp)")
    print(f"  {'='*88}")

    for sym, r in sorted(analysis["results"].items()):
        arrow = ARROW_MAP.get(r.action, "?")
        print(f"\n  {'─'*65}")
        print(f"  {arrow} {sym} | {r.action_vn} | EU={r.expected_utility:+.4f}")
        print(f"  {'─'*65}")
        print(f"  P(Gain|Evidence) = {r.p_gain:.1%}  |  Calib Penalty = {r.calibration_penalty:.2f}")
        print(f"  Allocation       = {r.allocation_pct:+.1f}%  |  Conviction    = {r.conviction:.1%}")
        print(f"  v2 Inputs:")
        print(f"    GĐ1 Prior:       {r.archetype_prior}")
        print(f"    GĐ2 Macro LR:    {r.lr_macro_dynamic:.3f} (dynamic per symbol)")
        print(f"    GĐ3 Ctx Health:  {r.contextual_health_score:.3f}")
        print(f"    GĐ4 Cap.Alloc:   {r.capital_allocation_archetype} ({r.capital_allocation_score:+.2f})")
        print(f"  Evidence Inputs:")
        print(f"    P0 MacroState:   {r.macro_state}")
        print(f"    P1 Transmission: {r.transmission_phase}")
        print(f"    P1 SectorPhase:  {r.sector_phase}")
        print(f"    P2 Health:       {r.health_archetype}")
        print(f"    L3 Valuation:    {r.valuation_zone}")
        print(f"    L4 Behavior:     {r.behavior_position}")
        print(f"  EU Ranking:")
        for action, eu in r.eu_ranking:
            marker = "←" if action == r.action else ""
            print(f"    {action:12s}: {eu:+.4f} {marker}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="P3 Governor — Bayesian Expected Utility")
    parser.add_argument("--symbols", nargs="+", default=[
        "FPT", "ACB", "HDB", "MBB", "VCB",
        "HPG", "VHM", "DGC", "MWG", "GAS",
    ], help="Danh sach symbol")
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
