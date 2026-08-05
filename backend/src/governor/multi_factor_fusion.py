"""
Multi-Factor Fusion Engine — Decision Hub for 5-Model Portfolio System

Combines 5 models into a single Composite Score for each symbol:
  S_Composite = w_M2 * S_M2 + w_M1 * S_M1 + w_Alpha * S_Alpha + w_M3 * S_M3

Decision Hierarchy:
  1. VN20 Quant Gate  (Hard Pass/Fail)
  2. M1 Macro Governance (Sector allocation cap)
  3. M2 Fundamental  (Valuation quality)
  4. M3 Behavioral   (Institutional flow)
  5. BacktestAlpha   (Momentum timing)
  6. Fusion          (Weighted composite)
  7. Position Sizing (Max 15% per symbol, Max 30% per sector)
  8. Execution       (Slippage + fees + trailing stop)

LAW-009: All macro scores are lag-adjusted before fusion.
LAW-010: All scores are regime-normalized before fusion.
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _hydrate_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
BACKEND_DIR = PROJECT_ROOT / "backend"
SRC_DIR = BACKEND_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ═══════════════════════════════════════════════════════════
# Default Fusion Weights (Grid Search optimized, 2021-2026, Sharpe=0.59)
# ═══════════════════════════════════════════════════════════
DEFAULT_FUSION_WEIGHTS = {
    "M2_FUNDAMENTAL": 0.20,
    "M1_MACRO": 0.00,
    "ALPHA_MOMENTUM": 0.10,
    "M3_BEHAVIORAL": 0.70,
}

# ═══════════════════════════════════════════════════════════
# Position Sizing Constraints
# ═══════════════════════════════════════════════════════════
MAX_POSITION_WEIGHT = 0.15  # 15% max per symbol
MAX_SECTOR_WEIGHT = 0.30  # 30% max per sector
MIN_CASH_RESERVE = 0.05  # 5% cash always reserved
TRANSACTION_COST = 0.0045  # 0.45% per round-trip (fee + tax + slippage)
TRAILING_STOP_PCT = 0.05  # 5% trailing stop loss (Grid Search optimal)
TRAILING_TAKE_PCT = 0.20  # 20% trailing take profit (Grid Search optimal)


@dataclass
class FactorScores:
    """Individual model scores for one symbol on one date."""

    symbol: str
    date: str
    sector: str
    M1_macro: float = 0.5
    M2_fundamental: float = 0.5
    M3_behavioral: float = 0.5
    alpha_momentum: float = 0.5
    vn20_pass: bool = False
    composite_score: float = 0.5


@dataclass
class Position:
    """Tracked position with risk management."""

    symbol: str
    shares: float
    entry_price: float
    entry_date: str
    peak_price: float
    current_weight: float = 0.0
    unrealized_pnl_pct: float = 0.0
    status: str = "OPEN"  # OPEN, STOPPED, TAKEN_PARTIAL, CLOSED


@dataclass
class FusionResult:
    """Fusion output for one symbol."""

    symbol: str
    sector: str
    composite_score: float
    action: str  # BUY, SELL, HOLD
    target_weight: float
    current_weight: float = 0.0
    reason: str = ""


class MultiFactorFusion:
    """
    Multi-Factor Decision Fusion Engine.

    Receives scores from all 5 models and produces:
    1. Composite score per symbol
    2. Buy/Sell/Hold decision
    3. Target position weight (with sector cap)
    """

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        max_position: float = MAX_POSITION_WEIGHT,
        max_sector: float = MAX_SECTOR_WEIGHT,
        min_cash: float = MIN_CASH_RESERVE,
        tx_cost: float = TRANSACTION_COST,
        trailing_stop: float = TRAILING_STOP_PCT,
        trailing_take: float = TRAILING_TAKE_PCT,
    ):
        self.weights = weights or DEFAULT_FUSION_WEIGHTS
        self.max_position = max_position
        self.max_sector = max_sector
        self.min_cash = min_cash
        self.tx_cost = tx_cost
        self.trailing_stop = trailing_stop
        self.trailing_take = trailing_take

    def fuse(
        self,
        scores: List[FactorScores],
        current_positions: Optional[Dict[str, Position]] = None,
    ) -> List[FusionResult]:
        """Fuse all factor scores into composite decisions.

        Args:
            scores: List of FactorScores for all candidate symbols
            current_positions: Currently held positions (for trailing stop check)

        Returns:
            List of FusionResult sorted by composite_score descending
        """
        if current_positions is None:
            current_positions = {}

        results = []

        for fs in scores:
            # Step 1: VN20 Gate (hard filter)
            if not fs.vn20_pass:
                results.append(
                    FusionResult(
                        symbol=fs.symbol,
                        sector=fs.sector,
                        composite_score=0.0,
                        action="EXCLUDED",
                        target_weight=0.0,
                        reason="VN20 Quant Gate: FAIL",
                    )
                )
                continue

            # Step 2: Compute composite score
            composite = (
                self.weights["M2_FUNDAMENTAL"] * fs.M2_fundamental
                + self.weights["M1_MACRO"] * fs.M1_macro
                + self.weights["ALPHA_MOMENTUM"] * fs.alpha_momentum
                + self.weights["M3_BEHAVIORAL"] * fs.M3_behavioral
            )
            fs.composite_score = composite

            # Step 3: Determine action
            current_pos = current_positions.get(fs.symbol)
            action, target_weight, reason = self._decide(fs, composite, current_pos)

            results.append(
                FusionResult(
                    symbol=fs.symbol,
                    sector=fs.sector,
                    composite_score=round(composite, 4),
                    action=action,
                    target_weight=target_weight,
                    current_weight=current_pos.current_weight if current_pos else 0.0,
                    reason=reason,
                )
            )

        # Step 4: Apply sector cap
        results = self._apply_sector_cap(results)

        # Sort by composite score descending
        results.sort(key=lambda r: r.composite_score, reverse=True)

        return results

    def _decide(
        self,
        fs: FactorScores,
        composite: float,
        current_pos: Optional[Position],
    ) -> tuple[str, float, str]:
        """Decide BUY/SELL/HOLD for one symbol."""
        if current_pos is None:
            # No position: BUY if composite > threshold
            if composite > 0.55:
                return "BUY", self.max_position, f"Composite {composite:.2f} > 0.55 threshold"
            return "HOLD", 0.0, f"Composite {composite:.2f} below BUY threshold"

        # Has position: check trailing stop/take
        pnl = current_pos.unrealized_pnl_pct

        if pnl <= -self.trailing_stop:
            return "SELL", 0.0, f"Trailing STOP at {pnl:.1%} (limit -{self.trailing_stop:.0%})"

        if pnl >= self.trailing_take:
            return "SELL", 0.0, f"Trailing TAKE at {pnl:.1%} (limit +{self.trailing_take:.0%})"

        # Hold if composite still decent
        if composite > 0.35:
            return "HOLD", self.max_position, f"Composite {composite:.2f} still healthy"

        # Composite degraded — reduce or exit
        return "SELL", 0.0, f"Composite {composite:.2f} degraded below HOLD threshold"

    def _apply_sector_cap(self, results: List[FusionResult]) -> List[FusionResult]:
        """Enforce max sector weight cap."""
        sector_weights: Dict[str, float] = {}
        for r in results:
            if r.action in ("BUY", "HOLD") and r.target_weight > 0:
                sector_weights[r.sector] = sector_weights.get(r.sector, 0.0) + r.target_weight

        for r in results:
            if r.action in ("BUY", "HOLD") and r.target_weight > 0:
                sector_total = sector_weights.get(r.sector, 0.0)
                if sector_total > self.max_sector:
                    excess = sector_total - self.max_sector
                    reduction = excess * (r.target_weight / sector_total)
                    r.target_weight = max(0.0, r.target_weight - reduction)
                    if r.target_weight < 0.01:
                        r.action = "HOLD"
                        r.target_weight = 0.0
                        r.reason = f"Sector {r.sector} cap reached ({sector_total:.1%})"

        return results

    def compute_execution_cost(self, trade_value: float) -> float:
        """Compute transaction cost for a trade."""
        return trade_value * self.tx_cost

    def get_risk_metrics(self, results: List[FusionResult]) -> Dict:
        """Compute portfolio-level risk metrics."""
        total_weight = sum(r.target_weight for r in results if r.action in ("BUY", "HOLD"))
        n_positions = sum(1 for r in results if r.action == "BUY")
        avg_composite = sum(r.composite_score for r in results if r.action == "BUY") / max(n_positions, 1)

        return {
            "total_allocated": round(total_weight * 100, 1),
            "cash_reserve": round((1.0 - total_weight) * 100, 1),
            "n_positions": n_positions,
            "avg_composite": round(avg_composite, 4),
            "sector_concentration_risk": self._check_sector_concentration(results),
        }

    def _check_sector_concentration(self, results: List[FusionResult]) -> Dict[str, float]:
        """Check sector concentration for risk monitoring."""
        sector_weights: Dict[str, float] = {}
        for r in results:
            if r.action in ("BUY", "HOLD") and r.target_weight > 0:
                sector_weights[r.sector] = sector_weights.get(r.sector, 0.0) + r.target_weight
        return {s: round(w * 100, 1) for s, w in sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)}
