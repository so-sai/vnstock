"""Unit tests for _parse_sbv_html() — pure HTML → dict parser.

Tests protect against SBV HTML structure changes (Oracle WebCenter).
When SBV changes their layout, the fixture HTML must be updated,
and the test will FAIL — alerting us immediately.
"""

from pathlib import Path

import pytest

from src.services.macro.interbank_seeder import _parse_sbv_html, TERM_MAP


# ── Fixture loader ──────────────────────────────────────────────────────────

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "sbv"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ── Expected outputs ────────────────────────────────────────────────────────

EXPECTED_7_TERMS = {"ON", "1W", "2W", "1M", "3M", "6M", "9M"}
EXPECTED_NORMAL = {"ON": 12.49, "1W": 7.26, "2W": 6.84, "1M": 8.20, "3M": 7.99, "6M": 7.32, "9M": 8.78}


# ── Tests ───────────────────────────────────────────────────────────────────

class TestParseNormal:
    """Standard SBV HTML — all 7 terms present, dot decimal format."""

    def test_all_terms_present(self):
        result = _parse_sbv_html(_load("interbank_normal.html"))
        assert set(result.keys()) == EXPECTED_7_TERMS

    def test_correct_values(self):
        result = _parse_sbv_html(_load("interbank_normal.html"))
        for term, expected in EXPECTED_NORMAL.items():
            assert result[term] == pytest.approx(expected, abs=0.01), f"{term} mismatch"

    def test_no_extra_keys(self):
        result = _parse_sbv_html(_load("interbank_normal.html"))
        assert set(result.keys()) == EXPECTED_7_TERMS, "No extra/missing keys"

    def test_all_values_are_float(self):
        result = _parse_sbv_html(_load("interbank_normal.html"))
        for k, v in result.items():
            assert isinstance(v, float), f"{k} is not float: {type(v)}"


class TestParseMissingTable:
    """Only 1 table on page (no interbank table) — should return empty dict."""

    def test_returns_empty(self):
        result = _parse_sbv_html(_load("interbank_missing_table.html"))
        assert result == {}

    def test_not_none(self):
        result = _parse_sbv_html(_load("interbank_missing_table.html"))
        assert isinstance(result, dict)


class TestParseCommas:
    """European comma format (12,15 instead of 12.15)."""

    COMMA_EXPECTED = {"ON": 12.15, "1W": 7.50, "2W": 6.92, "1M": 8.10, "3M": 7.85, "6M": 7.28, "9M": 8.65}

    def test_comma_conversion(self):
        result = _parse_sbv_html(_load("interbank_commas.html"))
        for term, expected in self.COMMA_EXPECTED.items():
            assert result.get(term) == pytest.approx(expected, abs=0.01), f"{term} mismatch"

    def test_all_terms_present(self):
        result = _parse_sbv_html(_load("interbank_commas.html"))
        assert set(result.keys()) == EXPECTED_7_TERMS


class TestParseEmptyCells:
    """Some rates missing (— or blank) — should return partial dict with None."""

    def test_on_present(self):
        result = _parse_sbv_html(_load("interbank_empty_cells.html"))
        assert result.get("ON") == pytest.approx(12.49, abs=0.01)

    def test_1w_none(self):
        result = _parse_sbv_html(_load("interbank_empty_cells.html"))
        assert result.get("1W") is None

    def test_2w_none(self):
        result = _parse_sbv_html(_load("interbank_empty_cells.html"))
        assert result.get("2W") is None

    def test_remaining_present(self):
        result = _parse_sbv_html(_load("interbank_empty_cells.html"))
        expected_keys = EXPECTED_7_TERMS
        assert set(result.keys()) == expected_keys


class TestParseGarbage:
    """Non-HTML input — should not crash."""

    @pytest.mark.parametrize("bad_input", [
        "",
        "not html at all",
        "<html><broken>",
        "<?xml version='1.0'?><root>text</root>",
    ])
    def test_no_crash(self, bad_input):
        result = _parse_sbv_html(bad_input)
        assert isinstance(result, dict)

    def test_empty_bytes(self):
        result = _parse_sbv_html("")
        assert result == {}


class TestTermMapCompleteness:
    """TERM_MAP covers exactly the SBV terms."""

    FIXTURE_TERMS = {"Qua đêm", "1 Tuần", "2 Tuần", "1 Tháng", "3 Tháng", "6 Tháng", "9 Tháng"}

    def test_all_fixture_terms_mapped(self):
        mapped = set(TERM_MAP.keys())
        assert mapped == self.FIXTURE_TERMS, (
            f"TERM_MAP missing: {self.FIXTURE_TERMS - mapped} | "
            f"TERM_MAP extra: {mapped - self.FIXTURE_TERMS}"
        )

    def test_no_duplicate_mappings(self):
        codes = list(TERM_MAP.values())
        assert len(codes) == len(set(codes)), "Duplicate term codes in TERM_MAP"
