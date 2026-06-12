import sys, os
from pathlib import Path
from datetime import datetime
from typing import Optional


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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import src.config
import pandas as pd
import numpy as np
from src.database.db_core import get_connection


TRU_COT = ["Ngân hàng", "Bất động sản", "Tài nguyên Cơ bản"]


def _get_volume_stats(target_date: str, industry: str) -> dict:
    """Lấy volume hôm nay và MA20 volume cho 1 ngành."""
    with get_connection() as conn:
        symbols = pd.read_sql(
            "SELECT symbol FROM symbol_industry WHERE icb_name2=?",
            conn, params=(industry,)
        )["symbol"].tolist()
        if not symbols:
            return {"volume_today": 0, "volume_ma20": 0, "ratio": 0}
        placeholders = ",".join("?" for _ in symbols)
        vol_today = pd.read_sql(
            f"SELECT SUM(volume) as total FROM daily_ohlcv "
            f"WHERE date=? AND symbol IN ({placeholders})",
            conn, params=(target_date, *symbols)
        )
        vol_hist = pd.read_sql(
            f"SELECT date, SUM(volume) as total FROM daily_ohlcv "
            f"WHERE date<? AND date>=date(?, '-27 days') AND symbol IN ({placeholders}) "
            f"GROUP BY date ORDER BY date",
            conn, params=(target_date, target_date, *symbols)
        )
    today = float(vol_today.iloc[0]['total']) if not vol_today.empty and vol_today.iloc[0]['total'] else 0
    ma20 = float(vol_hist['total'].tail(20).mean()) if len(vol_hist) >= 5 else 0
    ratio = (today / ma20) if ma20 > 0 else 0
    return {"volume_today": today, "volume_ma20": ma20, "ratio": round(ratio, 2)}


def kiem_tra_phan_phoi(target_date: Optional[str] = None) -> dict:
    """Phát hiện phân phối lớn đột ngột qua volume spike ở các trụ cột.
    
    Trả về: {
        "co_phan_phoi": bool,
        "muc_do": "CAO"|"TRUNG_BINH"|"THAP"|"KHONG",
        "trụ_nguy_hiem": [tên các trụ có spike],
        "chi_tiet": {tên_trụ: {volume_today, volume_ma20, ratio}},
    }
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    
    tru_khong_on = []
    chi_tiet = {}
    tong_diem = 0
    
    for ten_tru in TRU_COT:
        stats = _get_volume_stats(target_date, ten_tru)
        chi_tiet[ten_tru] = stats
        r = stats["ratio"]
        if r > 3.0:
            tru_khong_on.append(ten_tru)
            tong_diem += 3
        elif r > 2.0:
            tru_khong_on.append(ten_tru)
            tong_diem += 2
        elif r > 1.5:
            tong_diem += 1
    
    if tong_diem >= 5:
        muc_do = "CAO"
    elif tong_diem >= 3:
        muc_do = "TRUNG_BINH"
    elif tong_diem >= 1:
        muc_do = "THAP"
    else:
        muc_do = "KHONG"
    
    return {
        "co_phan_phoi": len(tru_khong_on) > 0 or muc_do == "CAO",
        "muc_do": muc_do,
        "tru_nguy_hiem": tru_khong_on,
        "chi_tiet": chi_tiet,
    }
