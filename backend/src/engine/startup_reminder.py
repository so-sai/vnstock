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

# ── Cổng phủ ĐỘNG cho Sentinel (Mục 3 backlog, GO 2026-10-16) ────────────────────────
# WHY: ngưỡng cứng `n_symbols_today < 1000` là LỖI THỜI — đo 2026-10-15: vét toàn sàn (19 lát,
# cấu hình tốt nhất) chỉ đạt **829 dòng HỢP LỆ** (+4 so với lượt trước) ⇒ 829 là trần thực tế;
# median dòng hợp lệ ≈ 865/phiên ⇒ ngưỡng 1000 fail **92/95 phiên** ⇒ cảnh báo luôn sáng
# (Alarm Fatigue). Nay so với **rolling median 20 phiên trước** × `MIN_VALUE_RATIO` (hệ số 0,5
# theo `panel_quality.assert_market_breadth` — đo 2026-06→10: mức 0,5 chỉ chặn 1/76 phiên, đúng
# phiên 72 dòng thật sự hỏng), với **SÀN 580** để không nới lỏng khi chính median bị hỏng.
MIN_VALUE_RATIO = 0.5
FALLBACK_MIN_SYMBOLS = 580


def nguong_toi_thieu(median_valid: float | None) -> int:
    """Ngưỡng số mã tối thiểu cho một phiên EOD (`max(sàn, median × 0,5)`)."""
    if not median_valid or median_valid <= 0:
        return FALLBACK_MIN_SYMBOLS
    return max(FALLBACK_MIN_SYMBOLS, int(median_valid * MIN_VALUE_RATIO))


def danh_gia_trang_thai_du_lieu(
    ngay_hien_tai: str,
    gio_hien_tai: int,
    n_symbols_today: int,
    co_vnindex_today: bool,
    ngay_du_lieu_vgb10y: str | None,
    median_valid_20: float | None = None,
) -> dict:
    """Đánh giá trạng thái dữ liệu theo thiết kế đa tầng.

    Anchor chính: daily_ohlcv (VNINDEX + đủ mã HỢP LỆ ngày hiện tại).
    Soft check:   VGB10Y (vĩ mô bổ trợ — nếu thiếu vẫn dùng T-1).

    `median_valid_20`: median số mã HỢP LỆ của 20 phiên **trước** phiên hiện tại (PIT-safe baseline).
    Thiếu tham số ⇒ dùng sàn `FALLBACK_MIN_SYMBOLS` (fail-closed về phía an toàn, không nới).

    Returns dict {status, note, command}:
      - EOD_READY:   OHLCV đủ (VGB10Y có thể T-1)
      - MARKET_OPEN: trước 16:00, chưa thể xác nhận
      - NOT_READY:   thiếu VNINDEX hoặc < ngưỡng động sau 16:00
    """
    nguong = nguong_toi_thieu(median_valid_20)
    if not co_vnindex_today or n_symbols_today < nguong:
        if gio_hien_tai >= 16:
            return {
                "status": "NOT_READY",
                "note": (
                    f"OHLCV hôm nay: {n_symbols_today} mã hợp lệ, "
                    f"VNINDEX={'có' if co_vnindex_today else 'chưa'} "
                    f"(ngưỡng {nguong} = max({FALLBACK_MIN_SYMBOLS}, median20×{MIN_VALUE_RATIO})). "
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
            # ĐẾM TRÊN DÒNG HỢP LỆ (không phải mọi dòng) + loại chỉ số — cùng vị từ với
            # `panel_quality`/`market_wide_breadth`; đếm thô sẽ tính cả dòng cách ly/chỉ số.
            from src.database.data_validity import STOCK_ONLY_SQL, VALID_ROW_SQL

            dong = conn.execute(
                f"SELECT COUNT(*) FROM daily_ohlcv WHERE date = ? AND {VALID_ROW_SQL} AND {STOCK_ONLY_SQL}",
                (ngay_hien_tai,),
            ).fetchone()
            n_symbols = int(dong[0]) if dong and dong[0] else 0
            # Baseline PIT-safe: median 20 PHIÊN TRƯỚC (không gồm phiên đang xét).
            rows = conn.execute(
                f"SELECT date, COUNT(*) FROM daily_ohlcv WHERE date < ? AND {VALID_ROW_SQL} AND {STOCK_ONLY_SQL} "
                "GROUP BY date ORDER BY date DESC LIMIT 20",
                (ngay_hien_tai,),
            ).fetchall()
            counts = sorted(int(r[1]) for r in rows)
            if not counts:
                median_valid = None
            elif len(counts) % 2:
                median_valid = float(counts[len(counts) // 2])
            else:
                median_valid = (counts[len(counts) // 2 - 1] + counts[len(counts) // 2]) / 2.0
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
        median_valid = None

    state = danh_gia_trang_thai_du_lieu(
        ngay_hien_tai=ngay_hien_tai,
        gio_hien_tai=gio_hien_tai,
        n_symbols_today=n_symbols,
        co_vnindex_today=co_vnindex,
        ngay_du_lieu_vgb10y=ngay_vgb10y,
        median_valid_20=median_valid,
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
