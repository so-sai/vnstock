"""test_system_auditor.py — System Auditor (CRO Layer): hợp nhất drift + calibration + overfitting + model/causal health.

TDD cho backend/src/calibration/system_auditor.py.

Các collector là hàm thuần hoặc đọc DB qua function-level imports (để monkeypatch được).
Aggregation (aggregate_audit) là hàm thuần — test trực tiếp.
"""
import json
import sqlite3

import pytest


# ═══════════════════════════════════════════════════════════════
# AGGREGATION (hàm thuần)
# ═══════════════════════════════════════════════════════════════

def _healthy_domains():
    """Bộ domain mặc định tất cả OK, score cao."""
    keys = ["data", "concept_drift", "calibration", "evidence",
            "model", "causal", "circuit_breaker", "generalization"]
    return {
        k: {"key": k, "score": 0.95, "status": "OK",
            "findings": [], "metrics": {}}
        for k in keys
    }


def test_aggregate_all_healthy_green():
    from calibration.system_auditor import aggregate_audit
    report = aggregate_audit(_healthy_domains())
    assert report["status"] == "GREEN"
    assert report["overall_score"] >= 0.85
    assert report["coverage"] == pytest.approx(1.0)


def test_aggregate_no_data_domains_excluded_and_renormalized():
    from calibration.system_auditor import aggregate_audit
    dom = _healthy_domains()
    # NO_DATA domain bị loại khỏi trung bình có trọng số
    dom["evidence"] = {"key": "evidence", "score": None,
                       "status": "NO_DATA", "findings": [], "metrics": {}}
    report = aggregate_audit(dom)
    assert report["coverage"] < 1.0
    # Các domain còn lại vẫn GREEN
    assert report["status"] == "GREEN"
    assert report["overall_score"] >= 0.85


def test_aggregate_concept_drift_degraded_lowers_score():
    from calibration.system_auditor import aggregate_audit
    dom = _healthy_domains()
    dom["concept_drift"] = {"key": "concept_drift", "score": 0.45,
                            "status": "WARN",
                            "findings": [{"severity": "HIGH",
                                          "message": "concept drift"}],
                            "metrics": {}}
    report = aggregate_audit(dom)
    assert report["overall_score"] < 0.90
    assert report["status"] in ("YELLOW", "ORANGE")


def test_aggregate_circuit_breaker_emergency_forces_red():
    from calibration.system_auditor import aggregate_audit
    dom = _healthy_domains()
    # CB level 3 là override cứng → RED dù các domain khác rất khỏe
    dom["circuit_breaker"] = {"key": "circuit_breaker", "score": 0.10,
                              "status": "CRITICAL",
                              "metrics": {"level": 3}}
    report = aggregate_audit(dom)
    assert report["status"] == "RED"
    assert "circuit_breaker" in report["hard_overrides"]


def test_aggregate_circuit_breaker_level2_forces_orange_min():
    from calibration.system_auditor import aggregate_audit
    dom = _healthy_domains()
    dom["circuit_breaker"] = {"key": "circuit_breaker", "score": 0.45,
                              "status": "CRITICAL",
                              "metrics": {"level": 2}}
    report = aggregate_audit(dom)
    # Level 2 → ít nhất ORANGE
    assert report["status"] in ("ORANGE", "RED")


def test_aggregate_any_critical_domain_forces_orange_min():
    from calibration.system_auditor import aggregate_audit
    dom = _healthy_domains()
    dom["model"] = {"key": "model", "score": 0.30, "status": "CRITICAL",
                    "findings": [], "metrics": {}}
    report = aggregate_audit(dom)
    assert report["status"] in ("ORANGE", "RED")
    assert "model" in report["hard_overrides"]


def test_aggregate_all_no_data_status_insufficient():
    from calibration.system_auditor import aggregate_audit
    dom = {k: {"key": k, "score": None, "status": "NO_DATA",
               "findings": [], "metrics": {}}
           for k in ["data", "concept_drift", "calibration"]}
    report = aggregate_audit(dom)
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall_score"] is None


def test_aggregate_full_report_dict_structure():
    from calibration.system_auditor import aggregate_audit
    report = aggregate_audit(_healthy_domains())
    for key in ["overall_score", "status", "risk_level", "coverage",
                "domains", "hard_overrides", "findings"]:
        assert key in report, f"missing {key}"
    assert report["risk_level"] == report["status"]


# ═══════════════════════════════════════════════════════════════
# COLLECTOR: collect_data_health
# ═══════════════════════════════════════════════════════════════

def test_collect_data_health_healthy(monkeypatch):
    from calibration import system_auditor

    class _FakeRows:
        def __init__(self, rows):
            self._rows = rows
        def fetchall(self):
            return self._rows
        def execute(self, *a, **k):
            return self

    class _FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return _FakeRows([
                {"component": "macro_sensors", "status": "HEALTHY"},
                {"component": "cafef", "status": "HEALTHY"},
            ])

    monkeypatch.setattr(system_auditor, "get_system_health_rows",
                        lambda: [{"component": "macro_sensors", "status": "HEALTHY"},
                                 {"component": "cafef", "status": "HEALTHY"}])
    monkeypatch.setattr(system_auditor, "get_prediction_stats",
                        lambda days=90: {"unresolved": 3, "resolved": 97})
    monkeypatch.setattr(system_auditor, "get_data_density_results",
                        lambda syms: {})

    out = system_auditor.collect_data_health(days=90)
    assert out["status"] == "OK"
    assert out["score"] >= 0.85
    assert out["key"] == "data"


def test_collect_data_health_stale_components_penalized(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_system_health_rows",
                        lambda: [{"component": "macro_sensors", "status": "STALE"},
                                 {"component": "cafef", "status": "HEALTHY"},
                                 {"component": "sbv", "status": "HEALTHY"}])
    monkeypatch.setattr(system_auditor, "get_prediction_stats",
                        lambda days=90: {"unresolved": 50, "resolved": 50})
    out = system_auditor.collect_data_health(days=90)
    assert out["status"] in ("WARN", "CRITICAL")
    assert out["score"] < 0.7
    assert any(f["severity"] == "HIGH" for f in out["findings"])


def test_collect_data_health_no_data(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_system_health_rows", lambda: [])
    monkeypatch.setattr(system_auditor, "get_prediction_stats",
                        lambda days=90: {"unresolved": 0, "resolved": 0})
    monkeypatch.setattr(system_auditor, "get_data_density_results", lambda syms: {})
    out = system_auditor.collect_data_health(days=90)
    assert out["status"] == "NO_DATA"
    assert out["score"] is None


# ═══════════════════════════════════════════════════════════════
# COLLECTOR: collect_concept_drift
# ═══════════════════════════════════════════════════════════════

def test_collect_concept_drift_no_data(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_calibration_trend",
                        lambda days=90: {"status": "NO_DATA"})
    out = system_auditor.collect_concept_drift(days=90)
    assert out["status"] == "NO_DATA"
    assert out["score"] is None


def test_collect_concept_drift_stable(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_calibration_trend",
                        lambda days=90: {
                            "status": "OK",
                            "trend": {
                                "degradation_detected": False,
                                "recent_avg_log_loss": 0.30,
                                "older_avg_log_loss": 0.29,
                            },
                        })
    out = system_auditor.collect_concept_drift(days=90)
    assert out["status"] == "OK"
    assert out["score"] >= 0.8


def test_collect_concept_drift_degradation_detected(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_calibration_trend",
                        lambda days=90: {
                            "status": "OK",
                            "trend": {
                                "degradation_detected": True,
                                "recent_avg_log_loss": 0.55,
                                "older_avg_log_loss": 0.30,
                            },
                        })
    out = system_auditor.collect_concept_drift(days=90)
    assert out["status"] in ("WARN", "CRITICAL")
    assert out["score"] < 0.7
    assert any(f["severity"] == "HIGH" for f in out["findings"])


# ═══════════════════════════════════════════════════════════════
# COLLECTOR: collect_calibration
# ═══════════════════════════════════════════════════════════════

def test_collect_calibration_no_snapshot(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_latest_calibration_snapshot",
                        lambda: None)
    out = system_auditor.collect_calibration()
    assert out["status"] == "NO_DATA"
    assert out["score"] is None


def test_collect_calibration_good_snapshot(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_latest_calibration_snapshot",
                        lambda: {"mean_log_loss": 0.40, "ece": 0.08,
                                 "mce": 0.15, "accuracy": 0.58})
    out = system_auditor.collect_calibration()
    assert out["status"] == "OK"
    assert out["score"] >= 0.8


def test_collect_calibration_bad_ece(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_latest_calibration_snapshot",
                        lambda: {"mean_log_loss": 0.75, "ece": 0.30,
                                 "mce": 0.40, "accuracy": 0.42})
    out = system_auditor.collect_calibration()
    assert out["status"] in ("WARN", "CRITICAL")
    assert out["score"] < 0.6


# ═══════════════════════════════════════════════════════════════
# COLLECTOR: collect_evidence
# ═══════════════════════════════════════════════════════════════

def test_collect_evidence_feed_gap_detected(monkeypatch):
    """Tất cả nodes n_updates=0 → hệ thống chưa feed outcome → WARNING + giảm score."""
    from calibration import system_auditor
    nodes = [
        {"node_id": "macro", "reliability": 0.5, "drift_score": 0.0, "n_updates": 0},
        {"node_id": "sector", "reliability": 0.5, "drift_score": 0.0, "n_updates": 0},
    ]
    monkeypatch.setattr(system_auditor, "get_evidence_nodes", lambda: nodes)
    out = system_auditor.collect_evidence()
    assert out["status"] in ("WARN", "CRITICAL")
    assert any("feed" in f["message"].lower() for f in out["findings"])
    assert out["score"] < 0.95


def test_collect_evidence_healthy(monkeypatch):
    from calibration import system_auditor
    nodes = [
        {"node_id": "macro", "reliability": 0.72, "drift_score": 0.05, "n_updates": 30},
        {"node_id": "sector", "reliability": 0.68, "drift_score": 0.08, "n_updates": 25},
    ]
    monkeypatch.setattr(system_auditor, "get_evidence_nodes", lambda: nodes)
    out = system_auditor.collect_evidence()
    assert out["status"] == "OK"
    assert out["score"] >= 0.8


def test_collect_evidence_drifting_nodes(monkeypatch):
    from calibration import system_auditor
    nodes = [
        {"node_id": "macro", "reliability": 0.30, "drift_score": 0.80, "n_updates": 40},
        {"node_id": "sector", "reliability": 0.55, "drift_score": 0.20, "n_updates": 25},
    ]
    monkeypatch.setattr(system_auditor, "get_evidence_nodes", lambda: nodes)
    out = system_auditor.collect_evidence()
    assert out["status"] in ("WARN", "CRITICAL")
    assert out["score"] < 0.6
    assert any("drift" in f["message"].lower() for f in out["findings"])


# ═══════════════════════════════════════════════════════════════
# COLLECTOR: collect_model / collect_causal / collect_circuit_breaker
# ═══════════════════════════════════════════════════════════════

def test_collect_model_retired_detected(monkeypatch):
    from calibration import system_auditor
    stats = {"total": 3, "active": 1, "dormant": 0, "retired": 2,
             "total_trades": 100}
    models = [
        {"model_id": "M1_MACRO", "state": "ACTIVE", "posterior": 0.9,
         "avg_brier": 0.20, "n_trades": 60},
        {"model_id": "M2_FUNDAMENTAL", "state": "RETIRED", "posterior": 0.05,
         "avg_brier": 0.30, "n_trades": 30},
        {"model_id": "M3_BEHAVIORAL", "state": "RETIRED", "posterior": 0.05,
         "avg_brier": 0.35, "n_trades": 10},
    ]
    monkeypatch.setattr(system_auditor, "get_model_stats", lambda: stats)
    monkeypatch.setattr(system_auditor, "get_models", lambda: models)
    out = system_auditor.collect_model()
    assert out["status"] in ("WARN", "CRITICAL")
    assert out["score"] < 0.8


def test_collect_model_healthy(monkeypatch):
    from calibration import system_auditor
    stats = {"total": 3, "active": 3, "dormant": 0, "retired": 0,
             "total_trades": 90}
    models = [
        {"model_id": "M1_MACRO", "state": "ACTIVE", "posterior": 0.4,
         "avg_brier": 0.18, "n_trades": 30},
        {"model_id": "M2_FUNDAMENTAL", "state": "ACTIVE", "posterior": 0.3,
         "avg_brier": 0.20, "n_trades": 30},
        {"model_id": "M3_BEHAVIORAL", "state": "ACTIVE", "posterior": 0.3,
         "avg_brier": 0.22, "n_trades": 30},
    ]
    monkeypatch.setattr(system_auditor, "get_model_stats", lambda: stats)
    monkeypatch.setattr(system_auditor, "get_models", lambda: models)
    out = system_auditor.collect_model()
    assert out["status"] == "OK"
    assert out["score"] >= 0.7


def test_collect_causal_dead_edges(monkeypatch):
    from calibration import system_auditor
    stats = {"n_edges": 5, "avg_confidence": 0.40,
             "total_counter_examples": 8}
    edges = [
        {"id": "A", "confidence": 0.15},
        {"id": "B", "confidence": 0.90},
        {"id": "C", "confidence": 0.80},
        {"id": "D", "confidence": 0.18},
        {"id": "E", "confidence": 0.70},
    ]
    monkeypatch.setattr(system_auditor, "get_causal_stats", lambda: stats)
    monkeypatch.setattr(system_auditor, "get_causal_edges", lambda: edges)
    out = system_auditor.collect_causal()
    assert out["status"] in ("WARN", "CRITICAL")
    assert any("confidence" in f["message"].lower() or "counter" in f["message"].lower()
               for f in out["findings"])


def test_collect_circuit_breaker_level_mapping(monkeypatch):
    from calibration import system_auditor
    monkeypatch.setattr(system_auditor, "get_cb_state",
                        lambda: {"level": 3, "label": "KHAN_CAP", "active": 1})
    out = system_auditor.collect_circuit_breaker()
    assert out["status"] == "CRITICAL"
    assert out["metrics"]["level"] == 3
    assert out["score"] <= 0.2

    monkeypatch.setattr(system_auditor, "get_cb_state",
                        lambda: {"level": 0, "label": "BINH_THUONG", "active": 0})
    out2 = system_auditor.collect_circuit_breaker()
    assert out2["status"] == "OK"
    assert out2["score"] >= 0.9


# ═══════════════════════════════════════════════════════════════
# PERSISTENCE (snapshot history)
# ═══════════════════════════════════════════════════════════════

def test_record_and_load_history_roundtrip():
    from calibration.system_auditor import (
        create_audit_table, record_snapshot, load_history,
    )
    conn = sqlite3.connect(":memory:")
    create_audit_table(conn)
    report = {
        "audit_date": "2099-01-05",
        "overall_score": 0.92,
        "status": "GREEN",
        "coverage": 0.8,
        "domains": {"data": {"key": "data", "score": 0.9}},
        "findings": [{"severity": "MEDIUM", "message": "test finding"}],
    }
    record_snapshot(report, conn=conn)
    rows = load_history(conn=conn)
    assert len(rows) == 1
    assert rows[0]["overall_score"] == pytest.approx(0.92)
    assert rows[0]["status"] == "GREEN"
    # JSON fields đã được parse thành dict/list bởi load_history
    assert "data" in rows[0]["domain_json"]
    assert rows[0]["findings_json"][0]["message"] == "test finding"
    conn.close()


def test_load_history_empty(monkeypatch):
    from calibration.system_auditor import (
        create_audit_table, load_history,
    )
    conn = sqlite3.connect(":memory:")
    create_audit_table(conn)
    assert load_history(conn=conn, limit=5) == []
    conn.close()


# ═══════════════════════════════════════════════════════════════
# SystemAuditor.run() — orchestration với injected sources
# ═══════════════════════════════════════════════════════════════

def test_system_auditor_run_with_injected_sources():
    from calibration.system_auditor import SystemAuditor

    def fake_collector(key, score, status):
        return {"key": key, "score": score, "status": status,
                "findings": [], "metrics": {}}

    sources = {
        "data": lambda days=90: fake_collector("data", 0.9, "OK"),
        "concept_drift": lambda days=90: fake_collector("concept_drift", 0.9, "OK"),
        "calibration": lambda days=90: fake_collector("calibration", 0.9, "OK"),
        "evidence": lambda days=90: fake_collector("evidence", 0.9, "OK"),
        "model": lambda days=90: fake_collector("model", 0.9, "OK"),
        "causal": lambda days=90: fake_collector("causal", 0.9, "OK"),
        "circuit_breaker": lambda days=90: fake_collector("circuit_breaker", 0.9, "OK"),
        "generalization": lambda days=90: fake_collector("generalization", 0.9, "OK"),
    }
    auditor = SystemAuditor(sources=sources)
    report = auditor.run(days=90)
    assert report["status"] == "GREEN"
    assert report["overall_score"] >= 0.85
    assert "audit_date" in report
    # Mỗi domain collector đã chạy
    assert len(report["domains"]) == 8


def test_system_auditor_run_cb_override():
    from calibration.system_auditor import SystemAuditor

    def fake_collector(key, score, status, metrics=None):
        return {"key": key, "score": score, "status": status,
                "findings": [], "metrics": metrics or {}}

    sources = {
        "data": lambda days=90: fake_collector("data", 0.95, "OK"),
        "concept_drift": lambda days=90: fake_collector("concept_drift", 0.95, "OK"),
        "calibration": lambda days=90: fake_collector("calibration", 0.95, "OK"),
        "evidence": lambda days=90: fake_collector("evidence", 0.95, "OK"),
        "model": lambda days=90: fake_collector("model", 0.95, "OK"),
        "causal": lambda days=90: fake_collector("causal", 0.95, "OK"),
        "circuit_breaker": lambda days=90: fake_collector(
            "circuit_breaker", 0.10, "CRITICAL", metrics={"level": 3}),
        "generalization": lambda days=90: fake_collector("generalization", 0.95, "OK"),
    }
    auditor = SystemAuditor(sources=sources)
    report = auditor.run(days=90)
    assert report["status"] == "RED"


def test_system_auditor_run_all_no_data():
    from calibration.system_auditor import SystemAuditor
    sources = {
        "data": lambda days=90: {"key": "data", "score": None,
                                 "status": "NO_DATA", "findings": [], "metrics": {}},
        "concept_drift": lambda days=90: {"key": "concept_drift", "score": None,
                                          "status": "NO_DATA", "findings": [], "metrics": {}},
    }
    auditor = SystemAuditor(sources=sources)
    report = auditor.run(days=90)
    assert report["status"] == "INSUFFICIENT_DATA"
