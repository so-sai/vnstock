"""evidence_engine.py — EvidenceNode atomic unit + Dynamic Weighting (LAW-004).

Every evidence node in the Governor is tracked here with Beta posterior
(forgetting-enabled), Brier accumulation, drift score, and dynamic weight.

Schema:
  evidence_registry:
    node_id        TEXT PRIMARY KEY  -- 'macro','transmission','sector','health',
                                     -- 'capital_allocation','valuation','behavior'
    alpha          REAL DEFAULT 10.0 -- Beta posterior success (forgetting-enabled)
    beta           REAL DEFAULT 10.0 -- Beta posterior failure
    brier_accum    REAL DEFAULT 0.0  -- Sum of Brier scores for running average
    n_updates      INTEGER DEFAULT 0 -- Count of updates
    ece_score      REAL DEFAULT 0.0  -- Expected Calibration Error (updated batch)
    reliability    REAL DEFAULT 0.5  -- Cached alpha/(alpha+beta)
    drift_score    REAL DEFAULT 0.0  -- Continuous [0,1] — derived from trend
    applicability  REAL DEFAULT 1.0  -- Per-regime applicability (Sprint 2 stub)
    last_updated   TEXT              -- ISO timestamp
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from calibration.prediction_log import get_conn


# ── Constants ──

FORGETTING_DECAY = 0.995       # Half-life ~138 phiên (~6.6 tháng)
DRIFT_LAMBDA = 2.0             # Drift penalty coefficient: e^(-λ·D)
PRIOR_ALPHA = 10.0             # Prior success count (uninformative)
PRIOR_BETA = 10.0              # Prior failure count

EVIDENCE_NODE_IDS = [
    "macro",
    "transmission",
    "sector",
    "health",
    "capital_allocation",
    "valuation",
    "behavior",
]

# ── SQL ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evidence_registry (
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
"""


# ====================================================================
# EVIDENCE ENGINE
# ====================================================================

class EvidenceEngine:
    """Atomic unit manager for all evidence nodes.

    Thread-unsafe (single-process design). Every public method opens its
    own connection (lightweight SQLite).
    """

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self._conn = conn

    # ── Schema ──────────────────────────────────────────────────────

    def init_schema(self):
        conn = self._get_conn()
        conn.execute(SCHEMA_SQL)
        # Seed nodes if table was empty
        existing = conn.execute("SELECT COUNT(*) FROM evidence_registry").fetchone()[0]
        if existing == 0:
            now = datetime.now().isoformat()
            for nid in EVIDENCE_NODE_IDS:
                conn.execute(
                    "INSERT INTO evidence_registry (node_id, alpha, beta, "
                    "reliability, last_updated) VALUES (?, ?, ?, ?, ?)",
                    (nid, PRIOR_ALPHA, PRIOR_BETA, 0.5, now),
                )
            conn.commit()

    # ── Beta posterior + forgetting ─────────────────────────────────

    def record_outcome(
        self,
        node_id: str,
        p_gain: float,
        y_true: float,
    ) -> Dict[str, float]:
        """Record one prediction outcome for an evidence node.

        Steps:
          1. Apply forgetting decay: alpha *= FORGETTING_DECAY, beta *= FORGETTING_DECAY
          2. Compute Brier = (p_gain - y_true)^2
          3. Accumulate Brier, increment n_updates
          4. If y_true == 1 → alpha += 1, else beta += 1
          5. Recompute reliability = alpha / (alpha + beta)
          6. Update drift_score from recent Brier trend

        Returns dict with updated fields.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT alpha, beta, brier_accum, n_updates FROM evidence_registry WHERE node_id=?",
            (node_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown evidence node: {node_id}")

        alpha, beta, brier_accum, n_updates = row["alpha"], row["beta"], row["brier_accum"], row["n_updates"]

        # 1. Forgetting decay
        alpha *= FORGETTING_DECAY
        beta *= FORGETTING_DECAY

        # 2. Brier
        brier = (p_gain - y_true) ** 2
        brier_accum += brier
        n_updates += 1

        # 4. Beta update
        if y_true >= 0.5:
            alpha += 1.0
        else:
            beta += 1.0

        # 5. Reliability (capped to avoid 0/1 extremes)
        total = alpha + beta
        reliability = alpha / total if total > 0 else 0.5

        # 6. Drift score from average Brier vs expected (Brier of 0.25 = random)
        avg_brier = brier_accum / max(n_updates, 1)
        drift_score = min(1.0, max(0.0, (avg_brier - 0.10) / 0.40))

        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE evidence_registry SET alpha=?, beta=?, brier_accum=?, "
            "n_updates=?, reliability=?, drift_score=?, last_updated=? WHERE node_id=?",
            (alpha, beta, brier_accum, n_updates, reliability, drift_score, now, node_id),
        )
        conn.commit()

        return {
            "node_id": node_id,
            "alpha": alpha,
            "beta": beta,
            "reliability": reliability,
            "drift_score": drift_score,
            "avg_brier": avg_brier,
            "n_updates": n_updates,
        }

    # ── Dynamic Weights ────────────────────────────────────────────

    def get_dynamic_weights(
        self,
        macro_state: Optional[str] = None,
        sector_phase: Optional[str] = None,
        entropy: float = 0.0,
    ) -> Dict[str, float]:
        """Compute normalized dynamic weights for all evidence nodes.

        Sprint 2: accepts optional macro/sector/entropy context for
        Applicability Engine (A_i = f(macro, sector, entropy)).

        Formula:
          θ_i = R_i × A_i × exp(-DRIFT_LAMBDA × D_i)
          w_i = θ_i / Σθ_j

        Where:
          R_i = reliability = alpha/(alpha+beta)
          A_i = applicability (Sprint 1: 1.0; Sprint 2: from ApplicabilityEngine)
          D_i = drift_score ∈ [0,1]
        """
        # Sprint 2: compute A_i from context if provided
        if macro_state is not None and sector_phase is not None:
            try:
                from calibration.applicability_engine import compute_applicability
                app_map = compute_applicability(macro_state, sector_phase, entropy)
                # Write to DB so it persists for caching
                now = datetime.now().isoformat()
                conn_w = self._get_conn()
                for nid, ai in app_map.items():
                    conn_w.execute(
                        "UPDATE evidence_registry SET applicability=?, last_updated=? WHERE node_id=?",
                        (ai, now, nid),
                    )
                conn_w.commit()
            except Exception:
                pass

        conn = self._get_conn()
        rows = conn.execute(
            "SELECT node_id, reliability, drift_score, applicability FROM evidence_registry"
        ).fetchall()
        if not rows:
            return {}

        theta = {}
        for r in rows:
            nid = r["node_id"]
            R = r["reliability"]
            D = r["drift_score"]
            A = r["applicability"]
            theta[nid] = R * A * math.exp(-DRIFT_LAMBDA * D)

        total = sum(theta.values())
        if total <= 0:
            return {nid: 1.0 / len(theta) for nid in theta}

        return {nid: v / total for nid, v in theta.items()}

    def get_reliability(self, node_id: str) -> float:
        """Quick lookup: reliability = alpha/(alpha+beta)."""
        row = self._get_conn().execute(
            "SELECT reliability FROM evidence_registry WHERE node_id=?", (node_id,)
        ).fetchone()
        return row["reliability"] if row else 0.5

    # ── Batch update from resolved predictions (Sprint 2) ──
    # TODO: compute node-level effective probability from LR contribution
    # before updating each node. Currently prediction_log stores only
    # the composite P(Gain), not per-node probabilities.
    #
    # def batch_update_from_resolved(self, days_back: int = 90) -> Dict[str, int]:
    #     ...

    # ── Reset ──────────────────────────────────────────────────────

    def reset_node(self, node_id: str, alpha: float = PRIOR_ALPHA, beta: float = PRIOR_BETA):
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            "UPDATE evidence_registry SET alpha=?, beta=?, brier_accum=0, "
            "n_updates=0, ece_score=0, reliability=?, drift_score=0, last_updated=? "
            "WHERE node_id=?",
            (alpha, beta, alpha / (alpha + beta), now, node_id),
        )
        conn.commit()

    def reset_all(self):
        for nid in EVIDENCE_NODE_IDS:
            self.reset_node(nid)

    # ── Report ─────────────────────────────────────────────────────

    def get_all_nodes(self) -> List[Dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT node_id, alpha, beta, brier_accum, n_updates, "
            "ece_score, reliability, drift_score, applicability, last_updated "
            "FROM evidence_registry ORDER BY node_id"
        ).fetchall()
        result = []
        for r in rows:
            row = dict(r)
            row["avg_brier"] = (
                round(row["brier_accum"] / max(row["n_updates"], 1), 4)
                if row["n_updates"] > 0 else None
            )
            result.append(row)
        return result

    def set_applicability(self, node_id: str, value: float):
        """Stub cho Sprint 2."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE evidence_registry SET applicability=?, last_updated=? WHERE node_id=?",
            (max(0.0, min(1.0, value)), datetime.now().isoformat(), node_id),
        )
        conn.commit()

    # ── Internals ──────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = get_conn()
        return self._conn


# ====================================================================
# MODULE-LEVEL HELPERS (no instance needed)
# ====================================================================

def get_dynamic_evidence_weights(
    macro_state: Optional[str] = None,
    sector_phase: Optional[str] = None,
    entropy: float = 0.0,
) -> Dict[str, float]:
    """Quick-shot: returns dynamic weights dict, fallback to uniform if no data.

    Sprint 2: accepts macro/sector/entropy context for Applicability Engine.
    """
    try:
        ee = EvidenceEngine()
        ee.init_schema()
        w = ee.get_dynamic_weights(macro_state, sector_phase, entropy)
        if w:
            return w
    except Exception:
        pass
    # Fallback: uniform
    return {nid: 1.0 / len(EVIDENCE_NODE_IDS) for nid in EVIDENCE_NODE_IDS}


def print_evidence_report(nodes: List[Dict], weights: Dict[str, float], lang_mode: str = "full"):
    """Print evidence registry report to console."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    # Check if applicability varies from default
    has_applicability = any(
        abs(n.get("applicability", 1.0) - 1.0) > 0.01 for n in nodes
    )

    print(f"\n  {'='*95}")
    if has_applicability:
        print(f"  {_('EVIDENCE REGISTRY')} — {_('Dynamic Weighting')} (LAW-004) | {_('A_i ACTIVE')}")
    else:
        print(f"  {_('EVIDENCE REGISTRY')} — {_('Dynamic Weighting')} (LAW-004)")
    print(f"  {'='*95}")

    # Table header
    hdr = f"  {_('Node'):<20} {_('Reliability'):>12} {_('Drift'):>8} {_('Brier'):>8} {'N':>4}"
    if has_applicability:
        hdr += f" {_('A_i'):>6}"
    hdr += f" {_('Weight'):>8} {_('Status'):>10}"
    print(hdr)
    print(f"  {'─'*90}")

    for node in nodes:
        nid = node["node_id"]
        rel = node["reliability"]
        drift = node["drift_score"]
        avg_b = node.get("avg_brier", None)
        n_upd = node["n_updates"]
        w = weights.get(nid, 0)
        ai = node.get("applicability", 1.0)

        # Status label from drift_score
        if drift < 0.30:
            status = "🟢 " + _("NORMAL")
        elif drift < 0.65:
            status = "🟡 " + _("DRIFTING")
        else:
            status = "🔴 " + _("DEGRADED")

        brier_str = f"{avg_b:.4f}" if avg_b is not None else _("N/A")
        line = f"  {nid:<20} {rel:>11.4f} {drift:>7.3f} {brier_str:>8} {n_upd:>4}"
        if has_applicability:
            line += f" {ai:>5.3f}"
        line += f" {w:>7.4f} {status:>10}"
        print(line)

    # Summary
    print(f"\n  {_('Formula')}: θ_i = R_i × A_i × exp(-{DRIFT_LAMBDA} × D_i)")
    print(f"  {_('Forgetting')}: decay={FORGETTING_DECAY} (half-life ~138 phiên)")
    if has_applicability:
        max_ai = max(nodes, key=lambda n: n.get("applicability", 0))
        min_ai = min(nodes, key=lambda n: n.get("applicability", 0))
        print(f"  {_('A_i ACTIVE')} — max: {max_ai['node_id']} ({max_ai.get('applicability', 0):.3f}), "
              f"min: {min_ai['node_id']} ({min_ai.get('applicability', 0):.3f})")
    if weights:
        dominant = max(weights, key=weights.get)
        print(f"  {_('Dominant node')}: {dominant} ({weights[dominant]:.3f})")



