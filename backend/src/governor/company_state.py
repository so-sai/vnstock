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

# Prior: long-run fraction of up-days on VNINDEX ~53%
PRIOR_PROB_GAIN = 0.53
PRIOR_ODDS = PRIOR_PROB_GAIN / (1.0 - PRIOR_PROB_GAIN)

# Evidence weights (relative predictive power, sum = 1.0)
EVIDENCE_WEIGHTS = {
    "macro": 0.30,
    "transmission": 0.20,
    "sector": 0.15,
    "health": 0.15,
    "valuation": 0.10,
    "behavior": 0.10,
}

# Likelihood ratios for each evidence level
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
    macro_entropy: float = 0.0,
    transmission_credit: float = 50.0,
) -> Tuple[float, float, float]:
    """Bayesian Weight-of-Evidence → P(Gain | Evidence).

    Returns:
      (posterior_prob, log_posterior_odds, calibration_penalty)
    """
    lr_macro = _lookup_lr(LR_MACRO, macro_state)
    lr_trans = _lookup_lr(LR_TRANSMISSION, transmission_phase)
    lr_sector = _lookup_lr(LR_SECTOR, sector_phase)
    lr_health = _lookup_lr(LR_HEALTH, health_archetype)
    lr_val = _lookup_lr(LR_VALUATION, valuation_zone)
    lr_beh = _lookup_lr(LR_BEHAVIOR, behavior_position)

    log_prior = math.log(PRIOR_ODDS)
    log_lr = (
        EVIDENCE_WEIGHTS["macro"] * math.log(max(lr_macro, 0.01))
        + EVIDENCE_WEIGHTS["transmission"] * math.log(max(lr_trans, 0.01))
        + EVIDENCE_WEIGHTS["sector"] * math.log(max(lr_sector, 0.01))
        + EVIDENCE_WEIGHTS["health"] * math.log(max(lr_health, 0.01))
        + EVIDENCE_WEIGHTS["valuation"] * math.log(max(lr_val, 0.01))
        + EVIDENCE_WEIGHTS["behavior"] * math.log(max(lr_beh, 0.01))
    )

    log_posterior_odds = log_prior + log_lr
    posterior_prob = 1.0 / (1.0 + math.exp(-log_posterior_odds))

    # Calibration penalty based on macro entropy + transmission credit
    # High entropy → uncertain macro → reduce confidence
    # Low credit → frozen lending → reduce confidence
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
    """Output of the P3 Bayesian Governor for one symbol."""
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
    eu_ranking: List[Tuple[str, float]]


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
    """P3 Governor: Bayesian Expected Utility replacing IF/THEN matrix."""

    def __init__(self):
        self.perception = PerceptionLoader()
        self.valuation = L3ValuationLoader()
        self.behavior = L4BehaviorLoader()

        # Load market-level context once
        self._macro = self.perception.load_macro_state()
        self._transmission = self.perception.load_transmission()
        self._sector = self.perception.load_sector()

    def assess(self, symbol: str) -> BayesianMandate:
        """Compute Bayesian mandate for a single symbol."""
        # Per-symbol evidence
        health = self.perception.load_health(symbol)
        val = self.valuation.score_valuation(symbol)
        beh = self.behavior.score_behavior(symbol)

        # Derive sector phase from rotation chain for this symbol
        # Use top_phase as market-level sector context
        sector_phase = self._sector.get("top_phase", "NEUTRAL")

        # Bayesian inference
        p_gain, log_odds, calib_penalty = compute_gain_probability(
            macro_state=self._macro["state"],
            transmission_phase=self._transmission["phase"],
            sector_phase=sector_phase,
            health_archetype=health["archetype"],
            valuation_zone=val.get("overall_zone", "FAIR"),
            behavior_position=beh.get("position", "IN_VA"),
            macro_entropy=self._macro["entropy"],
            transmission_credit=self._transmission["credit"],
        )

        # Expected utility
        eu_list = compute_expected_utilities(p_gain)
        best_action, best_eu = pick_best_action(eu_list)

        # Kelly sizing
        allocation = kelly_allocation(
            p_gain, calib_penalty, self._macro["entropy"]
        )
        # Negative allocation for defensive actions
        if best_action in ("VETO", "AVOID"):
            allocation = 0.0
        elif best_action == "REDUCE":
            allocation = -min(allocation, 10.0)

        # Conviction = Bayesian probability adjusted by calibration
        conviction = p_gain * (1.0 - calib_penalty)

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
            eu_ranking=eu_list,
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

ARROW_MAP = {
    "VETO": "⛔", "AVOID": "🚫", "REDUCE": "⬇",
    "WAIT": "⏳", "HOLD": "➡", "SCALE_IN": "📈", "OPEN": "🚀", 
}


def print_report(analysis: Dict):
    print(f"\n  {'='*80}")
    print(f"  P3 GOVERNOR — BAYESIAN EXPECTED UTILITY — {analysis['date']}")
    print(f"  {'='*80}")

    # Market context
    m = analysis["macro_state"]
    t = analysis["transmission"]
    s = analysis["sector"]
    print(f"  🌐 Macro:       {m['state']} (P={m['posterior']:.0%}, H={m['entropy']:.2f})")
    print(f"  🔄 Transmission: {t['phase']} (L={t['liquidity']:.0f} C={t['credit']:.0f} K={t['confidence']:.0f})")
    print(f"  🏭 Sector:      Top={s.get('top_sector','?')} | healthy={s.get('n_healthy',0)}/19 | phase={s.get('top_phase','?')}")
    print(f"  {'='*80}")

    # Table
    print(f"  {'Mã':<6} {'Hành động':<14} {'EU':>6} {'P(Gain)':>8} {'Alloc%':>7} {'Tin cậy':>8} {'Macro':<16} {'Health':<22}")
    print(f"  {'-'*80}")

    for sym, r in sorted(analysis["results"].items()):
        arrow = ARROW_MAP.get(r.action, "?")
        print(f"  {arrow} {sym:<5} {r.action_vn:<14} {r.expected_utility:>+6.3f} {r.p_gain:>7.1%} "
              f"{r.allocation_pct:>+6.1f}% {r.conviction:>7.1%} "
              f"{r.macro_state:<16} {r.health_archetype:<22}")

    print(f"\n  {'='*80}")
    print(f"  PHÂN PHỐI QUYẾT ĐỊNH")
    print(f"  {'='*80}")
    counts: Dict[str, int] = {}
    for r in analysis["results"].values():
        counts[r.action] = counts.get(r.action, 0) + 1
    for action in ["OPEN", "SCALE_IN", "HOLD", "WAIT", "REDUCE", "AVOID", "VETO"]:
        if action in counts:
            arrow = ARROW_MAP.get(action, "?")
            print(f"  {arrow} {ACTION_VN.get(action, action):<14}: {counts[action]} mã")

    print(f"\n  {'='*80}")
    print(f"  CHI TIẾT TỪNG MÃ")
    print(f"  {'='*80}")

    for sym, r in sorted(analysis["results"].items()):
        arrow = ARROW_MAP.get(r.action, "?")
        print(f"\n  {'─'*60}")
        print(f"  {arrow} {sym} | {r.action_vn} | EU={r.expected_utility:+.4f}")
        print(f"  {'─'*60}")
        print(f"  P(Gain|Evidence) = {r.p_gain:.1%}  |  Calib Penalty = {r.calibration_penalty:.2f}")
        print(f"  Allocation       = {r.allocation_pct:+.1f}%  |  Conviction    = {r.conviction:.1%}")
        print(f"  Evidence Inputs:")
        print(f"    P0 MacroState:  {r.macro_state}")
        print(f"    P1 Transmission:{r.transmission_phase}")
        print(f"    P1 SectorPhase: {r.sector_phase}")
        print(f"    P2 Health:      {r.health_archetype}")
        print(f"    L3 Valuation:   {r.valuation_zone}")
        print(f"    L4 Behavior:    {r.behavior_position}")
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
