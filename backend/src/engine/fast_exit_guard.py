import sys
from datetime import datetime
from pathlib import Path
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

import pandas as pd

from src.database.db_core import get_connection

TRU_COT = ["Ngân hàng", "Bất động sản", "Tài nguyên Cơ bản"]


def _get_tru_stats(target_date: str, industry: str) -> dict:
    """Lấy volume + price action cho 1 ngành.
    
    Trả về: {volume_today, volume_ma20, ratio, price_direction, range_position}
      - price_direction: "TANG"|"GIAM"|"TRUNG_TINH"
      - range_position: 0-1 (vị trí đóng cửa trong biên độ ngày)
    """
    with get_connection() as conn:
        symbols = pd.read_sql(
            "SELECT symbol FROM symbol_industry WHERE icb_name2=?",
            conn, params=(industry,)
        )["symbol"].tolist()
        if not symbols:
            return {"volume_today": 0, "volume_ma20": 0, "ratio": 0,
                    "price_direction": "TRUNG_TINH", "range_position": 0.5}
        placeholders = ",".join("?" for _ in symbols)
        data_today = pd.read_sql(
            f"SELECT SUM(volume) as vol, AVG(close) as avg_close, "
            f"AVG(high) as avg_high, AVG(low) as avg_low, AVG(open) as avg_open "
            f"FROM daily_ohlcv WHERE date=? AND symbol IN ({placeholders})",
            conn, params=(target_date, *symbols)
        )
        vol_hist = pd.read_sql(
            f"SELECT date, SUM(volume) as total FROM daily_ohlcv "
            f"WHERE date<? AND date>=date(?, '-27 days') AND symbol IN ({placeholders}) "
            f"GROUP BY date ORDER BY date",
            conn, params=(target_date, target_date, *symbols)
        )
    row = data_today.iloc[0]
    today_vol = float(row['vol']) if row['vol'] else 0
    ma20 = float(vol_hist['total'].tail(20).mean()) if len(vol_hist) >= 5 else 0
    vol_ratio = (today_vol / ma20) if ma20 > 0 else 0

    avg_open = float(row['avg_open']) if row['avg_open'] else 0
    avg_high = float(row['avg_high']) if row['avg_high'] else 0
    avg_low = float(row['avg_low']) if row['avg_low'] else 0
    avg_close = float(row['avg_close']) if row['avg_close'] else 0

    if avg_high > avg_low:
        range_pos = (avg_close - avg_low) / (avg_high - avg_low)
    else:
        range_pos = 0.5

    if avg_close > avg_open and range_pos > 0.6:
        direction = "TANG"
    elif avg_close < avg_open and range_pos < 0.4:
        direction = "GIAM"
    else:
        direction = "TRUNG_TINH"

    return {
        "volume_today": today_vol, "volume_ma20": ma20, "ratio": round(vol_ratio, 2),
        "price_direction": direction, "range_position": round(range_pos, 2),
        "close_vs_open": "CLOSE_CAO_HON" if avg_close >= avg_open else "CLOSE_THAP_HON",
    }


def kiem_tra_phan_phoi(target_date: Optional[str] = None) -> dict:
    """Phát hiện phân phối lớn đột ngột qua volume spike + price action.
    
    Nguyên tắc:
      - Volume spike (ratio > 2.0) + GIÁ GIẢM = PHÂN PHỐI (xả hàng)
      - Volume spike (ratio > 2.0) + GIÁ TĂNG = Smart Money Inflow (tích lũy)
      - Chỉ đánh dấu co_phan_phoi khi volume spike + price action xấu
    
    Trả về: {
        "co_phan_phoi": bool,
        "muc_do": "CAO"|"TRUNG_BINH"|"THAP"|"KHONG",
        "tru_nguy_hiem": [tên các trụ có spike + bearish],
        "chi_tiet": {tên_trụ: {...}},
    }
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    tru_nguy_hiem = []
    chi_tiet = {}
    tong_diem = 0

    for ten_tru in TRU_COT:
        stats = _get_tru_stats(target_date, ten_tru)
        chi_tiet[ten_tru] = stats
        r = stats["ratio"]
        direction = stats["price_direction"]

        # Volume spike + GIÁ GIẢM = distribution (xả hàng)
        if r > 2.0 and direction == "GIAM":
            tru_nguy_hiem.append(ten_tru)
            tong_diem += 3 if r > 3.0 else 2
        # Volume spike + GIÁ TRUNG TÍNH = cảnh báo nhẹ
        elif r > 2.0 and direction == "TRUNG_TINH":
            tong_diem += 1
        # Volume spike + GIÁ TĂNG = inflow — không phạt

    if tong_diem >= 5:
        muc_do = "CAO"
    elif tong_diem >= 3:
        muc_do = "TRUNG_BINH"
    elif tong_diem >= 1:
        muc_do = "THAP"
    else:
        muc_do = "KHONG"

    return {
        "co_phan_phoi": len(tru_nguy_hiem) > 0 or muc_do in ("CAO", "TRUNG_BINH"),
        "muc_do": muc_do,
        "tru_nguy_hiem": tru_nguy_hiem,
        "chi_tiet": chi_tiet,
    }
