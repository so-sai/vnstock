import logging
import sys
from datetime import datetime
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
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path


PROJECT_ROOT = _hydrate_path()
if sys.platform == "win32" and getattr(sys.stdout, "encoding", "") != "utf-8":
    import io

    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except OSError, AttributeError, ValueError:
                logging.getLogger(__name__).debug("stdout.reconfigure(utf-8) không khả dụng — giữ nguyên encoding")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import pandas as pd

from src.database.data_validity import VALID_ROW_SQL
from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

TRU_COT = ["Ngân hàng", "Bất động sản", "Tài nguyên Cơ bản"]


def _missing_tru(ly_do: str) -> dict:
    """Kết quả "KHÔNG ĐO ĐƯỢC" cho một trụ: mọi số đo là None, KHÔNG bịa 0/TRUNG_TINH.

    [WHY] Đây là module BẢO VỆ (ra lệnh hạ tỷ trọng) — thiếu dữ liệu phải hiện ra thành
    "không xác định". Nếu nó đội lốt yên bình (ratio 0 + TRUNG_TINH) thì mất dữ liệu = mất lưới.
    """
    return {
        "du_lieu": "THIEU",
        "n_bars": 0,
        "volume_today": None,
        "volume_ma20": None,
        "ratio": None,
        "price_direction": "KHONG_XAC_DINH",
        "range_position": None,
        "close_vs_open": None,
        "ly_do": ly_do,
    }


def _get_tru_stats(target_date: str, industry: str) -> dict:
    """Lấy volume + price action cho 1 ngành — CHỈ trên bar qua được lưới vị từ.

    Trả về: {du_lieu, n_bars, volume_today, volume_ma20, ratio, price_direction, range_position, close_vs_open}
      - du_lieu: "DU" | "THIEU" — THIEU ⇒ mọi số đo là None (không được thay bằng 0)
      - price_direction: "TANG"|"GIAM"|"TRUNG_TINH"|"KHONG_XAC_DINH"
      - range_position: 0-1 (vị trí đóng cửa trong biên độ ngày), None khi không xác định

    [WHY] `is_stale = 0 AND VALID_ROW_SQL` là BẮT BUỘC: store có 41.998 dòng `is_stale = 1`
    (trong đó 3.337 dòng vẫn "hợp lệ" về hình dạng — lệch thang giá) và 1 dòng `is_stale = 0`
    vi phạm vị từ. Thiếu MỘT trong hai là hở lưới, mà lưới này trực tiếp ra lệnh thoát vị thế.
    """
    with get_connection() as conn:
        symbols = pd.read_sql("SELECT symbol FROM symbol_industry WHERE icb_name2=?", conn, params=(industry,))[
            "symbol"
        ].tolist()
        if not symbols:
            logger.warning("[FAST-EXIT] MISSING_TRU_DATA: ngành %s không có mã trong symbol_industry", industry)
            return _missing_tru("ngành không có mã trong symbol_industry")
        placeholders = ",".join("?" for _ in symbols)
        data_today = pd.read_sql(
            f"SELECT SUM(volume) as vol, AVG(close) as avg_close, "
            f"AVG(high) as avg_high, AVG(low) as avg_low, AVG(open) as avg_open, COUNT(*) as n_bars "
            f"FROM daily_ohlcv WHERE date=? AND is_stale = 0 AND {VALID_ROW_SQL} "
            f"AND symbol IN ({placeholders})",
            conn,
            params=(target_date, *symbols),
        )
        vol_hist = pd.read_sql(
            f"SELECT date, SUM(volume) as total FROM daily_ohlcv "
            f"WHERE date<? AND date>=date(?, '-27 days') AND is_stale = 0 AND {VALID_ROW_SQL} "
            f"AND symbol IN ({placeholders}) "
            f"GROUP BY date ORDER BY date",
            conn,
            params=(target_date, target_date, *symbols),
        )

    row = data_today.iloc[0]
    n_bars = int(row["n_bars"] or 0)
    if n_bars == 0 or row["vol"] is None or row["avg_close"] is None or row["avg_open"] is None:
        logger.warning(
            "[FAST-EXIT] MISSING_TRU_DATA: ngành %s không có bar HỢP LỆ ngày %s (is_stale=0 + VALID_ROW_SQL)",
            industry,
            target_date,
        )
        return _missing_tru("không có bar hợp lệ cho ngày đo")

    today_vol = float(row["vol"])
    avg_open = float(row["avg_open"])
    avg_high = float(row["avg_high"])
    avg_low = float(row["avg_low"])
    avg_close = float(row["avg_close"])

    # Nền MA20: thiếu lịch sử ⇒ ratio KHÔNG đo được (None), không được gọi là "không có spike".
    ma20 = float(vol_hist["total"].tail(20).mean()) if len(vol_hist) >= 5 else None
    vol_ratio = round(today_vol / ma20, 2) if ma20 and ma20 > 0 else None

    if avg_high > avg_low:
        range_pos = (avg_close - avg_low) / (avg_high - avg_low)
    else:
        range_pos = None  # biên độ 0 ⇒ vị trí trong biên KHÔNG xác định (không phải 0,5)

    if range_pos is None:
        direction = "KHONG_XAC_DINH"
    elif avg_close > avg_open and range_pos > 0.6:
        direction = "TANG"
    elif avg_close < avg_open and range_pos < 0.4:
        direction = "GIAM"
    else:
        direction = "TRUNG_TINH"

    return {
        "du_lieu": "DU",
        "n_bars": n_bars,
        "volume_today": today_vol,
        "volume_ma20": ma20,
        "ratio": vol_ratio,
        "price_direction": direction,
        "range_position": round(range_pos, 2) if range_pos is not None else None,
        "close_vs_open": "CLOSE_CAO_HON" if avg_close >= avg_open else "CLOSE_THAP_HON",
    }


def kiem_tra_phan_phoi(target_date: str | None = None) -> dict:
    """Phát hiện phân phối lớn đột ngột qua volume spike + price action.

    Nguyên tắc:
      - Volume spike (ratio > 2.0) + GIÁ GIẢM = PHÂN PHỐI (xả hàng)
      - Volume spike (ratio > 2.0) + GIÁ TĂNG = Smart Money Inflow (tích lũy)
      - Chỉ đánh dấu co_phan_phoi khi volume spike + price action xấu

    Trả về: {
        "co_phan_phoi": bool,
        "muc_do": "CAO"|"TRUNG_BINH"|"THAP"|"KHONG"|"KHONG_XAC_DINH",
        "tru_nguy_hiem": [tên các trụ có spike + bearish],
        "tru_khong_ket_luan": [tên trụ KHÔNG đo được — thiếu dữ liệu, KHÔNG phải yên bình],
        "du_lieu_thieu": bool (có ít nhất 1 trụ không đo được),
        "chi_tiet": {tên_trụ: {...}},
    }
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    tru_nguy_hiem = []
    tru_khong_ket_luan = []
    chi_tiet = {}
    tong_diem = 0

    for ten_tru in TRU_COT:
        stats = _get_tru_stats(target_date, ten_tru)
        chi_tiet[ten_tru] = stats
        r = stats.get("ratio")
        direction = stats.get("price_direction")

        # KHÔNG chấm điểm trên dữ liệu không đo được (thiếu bar / thiếu nền MA20 / biên độ 0):
        # một lưới bảo vệ im lặng vì thiếu dữ liệu là một lưới đã hỏng.
        if stats.get("du_lieu") == "THIEU" or r is None or direction == "KHONG_XAC_DINH":
            tru_khong_ket_luan.append(ten_tru)
            continue

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
    elif tru_khong_ket_luan:
        # [INVARIANT] Chưa đo được gì ⇒ KHÔNG được tuyên bố "yên bình" (KHONG).
        muc_do = "KHONG_XAC_DINH"
    else:
        muc_do = "KHONG"

    if tru_khong_ket_luan:
        logger.warning(
            "[FAST-EXIT] MISSING_TRU_DATA: %s — không kết luận được phân phối (muc_do=%s). "
            "Thiếu dữ liệu KHÔNG phải là tín hiệu an toàn.",
            ", ".join(tru_khong_ket_luan),
            muc_do,
        )

    return {
        "co_phan_phoi": len(tru_nguy_hiem) > 0 or muc_do in ("CAO", "TRUNG_BINH"),
        "muc_do": muc_do,
        "tru_nguy_hiem": tru_nguy_hiem,
        "tru_khong_ket_luan": tru_khong_ket_luan,
        "du_lieu_thieu": bool(tru_khong_ket_luan),
        "chi_tiet": chi_tiet,
    }
