"""Cổng phủ ĐỘNG cho Sentinel (Mục 3 backlog, GO 2026-10-16).

WHY có file này: ngưỡng cứng `n_symbols_today < 1000` fail **92/95** phiên (median dòng hợp lệ
≈ 865; trần thực tế đo được của một lượt vét toàn sàn là **829** dòng/phiên) ⇒ cảnh báo luôn sáng
⇒ Alarm Fatigue. Nay ngưỡng = `max(580, median20 phiên trước × 0,5)`.
ĐIỀU KIỆN KHÔNG ĐƯỢC PHÁ: nhánh FAIL phải còn (phiên thiếu thật vẫn báo NOT_READY) — nới ngưỡng
quá tay sẽ che mất dữ liệu lỗi.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.engine.startup_reminder import (
    FALLBACK_MIN_SYMBOLS,
    danh_gia_trang_thai_du_lieu,
    nguong_toi_thieu,
)


def _goi(n_symbols: int, median: float | None, co_vnindex: bool = True, gio: int = 18) -> dict:
    return danh_gia_trang_thai_du_lieu(
        ngay_hien_tai="2026-10-16",
        gio_hien_tai=gio,
        n_symbols_today=n_symbols,
        co_vnindex_today=co_vnindex,
        ngay_du_lieu_vgb10y="2026-10-16",
        median_valid_20=median,
    )


def test_nguong_dong_co_san_va_khong_noi_long():
    """Sàn 580 luôn giữ: median nhỏ KHÔNG được kéo ngưỡng xuống dưới sàn."""
    assert nguong_toi_thieu(None) == FALLBACK_MIN_SYMBOLS
    assert nguong_toi_thieu(0) == FALLBACK_MIN_SYMBOLS
    assert nguong_toi_thieu(865) == FALLBACK_MIN_SYMBOLS  # 0,5×865 = 432 < 580 ⇒ sàn thắng
    assert nguong_toi_thieu(1200) == 600  # phủ cải thiện ⇒ ngưỡng tự siết
    assert nguong_toi_thieu(3000) == 1500


def test_phien_15_10_va_16_10_khong_con_bao_dong_gia():
    """DO-WHEN của Mục 3: 15/10 = 827 mã và 16/10 = 870 mã phải qua cổng (median ~865)."""
    assert _goi(827, 865.0)["status"] == "EOD_READY"
    assert _goi(870, 865.0)["status"] == "EOD_READY"


def test_phien_thieu_that_van_bao_NOT_READY():
    """Nhánh FAIL phải còn: 300 mã (< sàn 580) ⇒ NOT_READY, có nêu ngưỡng để truy vết."""
    st = _goi(300, 865.0)
    assert st["status"] == "NOT_READY"
    assert "580" in st["note"]


def test_median_cao_thi_nguong_tu_siet():
    """Khi nền phủ đã cao (median 1.500) ⇒ ngưỡng 750 ⇒ phiên 700 mã phải bị bắt."""
    assert _goi(700, 1500.0)["status"] == "NOT_READY"
    assert _goi(760, 1500.0)["status"] == "EOD_READY"


def test_thieu_vnindex_van_NOT_READY():
    assert _goi(870, 865.0, co_vnindex=False)["status"] == "NOT_READY"


def test_truoc_16h_thi_MARKET_OPEN():
    assert _goi(300, 865.0, gio=10)["status"] == "MARKET_OPEN"


def test_thieu_baseline_thi_dung_san_580():
    """Không có median (DB mới/ lỗi đọc) ⇒ dùng sàn, KHÔNG được coi là đủ."""
    assert _goi(500, None)["status"] == "NOT_READY"
    assert _goi(600, None)["status"] == "EOD_READY"
