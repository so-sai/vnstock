"""sector_exposure_matrix.py — Cross-Sector Macro Exposure Matrix.

LAW-005 (Causal Fingerprint):
  Each sector has a unique macro exposure signature.

LAW-008 (Regime Equivalence):
  Macro influence is regime-conditional; the same Fed move has different
  impact on BANK vs STEEL depending on the current cycle phase.

Architecture:
  Macro State Vector M ∈ R^4:
    M = [US_Liquidity, China_Economy, Commodity_Cycle, Domestic_Liquidity]

  Sector Exposure Weight Vector W_i ∈ R^4:
    W_BANK   = [0.40, 0.05, 0.00, 0.55]
    W_STEEL  = [0.10, 0.50, 0.30, 0.10]

  Sector Macro Score (Dot Product):
    Macro_Score_i = M · W_i^T ∈ [0, 1]

  This allows the SAME macro environment to produce BULL signals for
  STEEL (China boom) while producing BEAR signals for BANK (Fed tightening).

Mathematical Formulation:
  Let M = [m_US, m_CN, m_CM, m_DN] be the macro state vector.
  Let W_i = [w_US, w_CN, w_CM, w_DN] be sector i's exposure weights.

  Then: Macro_Score_i = Σ(m_k * w_ik) for k in {US, CN, CM, DN}

  Properties:
    - If all m_k ∈ [0, 1] and Σw_ik = 1, then Macro_Score_i ∈ [0, 1]
    - Dot product is commutative: M · W = W · M
    - Linear scaling: if M doubles, Macro_Score doubles (preserves direction)

Usage:
  matrix = SectorExposureMatrix()
  score = matrix.compute_sector_macro_score("HPG", macro_vector)
  # score = 0.72 (bullish for steel)
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()

# ── Macro Node Labels ─────────────────────────────────────────────────
# 4 orthogonal macro dimensions that drive sector returns.
MACRO_NODES = ["US_Liquidity", "China_Economy", "Commodity_Cycle", "Domestic_Liquidity"]

# ── Sector Exposure Weight Matrix ─────────────────────────────────────
# Each sector's vulnerability to each macro node.
# Weights sum to 1.0 per sector (probability simplex).
#
# WHY (First Principles — Multi-Polar Fingerprint):
#   "Không phải mọi cổ phiếu đều quan tâm Trung Quốc như nhau."
#   Macro Score ≠ Global Macro Score
#   Macro_i = W_i × GlobalFactors  (dot product per sector)
#
#   Same macro shock → DIFFERENT impact across sectors.
#   Fed tightening hurts BANK (-0.35) but barely affects STEEL (-0.10).
#   China boom helps STEEL (+0.50) but barely affects BANK (+0.05).
#
# Calibration Sources:
#   US_Liquidity:    Fed Funds Rate, DXY, US 10Y Treasury, VIX
#   China_Economy:   China Credit Impulse, Caixin PMI, USD/CNY
#   Commodity_Cycle: Brent Crude, Iron Ore, Copper, SCFI
#   Domestic_Liquidity: SBV OMO, Interbank ON, VND M2 growth
#
# Reference: VN30 sector correlation analysis 2020-2025
# User-refined matrix (2026-08-04): Each sector has unique Exposure Matrix

SECTOR_EXPOSURE_WEIGHTS: Dict[str, Dict[str, float]] = {
    # ── Financials ────────────────────────────────────────────────────
    # WHY: Ngân hàng sống còn nhờ SBV/OMO/Interbank (0.60).
    # Fed ảnh hưởng gián qua DXY → VND pressure → capital flow.
    # China几乎没有直接影响 (0.05).
    "BANK": {
        "US_Liquidity": 0.35,  # Fed rate → capital flow, DXY → VND pressure
        "China_Economy": 0.05,  # Minimal direct link
        "Commodity_Cycle": 0.00,  # No commodity exposure
        "Domestic_Liquidity": 0.60,  # SBV OMO, Interbank, credit growth
    },
    # WHY: Chứng khoán nhạy với risk appetite (VIX) + margin lending.
    "SEC": {
        "US_Liquidity": 0.25,  # Risk appetite, VIX
        "China_Economy": 0.10,  # Regional sentiment
        "Commodity_Cycle": 0.05,  # Indirect via market breadth
        "Domestic_Liquidity": 0.60,  # Margin lending, trading volume
    },
    # ── Real Estate ───────────────────────────────────────────────────
    # WHY: BĐS nhạy nhất với lãi suất vay (0.60).
    # China spillover qua tâm lý BĐS khu công nghiệp.
    "RE": {
        "US_Liquidity": 0.15,  # FDI flows, bond yields
        "China_Economy": 0.15,  # China property sentiment spillover
        "Commodity_Cycle": 0.10,  # Construction material costs
        "Domestic_Liquidity": 0.60,  # Mortgage rates, SBV credit policy
    },
    # ── Industrials / Materials ───────────────────────────────────────
    # WHY: Thép phụ thuộc TRỰC TIẾP vào China Credit Impulse + HRC prices.
    # Khi TQ bơm tiền → nhu cầu thép tăng → HPG, HSG hưởng lợi.
    "STEEL": {
        "US_Liquidity": 0.10,  # Minimal
        "China_Economy": 0.50,  # HRC prices, China credit impulse
        "Commodity_Cycle": 0.30,  # Iron ore, coking coal
        "Domestic_Liquidity": 0.10,  # Domestic demand
    },
    # WHY: Xây dựng phụ thuộc đầu tư công + vật liệu.
    "CONST": {
        "US_Liquidity": 0.10,  # Minimal
        "China_Economy": 0.20,  # Material costs
        "Commodity_Cycle": 0.25,  # Cement, steel input
        "Domestic_Liquidity": 0.45,  # Public investment, bond issuance
    },
    # ── Energy / Utilities ────────────────────────────────────────────
    # WHY: Dầu khí sống bằng Brent crude (0.50) + refining margins.
    "OIL": {
        "US_Liquidity": 0.15,  # USD pricing, Fed policy
        "China_Economy": 0.20,  # Demand from China manufacturing
        "Commodity_Cycle": 0.50,  # Brent crude, refining margins
        "Domestic_Liquidity": 0.15,  # Domestic fuel pricing
    },
    # WHY: Tiện ích phụ thuộc giá nhiên liệu đầu vào + tariff政策.
    "UTILITY": {
        "US_Liquidity": 0.10,  # Minimal
        "China_Economy": 0.15,  # Equipment imports
        "Commodity_Cycle": 0.25,  # Coal, gas prices
        "Domestic_Liquidity": 0.50,  # Tariff policy, retail electricity
    },
    # ── Consumer / Retail ─────────────────────────────────────────────
    # WHY: Bán lẻ sống bằng tiêu dùng nội địa (0.55).
    "CONSUMER": {
        "US_Liquidity": 0.10,  # Import costs
        "China_Economy": 0.15,  # Input sourcing
        "Commodity_Cycle": 0.20,  # Packaging, raw materials
        "Domestic_Liquidity": 0.55,  # Consumer credit, disposable income
    },
    # WHY: Thực phẩm phụ thuộc agricultural commodities (0.35).
    # Phân bón (DCM, DPM) đặc biệt nhạy với Oil.
    "FOOD": {
        "US_Liquidity": 0.05,  # Minimal
        "China_Economy": 0.15,  # Feedstock, fertilizer
        "Commodity_Cycle": 0.35,  # Agricultural commodities
        "Domestic_Liquidity": 0.45,  # Domestic consumption
    },
    # ── Technology ────────────────────────────────────────────────────
    # WHY: Tech valuations correlation với NASDAQ (0.30).
    "TECH": {
        "US_Liquidity": 0.30,  # Tech valuations, NASDAQ correlation
        "China_Economy": 0.20,  # Supply chain, hardware sourcing
        "Commodity_Cycle": 0.10,  # Semiconductor raw materials
        "Domestic_Liquidity": 0.40,  # IT spending, digital transformation
    },
    # ── Transport / Logistics ─────────────────────────────────────────
    # WHY: Logistics sống bằng SCFI/BDI (0.25) + China trade volume (0.30).
    # Khi TQ xuất khẩu mạnh → cảng biển HAH, GMD hưởng lợi.
    "TRANS": {
        "US_Liquidity": 0.15,  # Global trade volumes
        "China_Economy": 0.30,  # China import/export volumes
        "Commodity_Cycle": 0.25,  # SCFI, BDI, fuel costs
        "Domestic_Liquidity": 0.30,  # Domestic logistics demand
    },
}

# ── Fallback for unknown sectors ──────────────────────────────────────
DEFAULT_EXPOSURE = {node: 0.25 for node in MACRO_NODES}

# ── Sector Name Mapping (Vietnamese → English) ────────────────────────
VIETNAMESE_SECTOR_MAP = {
    # Full ICB names from symbol_industry.icb_name2 (screener_cache.db)
    "Ngân hàng": "BANK",
    "Dịch vụ tài chính": "SEC",
    "Bảo hiểm": "SEC",
    "Bất động sản": "RE",
    "Tài nguyên Cơ bản": "STEEL",
    "Hóa chất": "STEEL",
    "Xây dựng và Vật liệu": "CONST",
    "Xây dựng": "CONST",
    "Dầu khí": "OIL",
    "Điện, nước & xăng dầu khí đốt": "UTILITY",
    "Hàng cá nhân & Gia dụng": "CONSUMER",
    "Bán lẻ": "CONSUMER",
    "Thực phẩm và đồ uống": "FOOD",
    "Công nghệ Thông tin": "TECH",
    "Công nghệ": "TECH",
    "Hàng & Dịch vụ Công nghiệp": "TRANS",
    "Logistics": "TRANS",
    "Vận tải": "TRANS",
    "Cảng biển": "TRANS",
    "Du lịch và Giải trí": "TRANS",
    "Viễn thông": "TECH",
    "Truyền thông": "TECH",
    "Y tế": "CONSUMER",
    "Ô tô và phụ tùng": "TRANS",
}


@dataclass
class SectorMacroResult:
    """Result of sector-specific macro score computation."""

    sector: str
    macro_score: float  # Dot product M · W_i ∈ [0, 1]
    exposure_weights: Dict[str, float]  # W_i vector
    macro_vector: Dict[str, float]  # M vector used
    components: Dict[str, float]  # m_k * w_ik for each node


class SectorExposureMatrix:
    """Compute sector-specific macro scores via dot product.

    Architecture:
      1. Take macro state vector M from RegionalInfluenceEngine
      2. Look up sector exposure weights W_i
      3. Compute dot product: Macro_Score_i = M · W_i^T
      4. Return score ∈ [0, 1] for each sector

    This replaces the old "one macro score for all sectors" approach
    with a cross-sectional, sector-aware macro fingerprint.
    """

    def __init__(self, custom_weights: Optional[Dict[str, Dict[str, float]]] = None):
        self.weights = custom_weights or SECTOR_EXPOSURE_WEIGHTS

    def get_exposure_weights(self, sector: str) -> Dict[str, float]:
        """Get exposure weight vector for a sector.

        Args:
            sector: Sector code (e.g., 'BANK', 'STEEL') or Vietnamese name

        Returns:
            Dict mapping macro nodes to weights (sums to 1.0)
        """
        # Try English sector code first
        if sector in self.weights:
            return self.weights[sector]

        # Try Vietnamese name mapping
        en_sector = VIETNAMESE_SECTOR_MAP.get(sector)
        if en_sector and en_sector in self.weights:
            return self.weights[en_sector]

        return DEFAULT_EXPOSURE

    def compute_sector_macro_score(self, sector: str, macro_vector: Dict[str, float]) -> SectorMacroResult:
        """Compute macro score for a specific sector via dot product.

        Formula:
          Macro_Score_i = Σ(m_k * w_ik) for k in {US, CN, CM, DN}

        Args:
            sector: Sector code or Vietnamese name
            macro_vector: Dict mapping macro nodes to scores ∈ [0, 1]

        Returns:
            SectorMacroResult with score and decomposition
        """
        weights = self.get_exposure_weights(sector)

        # Compute dot product
        score = 0.0
        components = {}
        for node in MACRO_NODES:
            m_k = macro_vector.get(node, 0.5)  # default to neutral
            w_k = weights.get(node, 0.25)
            contribution = m_k * w_k
            score += contribution
            components[node] = round(contribution, 6)

        # Clamp to [0, 1]
        score = max(0.0, min(1.0, score))

        return SectorMacroResult(
            sector=sector,
            macro_score=round(score, 6),
            exposure_weights=weights,
            macro_vector=macro_vector,
            components=components,
        )

    def compute_all_sector_scores(self, macro_vector: Dict[str, float]) -> Dict[str, SectorMacroResult]:
        """Compute macro scores for all defined sectors.

        Args:
            macro_vector: Dict mapping macro nodes to scores ∈ [0, 1]

        Returns:
            Dict mapping sector codes to SectorMacroResult
        """
        results = {}
        for sector in self.weights:
            results[sector] = self.compute_sector_macro_score(sector, macro_vector)
        return results

    def get_sector_ranking(self, macro_vector: Dict[str, float], ascending: bool = False) -> List[Tuple[str, float]]:
        """Rank sectors by macro score.

        Args:
            macro_vector: Dict mapping macro nodes to scores ∈ [0, 1]
            ascending: If True, lowest score first (bearish sectors)

        Returns:
            List of (sector, score) tuples, sorted by score
        """
        results = self.compute_all_sector_scores(macro_vector)
        ranked = [(s, r.macro_score) for s, r in results.items()]
        ranked.sort(key=lambda x: x[1], reverse=not ascending)
        return ranked

    def compute_mos_adjustment(
        self,
        sector: str,
        macro_vector: Dict[str, float],
        base_mos: float = 1.0,
    ) -> float:
        """Compute Margin of Safety adjustment based on macro score.

        Formula:
          MoS_final = MoS_raw * (1.0 + w_sino * (Macro_Score - 0.5))

        Where Macro_Score > 0.5 → bullish → MoS increases
              Macro_Score < 0.5 → bearish → MoS decreases

        Args:
            sector: Sector code
            macro_vector: Dict mapping macro nodes to scores
            base_mos: Raw Margin of Safety before adjustment

        Returns:
            Adjusted MoS ∈ [0, 2] (clamped)
        """
        result = self.compute_sector_macro_score(sector, macro_vector)

        # Macro score deviation from neutral (0.5)
        deviation = result.macro_score - 0.5

        # Adjustment factor: ±50% of base MoS
        adjustment = 1.0 + deviation

        # Clamp to prevent extreme adjustments
        adjustment = max(0.5, min(1.5, adjustment))

        return round(base_mos * adjustment, 4)
