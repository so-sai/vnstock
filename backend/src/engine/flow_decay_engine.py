"""
Flow Decay Engine (Phase 12C — Time Kernel Layer).
Overlay kernel — applies regime-modulated exponential decay to raw time series
BEFORE signal computation. Does NOT modify existing engines.

Architecture:
    engines (raw signals)
        |
        v
    flow_decay_engine (decay kernel + persistence/stability metrics)
        |
        v
    flow.py (synthesis layer with uncertainty)
        |
        v
    decision_tensor (consumption layer)
"""

import sys
from pathlib import Path


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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

import io
import logging
import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.database.db_core import get_connection
from src.engine.regime_engine import detect_regime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Half-life constants (trading days) per channel / metric
# ---------------------------------------------------------------------------
HALF_LIVES = {
    "liquidity": {
        "retail_chase": 1.5,
        "volume_profile": 5.0,
        "structural": 15.0,
    },
    "sector": {
        "momentum": 10.0,
        "flow_propagation": 15.0,
        "rotation_signal": 7.0,
    },
    "foreign": {
        "daily_flow": 3.0,
        "accumulation": 10.0,
        "regime_shift": 20.0,
    },
}

# ---------------------------------------------------------------------------
# Regime modulation matrix
# Each regime multiplies the effective half-life.
# τ_effective = τ_base * multiplier
#   multiplier < 1 : decay slows (signal persists longer)
#   multiplier > 1 : decay accelerates (signal fades faster)
# ---------------------------------------------------------------------------
REGIME_MODULATION = {
    "TRENDING": {
        "liquidity": 0.80,
        "sector": 0.70,
        "foreign": 0.85,
    },
    "RANGING": {
        "liquidity": 1.00,
        "sector": 1.00,
        "foreign": 1.00,
    },
    "CRISIS": {
        "liquidity": 1.30,
        "sector": 1.20,
        "foreign": 1.40,
    },
}

DEFAULT_REGIME = "RANGING"


@dataclass
class DecayedSeriesOutput:
    """Output of a decay kernel applied to a time series."""

    decayed_series: np.ndarray = field(repr=False)
    decayed_mean: float = 0.0
    decayed_volatility: float = 0.0
    persistence: float = 0.5
    surge: float = 0.0
    instability: float = 0.0
    effective_half_life: float = 1.0
    n_observations: int = 0


class FlowDecayKernel:
    """
    Pure mathematical decay kernel.
    Applies regime-modulated exponential weighting to any 1-D series.

    w_i = exp(-i * ln(2) / τ_effective)
    score = Σ(value_i * w_i) / Σ(w_i)
    """

    def __init__(self, half_life: float, channel: str = "liquidity", regime: str | None = None):
        self.base_tau = half_life / np.log(2)
        self.channel = channel
        self.regime = regime or DEFAULT_REGIME
        self._update_multiplier()

    def _update_multiplier(self):
        regime_map = REGIME_MODULATION.get(self.regime, REGIME_MODULATION[DEFAULT_REGIME])
        self.multiplier = regime_map.get(self.channel, 1.0)
        self.effective_half_life = self.base_tau * np.log(2) * self.multiplier

    def set_regime(self, regime: str):
        self.regime = regime
        self._update_multiplier()

    def weights(self, n: int) -> np.ndarray:
        if n <= 1:
            return np.ones(n) / n
        lam = np.log(2) / (self.base_tau * self.multiplier)
        w = np.exp(-lam * np.arange(n))
        return w / w.sum()

    def apply(self, series: np.ndarray) -> DecayedSeriesOutput:
        series = np.asarray(series, dtype=float)
        # Strip NaN prefix (leading NaN from rolling operations)
        finite_mask = np.isfinite(series)
        if not finite_mask.any():
            return DecayedSeriesOutput(
                decayed_series=np.array([]),
                n_observations=0,
            )
        series = series[finite_mask]
        n = len(series)
        if n == 0:
            return DecayedSeriesOutput(
                decayed_series=np.array([]),
                n_observations=0,
            )
        w = self.weights(n)
        decayed = series * w[::-1]
        decayed_mean = float(np.sum(decayed))

        # decayed volatility = weighted std (handle NaN edge case)
        diff = series - decayed_mean
        finite_diff = diff[np.isfinite(diff)]
        if len(finite_diff) > 1:
            variance = float(np.sum(w[: len(finite_diff)] * finite_diff**2))
            decayed_vol = float(np.sqrt(max(0, variance)))
        else:
            decayed_vol = 0.0

        # persistence = correlation(decayed, raw)
        if n > 1 and np.std(series) > 1e-10 and np.std(decayed) > 1e-10:
            persistence = float(np.corrcoef(series, decayed)[0, 1])
        else:
            persistence = 0.5

        # surge = how much recent (last 3) exceeds expected
        recent_n = min(3, n)
        recent_mean = float(np.nanmean(series[-recent_n:])) if n > 0 else 0.0
        surge = (recent_mean / decayed_mean - 1) if abs(decayed_mean) > 1e-10 else 0.0

        # instability = CV of decayed series
        mean_abs = abs(np.mean(decayed))
        instability = float(np.std(decayed) / mean_abs) if mean_abs > 1e-10 else 0.0

        return DecayedSeriesOutput(
            decayed_series=decayed,
            decayed_mean=round(decayed_mean, 4),
            decayed_volatility=round(decayed_vol, 4),
            persistence=round(max(-1.0, min(1.0, persistence)), 4),
            surge=round(surge, 4),
            instability=round(min(instability, 10.0), 4),
            effective_half_life=round(self.effective_half_life, 2),
            n_observations=n,
        )


# ---------------------------------------------------------------------------
# Channel-specific builders (overlay layer — raw data → decay → signal)
# These functions read from the same DB tables as the engines but apply the
# decay kernel BEFORE computing derived signals.
# ---------------------------------------------------------------------------


def _resolve_current_regime() -> str:
    try:
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            verdict = detect_regime()
        finally:
            sys.stdout = old_stdout
        return verdict.get("status", DEFAULT_REGIME)
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as e:
        logger.warning(f"Regime detection failed for decay modulation: {e}")
        return DEFAULT_REGIME


def build_liquidity_decay(symbol: str, lookback: int = 60, regime: str | None = None) -> dict:
    """
    Build a decay-augmented volume profile for a single symbol.
    Returns both classic engine fields AND decay awareness metrics.
    """
    with get_connection() as conn:
        df = pd.read_sql(
            f"SELECT date, close, volume FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT {lookback}",
            conn,
            params=(symbol,),
        )
    if df.empty or len(df) < 10:
        return {"symbol": symbol, "status": "INSUFFICIENT_DATA"}

    df = df.sort_values("date").reset_index(drop=True)
    volume = df["volume"].values
    close = df["close"].values

    effective_regime = regime or _resolve_current_regime()

    # --- Apply decay kernels to volume ---
    retail_kernel = FlowDecayKernel(HALF_LIVES["liquidity"]["retail_chase"], "liquidity", effective_regime)
    inst_kernel = FlowDecayKernel(HALF_LIVES["liquidity"]["volume_profile"], "liquidity", effective_regime)

    retail_decay = retail_kernel.apply(volume)
    inst_decay = inst_kernel.apply(volume)

    # --- Compute decayed signals ---
    vol_ma20_decayed = inst_decay.decayed_mean
    latest_vol = float(volume[-1])
    vol_ratio_decayed = latest_vol / vol_ma20_decayed if vol_ma20_decayed > 0 else 0.0

    # Decayed volume acceleration
    half = len(volume) // 2
    vol_ma20_prev_decayed = inst_kernel.apply(volume[:half]).decayed_mean if half >= 5 else vol_ma20_decayed
    vol_accel_decayed = (vol_ma20_decayed / vol_ma20_prev_decayed - 1) if vol_ma20_prev_decayed > 0 else 0.0

    # Decayed retail chase score
    retail_chase_decayed = min(1.0, vol_ratio_decayed * abs(vol_accel_decayed) * 3)

    # Decayed turnover shock
    value_series = close * volume * 1000 / 1e9
    value_kernel = FlowDecayKernel(HALF_LIVES["liquidity"]["volume_profile"], "liquidity", effective_regime)
    value_decay = value_kernel.apply(value_series)
    value_decayed = value_decay.decayed_mean
    latest_value = float(value_series[-1])
    turnover_shock_decayed = (latest_value / value_decayed) if value_decayed > 0 else 0.0

    # Classic wave strength from decayed perspective
    wave_strength_decayed = "NORMAL"
    if vol_ratio_decayed > 2.0 and vol_accel_decayed > 0.3 and turnover_shock_decayed > 1.5:
        wave_strength_decayed = "SURGE"
    elif vol_ratio_decayed > 1.5 and vol_accel_decayed > 0.15:
        wave_strength_decayed = "STRONG"
    elif vol_ratio_decayed < 0.5 and vol_accel_decayed < -0.1:
        wave_strength_decayed = "DROUGHT"

    return {
        "symbol": symbol,
        "vol_ratio_decayed": round(vol_ratio_decayed, 2),
        "vol_accel_decayed": round(vol_accel_decayed, 3),
        "turnover_shock_decayed": round(turnover_shock_decayed, 2),
        "retail_chase_decayed": round(retail_chase_decayed, 3),
        "wave_strength_decayed": wave_strength_decayed,
        "retail_persistence": retail_decay.persistence,
        "retail_surge": retail_decay.surge,
        "instability": inst_decay.instability,
        "volume_signal_quality": _classify_signal_quality(retail_decay.persistence, inst_decay.instability),
    }


def build_sector_decay(sector: str, lookback: int = 60, regime: str | None = None) -> dict:
    """
    Build a decay-augmented sector RS profile.
    Momentum computed from decay-weighted daily returns instead of SMA crossover.
    """
    with get_connection() as conn:
        mapping = _load_sector_mapping()
        symbols = [s for s, sec in mapping.items() if sec == sector]
        if not symbols:
            return {"sector": sector, "status": "NO_SYMBOLS"}
        placeholders = ",".join(["?"] * len(symbols))
        df = pd.read_sql(
            f"SELECT symbol, date, close FROM daily_ohlcv "
            f"WHERE symbol IN ({placeholders}) AND date >= date('now', '-{lookback + 10} days') "
            f"ORDER BY date",
            conn,
            params=symbols,
        )
    if df.empty:
        return {"sector": sector, "status": "NO_DATA"}

    df.loc[:, "return"] = df.groupby("symbol")["close"].pct_change(fill_method=None)
    daily = df.groupby("date")["return"].mean().reset_index().sort_values("date")
    if daily.empty or len(daily) < 10:
        return {"sector": sector, "status": "INSUFFICIENT_DATA"}

    returns = daily["return"].fillna(0).values
    effective_regime = regime or _resolve_current_regime()

    # Apply decay kernel to daily returns
    mom_kernel = FlowDecayKernel(HALF_LIVES["sector"]["momentum"], "sector", effective_regime)
    mom_decay = mom_kernel.apply(returns)

    # Decayed momentum = decay-weighted mean of recent returns
    momentum_decayed = mom_decay.decayed_mean * 100

    # Decayed cumulative return
    cum_return = (1 + returns).cumprod()
    cum_kernel = FlowDecayKernel(HALF_LIVES["sector"]["rotation_signal"], "sector", effective_regime)
    cum_decay = cum_kernel.apply(cum_return)
    cum_return_decayed = cum_decay.decayed_mean

    # Momentum slope from decayed perspective
    mom_slope_decayed = 0.0
    if len(returns) >= 10:
        decayed_mom_vals = mom_kernel.apply(returns[-10:]).decayed_series
        if len(decayed_mom_vals) >= 5:
            mom_slope_decayed = (decayed_mom_vals[-1] - decayed_mom_vals[0]) / len(decayed_mom_vals)

    # Rotation phase from decayed momentum
    phase_decayed = _classify_rotation_phase_decayed(momentum_decayed, mom_slope_decayed)

    return {
        "sector": sector,
        "momentum_decayed": round(momentum_decayed, 2),
        "cum_return_decayed": round((cum_return_decayed - 1) * 100, 2),
        "momentum_slope_decayed": round(mom_slope_decayed * 100, 3),
        "phase_decayed": phase_decayed,
        "momentum_persistence": mom_decay.persistence,
        "momentum_surge": mom_decay.surge,
        "instability": mom_decay.instability,
        "signal_quality": _classify_signal_quality(mom_decay.persistence, mom_decay.instability),
    }


def build_foreign_decay(symbol: str, half_life_days: float = 10.0, regime: str | None = None) -> dict:
    """
    Build decay-augmented foreign accumulation.
    Replaces the flat 10-day sum with a decay-weighted accumulation.
    """
    with get_connection() as conn:
        n_days = int(max(30, half_life_days * 3))
        df = pd.read_sql(
            f"SELECT date, net_value FROM market_foreign_history WHERE symbol = ? ORDER BY date DESC LIMIT {n_days}",
            conn,
            params=(symbol,),
        )
    if df.empty:
        return {"symbol": symbol, "net_value_decayed": 0.0, "status": "NO_DATA"}

    df = df.sort_values("date").reset_index(drop=True)
    net_values = df["net_value"].fillna(0).values

    effective_regime = regime or _resolve_current_regime()
    channel = "foreign"
    kernel = FlowDecayKernel(half_life_days, channel, effective_regime)
    decay = kernel.apply(net_values)

    return {
        "symbol": symbol,
        "net_value_decayed": round(decay.decayed_mean, 2),
        "net_value_volatility": round(decay.decayed_volatility, 2),
        "persistence": decay.persistence,
        "surge": decay.surge,
        "instability": decay.instability,
        "effective_half_life": decay.effective_half_life,
        "n_observations": decay.n_observations,
    }


# ---------------------------------------------------------------------------
# Aggregation wrappers (mirror engine interfaces but with decay)
# ---------------------------------------------------------------------------


def get_decayed_liquidity_health(regime: str | None = None) -> dict:
    """Market-level liquidity health from decay-weighted volume."""
    effective_regime = regime or _resolve_current_regime()
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT date, SUM(volume) as total_vol, "
            "AVG(close * volume * 1000 / 1e9) as avg_value_bn "
            "FROM daily_ohlcv WHERE date >= date('now', '-30 days') "
            "GROUP BY date ORDER BY date",
            conn,
        )
    if df.empty or len(df) < 5:
        return {"status": "INSUFFICIENT_DATA"}

    vol_series = df["total_vol"].values
    value_series = df["avg_value_bn"].values

    kernel = FlowDecayKernel(HALF_LIVES["liquidity"]["volume_profile"], "liquidity", effective_regime)
    vol_decay = kernel.apply(vol_series)
    value_decay = kernel.apply(value_series)

    vol_trend_decayed = (vol_decay.decayed_mean / np.mean(vol_series) - 1) * 100
    value_trend_decayed = (value_decay.decayed_mean / np.mean(value_series) - 1) * 100

    phase = (
        "EXPANDING"
        if vol_trend_decayed > 5 and value_trend_decayed > 5
        else "CONTRACTING"
        if vol_trend_decayed < -5 and value_trend_decayed < -5
        else "NEUTRAL"
    )

    return {
        "liquidity_phase_decayed": phase,
        "volume_trend_decayed": round(vol_trend_decayed, 2),
        "value_trend_decayed": round(value_trend_decayed, 2),
        "vol_persistence": vol_decay.persistence,
        "vol_instability": vol_decay.instability,
    }


def get_decayed_rotation_beta(regime: str | None = None) -> dict:
    """Sector rotation regime from decay-weighted momentum across sectors."""
    results = []
    from src.engine.sector_rotation_graph import SECTOR_ORDER

    for sec in SECTOR_ORDER:
        rs = build_sector_decay(sec, 60, regime)
        if rs.get("status") in ("NO_SYMBOLS", "NO_DATA", "INSUFFICIENT_DATA"):
            continue
        results.append(rs)

    if not results:
        return {"rotation_regime_decayed": "UNKNOWN"}

    results.sort(key=lambda x: x["momentum_decayed"], reverse=True)
    top3 = [r["sector"] for r in results[:3]]
    bottom3 = [r["sector"] for r in results[-3:]]
    leaders_avg = np.mean([r["momentum_decayed"] for r in results[:3]]) if len(results) >= 3 else 0
    laggards_avg = np.mean([r["momentum_decayed"] for r in results[-3:]]) if len(results) >= 3 else 0
    spread = leaders_avg - laggards_avg

    phase_counts = {}
    for r in results:
        p = r.get("phase_decayed", "NEUTRAL")
        phase_counts[p] = phase_counts.get(p, 0) + 1

    score = (
        phase_counts.get("EARLY_ACCEL", 0)
        + phase_counts.get("MID_CYCLE", 0) * 0.7
        + phase_counts.get("SUSTAINED", 0) * 0.4
        - phase_counts.get("WEAKENING", 0) * 0.5
    ) / max(1, len(results))

    regime_mapped = "HEALTHY_ROTATION"
    if score < -0.2 and spread < 0:
        regime_mapped = "NARROW_LEADERSHIP"
    elif score < 0 and spread > 3:
        regime_mapped = "DIVERGENT"
    elif score > 0.15:
        regime_mapped = "BROAD_ROTATION"

    return {
        "rotation_regime_decayed": regime_mapped,
        "rotation_score_decayed": round(score, 3),
        "spread_decayed": round(spread, 2),
        "dominant_phase_decayed": max(phase_counts, key=phase_counts.get) if phase_counts else "NEUTRAL",
        "leading_sectors_decayed": top3,
        "lagging_sectors_decayed": bottom3,
        "num_sectors_active": len(results),
    }


def get_decayed_foreign_summary(top_n: int = 10, regime: str | None = None) -> dict:
    """
    Aggregate foreign flow summary using decay-weighted accumulation
    instead of flat 10-day sum.
    """
    top_symbols = ["VCB", "HPG", "BSR", "GMD", "STB", "MBB", "TCB", "VNM", "FPT", "VIC"]
    accumulations = {}
    total_decayed = 0.0

    for sym in top_symbols:
        try:
            result = build_foreign_decay(sym, HALF_LIVES["foreign"]["accumulation"], regime)
            net = result.get("net_value_decayed", 0.0)
            accumulations[sym] = round(net, 2)
            total_decayed += net
        except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
            logger.warning(f"Foreign decay failed for {sym}: {e}")
            accumulations[sym] = 0.0

    top_accumulated = sorted(accumulations.items(), key=lambda x: x[1], reverse=True)

    return {
        "total_net_decayed_bn_vnd": round(total_decayed, 2),
        "market_pressure_decayed": "ACCUMULATING" if total_decayed > 0 else "DISTRIBUTING",
        "top_accumulated_decayed": [{"symbol": s, "net_decayed_bn_vnd": v} for s, v in top_accumulated[:5]],
        "top_distributed_decayed": [{"symbol": s, "net_decayed_bn_vnd": v} for s, v in reversed(top_accumulated[-5:])],
    }


# ---------------------------------------------------------------------------
# Banner synthesis with uncertainty
# ---------------------------------------------------------------------------


def synthesize_decayed_banner(regime: str | None = None) -> dict:
    """
    Generate a probabilistic flow banner using decay-weighted signals.
    Includes persistence tags and confidence bands instead of deterministic narrative.
    """
    effective_regime = regime or _resolve_current_regime()

    # Gather all decay-augmented data
    liquidity = get_decayed_liquidity_health(effective_regime)
    rotation = get_decayed_rotation_beta(effective_regime)
    foreign = get_decayed_foreign_summary(regime=effective_regime)

    liq_phase = liquidity.get("liquidity_phase_decayed", "NEUTRAL")
    rot_regime = rotation.get("rotation_regime_decayed", "UNKNOWN")
    liq_persistence = liquidity.get("vol_persistence", 0.5)
    liq_instability = liquidity.get("vol_instability", 0.5)

    # Determine the most reliable signal channel
    signal_strength = _compute_signal_strength(liquidity, rotation, foreign)
    conflict_flag = _detect_channel_conflict(liquidity, rotation, foreign)

    # Build banner components
    liq_label = _phase_label_vn(liq_phase)
    rot_label = _rotation_label_vn(rot_regime)
    persistence_tag = _persistence_label(liq_persistence)
    pressure_label = _pressure_label_vn(foreign.get("market_pressure_decayed", "NEUTRAL"))

    parts = [f"THANH KHOẢN: {liq_label} ({persistence_tag})"]
    parts.append(f"XOAY VÒNG: {rot_label}")
    parts.append(f"K.MGOẠI: {pressure_label}")

    if conflict_flag["has_conflict"]:
        banner = f"⚠️ TÍN HIỆU XUNG ĐỘT — {conflict_flag['description']} | {' • '.join(parts)}"
    elif liq_persistence >= 0.7:
        banner = f"📊 DÒNG TIỀN ỔN ĐỊNH — {' • '.join(parts)}"
    elif liq_instability > 1.5:
        banner = f"⚡ DÒNG TIỀN BIẾN ĐỘNG — {' • '.join(parts)}"
    else:
        banner = f"🧭 THEO DÕI DÒNG TIỀN — {' • '.join(parts)}"

    return {
        "banner": banner,
        "liquidity_phase_decayed": liq_phase,
        "rotation_regime_decayed": rot_regime,
        "persistence_score": liq_persistence,
        "instability_score": liq_instability,
        "signal_strength": signal_strength,
        "conflict_flag": conflict_flag["has_conflict"],
        "confidence_band": _confidence_band(liq_persistence, liq_instability, signal_strength),
    }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _load_sector_mapping() -> dict:
    with get_connection() as conn:
        df = pd.read_sql("SELECT symbol, icb_name3 FROM symbol_industry", conn)
    return df.set_index("symbol")["icb_name3"].to_dict()


def _classify_rotation_phase_decayed(momentum: float, slope: float) -> str:
    if momentum > 0.03 and slope > 0.005:
        return "EARLY_ACCEL"
    if momentum > 0.05 and slope > 0:
        return "MID_CYCLE"
    if momentum > 0.03 and slope < -0.003:
        return "LATE_CYCLE"
    if momentum > 0.01:
        return "SUSTAINED"
    if momentum > -0.02:
        return "NEUTRAL"
    return "WEAKENING"


def _classify_signal_quality(persistence: float, instability: float) -> str:
    """Quality of a decay-weighted signal for downstream consumption."""
    if persistence >= 0.7 and instability < 0.8:
        return "HIGH"
    if persistence >= 0.4 and instability < 1.5:
        return "MEDIUM"
    return "LOW"


def _persistence_label(persistence: float) -> str:
    if persistence >= 0.7:
        return "BỀN VỮNG"
    if persistence >= 0.4:
        return "TRUNG BÌNH"
    return "YẾU"


def _phase_label_vn(phase: str) -> str:
    labels = {"EXPANDING": "MỞ RỘNG", "CONTRACTING": "CO HẸP", "NEUTRAL": "CÂN BẰNG"}
    return labels.get(phase, "KHÔNG XĐ")


def _rotation_label_vn(regime: str) -> str:
    labels = {
        "HEALTHY_ROTATION": "XOAY VÒNG KHỎE",
        "BROAD_ROTATION": "XOAY VÒNG RỘNG",
        "DIVERGENT": "PHÂN HÓA",
        "NARROW_LEADERSHIP": "DẪN DẮT HẸP",
    }
    return labels.get(regime, "THEO DÕI")


def _pressure_label_vn(pressure: str) -> str:
    return "HÚT RÒNG" if pressure == "ACCUMULATING" else "XẢ RÒNG"


def _compute_signal_strength(liquidity: dict, rotation: dict, foreign: dict) -> float:
    """Overall signal coherence across all 3 channels (0.0–1.0)."""
    liq_p = liquidity.get("vol_persistence", 0.5)
    rot_p = 0.5
    rot_score = rotation.get("rotation_score_decayed", 0)
    if abs(rot_score) > 0.1:
        rot_p = min(1.0, abs(rot_score) * 2)

    abs(liquidity.get("volume_trend_decayed", 0)) / 100
    f_p = min(1.0, abs(foreign.get("total_net_decayed_bn_vnd", 0)) / 500)

    return round((liq_p * 0.4 + rot_p * 0.35 + f_p * 0.25), 3)


def _detect_channel_conflict(liquidity: dict, rotation: dict, foreign: dict) -> dict:
    """
    Detect conflicts between the 3 flow channels.
    Returns conflict flag + description.
    """
    liq_trend = liquidity.get("volume_trend_decayed", 0)
    rot_score = rotation.get("rotation_score_decayed", 0)
    f_pressure = foreign.get("market_pressure_decayed", "NEUTRAL")

    conflicts = []

    # Liquidity expanding but sector weakening
    if liq_trend > 5 and rot_score < -0.1:
        conflicts.append("Thanh khoản mở rộng nhưng xoay vòng ngành yếu")
    # Liquidity contracting but foreign accumulating
    if liq_trend < -5 and f_pressure == "ACCUMULATING":
        conflicts.append("Thanh khoản co hẹp nhưng khối ngoại hút ròng")
    # Sector strong but foreign distributing
    if rot_score > 0.15 and f_pressure == "DISTRIBUTING":
        conflicts.append("Xoay vòng mạnh nhưng khối ngoại xả ròng")

    return {
        "has_conflict": len(conflicts) > 0,
        "description": " • ".join(conflicts) if conflicts else "",
        "conflict_count": len(conflicts),
    }


def _confidence_band(persistence: float, instability: float, signal_strength: float) -> str:
    """Map decay metrics to a confidence band label."""
    raw = persistence * 0.4 + (1 - min(instability / 2, 1)) * 0.3 + signal_strength * 0.3
    if raw >= 0.7:
        return "CAO"
    if raw >= 0.45:
        return "TRUNG BÌNH"
    return "THẤP"


# ---------------------------------------------------------------------------
# Entry point for flow.py middleware integration
# ---------------------------------------------------------------------------


def get_decayed_flow_summary(regime: str | None = None) -> dict:
    """
    Full decay-augmented flow summary.
    This is the main middleware entry point for flow.py.
    Returns everything needed for both the banner AND the 3 channel cards.
    """
    effective_regime = regime or _resolve_current_regime()

    liquidity = get_decayed_liquidity_health(effective_regime)
    rotation = get_decayed_rotation_beta(effective_regime)
    foreign = get_decayed_foreign_summary(regime=effective_regime)
    banner_data = synthesize_decayed_banner(effective_regime)

    return {
        "regime": effective_regime,
        "liquidity": liquidity,
        "rotation": rotation,
        "foreign": foreign,
        "banner": banner_data,
    }


if __name__ == "__main__":
    import json

    # Redirect stdout for Windows cp1252 compatibility
    if sys.platform == "win32":
        if isinstance(sys.stdout, io.TextIOWrapper):
            if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
                try:
                    sys.stdout.reconfigure(encoding="utf-8")
                except OSError, AttributeError, ValueError:
                    logger.debug("stdout.reconfigure(utf-8) không khả dụng — giữ nguyên encoding")
        elif hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    summary = get_decayed_flow_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Test individual decay kernel
    kernel = FlowDecayKernel(5.0, "liquidity", "TRENDING")
    test = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = kernel.apply(test)
    print(
        json.dumps(
            {
                "kernel_test": {
                    "mean": result.decayed_mean,
                    "persistence": result.persistence,
                    "surge": result.surge,
                    "instability": result.instability,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
    )
