import sys
import os
from pathlib import Path

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
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
import pandas as pd

class Deduplicator:
    """
    Xử lý trung hòa và loại bỏ trùng lặp dữ liệu từ nhiều nguồn (VCI, KBS).
    """
    
    @staticmethod
    def merge_ohlcv(df_vci, df_kbs):
        if df_vci.empty: return df_kbs
        if df_kbs.empty: return df_vci
        merged = pd.merge(df_vci, df_kbs, on=['symbol', 'date'], suffixes=('_vci', '_kbs'))
        final_df = pd.DataFrame()
        final_df['symbol'] = merged['symbol']
        final_df['date'] = merged['date']
        final_df['open'] = merged['open_kbs']
        final_df['high'] = merged['high_kbs']
        final_df['low'] = merged['low_kbs']
        final_df['close'] = merged['close_kbs']
        final_df['adj_close'] = merged['adj_close_vci']
        final_df['volume'] = merged['volume_kbs']
        final_df['source'] = 'HYBRID'
        return final_df

    @staticmethod
    def clean_industry_data(df):
        if df.empty: return df
        return df.sort_values('icb_name4', na_position='last').drop_duplicates('symbol', keep='first')

