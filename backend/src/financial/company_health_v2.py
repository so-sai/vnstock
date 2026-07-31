"""
company_health_v2.py — Phase 4, P2 (Perception Layer)

Transforms 22 financial ratios + raw financial facts into a 5-Organ Latent Vector
representing the company's underlying health state.

5 Organs:
  Profitability:  ROE trend, ROA level, Margin stability
  Cash:           OCF level, FCF trend, conversion quality
  Balance Sheet:  Debt trend, Interest coverage safety
  Efficiency:     ROIC level, Asset turnover
  Moat:           ROIC persistence (5Y), Gross margin persistence

Output: 5D vector [0,1] per organ + human-readable Latent Archetype.
Governor only sees the vector; the archetype is for human reporting.
"""

import json
import logging
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    if str(root_path / "backend") not in sys.path:
        sys.path.insert(0, str(root_path / "backend"))
    return root_path


PROJECT_ROOT = _hydrate_path()
FINANCIAL_DB = str(PROJECT_ROOT / "backend" / "data" / "financial_facts.db")


# ── Archetype definitions ───────────────────────────────────────

ARCHETYPES = {
    "HIGH_QUALITY_COMPOUNDER": {
        "label": "Công ty Tăng trưởng Chất lượng Cao",
        "desc": "ROE>15%, ROIC>WACC, OCF>NI, low debt, 5Y persistence",
        "profitability_min": 0.70,
        "cash_min": 0.60,
        "balance_sheet_min": 0.60,
        "efficiency_min": 0.65,
        "moat_min": 0.60,
    },
    "STEADY_EARNER": {
        "label": "Công ty Thu nhập Ổn định",
        "desc": "Margins ổn định, tăng trưởng vừa phải, cash flow healthy",
        "profitability_min": 0.50,
        "cash_min": 0.50,
        "balance_sheet_min": 0.40,
        "efficiency_min": 0.40,
        "moat_min": 0.40,
    },
    "CYCLICAL": {
        "label": "Công ty Chu kỳ",
        "desc": "Earnings biến động theo vĩ mô, high beta, margin không ổn định",
        "profitability_min": 0.30,
        "moat_max": 0.35,
    },
    "LEVERAGED_GROWTH": {
        "label": "Công ty Tăng trưởng Đòn bẩy Cao",
        "desc": "Debt cao, ROE cao nhờ leverage, rủi ro thanh khoản",
        "balance_sheet_max": 0.40,
        "profitability_min": 0.50,
        "efficiency_min": 0.50,
    },
    "DISTRESSED": {
        "label": "Công ty Suy yếu",
        "desc": "OCF âm, margin giảm, debt cao, không có moat",
        "profitability_max": 0.30,
        "cash_max": 0.30,
        "balance_sheet_max": 0.30,
    },
    "COMMODITY": {
        "label": "Công ty Hàng hóa",
        "desc": "Price-taker, margin gắn với giá đầu vào/đầu ra",
        "moat_max": 0.25,
        "profitability_min": 0.20,
    },
}


@dataclass
class OrganScores:
    profitability: float
    cash: float
    balance_sheet: float
    efficiency: float
    moat: float
    vector: list[float]


@dataclass
class HealthLatentState:
    symbol: str
    entity_type: str
    organs: OrganScores
    archetype: str
    archetype_label: str
    archetype_desc: str
    archetype_confidence: float
    data_periods: int
    latest_period: str
    no_data_organs: list[str] = None


class CompanyHealthV2:
    """
    Company Health v2 — 5-Organ Latent Engine.

    Reads from financial_facts.db (health_ratios + financial_facts),
    computes 5 organ scores, and classifies the latent archetype.

    Usage:
        engine = CompanyHealthV2()
        state = engine.analyze("FPT")
    """

    def __init__(self):
        self._db = FINANCIAL_DB

    # ── Public API ───────────────────────────────────────────────

    @staticmethod
    def _detect_no_data(ratios: dict, entity: str) -> list[str]:
        """Organ nào thiếu toàn bộ ratio nền tảng → NO_DATA (không phải YẾU).

        WHY: CaféF Bank API không trả CF statement cho doanh nghiệp thường
        (chỉ 17 rows tóm tắt) → BCM không bao giờ có CFO_TO_NET_INCOME/
        FCF_TO_NET_INCOME/CAPEX_TO_CFO → Cash=0.00 là THIẾU DỮ LIỆU, không
        phải CASH FLOW YẾU. Đánh dấu NO_DATA để không kết luận DISTRESSED
        dựa trên số 0 giả tạo.
        """
        missing = []
        cash_ratios = ["CFO_TO_NET_INCOME", "FCF_TO_NET_INCOME", "CAPEX_TO_CFO"]
        if entity == "BANK":
            cash_ratios = ["CFO_TO_NET_PROFIT"]
        if not any(ratios.get(r) for r in cash_ratios):
            missing.append("cash")
        bal_ratios = ["DEBT_TO_EQUITY", "DEBT_TO_ASSETS", "INTEREST_COVERAGE", "NPL_RATIO"]
        if not any(ratios.get(r) for r in bal_ratios):
            missing.append("balance_sheet")
        return missing

    def analyze(self, symbol: str) -> Optional[HealthLatentState]:
        """Compute 5-organ health state for a single symbol."""
        conn = sqlite3.connect(self._db)

        # 1. Load raw ratios
        ratios = self._load_ratios(conn, symbol)
        if not ratios:
            conn.close()
            return None

        # 2. Load financial facts for ROIC
        facts = self._load_facts(conn, symbol)

        # 3. Determine entity type
        entity_type = self._get_entity_type(conn, symbol)

        # 4. Compute organ scores
        no_data_organs = self._detect_no_data(ratios, entity_type)
        profitability = self._score_profitability(ratios, entity_type)
        cash = self._score_cash(ratios, entity_type)
        balance_sheet = self._score_balance_sheet(ratios, entity_type, facts)
        efficiency = self._score_efficiency(ratios, entity_type, facts)
        moat = self._score_moat(ratios, facts)

        organs = OrganScores(
            profitability=round(profitability, 4),
            cash=round(cash, 4),
            balance_sheet=round(balance_sheet, 4),
            efficiency=round(efficiency, 4),
            moat=round(moat, 4),
            vector=[round(profitability, 4), round(cash, 4), round(balance_sheet, 4),
                    round(efficiency, 4), round(moat, 4)],
        )

        # 5. Classify archetype
        archetype, confidence = self._classify_archetype(organs, no_data_organs)

        # 6. Get metadata
        periods = conn.execute(
            "SELECT COUNT(DISTINCT period) FROM health_ratios WHERE symbol=?", (symbol,)
        ).fetchone()[0]
        latest = conn.execute(
            "SELECT MAX(period) FROM health_ratios WHERE symbol=?", (symbol,)
        ).fetchone()[0]

        conn.close()

        arch_def = ARCHETYPES.get(archetype, {})
        return HealthLatentState(
            symbol=symbol,
            entity_type=entity_type,
            organs=organs,
            archetype=archetype,
            archetype_label=arch_def.get("label", archetype),
            archetype_desc=arch_def.get("desc", ""),
            archetype_confidence=round(confidence, 4),
            data_periods=periods,
            latest_period=latest or "",
            no_data_organs=no_data_organs,
        )

    def analyze_many(self, symbols: list[str]) -> dict[str, Optional[HealthLatentState]]:
        return {s: self.analyze(s) for s in symbols}

    # ── Data Loading ─────────────────────────────────────────────

    @staticmethod
    def _load_ratios(conn, symbol: str) -> dict:
        """Load ALL historical ratios for a symbol.

        Returns: {ratio_name: [(period, value), ...]}
        """
        rows = conn.execute(
            "SELECT period, ratio_name, ratio_value FROM health_ratios "
            "WHERE symbol=? ORDER BY period", (symbol,)
        ).fetchall()

        result = defaultdict(list)
        for period, rname, rval in rows:
            if rval is not None:
                result[rname].append((period, rval))
        return dict(result)

    @staticmethod
    def _load_facts(conn, symbol: str) -> dict:
        """Load all financial facts for a symbol.

        Returns: {metric: [(period, value), ...]}
        """
        try:
            rows = conn.execute(
                "SELECT period, metric, value FROM financial_facts "
                "WHERE symbol=? ORDER BY period", (symbol,)
            ).fetchall()
        except Exception:
            return {}

        result = defaultdict(list)
        for period, metric, val in rows:
            if val is not None:
                result[metric].append((period, val))
        return dict(result)

    @staticmethod
    def _get_entity_type(conn, symbol: str) -> str:
        row = conn.execute(
            "SELECT entity_type FROM entity_registry WHERE symbol=?", (symbol,)
        ).fetchone()
        return row[0] if row else "STANDARD"

    # ── Helper: Trend / Persistence ──────────────────────────────

    @staticmethod
    def _recent_values(series: list, n: int = 8) -> list[float]:
        """Get last n values from a time series [(period, val), ...]."""
        return [v for _, v in series[-n:]]

    @staticmethod
    def _trend(series: list, n: int = 8) -> float:
        """Compute linear trend slope over last n periods.

        Normalized: positive = improving, negative = declining.
        Returns: slope in [-1, 1]
        """
        vals = CompanyHealthV2._recent_values(series, n)
        if len(vals) < 3:
            return 0.0
        x = np.arange(len(vals))
        y = np.array(vals)
        slope, _ = np.polyfit(x, y, 1)
        max_abs = max(abs(y)) if max(abs(y)) > 0 else 1
        return float(np.clip(slope / max_abs, -1.0, 1.0))

    @staticmethod
    def _stability(series: list, n: int = 8) -> float:
        """Compute stability as inverse of CV (coefficient of variation)."""
        vals = CompanyHealthV2._recent_values(series, n)
        if len(vals) < 3:
            return 0.0
        mean_v = np.mean(vals)
        std_v = np.std(vals)
        if mean_v == 0:
            return 0.0
        cv = std_v / abs(mean_v)
        return float(np.clip(1.0 - cv, 0.0, 1.0))

    @staticmethod
    def _current_value(series: list) -> float:
        """Get most recent value."""
        return series[-1][1] if series else 0.0

    @staticmethod
    def _z_score(val: float, series: list) -> float:
        """Compute z-score of current value vs historical."""
        vals = CompanyHealthV2._recent_values(series, 20)
        if len(vals) < 3:
            return 0.0
        mean_v = np.mean(vals)
        std_v = np.std(vals)
        if std_v == 0:
            return 0.0
        return float((val - mean_v) / std_v)

    # ── Organ: Profitability ─────────────────────────────────────
    # ROE trend, ROA level, Margin stability

    def _score_profitability(self, ratios: dict, entity: str) -> float:
        roe = ratios.get("ROE", [])
        roa = ratios.get("ROA", [])
        margin = ratios.get("NET_MARGIN", [])
        gross = ratios.get("GROSS_MARGIN", [])

        if not roe and not roa:
            return 0.0

        scores = []

        # ROE: level + trend
        if roe:
            roe_now = self._current_value(roe)
            roe_z = self._z_score(roe_now, roe)
            roe_trend = self._trend(roe, 8)
            # Level: z-score, capped at [-2,2], mapped to [0,1]
            level = float(np.clip((roe_z + 2.0) / 4.0, 0, 1))
            # Trend: positive = good
            trend = float(np.clip(roe_trend * 5 + 0.5, 0, 1))
            scores.append(level * 0.6 + trend * 0.4)

        # ROA level
        if roa:
            roa_now = self._current_value(roa)
            roa_z = self._z_score(roa_now, roa)
            scores.append(float(np.clip((roa_z + 2.0) / 4.0, 0, 1)))

        # Margin stability
        if margin:
            scores.append(self._stability(margin, 8))
        if gross:
            scores.append(self._stability(gross, 8))

        return float(np.mean(scores)) if scores else 0.0

    # ── Organ: Cash ─────────────────────────────────────────────
    # OCF level, FCF trend, conversion quality

    def _score_cash(self, ratios: dict, entity: str) -> float:
        cfo_ni = ratios.get("CFO_TO_NET_INCOME", [])
        if entity == "BANK":
            cfo_ni = ratios.get("CFO_TO_NET_PROFIT", cfo_ni)
        fcf_ni = ratios.get("FCF_TO_NET_INCOME", [])
        capex_cfo = ratios.get("CAPEX_TO_CFO", [])

        scores = []

        # CFO/NI level: > 1.0 is excellent
        if cfo_ni:
            cfo_now = self._current_value(cfo_ni)
            level = float(np.clip(cfo_now / 2.0, 0, 1))
            trend = self._trend(cfo_ni, 8)
            trend_score = float(np.clip(trend * 5 + 0.5, 0, 1))
            scores.append(level * 0.6 + trend_score * 0.4)

        # FCF/NI level
        if fcf_ni:
            fcf_now = self._current_value(fcf_ni)
            fcf_level = float(np.clip((fcf_now + 1) / 2, 0, 1))
            scores.append(fcf_level)

        # CAPEX/CFO: lower is better (inverted)
        if capex_cfo:
            cfo_now = self._current_value(capex_cfo)
            scores.append(float(np.clip(1.0 - cfo_now, 0, 1)))

        return float(np.mean(scores)) if scores else 0.0

    # ── Organ: Balance Sheet ────────────────────────────────────
    # Debt trend, Interest coverage, NPL (bank)

    def _score_balance_sheet(self, ratios: dict, entity: str, facts: dict) -> float:
        debt_eq = ratios.get("DEBT_TO_EQUITY", [])
        debt_as = ratios.get("DEBT_TO_ASSETS", [])
        int_cov = ratios.get("INTEREST_COVERAGE", [])
        npl = ratios.get("NPL_RATIO", [])

        scores = []

        if debt_eq:
            d = self._current_value(debt_eq)
            trend = self._trend(debt_eq, 8)
            # D/E < 1 = good, > 3 = bad (except for banks)
            if entity == "BANK":
                level = float(np.clip(1.0 - (d - 2) / 15.0, 0, 1))  # banks have high D/E
            else:
                level = float(np.clip(1.0 - d / 3.0, 0, 1))
            trend_score = float(np.clip(1.0 - trend * 5, 0, 1))  # negative trend = improving
            scores.append(level * 0.5 + trend_score * 0.5)

        # Interest coverage: > 3x is safe
        if int_cov:
            ic = self._current_value(int_cov)
            scores.append(float(np.clip(ic / 5.0, 0, 1)))

        # NPL ratio (banks): < 2% is good
        if npl:
            npl_now = self._current_value(npl)
            npl_trend = self._trend(npl, 8)
            level = float(np.clip(1.0 - npl_now / 0.05, 0, 1))  # 5% = zero
            trend_score = float(np.clip(1.0 - npl_trend * 10, 0, 1))
            scores.append(level * 0.6 + trend_score * 0.4)

        return float(np.mean(scores)) if scores else 0.0

    # ── Organ: Efficiency ───────────────────────────────────────
    # ROIC level, Asset turnover

    def _score_efficiency(self, ratios: dict, entity: str, facts: dict) -> float:
        asset_turnover = ratios.get("ASSET_TURNOVER", [])
        roe = ratios.get("ROE", [])
        nim = ratios.get("NIM", [])
        scores = []

        # ROIC from financial facts (or ROE as proxy)
        roic_vals = self._compute_roic(facts)
        if roic_vals:
            roic_now = self._current_value(roic_vals)
            roic_z = self._z_score(roic_now, roic_vals)
            scores.append(float(np.clip((roic_z + 2.0) / 4.0, 0, 1)))
        elif roe:
            roe_now = self._current_value(roe)
            roe_z = self._z_score(roe_now, roe)
            scores.append(float(np.clip((roe_z + 2.0) / 4.0, 0, 1)))

        # Asset turnover
        if asset_turnover:
            at = self._current_value(asset_turnover)
            scores.append(float(np.clip(at * 5, 0, 1)))

        # NIM for banks
        if nim:
            n = self._current_value(nim)
            scores.append(float(np.clip(n / 0.05, 0, 1)))

        return float(np.mean(scores)) if scores else 0.0

    @staticmethod
    def _compute_roic(facts: dict) -> list:
        """Compute ROIC from financial facts over time.

        ROIC = NOPAT / Invested Capital
        NOPAT = EBIT * 0.8 (VN corporate tax ~20%)
        Invested Capital = Total Equity + Total Debt - Cash
        """
        ebit = facts.get("EBIT", [])
        equity = facts.get("TOTAL_EQUITY", [])
        debt = facts.get("TOTAL_DEBT", [])
        cash = facts.get("CASH_EQUIV", [])

        if not (ebit and equity):
            return []

        # Build period-indexed dicts
        def to_dict(series):
            return {p: v for p, v in series}

        ebit_d = to_dict(ebit)
        equity_d = to_dict(equity)
        debt_d = to_dict(debt) if debt else {}
        cash_d = to_dict(cash) if cash else {}

        roic_vals = []
        periods = sorted(set(ebit_d.keys()) & set(equity_d.keys()))
        for p in periods:
            nopat = ebit_d[p] * 0.8
            inv_cap = equity_d[p] + debt_d.get(p, 0) - cash_d.get(p, 0)
            if inv_cap > 0:
                roic_vals.append((p, nopat / inv_cap))
        return roic_vals

    # ── Organ: Moat ──────────────────────────────────────────────
    # ROIC persistence (5Y), Gross margin persistence

    def _score_moat(self, ratios: dict, facts: dict) -> float:
        scores = []

        # ROIC persistence
        roic_vals = self._compute_roic(facts)
        if roic_vals:
            persistence = self._stability(roic_vals, 20)
            level = self._z_score(self._current_value(roic_vals), roic_vals)
            level_score = float(np.clip((level + 2.0) / 4.0, 0, 1))
            scores.append(persistence * 0.5 + level_score * 0.5)

        # Gross margin persistence
        gross = ratios.get("GROSS_MARGIN", [])
        if gross:
            margin_stability = self._stability(gross, 20)
            margin_level = self._current_value(gross)
            level_score = float(np.clip(margin_level / 0.6, 0, 1))  # 60%+ = excellent
            scores.append(margin_stability * 0.5 + level_score * 0.5)

        # ROE persistence (fallback)
        if not roic_vals:
            roe = ratios.get("ROE", [])
            if roe:
                roe_stability = self._stability(roe, 20)
                scores.append(roe_stability)

        return float(np.mean(scores)) if scores else 0.0

    # ── Archetype Classification ─────────────────────────────────

    @staticmethod
    def _classify_archetype(organs: OrganScores, no_data: list[str] = None) -> tuple[str, float]:
        """Map 5-organ vector to latent archetype.

        no_data: danh sách organ THIẾU dữ liệu (score 0.0 giả tạo). Các organ
        này bị LOẠI khỏi điều kiện DISTRESSED — không được dùng số 0 giả để
        kết luận "suy yếu" khi thực tế chỉ là không có dữ liệu.
        """
        no_data = no_data or []
        p, c, b, e, m = organs.profitability, organs.cash, organs.balance_sheet, organs.efficiency, organs.moat

        candidates = []

        # HIGH_QUALITY_COMPOUNDER: all organs strong
        if (p >= ARCHETYPES["HIGH_QUALITY_COMPOUNDER"]["profitability_min"] and
                c >= ARCHETYPES["HIGH_QUALITY_COMPOUNDER"]["cash_min"] and
                b >= ARCHETYPES["HIGH_QUALITY_COMPOUNDER"]["balance_sheet_min"] and
                e >= ARCHETYPES["HIGH_QUALITY_COMPOUNDER"]["efficiency_min"] and
                m >= ARCHETYPES["HIGH_QUALITY_COMPOUNDER"]["moat_min"]):
            confidence = np.mean([p, c, b, e, m])
            candidates.append(("HIGH_QUALITY_COMPOUNDER", confidence))

        # STEADY_EARNER: moderate across the board
        if (p >= ARCHETYPES["STEADY_EARNER"]["profitability_min"] and
                c >= ARCHETYPES["STEADY_EARNER"]["cash_min"] and
                b >= ARCHETYPES["STEADY_EARNER"]["balance_sheet_min"]):
            confidence = np.mean([p, c, b])
            candidates.append(("STEADY_EARNER", confidence))

        # LEVERAGED_GROWTH: weak balance sheet but decent profitability
        if (b <= ARCHETYPES["LEVERAGED_GROWTH"]["balance_sheet_max"] and
                p >= ARCHETYPES["LEVERAGED_GROWTH"]["profitability_min"] and
                e >= ARCHETYPES["LEVERAGED_GROWTH"]["efficiency_min"]):
            confidence = np.mean([p, e]) * 0.7
            candidates.append(("LEVERAGED_GROWTH", confidence))

        # CYCLICAL: moderate profitability, weak moat
        if (p >= ARCHETYPES["CYCLICAL"]["profitability_min"] and
                m <= ARCHETYPES["CYCLICAL"]["moat_max"]):
            confidence = np.mean([p, 1 - m]) * 0.6
            candidates.append(("CYCLICAL", confidence))

        # DISTRESSED: all weak (bỏ qua organ NO_DATA)
        distressed_weak = []
        distressed_score = []
        if "profitability" not in no_data:
            distressed_weak.append(p <= ARCHETYPES["DISTRESSED"]["profitability_max"])
            distressed_score.append(p)
        if "cash" not in no_data:
            distressed_weak.append(c <= ARCHETYPES["DISTRESSED"]["cash_max"])
            distressed_score.append(c)
        if "balance_sheet" not in no_data:
            distressed_weak.append(b <= ARCHETYPES["DISTRESSED"]["balance_sheet_max"])
            distressed_score.append(b)
        if distressed_weak and any(distressed_weak):
            confidence = 1.0 - np.mean(distressed_score)
            candidates.append(("DISTRESSED", confidence))

        # COMMODITY: no moat, profitability tied to cycle
        if (m <= ARCHETYPES["COMMODITY"]["moat_max"] and
                p >= ARCHETYPES["COMMODITY"]["profitability_min"]):
            confidence = (1.0 - m) * 0.5
            candidates.append(("COMMODITY", confidence))

        if not candidates:
            return "STEADY_EARNER", 0.35

        # Pick highest confidence
        candidates.sort(key=lambda x: -x[1])
        return candidates[0]

    # ── Persistence ─────────────────────────────────────────────

    @staticmethod
    def save_report(states: dict[str, HealthLatentState], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            sym: asdict(st) for sym, st in states.items() if st is not None
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── CLI Helper ───────────────────────────────────────────────────

def print_health_report(states: dict[str, Optional[HealthLatentState]]) -> None:
    """Print human-readable health latent state report."""
    print(f"\n  {'='*70}")
    print(f"  COMPANY HEALTH v2 — 5-ORGAN LATENT STATE")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {'='*70}")
    print(f"  {'Symbol':<8} {'Type':<10} {'Prof':>6} {'Cash':>6} {'Bal':>6} {'Eff':>6} {'Moat':>6} {'Archetype':<35}")
    print(f"  {'─'*70}")

    for sym, state in states.items():
        if state is None:
            print(f"  {sym:<8} {'⚠ NO DATA':<50}")
            continue
        o = state.organs
        cash_str = " NO DATA" if "cash" in (state.no_data_organs or []) else f"{o.cash:>6.2f}"
        bal_str = " NO DATA" if "balance_sheet" in (state.no_data_organs or []) else f"{o.balance_sheet:>6.2f}"
        print(f"  {sym:<8} {state.entity_type:<10} {o.profitability:>6.2f} {cash_str} {bal_str} "
              f"{o.efficiency:>6.2f} {o.moat:>6.2f} "
              f"{state.archetype_label:<35}")

    print(f"  {'='*70}\n")

    # Detail view
    for sym, state in states.items():
        if state is None:
            continue
        o = state.organs
        print(f"  ┌─ {sym} ({state.entity_type}) — {state.archetype_label}")
        print(f"  │  Archetype: {state.archetype} (P={state.archetype_confidence:.2%})")
        print(f"  │  Vector:    [{o.profitability:.3f} {o.cash:.3f} {o.balance_sheet:.3f} {o.efficiency:.3f} {o.moat:.3f}]")
        print(f"  │  Data:      {state.data_periods} periods, latest {state.latest_period}")
        print(f"  │")
        no_data = state.no_data_organs or []
        for name, key, val in [("Profitability ", "profitability", o.profitability),
                               ("Cash         ", "cash", o.cash),
                               ("Balance Sheet", "balance_sheet", o.balance_sheet),
                               ("Efficiency   ", "efficiency", o.efficiency),
                               ("Moat         ", "moat", o.moat)]:
            if key in no_data:
                print(f"  │  {name}: NO DATA")
                continue
            bar = "▓" * int(val * 20) + "░" * (20 - int(val * 20))
            color = "🟢" if val > 0.6 else "🟡" if val > 0.3 else "🔴"
            print(f"  │  {name}: {val:5.3f} {color} |{bar}|")
        print(f"  └──")
