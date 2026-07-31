"""causal_edge.py — Sprint 3: CausalEdge Structure (lag, confidence, half-life, counter-examples).

Formalizes each macro→sector→company causal link with:
  - lag_range: (min_days, max_days) expected delay
  - confidence: [0,1] how certain the link is
  - half_life: days until 50% effect decay
  - attenuation: [0,1] information loss per hop
  - counter_examples: historical dates where the link did NOT hold

Architecture:
  CausalGraph holds all edges in a DAG.
  propagate() computes effective impact with lag-corrected confidence.
  trace_path() returns the full chain with per-hop parameters.

# ===================================================================
# ADR #4 — WHY Attenuation & Effective Lag in CausalEdge?
# ===================================================================
# Tac dong vi mo khong xay ra tuc thoi. Do tre lag_range (90-300 ngay)
# va suy giam attenuation (10-28%) mo hinh hoa chinh xac thoi gian
# chuyen hoa tu chinh sach/song nganh vao BCTC doanh nghiep.
# Moi hop giam tu 10-15% thong tin (attenuation tich luy).
# ===================================================================
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ── Edge definition ──────────────────────────────────────────────────────


@dataclass
class CausalEdge:
    """One directed causal link in the macro→sector→company graph.

    Fields:
      id:             unique edge identifier (e.g. 'CREDIT_STRESS→BANK_NPL')
      source:         upstream node label
      target:         downstream node label
      edge_type:      MACRO→MACRO | MACRO→SECTOR | MACRO→COMPANY |
                      SECTOR→COMPANY | COMPANY→METRIC
      lag_min:        minimum expected lag in trading days
      lag_max:        maximum expected lag
      confidence:     [0, 1] — how certain the causal link is
      half_life:      days until effect decays 50%
      attenuation:    [0, 1] — information loss fraction per hop
      archetype:      archetype this edge applies to (None = universal)
      factor_id:      linked FactorDef id (from factor_exposure.py)
      counter_examples: list of ISO date strings where link failed
      description:    human-readable explanation
    """
    id: str
    source: str
    target: str
    edge_type: str
    lag_min: int = 0
    lag_max: int = 90
    confidence: float = 0.5
    half_life: float = 30.0
    attenuation: float = 0.0
    archetype: Optional[str] = None
    factor_id: Optional[str] = None
    counter_examples: List[str] = field(default_factory=list)
    description: str = ""
    created_at: str = ""
    updated_at: str = ""


# ── Edge Registry ────────────────────────────────────────────────────────
# Expert-calibrated edges connecting macro → sector → company per archetype.

def _now() -> str:
    return datetime.now().isoformat()


def build_edge_registry() -> Dict[str, CausalEdge]:
    """Build the full CausalEdge registry.

    Sources are grouped by archetype for clarity.
    """
    now = _now()
    E: Dict[str, CausalEdge] = {}

    def _add(
        eid: str, src: str, tgt: str, etype: str,
        lag_min: int = 0, lag_max: int = 90,
        confidence: float = 0.5, half_life: float = 30.0,
        attenuation: float = 0.0, arch: Optional[str] = None,
        factor: Optional[str] = None, desc: str = "",
    ):
        E[eid] = CausalEdge(
            id=eid, source=src, target=tgt, edge_type=etype,
            lag_min=lag_min, lag_max=lag_max,
            confidence=confidence, half_life=half_life,
            attenuation=attenuation, archetype=arch, factor_id=factor,
            description=desc, created_at=now, updated_at=now,
        )

    # ═══════════════════════════════════════════════════════════════════
    # UNIVERSAL (no archetype) — Macro → Transmission → Sector
    # ═══════════════════════════════════════════════════════════════════
    #
    # ── World Layer (P0.5) — 8 Fed→Vietnam transmission edges ──
    # WHY: Fed policy is a SIGNAL GENERATOR, not a direct Vietnam driver.
    #      Each edge forces the signal to pass through measurable intermediate
    #      variables (DXY, US10Y, Global Liquidity) before reaching Vietnam
    #      macro state. This prevents the logical fallacy "Fed hawk → sell VN."
    #      Reference: ADR #8 (world_sensor.py).
    #
    # Chain: FED_TARGET_RATE → DXY_USD → USD_VND (VN FX pressure)
    #        FED_TARGET_RATE → US10Y_YIELD → INTEREST_RATE (VN rates)
    #        FOMC_DISSENT → FED_UNCERTAINTY → RISK_OFF_FLIGHT (fear)
    #        QT_IMPULSE → GLOBAL_LIQUIDITY → LIQUIDITY_TRAP (liquidity)
    # ═══════════════════════════════════════════════════════════════════

    # Group A: Fed Policy → US Financial Conditions (3 edges)
    # WHY lag/confidence values for World edges:
    #   FED→DXY (1-30d, 0.80): Rate differentials take days-weeks to fully
    #     transmit into FX, but confidence is high due to UIP theory.
    #   FED→US10Y (1-10d, 0.90): Bond market reprices expectations within days,
    #     highest confidence of all world links.
    #   DISSENT→UNCERTAINTY (0-5d, 0.60): Dissent ≠ immediate policy change,
    #     it measures dispersion (weaker signal).
    #   QT→LIQUIDITY (10-60d, 0.70): Balance sheet runoff is slow and telegraphed.
    #   UNCERTAINTY→RISK_OFF (0-3d, 0.65): Fear is fast but less predictable.
    #   DXY→USD_VND (1-10d, 0.85): SBV actively manages VND → smoother pass-through.
    #   US10Y→INTEREST_RATE (5-30d, 0.65): VN rates partially decoupled from US.
    #   GLOBAL_LIQUIDITY→LIQUIDITY_TRAP (3-21d, 0.60): Weakest link due to
    #     SBV buffer (FX reserves, policy tools).
    _add("FED_TARGET→DXY_USD", "FED_TARGET_RATE", "DXY_USD",
         "MACRO→MACRO", lag_min=1, lag_max=30, confidence=0.80, half_life=60,
         attenuation=0.10,
         desc="Fed rate change → 1-30 days → USD index (rate differential channel)")

    _add("FED_TARGET→US10Y", "FED_TARGET_RATE", "US10Y_YIELD",
         "MACRO→MACRO", lag_min=1, lag_max=10, confidence=0.90, half_life=45,
         attenuation=0.05,
         desc="Fed rate → 1-10 days → US 10Y yield (expectations channel)")

    _add("FOMC_DISSENT→FED_UNCERTAINTY", "FOMC_DISSENT", "FED_UNCERTAINTY",
         "MACRO→MACRO", lag_min=0, lag_max=5, confidence=0.60, half_life=90,
         attenuation=0.15,
         desc="FOMC dissent vote → 0-5 days → policy uncertainty premium")

    # Group B: US Financial Conditions → Global Transmission (2 edges)
    _add("QT_IMPULSE→GLOBAL_LIQUIDITY", "QT_IMPULSE", "GLOBAL_LIQUIDITY",
         "MACRO→MACRO", lag_min=10, lag_max=60, confidence=0.70, half_life=120,
         attenuation=0.20,
         desc="Fed QT runoff → 2-8 weeks → global USD liquidity scarcity")

    _add("FED_UNCERTAINTY→RISK_OFF", "FED_UNCERTAINTY", "RISK_OFF_FLIGHT",
         "MACRO→MACRO", lag_min=0, lag_max=3, confidence=0.65, half_life=30,
         attenuation=0.10,
         desc="Policy uncertainty → 0-3 days → risk-off flight-to-safety")

    # Group C: Transmission → Vietnam Macro State (3 edges)
    _add("DXY_USD→VN_FX", "DXY_USD", "USD_VND",
         "MACRO→MACRO", lag_min=1, lag_max=10, confidence=0.85, half_life=30,
         attenuation=0.08,
         desc="USD strength → 1-10 days → VND depreciation pressure")

    _add("US10Y→VN_RATES", "US10Y_YIELD", "INTEREST_RATE",
         "MACRO→MACRO", lag_min=5, lag_max=30, confidence=0.65, half_life=60,
         attenuation=0.20,
         desc="US 10Y yield → 1-4 weeks → Vietnam domestic rate corridor")

    _add("GLOBAL_LIQUIDITY→VN_LIQUIDITY", "GLOBAL_LIQUIDITY", "LIQUIDITY_TRAP",
         "MACRO→MACRO", lag_min=3, lag_max=21, confidence=0.60, half_life=45,
         attenuation=0.25,
         desc="Global liquidity → 3-21 days → Vietnam interbank liquidity tightness")

    _add("CREDIT_STRESS→LIQUIDITY_CRUNCH", "CREDIT_STRESS", "LIQUIDITY_TRAP",
         "MACRO→MACRO", lag_min=1, lag_max=10, confidence=0.85, half_life=45,
         attenuation=0.10,
         desc="Credit stress causes immediate liquidity hoarding at interbank")

    _add("LIQUIDITY_TRAP→SECTOR_ALL", "LIQUIDITY_TRAP", "SECTOR_LIQUIDITY",
         "MACRO→SECTOR", lag_min=3, lag_max=21, confidence=0.70, half_life=30,
         attenuation=0.20,
         desc="Liquidity trap propagates to sector-level funding within 1-4 weeks")

    _add("CREDIT_STRESS→BEHAVIOR_FEAR", "CREDIT_STRESS", "RISK_OFF_FLIGHT",
         "MACRO→MACRO", lag_min=0, lag_max=3, confidence=0.90, half_life=20,
         attenuation=0.05,
         desc="Credit stress instantly triggers risk-off behavior")

    _add("INFLATION_SHOCK→OVERHEATING", "INFLATION_SHOCK", "OVERHEATING",
         "MACRO→MACRO", lag_min=10, lag_max=60, confidence=0.65, half_life=90,
         attenuation=0.15,
         desc="Inflation shock feeds into overheating with 2-12 week lag")

    _add("AI_BOOM→IT_SECTOR", "AI_BOOM", "SECTOR_IT",
         "MACRO→SECTOR", lag_min=5, lag_max=30, confidence=0.75, half_life=120,
         attenuation=0.10,
         desc="AI boom directly boosts IT sector demand within 1-6 weeks")

    _add("STABLE→SECTOR_FUNDAMENTALS", "STABLE", "SECTOR_FUNDAMENTALS",
         "MACRO→SECTOR", lag_min=0, lag_max=15, confidence=0.60, half_life=60,
         attenuation=0.20,
         desc="Stable macro allows sector fundamentals to drive")

    _add("RECOVERY→SECTOR_EARLY", "RECOVERY", "EARLY",
         "MACRO→SECTOR", lag_min=5, lag_max=30, confidence=0.70, half_life=45,
         attenuation=0.15,
         desc="Recovery triggers early-cycle sector rotation within 1-6 weeks")

    _add("RISK_OFF→SECTOR_WEAKENING", "RISK_OFF", "WEAKENING",
         "MACRO→SECTOR", lag_min=0, lag_max=5, confidence=0.85, half_life=25,
         attenuation=0.10,
         desc="Risk-off instantly weakens most sector flows")

    # ═══════════════════════════════════════════════════════════════════
    # COMPOUNDER (FPT, HPG, DGC)
    # ═══════════════════════════════════════════════════════════════════
    # Primary chain: AI_CAPEX → IT_BACKLOG → IT_REVENUE → IT_MARGIN
    # Sensitivity: AI_CAPEX, GOV_IT_BUDGET, CORP_EARNINGS, GDP_GROWTH

    _add("COMPOUNDER:AI_CAPEX→IT_BACKLOG", "AI_CAPEX", "IT_BACKLOG",
         "MACRO→COMPANY", lag_min=30, lag_max=120, confidence=0.75, half_life=180,
         attenuation=0.20, arch="COMPOUNDER", factor="AI_CAPEX",
         desc="AI capex commitment → 1-4 months → IT service backlog build")

    _add("COMPOUNDER:IT_BACKLOG→IT_REVENUE", "IT_BACKLOG", "IT_REVENUE",
         "COMPANY→METRIC", lag_min=60, lag_max=180, confidence=0.85, half_life=120,
         attenuation=0.10, arch="COMPOUNDER", factor="AI_CAPEX",
         desc="Backlog → 2-6 months → recognised revenue")

    _add("COMPOUNDER:GOV_IT→IT_BACKLOG", "GOV_IT_BUDGET", "IT_BACKLOG",
         "MACRO→COMPANY", lag_min=45, lag_max=150, confidence=0.60, half_life=120,
         attenuation=0.25, arch="COMPOUNDER", factor="GOV_IT_BUDGET",
         desc="Gov IT budget approval → 1.5-5 months → backlog (slower procurement)")

    # ═══════════════════════════════════════════════════════════════════
    # CYCLICAL_HEAVY (HPG, DGC)
    # ═══════════════════════════════════════════════════════════════════
    # Primary chain: STEEL_PRICE → Inventory → Gross Margin → EBITDA
    # Sensitivity: STEEL_PRICE, IRON_ORE, CONSTRUCTION, CHINA_DEMAND

    _add("CYCLICAL:STEEL_PRICE→SPREAD", "STEEL_PRICE", "STEEL_SPREAD",
         "MACRO→COMPANY", lag_min=5, lag_max=30, confidence=0.80, half_life=45,
         attenuation=0.10, arch="CYCLICAL_HEAVY", factor="STEEL_PRICE",
         desc="Steel price change → 1-6 weeks → spread movement (HRC - raw)")

    _add("CYCLICAL:SPREAD→GROSS_MARGIN", "STEEL_SPREAD", "GROSS_MARGIN",
         "COMPANY→METRIC", lag_min=15, lag_max=45, confidence=0.90, half_life=60,
         attenuation=0.05, arch="CYCLICAL_HEAVY", factor="STEEL_PRICE",
         desc="Spread converts to gross margin within 3-9 weeks (inventory turn)")

    _add("CYCLICAL:CONSTRUCTION→STEEL_VOLUME", "CONSTRUCTION", "STEEL_VOLUME",
         "MACRO→COMPANY", lag_min=15, lag_max=60, confidence=0.65, half_life=90,
         attenuation=0.20, arch="CYCLICAL_HEAVY", factor="CONSTRUCTION",
         desc="Construction activity → 3-12 weeks → steel order volume")

    _add("CYCLICAL:CHINA→STEEL_PRICE", "CHINA_DEMAND", "STEEL_PRICE",
         "MACRO→MACRO", lag_min=3, lag_max=21, confidence=0.55, half_life=30,
         attenuation=0.25, arch="CYCLICAL_HEAVY", factor="CHINA_DEMAND",
         desc="China demand change → 3-21 days → HRC price (partially decoupled)")

    # ═══════════════════════════════════════════════════════════════════
    # FRANCHISE_BANK (ACB, HDB, MBB, VCB)
    # ═══════════════════════════════════════════════════════════════════
    # Primary chain: INTEREST_RATE → NIM → CASA → Loan Book → NPL
    # Sensitivity: INTEREST_RATE, CREDIT_GROWTH, NPL_CYCLE, CASA_RATIO

    _add("BANK:INTEREST→NIM", "INTEREST_RATE", "NIM",
         "MACRO→COMPANY", lag_min=15, lag_max=60, confidence=0.85, half_life=90,
         attenuation=0.10, arch="FRANCHISE_BANK", factor="INTEREST_RATE",
         desc="Rate change → 3-12 weeks → NIM repricing (floating loans)")

    _add("BANK:INTERBANK→FUNDING_COST", "INTERBANK_ON", "FUNDING_COST",
         "MACRO→COMPANY", lag_min=0, lag_max=7, confidence=0.90, half_life=15,
         attenuation=0.05, arch="FRANCHISE_BANK", factor="INTERBANK_ON",
         desc="Interbank rate → instant to 1 week → bank funding cost")

    _add("BANK:CREDIT_GROWTH→LOAN_BOOK", "CREDIT_GROWTH", "LOAN_BOOK",
         "MACRO→COMPANY", lag_min=10, lag_max=45, confidence=0.75, half_life=90,
         attenuation=0.15, arch="FRANCHISE_BANK", factor="CREDIT_GROWTH",
         desc="SBV credit growth target → 2-9 weeks → loan book expansion")

    _add("BANK:NPL_CYCLE→PROVISIONING", "NPL_CYCLE", "NPL_PROVISIONING",
         "MACRO→COMPANY", lag_min=30, lag_max=120, confidence=0.70, half_life=180,
         attenuation=0.20, arch="FRANCHISE_BANK", factor="NPL_CYCLE",
         desc="NPL cycle deterioration → 1-4 months → provisioning expense")

    # ═══════════════════════════════════════════════════════════════════
    # ASSET_BANK (MBB, HDB, STB, VIB)
    # ═══════════════════════════════════════════════════════════════════
    # Primary chain: INTEREST_RATE → NIM → Fee Income → Loan Book
    # Sensitivity: INTEREST_RATE, INTERBANK_ON, CREDIT_STRESS

    _add("BANK2:INTEREST→NIM", "INTEREST_RATE", "NIM",
         "MACRO→COMPANY", lag_min=15, lag_max=60, confidence=0.80, half_life=90,
         attenuation=0.15, arch="ASSET_BANK", factor="INTEREST_RATE",
         desc="Rate change → 3-12 weeks → NIM repricing (asset bank, higher beta)")

    _add("BANK2:INTERBANK→FUNDING_COST", "INTERBANK_ON", "FUNDING_COST",
         "MACRO→COMPANY", lag_min=1, lag_max=10, confidence=0.85, half_life=15,
         attenuation=0.10, arch="ASSET_BANK", factor="INTERBANK_ON",
         desc="Interbank rate → 1-10 days → bank funding cost (asset banks more sensitive)")

    _add("BANK2:CREDIT_STRESS→NPL", "CREDIT_STRESS", "NPL_RATIO",
         "MACRO→COMPANY", lag_min=30, lag_max=90, confidence=0.75, half_life=180,
         attenuation=0.20, arch="ASSET_BANK", factor="CREDIT_STRESS",
         desc="Credit stress → 1-3 months → NPL ratio deterioration (retail-heavy book)")

    # ═══════════════════════════════════════════════════════════════════
    # REAL_ESTATE_DEVELOPER (VHM, KDH, NLG)
    # ═══════════════════════════════════════════════════════════════════
    # Primary chain: HOUSING_POLICY → Land Bank → Presales → Revenue
    # Sensitivity: INTEREST_RATE, HOUSING_POLICY, CONSTRUCTION

    _add("RE:INTEREST→PRESALES", "INTEREST_RATE", "PRESALES",
         "MACRO→COMPANY", lag_min=30, lag_max=120, confidence=0.70, half_life=120,
         attenuation=0.20, arch="REAL_ESTATE_DEVELOPER", factor="INTEREST_RATE",
         desc="Rate change → 1-4 months → buyer mortgage capacity → presales")

    _add("RE:HOUSING_POLICY→LAND_BANK", "HOUSING_POLICY", "LAND_BANK",
         "MACRO→COMPANY", lag_min=60, lag_max=365, confidence=0.50, half_life=365,
         attenuation=0.30, arch="REAL_ESTATE_DEVELOPER", factor="HOUSING_POLICY",
         desc="Housing policy → 2-12 months → legal clearance → land bank release")

    # ═══════════════════════════════════════════════════════════════════
    # RETAIL_PLATFORM (MWG, PNJ)
    # ═══════════════════════════════════════════════════════════════════
    # Primary: CONSUMER_SPENDING → SSS → Retail Margin → Inventory Turn
    # Sensitivity: CONSUMER_SPENDING, RETAIL_SALES, INFLATION

    _add("RETAIL:CONSUMER→SSS", "CONSUMER_SPENDING", "SAME_STORE_SALES",
         "MACRO→COMPANY", lag_min=5, lag_max=30, confidence=0.70, half_life=45,
         attenuation=0.15, arch="RETAIL_PLATFORM", factor="CONSUMER_SPENDING",
         desc="Consumer spending → 1-6 weeks → same-store sales growth")

    _add("RETAIL:INFLATION→MARGIN", "INFLATION", "RETAIL_MARGIN",
         "MACRO→COMPANY", lag_min=15, lag_max=60, confidence=0.60, half_life=60,
         attenuation=0.20, arch="RETAIL_PLATFORM", factor="CONSUMER_SPENDING",
         desc="Inflation squeeze → 3-12 weeks → retail margin compression")

    # ═══════════════════════════════════════════════════════════════════
    # REGULATED_UTILITY (GAS, POW, BWE)
    # ═══════════════════════════════════════════════════════════════════
    # Primary: OIL_PRICE → BRENT_LINK → GAS_VOLUME → EBITDA
    # Sensitivity: OIL_PRICE, GAS_VOLUME, REGULATORY_TARIFF

    _add("UTILITY:OIL→GAS_PRICE", "OIL_PRICE", "BRENT_LINK",
         "MACRO→COMPANY", lag_min=10, lag_max=45, confidence=0.80, half_life=60,
         attenuation=0.10, arch="REGULATED_UTILITY", factor="OIL_PRICE",
         desc="Brent → 2-9 weeks → gas contract price (LNG/gas link)")

    _add("UTILITY:TARIFF→EBITDA", "REGULATORY_TARIFF", "TARIFF_IMPACT",
         "MACRO→COMPANY", lag_min=30, lag_max=180, confidence=0.60, half_life=365,
         attenuation=0.25, arch="REGULATED_UTILITY", factor="REGULATORY_TARIFF",
         desc="Tariff decision → 1-6 months → revenue impact")

    # ═══════════════════════════════════════════════════════════════════
    # EXPORT_MANUFACTURER (TCM, DBC)
    # ═══════════════════════════════════════════════════════════════════
    # Primary: USD_VND → FX → Gross Margin → EBITDA
    # Sensitivity: USD_VND, GLOBAL_DEMAND, FREIGHT, TARIFF

    _add("EXPORT:FX→GROSS_MARGIN", "USD_VND", "FX_MARGIN_IMPACT",
         "MACRO→COMPANY", lag_min=5, lag_max=30, confidence=0.75, half_life=60,
         attenuation=0.10, arch="EXPORT_MANUFACTURER", factor="USD_VND",
         desc="USD/VND → 1-6 weeks → export gross margin")

    _add("EXPORT:FREIGHT→COST", "FREIGHT_COST", "LOGISTICS_COST",
         "MACRO→COMPANY", lag_min=7, lag_max=30, confidence=0.70, half_life=45,
         attenuation=0.15, arch="EXPORT_MANUFACTURER", factor="USD_VND",
         desc="Freight cost → 1-4 weeks → logistics expense")

    _add("EXPORT:TARIFF→ORDER_BOOK", "TARIFF_POLICY", "ORDER_BOOK",
         "MACRO→COMPANY", lag_min=20, lag_max=90, confidence=0.55, half_life=90,
         attenuation=0.25, arch="EXPORT_MANUFACTURER", factor="USD_VND",
         desc="Tariff policy → 3-13 weeks → export order book adjustment")

    return E


# ═══════════════════════════════════════════════════════════════════════════
# CausalGraph
# ═══════════════════════════════════════════════════════════════════════════

class CausalGraph:
    """DAG of CausalEdges with propagation, tracing, and counter-example verification.

    Usage:
        cg = CausalGraph()
        cg.propagate("CREDIT_STRESS", "COMPOUNDER")
        path = cg.trace_path("AI_CAPEX", "FPT")
        conf = cg.cascade_confidence("CREDIT_STRESS", "LIQUIDITY_TRAP")
    """

    def __init__(self, edges: Optional[Dict[str, CausalEdge]] = None):
        self.edges = edges or build_edge_registry()
        self._index: Optional[Dict[str, List[CausalEdge]]] = None
        self._rebuild_index()

    # ── Index ──────────────────────────────────────────────────────────

    def _rebuild_index(self) -> None:
        """Build source→edges and target→edges lookup maps."""
        self._by_source: Dict[str, List[CausalEdge]] = {}
        self._by_target: Dict[str, List[CausalEdge]] = {}
        self._nodes: Set[str] = set()
        for e in self.edges.values():
            self._by_source.setdefault(e.source, []).append(e)
            self._by_target.setdefault(e.target, []).append(e)
            self._nodes.add(e.source)
            self._nodes.add(e.target)

    # ── Propagation ───────────────────────────────────────────────────

    def propagate(
        self,
        source_node: str,
        archetype: Optional[str] = None,
        max_hops: int = 5,
        min_confidence: float = 0.10,
    ) -> List[Dict]:
        """Propagate signal from source_node through the DAG.

        Returns list of {node, confidence, total_lag_min, total_lag_max,
                         attenuation, depth, edges}.
        Each step compounds confidence × (1 - attenuation) and sums lags.
        """
        visited: Set[str] = set()
        results: List[Dict] = []

        def _dfs(node: str, conf: float, lag_min: int, lag_max: int,
                 atten: float, depth: int, path: List[str]):
            if node in visited or depth > max_hops or conf < min_confidence:
                return
            visited.add(node)

            results.append({
                "node": node,
                "confidence": round(conf, 4),
                "lag_min": lag_min,
                "lag_max": lag_max,
                "attenuation": round(atten, 4),
                "depth": depth,
                "path": list(path),
            })

            for e in self._by_source.get(node, []):
                # Filter by archetype: only follow edges that match
                if e.archetype and archetype and e.archetype != archetype:
                    continue
                if e.archetype and not archetype:
                    continue
                next_node = e.target
                if next_node in visited:
                    continue
                next_conf = conf * e.confidence
                next_lag_min = lag_min + e.lag_min
                next_lag_max = lag_max + e.lag_max
                next_atten = 1.0 - (1.0 - atten) * (1.0 - e.attenuation)
                _dfs(next_node, next_conf, next_lag_min, next_lag_max,
                     next_atten, depth + 1, path + [e.id])

        _dfs(source_node, 1.0, 0, 0, 0.0, 0, [])
        return results

    # ── Tracing ────────────────────────────────────────────────────────

    def trace_path(
        self,
        source: str,
        target: str,
        archetype: Optional[str] = None,
    ) -> Optional[List[Dict]]:
        """Find the highest-confidence path from source to target.

        Returns ordered list of edge hops or None if no path exists.
        """
        best_path: Optional[List[Dict]] = None
        best_conf: float = 0.0

        def _dfs(node: str, target: str, conf: float,
                 lag_min: int, lag_max: int, atten: float,
                 path: List[Dict], visited: Set[str]):
            nonlocal best_path, best_conf
            if node == target:
                if conf > best_conf:
                    best_conf = conf
                    best_path = list(path)
                return
            if node in visited:
                return
            visited.add(node)
            for e in self._by_source.get(node, []):
                if e.archetype and archetype and e.archetype != archetype:
                    continue
                if e.archetype and not archetype:
                    continue
                next_conf = conf * e.confidence
                if next_conf < best_conf:
                    continue  # prune
                next_atten = 1.0 - (1.0 - atten) * (1.0 - e.attenuation)
                hop = {
                    "edge_id": e.id, "source": e.source, "target": e.target,
                    "confidence": round(e.confidence, 4),
                    "compounded_confidence": round(next_conf, 4),
                    "lag_min": lag_min + e.lag_min,
                    "lag_max": lag_max + e.lag_max,
                    "attenuation": round(next_atten, 4),
                    "half_life": e.half_life,
                    "factor_id": e.factor_id,
                    "counter_examples": e.counter_examples,
                    "description": e.description,
                }
                _dfs(e.target, target, next_conf,
                     lag_min + e.lag_min, lag_max + e.lag_max,
                     next_atten, path + [hop], visited)
            visited.remove(node)

        _dfs(source, target, 1.0, 0, 0, 0.0, [], set())
        return best_path

    # ── Cascade confidence ────────────────────────────────────────────

    def cascade_confidence(self, source: str, target: str) -> float:
        """Confidence that source causally affects target along best path."""
        path = self.trace_path(source, target)
        if not path:
            return 0.0
        return path[-1].get("compounded_confidence", 0.0)

    def effective_lag(self, source: str, target: str) -> Tuple[int, int]:
        """(min, max) total expected lag from source to target."""
        path = self.trace_path(source, target)
        if not path:
            return (0, 0)
        return (path[-1]["lag_min"], path[-1]["lag_max"])

    def total_attenuation(self, source: str, target: str) -> float:
        """Cumulative information loss [0,1] from source to target."""
        path = self.trace_path(source, target)
        if not path:
            return 1.0
        return path[-1]["attenuation"]

    # ── Counter-examples ──────────────────────────────────────────────

    def verify_counter_examples(self, edge_id: str) -> Dict:
        """Check if an edge has counter-examples and return analysis."""
        e = self.edges.get(edge_id)
        if not e:
            return {"edge_id": edge_id, "exists": False}
        nex = len(e.counter_examples)
        return {
            "edge_id": edge_id,
            "exists": True,
            "source": e.source,
            "target": e.target,
            "confidence": e.confidence,
            "n_counter_examples": nex,
            "counter_examples": e.counter_examples,
            "adjusted_confidence": max(0.05, e.confidence - nex * 0.05),
        }

    def add_counter_example(self, edge_id: str, event_date: str) -> None:
        """Log a counter-example for an edge, reducing its confidence."""
        e = self.edges.get(edge_id)
        if not e:
            return
        if event_date not in e.counter_examples:
            e.counter_examples.append(event_date)
            e.confidence = max(0.05, e.confidence - 0.05)
            e.updated_at = _now()

    # ── Graph stats ───────────────────────────────────────────────────

    def stats(self) -> Dict:
        return {
            "n_edges": len(self.edges),
            "n_nodes": len(self._nodes),
            "n_archetypes": len({e.archetype for e in self.edges.values() if e.archetype}),
            "universal_edges": sum(1 for e in self.edges.values() if not e.archetype),
            "avg_confidence": round(
                sum(e.confidence for e in self.edges.values()) / max(len(self.edges), 1), 4
            ),
            "total_counter_examples": sum(
                len(e.counter_examples) for e in self.edges.values()
            ),
        }

    # ── Persistence ───────────────────────────────────────────────────

    @staticmethod
    def _db_path() -> Path:
        _p = Path(__file__).resolve().parent.parent.parent
        for _par in [_p] + list(_p.parents):
            if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
                return _par / "backend" / "data" / "calibration.db"
        return _p / "backend" / "data" / "calibration.db"

    def init_schema(self) -> None:
        """Create causal_edges table in calibration.db."""
        import sqlite3
        conn = sqlite3.connect(str(self._db_path()))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS causal_edges (
                edge_id         TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                target          TEXT NOT NULL,
                edge_type       TEXT NOT NULL,
                lag_min         INTEGER DEFAULT 0,
                lag_max         INTEGER DEFAULT 90,
                confidence      REAL DEFAULT 0.5,
                half_life       REAL DEFAULT 30.0,
                attenuation     REAL DEFAULT 0.0,
                archetype       TEXT,
                factor_id       TEXT,
                counter_examples TEXT DEFAULT '[]',
                description     TEXT DEFAULT '',
                created_at      TEXT,
                updated_at      TEXT
            )
        """)
        conn.commit()
        conn.close()

    def persist(self) -> None:
        """Write all edges to SQLite."""
        import sqlite3
        self.init_schema()
        conn = sqlite3.connect(str(self._db_path()))
        for e in self.edges.values():
            conn.execute(
                """INSERT OR REPLACE INTO causal_edges
                   (edge_id, source, target, edge_type, lag_min, lag_max,
                    confidence, half_life, attenuation, archetype, factor_id,
                    counter_examples, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (e.id, e.source, e.target, e.edge_type, e.lag_min, e.lag_max,
                 e.confidence, e.half_life, e.attenuation, e.archetype, e.factor_id,
                 json.dumps(e.counter_examples), e.description, e.created_at, e.updated_at),
            )
        conn.commit()
        conn.close()

    def load_from_db(self) -> None:
        """Load edges from SQLite, merging with registry defaults."""
        import sqlite3
        self.init_schema()
        conn = sqlite3.connect(str(self._db_path()))
        rows = conn.execute("SELECT * FROM causal_edges").fetchall()
        conn.close()
        if not rows:
            return
        # Merge: DB values override defaults
        defaults = build_edge_registry()
        now = _now()
        for r in rows:
            eid = r["edge_id"]
            default = defaults.get(eid)
            self.edges[eid] = CausalEdge(
                id=eid,
                source=r["source"],
                target=r["target"],
                edge_type=r["edge_type"],
                lag_min=r["lag_min"],
                lag_max=r["lag_max"],
                confidence=r["confidence"],
                half_life=r["half_life"],
                attenuation=r["attenuation"],
                archetype=r["archetype"],
                factor_id=r["factor_id"],
                counter_examples=json.loads(r["counter_examples"] or "[]"),
                description=r["description"] or (default.description if default else ""),
                created_at=r["created_at"] or now,
                updated_at=r["updated_at"] or now,
            )
        self._rebuild_index()


# ═══════════════════════════════════════════════════════════════════════════
# Report helpers
# ═══════════════════════════════════════════════════════════════════════════

def print_causal_graph_report(
    graph: CausalGraph,
    source: Optional[str] = None,
    archetype: Optional[str] = None,
    lang_mode: str = "full",
):
    """Print propagation report."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    if source:
        results = graph.propagate(source, archetype)
        print(f"\n  {'='*90}")
        print(f"  {_('CAUSAL PROPAGATION')} — {source}"
              + (f" | {_('Archetype')}: {archetype}" if archetype else ""))
        print(f"  {'='*90}")
        print(f"  {_('Node'):<30} {_('Conf'):>6} {_('LagMin'):>7} {_('LagMax'):>7} {_('Atten'):>6} {_('Depth'):>6}")
        print(f"  {'─'*70}")
        for r in results:
            print(f"  {r['node']:<30} {r['confidence']:>6.3f} {r['lag_min']:>7}"
                  f" {r['lag_max']:>7} {r['attenuation']:>6.3f} {r['depth']:>6}")
        print(f"  {_('Total nodes reached')}: {len(results)}")
    else:
        stats = graph.stats()
        print(f"\n  {'='*60}")
        print(f"  {_('CAUSAL GRAPH')} — {_('Sprint 3')} ({stats['n_edges']} {_('edges')})")
        print(f"  {'='*60}")
        print(f"  {_('Nodes')}: {stats['n_nodes']} | {_('Archetypes')}: {stats['n_archetypes']}")
        print(f"  {_('Universal edges')}: {stats['universal_edges']} | {_('Avg confidence')}: {stats['avg_confidence']}")
        print(f"  {_('Counter-examples logged')}: {stats['total_counter_examples']}")


def trace_report(
    graph: CausalGraph,
    source: str,
    target: str,
    archetype: Optional[str] = None,
    lang_mode: str = "full",
):
    """Print trace_path report."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    path = graph.trace_path(source, target, archetype)
    if not path:
        print(f"\n  {_('No causal path from')} {source} → {target}")
        return

    print(f"\n  {'='*100}")
    print(f"  {_('CAUSAL PATH')}: {source} → {target}"
          + (f" [{archetype}]" if archetype else ""))
    print(f"  {'='*100}")
    print(f"  {_('Hop'):<2} {_('Edge ID'):<40} {'→':<4} {_('Conf'):>6} {_('Lag'):>8} {_('Atten'):>7} {_('HL'):>6}")
    print(f"  {'─'*80}")
    for i, hop in enumerate(path):
        lag = f"{hop['lag_min']}–{hop['lag_max']}d"
        print(f"  {i+1:<2} {hop['edge_id']:<40} {hop['target'][:12]:<12}"
              f" {hop['compounded_confidence']:>6.3f} {lag:>8}"
              f" {hop['attenuation']:>6.3f} {hop['half_life']:>5.0f}d")
    last = path[-1]
    print(f"\n  {_('Summary')}:")
    print(f"    {_('Compounded confidence')}: {last['compounded_confidence']:.3f}")
    print(f"    {_('Total lag')}: {last['lag_min']}–{last['lag_max']} {_('days')}")
    print(f"    {_('Information loss')}: {last['attenuation']:.1%} ({last['factor_id'] or 'N/A'})")
    if any(h["counter_examples"] for h in path):
        print(f"    ⚠ {_('Counter-examples exist on this path')}")


def edge_summary(graph: CausalGraph, lang_mode: str = "full"):
    """Print all edges grouped by archetype."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    print(f"\n  {'='*90}")
    print(f"  {_('CAUSAL EDGE REGISTRY')} — {len(graph.edges)} {_('edges')}")
    print(f"  {'='*90}")

    current_arch: Optional[str] = None
    for e in sorted(graph.edges.values(), key=lambda x: (x.archetype or "ZZZ", x.id)):
        arch = e.archetype or _("Universal")
        if arch != current_arch:
            current_arch = arch
            print(f"\n  [{arch}]")
        nex = len(e.counter_examples)
        ce_mark = f" ⚠{nex}{_('cx')}" if nex else ""
        print(f"    {e.source:<30} → {e.target:<24}"
              f"  {_('conf')}={e.confidence:.2f}  {_('lag')}={e.lag_min}–{e.lag_max}d"
              f"  {_('HL')}={e.half_life:.0f}d  {_('atten')}={e.attenuation:.2f}"
              f"{ce_mark}")
