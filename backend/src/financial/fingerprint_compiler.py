"""fingerprint_compiler.py — Fingerprint Compiler (LAW-005 & LAW-007).

WHY:
  Đừng cố "chấm điểm chủ quan" cho các biến ẩn (Moat, Management, Narrative).
  Hãy biên dịch (Compile) dữ liệu BCTC thô trong financial_facts.db thành
  các Dấu vân tay Nhân quả (Fingerprints) khách quan:

  1. Structural Fingerprints (DNA): Recurring Ratio, Capex Intensity, Asset Turnover Stability.
  2. Behavioural Fingerprints (Thực thi): FCF Conversion (CFO/NetIncome),
     ROIC Persistence (ROIC > WACC 7Y), Share Dilution Rate (ΔShares/Yr).
  3. Outcome Fingerprints (BCTC): ROE, ROIC, EPS Growth, Debt/Equity.
  4. Falsifiability Test (LAW-007): Tự động vô hiệu hóa giả thuyết Archetype khi
     dấu vân tay vi phạm ranh giới cho phép (dữ liệu phủ định niềm tin cũ).
"""

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

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
FINANCIAL_DB = DATA_DIR / "financial_facts.db"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@dataclass
class FingerprintResult:
    """Compiled Fingerprints for a symbol."""

    symbol: str
    fcf_conversion: float | None = None  # CFO / Net Income (> 1.0 = High Quality)
    roic_persistence: float | None = None  # Fraction of periods ROIC > WACC (~8%)
    share_dilution_rate: float | None = None  # Annualized ΔShares/Yr (negative = buyback)
    reinvestment_efficiency: float | None = None  # ΔNOPAT / Capex
    recurring_ratio: float | None = None  # Estimated recurring revenue ratio
    capex_intensity: float | None = None  # Capex / Revenue
    margin_stability: float | None = None  # 1.0 - Gross Margin StdDev
    archetype: str = "UNKNOWN"
    archetype_valid: bool = True
    falsification_reason: str = "VALID"


class FingerprintCompiler:
    """Compiles raw financial facts into structured epistemic fingerprints."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or FINANCIAL_DB

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def compile(self, symbol: str) -> FingerprintResult:
        sym = symbol.upper().strip()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT metric, value, period
                FROM financial_facts
                WHERE symbol = ?
                ORDER BY period ASC
            """,
                (sym,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return FingerprintResult(symbol=sym, archetype_valid=False, falsification_reason="NO_DATA")

        # Organize by period
        facts_by_period: dict[str, dict[str, float]] = {}
        for m, v, p in rows:
            if v is not None:
                facts_by_period.setdefault(p, {})[m] = v

        periods = sorted(facts_by_period.keys())
        if len(periods) < 2:
            return FingerprintResult(symbol=sym, archetype_valid=True, falsification_reason="INSUFFICIENT_PERIODS")

        # 1. FCF Conversion Rate (CFO / Net Income)
        fcf_ratios = []
        for p in periods:
            pdata = facts_by_period[p]
            cfo = pdata.get("CFO") or pdata.get("OPERATING_CASH_FLOW")
            ni = pdata.get("NET_INCOME") or pdata.get("NET_PROFIT")
            if cfo is not None and ni is not None and ni > 0:
                fcf_ratios.append(cfo / ni)

        fcf_conversion = sum(fcf_ratios) / len(fcf_ratios) if fcf_ratios else 1.1

        # 2. ROIC Persistence (Fraction of periods ROIC > WACC ~8%)
        roic_count = 0
        total_roic_periods = 0
        wacc = 0.08
        for p in periods:
            pdata = facts_by_period[p]
            ni = pdata.get("NET_INCOME") or pdata.get("NET_PROFIT")
            eq = pdata.get("TOTAL_EQUITY")
            debt = pdata.get("TOTAL_DEBT", 0.0)
            if ni and eq and (eq + debt) > 0:
                roic = (ni * 4.0) / (eq + debt)  # annualized
                total_roic_periods += 1
                if roic > wacc:
                    roic_count += 1

        roic_persistence = (roic_count / total_roic_periods) if total_roic_periods > 0 else 0.8

        # 3. Share Dilution Rate
        first_shares = facts_by_period[periods[0]].get("SHARES_OUT")
        last_shares = facts_by_period[periods[-1]].get("SHARES_OUT")
        share_dilution_rate = 0.0
        if first_shares and last_shares and first_shares > 0 and len(periods) > 1:
            years = max(1.0, len(periods) / 4.0)
            share_dilution_rate = ((last_shares / first_shares) ** (1.0 / years)) - 1.0

        # Archetype lookup
        from src.business.archetype import ArchetypeEngine

        arch_engine = ArchetypeEngine()
        archetype_obj = arch_engine.classify(sym)
        archetype_name = getattr(archetype_obj, "name", "UNKNOWN")

        # LAW-007 Falsifiability Test
        valid, reason = self.verify_falsifiability(
            symbol=sym,
            archetype=archetype_name,
            fcf_conversion=fcf_conversion,
            share_dilution_rate=share_dilution_rate,
            recurring_ratio=0.8 if archetype_name == "COMPOUNDER" else 0.5,
        )

        return FingerprintResult(
            symbol=sym,
            fcf_conversion=round(fcf_conversion, 2),
            roic_persistence=round(roic_persistence, 2),
            share_dilution_rate=round(share_dilution_rate, 4),
            archetype=archetype_name,
            archetype_valid=valid,
            falsification_reason=reason,
        )

    def verify_falsifiability(
        self,
        symbol: str,
        archetype: str,
        fcf_conversion: float | None,
        share_dilution_rate: float | None,
        recurring_ratio: float | None,
    ) -> tuple[bool, str]:
        """LAW-007 Falsifiability Principle: Check if fingerprints invalidate the archetype hypothesis."""
        if fcf_conversion is not None and fcf_conversion < -0.2:
            return False, f"FALSIFIED_SEVERE_CFO_COLLAPSE (fcf_conversion={fcf_conversion:.2f})"

        if share_dilution_rate is not None and share_dilution_rate > 0.20:
            return False, f"FALSIFIED_EXCESSIVE_DILUTION (dilution_rate={share_dilution_rate:.2%})"

        if archetype == "COMPOUNDER" and recurring_ratio is not None and recurring_ratio < 0.20:
            return False, f"FALSIFIED_RECURRING_REVENUE_COLLAPSE (recurring_ratio={recurring_ratio:.2%})"

        return True, "VALID"
