"""test_epistemic_engine.py — TDD cho Epistemic Engine (LAW-008 Open World & Surprise Engine).

Kiểm thử:
  - LAW-008: Open World Principle (Coverage capped ở max 0.90, dành 10% cho Unknown Unknowns)
  - Surprise Engine: Shannon Surprise S = -log2(P(Outcome)) phát hiện Anomaly khi tự tin cao nhưng thất bại
"""

import pytest


def test_law_008_open_world_coverage_cap():
    from calibration.epistemic_engine import EpistemicEngine, COVERAGE_MAX

    engine = EpistemicEngine()

    # Dữ liệu hoàn hảo mọi node
    perfect_nodes = {
        "macro": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
        "health": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
        "valuation": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
        "behavior": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
    }

    coverage = engine.compute_coverage(perfect_nodes)

    # LAW-008: Không bao giờ được phép Coverage = 1.00 (Luôn giới hạn <= COVERAGE_MAX = 0.90)
    assert coverage <= COVERAGE_MAX
    assert coverage <= 0.90


def test_surprise_engine_high_surprise():
    from calibration.epistemic_engine import EpistemicEngine

    engine = EpistemicEngine()

    # TH1: Tự tin rất cao (90%) nhưng thất bại (outcome = 0) -> HIGH SURPRISE (S > 3.0 bits)
    s1 = engine.compute_surprise(p_predict=0.90, y_outcome=0)
    assert s1.surprise_bits >= 3.0
    assert s1.is_high_surprise is True
    assert s1.status == "HIGH_SURPRISE_ANOMALY"

    # TH2: Tự tin vừa phải (55%) và thất bại (outcome = 0) -> LOW SURPRISE (S ~ 1.15 bits)
    s2 = engine.compute_surprise(p_predict=0.55, y_outcome=0)
    assert s2.surprise_bits < 2.0
    assert s2.is_high_surprise is False
    assert s2.status == "NORMAL_SURPRISE"


def test_epistemic_alloc_factor_combines_laws():
    from calibration.epistemic_engine import EpistemicEngine

    engine = EpistemicEngine()

    perfect_nodes = {
        "macro": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
        "health": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
        "valuation": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
        "behavior": {"available": True, "freshness": 1.0, "reliability": 1.0, "importance": 0.25},
    }
    coherent_chain = [
        {"node": "macro", "direction": "BEARISH", "lr": 0.6},
        {"node": "sector", "direction": "BEARISH", "lr": 0.7},
        {"node": "health", "direction": "BEARISH", "lr": 0.8},
    ]

    factor = engine.compute_alloc_factor(perfect_nodes, coherent_chain)

    # LAW-004 + LAW-006 + LAW-008 combined discount <= 0.81 (0.90 coverage * 0.90 open_world_discount)
    assert 0.0 < factor <= 0.81
