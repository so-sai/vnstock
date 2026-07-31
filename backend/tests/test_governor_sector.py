"""test_governor_sector.py — Regression tests cho _symbol_sector() trong company_state.py.

WHY (bug từng xảy ra):
  - Governor V3 print_report chỉ in top_sector của TOÀN THỊ TRƯỜNG (rotation chain)
    trong Context line. Khi chạy --symbols BCM, dòng Context hiện "Sector=Viễn thông"
    (top sector market) → operator tưởng BCM thuộc ngành Viễn thông.
  - Fix: thêm _symbol_sector(symbol) trả sector THẬT per-symbol từ symbol_industry
    (icb_name2), fallback archetype → sector chỉ cho symbol trong BASELINE_MAP.
    Ranking table hiển thị cột "Ngành" per-symbol; Context line đổi label thành
    "Top Sector" (market context).

Run:  python -m pytest tests/test_governor_sector.py -q   (từ backend/)
"""

import pytest


def _src(path: str) -> str:
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent.parent
    full_path = backend_dir / "src" / path
    with open(full_path, encoding="utf-8-sig") as f:
        return f.read()


# ============================================================
# BCM must resolve to Bất động sản (fix bug "Viễn thông")
# ============================================================
class TestSymbolSectorBCM:
    def test_bcm_maps_to_bat_dong_san(self):
        """BCM phải thuộc ngành 'Bất động sản' (icb_name2 trong symbol_industry)."""
        from src.governor.company_state import _symbol_sector
        assert _symbol_sector("BCM") == "Bất động sản"

    def test_known_symbols_resolve(self):
        from src.governor.company_state import _symbol_sector
        assert _symbol_sector("FPT") == "Công nghệ Thông tin"
        assert _symbol_sector("ACB") == "Ngân hàng"
        assert _symbol_sector("VHM") == "Bất động sản"

    def test_unknown_symbol_returns_none_not_fallback(self):
        """Symbol lạ KHÔNG được rơi vào sector giả — phải None (không gây hiểu lầm)."""
        from src.governor.company_state import _symbol_sector
        assert _symbol_sector("ZZZZ") is None

    def test_no_market_top_sector_used_as_per_symbol(self):
        """Hàm không được trả về top_sector thị trường ('Viễn thông') cho BCM."""
        from src.governor.company_state import _symbol_sector
        assert _symbol_sector("BCM") != "Viễn thông"


# ============================================================
# print_report display contract
# ============================================================
class TestReportDisplay:
    def test_context_line_labels_top_sector(self):
        """Context line phải ghi 'Top Sector' — phân biệt sector market vs per-symbol."""
        src = _src("governor/company_state.py")
        assert "{_('Top Sector')}" in src, "Context line chưa đổi label thành 'Top Sector'."

    def test_ranking_table_has_sector_column(self):
        """Ranking table phải có cột 'Ngành' per-symbol."""
        src = _src("governor/company_state.py")
        assert "{'Ngành':<18}" in src, "Ranking table thiếu cột Ngành per-symbol."
        assert "sym_sector = _symbol_sector(sym) or \"?\"" in src, (
            "Ranking table không dùng _symbol_sector per-symbol."
        )
