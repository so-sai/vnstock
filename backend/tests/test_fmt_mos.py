# -*- coding: utf-8 -*-
"""Unit tests for csi_explain._fmt_mos zone labels (D1/label bug regression)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.governor.csi_explain import _fmt_mos


class TestFmtMosZone:
    def test_positive_mos_is_discount(self):
        """MoS > 0 = undervalued = DISCOUNT (fair_multiple_engine contract)."""
        assert "DISCOUNT" in _fmt_mos(18.0)
        assert "PREMIUM" not in _fmt_mos(18.0)

    def test_negative_mos_is_premium(self):
        """MoS < 0 = expensive = PREMIUM (was mislabeled DISCOUNT)."""
        out = _fmt_mos(-100.6)
        assert "PREMIUM" in out
        assert "DISCOUNT" not in out
        assert "-100.6" in out

    def test_zero_mos_is_fair(self):
        assert "FAIR" in _fmt_mos(0.0)

    def test_none_is_na(self):
        assert "N/A" in _fmt_mos(None)

    def test_sign_preserved(self):
        assert "+18.0%" in _fmt_mos(18.0)
        assert "-18.0%" in _fmt_mos(-18.0)
