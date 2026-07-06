"""Tests for _validate_structural_integrity — wide-bound + monotonicity."""
import pytest
from src.services.macro.interbank_seeder import _validate_structural_integrity


class TestValidationNormal:
    def test_normal_curve_passes(self):
        r = {"ON": 5.0, "1W": 4.5, "2W": 4.3, "1M": 4.0, "3M": 3.8, "6M": 3.5, "9M": 3.2}
        assert _validate_structural_integrity(r) is True

    def test_inverted_curve_passes(self):
        r = {"ON": 12.5, "1W": 10.0, "2W": 8.0, "1M": 7.0}
        assert _validate_structural_integrity(r) is True

    def test_black_swan_on_18_pct_passes(self):
        r = {"ON": 18.0, "1W": 10.0, "2W": 8.0, "1M": 6.5, "3M": 5.5}
        assert _validate_structural_integrity(r) is True

    def test_all_tenors_identical_passes(self):
        r = {t: 4.5 for t in ["ON", "1W", "2W", "1M", "3M", "6M", "9M"]}
        assert _validate_structural_integrity(r) is True


class TestValidationReject:
    def test_date_parsed_as_rate(self):
        r = {"ON": 2026.0, "1W": 4.5}
        assert _validate_structural_integrity(r) is False

    def test_on_swapped_with_3m(self):
        r = {"ON": 4.0, "1W": 4.2, "2W": 4.3, "1M": 4.5, "3M": 12.5, "6M": 3.5, "9M": 3.2}
        assert _validate_structural_integrity(r) is False

    def test_negative_rate(self):
        r = {"ON": -5.0, "1W": 4.5}
        assert _validate_structural_integrity(r) is False

    def test_absurdly_high_9m(self):
        r = {"ON": 5.0, "1W": 4.5, "9M": 99.0}
        assert _validate_structural_integrity(r) is False

    def test_severe_inversion(self):
        r = {"ON": 35.0, "1W": 5.0, "2W": 4.5}
        assert _validate_structural_integrity(r) is True  # ON=35 is at bound

    def test_beyond_bound_inversion(self):
        r = {"ON": 36.0, "1W": 5.0}
        assert _validate_structural_integrity(r) is False  # ON=36 > bound 35


class TestValidationEdgeCases:
    def test_empty_dict(self):
        assert _validate_structural_integrity({}) is True

    def test_single_tenor_valid(self):
        assert _validate_structural_integrity({"ON": 5.0}) is True

    def test_single_tenor_invalid(self):
        assert _validate_structural_integrity({"ON": 999.0}) is False

    def test_partial_missing(self):
        r = {"ON": 5.0, "3M": 4.0}
        assert _validate_structural_integrity(r) is True

    def test_none_values(self):
        r = {"ON": None, "1W": 4.5}
        assert _validate_structural_integrity(r) is True
