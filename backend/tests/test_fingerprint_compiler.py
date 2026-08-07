"""test_fingerprint_compiler.py — TDD cho FingerprintCompiler & EpistemicEngine (P0–P3).

Kiểm thử:
  - LAW-004: Dynamic Coverage (Importance × Availability × Freshness × Reliability)
  - LAW-005: Fingerprint Compiler (Structural, Behavioural, Financial Outcome Fingerprints)
  - LAW-006: Causal Coherence Consistency (DAG Structural Alignment)
  - LAW-007: Falsifiability Principle (Phủ định & Vô hiệu hóa giả thuyết khi vi phạm)
"""



def test_fingerprint_compiler_extracts_fingerprints():
    from financial.fingerprint_compiler import FingerprintCompiler

    compiler = FingerprintCompiler()
    fp = compiler.compile("FPT")

    assert fp.symbol == "FPT"
    assert fp.fcf_conversion is not None
    assert fp.roic_persistence is not None
    assert fp.share_dilution_rate is not None
    assert fp.archetype_valid is True


def test_falsifiability_invalidates_broken_archetype():
    from financial.fingerprint_compiler import FingerprintCompiler

    compiler = FingerprintCompiler()
    # Giả lập dữ liệu mâu thuẫn nặng với archetype COMPOUNDER (FCF conversion âm, pha loãng cao)
    is_valid, reason = compiler.verify_falsifiability(
        symbol="TEST_FAIL",
        archetype="COMPOUNDER",
        fcf_conversion=-0.5,
        share_dilution_rate=0.25,
        recurring_ratio=0.10,
    )

    assert is_valid is False
    assert "FALSIFIED" in reason


def test_dynamic_coverage_law_004():
    from calibration.epistemic_engine import EpistemicEngine

    nodes_data = {
        "macro": {"available": True, "freshness": 1.0, "reliability": 0.95, "importance": 0.25},
        "health": {"available": True, "freshness": 0.8, "reliability": 0.90, "importance": 0.25},
        "valuation": {"available": True, "freshness": 1.0, "reliability": 0.90, "importance": 0.20},
        "behavior": {"available": True, "freshness": 1.0, "reliability": 0.85, "importance": 0.15},
        "management": {"available": False, "freshness": 0.0, "reliability": 0.50, "importance": 0.15},
    }

    engine = EpistemicEngine()
    coverage = engine.compute_coverage(nodes_data)

    assert 0.0 < coverage <= 1.0
    # Dù thiếu 1 node (management), coverage được tính theo Importance * Freshness * Reliability
    assert round(coverage, 2) > 0.60


def test_causal_coherence_law_006():
    from calibration.epistemic_engine import EpistemicEngine

    # Causal chain: Macro -> Sector -> Health -> Behavior
    # Trường hợp 1: Tín hiệu đồng thuận chiều nhân quả
    coherent_chain = [
        {"node": "macro", "direction": "BEARISH", "lr": 0.6},
        {"node": "sector", "direction": "BEARISH", "lr": 0.7},
        {"node": "health", "direction": "BEARISH", "lr": 0.8},
        {"node": "behavior", "direction": "BEARISH", "lr": 0.6},
    ]

    # Trường hợp 2: Tín hiệu mâu thuẫn nặng dọc chuỗi nhân quả
    conflicting_chain = [
        {"node": "macro", "direction": "BEARISH", "lr": 0.2},
        {"node": "sector", "direction": "BULLISH", "lr": 3.0},
        {"node": "health", "direction": "BEARISH", "lr": 0.3},
        {"node": "behavior", "direction": "BULLISH", "lr": 2.5},
    ]

    engine = EpistemicEngine()
    coherence_1 = engine.compute_causal_coherence(coherent_chain)
    coherence_2 = engine.compute_causal_coherence(conflicting_chain)

    assert coherence_1 > coherence_2
    assert coherence_1 >= 0.80
    assert coherence_2 <= 0.55
