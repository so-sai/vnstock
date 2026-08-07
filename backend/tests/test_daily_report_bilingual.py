"""Tests — daily_market_report bilingual renderer.

1. _bi() mode contract: full=VI, compact=EN, annotated/auto="VI (EN)"
2. in_bao_cao() renders bilingual headers/labels by default
"""

import sys
from pathlib import Path

# ── Path Setup ──────────────────────────────────────────────────
PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))


from src.services.daily_market_report import _bi, in_bao_cao


class TestBi:
    def test_annotated_bilingual_vi_en(self):
        assert _bi("tăng", "annotated") == "tăng (rising)"

    def test_full_vi_only(self):
        assert _bi("tăng", "full") == "tăng"

    def test_compact_en_only(self):
        assert _bi("tăng", "compact") == "rising"

    def test_auto_resolves_to_annotated(self):
        assert _bi("Xu hướng chung", "auto") == "Xu hướng chung (Overall trend)"

    def test_unknown_label_passthrough(self):
        assert _bi("CỤM_LẠ_PHÁT_MINH", "annotated") == "CỤM_LẠ_PHÁT_MINH"

    def test_section_title_bilingual(self):
        assert _bi("TỔNG QUAN THỊ TRƯỜNG", "annotated") == "TỔNG QUAN THỊ TRƯỜNG (MARKET OVERVIEW)"

    def test_behavior_conclusion_bilingual(self):
        assert _bi("Nên đứng ngoài", "annotated") == "Nên đứng ngoài (Stay out)"


class TestInBaoCao:
    def _report(self):
        return {
            "title": "BÁO CÁO THỊ TRƯỜNG HẰNG NGÀY",
            "tong_quan": {
                "xu_huong_chung": "tăng",
                "muc_do_ro_xu_huong": "mạnh",
                "tam_ly_thi_truong": "tích cực",
                "diem_regime": 0.6,
                "ket_luan": "Thị trường hôm nay đang ở trạng thái: tăng",
            },
            "dong_tien": {
                "nhom_manh_nhat": ["OIL"],
                "nhom_yeu_nhat": [],
                "tap_trung_vai_nhom": "Có",
                "trang_thai_dong_tien": "MỞ_RỘNG",
                "ket_luan": "Dòng tiền đang tập trung vào: OIL",
            },
            "rui_ro": {
                "do_rong_thi_truong": "tốt",
                "dong_tien_lan_toa": "hẹp",
                "bien_dong_gia": "thấp",
                "xep_loai": "thấp",
                "diem_suc_khoe_do_rong": 99.0,
                "ket_luan": "Rủi ro hiện tại: thấp",
            },
            "tin_hieu_dac_biet": [],
            "ket_luan_hanh_vi": "Có thể tham gia",
            "canh_bao_som": {"cap_do_tieng_viet": "Bình thường", "canh_bao": []},
            "xac_nhan_chuyen_pha": {
                "ky_hieu": "🟢",
                "ten": "Nhiễu",
                "so_nhom_dat": 0,
                "chi_tiet": {},
                "mo_ta": "",
            },
            "quyet_dinh_cuoi_cung": {"ky_hieu": "🟢", "ten": "An toàn", "mo_ta": ""},
        }

    def test_default_bilingual(self, capsys):
        in_bao_cao(self._report())
        out = capsys.readouterr().out
        assert "TỔNG QUAN THỊ TRƯỜNG (MARKET OVERVIEW)" in out
        assert "Xu hướng chung (Overall trend)" in out
        assert "tăng (rising)" in out
        assert "Hành vi khuyến nghị (Recommended action)" in out

    def test_full_vi_only(self, capsys):
        in_bao_cao(self._report(), lang_mode="full")
        out = capsys.readouterr().out
        assert "(MARKET OVERVIEW)" not in out
        assert "(Overall trend)" not in out
        assert "TỔNG QUAN THỊ TRƯỜNG" in out

    def test_compact_en_only(self, capsys):
        in_bao_cao(self._report(), lang_mode="compact")
        out = capsys.readouterr().out
        assert "MARKET OVERVIEW" in out
        assert "TỔNG QUAN THỊ TRƯỜNG" not in out
