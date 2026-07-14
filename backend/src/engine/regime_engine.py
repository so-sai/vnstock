
import sys
from pathlib import Path

import numpy as np
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
from src.database.db_core import get_connection


def _calc_adx_dmi(df, period=14):
    """Calculates ADX, +DI, -DI for DMI-based trend analysis."""
    df = df.copy()
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(period).mean()
    return adx, plus_di, minus_di

def _ll(label: str, lang_mode: str = "compact") -> str:
    """Localize label if mode is not compact. Lazy import avoids circular deps."""
    if lang_mode == "compact":
        return label
    try:
        from src.core.canonical_output_adapter import localize_label
        return localize_label(label, lang_mode)
    except Exception:
        return label


def detect_regime(target_date=None, lang_mode: str = "compact"):
    """
    Institutional Regime Engine v2.0 (Score-Based):
    Calculates Regime Score (RS) = 0.5*B + 0.3*T + 0.2*V
    Includes [LOCK 2] ATR Shock Filter.
    Supports Point-in-time accuracy via target_date.
    lang_mode: 'compact' (EN), 'annotated' (EN+VI), 'full' (VI only), 'auto'
    """
    label_replay = _ll("REGIME ENGINE", lang_mode)
    label_sub = _ll("HISTORICAL REPLAY" if target_date else "LIVE ANALYSIS", lang_mode)
    print("\n" + "="*50)
    print(f"{label_replay} v2.0: {label_sub}")
    print("="*50)

    # 1. B-Score (Breadth): 50% Weight
    with get_connection() as conn:
        if target_date:
            # Point-in-time window: 60 days before target_date to ensure MA20 calculation
            df_all = pd.read_sql(f"SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date <= '{target_date}' AND date >= date('{target_date}', '-60 days') AND symbol != 'VNINDEX'", conn)
        else:
            df_all = pd.read_sql("SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date >= '2025-10-01' AND symbol != 'VNINDEX'", conn)

    df_all = df_all.copy()
    df_all['date'] = pd.to_datetime(df_all['date'], format='mixed')
    df_all = df_all.sort_values(['symbol', 'date'])
    current_date = pd.to_datetime(target_date) if target_date else df_all['date'].max()

    g = df_all.groupby('symbol')
    df_all['ma20'] = g['close'].transform(lambda x: x.rolling(20).mean())
    df_all['avg_vol_20d'] = g['volume'].transform(lambda x: x.rolling(20).mean())

    latest_df = df_all[df_all['date'] == current_date].copy()

    # ── BREADTH_SUSPENDED: không có dữ liệu cho current_date → hoãn breadth ──
    if latest_df.empty:
        print(f"  [REGIME] BREADTH_SUSPENDED: no data for {current_date.date()} — breadth deferred")
        breadth_pct = None
    else:
        liquid_df = latest_df[latest_df['avg_vol_20d'] >= 50000]
        breadth_pct = (len(liquid_df[liquid_df['close'] > liquid_df['ma20']]) / len(liquid_df) * 100) if not liquid_df.empty else 0

    # [INTERNAL HOOK] Calculate Breadth Stability (STD 10D) & Momentum
    with get_connection() as conn:
        if target_date:
            df_hist_b = pd.read_sql(f"SELECT breadth_pct FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT 10", conn)
        else:
            df_hist_b = pd.read_sql("SELECT breadth_pct FROM regime_history ORDER BY date DESC LIMIT 10", conn)

    # Calculate rolling STD including current data (filter None)
    all_breadth = [b for b in (df_hist_b['breadth_pct'].tolist() + [breadth_pct]) if b is not None]
    breadth_std_10d = np.std(all_breadth) if len(all_breadth) >= 2 else 0.0

    # Calculate Breadth Momentum (vs 5 days ago)
    if breadth_pct is None:
        breadth_momentum = 0.0
    else:
        breadth_5d_ago = df_hist_b['breadth_pct'].iloc[4] if len(df_hist_b) >= 5 else (df_hist_b['breadth_pct'].iloc[-1] if not df_hist_b.empty else breadth_pct)
        breadth_momentum = breadth_pct - breadth_5d_ago

    # Breadth Score: None when BREADTH_SUSPENDED
    b_score = None if breadth_pct is None else max(0.0, min(1.0, breadth_pct / 100.0))

    # 2. T-Score (Trend): 30% Weight
    with get_connection() as conn:
        if target_date:
            df_idx = pd.read_sql(f"SELECT symbol, date, high, low, close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date <= '{target_date}' ORDER BY date", conn)
        else:
            df_idx = pd.read_sql("SELECT symbol, date, high, low, close FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date", conn)

    df_idx = df_idx.copy()
    if df_idx.empty:
        latest_date = (pd.to_datetime(target_date) if target_date else datetime.now()).strftime("%Y-%m-%d")
        return {
            "date": latest_date,
            "regime_score": 0.5,
            "status": "RANGING",
            "details": {
                "b_score": 0.5,
                "breadth_pct": 50.0,
                "breadth_std_10d": 0.0,
                "breadth_momentum": 0.0,
                "t_score": 0.5,
                "vnindex_vs_ma200": "NO_DATA",
                "vnindex_vs_ma50": "NO_DATA",
                "ma50_slope": 0.0,
                "adx": 0.0,
                "v_score": 0.5,
                "atr_ratio": 1.0,
                "error": "NO_VNINDEX_DATA",
            }
        }
    df_idx['date'] = pd.to_datetime(df_idx['date'], format='mixed')
    df_idx['ma200'] = df_idx['close'].rolling(200).mean()
    df_idx['ma50'] = df_idx['close'].rolling(50).mean()
    df_idx['ma20'] = df_idx['close'].rolling(20).mean()
    df_idx['adx'], df_idx['plus_di'], df_idx['minus_di'] = _calc_adx_dmi(df_idx)
    latest_idx = df_idx.iloc[-1]

    # Multi-timeframe position matrix: 40% short(MA20) + 30% medium(MA50) + 30% secular(MA200)
    t_short = 1.0 if latest_idx['close'] > latest_idx['ma20'] else 0.0
    t_medium = 1.0 if latest_idx['close'] > latest_idx['ma50'] else 0.0
    t_long = 1.0 if latest_idx['close'] > latest_idx['ma200'] else 0.0
    t_base = 0.4 * t_short + 0.3 * t_medium + 0.3 * t_long

    # DMI Penalty: ADX < 20 (low momentum) AND (DMI- − DMI+) > 2.0 (hysteresis band)
    #   → hair-cut 50%: e.g. 0.60 → 0.30, forces regime into CORRECTING not RANGING
    #   Band > 2.0 eliminates whipsaw false signals when vectors entangle in low liquidity
    minus_di_val = float(latest_idx['minus_di']) if not np.isnan(latest_idx['minus_di']) else 0.0
    plus_di_val = float(latest_idx['plus_di']) if not np.isnan(latest_idx['plus_di']) else 0.0
    dmi_penalty = bool(latest_idx['adx'] < 20 and (minus_di_val - plus_di_val) > 2.0)
    t_score = t_base * 0.5 if dmi_penalty else t_base

    # MA50 Slope — diagnostic only (not in T-Score computation)
    ma50_today = latest_idx['ma50']
    ma50_5d_ago = df_idx['ma50'].iloc[-6] if len(df_idx) >= 6 else ma50_today
    ma50_slope = ma50_today - ma50_5d_ago

    # 3. V-Score (Volatility): 20% Weight
    tr = pd.concat([
        df_idx['high'] - df_idx['low'],
        (df_idx['high'] - df_idx['close'].shift(1)).abs(),
        (df_idx['low'] - df_idx['close'].shift(1)).abs()
    ], axis=1).max(axis=1)

    atr_20 = tr.rolling(20).mean()
    atr_today = tr.iloc[-1]
    atr_avg = atr_20.iloc[-1]

    # Volatility Score: continuous inverse of ATR ratio excess
    # v_score = 1.0 - clamp(atr_ratio - 1.0, 0.0, 0.8)  ->  range [0.2, 1.0]
    # Replaces discrete 3-step to eliminate cliff-edge jumps at 1.5x ATR boundary
    atr_ratio = (atr_today / atr_avg) if atr_avg and atr_avg > 0 else 1.0
    v_score = max(0.2, min(1.0, 1.0 - max(0.0, min(0.8, atr_ratio - 1.0))))

    # 4. Final Aggregation — Raw Score (BREADTH_SUSPENDED → 2-factor fallback)
    if b_score is None:
        regime_score_raw = (0.6 * t_score) + (0.4 * v_score)
        print(f"  [REGIME] BREADTH_SUSPENDED — 2-factor score: T={t_score:.2f} V={v_score:.2f} → raw={regime_score_raw:.4f}")
    else:
        regime_score_raw = (0.5 * b_score) + (0.3 * t_score) + (0.2 * v_score)

    # [LOCK 2] ATR Shock: applied to raw score before smoothing so the EMA sees the shock signal
    if atr_today > 1.5 * atr_avg:
        flag = ">>" if sys.platform == "win32" else "\u26a0\ufe0f"
        label_shock = _ll("[LOCK 2] ATR SHOCK DETECTED", lang_mode)
        label_today = _ll("Today:", lang_mode)
        label_avg = _ll("vs Avg:", lang_mode)
        print(f"{flag} {label_shock} ({label_today} {atr_today:.2f} {label_avg} {atr_avg:.2f}).")
        regime_score_raw *= 0.7

    # 5. Irregular Time-Series EMA Smoothing
    # Base alpha from ATR volatility: clamp(0.2 * atr_ratio, 0.1, 1.0)
    #   -> low volatility  : alpha near 0.1 (heavy smoothing)
    #   -> high volatility  : alpha near 1.0 (pass-through)
    ema_alpha = max(0.1, min(1.0, 0.2 * atr_ratio))

    # Seed: fetch the most recent smoothed regime_score + its date
    with get_connection() as conn:
        if target_date:
            df_prev = pd.read_sql(
                "SELECT date, regime_score FROM regime_history "
                f"WHERE date < '{target_date}' ORDER BY date DESC LIMIT 1",
                conn
            )
        else:
            df_prev = pd.read_sql(
                "SELECT date, regime_score FROM regime_history ORDER BY date DESC LIMIT 1",
                conn
            )
    prev_smoothed = df_prev['regime_score'].iloc[0] if not df_prev.empty else regime_score_raw
    prev_date_str = df_prev['date'].iloc[0] if not df_prev.empty else None

    # Time-decay factor: alpha_decay = 1 - exp(-lambda * dt)
    #   lambda = 0.1 (half-life ~7 trading days)
    #   When cron dies for 14 days: alpha_decay = 1 - exp(-1.4) ≈ 0.75
    #     → system forgets stale prev, gives 75% weight to fresh raw score
    #   When daily update runs (dt=1): alpha_decay = 1 - exp(-0.1) ≈ 0.095
    #     → ATR-based alpha dominates, preserves smoothing
    LAMBDA_DECAY = 0.1
    if prev_date_str:
        try:
            dt_days = (current_date - pd.to_datetime(prev_date_str)).days
        except Exception:
            dt_days = 1
    else:
        dt_days = 1
    alpha_decay = 1.0 - np.exp(-LAMBDA_DECAY * dt_days)

    # Effective alpha = max(ATR base, time-decay) — more reactive wins
    alpha_effective = max(ema_alpha, alpha_decay)

    # Irregular EMA: RS_smoothed = alpha_eff * RS_raw + (1 - alpha_eff) * RS_prev
    regime_score = (alpha_effective * regime_score_raw) + ((1.0 - alpha_effective) * prev_smoothed)

    # ── Momentum Velocity: 5D & 10D Rate of Change (Z-Score) ──
    velocity_penalty = 0.0
    momentum = {}
    try:
        close_series = df_idx['close']
        roc_5d = (close_series.iloc[-1] / close_series.iloc[-6] - 1) * 100 if len(close_series) >= 6 else 0.0
        roc_10d = (close_series.iloc[-1] / close_series.iloc[-11] - 1) * 100 if len(close_series) >= 11 else 0.0

        # Z-Score of 5D ROC (60-session rolling window)
        roc_5d_series = close_series.pct_change(5) * 100
        roc_5d_mean = float(roc_5d_series.rolling(60).mean().iloc[-1]) if len(close_series) >= 65 else 0.0
        roc_5d_std = float(roc_5d_series.rolling(60).std().iloc[-1]) if len(close_series) >= 65 else 1.0
        roc_5d_z = (roc_5d - roc_5d_mean) / roc_5d_std if roc_5d_std > 0 else 0.0

        roc_10d_series = close_series.pct_change(10) * 100
        roc_10d_mean = float(roc_10d_series.rolling(60).mean().iloc[-1]) if len(close_series) >= 70 else 0.0
        roc_10d_std = float(roc_10d_series.rolling(60).std().iloc[-1]) if len(close_series) >= 70 else 1.0
        roc_10d_z = (roc_10d - roc_10d_mean) / roc_10d_std if roc_10d_std > 0 else 0.0

        momentum = {
            "roc_5d": round(roc_5d, 2),
            "roc_10d": round(roc_10d, 2),
            "roc_5d_z": round(roc_5d_z, 2),
            "roc_10d_z": round(roc_10d_z, 2),
        }

        # Velocity penalty: gamma * clamp(|roc_5d| * 0.01, 0, 0.20) when roc_5d negative
        #   e.g. -3.7% drop → penalty = 1.0 * 0.037 = 0.037
        #   Absolute bounding: final_raw_score = max(0.0, min(1.0, base_score - penalty))
        if roc_5d < 0:
            gamma = 1.0
            velocity_penalty = gamma * max(0.0, min(0.20, abs(roc_5d) / 100.0))
            regime_score = max(0.0, min(1.0, regime_score - velocity_penalty))
    except Exception:
        pass

    # Status classification applied to the ADJUSTED smoothed score
    status = "TRENDING" if regime_score > 0.65 else "RANGING" if regime_score >= 0.35 else "CRISIS"

    # [RAD v3] Regime Acceleration Detector — Dynamic Scoring via RPA
    # Risk Points Accumulation (RPA): each signal contributes 1 point.
    # Threshold ≥ 2 → override CRISIS_WARNING. Gold is separate Black Swan flag.
    rad = {"activated": False, "override_status": None, "signals": {}}
    try:
        idx_len = len(df_idx)
        if idx_len >= 10:
            lookback = min(4, idx_len - 2)
            adx_today = float(latest_idx['adx'])
            adx_t3 = float(df_idx['adx'].iloc[-1 - lookback])
            delta_adx = adx_today - adx_t3

            # DMI lead: phe bán (DMI-) đang kiểm soát?
            minus_di_val = float(latest_idx['minus_di']) if not np.isnan(latest_idx['minus_di']) else 0.0
            plus_di_val = float(latest_idx['plus_di']) if not np.isnan(latest_idx['plus_di']) else 0.0
            dmi_lead_bears = bool(minus_di_val > plus_di_val)

            # v_breadth: breadth velocity (3-session lookback)
            with get_connection() as conn:
                if target_date:
                    df_b = pd.read_sql(
                        f"SELECT date, breadth_pct FROM regime_history WHERE date < '{target_date}' ORDER BY date DESC LIMIT 1",
                        conn
                    )
                else:
                    df_b = pd.read_sql(
                        "SELECT date, breadth_pct FROM regime_history ORDER BY date DESC LIMIT 1",
                        conn
                    )
            breadth_prev = float(df_b['breadth_pct'].iloc[0]) if not df_b.empty else (breadth_pct or 0)
            v_breadth = ((breadth_pct or 0) - breadth_prev) / 3

            # DXY rate of change — macro stress signal
            dxy_roc = None
            try:
                with get_connection() as conn:
                    df_dxy = pd.read_sql(
                        "SELECT date, value FROM macro_history WHERE variable='DXY' ORDER BY date DESC LIMIT 6",
                        conn
                    )
                if len(df_dxy) >= 6:
                    dxy_today = float(df_dxy['value'].iloc[0])
                    dxy_5d = float(df_dxy['value'].iloc[-1])
                    dxy_roc = ((dxy_today / dxy_5d) - 1) * 100
            except Exception:
                pass

            # Asia cross-asset rotation — spectral signal from Phase 4
            rotation_angle = None
            try:
                from src.services.macro.time_series_aligner import TimeSeriesAligner
                tsa = TimeSeriesAligner()
                rotation = tsa.compute_asia_rotation()
                if rotation and isinstance(rotation, dict):
                    rotation_angle = rotation.get("rotation_angle_deg")
            except Exception:
                pass

            # Gold premium — Black Swan flag, DOES NOT count toward RPA total
            gold_premium = None
            try:
                sys.path.insert(0, str(PROJECT_ROOT))
                from core.macro.gold_spread_engine import analyze_domestic_premium
                gp = analyze_domestic_premium()
                gold_premium = gp.get('premium_pct', 0)
            except Exception:
                pass

            # ── Risk Points Accumulation ──
            risk_points = 0
            rpa_signals = {}

            rpa_signals["v_breadth_crash"] = v_breadth < -3
            if rpa_signals["v_breadth_crash"]:
                risk_points += 1

            rpa_signals["delta_adx_surge"] = bool(delta_adx > 5 and dmi_lead_bears)
            if rpa_signals["delta_adx_surge"]:
                risk_points += 1

            rpa_signals["asia_rotation_surge"] = rotation_angle is not None and rotation_angle > 45
            if rpa_signals["asia_rotation_surge"]:
                risk_points += 1

            rpa_signals["dxy_stress"] = dxy_roc is not None and dxy_roc > 1
            if rpa_signals["dxy_stress"]:
                risk_points += 1

            rpa_signals["black_swan_gold"] = gold_premium is not None and gold_premium > 3

            rad["signals"] = {
                "delta_adx": round(delta_adx, 2),
                "v_breadth": round(v_breadth, 2),
                "dmi_lead_bears": dmi_lead_bears,
                "dxy_roc_pct": round(dxy_roc, 2) if dxy_roc is not None else None,
                "rotation_angle": round(rotation_angle, 1) if rotation_angle is not None else None,
                "gold_premium": gold_premium,
                "rpa": rpa_signals,
                "risk_points": risk_points,
            }

            if risk_points >= 2:
                status = "CRISIS_WARNING"
                rad["activated"] = True
                rad["override_status"] = "CRISIS_WARNING"
                rad["reason"] = f"RPA≥2 (points={risk_points})"
    except Exception:
        pass

    verdict = {
        "date": current_date.strftime("%Y-%m-%d"),
        "regime_score": round(regime_score, 2),
        "status": status,
        "regime_score_raw": round(regime_score_raw, 4),
        "ema_alpha": round(ema_alpha, 4),
        "alpha_decay": round(alpha_decay, 4),
        "alpha_effective": round(alpha_effective, 4),
        "dt_days": dt_days,
        "velocity_penalty": round(velocity_penalty, 4),
        "rad": rad,
        "details": {
            "b_score": round(b_score, 4) if b_score is not None else None,
            "breadth_pct": round(breadth_pct, 1) if breadth_pct is not None else None,
            "breadth_std_10d": round(breadth_std_10d, 2),
            "breadth_momentum": round(breadth_momentum, 1),
            "t_score": round(t_score, 4),
            "t_short": round(t_short, 1),
            "t_medium": round(t_medium, 1),
            "t_long": round(t_long, 1),
            "dmi_penalty": dmi_penalty,
            "dmi_hysteresis": round(minus_di_val - plus_di_val, 2) if dmi_penalty else None,
            "vnindex_vs_ma200": "ABOVE" if latest_idx['close'] > latest_idx['ma200'] else "BELOW",
            "vnindex_vs_ma50": "ABOVE" if latest_idx['close'] > latest_idx['ma50'] else "BELOW",
            "vnindex_vs_ma20": "ABOVE" if latest_idx['close'] > latest_idx['ma20'] else "BELOW",
            "ma50_slope": round(ma50_slope, 2),
            "adx": round(latest_idx['adx'], 1),
            "v_score": round(v_score, 4),
            "atr_ratio": round(atr_ratio, 4),
            "momentum": momentum,
        }
    }

    # Localized labels for live analysis block
    lb = _ll("B-Score (continuous)", lang_mode)
    lt = _ll("T-Score", lang_mode)
    lv = _ll("V-Score (continuous)", lang_mode)
    lr = _ll("Raw Score", lang_mode)
    le = _ll("EMA Alpha", lang_mode)
    lp = _ll("Prev Smoothed", lang_mode)
    ls = _ll("SMOOTHED REGIME SCORE", lang_mode)
    lm = _ll("Momentum(5D/10D)", lang_mode)
    # Compute dynamic column width for alignment
    labels = [lb, lt, lv, lr]
    max_w = max(len(l) for l in labels)
    if b_score is not None:
        print(f"  {lb:{max_w}s} {b_score:.4f} ({breadth_pct:.1f}%)")
    else:
        print(f"  {lb:{max_w}s} BREADTH_SUSPENDED (no data for {current_date.date()})")
    print(f"  {lt:{max_w}s} {t_score:.4f} (MA20/50/200: {t_short:.0f}/{t_medium:.0f}/{t_long:.0f}, ADX: {latest_idx['adx']:.1f})")
    if dmi_penalty:
        print(f"  DMI HAIRCUT        ACTIVE (ADX<20, DMI--DMI+>2.0, T halved {t_base:.3f}->{t_score:.3f})")
    print(f"  {lv:{max_w}s} {v_score:.4f} (ATR Ratio: {atr_ratio:.4f})")
    print(f"  {lr:{max_w}s} {regime_score_raw:.4f}")
    print(f"  {le:{max_w}s} {ema_alpha:.4f}  |  α_decay:{alpha_decay:.4f} α_eff:{alpha_effective:.4f} (Δt={dt_days}d)")
    if momentum:
        print(f"  {lm:{max_w}s} {momentum.get('roc_5d','?'):>6}%/{momentum.get('roc_10d','?'):>6}%  Z:{momentum.get('roc_5d_z','?'):>5.1f}/{momentum.get('roc_10d_z','?'):>5.1f}")
    if velocity_penalty > 0:
        print(f"  VELOCITY PENALTY   -{velocity_penalty:.4f}")
    if rad.get("activated"):
        print(f"  ⚠ RAD OVERRIDE    {rad.get('reason','')} (risk_points={rad['signals'].get('risk_points',0)})")
    print("-" * 30)
    flag = ">>" if sys.platform == "win32" else "\U0001f6a9"
    print(f"{flag} {ls}: {regime_score:.4f} -> {status}")
    print("="*50)

    return verdict


def backfill_regime_history(target_date=None, batch_size=30):
    """
    Backfill regime_history for dates missing from daily_ohlcv.
    Processes in reverse chronological order (latest first) so EMA
    seeds are available for each computation.

    Returns: (processed, inserted) count tuple.
    """
    from src.database.db_core import save_data_upsert
    import time

    with get_connection() as conn:
        # All VNINDEX dates
        df_idx = pd.read_sql(
            "SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date",
            conn
        )
        # Existing regime dates
        df_reg = pd.read_sql(
            "SELECT date FROM regime_history", conn
        )

    all_dates = set(df_idx['date'].tolist())
    existing = set(df_reg['date'].tolist())
    missing = sorted(all_dates - existing)

    if target_date:
        missing = [d for d in missing if d <= target_date]

    print(f"\n{'='*50}")
    print(f"BACKFILL REGIME: {len(missing)}/{len(all_dates)} dates missing")
    print(f"{'='*50}")

    if not missing:
        print("✅ regime_history đã đầy đủ.")
        return 0, 0

    processed = 0
    inserted = 0
    t0 = time.time()

    for i, d in enumerate(missing):
        try:
            verdict = detect_regime(target_date=d, lang_mode="compact")
            if not verdict or not verdict.get('regime_score'):
                continue

            details = verdict.get('details', {})
            row = {
                "date": d,
                "regime_score": verdict.get('regime_score'),
                "status": verdict.get('status', 'RANGING'),
                "breadth_pct": details.get('breadth_pct'),
                "breadth_velocity": details.get('breadth_momentum', 0.0),
                "trend_score": details.get('t_score'),
                "vol_score": details.get('v_score'),
                "atr_ratio": details.get('atr_ratio'),
                "active_model": 'NONE',
                "recovery_flag": 0,
            }
            df_row = pd.DataFrame([row])

            with get_connection() as conn:
                save_data_upsert("regime_history", df_row, conn)

            inserted += 1
            if (i + 1) % 10 == 0:
                elapsed = time.time() - t0
                eta = (elapsed / (i + 1)) * (len(missing) - i - 1)
                print(f"  [{i+1}/{len(missing)}] ✅ {d}  score={row['regime_score']}  ({elapsed:.0f}s elapsed, ETA {eta:.0f}s)")

        except Exception as exc:
            print(f"  [{i+1}/{len(missing)}] ❌ {d}: {exc}")
        processed += 1

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"✅ BACKFILL HOÀN TẤT: {inserted}/{processed} inserted trong {elapsed:.0f}s")
    print(f"{'='*50}")
    return processed, inserted


if __name__ == "__main__":
    detect_regime()
