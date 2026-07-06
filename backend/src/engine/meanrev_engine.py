import sys
from pathlib import Path

import pandas as pd


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


def run_meanrev_scan(regime_data=None, target_date=None):
    """
    Model B v2: Controlled Pullback Continuation Engine.
    Chuyển từ 'deep oversold reversal' sang 'institutional pullback exploitation'.
    Kiến trúc 4 tầng: Regime Context → Pullback Quality → Participation Recovery → Xếp hạng.
    """
    print("\n" + "="*50)
    print(f"MODEL B V2: {'HISTORICAL REPLAY' if target_date else 'LIVE ANALYSIS'}")
    print("="*50)

    cfg = src.config.MODEL_B_CONFIG
    sec_cfg = cfg['secondary_context']

    # 1. Regime Context
    if not regime_data:
        from src.engine.regime_engine import detect_regime
        regime_data = detect_regime(target_date=target_date)

    details = regime_data['details']
    breadth_pct = details['breadth_pct']
    breadth_std = details['breadth_std_10d']
    breadth_mom = details['breadth_momentum']
    atr_ratio = details['atr_ratio']

    vnindex_above_ma200 = details['vnindex_vs_ma200'] == "ABOVE"
    vnindex_above_ma50 = details.get('vnindex_vs_ma50', 'BELOW') == "ABOVE"
    ma50_slope = details.get('ma50_slope', 0.0)

    # --- LAYER 1: REGIME CONTEXT ---
    context_source = "BLOCKED"
    block_reason = ""

    primary_ok = vnindex_above_ma200
    secondary_ok = (
        vnindex_above_ma50 and
        ma50_slope > sec_cfg['min_ma50_slope'] and
        breadth_pct > sec_cfg['min_breadth'] and
        breadth_std < sec_cfg['max_breadth_std']
    )

    if primary_ok:
        context_source = "PRIMARY_MA200"
    elif secondary_ok:
        context_source = "SECONDARY_STABLE"
    else:
        reasons = []
        if not vnindex_above_ma200: reasons.append("VNINDEX_BELOW_MA200")
        if not vnindex_above_ma50: reasons.append("VNINDEX_BELOW_MA50")
        if ma50_slope <= sec_cfg['min_ma50_slope']: reasons.append("SLOPE_TOO_FLAT")
        if breadth_pct <= sec_cfg['min_breadth']: reasons.append(f"BREADTH_LOW({breadth_pct:.0f}%)")
        if breadth_std >= sec_cfg['max_breadth_std']: reasons.append(f"STD_HIGH({breadth_std:.1f})")
        block_reason = "|".join(reasons)
        print(f"[BLOCKED] Context Lock: {block_reason}")
        return []

    print(f">>> CONTEXT: {context_source}")

    # Adaptive RSI
    if atr_ratio < 0.9:
        rsi_threshold = cfg['adaptive_rsi']['low_vol']
    elif atr_ratio < 1.3:
        rsi_threshold = cfg['adaptive_rsi']['mid_vol']
    else:
        rsi_threshold = cfg['adaptive_rsi']['standard']

    # Breadth Kill Switch
    if breadth_pct < 15:
        print("[KILL] Breadth < 15%. Model B disabled.")
        return []

    # --- BREADTH EXPANSION CHECK ---
    breadth_velocity = regime_data.get('breadth_velocity', 0.0)
    adv_dec_ratio = details.get('adv_dec_ratio', 1.0)
    expansion_ok = breadth_velocity > cfg['breadth_expansion']['min_velocity'] or adv_dec_ratio > cfg['breadth_expansion']['min_adv_dec_ratio']

    if not expansion_ok:
        print(f"[BLOCKED] No breadth expansion. velocity={breadth_velocity:.2f}, adv/dec={adv_dec_ratio:.2f}")
        return []

    # 2. Fetch Data
    with get_connection() as conn:
        limit_days = 400
        if target_date:
            df = pd.read_sql(
                "SELECT symbol, date, close, volume FROM daily_ohlcv "
                "WHERE date <= ? AND date >= date(?, '-400 days')",
                conn, params=(target_date, target_date)
            )
        else:
            df = pd.read_sql("SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date >= '2025-06-01'", conn)

    df['date'] = pd.to_datetime(df['date'], format='mixed')
    df = df.sort_values(['symbol', 'date'])
    current_date = pd.to_datetime(target_date) if target_date else df['date'].max()

    # 3. Vectorized feature computation
    g = df.groupby('symbol')
    df['ma20'] = g['close'].transform(lambda x: x.rolling(20).mean())
    df['std20'] = g['close'].transform(lambda x: x.rolling(20).std())
    df['ma50'] = g['close'].transform(lambda x: x.rolling(50).mean())
    df['ma100'] = g['close'].transform(lambda x: x.rolling(100).mean())
    df['ma200'] = g['close'].transform(lambda x: x.rolling(200).mean())
    df['avg_vol_20d'] = g['volume'].transform(lambda x: x.rolling(20).mean())

    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    df['rsi14'] = g['close'].transform(calc_rsi)
    df['atr14'] = g['close'].transform(lambda x: (x.diff().abs()).rolling(14).mean())

    # 4. Latest snapshot
    latest_df = df[df['date'] == current_date].copy()
    trend_filter = latest_df['ma200'] if primary_ok else latest_df['ma100']

    candidates = latest_df[
        (latest_df['close'] > trend_filter) &
        (latest_df['avg_vol_20d'] >= 50000)
    ].copy()

    if candidates.empty:
        print("[SKIP] No symbols passing trend filter + volume threshold.")
        return []

    # --- LAYER 2: PULLBACK QUALITY ---
    candidates = candidates.dropna(subset=['ma50', 'close'])
    candidates['distance_from_ma50'] = ((candidates['close'] - candidates['ma50']) / candidates['ma50']) * 100

    pb_min = cfg['pullback_range']['min_pct']
    pb_max = cfg['pullback_range']['max_pct']

    pullback_mask = (
        (candidates['distance_from_ma50'] > pb_min) &
        (candidates['distance_from_ma50'] < pb_max)
    )

    pulls = candidates[pullback_mask].copy()
    out_of_range = candidates[~pullback_mask]

    deep_count = len(out_of_range[out_of_range['distance_from_ma50'] <= pb_min])
    micro_count = len(out_of_range[out_of_range['distance_from_ma50'] >= pb_max])
    total = len(candidates)

    print(f"[PULLBACK] Valid: {len(pulls)} | Too deep: {deep_count} | Micro noise: {micro_count} | Total: {total}")

    if pulls.empty:
        print("[EMPTY] No symbols in valid pullback range.")
        return []

    # --- LAYER 3: OVERSOLD CONFIRMATION (RSI) ---
    final = pulls[
        (pulls['rsi14'] < rsi_threshold)
    ].copy()

    if final.empty:
        print(f"[EMPTY] No symbols with RSI < {rsi_threshold} in pullback range.")
        return []

    print(f"Adaptive: RSI < {rsi_threshold} (ATR Ratio: {atr_ratio})")

    # 5. Ranking: closest_to_norm (prefer controlled pullback near equilibrium)
    final['dist_ratio'] = 1.0 / (final['distance_from_ma50'].abs() + 1.0)
    final['vol_rank'] = final['avg_vol_20d'].rank(pct=True)
    final['rank_score'] = (0.5 * final['dist_ratio']) + (0.5 * final['vol_rank'])
    final = final.sort_values('rank_score', ascending=False)

    # 6. Output
    results = []
    for _, row in final.head(10).iterrows():
        print(f"Pick: {row['symbol']} | MA50 dist: {row['distance_from_ma50']:.1f}% | RSI: {row['rsi14']:.1f}")
        results.append({
            "symbol": row['symbol'],
            "distance_from_ma50": round(row['distance_from_ma50'], 2),
            "rsi": round(row['rsi14'], 1),
            "rank_score": round(row['rank_score'], 4),
            "context_source": context_source,
            "price": round(float(row['close']), 2),
            "avg_vol_20d": int(row['avg_vol_20d']),
        })

    return results
