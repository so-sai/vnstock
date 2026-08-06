"""test_startup_reminder.py — TDD: multi-tier EOD readiness check.

Rule: daily_ohlcv là ANCHOR chính (VNINDEX + >=1000 mã). VGB10Y chỉ là
soft check (dữ liệu vĩ mô bổ trợ, có thể dùng T-1).
"""

from src.engine.startup_reminder import danh_gia_trang_thai_du_lieu


# ============================================================
# Case: dữ liệu OHLCV đầy đủ
# ============================================================
class TestOhlcvAnchor:
    def test_du_1000_ma_va_vnindex(self):
        """daily_ohlcv có VNINDEX + >=1000 mã → EOD_READY."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=1515,
            co_vnindex_today=True,
            ngay_du_lieu_vgb10y="2026-08-06",
        )
        assert state["status"] == "EOD_READY"

    def test_du_ohlcv_nhung_vgb10y_t1(self):
        """OHLCV đủ + VGB10Y dùng T-1 → vẫn EOD_READY kèm ghi chú soft."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=1515,
            co_vnindex_today=True,
            ngay_du_lieu_vgb10y="2026-08-05",
        )
        assert state["status"] == "EOD_READY"
        assert "T-1" in state.get("note", "")

    def test_du_ohlcv_nhung_vgb10y_thieu_hoan_toan(self):
        """VGB10Y chưa từng có → vẫn EOD_READY, ghi chú rõ 'dùng T-1'."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=1515,
            co_vnindex_today=True,
            ngay_du_lieu_vgb10y=None,
        )
        assert state["status"] == "EOD_READY"
        assert "T-1" in state.get("note", "")


# ============================================================
# Case: dữ liệu OHLCV chưa đủ
# ============================================================
class TestOhlcvIncomplete:
    def test_chua_co_vnindex(self):
        """Chưa có VNINDEX hôm nay → NOT_READY (dù có nhiều mã)."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=1200,
            co_vnindex_today=False,
            ngay_du_lieu_vgb10y="2026-08-06",
        )
        assert state["status"] == "NOT_READY"

    def test_duoi_1000_ma(self):
        """Chỉ có 800 mã → NOT_READY (chưa đạt ngưỡng 1000)."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=800,
            co_vnindex_today=True,
            ngay_du_lieu_vgb10y="2026-08-06",
        )
        assert state["status"] == "NOT_READY"

    def test_duoi_1000_ma_truoc_16h(self):
        """Trước 16:00 chưa đủ mã → MARKET_OPEN (không phải cảnh báo giả)."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=14,
            n_symbols_today=800,
            co_vnindex_today=True,
            ngay_du_lieu_vgb10y="2026-08-05",
        )
        assert state["status"] == "MARKET_OPEN"


# ============================================================
# Case: biên giới
# ============================================================
class TestBoundary:
    def test_du_dung_1000_ma(self):
        """Đúng ngưỡng 1000 mã → EOD_READY."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=1000,
            co_vnindex_today=True,
            ngay_du_lieu_vgb10y="2026-08-06",
        )
        assert state["status"] == "EOD_READY"

    def test_0_ma(self):
        """0 mã hôm nay → NOT_READY (không có dữ liệu)."""
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=0,
            co_vnindex_today=False,
            ngay_du_lieu_vgb10y="2026-08-05",
        )
        assert state["status"] == "NOT_READY"


# ============================================================
# Mệnh lệnh được khuyến nghị trong mỗi trạng thái
# ============================================================
class TestCommandHints:
    def test_eod_ready_command(self):
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=1515,
            co_vnindex_today=True,
            ngay_du_lieu_vgb10y="2026-08-06",
        )
        assert "flow-map" in state.get("command", "")

    def test_not_ready_command(self):
        state = danh_gia_trang_thai_du_lieu(
            ngay_hien_tai="2026-08-06",
            gio_hien_tai=17,
            n_symbols_today=100,
            co_vnindex_today=False,
            ngay_du_lieu_vgb10y="2026-08-05",
        )
        assert "daily-update" in state.get("command", "")
