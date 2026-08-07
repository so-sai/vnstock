import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# WHY: NGƯỠNG_MIN_SYMBOLS = 1000 đại diện cho "phiên giao dịch hoàn chỉnh".
# VN có ~1500+ mã niêm yết; nếu số mã cập nhật ngày hiện tại < 1000 nghĩa là
# dữ liệu chưa kéo đủ (backfill dang dở), không thể xác nhận EOD đã sẵn sàng.


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

from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

_SESSION_SHOWN = False


def danh_gia_trang_thai_du_lieu(
    ngay_hien_tai: str,
    gio_hien_tai: int,
    n_symbols_today: int,
    co_vnindex_today: bool,
    ngay_du_lieu_vgb10y: str | None,
) -> dict:
    """Đánh giá trạng thái dữ liệu theo thiết kế đa tầng.

    Anchor chính: daily_ohlcv (VNINDEX + >=1000 mã ngày hiện tại).
    Soft check:   VGB10Y (vĩ mô bổ trợ — nếu thiếu vẫn dùng T-1).

    Returns dict {status, note, command}:
      - EOD_READY:   OHLCV đủ (VGB10Y có thể T-1)
      - MARKET_OPEN: trước 16:00, chưa thể xác nhận
      - NOT_READY:   thiếu VNINDEX hoặc < 1000 mã sau 16:00
    """
    if not co_vnindex_today or n_symbols_today < 1000:
        if gio_hien_tai >= 16:
            return {
                "status": "NOT_READY",
                "note": (
                    f"OHLCV hôm nay: {n_symbols_today} mã, "
                    f"VNINDEX={'có' if co_vnindex_today else 'chưa'}. "
                    "Backfill chưa hoàn tất."
                ),
                "command": "python ptck.py daily-update",
            }
        return {
            "status": "MARKET_OPEN",
            "note": "Thị trường chưa đóng cửa — chưa thể xác nhận EOD.",
            "command": "He thong tu dong cap nhat luc 16:00.",
        }

    # OHLCV đủ — kiểm tra soft VGB10Y
    if ngay_du_lieu_vgb10y == ngay_hien_tai:
        note = "Dữ liệu EOD hôm nay đã sẵn sàng (OHLCV + VGB10Y)."
    else:
        note = f"Dữ liệu EOD hôm nay đã sẵn sàng. Macro VGB10Y dùng T-1 ({ngay_du_lieu_vgb10y or 'chưa có'})."
    return {
        "status": "EOD_READY",
        "note": note,
        "command": "python ptck.py flow-map",
    }


def kiem_tra_va_nhac_nho():
    global _SESSION_SHOWN
    if _SESSION_SHOWN:
        return
    _SESSION_SHOWN = True

    bay_gio = datetime.now()
    ngay_hien_tai = bay_gio.strftime("%Y-%m-%d")
    gio_hien_tai = bay_gio.hour

    try:
        with get_connection() as conn:
            dong = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM daily_ohlcv WHERE date = ?",
                (ngay_hien_tai,),
            ).fetchone()
            n_symbols = int(dong[0]) if dong and dong[0] else 0
            dong = conn.execute(
                "SELECT COUNT(*) FROM daily_ohlcv WHERE date = ? AND symbol = 'VNINDEX'",
                (ngay_hien_tai,),
            ).fetchone()
            co_vnindex = bool(dong and dong[0])
            dong = conn.execute("SELECT MAX(date) FROM macro_history WHERE variable = 'VGB10Y'").fetchone()
            ngay_vgb10y = dong[0] if dong else None
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        logger.debug("kiểm_tra_và_nhắc_nhở: không đọc được DB — fallback 0")
        n_symbols = 0
        co_vnindex = False
        ngay_vgb10y = None

    state = danh_gia_trang_thai_du_lieu(
        ngay_hien_tai=ngay_hien_tai,
        gio_hien_tai=gio_hien_tai,
        n_symbols_today=n_symbols,
        co_vnindex_today=co_vnindex,
        ngay_du_lieu_vgb10y=ngay_vgb10y,
    )

    print()
    print("=" * 55)
    print("  HỆ THỐNG SENTINEL — NHẮC NHỞ VẬN HÀNH")
    print("=" * 55)

    if state["status"] == "EOD_READY":
        print("  [+] DU LIEU EOD HOM NAY DA SAN SANG")
    elif state["status"] == "MARKET_OPEN":
        print("  [~] Thi truong chua dong cua.")
    else:
        print("  [!] Chua du du lieu EOD hom nay.")
    print(f"  {state['note']}")
    print(f"  {state['command']}")
    print("=" * 55)
    print()
