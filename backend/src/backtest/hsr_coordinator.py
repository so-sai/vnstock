# -*- coding: utf-8 -*-
"""
hsr_coordinator.py — Historical State Reconstruction Coordinator.

NOT a backtest. NOT a trading simulator.

Purpose:
    Reconstruct the 11-field DailySnapshot that SRV needs, for any historical
    trading day, by calling each engine with target_date isolation.

Contract:
    - Every engine call passes target_date to prevent look-ahead bias.
    - Missing data sources (capital_displacement, risk_governor) are marked
      with quality="MISSING_HISTORICAL_SOURCE" — never fabricated.
    - Module-level mutable state (DPL _dbe_history, TTL _ttl_history) is
      externally managed by the caller.
"""

import sys, json, logging, pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger("sentinel.hsr")

_OHLCV_CACHE: Optional[pd.DataFrame] = None
"""Module-level cache: pre-loaded OHLCV for all symbols across entire replay window."""


def preload_ohlcv(start_date: str, end_date: str) -> pd.DataFrame:
    """Load ALL OHLCV data for ALL symbols in one query. Called once before replay loop."""
    global _OHLCV_CACHE
    from src.database.db_core import get_connection
    with get_connection() as conn:
        _OHLCV_CACHE = pd.read_sql(
            "SELECT symbol, date, close FROM daily_ohlcv "
            "WHERE date >= ? AND date <= ? AND volume > 0 "
            "ORDER BY symbol, date",
            conn, params=(start_date, end_date)
        )
    _OHLCV_CACHE['date'] = pd.to_datetime(_OHLCV_CACHE['date'], format='mixed')
    logger.info(f"  [HSR] Pre-loaded {len(_OHLCV_CACHE):,} rows for "
                f"{_OHLCV_CACHE['symbol'].nunique()} symbols "
                f"({start_date} → {end_date})")
    return _OHLCV_CACHE


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

from src.engine.decision_engine import merge_decisions
from src.engine.breadth_engine import run_breadth_analysis
from src.engine.market_structure import analyse_market_structure
from src.engine.capital_flow_forecasting_engine import generate_flow_forecast
from src.engine.rsi_regime_engine import generate_market_rsi_report

from core.presentation.trade_state_policy import compute_trade_state, compile_action_policy
from core.presentation.state_stability_index import compute_ssi
from core.presentation.decision_closure_layer import compute_dcl
from core.presentation.directional_bias_extractor import compute_directional_bias
from core.presentation.direction_persistence_layer import (
    compute_direction_persistence, clear_history as clear_dpl_history,
)
from core.presentation.transition_trigger_layer import (
    compute_transition_trigger, clear_ttl_history,
)
from core.presentation.asset_preference_mapping import compute_asset_preference


def _flow_status_to_bias_score(flow_status: str) -> float:
    mapping = {
        "MỞ_RỘNG": 1.0, "MỞ_RỘNG_TÍCH_CỰC": 1.0,
        "DUY_TRÌ": 0.65, "ỔN_ĐỊNH": 0.65,
        "TRUNG_TÍNH": 0.5, "PHÂN_HÓA": 0.35,
        "THU_HẸP": 0.1, "UNKNOWN": 0.4,
    }
    return mapping.get(flow_status, 0.4)


def _derive_flow_status(forecast: dict) -> str:
    summary = forecast.get("projection_summary", {})
    acc = summary.get("acceleration_count", 0)
    dec = summary.get("deceleration_count", 0)
    cont = summary.get("continuation_count", 0)
    if acc >= 2 and acc > dec:
        return "MỞ_RỘNG"
    if dec >= 2 and dec > acc:
        return "THU_HẸP"
    if cont >= 2:
        return "DUY_TRÌ"
    return "PHÂN_HÓA"


def _derive_drift_label(structure: dict) -> str:
    bdi = abs(structure.get("bdi_pct", 0))
    lcr = structure.get("lcr_pct", 30.0)
    signal = structure.get("bdi_signal", "CAN_BANG")
    if bdi > 10 and lcr > 35 and signal != "CAN_BANG":
        return f"STRUCTURAL — index +{bdi:.0f}% lech breadth, LCR {lcr:.0f}%"
    if bdi > 5 or lcr > 40:
        return f"TRANSIENT — {signal}, LCR {lcr:.0f}%"
    return "NONE — thong so dong bo"


def _breadth_from_lattice(lattice: pd.DataFrame, target_date: str) -> tuple:
    """Compute breadth health from pre-computed Feature Lattice."""
    day = lattice.loc[lattice["date"] == pd.Timestamp(target_date)]
    total = len(day)
    if total == 0:
        return 0.0, {}
    above = (day["close"] > day["ma_20"]).sum()
    health = above / total
    return health, {"health_score_ma20": health * 100, "total_active": total}


def build_historical_snapshot(
    target_date: str,
    dpl_history: Optional[list] = None,
    ttl_state: Optional[dict] = None,
    preloaded_df: Optional[pd.DataFrame] = None,
    lattice: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Reconstruct a single daily snapshot for SRV at target_date.

    Parameters
    ----------
    target_date : str
        YYYY-MM-DD trading date to reconstruct.
    dpl_history : list or None
        External DPL state (list of previous DBE reports). Pass None to start fresh.
    ttl_state : dict or None
        External TTL state (prev_regime, prev_trend_quality). Pass None to start fresh.
    lattice : pd.DataFrame or None
        Pre-computed Feature Lattice (Layer 0). When provided, breadth and RSI
        read from cache instead of re-computing from OHLCV.

    Returns
    -------
    dict with 11 SRV fields + metadata, or dict with "status": "SKIP" if no data.
    """
    logger.info(f"  [HSR] Reconstructing {target_date} ...")

    # ── Step 1: Decision Engine (regime + consensus) ────────────────────
    try:
        board = merge_decisions(target_date=target_date)
        regime_status = board.get("market_status", "UNKNOWN")
    except Exception as e:
        logger.warning(f"  [HSR] decision_engine failed for {target_date}: {e}")
        return {"date": target_date, "status": "ENGINE_FAILURE", "error": str(e)}

    # ── Step 2: Market Structure (lcr_pct, bdi_signal) ──────────────────
    try:
        structure = analyse_market_structure(target_date=target_date, verbose=False)
        if structure is None:
            structure = {"lcr_pct": 30.0, "bdi_signal": "CAN_BANG", "bdi_pct": 0.0}
    except Exception as e:
        logger.warning(f"  [HSR] market_structure failed: {e}")
        structure = {"lcr_pct": 30.0, "bdi_signal": "CAN_BANG", "bdi_pct": 0.0}

    # ── Step 3: Breadth (health_score_ma20) ─────────────────────────────
    if lattice is not None:
        breadth_health, pulse = _breadth_from_lattice(lattice, target_date)
    else:
        try:
            pulse = run_breadth_analysis(target_date=target_date)
            if pulse is None:
                pulse = {"health_score_ma20": 0.0, "total_active": 0}
            breadth_health = pulse.get("health_score_ma20", 0.0) / 100.0
        except Exception as e:
            logger.warning(f"  [HSR] breadth_engine failed: {e}")
            breadth_health = 0.0
            pulse = {}

    # ── Step 4: Flow Forecast (flow_bias_score) ─────────────────────────
    try:
        forecast = generate_flow_forecast(target_date=target_date, lookback_days=60)
        if forecast.get("status") in ("NO_DATA", "NO_CURRENT_VECTOR"):
            flow_label = "UNKNOWN"
            flow_bias_score = 0.4
        else:
            flow_label = _derive_flow_status(forecast)
            flow_bias_score = _flow_status_to_bias_score(flow_label)
    except Exception as e:
        logger.warning(f"  [HSR] flow_forecast failed: {e}")
        flow_label = "UNKNOWN"
        flow_bias_score = 0.4
        forecast = {}

    # ── Step 5: RSI Regime Report ───────────────────────────────────────
    rsi_report = {}
    if preloaded_df is not None:
        try:
            rsi_report = generate_market_rsi_report(
                target_date=target_date,
                preloaded_df=preloaded_df,
            )
        except Exception as e:
            logger.warning(f"  [HSR] rsi_engine failed: {e}")

    # ── Step 6: Pure Presentation Layer ─────────────────────────────────
    lcr_pct = structure.get("lcr_pct", 30.0)
    bdi_signal = structure.get("bdi_signal", "CAN_BANG")
    drift_label = _derive_drift_label(structure)

    trade_state = compute_trade_state(
        regime_status=regime_status,
        regime_score=board.get("regime_score", 0.0),
        breadth_health=breadth_health * 100,
        lcr_pct=lcr_pct,
        flow_status=flow_label,
        risk_governor="NORMAL",
        bdi_signal=bdi_signal,
    )

    ssi = compute_ssi(
        regime_status=regime_status,
        regime_score=board.get("regime_score", 0.0),
        breadth_health=breadth_health * 100,
        lcr_pct=lcr_pct,
        bdi_signal=bdi_signal,
        drift_label=drift_label,
        flow_status=flow_label,
        flow_velocity=forecast.get("flow_momentum", {}).get("total_flow_velocity", 0.0),
        rotation_velocity=forecast.get("flow_momentum", {}).get("rotation_velocity", 0.0),
    )

    dcl = compute_dcl(
        sentinel_green=False,
        sentinel_label="UNKNOWN",
        flow_bias_score=flow_bias_score,
        flow_label=flow_label,
        breadth_health=breadth_health * 100,
        lcr_pct=lcr_pct,
        bdi_signal=bdi_signal,
        ssi_score=ssi.score,
        trade_state_level=trade_state.level,
        trade_state_score=trade_state.score,
    )

    dbe = compute_directional_bias(
        regime_status=regime_status,
        trade_state_level=trade_state.level,
        breadth_health=breadth_health,
        lcr_pct=lcr_pct,
        bdi_signal=bdi_signal,
        flow_bias_score=flow_bias_score,
        flow_label=flow_label,
        ssi_score=ssi.score,
        dcl_verdict=dcl.verdict,
        dcl_score=dcl.score,
        compensations_triggered=dcl.compensations_triggered,
    )

    dpl = compute_direction_persistence(dbe)

    ttl = compute_transition_trigger(
        dpl=dpl, dbe=dbe, regime_status=regime_status,
    )

    # ── Step 7: Assemble SRV snapshot ──────────────────────────────────
    snapshot = {
        "date": target_date,
        "regime_status": regime_status,
        "trade_state_level": trade_state.level,
        "breadth_health": round(breadth_health, 4),
        "lcr_pct": lcr_pct,
        "bdi_signal": bdi_signal,
        "flow_bias_score": round(flow_bias_score, 4),
        "ssi_score": round(ssi.score, 4),
        "dcl_verdict": dcl.verdict,
        "dcl_score": round(dcl.score, 4),
        "compensations_triggered": dcl.compensations_triggered,

        # HSR metadata
        "hsr_quality": {
            "capital_displacement": "MISSING_HISTORICAL_SOURCE",
            "risk_governor": "MISSING_HISTORICAL_SOURCE",
            "sentinel_verdict": "MISSING_HISTORICAL_SOURCE",
            "breadth_source": "breadth_engine" if pulse else "regime_fallback",
        },
    }

    return snapshot


def reset_memory():
    clear_dpl_history()
    clear_ttl_history()
