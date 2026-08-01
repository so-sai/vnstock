"""test_outcome_feed.py — Bước 2: Outcome Feed vào EvidenceEngine + CausalEdge.

TDD cho:
  - calibration.prediction_log.get_resolved_by_model()
  - calibration.evidence_engine.EvidenceEngine.batch_update_from_resolved()
  - calibration.evidence_engine.EvidenceEngine._apply_outcome() (refactor)
  - calibration.causal_edge.CausalGraph.apply_time_decay() / retire_degraded()

Nguyên tắc: hàm thuần / read-DB qua function-level imports để monkeypatch được.
"""
import pytest


# ═══════════════════════════════════════════════════════════════
# get_resolved_by_model — query helper
# ═══════════════════════════════════════════════════════════════

def test_get_resolved_by_model_returns_only_resolved_rows(tmp_path):
    import calibration.prediction_log as pl
    import sqlite3

    db_path = str(tmp_path / "pred_log2.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE prediction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            model_id TEXT,
            p_gain REAL NOT NULL,
            outcome REAL,
            log_loss REAL
        );
    """)
    conn.executemany(
        "INSERT INTO prediction_log (date, symbol, model_id, p_gain, outcome) "
        "VALUES (?,?,?,?,?)",
        [
            ("2026-07-01", "FPT", "M1_MACRO", 0.62, 1.0),
            ("2026-07-02", "ACB", "M1_MACRO", 0.55, 0.0),
        ],
    )
    conn.commit()
    conn.close()

    original = pl.get_conn
    pl.get_conn = lambda: _conn_with_factory(db_path)
    try:
        rows = pl.get_resolved_by_model("M1_MACRO", days=90)
    finally:
        pl.get_conn = original
    assert len(rows) == 2
    assert rows[0]["outcome"] == 1.0
    assert rows[1]["outcome"] == 0.0


def test_get_resolved_by_model_filters_by_model_id(tmp_path):
    import calibration.prediction_log as pl
    import sqlite3

    db_path = str(tmp_path / "pred_log.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE prediction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            model_id TEXT,
            p_gain REAL NOT NULL,
            outcome REAL,
            log_loss REAL
        );
    """)
    conn.executemany(
        "INSERT INTO prediction_log (date, symbol, model_id, p_gain, outcome) "
        "VALUES (?,?,?,?,?)",
        [
            ("2026-07-01", "FPT", "M1_MACRO", 0.6, 1.0),
            ("2026-07-01", "FPT", "M2_FUNDAMENTAL", 0.7, 1.0),
            ("2026-07-02", "FPT", "M1_MACRO", 0.6, None),  # unresolved -> bỏ
        ],
    )
    conn.commit()
    conn.close()

    original = pl.get_conn
    pl.get_conn = lambda: _conn_with_factory(db_path)
    try:
        rows = pl.get_resolved_by_model("M1_MACRO", days=90)
    finally:
        pl.get_conn = original
    assert len(rows) == 1
    assert rows[0]["symbol"] == "FPT"
    assert rows[0]["outcome"] == 1.0


# ═══════════════════════════════════════════════════════════════
# _apply_outcome — batch-safe outcome application
# ═══════════════════════════════════════════════════════════════

def test_apply_outcome_updates_reliability_and_brier(tmp_path):
    from calibration.evidence_engine import EvidenceEngine, PRIOR_ALPHA, PRIOR_BETA
    import sqlite3

    conn = _make_evidence_conn(tmp_path)
    ee = EvidenceEngine(conn=conn)
    out = ee._apply_outcome("macro", p_gain=0.80, y_true=1.0)
    assert out["reliability"] > 0.5  # y_true=1 với p cao -> reliability tăng
    assert out["n_updates"] == 1
    assert out["avg_brier"] == pytest.approx((0.8 - 1.0) ** 2, abs=1e-6)

    # Alpha phải tăng (y_true=1)
    row = conn.execute("SELECT alpha, beta FROM evidence_registry WHERE node_id='macro'").fetchone()
    assert row["alpha"] > PRIOR_ALPHA
    assert row["beta"] == PRIOR_BETA * 0.995  # forgetting decay chỉ áp dụng cho beta
    conn.close()


def test_apply_outcome_unknown_node_raises(tmp_path):
    from calibration.evidence_engine import EvidenceEngine
    import sqlite3

    conn = _make_evidence_conn(tmp_path)
    ee = EvidenceEngine(conn=conn)
    with pytest.raises(ValueError):
        ee._apply_outcome("unknown_node", p_gain=0.5, y_true=1.0)
    conn.close()


# ═══════════════════════════════════════════════════════════════
# batch_update_from_resolved — feed resolved predictions vào nodes
# ═══════════════════════════════════════════════════════════════

def test_batch_update_maps_model_to_nodes(tmp_path, monkeypatch):
    from calibration import prediction_log
    from calibration.evidence_engine import EvidenceEngine

    conn = _make_evidence_conn(tmp_path)

    resolved_rows = [
        {"id": 1, "date": "2026-06-01", "symbol": "FPT", "model_id": "M1_MACRO",
         "p_gain": 0.62, "outcome": 1.0},
        {"id": 2, "date": "2026-06-01", "symbol": "FPT", "model_id": "M3_BEHAVIORAL",
         "p_gain": 0.58, "outcome": 0.0},
    ]

    monkeypatch.setattr(
        prediction_log, "get_resolved_by_model",
        lambda mid, days=90: [r for r in resolved_rows if r["model_id"] == mid],
    )

    ee = EvidenceEngine(conn=conn)
    counts = ee.batch_update_from_resolved(days_back=90)

    # M1_MACRO → macro/transmission/sector; M3_BEHAVIORAL → behavior
    assert counts.get("macro") == 1
    assert counts.get("transmission") == 1
    assert counts.get("sector") == 1
    assert counts.get("behavior") == 1
    assert counts.get("health") is None
    assert counts.get("valuation") is None

    # Verify reliability phân biệt: macro nhận y_true=1, behavior nhận y_true=0
    row = conn.execute("SELECT alpha, beta FROM evidence_registry WHERE node_id='macro'").fetchone()
    assert row["alpha"] > row["beta"]
    row = conn.execute("SELECT alpha, beta FROM evidence_registry WHERE node_id='behavior'").fetchone()
    assert row["beta"] > row["alpha"]
    conn.close()


def test_batch_update_skips_unknown_model(tmp_path, monkeypatch):
    from calibration import prediction_log
    from calibration.evidence_engine import EvidenceEngine

    conn = _make_evidence_conn(tmp_path)

    monkeypatch.setattr(
        prediction_log, "get_resolved_by_model",
        lambda mid, days=90: [],  # không có resolved nào cho M1/M2/M3
    )
    ee = EvidenceEngine(conn=conn)
    counts = ee.batch_update_from_resolved(days_back=90)

    assert all(v == 0 or v is None for v in counts.values())
    conn.close()


# ═══════════════════════════════════════════════════════════════
# CausalGraph.apply_time_decay — suy hao cạnh hỏng theo half-life
# ═══════════════════════════════════════════════════════════════

def test_apply_time_decay_reduces_old_edge_confidence():
    from calibration import causal_edge
    from calibration.causal_edge import CausalGraph, CausalEdge
    from datetime import datetime, timedelta

    now = datetime.now()
    old_ts = (now - timedelta(days=60)).isoformat()
    fresh_ts = now.isoformat()

    g = CausalGraph()
    g.edges = {
        "OLD_EDGE": CausalEdge(
            id="OLD_EDGE", source="S", target="T", edge_type="MACRO→MACRO",
            confidence=0.90, half_life=30.0, updated_at=old_ts,
            created_at=old_ts,
        ),
        "FRESH_EDGE": CausalEdge(
            id="FRESH_EDGE", source="S", target="T", edge_type="MACRO→MACRO",
            confidence=0.80, half_life=30.0, updated_at=fresh_ts,
            created_at=fresh_ts,
        ),
    }
    g._rebuild_index()

    changed = g.apply_time_decay()
    assert changed >= 1
    # Edge 60 ngày tuổi với half-life 30 → confidence ~0.90 * 0.25
    assert g.edges["OLD_EDGE"].confidence < 0.40
    # Edge mới giữ nguyên (elapsed ~0)
    assert g.edges["FRESH_EDGE"].confidence == pytest.approx(0.80)


def test_apply_time_decay_respects_floor():
    from calibration import causal_edge
    from calibration.causal_edge import CausalGraph, CausalEdge
    from datetime import datetime, timedelta

    old_ts = (datetime.now() - timedelta(days=3650)).isoformat()
    g = CausalGraph()
    g.edges = {
        "VERY_OLD": CausalEdge(
            id="VERY_OLD", source="S", target="T", edge_type="MACRO→MACRO",
            confidence=0.90, half_life=10.0, updated_at=old_ts,
            created_at=old_ts,
        ),
    }
    g._rebuild_index()
    g.apply_time_decay()
    assert g.edges["VERY_OLD"].confidence >= causal_edge.CONFIDENCE_FLOOR


def test_retire_degraded_moves_below_threshold():
    from calibration.causal_edge import CausalGraph, CausalEdge

    g = CausalGraph()
    g.edges = {
        "GOOD": CausalEdge(id="GOOD", source="S", target="T", edge_type="MACRO→MACRO",
                           confidence=0.60),
        "BAD": CausalEdge(id="BAD", source="S", target="T", edge_type="MACRO→MACRO",
                          confidence=0.03),
    }
    g._rebuild_index()
    retired = g.retire_degraded(min_confidence=0.05)
    assert "BAD" in retired
    assert "GOOD" not in retired
    assert "BAD" not in g.edges


def test_retire_degraded_returns_empty_when_all_healthy():
    from calibration.causal_edge import CausalGraph, CausalEdge

    g = CausalGraph()
    g.edges = {
        "GOOD": CausalEdge(id="GOOD", source="S", target="T", edge_type="MACRO→MACRO",
                           confidence=0.60),
    }
    g._rebuild_index()
    assert g.retire_degraded(min_confidence=0.05) == []


def test_remove_from_db_deletes_rows(tmp_path, monkeypatch):
    import sqlite3
    from calibration import causal_edge as ce_mod
    from calibration.causal_edge import CausalGraph

    db_path = str(tmp_path / "causal.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE causal_edges (
            edge_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            lag_min INTEGER DEFAULT 0,
            lag_max INTEGER DEFAULT 90,
            confidence REAL DEFAULT 0.5,
            half_life REAL DEFAULT 30.0,
            attenuation REAL DEFAULT 0.0,
            archetype TEXT,
            factor_id TEXT,
            counter_examples TEXT DEFAULT '[]',
            description TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO causal_edges (edge_id, source, target, edge_type, confidence) "
        "VALUES ('BAD', 'S', 'T', 'MACRO→MACRO', 0.03)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(CausalGraph, "_db_path", staticmethod(lambda: db_path))
    g = CausalGraph()
    g.remove_from_db(["BAD"])

    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM causal_edges").fetchone()[0]
    conn.close()
    assert cnt == 0


# ═══════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════

def _conn_with_factory(db_path):
    import sqlite3
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def _make_evidence_conn(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "evidence.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE evidence_registry (
            node_id        TEXT PRIMARY KEY,
            alpha          REAL DEFAULT 10.0,
            beta           REAL DEFAULT 10.0,
            brier_accum    REAL DEFAULT 0.0,
            n_updates      INTEGER DEFAULT 0,
            ece_score      REAL DEFAULT 0.0,
            reliability    REAL DEFAULT 0.5,
            drift_score    REAL DEFAULT 0.0,
            applicability  REAL DEFAULT 1.0,
            last_updated   TEXT
        );
    """)
    from calibration.evidence_engine import EVIDENCE_NODE_IDS
    for nid in EVIDENCE_NODE_IDS:
        conn.execute(
            "INSERT INTO evidence_registry (node_id, alpha, beta, reliability) "
            "VALUES (?, 10.0, 10.0, 0.5)",
            (nid,),
        )
    conn.commit()
    return conn
