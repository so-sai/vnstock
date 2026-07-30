"""model_registry.py — Sprint 4: Competing Hypotheses Engine (Bayesian Model Averaging).

Manages 3 competing models (M1_MACRO, M2_FUNDAMENTAL, M3_BEHAVIORAL) with:
  - State transitions: ACTIVE (P≥0.15) / DORMANT (0.05≤P<0.15) / RETIRED (P<0.05)
  - Bayesian Model Averaging for posterior-weighted ensemble
  - Regime-fit scoring per macro context
  - Counter-signal tracking for conflict resolution

Schema (model_registry table in calibration.db):
  model_id        TEXT PRIMARY KEY
  hypothesis      TEXT              — description of the profit hypothesis
  prior           REAL DEFAULT 0.333 — P(M_k) before evidence
  posterior       REAL DEFAULT 0.333 — P(M_k | D) after Bayesian update
  state           TEXT DEFAULT 'ACTIVE'
  regime_fit      TEXT DEFAULT '{}'  — JSON: {regime: fit_score}
  n_trades        INTEGER DEFAULT 0
  n_wins          INTEGER DEFAULT 0
  sharpe          REAL DEFAULT 0.0
  max_drawdown    REAL DEFAULT 0.0
  brier_accum     REAL DEFAULT 0.0
  log_loss_accum  REAL DEFAULT 0.0
  counter_signals INTEGER DEFAULT 0
  last_active     TEXT
  retirement_reason TEXT DEFAULT ''
  created_at      TEXT
  updated_at      TEXT
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 3 Competing Hypotheses ──────────────────────────────────────────────

MODEL_DEFINITIONS = {
    "M1_MACRO": {
        "hypothesis": "Top-Down Macro Causal — thị trường vận động theo vĩ mô và dòng tiền liên ngân hàng",
        "hypothesis_en": "Top-Down Macro Causal — markets move on macro + interbank liquidity",
        "focus_nodes": ["macro", "transmission", "sector"],
        "regime_fit": {
            "CREDIT_STRESS": 0.85, "LIQUIDITY_EXPANSION": 0.90,
            "INFLATION_SHOCK": 0.80, "RISK_OFF": 0.85,
            "RECOVERY": 0.60, "STABLE": 0.40,
            "AI_BOOM": 0.50, "PRE_CREDIT_EXPANSION": 0.75,
        },
        "archetype_bias": {"COMPOUNDER": 0.50, "CYCLICAL_HEAVY": 0.85, "FRANCHISE_BANK": 0.90,
                           "REAL_ESTATE_DEVELOPER": 0.75, "EXPORT_MANUFACTURER": 0.70},
    },
    "M2_FUNDAMENTAL": {
        "hypothesis": "Bottom-Up Quality & Capital — doanh nghiệp tốt tự vượt qua chu kỳ vĩ mô",
        "hypothesis_en": "Bottom-Up Quality & Capital — quality companies overcome macro cycles",
        "focus_nodes": ["health", "capital_allocation", "valuation"],
        "regime_fit": {
            "CREDIT_STRESS": 0.20, "LIQUIDITY_EXPANSION": 0.50,
            "INFLATION_SHOCK": 0.30, "RISK_OFF": 0.15,
            "RECOVERY": 0.80, "STABLE": 0.85,
            "AI_BOOM": 0.75, "PRE_CREDIT_EXPANSION": 0.55,
        },
        "archetype_bias": {"COMPOUNDER": 0.90, "CYCLICAL_HEAVY": 0.40, "FRANCHISE_BANK": 0.70,
                           "RETAIL_PLATFORM": 0.80, "REGULATED_UTILITY": 0.60},
    },
    "M3_BEHAVIORAL": {
        "hypothesis": "Microstructure & Flow — dòng tiền và hành vi nhà đầu tư quyết định giá ngắn hạn",
        "hypothesis_en": "Microstructure & Flow — order flow + investor behavior drive short-term price",
        "focus_nodes": ["behavior", "entropy"],
        "regime_fit": {
            "CREDIT_STRESS": 0.65, "LIQUIDITY_EXPANSION": 0.60,
            "INFLATION_SHOCK": 0.55, "RISK_OFF": 0.80,
            "RECOVERY": 0.45, "STABLE": 0.35,
            "AI_BOOM": 0.40, "PRE_CREDIT_EXPANSION": 0.50,
        },
        "archetype_bias": {"COMPOUNDER": 0.40, "CYCLICAL_HEAVY": 0.55, "FRANCHISE_BANK": 0.60,
                           "RETAIL_PLATFORM": 0.65, "EXPORT_MANUFACTURER": 0.50},
    },
}

STATE_ACTIVE = "ACTIVE"
STATE_DORMANT = "DORMANT"
STATE_RETIRED = "RETIRED"

P_ACTIVE_THRESHOLD = 0.15
P_DORMANT_THRESHOLD = 0.05
PRIOR_DEFAULT = 1.0 / 3.0


# ═══════════════════════════════════════════════════════════════════════════
# ModelRegistry
# ═══════════════════════════════════════════════════════════════════════════

class ModelRegistry:
    """Competing Hypotheses Engine — manages 3 models with Bayesian Model Averaging."""

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self._conn = conn
        self._init_schema()
        self._seed_defaults()

    # ── Schema ──────────────────────────────────────────────────────────

    @staticmethod
    def _db_path() -> Path:
        _p = Path(__file__).resolve().parent.parent.parent.parent
        for _par in [_p] + list(_p.parents):
            if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
                return _par / "backend" / "data" / "calibration.db"
        return _p / "backend" / "data" / "calibration.db"

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path()))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_registry (
                model_id        TEXT PRIMARY KEY,
                hypothesis      TEXT NOT NULL,
                prior           REAL DEFAULT 0.333,
                posterior       REAL DEFAULT 0.333,
                state           TEXT DEFAULT 'ACTIVE',
                regime_fit      TEXT DEFAULT '{}',
                n_trades        INTEGER DEFAULT 0,
                n_wins          INTEGER DEFAULT 0,
                sharpe          REAL DEFAULT 0.0,
                max_drawdown    REAL DEFAULT 0.0,
                brier_accum     REAL DEFAULT 0.0,
                log_loss_accum  REAL DEFAULT 0.0,
                counter_signals INTEGER DEFAULT 0,
                last_active     TEXT,
                retirement_reason TEXT DEFAULT '',
                created_at      TEXT,
                updated_at      TEXT
            )
        """)
        conn.commit()

        # Seed history for new models
        for mid, cfg in MODEL_DEFINITIONS.items():
            self._log_transition(mid, STATE_ACTIVE, PRIOR_DEFAULT, "initial_seed")

    def _seed_defaults(self) -> None:
        """Insert 3 models if table empty."""
        conn = self._get_conn()
        existing = conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0]
        if existing > 0:
            return
        now = datetime.now().isoformat()
        for mid, cfg in MODEL_DEFINITIONS.items():
            conn.execute(
                """INSERT INTO model_registry
                   (model_id, hypothesis, prior, posterior, state, regime_fit,
                    n_trades, n_wins, sharpe, max_drawdown, brier_accum,
                    log_loss_accum, counter_signals, last_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, ?, ?, ?)""",
                (mid, cfg["hypothesis"], PRIOR_DEFAULT, PRIOR_DEFAULT,
                 STATE_ACTIVE, json.dumps(cfg["regime_fit"]),
                 now, now, now),
            )
        conn.commit()

    # ── Core: Regime Fit ────────────────────────────────────────────────

    def regime_fit_score(self, model_id: str, macro_state: str,
                         archetype: Optional[str] = None) -> float:
        """Compute contextual fit score from regime + archetype.

        Returns score ∈ [0, 1] combining regime_fit and (optionally) archetype_bias.
        """
        cfg = MODEL_DEFINITIONS.get(model_id)
        if not cfg:
            return 0.0
        base = cfg["regime_fit"].get(macro_state, 0.5)
        if archetype:
            arch_bias = cfg.get("archetype_bias", {}).get(archetype, 0.5)
            base = 0.6 * base + 0.4 * arch_bias
        return max(0.0, min(1.0, base))

    def select_best(self, macro_state: str,
                    archetype: Optional[str] = None) -> Dict:
        """Select best model for current context.

        Returns model dict with regime_fit_score, ordered by fit desc.
        """
        rows = self._get_conn().execute(
            "SELECT model_id, state FROM model_registry"
        ).fetchall()
        candidates = []
        for r in rows:
            if r["state"] != STATE_ACTIVE:
                continue
            score = self.regime_fit_score(r["model_id"], macro_state, archetype)
            candidates.append({"model_id": r["model_id"], "regime_fit_score": score})
        candidates.sort(key=lambda x: x["regime_fit_score"], reverse=True)
        return candidates[0] if candidates else {"model_id": "M1_MACRO", "regime_fit_score": 0.5}

    # ── Core: Bayesian Model Averaging ─────────────────────────────────

    def bma_posterior(self, macro_state: str, archetype: Optional[str] = None) -> Dict[str, float]:
        """Compute BMA-weighted posterior probabilities P(M_k | D, context).

        P(M_k | D) ∝ P(M_k) × exp(regime_fit_score_k / temperature)
        Normalized to sum = 1.0 across ACTIVE models.
        """
        temperature = 0.33  # softmax temperature
        rows = self._get_conn().execute(
            "SELECT model_id, posterior, state FROM model_registry"
        ).fetchall()
        active = [r for r in rows if r["state"] == STATE_ACTIVE]
        if not active:
            active = rows  # fallback: all models
        raw = {}
        for r in active:
            mid = r["model_id"]
            prior = r["posterior"]
            fit = self.regime_fit_score(mid, macro_state, archetype)
            raw[mid] = prior * math.exp(fit / temperature)
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}

    def bma_ensemble_prediction(self, predictions: Dict[str, float],
                                macro_state: str,
                                archetype: Optional[str] = None) -> float:
        """Weighted average of per-model predictions using BMA posteriors.

        Args:
            predictions: {model_id: predicted_p_gain}
            macro_state: current regime
            archetype: optional archetype for bias

        Returns: ensemble P(Gain) ∈ [0, 1]
        """
        weights = self.bma_posterior(macro_state, archetype)
        weighted = 0.0
        total_w = 0.0
        for mid, p in predictions.items():
            w = weights.get(mid, 0.0)
            weighted += w * p
            total_w += w
        return weighted / total_w if total_w > 0 else 0.5

    # ── Core: Posterior Update from Outcomes ──────────────────────────

    def record_outcome(self, model_id: str, p_gain: float, y_true: float) -> None:
        """Update model posterior from prediction outcome using Bayes rule.

        Likelihood: P(D | M_k) = 1 - |p_gain - y_true|  (accuracy proxy)
        Posterior: P(M_k | D) = P(D | M_k) × prior / evidence
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT posterior, n_trades, n_wins, brier_accum, log_loss_accum, state "
            "FROM model_registry WHERE model_id=?", (model_id,)
        ).fetchone()
        if not row:
            return

        posterior = row["posterior"]
        n_trades = row["n_trades"]
        n_wins = row["n_wins"]
        brier_accum = row["brier_accum"]
        ll_accum = row["log_loss_accum"]
        state = row["state"]

        # Likelihood: higher when prediction is accurate
        likelihood = 1.0 - abs(p_gain - y_true)
        # Avoid extremes
        likelihood = max(0.01, min(0.99, likelihood))

        # Evidence = Σ P(D | M_k) × P(M_k) across all models
        evidence = 0.0
        all_rows = conn.execute(
            "SELECT model_id, posterior FROM model_registry"
        ).fetchall()
        for ar in all_rows:
            other_mid = ar["model_id"]
            other_prior = ar["posterior"]
            if other_mid == model_id:
                other_likelihood = likelihood
            else:
                other_likelihood = 0.5  # uninformed for other models
            evidence += other_likelihood * other_prior

        evidence = max(evidence, 0.001)

        # Posterior update
        new_posterior = likelihood * posterior / evidence
        new_posterior = max(0.01, min(0.99, new_posterior))

        # Performance tracking
        brier = (p_gain - y_true) ** 2
        log_loss = -(y_true * math.log(max(p_gain, 0.001))
                     + (1 - y_true) * math.log(max(1 - p_gain, 0.001)))

        n_trades += 1
        if (y_true >= 0.5 and p_gain >= 0.5) or (y_true < 0.5 and p_gain < 0.5):
            n_wins += 1
        brier_accum += brier
        ll_accum += log_loss

        # State transition
        new_state = state
        if new_posterior >= P_ACTIVE_THRESHOLD:
            new_state = STATE_ACTIVE
        elif new_posterior >= P_DORMANT_THRESHOLD:
            new_state = STATE_DORMANT if state != STATE_RETIRED else state
        else:
            new_state = STATE_RETIRED

        # Log transition if state changed
        if new_state != state:
            self._log_transition(model_id, new_state, new_posterior,
                                 f"auto:{state}->{new_state} (posterior={new_posterior:.3f})")

        now = datetime.now().isoformat()
        conn.execute(
            """UPDATE model_registry SET posterior=?, state=?, n_trades=?, n_wins=?,
               brier_accum=?, log_loss_accum=?, last_active=?, updated_at=?
               WHERE model_id=?""",
            (new_posterior, new_state, n_trades, n_wins,
             brier_accum, ll_accum, now, now, model_id),
        )
        conn.commit()

        # Re-normalize posteriors across all models (BMA constraint)
        self._renormalize_posteriors()

    def _renormalize_posteriors(self) -> None:
        """Ensure all model posteriors sum to 1.0."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT model_id, posterior, state FROM model_registry"
        ).fetchall()
        # Only normalize across ACTIVE models
        active = [r for r in rows if r["state"] != STATE_RETIRED]
        total = sum(r["posterior"] for r in active) or 1.0
        for r in active:
            normalized = r["posterior"] / total
            conn.execute(
                "UPDATE model_registry SET posterior=? WHERE model_id=?",
                (normalized, r["model_id"]),
            )
        conn.commit()

    # ── State Transitions ──────────────────────────────────────────────

    def _log_transition(self, model_id: str, state: str, posterior: float, reason: str = "") -> None:
        """Log state transition to model_registry_history."""
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO model_registry_history (model_id, state, posterior, reason) VALUES (?,?,?,?)",
                (model_id, state, posterior, reason),
            )
            conn.commit()
        except Exception:
            pass  # table may not exist yet

    def activate(self, model_id: str, reason: str = "") -> None:
        conn = self._get_conn()
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE model_registry SET state=?, retirement_reason=?, last_active=?, updated_at=? WHERE model_id=?",
            (STATE_ACTIVE, "", now, now, model_id),
        )
        conn.commit()
        self._log_transition(model_id, STATE_ACTIVE, PRIOR_DEFAULT, reason)

    def suspend(self, model_id: str, reason: str = "") -> None:
        conn = self._get_conn()
        now = datetime.now().isoformat()
        row = conn.execute("SELECT posterior FROM model_registry WHERE model_id=?", (model_id,)).fetchone()
        posterior = row["posterior"] if row else PRIOR_DEFAULT
        conn.execute(
            "UPDATE model_registry SET state=?, retirement_reason=?, updated_at=? WHERE model_id=?",
            (STATE_DORMANT, reason, now, model_id),
        )
        conn.commit()
        self._log_transition(model_id, STATE_DORMANT, posterior, reason)

    def retire(self, model_id: str, reason: str = "") -> None:
        conn = self._get_conn()
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE model_registry SET state=?, retirement_reason=?, posterior=0.01, updated_at=? WHERE model_id=?",
            (STATE_RETIRED, reason, now, model_id),
        )
        conn.commit()
        self._renormalize_posteriors()
        self._log_transition(model_id, STATE_RETIRED, 0.01, reason)

    def revive(self, model_id: str, reason: str = "") -> None:
        conn = self._get_conn()
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE model_registry SET state=?, retirement_reason=?, posterior=?, updated_at=? WHERE model_id=?",
            (STATE_ACTIVE, "", PRIOR_DEFAULT, now, model_id),
        )
        conn.commit()
        self._renormalize_posteriors()
        self._log_transition(model_id, STATE_ACTIVE, PRIOR_DEFAULT, reason)

    # ── Query ──────────────────────────────────────────────────────────

    def get_all_models(self) -> List[Dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM model_registry").fetchall()
        result = []
        for r in rows:
            row = dict(r)
            row["regime_fit_dict"] = json.loads(r["regime_fit"] or "{}")
            row["win_rate"] = (row["n_wins"] / max(row["n_trades"], 1))
            row["avg_brier"] = (row["brier_accum"] / max(row["n_trades"], 1))
            row["avg_log_loss"] = (row["log_loss_accum"] / max(row["n_trades"], 1))
            result.append(row)
        return result

    def get_model(self, model_id: str) -> Optional[Dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM model_registry WHERE model_id=?", (model_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["regime_fit_dict"] = json.loads(row["regime_fit"] or "{}")
        d["win_rate"] = (d["n_wins"] / max(d["n_trades"], 1))
        d["avg_brier"] = (d["brier_accum"] / max(d["n_trades"], 1))
        d["avg_log_loss"] = (d["log_loss_accum"] / max(d["n_trades"], 1))
        return d

    def get_history(self, model_id: str, limit: int = 20) -> List[Dict]:
        """Return last N state transitions from model_registry_history if table exists."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM model_registry_history WHERE model_id=? ORDER BY changed_at DESC LIMIT ?",
                (model_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ── Stats ──────────────────────────────────────────────────────────

    def stats(self) -> Dict:
        rows = self.get_all_models()
        active = sum(1 for r in rows if r["state"] == STATE_ACTIVE)
        dormant = sum(1 for r in rows if r["state"] == STATE_DORMANT)
        retired = sum(1 for r in rows if r["state"] == STATE_RETIRED)
        return {
            "total": len(rows),
            "active": active,
            "dormant": dormant,
            "retired": retired,
            "avg_posterior": sum(r["posterior"] for r in rows) / max(len(rows), 1),
            "total_trades": sum(r["n_trades"] for r in rows),
            "avg_win_rate": sum(r["win_rate"] for r in rows) / max(len(rows), 1),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Report helpers
# ═══════════════════════════════════════════════════════════════════════════

def print_registry_report(registry: ModelRegistry, macro_state: str = "STABLE",
                          archetype: Optional[str] = None,
                          lang_mode: str = "full"):
    """Print full model registry report."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    models = registry.get_all_models()
    stats = registry.stats()

    print(f"\n  {'='*100}")
    print(f"  {_('MODEL REGISTRY')} — {_('Sprint 4')} | {_('Macro')}: {macro_state}"
          + (f" | {_('Archetype')}: {archetype}" if archetype else ""))
    print(f"  {_('Bayesian Model Averaging')} — {stats['active']} {_('active')}, "
          f"{stats['dormant']} {_('dormant')}, {stats['retired']} {_('retired')}")
    print(f"  {'='*100}")
    print(f"  {_('Model ID'):<16} {_('Posterior'):>10} {_('State'):>10}"
          f" {_('Regime Fit'):>11} {_('Brier'):>8} {_('LogLoss'):>8}"
          f" {_('WinRate'):>8} {_('Trades'):>7} {_('Focus')}")
    print(f"  {'─'*100}")

    for m in models:
        mid = m["model_id"]
        fit = registry.regime_fit_score(mid, macro_state, archetype)
        focus = ", ".join(MODEL_DEFINITIONS.get(mid, {}).get("focus_nodes", []))
        brier_s = f"{m['avg_brier']:.4f}" if m["n_trades"] > 0 else "N/A"
        ll_s = f"{m['avg_log_loss']:.4f}" if m["n_trades"] > 0 else "N/A"
        wr_s = f"{m['win_rate']:.1%}" if m["n_trades"] > 0 else "N/A"
        state_icon = {"ACTIVE": "🟢", "DORMANT": "🟡", "RETIRED": "🔴"}.get(m["state"], "⚪")
        print(f"  {mid:<16} {m['posterior']:>9.3f} {state_icon}{m['state']:>9}"
              f" {fit:>10.2f} {brier_s:>8} {ll_s:>8}"
              f" {wr_s:>8} {m['n_trades']:>7} {focus}")

    # BMA weights for current context
    bma_w = registry.bma_posterior(macro_state, archetype)
    print(f"\n  {_('BMA Weights')} ({macro_state}):")
    for mid, w in sorted(bma_w.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * int(w * 40) + "░" * (40 - int(w * 40))
        fit = registry.regime_fit_score(mid, macro_state, archetype)
        print(f"    {mid:<16} {w:>6.1%} {bar}  ({_('fit')}={fit:.2f})")

    best = registry.select_best(macro_state, archetype)
    print(f"\n  {_('Best model for context')}: {best['model_id']} "
          f"({_('fit')}={best['regime_fit_score']:.2f})")


def print_selection_report(registry: ModelRegistry, macro_state: str,
                           archetype: Optional[str] = None,
                           lang_mode: str = "full"):
    """Print model selection details."""
    try:
        from src.core.canonical_output_adapter import localize_label
    except Exception:
        def localize_label(l, m="full"): return l
    _ = lambda x: localize_label(x, lang_mode)

    bma_w = registry.bma_posterior(macro_state, archetype)
    best = registry.select_best(macro_state, archetype)

    print(f"\n  {'='*60}")
    print(f"  {_('MODEL SELECTION')} — {macro_state}"
          + (f" | {archetype}" if archetype else ""))
    print(f"  {'='*60}")
    print(f"  {_('Primary model')}: {best['model_id']} ({_('fit')}={best['regime_fit_score']:.2f})")
    print(f"  {_('BMA Ensemble')}:")
    for mid, w in sorted(bma_w.items(), key=lambda x: x[1], reverse=True):
        label = "← " + _("primary") if mid == best["model_id"] else ""
        print(f"    {mid:<20} {w:>6.1%} {label}")
