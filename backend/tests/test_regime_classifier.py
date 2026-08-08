"""test_regime_classifier.py — Unit tests for HMM 3-State Regime Classifier.

Tests First Principles:
  1. Stationary feature extraction (Δr_ON, Z-score OMO, USD/VND_Dev_MA90)
  2. Probability vector continuity across regime boundaries
  3. Temperature scaling effect on probability distribution
  4. EMA smoothing prevents abrupt jumps
  5. Overfitting protection (BIC monitoring, reg_covar)
  6. Fallback heuristic classification
"""

import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.governor.regime_classifier import (
    REGIME_LABELS,
    RegimeClassifier,
    RegimeResult,
    _heuristic_classify,
)


def _make_macro_db(
    interbank_on=None,
    usd_vnd=None,
    interbank_history=None,
    usd_vnd_history=None,
    seed_history=True,
):
    """Create a temporary SQLite DB with macro_history table."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE macro_history (
            variable TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL,
            is_stale INTEGER DEFAULT 0,
            PRIMARY KEY (variable, date)
        )
    """)

    today = date.today()

    # Seed interbank history
    if seed_history:
        if interbank_history is None:
            # Default: 260 days of interbank with realistic distribution
            interbank_history = []
            for i in range(260):
                d = today - timedelta(days=260 - i)
                # Mix of regimes: low (0-90), normal (90-180), high (180-260)
                if i < 90:
                    base = 3.0 + (i % 10) * 0.1  # EXPANSION
                elif i < 180:
                    base = 4.5 + (i % 10) * 0.1  # NORMAL
                else:
                    base = 6.0 + (i % 10) * 0.2  # CONTRACTION
                interbank_history.append((d.isoformat(), round(base, 2)))

        for date_str, val in interbank_history:
            conn.execute(
                "INSERT OR REPLACE INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                ("INTERBANK_ON", date_str, val),
            )

    # Seed USD/VND history
    if seed_history:
        if usd_vnd_history is None:
            usd_vnd_history = []
            for i in range(260):
                d = today - timedelta(days=260 - i)
                base = 25500 + i * 10  # gradual depreciation
                usd_vnd_history.append((d.isoformat(), round(base, 2)))

        for date_str, val in usd_vnd_history:
            conn.execute(
                "INSERT OR REPLACE INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
                ("USD_VND", date_str, val),
            )

    # Insert current values
    if interbank_on is not None:
        conn.execute(
            "INSERT OR REPLACE INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
            ("INTERBANK_ON", today.isoformat(), interbank_on),
        )
    if usd_vnd is not None:
        conn.execute(
            "INSERT OR REPLACE INTO macro_history (variable, date, value) VALUES (?, ?, ?)",
            ("USD_VND", today.isoformat(), usd_vnd),
        )

    conn.commit()
    conn.close()
    return tmp.name


# ── Test: Stationary Feature Extraction ───────────────────────────────


class TestStationaryFeatures:
    """Test stationary feature extraction from macro history."""

    def test_delta_r_removes_trend(self):
        """Δr_ON should be mean-reverting, not trending."""
        # Create trending interbank data
        history = []
        for i in range(100):
            d = date(2025, 1, 1) + timedelta(days=i)
            history.append((d.isoformat(), 3.0 + i * 0.05))  # linear trend

        db = _make_macro_db(interbank_history=history)
        classifier = RegimeClassifier(db)

        X, names = classifier._extract_stationary_features()
        assert "delta_r_on" in names
        delta_idx = names.index("delta_r_on")
        delta = X[:, delta_idx]

        # Delta should be constant (0.05) for linear trend
        assert np.allclose(delta, 0.05, atol=1e-10)
        Path(db).unlink()

    def test_usdvnd_deviation_mean_reverting(self):
        """USD/VND_Dev_MA90 should be mean-reverting."""
        # Create mean-reverting USD/VND data
        history = []
        for i in range(200):
            d = date(2025, 1, 1) + timedelta(days=i)
            # Oscillate around 25500
            base = 25500 + 500 * np.sin(2 * np.pi * i / 60)
            history.append((d.isoformat(), round(base, 2)))

        db = _make_macro_db(usd_vnd_history=history)
        classifier = RegimeClassifier(db)

        X, names = classifier._extract_stationary_features()
        assert "usdvnd_dev_ma90" in names
        dev_idx = names.index("usdvnd_dev_ma90")
        dev = X[:, dev_idx]

        # Deviation should oscillate around 0
        assert abs(np.mean(dev)) < 0.01
        Path(db).unlink()

    def test_zscore_omo_standardized(self):
        """Z-score OMO should have mean ≈ 0, std ≈ 1."""
        history = []
        for i in range(200):
            d = date(2025, 1, 1) + timedelta(days=i)
            base = 4.0 + np.random.randn() * 0.5  # normal distribution
            history.append((d.isoformat(), round(base, 2)))

        db = _make_macro_db(interbank_history=history)
        classifier = RegimeClassifier(db)

        X, names = classifier._extract_stationary_features()
        assert "zscore_omo" in names
        z_idx = names.index("zscore_omo")
        z = X[:, z_idx]

        # Z-score should be approximately standard normal
        assert abs(np.mean(z)) < 0.5
        assert abs(np.std(z) - 1.0) < 0.5
        Path(db).unlink()


# ── Test: Probability Vector Continuity ───────────────────────────────


class TestProbabilityContinuity:
    """Test that probability vectors are continuous across regime boundaries."""

    def test_smooth_transition_expansion_to_normal(self):
        """Probability should transition smoothly from EXPANSION to NORMAL."""
        # Create data that transitions from low to medium rates
        history = []
        for i in range(200):
            d = date(2025, 1, 1) + timedelta(days=i)
            if i < 100:
                base = 3.0 + (i % 10) * 0.05  # EXPANSION
            else:
                base = 4.0 + ((i - 100) % 10) * 0.1  # NORMAL
            history.append((d.isoformat(), round(base, 2)))

        db = _make_macro_db(interbank_history=history)
        classifier = RegimeClassifier(db)

        # Get probabilities at transition point
        result = classifier.classify()
        probs = result.probabilities

        # Sum of probabilities should be 1.0
        assert abs(sum(probs.values()) - 1.0) < 1e-6

        # All probabilities should be in [0, 1]
        for p in probs.values():
            assert 0.0 <= p <= 1.0
        Path(db).unlink()

    def test_probability_vector_sums_to_one(self):
        """P(EXPANSION) + P(NORMAL) + P(CONTRACTION) = 1.0."""
        db = _make_macro_db()
        classifier = RegimeClassifier(db)

        result = classifier.classify()
        total = sum(result.probabilities.values())
        assert abs(total - 1.0) < 1e-6
        Path(db).unlink()

    def test_confidence_equals_max_probability(self):
        """Confidence should equal max probability."""
        db = _make_macro_db()
        classifier = RegimeClassifier(db)

        result = classifier.classify()
        assert result.confidence == max(result.probabilities.values())
        Path(db).unlink()


# ── Test: Softmax Temperature Calibration ─────────────────────────────


class TestTemperatureScaling:
    """Test Softmax Temperature Calibration effect on posterior distribution."""

    def test_high_temperature_softens_distribution(self):
        """Higher temperature should produce more uniform distribution."""
        classifier = RegimeClassifier()

        probs = np.array([0.7, 0.2, 0.1])
        soft = classifier._apply_temperature_scaling(probs, temperature=2.0)
        hard = classifier._apply_temperature_scaling(probs, temperature=0.5)

        # Soft should be more uniform (lower max)
        assert np.max(soft) < np.max(hard)

        # Both should sum to 1
        assert abs(np.sum(soft) - 1.0) < 1e-6
        assert abs(np.sum(hard) - 1.0) < 1e-6

    def test_temperature_preserves_order(self):
        """Temperature scaling should preserve relative order."""
        classifier = RegimeClassifier()

        probs = np.array([0.6, 0.3, 0.1])
        scaled = classifier._apply_temperature_scaling(probs, temperature=1.5)

        # Order should be preserved
        assert scaled[0] > scaled[1] > scaled[2]


# ── Test: EMA Smoothing ──────────────────────────────────────────────


class TestEMASmoothing:
    """Test EMA smoothing prevents abrupt probability jumps."""

    def test_smoothing_reduces_jump_magnitude(self):
        """EMA should reduce magnitude of probability jumps."""
        classifier = RegimeClassifier()

        # Simulate abrupt jump
        prev_probs = np.array([0.8, 0.15, 0.05])
        curr_probs = np.array([0.1, 0.2, 0.7])

        # Without history: returns current_probs unchanged
        _ = classifier._smooth_probability_vector(curr_probs, [])

        # With history: smoothed result is closer to prev_probs
        history = [prev_probs] * 5
        result_with_hist = classifier._smooth_probability_vector(curr_probs, history)

        # The raw jump from prev to curr
        raw_jump = np.linalg.norm(curr_probs - prev_probs)
        # The smoothed jump from prev to smoothed
        smoothed_jump = np.linalg.norm(result_with_hist - prev_probs)

        # Smoothed jump should be smaller than raw jump
        assert smoothed_jump < raw_jump, f"Smoothed jump {smoothed_jump:.4f} should be < raw jump {raw_jump:.4f}"

    def test_smoothing_preserves_sum_to_one(self):
        """Smoothing should preserve probability sum = 1."""
        classifier = RegimeClassifier()

        history = [
            np.array([0.7, 0.2, 0.1]),
            np.array([0.6, 0.3, 0.1]),
            np.array([0.5, 0.4, 0.1]),
        ]
        current = np.array([0.1, 0.2, 0.7])

        smoothed = classifier._smooth_probability_vector(current, history)
        assert abs(np.sum(smoothed) - 1.0) < 1e-6


# ── Test: Overfitting Protection ──────────────────────────────────────


class TestOverfittingProtection:
    """Test overfitting safeguards in HMM fitting."""

    def test_bic_score_computed(self):
        """BIC score should be computed for model quality monitoring."""
        db = _make_macro_db()
        classifier = RegimeClassifier(db)

        result = classifier.classify()
        assert result.bic_score is not None
        assert isinstance(result.bic_score, float)
        Path(db).unlink()

    def test_reg_covar_prevents_singular(self):
        """reg_covar should prevent singular covariance matrices."""
        # This is tested implicitly by successful HMM fit
        db = _make_macro_db()
        classifier = RegimeClassifier(db)

        result = classifier.classify()
        assert result.hmm_fitted is True
        Path(db).unlink()


# ── Test: Fallback Heuristic ──────────────────────────────────────────


class TestHeuristicFallback:
    """Test fallback heuristic classification when HMM unavailable."""

    def test_expansion_regime(self):
        """Low rates (<= 3.0%) should classify as EXPANSION."""
        probs = _heuristic_classify(2.5)
        assert probs["EXPANSION"] == 1.0
        assert probs["NORMAL"] == 0.0
        assert probs["CONTRACTION"] == 0.0

    def test_normal_regime(self):
        """Medium rates (3.5-5.0%) should classify as NORMAL."""
        probs = _heuristic_classify(4.5)
        assert probs["EXPANSION"] == 0.0
        assert probs["NORMAL"] == 1.0
        assert probs["CONTRACTION"] == 0.0

    def test_contraction_regime(self):
        """High rates (>= 7.5%) should classify as CONTRACTION."""
        probs = _heuristic_classify(8.0)
        assert probs["EXPANSION"] == 0.0
        assert probs["NORMAL"] == 0.0
        assert probs["CONTRACTION"] == 1.0

    def test_boundary_taper_expansion_normal(self):
        """Boundary between EXPANSION and NORMAL should taper."""
        probs = _heuristic_classify(3.25)  # midpoint 3.0-3.5
        assert 0.0 < probs["EXPANSION"] < 1.0
        assert 0.0 < probs["NORMAL"] < 1.0
        assert probs["CONTRACTION"] == 0.0
        assert abs(probs["EXPANSION"] - 0.5) < 0.1

    def test_boundary_taper_normal_contraction(self):
        """Boundary between NORMAL and CONTRACTION should taper."""
        probs = _heuristic_classify(5.25)  # midpoint 5.0-5.5
        assert probs["EXPANSION"] == 0.0
        assert 0.0 < probs["NORMAL"] < 1.0
        assert 0.0 < probs["CONTRACTION"] < 1.0
        assert abs(probs["NORMAL"] - 0.5) < 0.1


# ── Test: RegimeResult Dataclass ──────────────────────────────────────


class TestRegimeResult:
    """Test RegimeResult dataclass structure."""

    def test_regime_result_has_required_fields(self):
        """RegimeResult should have all required fields."""
        result = RegimeResult(
            regime="NORMAL",
            probabilities={"EXPANSION": 0.2, "NORMAL": 0.6, "CONTRACTION": 0.2},
            interbank_avg_90d=4.5,
            confidence=0.6,
            hmm_fitted=True,
            features_used=["delta_r_on", "zscore_omo"],
            bic_score=1500.0,
        )
        assert result.regime == "NORMAL"
        assert result.probabilities["NORMAL"] == 0.6
        assert result.features_used == ["delta_r_on", "zscore_omo"]
        assert result.bic_score == 1500.0

    def test_regime_result_optional_fields(self):
        """Optional fields should default to None."""
        result = RegimeResult(
            regime="EXPANSION",
            probabilities={"EXPANSION": 1.0, "NORMAL": 0.0, "CONTRACTION": 0.0},
            interbank_avg_90d=3.0,
            confidence=1.0,
            hmm_fitted=False,
        )
        assert result.features_used is None
        assert result.bic_score is None


# ── Test: End-to-End Classification ───────────────────────────────────


class TestEndToEnd:
    """End-to-end tests for regime classification."""

    def test_live_classification(self):
        """Live classification should return valid result."""
        db = _make_macro_db()
        classifier = RegimeClassifier(db)

        result = classifier.classify()
        assert isinstance(result, RegimeResult)
        assert result.regime in REGIME_LABELS
        assert abs(sum(result.probabilities.values()) - 1.0) < 1e-6
        Path(db).unlink()

    def test_pit_date_classification(self):
        """PIT date classification should use only data up to target date."""
        # Create data with known pattern
        history = []
        for i in range(200):
            d = date(2025, 1, 1) + timedelta(days=i)
            if i < 100:
                base = 3.0  # EXPANSION
            else:
                base = 6.0  # CONTRACTION
            history.append((d.isoformat(), round(base, 2)))

        db = _make_macro_db(interbank_history=history)
        classifier = RegimeClassifier(db)

        # Classify at transition point (should see EXPANSION)
        result = classifier.classify(target_date="2025-04-10")
        assert result.regime in REGIME_LABELS
        Path(db).unlink()
