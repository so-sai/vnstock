"""Tests cho Meta Evidence + Adaptive CUSUM modules."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "backend"))

from src.core.meta_evidence import (
    MetaEvidence, ExecutionJournal, ExecutionQuality, ModelQuality,
    STOP_LOSS_DECAY, SIGMOID_BIAS,
)
from src.core.adaptive_cusum import AdaptiveCUSUM, RollingMAD, SIGMA_FLOOR


# ── SIGMOID BIAS ────────────────────────────────────────────────────

class TestSigmoidBias:
    def test_perfect_returns_095(self):
        raw = SIGMOID_BIAS - 0.0
        assert 0.94 < 1.0 / (1.0 + math.exp(-raw)) < 0.96

    def test_penalty_3_returns_05(self):
        raw = SIGMOID_BIAS - 3.0
        assert 0.48 < 1.0 / (1.0 + math.exp(-raw)) < 0.52

    def test_penalty_6_returns_005(self):
        raw = SIGMOID_BIAS - 6.0
        assert 0.04 < 1.0 / (1.0 + math.exp(-raw)) < 0.06


# ── HALF-LIFE DECAY ────────────────────────────────────────────────

class TestHalfLife:
    def test_60_phiên_decay(self):
        assert 0.988 < STOP_LOSS_DECAY < 0.989

    def test_decay_accumulation(self):
        """5 phiên decay → ~0.943"""
        val = 1.0
        for _ in range(5):
            val *= STOP_LOSS_DECAY
        assert 0.94 < val < 0.95

    def test_decay_60_phiên(self):
        """60 phiên decay → ~0.5"""
        val = 1.0
        for _ in range(60):
            val *= STOP_LOSS_DECAY
        assert 0.49 < val < 0.51


# ── EXECUTION QUALITY ──────────────────────────────────────────────

class TestExecutionQuality:
    def test_initial_score(self):
        eq = ExecutionQuality()
        assert 0.94 < eq.score < 0.96  # near perfect

    def test_slippage_decreases_score(self):
        eq = ExecutionQuality()
        for _ in range(50):  # đủ để EMA hội tụ ~99.5%
            eq.update({"slippage_bps": 100})
        assert eq.score < 0.80


# ── MODEL QUALITY ──────────────────────────────────────────────────

class TestModelQuality:
    def test_initial_perfect(self):
        mq = ModelQuality(half_life=60)
        assert 0.94 < mq.market_fit_score < 0.96

    def test_stop_loss_reduces_score(self):
        mq = ModelQuality(half_life=60)
        for _ in range(10):
            mq.record_stop_loss()
        assert mq.market_fit_score < 0.85

    def test_prediction_error_reduces_score(self):
        mq = ModelQuality(half_life=60)
        for _ in range(50):  # đủ để EMA hội tụ ~92%
            mq.record_prediction_error(0.05, -0.10)  # error=0.15
        assert mq.market_fit_score < 0.85

    def test_decay_heals_score(self):
        mq = ModelQuality(half_life=60)
        mq.record_stop_loss()
        low = mq.market_fit_score
        for _ in range(60):
            mq.decay_step()
        # Sau 60 phiên, score should recover
        assert mq.market_fit_score > low

    def test_novelty(self):
        mq = ModelQuality(half_life=60)
        mq.update_novelty(10.0)  # high distance
        assert mq.novelty_risk_score > 0.5

    def test_reset(self):
        mq = ModelQuality(half_life=60)
        mq.record_stop_loss()
        mq.reset()
        assert mq.stop_loss_count == 0.0
        assert 0.94 < mq.market_fit_score < 0.96


# ── EXECUTION JOURNAL ──────────────────────────────────────────────

class TestExecutionJournal:
    def test_record_and_recent(self):
        j = ExecutionJournal()
        j.record({"symbol": "FPT", "exit_reason": "STOP_LOSS"})
        j.record({"symbol": "VCB", "exit_reason": "TARGET_HIT"})
        recent = j.recent(5)
        assert len(recent) == 2
        assert recent[-1]["symbol"] == "VCB"

    def test_clear(self):
        j = ExecutionJournal()
        j.record({"symbol": "FPT"})
        j.clear()
        assert len(j.recent()) == 0


# ── META EVIDENCE ──────────────────────────────────────────────────

class TestMetaEvidence:
    def test_initial_trust_high(self):
        meta = MetaEvidence()
        assert meta.effective_trust > 0.90

    def test_calibration_vector_4d(self):
        meta = MetaEvidence()
        cv = meta.calibration_vector
        assert len(cv) == 4
        for k in ("market_fit", "execution_fit", "data_quality", "novelty_risk"):
            assert k in cv

    def test_stop_loss_reduces_trust(self):
        meta = MetaEvidence()
        for _ in range(5):
            meta.update_from_journal({"exit_reason": "STOP_LOSS", "slippage_bps": 10})
            meta.decay_step()
        assert meta.effective_trust < 0.90

    def test_data_quality_stale(self):
        meta = MetaEvidence()
        meta.update_data_quality(stale=True, missing_ratio=0.0)
        assert meta.calibration_vector["data_quality"] < 0.95

    def test_reset(self):
        meta = MetaEvidence()
        meta.update_from_journal({"exit_reason": "STOP_LOSS"})
        meta.reset()
        assert meta.effective_trust > 0.90

    def test_flow_simulation(self):
        """Mô phỏng 30 phiên EOD với Meta Evidence update."""
        meta = MetaEvidence()
        for i in range(30):
            entry = {
                "exit_reason": "STOP_LOSS" if i % 5 == 0 else "TARGET_HIT",
                "slippage_bps": 5 + (i % 10),
                "expected_return": 0.02,
                "realized_return": -0.02 if i % 5 == 0 else 0.025,
            }
            meta.update_from_journal(entry)
            meta.decay_step()
        cv = meta.calibration_vector
        trust = meta.effective_trust
        assert all(0 <= v <= 1 for v in cv.values())
        assert 0 < trust <= 1


# ── ROLLING MAD ────────────────────────────────────────────────────

class TestRollingMAD:
    def test_constant_series(self):
        mad = RollingMAD(window=10)
        for _ in range(10):
            mad.push(50.0)
        assert mad.value == SIGMA_FLOOR  # no deviation → floor

    def test_noisy_series(self):
        mad = RollingMAD(window=10)
        for v in [50, 60, 40, 55, 45, 60, 40, 50, 50, 55]:
            mad.push(v)
        assert mad.value > SIGMA_FLOOR * 2  # clear deviation

    def test_mean(self):
        mad = RollingMAD(window=5)
        for v in [10, 20, 30, 40, 50]:
            mad.push(v)
        assert mad.mean == 30.0

    def test_reset(self):
        mad = RollingMAD(window=5)
        mad.push(100.0)
        mad.reset()
        assert mad.value == SIGMA_FLOOR


# ── ADAPTIVE CUSUM ─────────────────────────────────────────────────

class TestAdaptiveCUSUM:
    def test_no_break_in_stable(self):
        cusum = AdaptiveCUSUM(window=10)
        breaks = []
        for _ in range(30):
            if cusum.update(50.0):
                breaks.append(True)
        assert len(breaks) == 0  # flat series → no break

    def test_break_on_regime_shift(self):
        cusum = AdaptiveCUSUM(window=10)
        break_found = False
        for i in range(40):
            rs = 50.0 if i < 20 else 10.0
            if cusum.update(rs):
                # Break detected — Hard Reset handler sẽ gọi reset()
                break_found = True
                assert i >= 20  # break after regime shift
                cusum.reset()
                break
        # Only test that the second regime does NOT re-trigger immediately
        assert break_found
        no_false = True
        for _ in range(5):
            if cusum.update(10.0):
                no_false = False
        assert no_false  # no false triggers in new regime

    def test_no_break_during_warmup(self):
        """Không break trong window phiên đầu tiên."""
        cusum = AdaptiveCUSUM(window=10)
        jumps = [100.0] + [0.0] * 5 + [100.0] * 4
        breaks = []
        for v in jumps:
            if cusum.update(v):
                breaks.append(True)
        # Warmup = window = 10, no breaks during warmup
        assert len(breaks) == 0

    def test_reset_clears_state(self):
        cusum = AdaptiveCUSUM(window=5)
        # Warmup first
        for v in [50, 50, 50, 50, 50]:
            cusum.update(v)
        # Trigger a break
        assert cusum.update(5.0)
        cusum.reset()
        # Check state immediately after reset (before any update)
        diag = cusum.diagnose()
        assert diag["S_plus"] == 0.0
        assert diag["S_minus"] == 0.0
        assert diag["steps_since_reset"] == 0

    def test_parameters_adaptive(self):
        """k và h tỷ lệ với sigma."""
        cusum = AdaptiveCUSUM(window=10)
        cusum.update(50.0)
        cusum.update(50.0)
        diag0 = cusum.diagnose()
        # Sau warmup, push data with variance
        for v in [50, 55, 45, 52, 48]:
            cusum.update(v)
        diag1 = cusum.diagnose()
        assert diag1["k"] > 0
        assert diag1["h"] > 0

    def test_diagnose(self):
        cusum = AdaptiveCUSUM(window=5)
        for v in [50, 50, 50, 50, 50, 50]:
            cusum.update(v)
        diag = cusum.diagnose()
        for key in ("S_plus", "S_minus", "k", "h", "sigma", "steps_since_reset"):
            assert key in diag
