"""test_daily_cycle_orchestrator.py — Ưu tiên 3: Orchestration & Economic Daily Cycle.

TDD cho backend/src/orchestration/daily_cycle_orchestrator.py.

ExecutionGraph phân rã 3 kịch bản (morning/close/earnings), mỗi node có
stale_window (skip nếu data còn mới), mọi workflow kết thúc bằng SystemAuditor.
"""
import pytest


# ═══════════════════════════════════════════════════════════════
# ExecutionGraph — topo sort + skip fresh
# ═══════════════════════════════════════════════════════════════

def test_graph_topological_order_respects_dependencies():
    from orchestration.daily_cycle_orchestrator import ExecutionGraph

    g = ExecutionGraph()
    g.add_node("a", run=lambda ctx: {"v": 1}, deps=[])
    g.add_node("b", run=lambda ctx: {"v": 2}, deps=["a"])
    g.add_node("c", run=lambda ctx: {"v": 3}, deps=["a", "b"])
    order = g.order()
    assert order.index("a") < order.index("b") < order.index("c")


def test_graph_detects_cycle():
    from orchestration.daily_cycle_orchestrator import ExecutionGraph

    g = ExecutionGraph()
    g.add_node("a", run=lambda ctx: {}, deps=["b"])
    g.add_node("b", run=lambda ctx: {}, deps=["a"])
    with pytest.raises(Exception):
        g.order()


def test_graph_runs_in_dependency_order():
    from orchestration.daily_cycle_orchestrator import ExecutionGraph

    calls = []

    def mk(nid, dep):
        def _run(ctx):
            calls.append(nid)
            return {"node": nid}
        return _run

    g = ExecutionGraph()
    g.add_node("a", run=mk("a", None), deps=[])
    g.add_node("b", run=mk("b", None), deps=["a"])
    g.add_node("c", run=mk("c", None), deps=["b"])
    results = g.run()
    assert calls == ["a", "b", "c"]
    assert results["a"]["node"] == "a"


def test_graph_skip_fresh_node():
    from orchestration.daily_cycle_orchestrator import ExecutionGraph

    called = {"n": 0}

    def _run(ctx):
        called["n"] += 1
        return {"ok": True}

    g = ExecutionGraph()
    g.add_node("node_a", run=_run, deps=[], stale_window=7)
    # fresh_checker: node_a vẫn còn mới → skip
    results = g.run(skip_fresh=True,
                    fresh_checker=lambda nid: nid == "node_a")
    assert called["n"] == 0
    assert results["node_a"]["status"] == "SKIPPED"


def test_graph_runs_stale_node():
    from orchestration.daily_cycle_orchestrator import ExecutionGraph

    called = {"n": 0}

    def _run(ctx):
        called["n"] += 1
        return {"ok": True}

    g = ExecutionGraph()
    g.add_node("node_a", run=_run, deps=[], stale_window=7)
    results = g.run(skip_fresh=True,
                    fresh_checker=lambda nid: False)  # không fresh → chạy
    assert called["n"] == 1
    assert results["node_a"]["status"] == "OK"


def test_graph_isolates_node_failure():
    from orchestration.daily_cycle_orchestrator import ExecutionGraph

    def bad(ctx):
        raise RuntimeError("boom")

    def good(ctx):
        return {"ok": True}

    g = ExecutionGraph()
    g.add_node("bad", run=bad, deps=[])
    g.add_node("good", run=good, deps=["bad"])
    results = g.run(skip_fresh=False)
    assert results["bad"]["status"] == "FAILED"
    assert "boom" in results["bad"]["error"]
    assert results["good"]["status"] == "OK"


# ═══════════════════════════════════════════════════════════════
# DailyCycleOrchestrator — 3 kịch bản
# ═══════════════════════════════════════════════════════════════

def test_orchestrator_scenarios_defined():
    from orchestration.daily_cycle_orchestrator import SCENARIOS
    assert set(SCENARIOS.keys()) == {"morning", "close", "earnings"}


def test_morning_workflow_ends_with_audit():
    from orchestration.daily_cycle_orchestrator import DailyCycleOrchestrator

    orch = DailyCycleOrchestrator()
    g = orch.build_graph("morning")
    order = g.order()
    # Kịch bản sáng: sensors → macro → governor → audit (terminal)
    assert "system_audit" in order
    assert order[-1] == "system_audit"
    assert "world_sensors" in order
    assert "macro_state" in order
    assert "governor" in order
    assert order.index("world_sensors") < order.index("macro_state")


def test_close_workflow_ends_with_audit():
    from orchestration.daily_cycle_orchestrator import DailyCycleOrchestrator

    orch = DailyCycleOrchestrator()
    g = orch.build_graph("close")
    order = g.order()
    assert order[-1] == "system_audit"
    assert "breadth" in order
    assert "sector" in order
    assert "governor" in order


def test_earnings_workflow_ends_with_audit():
    from orchestration.daily_cycle_orchestrator import DailyCycleOrchestrator

    orch = DailyCycleOrchestrator()
    g = orch.build_graph("earnings")
    order = g.order()
    assert order[-1] == "system_audit"
    assert "financial_crawl" in order
    assert "health_v2" in order
    assert "valuation" in order
    assert order.index("financial_crawl") < order.index("health_v2")


def test_run_returns_health_report_with_veto_and_confidence(monkeypatch):
    from orchestration import daily_cycle_orchestrator as dco

    # Mock toàn bộ node runners + audit để test thuần
    def _fake_audit(**kwargs):
        return {
            "status": "YELLOW",
            "overall_score": 0.72,
            "coverage": 0.9,
            "domains": {},
        }

    monkeypatch.setattr(dco, "SCENARIOS", {
        "morning": [
            dco.NodeSpec("world_sensors", deps=[], stale_window=1,
                         desc="", fn=lambda ctx: {"ok": True}),
            dco.NodeSpec("macro_state", deps=["world_sensors"], stale_window=1,
                         desc="", fn=lambda ctx: {"ok": True}),
            dco.NodeSpec("system_audit", deps=["macro_state"], stale_window=None,
                         desc="", fn=lambda ctx: _fake_audit()),
        ],
    })
    orch = dco.DailyCycleOrchestrator()
    report = orch.run("morning", skip_fresh=False)
    assert report["workflow"] == "morning"
    assert report["health_report"]["status"] == "YELLOW"
    assert "confidence_score" in report
    assert isinstance(report["confidence_score"], float)
    assert "veto_reasons" in report
    assert isinstance(report["veto_reasons"], list)


def test_run_audit_node_collects_high_severity_reasons(monkeypatch):
    from orchestration import daily_cycle_orchestrator as dco

    def _audit(**kwargs):
        return {
            "status": "RED",
            "overall_score": 0.35,
            "coverage": 1.0,
            "domains": {
                "data": {"status": "CRITICAL",
                         "findings": [{"severity": "HIGH",
                                       "message": "unresolved cao"}]},
                "evidence": {"status": "WARN", "findings": []},
            },
        }

    monkeypatch.setattr(dco, "SCENARIOS", {
        "close": [
            dco.NodeSpec("system_audit", deps=[], stale_window=None,
                         desc="", fn=lambda ctx: _audit()),
        ],
    })
    orch = dco.DailyCycleOrchestrator()
    report = orch.run("close", skip_fresh=False)
    reasons = report["veto_reasons"]
    assert any("unresolved cao" in r for r in reasons)
    assert report["health_report"]["status"] == "RED"
    assert report["confidence_score"] < 0.5


# ═══════════════════════════════════════════════════════════════
# persistence — run log
# ═══════════════════════════════════════════════════════════════

def test_record_run_log_and_freshness(tmp_path, monkeypatch):
    import sqlite3
    from orchestration import daily_cycle_orchestrator as dco

    db_path = str(tmp_path / "cycle.db")
    monkeypatch.setattr(dco, "_cycle_db_path", lambda: db_path)

    # Ghi log node chạy thành công
    dco.record_run_log("morning", "macro_state", "OK", {"meta": 1})
    rows = _load_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["node_id"] == "macro_state"
    assert rows[0]["status"] == "OK"

    # fresh_checker phải trả True cho node vừa chạy OK (còn mới)
    assert dco._is_fresh("morning", "macro_state", window_days=7) is True
    # Node chưa bao giờ chạy → stale
    assert dco._is_fresh("morning", "never_ran", window_days=7) is False


def _load_rows(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM daily_cycle_run_log")]
    conn.close()
    return rows
