"""system_auditor.py — System Auditor (CRO Layer): Chief Risk Officer cho PTCK.

Hợp nhất toàn bộ tự đánh giá của hệ thống vào MỘT lớp duy nhất:

  - DATA_COMPLETENESS — sức khỏe nguồn dữ liệu (system_health + prediction log)
  - CONCEPT_DRIFT      — degradation Log-Loss qua calibration_trend_report
  - CALIBRATION        — chất lượng calibration (ECE/MCE/Log-Loss/accuracy)
  - EVIDENCE           — sức khỏe evidence registry (drift, reliability, feed gap)
  - MODEL              — sức khỏe model registry (retirement, concentration, Brier)
  - CAUSAL             — sức khỏe causal edge registry (dead edges, counter-examples)
  - CIRCUIT_BREAKER    — trạng thái đóng băng vị thế (override cứng!)
  - GENERALIZATION     — live vs random (QuantStatsBridge)

Đầu ra:
  - Per-domain: score [0,1] (1 = khỏe), status, findings
  - Overall: score có trọng số + risk level + hard overrides
  - Snapshot lưu vào bảng system_audit_history (calibration.db) để theo trend

# ===================================================================
# ADR #7 — WHY Hard Overrides trong System Auditor?
# ===================================================================
# Circuit Breaker level 3 (KHAN_CAP) = đóng băng toàn bộ vị thế. Dù mọi
# domain khác rất khỏe, hệ thống PHẢI báo RED — vì CB phản ánh degradation
# thực tế của predictions đã resolve. Tương tự, bất kỳ domain nào CRITICAL
# cũng kéo risk level lên ít nhất ORANGE (không cho phép "trung bình đẹp"
# che giấu một hệ thống con đang sụp đổ).
# ===================================================================
"""

import json
import sqlite3
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from statistics import mean


# ── Sentinel v2.2 (AGENTS.md Anchor) ─────────────────────────────────
def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    for _p in [root_path / "backend" / "src", root_path / "backend", root_path]:
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))
    return root_path


PROJECT_ROOT = _hydrate_path()


# ── Constants ────────────────────────────────────────────────────────

DOMAIN_WEIGHTS: dict[str, float] = {
    "data": 0.10,
    "concept_drift": 0.15,
    "calibration": 0.15,
    "evidence": 0.15,
    "model": 0.15,
    "causal": 0.10,
    "circuit_breaker": 0.10,
    "generalization": 0.10,
}

# score → risk level (score cao = khỏe)
RISK_GREEN = 0.85
RISK_YELLOW = 0.70
RISK_ORANGE = 0.50

SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
STATUS_RANK = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}


# ════════════════════════════════════════════════════════════════════
# DATA SOURCE GETTERS — function-level imports để monkeypatch trong test
# ════════════════════════════════════════════════════════════════════


def get_system_health_rows() -> list[dict]:
    """Đọc component status ledger từ screener_cache.db (system_health)."""
    try:
        from src.database.db_core import get_connection

        with get_connection() as conn:
            rows = conn.execute("SELECT component, status, last_error FROM system_health").fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_prediction_stats(days: int = 90) -> dict:
    """Đếm predictions unresolved vs resolved trong cửa sổ."""
    try:
        from calibration.prediction_log import (
            get_outcomes_for_calibration,
            get_unresolved_predictions,
        )

        unresolved = get_unresolved_predictions()
        resolved = get_outcomes_for_calibration(days)
        return {"unresolved": len(unresolved), "resolved": len(resolved)}
    except Exception:
        return {"unresolved": 0, "resolved": 0}


def get_calibration_trend(days: int = 90) -> dict:
    try:
        from calibration.calibrator import calibration_trend_report

        return calibration_trend_report(days)
    except Exception:
        return {"status": "NO_DATA"}


def get_latest_calibration_snapshot() -> dict | None:
    try:
        from calibration.prediction_log import get_latest_calibration

        return get_latest_calibration()
    except Exception:
        return None


def get_evidence_nodes() -> list[dict]:
    try:
        from calibration.evidence_engine import EvidenceEngine

        return EvidenceEngine().get_all_nodes()
    except Exception:
        return []


def get_model_stats() -> dict:
    try:
        from calibration.model_registry import ModelRegistry

        return ModelRegistry().stats()
    except Exception:
        return {}


def get_models() -> list[dict]:
    try:
        from calibration.model_registry import ModelRegistry

        return ModelRegistry().get_all_models()
    except Exception:
        return []


def get_causal_stats() -> dict:
    try:
        from calibration.causal_edge import CausalGraph

        return CausalGraph().stats()
    except Exception:
        return {}


def get_causal_edges() -> list[dict]:
    try:
        from calibration.causal_edge import CausalGraph

        cg = CausalGraph()
        return [{"id": e.id, "confidence": e.confidence} for e in cg.edges.values()]
    except Exception:
        return []


def get_cb_state() -> dict:
    try:
        from calibration.prediction_log import get_circuit_breaker_state

        return get_circuit_breaker_state()
    except Exception:
        return {}


def get_generalization_report() -> dict:
    try:
        from src.core.quantstats_bridge import QuantStatsBridge

        return QuantStatsBridge().run_all()
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════════
# COLLECTORS — mỗi domain trả dict {key, score, status, findings, metrics}
# ════════════════════════════════════════════════════════════════════


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _status_from_score(score: float) -> str:
    if score >= RISK_GREEN:
        return "OK"
    if score >= 0.60:
        return "WARN"
    return "CRITICAL"


def get_data_density_results(symbols: list[str]) -> dict:
    """Đọc mật độ dữ liệu BCTC 30 quý gần nhất cho danh mục cổ phiếu."""
    try:
        from src.audit.data_integrity_auditor import DataIntegrityAuditor

        auditor = DataIntegrityAuditor()
        return auditor.audit_many(symbols)
    except Exception:
        return {}


def collect_data_health(days: int = 90) -> dict:
    rows = get_system_health_rows()
    stats = get_prediction_stats(days)

    findings = []
    penalty = 0.0

    # Deep Data Density Scan (2019Q1 - 2026Q2)
    target_syms = ["FPT", "VCB", "ACB", "VPB", "HPG", "VHM", "DGC", "GAS", "MWG", "IJC", "BCM"]
    density_results = get_data_density_results(target_syms)
    if density_results:
        gap_syms = [sym for sym, r in density_results.items() if getattr(r, "status", "") in ("GAP_FOUND", "SEVERE_GAP")]
        if gap_syms:
            severe_syms = [sym for sym, r in density_results.items() if getattr(r, "status", "") == "SEVERE_GAP"]
            findings.append(
                {
                    "severity": "HIGH" if severe_syms else "MEDIUM",
                    "message": f"phát hiện lỗ hổng mật độ dữ liệu (Data Density Gap): {', '.join(gap_syms)} thiếu chuỗi BCTC quý",
                }
            )
            penalty += 0.20 if severe_syms else 0.10

    if not rows and stats.get("resolved", 0) == 0 and stats.get("unresolved", 0) == 0 and not findings:
        return {"key": "data", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    if rows:
        bad = [r for r in rows if str(r.get("status", "")).upper() not in ("HEALTHY", "OK", "")]
        bad_ratio = len(bad) / len(rows)
        penalty += 0.6 * bad_ratio
        if bad:
            components = ", ".join(r["component"] for r in bad[:5])
            findings.append(
                {
                    "severity": "HIGH" if bad_ratio >= 0.25 else "MEDIUM",
                    "message": f"component(s) không HEALTHY: {components}",
                }
            )

    total_pred = stats.get("resolved", 0) + stats.get("unresolved", 0)
    if total_pred > 0:
        unresolved_ratio = stats.get("unresolved", 0) / total_pred
        penalty += 0.4 * unresolved_ratio
        if unresolved_ratio > 0.5:
            findings.append(
                {
                    "severity": "HIGH",
                    "message": f"tỷ lệ prediction chưa resolve rất cao: {unresolved_ratio:.0%} "
                    f"({stats.get('unresolved', 0)} chưa resolve, "
                    f"{stats.get('resolved', 0)} resolved)",
                }
            )
        elif unresolved_ratio > 0.3:
            findings.append(
                {
                    "severity": "MEDIUM",
                    "message": f"tỷ lệ prediction chưa resolve cao: {unresolved_ratio:.0%}",
                }
            )

    score = _clamp(1.0 - penalty)
    return {
        "key": "data",
        "score": score,
        "status": _status_from_score(score),
        "findings": findings,
        "metrics": {"components": len(rows), "unresolved": stats.get("unresolved", 0)},
    }


def collect_concept_drift(days: int = 90) -> dict:
    report = get_calibration_trend(days)
    if report.get("status") != "OK":
        return {"key": "concept_drift", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    trend = report.get("trend", {})
    degraded = trend.get("degradation_detected", False)
    recent_ll = trend.get("recent_avg_log_loss", 0.0) or 0.0
    older_ll = trend.get("older_avg_log_loss", 0.0) or 0.0

    findings = []
    penalty = 0.0
    if degraded:
        penalty += 0.5
        findings.append(
            {
                "severity": "HIGH",
                "message": f"concept drift phát hiện: Log-Loss tăng ({older_ll:.3f} → {recent_ll:.3f})",
            }
        )
    if recent_ll > 0.55:
        penalty += 0.3
        findings.append(
            {
                "severity": "HIGH" if recent_ll > 0.70 else "MEDIUM",
                "message": f"recent Log-Loss cao: {recent_ll:.3f}",
            }
        )

    score = _clamp(1.0 - penalty)
    return {
        "key": "concept_drift",
        "score": score,
        "status": _status_from_score(score),
        "findings": findings,
        "metrics": {"degraded": degraded, "recent_avg_log_loss": recent_ll},
    }


def collect_calibration(days: int = 90) -> dict:
    snap = get_latest_calibration_snapshot()
    if not snap:
        return {"key": "calibration", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    findings = []
    penalty = 0.0

    ece = snap.get("ece") or 0.0
    mce = snap.get("mce") or 0.0
    ll = snap.get("mean_log_loss") or 0.0
    acc = snap.get("accuracy") or 0.0

    if ece > 0.25:
        penalty += 0.4
        findings.append({"severity": "HIGH", "message": f"ECE cao: {ece:.3f}"})
    elif ece > 0.15:
        penalty += 0.2
        findings.append({"severity": "MEDIUM", "message": f"ECE: {ece:.3f}"})

    if ll > 0.70:
        penalty += 0.4
        findings.append({"severity": "HIGH", "message": f"Log-Loss cao: {ll:.3f}"})
    elif ll > 0.50:
        penalty += 0.2
        findings.append({"severity": "MEDIUM", "message": f"Log-Loss: {ll:.3f}"})

    if mce > 0.30:
        penalty += 0.1

    if acc < 0.50:
        penalty += 0.15
        findings.append({"severity": "MEDIUM", "message": f"accuracy thấp: {acc:.0%}"})

    score = _clamp(1.0 - penalty)
    return {
        "key": "calibration",
        "score": score,
        "status": _status_from_score(score),
        "findings": findings,
        "metrics": {"ece": ece, "mean_log_loss": ll, "accuracy": acc},
    }


def collect_evidence(days: int = 90) -> dict:
    nodes = get_evidence_nodes()
    if not nodes:
        return {"key": "evidence", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    findings = []
    drift_scores = [n.get("drift_score", 0.0) or 0.0 for n in nodes]
    reliabilities = [n.get("reliability", 0.5) or 0.5 for n in nodes]
    n_updates = [n.get("n_updates", 0) or 0 for n in nodes]

    avg_drift = mean(drift_scores)
    avg_rel = mean(reliabilities)
    high_drift = [n["node_id"] for n in nodes if (n.get("drift_score", 0.0) or 0.0) >= 0.65]
    feed_gap = sum(n_updates) == 0

    penalty = avg_drift
    penalty += 0.3 * (len(high_drift) / len(nodes))
    if feed_gap:
        penalty += 0.25
        findings.append(
            {
                "severity": "HIGH",
                "message": "evidence registry chưa được feed outcome thực tế "
                "(mọi node n_updates=0) — dynamic weighting chạy trên prior",
            }
        )
    if high_drift:
        findings.append(
            {
                "severity": "HIGH" if len(high_drift) >= len(nodes) / 2 else "MEDIUM",
                "message": f"evidence node drift cao: {', '.join(high_drift)}",
            }
        )
    elif avg_drift >= 0.35:
        findings.append(
            {
                "severity": "MEDIUM",
                "message": f"evidence drift trung bình cao: {avg_drift:.2f}",
            }
        )
    elif avg_drift >= 0.25:
        findings.append(
            {
                "severity": "LOW",
                "message": f"evidence drift trung bình tăng: {avg_drift:.2f}",
            }
        )

    score = _clamp(1.0 - penalty)
    return {
        "key": "evidence",
        "score": score,
        "status": _status_from_score(score),
        "findings": findings,
        "metrics": {"avg_drift": round(avg_drift, 4), "avg_reliability": round(avg_rel, 4), "feed_gap": feed_gap},
    }


def collect_model(days: int = 90) -> dict:
    stats = get_model_stats()
    models = get_models()
    if not stats or not models:
        return {"key": "model", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    findings = []
    total = stats.get("total", 0)
    if total == 0:
        return {"key": "model", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    retired_ratio = stats.get("retired", 0) / total
    penalty = 0.5 * retired_ratio

    if retired_ratio > 0.3:
        findings.append(
            {
                "severity": "HIGH" if retired_ratio >= 0.5 else "MEDIUM",
                "message": f"{stats.get('retired', 0)}/{total} hypothesis đã RETIRED",
            }
        )

    active = [m for m in models if m.get("state") == "ACTIVE"]
    if active:
        briers = [m.get("avg_brier", 0.0) or 0.0 for m in active]
        avg_brier = mean(briers)
        penalty += 0.3 * avg_brier
        concentration = max(m.get("posterior", 0.0) for m in active)
        if concentration > 0.8:
            penalty += 0.2
            findings.append(
                {
                    "severity": "MEDIUM",
                    "message": f"BMA concentration quá cao: M{concentration:.0%} chiếm gần như toàn bộ posterior",
                }
            )

    score = _clamp(1.0 - penalty)
    return {
        "key": "model",
        "score": score,
        "status": _status_from_score(score),
        "findings": findings,
        "metrics": {"retired_ratio": round(retired_ratio, 4), "total": total},
    }


def collect_causal(days: int = 90) -> dict:
    stats = get_causal_stats()
    edges = get_causal_edges()
    if not edges:
        return {"key": "causal", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    findings = []
    dead = [e for e in edges if (e.get("confidence", 0.0) or 0.0) < 0.30]
    dead_ratio = len(dead) / len(edges)
    avg_conf = stats.get("avg_confidence", 0.0) or 0.0
    n_cx = stats.get("total_counter_examples", 0) or 0

    penalty = 0.3 * dead_ratio + min(0.2, n_cx / 50.0)
    score = _clamp(avg_conf - penalty)

    if dead_ratio > 0.2:
        findings.append(
            {
                "severity": "HIGH",
                "message": f"{len(dead)}/{len(edges)} causal edge confidence < 0.30 (dead/retired candidate)",
            }
        )
    if n_cx > 0:
        findings.append(
            {
                "severity": "MEDIUM",
                "message": f"{n_cx} counter-example tích lũy trong causal graph",
            }
        )

    return {
        "key": "causal",
        "score": score,
        "status": _status_from_score(score),
        "findings": findings,
        "metrics": {"avg_confidence": round(avg_conf, 4), "dead_edges": len(dead), "counter_examples": n_cx},
    }


def collect_circuit_breaker(days: int = 90) -> dict:
    cb = get_cb_state()
    if not cb:
        return {"key": "circuit_breaker", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    level = int(cb.get("level", 0) or 0)
    findings = []
    score_map = {0: 1.0, 1: 0.75, 2: 0.45, 3: 0.10}
    score = score_map.get(level, 0.10)

    if level >= 2:
        findings.append(
            {
                "severity": "CRITICAL",
                "message": f"circuit breaker level {level} — {cb.get('label', '')} ({cb.get('trigger_reason', '')})",
            }
        )
    elif level == 1:
        findings.append(
            {
                "severity": "MEDIUM",
                "message": "circuit breaker level 1 (CAUTION)",
            }
        )

    return {
        "key": "circuit_breaker",
        "score": score,
        "status": "CRITICAL" if level >= 2 else ("WARN" if level == 1 else "OK"),
        "findings": findings,
        "metrics": {"level": level},
    }


def collect_generalization(days: int = 90) -> dict:
    report = get_generalization_report()
    cal = report.get("calibration", {})
    if not cal or cal.get("sharpe_live_smoothed") is None:
        return {"key": "generalization", "score": None, "status": "NO_DATA", "findings": [], "metrics": {}}

    findings = []
    penalty = cal.get("calibration_penalty", 0.0) or 0.0
    action = cal.get("action", "NONE")
    svr = cal.get("sharpe_vs_random", 0.0) or 0.0

    if action == "ABORT":
        penalty = max(penalty, 0.85)
        findings.append({"severity": "CRITICAL", "message": f"generalization ABORT — {cal.get('reason', '')}"})
    elif action == "SCALE":
        penalty = max(penalty, 0.5)
        findings.append({"severity": "HIGH", "message": f"generalization SCALE — {cal.get('reason', '')}"})
    elif svr < 1.0:
        penalty = max(penalty, 0.4)
        findings.append(
            {
                "severity": "HIGH",
                "message": f"live sharpe thấp hơn random baseline (ratio={svr:.2f})",
            }
        )

    score = _clamp(1.0 - penalty)
    return {
        "key": "generalization",
        "score": score,
        "status": _status_from_score(score),
        "findings": findings,
        "metrics": {"sharpe_vs_random": svr, "action": action},
    }


# ── Mặc định: map domain key → collector ─────────────────────────────

DEFAULT_COLLECTORS: dict[str, Callable] = {
    "data": collect_data_health,
    "concept_drift": collect_concept_drift,
    "calibration": collect_calibration,
    "evidence": collect_evidence,
    "model": collect_model,
    "causal": collect_causal,
    "circuit_breaker": collect_circuit_breaker,
    "generalization": collect_generalization,
}


# ════════════════════════════════════════════════════════════════════
# AGGREGATION — hàm thuần, dễ test
# ════════════════════════════════════════════════════════════════════


def _risk_level(score: float) -> str:
    if score >= RISK_GREEN:
        return "GREEN"
    if score >= RISK_YELLOW:
        return "YELLOW"
    if score >= RISK_ORANGE:
        return "ORANGE"
    return "RED"


def aggregate_audit(
    domain_reports: dict[str, dict],
    weights: dict[str, float] | None = None,
) -> dict:
    """Tính overall score (weighted), risk level, hard overrides, findings.

    - Domain NO_DATA (score None) bị loại khỏi trung bình, weights chuẩn hóa lại.
    - Hard overrides:
        1. Circuit Breaker level >= 3 → RED
        2. Circuit Breaker level == 2 → ít nhất ORANGE
        3. Bất kỳ domain CRITICAL nào → ít nhất ORANGE
    """
    weights = weights or DOMAIN_WEIGHTS

    available = {k: v for k, v in domain_reports.items() if v.get("score") is not None}
    all_findings: list[dict] = []
    hard_overrides: list[str] = []

    for rep in domain_reports.values():
        for f in rep.get("findings", []):
            sev = f.get("severity", "LOW")
            if SEVERITY_RANK.get(sev, 0) >= SEVERITY_RANK["HIGH"]:
                all_findings.append({**f, "domain": rep.get("key", "?")})

    # CB override
    cb = domain_reports.get("circuit_breaker", {})
    cb_level = (cb.get("metrics", {}) or {}).get("level", 0) or 0
    if cb_level >= 3:
        hard_overrides.append("circuit_breaker")
    elif cb_level == 2:
        hard_overrides.append("circuit_breaker")

    # Any CRITICAL domain
    for key, rep in domain_reports.items():
        if rep.get("status") == "CRITICAL":
            hard_overrides.append(key)

    if not available:
        return {
            "overall_score": None,
            "status": "INSUFFICIENT_DATA",
            "risk_level": "INSUFFICIENT_DATA",
            "coverage": 0.0,
            "domains": domain_reports,
            "hard_overrides": sorted(set(hard_overrides)),
            "findings": all_findings,
        }

    w_total = sum(weights.get(k, 0.0) for k in available)
    if w_total <= 0:
        score = mean(v["score"] for v in available.values())
    else:
        score = sum(weights.get(k, 0.0) * v["score"] for k, v in available.items()) / w_total

    status = _risk_level(score)
    if "circuit_breaker" in hard_overrides and cb_level >= 3:
        status = "RED"
    elif "circuit_breaker" in hard_overrides and cb_level == 2:
        status = "ORANGE" if STATUS_RANK[status] < STATUS_RANK["ORANGE"] else status
    if any(k != "circuit_breaker" for k in hard_overrides):
        status = "ORANGE" if STATUS_RANK[status] < STATUS_RANK["ORANGE"] else status
    # WARN domain bất kỳ → ít nhất YELLOW (không cho trung bình che giấu degradation)
    if any(r.get("status") == "WARN" for r in domain_reports.values()):
        status = "YELLOW" if STATUS_RANK[status] < STATUS_RANK["YELLOW"] else status

    coverage = w_total / max(sum(weights.values()), 1e-9)
    return {
        "overall_score": round(score, 4),
        "status": status,
        "risk_level": status,
        "coverage": round(coverage, 4),
        "domains": domain_reports,
        "hard_overrides": sorted(set(hard_overrides)),
        "findings": all_findings,
    }


# ════════════════════════════════════════════════════════════════════
# PERSISTENCE — snapshot vào calibration.db (system_audit_history)
# ════════════════════════════════════════════════════════════════════


def _default_audit_conn() -> sqlite3.Connection:
    from calibration.prediction_log import get_conn

    return get_conn()


def create_audit_table(conn: sqlite3.Connection | None = None) -> None:
    conn = conn or _default_audit_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_audit_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_date   TEXT NOT NULL,
            overall_score REAL,
            status       TEXT,
            coverage     REAL,
            domain_json  TEXT DEFAULT '{}',
            findings_json TEXT DEFAULT '[]',
            created_at   TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()


def record_snapshot(report: dict, conn: sqlite3.Connection | None = None) -> int:
    """Lưu một snapshot audit. Trả về id mới."""
    conn = conn or _default_audit_conn()
    create_audit_table(conn)
    cur = conn.execute(
        """INSERT INTO system_audit_history
           (audit_date, overall_score, status, coverage, domain_json, findings_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            report.get("audit_date", str(date.today())),
            report.get("overall_score"),
            report.get("status", "UNKNOWN"),
            report.get("coverage", 0.0),
            json.dumps(report.get("domains", {}), ensure_ascii=False),
            json.dumps(report.get("findings", []), ensure_ascii=False),
        ),
    )
    conn.commit()
    return cur.lastrowid


def load_history(
    limit: int = 20,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Đọc các snapshot audit gần nhất, mới nhất trước."""
    conn = conn or _default_audit_conn()
    create_audit_table(conn)
    cur = conn.execute(
        """SELECT id, audit_date, overall_score, status, coverage,
                  domain_json, findings_json, created_at
           FROM system_audit_history
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, r)) if not hasattr(r, "keys") else dict(r)
        try:
            d["domain_json"] = json.loads(d.get("domain_json") or "{}")
        except Exception:
            d["domain_json"] = {}
        try:
            d["findings_json"] = json.loads(d.get("findings_json") or "[]")
        except Exception:
            d["findings_json"] = []
        out.append(d)
    return out


# ════════════════════════════════════════════════════════════════════
# ORCHESTRATION — SystemAuditor
# ════════════════════════════════════════════════════════════════════


class SystemAuditor:
    """Chạy toàn bộ audit và trả về report dict.

    sources: dict key → callable(days=90) trả domain report. Mặc định dùng
    DEFAULT_COLLECTORS. Dùng để inject trong test.
    """

    def __init__(self, sources: dict[str, Callable] | None = None):
        self.sources = sources or DEFAULT_COLLECTORS

    def collect_all(self, days: int = 90) -> dict[str, dict]:
        domains = {}
        for key, collector in self.sources.items():
            try:
                rep = collector(days=days)
            except Exception as e:
                rep = {
                    "key": key,
                    "score": None,
                    "status": "ERROR",
                    "findings": [{"severity": "HIGH", "message": f"collector {key} lỗi: {e}"}],
                    "metrics": {},
                }
            rep.setdefault("key", key)
            domains[key] = rep
        return domains

    def run(self, days: int = 90, persist: bool = False, conn: sqlite3.Connection | None = None) -> dict:
        domains = self.collect_all(days)
        report = aggregate_audit(domains)
        report["audit_date"] = str(date.today())
        report["generated_at"] = datetime.now().isoformat()
        if persist:
            report["snapshot_id"] = record_snapshot(report, conn=conn)
        return report


# ════════════════════════════════════════════════════════════════════
# REPORT PRINTING
# ════════════════════════════════════════════════════════════════════


def print_audit_report(report: dict, lang_mode: str = "full"):
    """In toàn bộ System Audit ra console (song ngữ)."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:

        def localize_label(label, m="full"):
            return label

    def _(x):
        return localize_label(x, lang_mode)

    status = report.get("status", "INSUFFICIENT_DATA")
    score = report.get("overall_score")
    coverage = report.get("coverage", 0.0)
    icon = {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴", "INSUFFICIENT_DATA": "⚪"}.get(status, "⚪")

    print(f"\n  {'=' * 100}")
    print(
        f"  {_('SYSTEM AUDITOR')} — {_('Chief Risk Officer Layer')} | {_('Generated')}: {report.get('generated_at', '')[:19]}"
    )
    print(f"  {'=' * 100}")
    print(
        f"  {icon} {_('Risk Level')}: {status}"
        + (f"  |  {_('Overall Health')}: {score:.1%}" if score is not None else "")
        + f"  |  {_('Coverage')}: {coverage:.0%}"
    )
    print(f"  {'=' * 100}")

    print(f"\n  {'─' * 100}")
    print(f"  {_('DOMAIN HEALTH')}")
    print(f"  {'─' * 100}")
    hdr = f"  {_('Domain'):<18} {_('Status'):>10} {_('Score'):>8} {'Findings'}"
    print(hdr)
    print(f"  {'─' * 100}")
    for key, rep in sorted(report.get("domains", {}).items()):
        d_status = rep.get("status", "NO_DATA")
        d_icon = {"OK": "🟢", "WARN": "🟡", "CRITICAL": "🔴", "NO_DATA": "⚪", "ERROR": "❌"}.get(d_status, "⚪")
        d_score = rep.get("score")
        n_find = len(rep.get("findings", []))
        score_s = f"{d_score:.3f}" if d_score is not None else "N/A"
        print(f"  {key:<18} {d_icon}{d_status:>9} {score_s:>8} {n_find:>8}")

    findings = report.get("findings", [])
    print(f"\n  {'─' * 100}")
    print(f"  {_('FINDINGS')} ({len(findings)})")
    print(f"  {'─' * 100}")
    if findings:
        sev_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "CRITICAL": "⛔", "LOW": "🔵"}
        for f in findings:
            dom = f.get("domain", "?")
            sev = f.get("severity", "LOW")
            print(f"  {sev_icon.get(sev, '•')} [{dom:<16}] {f.get('message', '')}")
    else:
        print(f"  {_('Không có finding nghiêm trọng.')}")

    overrides = report.get("hard_overrides", [])
    if overrides:
        print(f"\n  {'─' * 100}")
        print(f"  {_('HARD OVERRIDES')}")
        print(f"  {'─' * 100}")
        for o in overrides:
            print(f"  ⛔ {_('Override')}: {o}")

    print(f"\n  {'=' * 100}")
    print(f"  {_('KẾT LUẬN')}")
    print(f"  {'=' * 100}")
    if score is None:
        print(f"  {_('Chưa đủ dữ liệu để audit — chờ predictions resolved + snapshots calibration.')}")
    elif status == "GREEN":
        print(f"  {_('Hệ thống ổn định. Không có rủi ro nghiêm trọng.')}")
    elif status == "YELLOW":
        print(f"  {_('Có dấu hiệu degradation nhẹ — theo dõi sát các domain WARN.')}")
    elif status == "ORANGE":
        print(f"  {_('Hệ thống có vấn đề rõ rệt — cần xử lý trước khi mở vị thế mới.')}")
    else:
        print(f"  {_('HỆ THỐNG Ở MỨC RỦI RO CAO — cân nhắc đóng băng vị thế.')}")


def print_audit_history(history: list[dict], lang_mode: str = "full"):
    """In lịch sử các snapshot audit gần nhất."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:

        def localize_label(label, m="full"):
            return label

    def _(x):
        return localize_label(x, lang_mode)

    print(f"\n  {'=' * 70}")
    print(f"  {_('SYSTEM AUDIT HISTORY')}")
    print(f"  {'=' * 70}")
    if not history:
        print(f"  {_('Chưa có snapshot nào. Chạy')} 'system-audit run --persist'.")
        return
    hdr = f"  {'ID':<5} {'Date':<12} {'Score':>8} {'Risk':>8} {'Coverage':>10}"
    print(hdr)
    print(f"  {'─' * 60}")
    for h in history:
        score = h.get("overall_score")
        score_s = f"{score:.3f}" if score is not None else "N/A"
        print(
            f"  {h.get('id', '?'):<5} {str(h.get('audit_date', '')):<12} "
            f"{score_s:>8} {str(h.get('status', '')):>8} "
            f"{h.get('coverage', 0.0):>9.0%}"
        )
