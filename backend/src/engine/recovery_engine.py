import sys
from pathlib import Path

import pandas as pd


# Sentinel v2.1 (Anchor Fix)
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
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config
from src.database.db_core import get_connection


def evaluate_recovery_status(regime_data, velocity_5d, target_date=None):
    """
    Recovery Engine v2.5 (Hardened - The Titanium Muzzle).
    Includes Stability, Cooldown, and Depth filters to prevent sideway spam.

    Returns: dict with keys:
      - is_recovery: bool
      - is_abort: bool
      - status: str (RECOVERY_ACTIVE / STANDBY / PILOT_ABORT / NO_DATA)
      - block_reason: str
      - ma10_reclaim: bool
      - log: list[str] — diagnostic messages (for orchestrator to display)
      - details: dict
    """
    breadth_pct = regime_data['details']['breadth_pct']
    atr_ratio = regime_data['details']['atr_ratio']
    breadth_std = regime_data['details'].get('breadth_std_10d', 0.0)

    # Configuration
    cfg = src.config.RECOVERY_CONFIG

    # 1. Fetch VNINDEX Metrics (Price & Volume)
    with get_connection() as conn:
        if target_date:
            df_idx = pd.read_sql(f"SELECT date, close, volume FROM daily_ohlcv WHERE symbol='VNINDEX' AND date <= '{target_date}' ORDER BY date DESC LIMIT 60", conn)
        else:
            df_idx = pd.read_sql("SELECT symbol, date, close, volume FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date DESC LIMIT 60", conn)

    if df_idx.empty:
        return {"is_recovery": False, "is_abort": False, "status": "NO_DATA"}

    current_idx = df_idx.iloc[0]['close']
    current_vol = df_idx.iloc[0]['volume']
    avg_vol_20 = df_idx['volume'].head(20).mean()
    ma10 = df_idx['close'].head(10).mean()

    # Drawdown Check
    peak_idx = df_idx['close'].max()
    current_dd = (current_idx / peak_idx - 1) * 100

    reclaim_ma10 = current_idx > ma10

    # 2. Fetch History for Filters (Cooldown & Thrust)
    with get_connection() as conn:
        if target_date:
            df_hist = pd.read_sql(f"SELECT date, status, breadth_pct FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT 20", conn)
        else:
            df_hist = pd.read_sql("SELECT date, status, breadth_pct FROM regime_history ORDER BY date DESC LIMIT 20", conn)

    prev_breadth = df_hist.iloc[0]['breadth_pct'] if not df_hist.empty else 0.0
    prev_vol = df_idx.iloc[1]['volume'] if len(df_idx) >= 2 else 0.0

    # 3. Core Strength Signals
    vol_thrust_today = current_vol > (avg_vol_20 * 1.2)
    breadth_thrust_today = (breadth_pct > prev_breadth + 10.0) or (breadth_pct > 30.0)
    thrust_2d = (vol_thrust_today and (prev_vol > avg_vol_20 * 1.1)) or (breadth_thrust_today and breadth_pct >= prev_breadth)

    is_at_depth = (prev_breadth < 15.0)
    velocity_target = 8.0 if (is_at_depth and (vol_thrust_today or breadth_thrust_today)) else 15.0
    is_velocity_ok = (velocity_5d >= velocity_target)
    is_atr_cooling = (atr_ratio < 1.4)

    raw_recovery = is_velocity_ok and is_atr_cooling and reclaim_ma10 and (thrust_2d or breadth_thrust_today)

    # 4. HARDENING FILTERS (Phase 7.5)
    block_reason = "NONE"

    # Filter 1: Stability (Muzzle the Sideway Noise)
    if raw_recovery and breadth_std >= cfg['breadth_std_threshold']:
        raw_recovery = False
        block_reason = "BLOCKED_BY_STABILITY"

    # Filter 2: Cooldown (Stop the Spam)
    if raw_recovery and not df_hist.empty:
        last_rec = df_hist[df_hist['status'].str.contains("RECOVERY", na=False)]
        if not last_rec.empty:
            last_date = pd.to_datetime(last_rec.iloc[0]['date'])
            curr_date = pd.to_datetime(target_date) if target_date else pd.to_datetime(df_idx.iloc[0]['date'])
            days_since = (curr_date - last_date).days
            if days_since < cfg['cooldown_days']:
                raw_recovery = False
                block_reason = "BLOCKED_BY_COOLDOWN"

    # Filter 3: Depth (Minimum Pain Requirement)
    if raw_recovery and current_dd > cfg['min_index_drawdown']: # e.g., -5% > -8% is True (drawdown not deep enough)
        raw_recovery = False
        block_reason = "BLOCKED_BY_DEPTH"

    # Evaluation
    is_abort = (current_idx < ma10) or (breadth_pct < 15.0)
    status = "RECOVERY_ACTIVE" if raw_recovery else "STANDBY"
    if is_abort and not raw_recovery:
        status = "PILOT_ABORT"

    # Build clean log (no print — caller decides display)
    log = [
        f"Index Drawdown: {current_dd:.1f}% (Threshold: {cfg['min_index_drawdown']}%)",
        f"Breadth Stability: {breadth_std:.2f} (Threshold: < {cfg['breadth_std_threshold']})",
        f"MA10 Reclaim: {'YES' if reclaim_ma10 else 'NO'}",
        f"Vol/Breadth Thrust: {'YES' if thrust_2d else 'NO'}",
    ]
    if block_reason != "NONE":
        log.append(f"RECOVERY BLOCKED: {block_reason}")
    log.append(f"FINAL STATUS: {status}")

    return {
        "is_recovery": bool(raw_recovery),
        "is_abort": bool(is_abort),
        "status": status,
        "block_reason": block_reason,
        "ma10_reclaim": bool(reclaim_ma10),
        "log": log,
        "details": {
            "index_dd": round(current_dd, 2),
            "breadth_std": round(breadth_std, 2),
            "velocity_5d": float(velocity_5d)
        }
    }
