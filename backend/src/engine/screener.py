import os
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
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path

PROJECT_ROOT = _hydrate_path()
if sys.platform == "win32" and getattr(sys.stdout, 'encoding', '') != 'utf-8':
    import io
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import numpy as np
import pandas as pd

import src.config
from src.database.db_core import get_connection

RS_COLS = ['symbol', 'close', 'rs_21d', 'rs_63d', 'rs_126d', 'mom_5d', 'mom_20d', 'mom_50d',
           'vol_trend', 'rs_improve', 'near_high', 'vol_stability', 'composite']

PHASE_LABELS = [
    (0.7, 'BUNG PHA'),
    (0.5, 'TANG TRUONG'),
    (0.3, 'PHUC HOI'),
    (0.0, 'SUY YEU'),
]

def phase_label(score):
    for threshold, label in PHASE_LABELS:
        if score >= threshold:
            return label
    return 'SUY YEU'

def compute_rs_line(df_stock, df_idx, window=63):
    merged = df_stock.merge(df_idx, on='date', how='inner', suffixes=('', '_idx'))
    merged = merged.sort_values('date')
    if len(merged) < window + 5:
        return None
    merged['rs_line'] = merged['close'] / merged['idx_close']
    merged['rs_{}d'.format(window)] = merged['rs_line'].pct_change(window)
    return merged

def compute_momentum(series, window):
    if len(series) < window + 2:
        return np.nan
    return series.iloc[-1] / series.iloc[-window - 1] - 1

def scan_market(lookback=252, top_n=20, min_value=1e9):
    from src.database.data_integrity import ensure_vnindex_integrity
    ensure_vnindex_integrity(verbose=False)

    print("\n" + "="*65)
    print("STOCK DISCOVERY ENGINE — Quét cơ hội thị trường")
    print("="*65)

    db_path = os.path.join(src.config.DATA_DIR, "screener_cache.db")
    if not os.path.exists(db_path):
        print("Loi: CSDL khong ton tai.")
        return

    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT symbol, date, close, volume
            FROM daily_ohlcv
            WHERE symbol != 'VNINDEX'
            ORDER BY symbol, date
        """, conn)
        df_idx = pd.read_sql("""
            SELECT date, close as idx_close
            FROM daily_ohlcv
            WHERE symbol = 'VNINDEX'
            ORDER BY date
        """, conn)

    if df.empty or df_idx.empty:
        print("Khong du du lieu.")
        return

    df['date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce').dt.normalize()
    df_idx['date'] = pd.to_datetime(df_idx['date'], format='mixed', errors='coerce').dt.normalize()
    df = df.dropna(subset=['date'])
    df_idx = df_idx.dropna(subset=['date'])
    df = df[df['close'] > 0]
    df_idx = df_idx[df_idx['idx_close'] > 100]

    cutoff = df['date'].max() - pd.Timedelta(days=lookback)
    df = df[df['date'] >= cutoff].copy()

    daily = df.groupby(['symbol', 'date'], as_index=False).agg(
        close=('close', 'last'), volume=('volume', 'last'))
    daily = daily[daily['close'] > 0]
    daily = daily.sort_values(['symbol', 'date']).reset_index(drop=True)

    daily.insert(3, 'traded_value', daily['close'] * daily['volume'])
    recent = daily.loc[daily['date'] >= daily['date'].max() - pd.Timedelta(days=20)]
    avg_val = recent.groupby('symbol', as_index=False)['traded_value'].mean()
    avg_val.columns = ['symbol', 'avg_value']
    avg_val = avg_val.loc[avg_val['avg_value'] >= min_value]
    liquid_symbols = set(avg_val['symbol'])
    daily = daily[daily['symbol'].isin(liquid_symbols)].copy()

    df_idx = df_idx[df_idx['date'] >= cutoff].copy()
    df_idx = df_idx.sort_values('date')

    with get_connection() as conn:
        sector_df = pd.read_sql("SELECT symbol, icb_name2 as industry_name FROM symbol_industry", conn)
    sector_map = dict(zip(sector_df['symbol'], sector_df['industry_name']))

    print(f"Tong so ma phan tich: {len(liquid_symbols)}")
    print(f"Ky phan tich: {daily['date'].min().date()} → {daily['date'].max().date()} ({len(daily['date'].unique())} phien)")
    print()

    merged = daily.merge(df_idx[['date', 'idx_close']], on='date', how='inner')
    merged = merged.sort_values(['symbol', 'date'])

    LAG = 21
    results = []
    today = merged['date'].max()

    for sym in liquid_symbols:
        stock = merged[merged['symbol'] == sym].copy()
        min_req = 90
        if len(stock) < min_req:
            continue

        stock = stock.sort_values('date')

        rs_line = stock['close'] / stock['idx_close']
        rs_21d = rs_line.pct_change(21)
        rs_63d = rs_line.pct_change(63)
        rs_126d = rs_line.pct_change(126)

        def get_rs(arr, idx=1):
            return arr.iloc[idx] if len(arr) >= abs(idx) + 1 and not pd.isna(arr.iloc[idx]) else 0.0

        rs_21d_val = get_rs(rs_21d, -1)
        rs_63d_val = get_rs(rs_63d, -1)
        rs_126d_val = get_rs(rs_126d, -1)
        rs_21d_prev = get_rs(rs_21d, -1 - LAG)
        rs_63d_prev = get_rs(rs_63d, -1 - LAG)

        close_vals = stock['close'].values
        vol_series = stock['volume'].values

        def val_at(arr, idx, default=0.0):
            return arr[idx] if len(arr) > abs(idx) else default
        def ratio(arr, i1, i2, default=0.0):
            return arr[i1] / arr[i2] - 1 if len(arr) > max(abs(i1), abs(i2)) and arr[i2] != 0 else default

        mom_5d = ratio(close_vals, -1, -6)
        mom_20d = ratio(close_vals, -1, -21)
        mom_50d = ratio(close_vals, -1, -51)
        mom_5d_prev = ratio(close_vals, -1 - LAG, -6 - LAG)
        mom_20d_prev = ratio(close_vals, -1 - LAG, -21 - LAG)
        mom_50d_prev = ratio(close_vals, -1 - LAG, -51 - LAG)

        def avg_range(arr, start, end, default=0):
            return np.mean(arr[start:end]) if len(arr) >= abs(start) else default

        v20_now = avg_range(vol_series, -20, None)
        v50_now = avg_range(vol_series, -50, None)
        vt_now = (v20_now / v50_now - 1) if v50_now > 0 else 0
        v20_prev = avg_range(vol_series, -41, -21)
        v50_prev = avg_range(vol_series, -71, -21)
        vt_prev = (v20_prev / v50_prev - 1) if v50_prev > 0 else 0

        rs_improve = rs_21d_val - rs_63d_val
        rs_improve_prev = rs_21d_prev - rs_63d_prev

        h52 = np.max(close_vals[-252:]) if len(close_vals) >= 252 else np.max(close_vals)
        near_high = close_vals[-1] / h52 if h52 > 0 else 0
        h52_prev = np.max(close_vals[-(252 + LAG):-LAG]) if len(close_vals) >= 252 + LAG else np.max(close_vals[:-LAG]) if len(close_vals) > LAG else 0
        near_high_prev = close_vals[-1 - LAG] / h52_prev if h52_prev > 0 and len(close_vals) > LAG else 0

        daily_returns = np.diff(close_vals) / close_vals[:-1]
        vol_stability = 1.0 / (np.std(daily_returns[-60:]) + 0.001) if len(daily_returns) > 60 else 0

        ma20 = np.mean(close_vals[-20:]) if len(close_vals) >= 20 else close_vals[-1]
        ma50 = np.mean(close_vals[-50:]) if len(close_vals) >= 50 else close_vals[-1]
        price_vs_ma20 = close_vals[-1] / ma20 - 1 if ma20 > 0 else 0
        price_vs_ma50 = close_vals[-1] / ma50 - 1 if ma50 > 0 else 0
        ma_bull = 1 if ma20 > ma50 else 0

        h20 = np.max(close_vals[-20:])
        h50 = np.max(close_vals[-50:])
        bo20 = (close_vals[-1] - h20) / h20 if h20 > 0 else 0
        bo50 = (close_vals[-1] - h50) / h50 if h50 > 0 else 0

        rs_vals = rs_21d.dropna().values
        rs_streak = 0
        for v in reversed(rs_vals):
            if v > 0: rs_streak += 1
            else: break

        vol_days_above = np.sum(vol_series[-20:] > v50_now) if v50_now > 0 else 0
        vol_consistency_pct = vol_days_above / 20

        results.append({
            'symbol': sym, 'close': close_vals[-1],
            'rs_21d': rs_21d_val, 'rs_63d': rs_63d_val, 'rs_126d': rs_126d_val,
            'mom_5d': mom_5d, 'mom_20d': mom_20d, 'mom_50d': mom_50d,
            'vol_trend': vt_now, 'rs_improve': rs_improve, 'near_high': near_high,
            'vol_stability': vol_stability,
            'rs_21d_prev': rs_21d_prev, 'rs_63d_prev': rs_63d_prev,
            'mom_5d_prev': mom_5d_prev, 'mom_20d_prev': mom_20d_prev, 'mom_50d_prev': mom_50d_prev,
            'vol_trend_prev': vt_prev, 'rs_improve_prev': rs_improve_prev, 'near_high_prev': near_high_prev,
            'ma_bull': ma_bull, 'price_vs_ma20': price_vs_ma20, 'price_vs_ma50': price_vs_ma50,
            'breakout_20d': bo20, 'breakout_50d': bo50,
            'rs_streak': rs_streak, 'vol_consistency_pct': vol_consistency_pct,
        })

    if not results:
        print("Khong co ma nao du dieu kien.")
        return

    rdf = pd.DataFrame(results)
    rdf = rdf.replace([np.inf, -np.inf], 0).fillna(0)

    now_cols = ['rs_21d', 'rs_63d', 'rs_126d', 'mom_5d', 'mom_20d', 'mom_50d', 'vol_trend', 'rs_improve']
    prev_cols = [c + '_prev' for c in ['rs_21d', 'rs_63d', 'mom_5d', 'mom_20d', 'mom_50d', 'vol_trend', 'rs_improve']]

    for col in ['rs_21d', 'rs_63d', 'rs_126d'] + ['rs_21d_prev', 'rs_63d_prev']:
        rdf[col] = rdf[col].clip(-1, 5)
    for col in ['mom_5d', 'mom_20d', 'mom_50d'] + ['mom_5d_prev', 'mom_20d_prev', 'mom_50d_prev']:
        rdf[col] = rdf[col].clip(-0.95, 5)
    for col in ['vol_trend', 'vol_trend_prev', 'rs_improve', 'rs_improve_prev']:
        rdf[col] = rdf[col].clip(-1, 10)

    factor_cols = ['rs_21d', 'rs_63d', 'mom_5d', 'mom_20d', 'mom_50d',
                   'vol_trend', 'rs_improve', 'near_high']
    for col in factor_cols:
        rdf[col + '_pct'] = rdf[col].rank(pct=True)

    rdf['composite'] = (
        rdf['rs_21d_pct'] * 0.20 + rdf['rs_63d_pct'] * 0.15 +
        rdf['mom_5d_pct'] * 0.10 + rdf['mom_20d_pct'] * 0.15 +
        rdf['mom_50d_pct'] * 0.10 + rdf['vol_trend_pct'] * 0.10 +
        rdf['rs_improve_pct'] * 0.12 + rdf['near_high_pct'] * 0.08
    )

    for col in ['rs_21d_prev', 'rs_63d_prev', 'mom_5d_prev', 'mom_20d_prev',
                'mom_50d_prev', 'vol_trend_prev', 'rs_improve_prev', 'near_high_prev']:
        rdf[col + '_pct'] = rdf[col].rank(pct=True)

    rdf['composite_prev'] = (
        rdf['rs_21d_prev_pct'] * 0.20 + rdf['rs_63d_prev_pct'] * 0.15 +
        rdf['mom_5d_prev_pct'] * 0.10 + rdf['mom_20d_prev_pct'] * 0.15 +
        rdf['mom_50d_prev_pct'] * 0.10 + rdf['vol_trend_prev_pct'] * 0.10 +
        rdf['rs_improve_prev_pct'] * 0.12 + rdf['near_high_prev_pct'] * 0.08
    )

    rdf['rank_now'] = rdf['composite'].rank(ascending=False)
    rdf['rank_prev'] = rdf['composite_prev'].rank(ascending=False)
    rdf['rank_change'] = rdf['rank_prev'] - rdf['rank_now']

    rdf['industry'] = rdf['symbol'].map(sector_map).fillna('')
    rdf['rs_strong'] = (rdf['rs_21d_pct'] >= 0.5).astype(int)

    def sector_alignment(g):
        strong_count = g['rs_strong'].sum()
        total = max(len(g), 1)
        g['sector_ratio'] = strong_count / total
        return g
    rdf = rdf.groupby('industry', group_keys=False).apply(sector_alignment)
    rdf['sector_ratio'] = rdf['sector_ratio'].fillna(0)

    def confirm_label(row):
        score = 0
        if row['rs_streak'] >= 10: score += 2
        elif row['rs_streak'] >= 5: score += 1
        if row['vol_consistency_pct'] >= 0.6: score += 2
        elif row['vol_consistency_pct'] >= 0.4: score += 1
        if row['ma_bull'] == 1: score += 1
        if row['breakout_20d'] >= 0: score += 1.5
        elif row['breakout_20d'] >= -0.02: score += 0.5
        if row['sector_ratio'] >= 0.3: score += 1
        if score >= 5: return 'MANH'
        if score >= 3: return 'TB'
        return 'YEU'
    rdf['xac_nhan'] = rdf.apply(confirm_label, axis=1)

    rdf = rdf.sort_values('composite', ascending=False).reset_index(drop=True)

    dandat = rdf.head(top_n).copy()
    dandat['rank'] = range(1, len(dandat) + 1)

    rising = rdf[(rdf['composite'] >= 0.5) & (rdf['rank_change'] > 0)].copy()
    rising = rising.sort_values('rank_change', ascending=False).head(top_n).copy()
    rising['rank'] = range(1, len(rising) + 1)

    falling = rdf[rdf['rank_change'] < 0].sort_values('rank_change').head(top_n).copy()
    falling['rank'] = range(1, len(falling) + 1)

    print(f"{'#'*4} {'💪 DẪN DẮT - Top sức mạnh tổng hợp':<60}")
    print(f"{'#':<4} {'Ma':<7} {'Gia':>9} {'RS 1T':>7} {'RS 3T':>7} {'RS 6T':>7} {'Mom20':>7} {'Vol':>6} {'Diem':>6} {'Pha':>12}")
    print("-"*80)
    for _, row in dandat.iterrows():
        phase = phase_label(row['composite'])
        print(f"{row['rank']:<4} {row['symbol']:<7} {row['close']:>8.0f} {row['rs_21d']:>+6.1%} {row['rs_63d']:>+6.1%} {row['rs_126d']:>+6.1%} {row['mom_20d']:>+6.1%} {row['vol_trend']:>+5.1%} {row['composite']:>5.2f} {phase:>12}")

    if not rising.empty:
        print(f"\n{'#'*4} {'🔥 MỚI NỔI - Cải thiện hạng nhanh nhất':<60}")
        print(f"{'#':<4} {'Ma':<7} {'Gia':>9} {'Thay doi':>9} {'RS 1T':>7} {'RS truoc':>8} {'Chuoi RS':>8} {'Vol deu':>7} {'Diem':>6} {'XN':>6}")
        print("-"*75)
        for _, row in rising.iterrows():
            vol_lbl = f"{row['vol_consistency_pct']*100:.0f}%"
            print(f"{row['rank']:<4} {row['symbol']:<7} {row['close']:>8.0f} {int(row['rank_change']):>+8} {row['rs_21d']:>+6.1%} {row['rs_21d_prev']:>+6.1%} {int(row['rs_streak']):>7} {vol_lbl:>6} {row['composite']:>5.2f} {row['xac_nhan']:>6}")

    if not falling.empty:
        print(f"\n{'#'*4} {'❄️ SUY YẾU - Tụt hạng nhiều nhất':<60}")
        print(f"{'#':<4} {'Ma':<7} {'Gia':>9} {'Thay doi':>9} {'RS 1T':>7} {'Chuoi RS':>8} {'Vol deu':>7} {'Diem':>6} {'XN':>6}")
        print("-"*65)
        for _, row in falling.iterrows():
            vol_lbl = f"{row['vol_consistency_pct']*100:.0f}%"
            print(f"{row['rank']:<4} {row['symbol']:<7} {row['close']:>8.0f} {int(row['rank_change']):>+8} {row['rs_21d']:>+6.1%} {int(row['rs_streak']):>7} {vol_lbl:>6} {row['composite']:>5.2f} {row['xac_nhan']:>6}")

    print(f"\n{'='*65}")
    print(f"Tong ma phan tich: {len(rdf)} | Diem TB: {rdf['composite'].mean():.2f}")
    print(f"Dẫn dắt TB: {dandat['composite'].mean():.2f} | Đang lên: {len(rising)} | Đang xuống: {len(falling)}")

    return {
        'rdf': rdf,
        'dandat': dandat,
        'moinoi': rising,
        'suyyeu': falling,
    }


if __name__ == '__main__':
    scan_market()
