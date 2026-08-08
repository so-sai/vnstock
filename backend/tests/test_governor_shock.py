"""TDD tests for ShockDetector — Shock ≠ Macro Score.

Shock_t = clip(w1·Z_vol + w2·Z_spread + w3·Z_fx + w4·Z_liq + w5·Z_breadth)
Z-scores are PIT: computed vs a trailing window strictly BEFORE target_date.

Severity bands (per design):
  <0.30 NORMAL / <0.50 WATCH / <0.70 STRESS / <0.85 SHOCK / >=0.85 SYSTEMIC_SHOCK

LAWS:
  - Shock only has veto / sizing rights. It NEVER creates BUY/SELL orders.
  - PIT: no future data (trailing window excludes target_date).
  - Determinism: same date + same DB → same ShockScore.

Run: python -m pytest backend/tests/test_governor_shock.py -v
"""

import sys

import pytest
from conftest import PROJECT_ROOT

SCREENER_DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from src.governor.shock_detector import (
    COMPONENT_WEIGHTS,
    ShockDetector,
    ShockScore,
    classify_shock,
    severity_bands,
)


@pytest.fixture(scope="module")
def detector() -> ShockDetector:
    return ShockDetector(db_path=str(SCREENER_DB_PATH))


# ── Shape / range ──────────────────────────────────────────────────────────
class TestOutputShape:
    def test_severity_in_range(self, detector):
        s = detector.detect("2024-06-14")
        assert 0.0 <= s.severity <= 1.0

    def test_components_are_z_scores(self, detector):
        s = detector.detect("2024-06-14")
        for name in ("vol", "spread", "fx", "liquidity", "breadth"):
            assert name in s.z_scores, name

    def test_source_trace(self, detector):
        s = detector.detect("2024-06-14")
        for name in ("vol", "spread", "fx", "liquidity", "breadth"):
            assert s.source_trace[name], name

    def test_component_weights_sum_one(self):
        assert sum(COMPONENT_WEIGHTS.values()) == pytest.approx(1.0)


# ── PIT / determinism ───────────────────────────────────────────────────────
class TestPIT:
    def test_determinism(self, detector):
        a = detector.detect("2024-06-14")
        b = detector.detect("2024-06-14")
        assert a.severity == b.severity
        assert a.z_scores == b.z_scores

    def test_early_date_runs(self, detector):
        s = detector.detect("2021-04-05")
        assert 0.0 <= s.severity <= 1.0

    def test_trailing_window_never_uses_future(self, detector):
        s = detector.detect("2022-04-25")
        # The 2022-04-25 crisis (breadth_velocity -68.5) must be visible:
        # regime_history.breadth_velocity is read from rows <= target_date.
        assert s.z_scores["breadth"] is not None


# ── Severity classification ─────────────────────────────────────────────────
class TestSeverity:
    def test_bands_exhaustive(self):
        assert len(severity_bands()) == 5

    def test_classify_low(self):
        assert classify_shock(0.10) == "NORMAL"

    def test_classify_watch(self):
        assert classify_shock(0.40) == "WATCH"

    def test_classify_stress(self):
        assert classify_shock(0.60) == "STRESS"

    def test_classify_shock(self):
        assert classify_shock(0.80) == "SHOCK"

    def test_classify_systemic(self):
        assert classify_shock(0.90) == "SYSTEMIC_SHOCK"

    def test_boundaries_consistent(self):
        # Band thresholds must be monotonic.
        bands = [classify_shock(x) for x in (0.05, 0.35, 0.55, 0.75, 0.90)]
        assert bands == ["NORMAL", "WATCH", "STRESS", "SHOCK", "SYSTEMIC_SHOCK"]


# ── Veto / sizing rights (never creates orders) ─────────────────────────────
class TestGovernance:
    def test_score_has_no_order_semantics(self):
        # ShockScore is a read-only risk signal: exposes severity + band only.
        s = ShockScore(
            target_date="2024-06-14",
            severity=0.9,
            z_scores={"vol": 3.0, "spread": 3.0, "fx": 3.0, "liquidity": 3.0, "breadth": 3.0},
            source_trace={},
        )
        assert s.band == "SYSTEMIC_SHOCK"
        assert not hasattr(s, "action")  # no buy/sell decision built in

    def test_provenance_exposed(self, detector):
        s = detector.detect("2024-06-14")
        assert s.db_path == str(SCREENER_DB_PATH)
        assert s.target_date == "2024-06-14"
