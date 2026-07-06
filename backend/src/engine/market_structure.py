import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent
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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import numpy as np
import pandas as pd

import src.config
from src.database.data_integrity import ensure_vnindex_integrity
from src.database.db_core import get_connection


def analyse_market_structure(lookback=60, top_n=10, min_value=1e9, verbose=True,
                              target_date=None):
    if target_date:
        ref_date = target_date
    else:
        from datetime import datetime as _dt
        ref_date = _dt.now().strftime('%Y-%m-%d')

    ensure_vnindex_integrity(verbose=False)

    with get_connection() as conn:
        df = pd.read_sql(
            f"SELECT symbol, date, close, volume FROM daily_ohlcv "
            f"WHERE symbol != 'VNINDEX' AND date <= '{ref_date}' ORDER BY symbol, date",
            conn,
        )
        df_idx = pd.read_sql(
            f"SELECT date, close as idx_close FROM daily_ohlcv "
            f"WHERE symbol='VNINDEX' AND close > 100 AND date <= '{ref_date}' ORDER BY date",
            conn,
        )

    if df.empty or df_idx.empty:
        if verbose:
            print("Khong du du lieu de phan tich cau truc thi truong.")
        return None

    df['date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce').dt.normalize()
    df_idx['date'] = pd.to_datetime(df_idx['date'], format='mixed', errors='coerce').dt.normalize()
    df = df.dropna(subset=['date']).copy()
    df_idx = df_idx.dropna(subset=['date']).copy()
    df = df[df['close'] > 0]
    df_idx = df_idx[df_idx['idx_close'] > 100]

    ref_dt = pd.to_datetime(ref_date)
    cutoff = ref_dt - pd.Timedelta(days=lookback)
    df = df[df['date'] >= cutoff].copy()

    daily = df.groupby(['symbol', 'date'], as_index=False).agg(
        close=('close', 'last'), volume=('volume', 'last')
    )
    daily = daily[daily['close'] > 0].sort_values(['symbol', 'date']).reset_index(drop=True)
    daily.insert(3, 'traded_value', daily['close'] * daily['volume'])

    # Weight proxy: avg traded value over last 20 days
    recent = daily.loc[daily['date'] >= ref_dt - pd.Timedelta(days=20)]
    avg_value = recent.groupby('symbol', as_index=False)['traded_value'].mean()
    avg_value.columns = ['symbol', 'avg_value']
    avg_value = avg_value.loc[avg_value['avg_value'] >= min_value]
    total_avg_value = avg_value['avg_value'].sum()
    avg_value.insert(2, 'weight', avg_value['avg_value'] / total_avg_value)

    # Top N by weight
    top_symbols = avg_value.nlargest(top_n, 'weight')['symbol'].tolist()

    daily = daily.merge(avg_value[['symbol', 'weight']], on='symbol', how='inner')
    daily = daily.sort_values(['symbol', 'date']).reset_index(drop=True)
    daily.insert(5, 'return', daily.groupby('symbol')['close'].transform(lambda x: x.pct_change()))
    daily['return'] = daily['return'].fillna(0.0).replace([np.inf, -np.inf], 0.0).clip(-0.5, 0.5)

    # Merge VNINDEX
    df_idx = df_idx.sort_values('date')
    df_idx['idx_return'] = df_idx['idx_close'].pct_change().replace([np.inf, -np.inf], 0.0)
    merged = daily.merge(df_idx[['date', 'idx_close', 'idx_return']], on='date', how='inner')

    # --- Compute indices ---
    dates = merged['date'].unique()
    date_daily = merged.groupby('date')

    # VNINDEX return
    idx_daily = df_idx[df_idx['date'].isin(dates)].copy()
    vnindex_ret = idx_daily['idx_return'].values
    vnindex_total = (1 + vnindex_ret).prod() - 1

    # SBMI: cap-weighted, ex-top10
    ex_top = merged[~merged['symbol'].isin(top_symbols)]
    sbmi_daily = ex_top.groupby('date').apply(
        lambda g: (g['return'] * g['weight']).sum() / g['weight'].sum()
        if g['weight'].sum() > 0 else 0.0,
        include_groups=False,
    ).reset_index(name='sbmi_ret')
    sbmi_ret = sbmi_daily['sbmi_ret'].values
    sbmi_total = (1 + sbmi_ret).prod() - 1

    # EWMI: equal weight (simple mean)
    ewmi_daily = merged.groupby('date')['return'].mean().reset_index(name='ewmi_ret')
    ewmi_ret = ewmi_daily['ewmi_ret'].values
    ewmi_total = (1 + ewmi_ret).prod() - 1

    # BDI: Breadth Divergence Index = cumulative VNINDEX - cumulative EWMI
    cumulative_vn = (1 + idx_daily['idx_return'].values).cumprod()
    cumulative_ew = (1 + ewmi_ret).cumprod()
    bdi = cumulative_vn / cumulative_ew - 1
    bdi_latest = bdi[-1]

    # BDI signal
    if bdi_latest > 0.05:
        bdi_signal = "PHAN_KY_DUONG"  # big caps overperforming
    elif bdi_latest < -0.05:
        bdi_signal = "PHAN_KY_AM"     # small caps overperforming
    else:
        bdi_signal = "CAN_BANG"

    # LCR: Leadership Concentration
    top10_weight = avg_value[avg_value['symbol'].isin(top_symbols)]['weight'].sum()
    lcr = top10_weight * 100

    if verbose:
        print(f"  VNINDEX chinh thuc: {vnindex_total*100:+.1f}%")
        print(f"  SBMI (ex-top{top_n}):    {sbmi_total*100:+.1f}%")
        print(f"  EWMI (binh quan):        {ewmi_total*100:+.1f}%")
        print(f"  BDI (phan ky):           {bdi_latest*100:+.1f}% ({bdi_signal})")
        print(f"  LCR (tap trung):         {lcr:.1f}%")
        print(f"  Top {top_n}: {', '.join(top_symbols)}")

    result = {
        "vnindex_pct": round(vnindex_total * 100, 2),
        "sbmi_pct": round(sbmi_total * 100, 2),
        "ewmi_pct": round(ewmi_total * 100, 2),
        "bdi_pct": round(bdi_latest * 100, 2),
        "bdi_signal": bdi_signal,
        "lcr_pct": round(lcr, 1),
        "top_n": top_symbols,
        "data_points": len(dates),
    }

    out_path = Path(src.config.DATA_DIR) / "output" / "market_structure.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        import json
        json.dump(result, f, indent=2, ensure_ascii=False)

    return result


if __name__ == "__main__":
    result = analyse_market_structure(lookback=60, top_n=10)
