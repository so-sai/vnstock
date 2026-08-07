"""test_causal_dag_engine.py — TDD cho Causal DAG Engine (LAW-008, LAW-009, LAW-010).

Kiểm thử:
  - LAW-008: Causal Independence (Gom nhóm nhân quả nén chain chống đếm trùng Double Counting)
  - LAW-009: Information Gain (Tính KL-Divergence / Entropy đo mức độ thông tin mới)
  - LAW-010: Evidence Utility Index (Tỷ lệ Delta Conviction / Cost)
"""



def test_law_008_causal_clustering_prevents_double_counting():
    from calibration.causal_dag_engine import CausalDAGEngine

    # Chuỗi phụ thuộc: Fed -> DXY -> USD/VND -> SBV (cùng 1 nguyên nhân Fed Hawkish)
    dependent_nodes = {
        "fed_rate": {"lr": 2.0, "cluster": "fed_chain"},
        "dxy_index": {"lr": 1.8, "cluster": "fed_chain"},
        "usd_vnd": {"lr": 1.7, "cluster": "fed_chain"},
        "sbv_omo": {"lr": 1.6, "cluster": "fed_chain"},
        "company_health": {"lr": 1.5, "cluster": "fundamental"},
    }

    engine = CausalDAGEngine()
    clustered = engine.cluster_dependent_nodes(dependent_nodes)

    # Cụm fed_chain phải được nén thành 1 LR duy nhất, nhỏ hơn rất nhiều so với tích 2.0 * 1.8 * 1.7 * 1.6 = 9.792
    fed_cluster_lr = clustered["fed_chain"]["lr"]
    assert fed_cluster_lr < 3.0  # Không bị nổ tích đếm trùng 9.792
    assert fed_cluster_lr >= 2.0  # Lớn hơn hoặc bằng max node 2.0


def test_law_009_information_gain_kl_divergence():
    from calibration.causal_dag_engine import CausalDAGEngine

    engine = CausalDAGEngine()

    # TH1: Sự kiện dự báo trước (Prior 0.50 -> Post 0.52): Information Gain rất thấp (~0.001 bits)
    ig1 = engine.compute_information_gain(p_prior=0.50, p_posterior=0.52)
    assert ig1.ig_bits < 0.05
    assert ig1.is_anomaly is False

    # TH2: Phân kỳ nhân quả bất ngờ (Prior 0.20 -> Post 0.85): Information Gain rất cao (> 0.5 bits)
    ig2 = engine.compute_information_gain(p_prior=0.20, p_posterior=0.85)
    assert ig2.ig_bits >= 0.50
    assert ig2.is_anomaly is True


def test_law_010_evidence_utility():
    from calibration.causal_dag_engine import CausalDAGEngine

    engine = CausalDAGEngine()

    # Node A: Delta Conviction = 0.20, Cost = 0.1 (BCTC Facts - Rẻ)
    u_a = engine.compute_evidence_utility(delta_conviction=0.20, compute_cost=0.1)

    # Node B: Delta Conviction = 0.20, Cost = 10.0 (Playwright Crawl - Đắt)
    u_b = engine.compute_evidence_utility(delta_conviction=0.20, compute_cost=10.0)

    assert u_a > u_b
    assert u_a >= 1.5
