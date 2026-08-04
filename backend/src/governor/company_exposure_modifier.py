"""company_exposure_modifier.py — Per-Company Macro Exposure Delta.

LAW-009 (Causal Delay & Interaction Principle):
  The same macro signal reaches different companies at different speeds.
  A company's position in the supply chain, inventory cycle, and contract
  structure creates company-specific deltas from the sector base lag.

Architecture:
  Company_Modifiers = {
    "inventory_cycle":  {"FAST": -5d, "NORMAL": 0d, "SLOW": +10d},
    "contract_type":    {"SPOT": -3d, "MIXED": 0d, "FIXED": +15d},
    "supply_chain":     {"VERTICAL": -5d, "INTEGRATED": 0d, "FRAGMENTED": +5d},
  }

  Effective_Lag(sector, company) = Base_Lag(sector) + Σ Delta(modifier)

  WHY This Matters:
  HPG (Dung Quất) has VERTICAL integration (BF-BOF → HRC → finished steel).
  When China steel price rises, HPG captures it FASTER than a EAF mill
  that needs to buy scrap + electricity at new prices. HPG's effective lag
  is SHORTER than the sector base.

  Conversely, a company with FIXED-PRICE forward contracts (e.g., utility
  with regulated tariff) has LONGER effective lag — the signal is locked
  out until contracts reprice.

Usage:
  modifier = CompanyExposureModifier()
  delta = modifier.compute_delta("HPG", "STEEL")
  # delta = -8 days (HPG is faster than sector base)

  effective_lag = base_lag + delta
  # STEEL base: 3-21d → HPG effective: 0-16d (faster transmission)
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


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

# ── Company Modifier Profiles ─────────────────────────────────────────
# WHY: These profiles encode the structural characteristics that determine
# how fast a macro signal reaches a specific company.
#
# Inventory Cycle:
#   FAST:   Just-in-time, low inventory days (<30d). Signal reaches fast.
#   NORMAL: Average inventory (30-90d).
#   SLOW:   Heavy inventory (>90d). Signal absorbed by stockpile.
#
# Contract Type:
#   SPOT:   Prices reprice immediately with market. Fastest transmission.
#   MIXED:  Mix of spot and forward contracts.
#   FIXED:  Long-term fixed-price contracts. Signal locked out.
#
# Supply Chain:
#   VERTICAL:    Raw material → finished product in-house. Fastest.
#   INTEGRATED:  Partial integration. Moderate speed.
#   FRAGMENTED:  Multiple suppliers. Slowest (bottlenecks).


@dataclass
class ModifierProfile:
    """Company-specific modifier deltas from sector base lag."""

    inventory_delta: int = 0  # days adjustment for inventory cycle
    contract_delta: int = 0  # days adjustment for contract type
    supply_chain_delta: int = 0  # days adjustment for supply chain
    size_modifier: float = 1.0  # multiplier on sector exposure (0.5-1.5)
    description: str = ""

    @property
    def total_lag_delta(self) -> int:
        """Total lag adjustment in days."""
        return self.inventory_delta + self.contract_delta + self.supply_chain_delta


# ── Stock-to-Profile Mapping ──────────────────────────────────────────
# WHY (HPG example):
#   HPG = VERTICAL integration (BF-BOF at Dung Quất, iron ore → HRC → finished).
#   HPG uses MIXED contracts (some spot, some forward with downstream customers).
#   HPG has NORMAL inventory (steel coils, 30-60 days).
#   Result: HPG is FASTER than sector base by ~8 days.

# ── Stock profiles ────────────────────────────────────────────────────
# Format: symbol → ModifierProfile

STOCK_PROFILES: Dict[str, ModifierProfile] = {
    # ── STEEL ────────────────────────────────────────────────────────
    # HPG: Vertical integration, MIXED contracts, NORMAL inventory
    "HPG": ModifierProfile(
        inventory_delta=0,
        contract_delta=-3,
        supply_chain_delta=-5,
        size_modifier=1.2,  # Large cap, more market influence
        description="HPG: BF-BOF vertical (Dung Quất). HRC spot + fwd mix.",
    ),
    # HSG: Similar to HPG but slightly smaller, more spot-reliant
    "HSG": ModifierProfile(
        inventory_delta=-2,
        contract_delta=-2,
        supply_chain_delta=-3,
        size_modifier=1.0,
        description="HSG: Galvanized steel. Spot-heavy pricing.",
    ),
    # NKG: EAF mill, FRAGMENTED supply chain (scrap from multiple sources)
    "NKG": ModifierProfile(
        inventory_delta=5,
        contract_delta=0,
        supply_chain_delta=5,
        size_modifier=0.8,
        description="NKG: EAF, scrap-dependent. Fragmented supply chain.",
    ),
    # DPM: Phosphate fertilizer, FIXED contracts with farmers
    "DPM": ModifierProfile(
        inventory_delta=10,
        contract_delta=15,
        supply_chain_delta=0,
        size_modifier=0.9,
        description="DPM: Fertilizer, fixed-price seasonal contracts.",
    ),
    # DCM: Urea fertilizer, MIXED contracts
    "DCM": ModifierProfile(
        inventory_delta=5,
        contract_delta=5,
        supply_chain_delta=0,
        size_modifier=0.9,
        description="DCM: Urea, mixed spot/forward.",
    ),
    # ── BANK ─────────────────────────────────────────────────────────
    # VCB: Large state-owned, slow NIM repricing (policy-driven)
    "VCB": ModifierProfile(
        inventory_delta=0,
        contract_delta=10,
        supply_chain_delta=0,
        size_modifier=1.3,
        description="VCB: State bank, NIM reprices slowly (policy rate lag).",
    ),
    # ACB: Private bank, faster NIM repricing (market-driven)
    "ACB": ModifierProfile(
        inventory_delta=0,
        contract_delta=-5,
        supply_chain_delta=0,
        size_modifier=1.0,
        description="ACB: Private bank, market-rate sensitive.",
    ),
    # MBB: Military bank, moderate repricing
    "MBB": ModifierProfile(
        inventory_delta=0,
        contract_delta=0,
        supply_chain_delta=0,
        size_modifier=1.0,
        description="MBB: Military bank, mixed repricing speed.",
    ),
    # HDB: Ho Chi Minh City Development Bank
    "HDB": ModifierProfile(
        inventory_delta=0,
        contract_delta=-3,
        supply_chain_delta=0,
        size_modifier=0.95,
        description="HDB: Retail-focused, moderate NIM sensitivity.",
    ),
    # ── REAL ESTATE ──────────────────────────────────────────────────
    # VHM: Large developer, LONG project cycle
    "VHM": ModifierProfile(
        inventory_delta=15,
        contract_delta=10,
        supply_chain_delta=5,
        size_modifier=1.1,
        description="VHM: Vinhomes, large-scale projects, long cycle.",
    ),
    # KDH: Mid-size developer
    "KDH": ModifierProfile(
        inventory_delta=10,
        contract_delta=5,
        supply_chain_delta=0,
        size_modifier=0.9,
        description="KDH: Mid-scale, moderate project cycle.",
    ),
    # ── OIL & GAS ────────────────────────────────────────────────────
    # GAS: PV Gas, FIXED long-term contracts
    "GAS": ModifierProfile(
        inventory_delta=0,
        contract_delta=20,
        supply_chain_delta=0,
        size_modifier=1.2,
        description="GAS: PV Gas, long-term fixed contracts with power plants.",
    ),
    # PLX: PetroVietnam, MIXED (refinery + retail)
    "PLX": ModifierProfile(
        inventory_delta=5,
        contract_delta=5,
        supply_chain_delta=5,
        size_modifier=1.1,
        description="PLX: Refinery + retail, mixed contract structure.",
    ),
    # ── RETAIL ───────────────────────────────────────────────────────
    # MWG: Mobile World, FAST inventory (electronics)
    "MWG": ModifierProfile(
        inventory_delta=-5,
        contract_delta=-3,
        supply_chain_delta=-2,
        size_modifier=1.1,
        description="MWG: Electronics retail, JIT inventory.",
    ),
    # PNJ: jewelry, SLOW inventory (gold price dependent)
    "PNJ": ModifierProfile(
        inventory_delta=10,
        contract_delta=5,
        supply_chain_delta=0,
        size_modifier=0.9,
        description="PNJ: Jewelry, gold inventory cycle.",
    ),
    # ── TECH ─────────────────────────────────────────────────────────
    # FPT: IT services, LONG backlog cycle
    "FPT": ModifierProfile(
        inventory_delta=0,
        contract_delta=10,
        supply_chain_delta=0,
        size_modifier=1.2,
        description="FPT: IT services, enterprise procurement backlog.",
    ),
    # ── TRANSPORT ────────────────────────────────────────────────────
    # GMD: Port operator, moderate cycle
    "GMD": ModifierProfile(
        inventory_delta=0,
        contract_delta=0,
        supply_chain_delta=-3,
        size_modifier=1.0,
        description="GMD: Port/logistics, moderate supply chain.",
    ),
    # HAH: Shipping, moderate cycle
    "HAH": ModifierProfile(
        inventory_delta=0,
        contract_delta=5,
        supply_chain_delta=0,
        size_modifier=0.9,
        description="HAH: Shipping, moderate contract cycle.",
    ),
    # ── CONSUMER ─────────────────────────────────────────────────────
    # VNM: Vinamilk, FIXED contracts with dairy farms
    "VNM": ModifierProfile(
        inventory_delta=5,
        contract_delta=10,
        supply_chain_delta=5,
        size_modifier=1.1,
        description="VNM: Dairy, fixed supply contracts with farmers.",
    ),
}

# ── Default profile for unknown stocks ────────────────────────────────
DEFAULT_PROFILE = ModifierProfile(
    description="Default: no company-specific adjustment.",
)


# ── Company Exposure Modifier ─────────────────────────────────────────


@dataclass
class ModifierResult:
    """Result of company-specific exposure modification."""

    symbol: str
    sector: str
    total_lag_delta: int  # Total lag adjustment (days)
    inventory_delta: int
    contract_delta: int
    supply_chain_delta: int
    size_modifier: float  # Exposure multiplier
    effective_lag_min: int  # Sector lag_min + delta (clamped ≥0)
    effective_lag_max: int  # Sector lag_max + delta (clamped ≥0)
    effective_half_life: float  # Sector half_life × size_modifier
    description: str


class CompanyExposureModifier:
    """Apply company-specific deltas to sector base lag parameters.

    Architecture:
      1. Look up company profile (from STOCK_PROFILES or default)
      2. Compute total lag delta from inventory + contract + supply chain
      3. Apply size modifier to exposure
      4. Return adjusted lag parameters

    Usage:
      modifier = CompanyExposureModifier()
      result = modifier.compute("HPG", "STEEL", lag_min=3, lag_max=21, half_life=15.0)
      # result.effective_lag_min = 0  (HPG is faster)
      # result.effective_lag_max = 16
      # result.effective_half_life = 18.0
    """

    def __init__(self, custom_profiles: Optional[Dict[str, ModifierProfile]] = None):
        self.profiles = custom_profiles or STOCK_PROFILES

    def get_profile(self, symbol: str) -> ModifierProfile:
        """Get modifier profile for a stock."""
        return self.profiles.get(symbol.upper(), DEFAULT_PROFILE)

    def compute(
        self,
        symbol: str,
        sector: str,
        lag_min: int,
        lag_max: int,
        half_life: float,
    ) -> ModifierResult:
        """Compute company-adjusted lag parameters.

        Args:
            symbol: Stock ticker
            sector: Sector code
            lag_min: Sector base lag_min
            lag_max: Sector base lag_max
            half_life: Sector base half_life

        Returns:
            ModifierResult with adjusted parameters
        """
        profile = self.get_profile(symbol)

        # ── Compute effective lag ────────────────────────────────────
        effective_lag_min = max(0, lag_min + profile.total_lag_delta)
        effective_lag_max = max(effective_lag_min + 1, lag_max + profile.total_lag_delta)

        # ── Apply size modifier to half_life ─────────────────────────
        # WHY: Large companies (size_modifier > 1.0) have MORE market
        # influence and respond MORE to macro signals (they ARE the market).
        # Small companies (size_modifier < 1.0) are price-takers and
        # respond LESS (signal is diluted by their limited market share).
        effective_half_life = half_life * profile.size_modifier

        return ModifierResult(
            symbol=symbol.upper(),
            sector=sector,
            total_lag_delta=profile.total_lag_delta,
            inventory_delta=profile.inventory_delta,
            contract_delta=profile.contract_delta,
            supply_chain_delta=profile.supply_chain_delta,
            size_modifier=profile.size_modifier,
            effective_lag_min=effective_lag_min,
            effective_lag_max=effective_lag_max,
            effective_half_life=round(effective_half_life, 1),
            description=profile.description,
        )
