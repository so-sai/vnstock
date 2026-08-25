"""test_sector_labels.py — Canonical sector vocabulary (Patch B1).

Invariant:
  1. sector_labels.py là NGUỒN CHÂN LÝ DUY NHẤT cho sector code → (VI, EN);
     phủ đúng 11 ngành CORE_SECTORS — không thiếu, không thừa.
  2. daily_market_report._SECTOR_BI và portfolio_decision_layer._SECTOR_VI
     PHẢI derive từ canonical (không cho phép mapping copy thứ hai).
  3. Orchestrator Sector Macro Scores dùng canonical formatter
     (không in mã thô STEEL/OIL...).
  4. Contract 3 mode: full=VI, compact=EN, annotated="VI (CODE)".
"""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BACKEND = ROOT / "backend"
for _p in [str(BACKEND / "src"), str(BACKEND), str(ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.core.sector_labels import (
    CORE_SECTOR_CODES,
    SECTOR_LABELS,
    sector_label,
    sector_label_vi,
)


class TestCanonicalCoverage:
    def test_covers_exactly_core_sectors(self):
        from src.engine.universe import CORE_SECTORS

        assert set(CORE_SECTOR_CODES) == set(CORE_SECTORS), (
            "canonical vocabulary lệch CORE_SECTORS: "
            f"thiếu={set(CORE_SECTORS) - set(CORE_SECTOR_CODES)}, "
            f"thừa={set(CORE_SECTOR_CODES) - set(CORE_SECTORS)}"
        )

    def test_every_entry_has_vi_and_en(self):
        for code, pair in SECTOR_LABELS.items():
            vi, en = pair
            assert vi and vi.strip(), f"{code} thiếu tên VI"
            assert en and en.strip(), f"{code} thiếu tên EN"
            assert vi != code, f"{code} chưa được dịch VI"

    def test_no_duplicate_vi_names(self):
        vi_names = [vi for vi, _ in SECTOR_LABELS.values()]
        assert len(vi_names) == len(set(vi_names)), "trùng tên VI giữa các ngành"


class TestThreeModeContract:
    def test_annotated_vi_with_code(self):
        assert sector_label("STEEL", "annotated") == "Thép (STEEL)"
        assert sector_label("UTILITY", "annotated") == "Tiện ích / Điện (UTILITY)"

    def test_full_vi_only(self):
        assert sector_label("BANK", "full") == "Ngân hàng"

    def test_compact_en_only(self):
        assert sector_label("SEC", "compact") == "Securities"

    def test_case_insensitive_trimmed(self):
        assert sector_label(" oil ", "annotated") == "Dầu khí (OIL)"

    def test_unknown_code_passthrough(self):
        assert sector_label("OTHER", "annotated") == "OTHER"

    def test_vi_helper(self):
        assert sector_label_vi("TECH") == "Công nghệ"
        assert sector_label_vi("NOPE") == "NOPE"


class TestNoDuplicateMappings:
    """Không cho phép mapping copy thứ hai tồn tại ngoài canonical."""

    def test_daily_report_derives_from_canonical(self):
        from src.services import daily_market_report as dmr

        assert dmr._SECTOR_BI is SECTOR_LABELS, (
            "daily_market_report._SECTOR_BI phải là re-export của canonical "
            "SECTOR_LABELS — cấm giữ bản copy riêng"
        )

    def test_portfolio_layer_derives_from_canonical(self):
        from src.services import portfolio_decision_layer as pdl

        assert pdl._SECTOR_VI is not None
        for code, vi in pdl._SECTOR_VI.items():
            expected = SECTOR_LABELS[code][0]
            assert vi == expected, (
                f"portfolio_decision_layer._SECTOR_VI[{code}]='{vi}' "
                f"lệch canonical '{expected}' — phải derive từ sector_labels"
            )

    def test_orchestrator_uses_canonical_formatter(self):
        """Orchestrator không được in mã thô — phải import formatter."""
        import src.engine.orchestrator as orch

        src = inspect.getsource(orch)
        assert "sector_labels" in src, (
            "orchestrator.py chưa import src.core.sector_labels"
        )
