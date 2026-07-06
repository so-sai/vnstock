"""
Capital Intelligence Engine v1.0
Position sizing based on Conviction Score, Risk Unit (R) framework, and Regime Matrix.
"""
import logging
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

logger = logging.getLogger(__name__)

REGIME_MATRIX = {
    "CRISIS":   {"max_gross": 0.10, "risk_per_trade": 0.0025},
    "RECOVERY": {"max_gross": 0.25, "risk_per_trade": 0.0040},
    "RANGING":  {"max_gross": 0.35, "risk_per_trade": 0.0050},
    "TRENDING": {"max_gross": 1.00, "risk_per_trade": 0.0100},
}

def calculate_conviction_score(signal_quality: float, breadth_alignment: float,
                                liquidity_rank: float, regime_confidence: float) -> float:
    score = (0.35 * signal_quality
           + 0.25 * breadth_alignment
           + 0.20 * liquidity_rank
           + 0.20 * regime_confidence)
    return round(score, 2)

def compute_position_size(total_nav: float, cash_balance: float, active_regime: str,
                          entry_price: float, stop_loss: float, symbol: str = "",
                          conviction_score: float = 0.0,
                          current_sector_exposure: float = 0.0,
                          current_portfolio_heat: float = 0.0,
                          liquidity_20d: float = 1e9) -> dict:
    if conviction_score < 0.4:
        return {"status": "REJECTED", "reason": "CONVICTION_TOO_LOW", "shares": 0, "value_vnd": 0}
    if current_sector_exposure >= 0.30:
        return {"status": "REJECTED", "reason": "SECTOR_CAP", "shares": 0, "value_vnd": 0}
    if current_portfolio_heat > 0.05:
        return {"status": "REJECTED", "reason": "HEAT_OVERFLOW", "shares": 0, "value_vnd": 0}

    regime_cfg = REGIME_MATRIX.get(active_regime, REGIME_MATRIX["CRISIS"])

    if conviction_score < 0.6:
        risk_mult = 0.5
    elif conviction_score < 0.8:
        risk_mult = 1.0
    else:
        risk_mult = 1.2

    target_risk_vnd = total_nav * regime_cfg["risk_per_trade"] * risk_mult
    stop_dist = entry_price - stop_loss

    if stop_dist <= 0:
        return {"status": "REJECTED", "reason": "INVALID_STOP", "shares": 0, "value_vnd": 0}

    raw_shares = int(target_risk_vnd // stop_dist)

    liquidity_cap = int((liquidity_20d * 0.10) // entry_price) if liquidity_20d else raw_shares
    final_shares = min(raw_shares, liquidity_cap)

    max_single_nav = total_nav * 0.10
    max_by_nav = int(max_single_nav // entry_price)
    final_shares = min(final_shares, max_by_nav)

    value_vnd = final_shares * entry_price

    if value_vnd > cash_balance:
        final_shares = int(cash_balance // entry_price)
        value_vnd = final_shares * entry_price

    final_shares = int(final_shares // 100 * 100)
    value_vnd = final_shares * entry_price

    if final_shares <= 0:
        return {"status": "REJECTED", "reason": "ZERO_SHARES", "shares": 0, "value_vnd": 0}

    return {
        "status": "APPROVED",
        "shares": final_shares,
        "value_vnd": value_vnd,
        "nav_exposure_pct": round((value_vnd / total_nav) * 100, 2) if total_nav else 0,
        "initial_risk_pct": round((target_risk_vnd / total_nav) * 100, 3) if total_nav else 0,
        "risk_unit_r": risk_mult,
    }
