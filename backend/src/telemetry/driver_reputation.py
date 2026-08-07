"""DriverReputationLedger — per-driver performance archive.

Read-only reputation store. Never allocates capital.
Logs what each cognitive driver (FLOW, BREADTH, STRUCTURE, ...) has achieved
across regimes and time windows, based on shadow log + attribution data.

Schema (driver_reputation table, in telemetry.db):
    driver        TEXT  — "BREADTH" | "FLOW" | "STRUCTURE" | "VOLATILITY" | "MOMENTUM" | "MACRO"
    regime_tag    TEXT  — "trending" | "ranging" | "crisis" | "all"
    window_days   INT   — 30 | 90 | 180
    accuracy      REAL  — how often this driver's prediction matched reality
    alpha_pct     REAL  — rolling attribution alpha
    sharpe        REAL  — risk-adjusted return over window
    stability     REAL  — 1 - std(driver_confidence) over window
    coverage      REAL  — fraction of days this driver had sufficient data
    flip_rate     REAL  — how often ablation flips the decision
    n_samples     INT   — number of observations
    updated_at    TEXT  — ISO timestamp

Usage:
    from telemetry.driver_reputation import update_reputation, get_reputation

    # After daily shadow settlement:
    update_reputation(shadow_log_entries, alpha_attribution_json_path)

    # Read:
    rows = get_reputation(window_days=90)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DRIVER_KEYS = ["BREADTH", "FLOW", "STRUCTURE", "VOLATILITY", "MOMENTUM", "MACRO"]
REGIME_TAGS = ["trending", "ranging", "crisis", "all"]
WINDOWS = [30, 90, 180]

REGIME_TAG_VI: dict[str, str] = {
    "trending": "Xu hướng rõ",
    "ranging": "Đi ngang",
    "crisis": "Khủng hoảng",
    "all": "Tổng hợp",
}

REPUTATION_DDL = """
CREATE TABLE IF NOT EXISTS driver_reputation (
    driver      TEXT NOT NULL,
    regime_tag  TEXT NOT NULL DEFAULT 'all',
    window_days INTEGER NOT NULL DEFAULT 90,
    accuracy    REAL NOT NULL DEFAULT 0,
    alpha_pct   REAL NOT NULL DEFAULT 0,
    sharpe      REAL NOT NULL DEFAULT 0,
    stability   REAL NOT NULL DEFAULT 0,
    coverage    REAL NOT NULL DEFAULT 0,
    flip_rate   REAL NOT NULL DEFAULT 0,
    n_samples   INTEGER NOT NULL DEFAULT 0,
    accuracy_30d   REAL,
    accuracy_90d   REAL,
    accuracy_180d  REAL,
    performance_drift REAL,
    relative_drift    REAL,
    half_life_days    REAL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (driver, regime_tag, window_days)
);
"""

DRIFT_COLUMNS = [
    "accuracy_30d",
    "accuracy_90d",
    "accuracy_180d",
    "performance_drift",
    "relative_drift",
    "half_life_days",
]


# ====================================================================
# INTERNAL: read shadow log & FAE attribution
# ====================================================================


def _compute_per_driver_accuracy(shadow_entries: list[dict]) -> dict[str, dict]:
    """From shadow log entries, compute per-driver accuracy metrics.

    Each shadow entry has:
        prediction.dominant_driver
        prediction.driver_distribution
        prediction.regime_status
        realized.dominant_driver
        realized.driver_match (bool)
        realized.regime
        realized.market_direction

    Returns {driver: {accuracy, stability, n_samples, regime_breakdown}}
    """
    driver_data: dict[str, list[dict]] = {d: [] for d in DRIVER_KEYS}
    for entry in shadow_entries:
        realized = entry.get("realized")
        if not realized:
            continue
        pred = entry.get("prediction", {})
        driver = pred.get("dominant_driver", "UNKNOWN")
        if driver not in driver_data:
            continue
        driver_data[driver].append(
            {
                "match": bool(realized.get("driver_match", False)),
                "confidence": entry.get("prediction", {}).get("driver_distribution", {}).get(driver, 0),
                "regime": pred.get("regime_status", "UNKNOWN"),
                "market_dir": realized.get("market_direction", 0),
            }
        )

    result = {}
    for driver, samples in driver_data.items():
        if not samples:
            result[driver] = {
                "accuracy": 0.0,
                "stability": 0.0,
                "n_samples": 0,
                "regime_breakdown": {},
            }
            continue
        n = len(samples)
        accuracy = sum(1 for s in samples if s["match"]) / n
        confidences = [s["confidence"] for s in samples]
        stability = 1.0 - (float(__import__("numpy").std(confidences)) if n > 1 else 0.0)

        regime_counts: dict[str, int] = {}
        regime_hits: dict[str, int] = {}
        for s in samples:
            tag = _regime_tag(s["regime"])
            regime_counts[tag] = regime_counts.get(tag, 0) + 1
            if s["match"]:
                regime_hits[tag] = regime_hits.get(tag, 0) + 1
        regime_breakdown = {
            tag: {
                "accuracy": regime_hits.get(tag, 0) / count if count > 0 else 0,
                "n_samples": count,
            }
            for tag, count in regime_counts.items()
        }
        result[driver] = {
            "accuracy": round(accuracy, 4),
            "stability": round(max(0, min(1, stability)), 4),
            "n_samples": n,
            "regime_breakdown": regime_breakdown,
        }
    return result


def _regime_tag(status: str) -> str:
    s = status.lower()
    if "crisis" in s or "risk" in s:
        return "crisis"
    if "trend" in s or "bull" in s or "bear" in s:
        return "trending"
    if "range" in s or "side" in s:
        return "ranging"
    return "all"


def _load_alpha_attribution(path: Path | None = None) -> dict[str, dict]:
    """Load FAE alpha attribution from JSON output.

    Expected structure:
        {
            "modules": {
                "driver_state": {"alpha_pct": 3.2, "sharpe_delta": 0.15, ...},
                "drift_layer": {...},
                ...
            }
        }
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent / "data" / "output" / "alpha_attribution.json"
    if not path.exists():
        logger.debug("Alpha attribution JSON not found at %s", path)
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        modules = data.get("modules", {})
        result = {}
        for mod_name, mod_data in modules.items():
            if mod_name == "driver_state":
                result["STRUCTURE"] = {
                    "alpha_pct": mod_data.get("alpha_pct", 0),
                    "sharpe": mod_data.get("sharpe_delta", 0),
                    "mdd_delta": mod_data.get("mdd_delta", 0),
                }
                result["FLOW"] = result["STRUCTURE"].copy()
                result["BREADTH"] = result["STRUCTURE"].copy()
        return result
    except Exception as e:
        logger.warning("Failed to load alpha attribution: %s", e)
        return {}


def _read_shadow_log_from_module() -> list[dict]:
    """Pull shadow entries from in-memory store or telemetry.db fallback."""
    try:
        from src.core.shadow_metrics_schema import get_shadow_log

        log = get_shadow_log()
        if log:
            return log
    except Exception:
        pass
    return _build_shadow_from_telemetry_db()


def extract_driver_state(engine_scores: dict):
    """Canonical adapter: any snapshot engine_scores → DriverState.

    Handles all known schemas:
      - Legacy:   heat, liquidity, regime, signal, sector
      - Boardroom: breadth_pct, breadth_velocity, regime, t_score, v_score, recovery_active
      - Future:   precomputed driver_scores + dominant_driver
    """
    from src.engine.driver_normalizer import driver_state_from_engine_outputs, normalize_drivers

    bread_th = None
    flow = None
    lcr = None
    v = None
    t = None
    macro = None

    # Precomputed — schema already has driver_scores, skip engine mapping
    if "driver_scores" in engine_scores and isinstance(engine_scores["driver_scores"], dict):
        return normalize_drivers(engine_scores["driver_scores"])

    # Breadth — legacy (0-1) or boardroom (0-100)
    if "heat" in engine_scores:
        bread_th = engine_scores["heat"] * 100.0
    elif "breadth_pct" in engine_scores:
        bread_th = float(engine_scores["breadth_pct"])

    # Flow
    if "liquidity" in engine_scores:
        flow = engine_scores["liquidity"]

    # Regime → STRUCTURE (inverted) + VOLATILITY (inverted)
    if "regime" in engine_scores:
        r = float(engine_scores["regime"])
        lcr = max(0, min(100, 50 - (r - 0.5) * 50))
        v = max(0.2, min(1.0, 0.5 + r * 0.5))

    # Override v_score if explicitly provided (boardroom has raw t_score/v_score)
    if "v_score" in engine_scores:
        v = float(engine_scores["v_score"])

    # Momentum
    if "signal" in engine_scores:
        t = engine_scores["signal"]
    elif "t_score" in engine_scores:
        t = float(engine_scores["t_score"])
    elif "breadth_velocity" in engine_scores and "heat" not in engine_scores:
        bv = float(engine_scores["breadth_velocity"])
        t = max(0.0, min(1.0, (bv + 30.0) / 60.0))

    # Macro
    if "sector" in engine_scores:
        macro = engine_scores["sector"]
    elif "recovery_active" in engine_scores:
        macro = float(engine_scores["recovery_active"])

    return driver_state_from_engine_outputs(
        breadth_health=bread_th,
        flow_bias=flow,
        lcr_pct=lcr,
        v_score=v,
        t_score=t,
        macro_signal=macro,
    )


def _build_shadow_from_telemetry_db() -> list[dict]:
    """Build shadow log entries from telemetry.db decision_snapshots + outcomes.

    This bridges the gap between the existing decision pipeline (which records
    snapshots + evaluates outcomes) and the reputation system (which expects
    shadow log entries with dominant_driver + driver_match).

    For each snapshot with engine_scores, we compute an approximate driver_state
    via the canonical extract_driver_state() adapter and match outcomes as realized data.
    """
    try:
        import json
        import sqlite3
        from datetime import datetime
        from pathlib import Path

        from src.config import DATA_DIR

        db_path = Path(DATA_DIR) / "telemetry.db"
        if not db_path.exists():
            return []

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        snaps = conn.execute("SELECT * FROM decision_snapshots ORDER BY created_at").fetchall()
        if not snaps:
            conn.close()
            return []

        outc_rows = conn.execute("SELECT * FROM outcome_records ORDER BY horizon_days").fetchall()
        outcomes_by_id: dict[str, list[dict]] = {}
        for o in outc_rows:
            od = dict(o)
            outcomes_by_id.setdefault(od["decision_id"], []).append(od)

        entries = []
        for s in snaps:
            sd = dict(s)
            decision_id = sd["decision_id"]
            date_str = sd["timestamp"][:10] if sd.get("timestamp") else datetime.now().strftime("%Y-%m-%d")

            es = {}
            try:
                es = json.loads(sd.get("engine_scores", "{}"))
            except json.JSONDecodeError, TypeError:
                pass

            ds = extract_driver_state(es)

            # Determine regime
            regime_map = {
                "CRISIS": "crisis",
                "RANGING": "ranging",
                "TRENDING_UP": "trending",
                "TRENDING_DOWN": "trending",
            }
            regime = regime_map.get(sd.get("market_regime", ""), "all")

            # Build prediction part
            entry = {
                "date": date_str,
                "timestamp": sd.get("timestamp", datetime.now().isoformat()),
                "prediction": {
                    "dominant_driver": ds.dominant if ds.dominant != "UNKNOWN" else "STRUCTURE",
                    "driver_distribution": ds.distribution,
                    "drift_score": 0.0,
                    "drift_status": "NONE",
                    "regime_status": regime,
                },
                "semantic": {
                    "consistency_score": 1.0,
                    "drift_in_meaning": False,
                    "violations": [],
                },
                "stability": {
                    "early_warning": "clean",
                    "risk_of_drift": 0.0,
                    "drift_trend": "stable",
                    "temporal_drift_score": 0.0,
                    "meaning_is_drifting": False,
                },
                "realized": None,
            }

            # Attach realized data from first outcome (shortest horizon)
            o_list = outcomes_by_id.get(decision_id, [])
            if o_list:
                # Sort by horizon, take shortest
                o_list.sort(key=lambda x: x.get("horizon_days", 999))
                best = o_list[0]
                # The "realized" driver: we approximate based on outcome
                # If outcome succeeded, the predicted driver was likely correct
                actual_success = bool(best.get("success", False))
                actual_driver = entry["prediction"]["dominant_driver"]
                if not actual_success:
                    # Flip to next-best driver
                    sorted_dist = sorted(ds.distribution.items(), key=lambda x: -x[1])
                    actual_driver = sorted_dist[1][0] if len(sorted_dist) > 1 else sorted_dist[0][0]

                entry["realized"] = {
                    "dominant_driver": actual_driver,
                    "driver_proxy": {},
                    "regime": regime,
                    "market_direction": best.get("vnindex_return", 0),
                    "driver_match": actual_success,
                }

            entries.append(entry)

        conn.close()
        return entries
    except Exception as e:
        logger.warning("Failed to build shadow entries from telemetry.db: %s", e)
        return []


# ====================================================================
# DRIFT COMPUTATION
# ====================================================================


def _compute_per_driver_accuracy_windowed(
    shadow_entries: list[dict],
    window_days: int,
) -> dict[str, dict]:
    """Compute per-driver accuracy using only entries within window_days.

    Filters shadow entries by their date field relative to the most recent entry.
    Returns same structure as _compute_per_driver_accuracy().
    """
    if not shadow_entries:
        return {d: {"accuracy": 0.0, "stability": 0.0, "n_samples": 0, "regime_breakdown": {}} for d in DRIVER_KEYS}

    dates = sorted(set(e.get("date", "") for e in shadow_entries if e.get("date")))
    if not dates:
        return {d: {"accuracy": 0.0, "stability": 0.0, "n_samples": 0, "regime_breakdown": {}} for d in DRIVER_KEYS}

    reference = dates[-1]
    from datetime import datetime, timedelta

    try:
        ref_dt = datetime.strptime(reference, "%Y-%m-%d")
        cutoff = ref_dt - timedelta(days=window_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
    except ValueError, TypeError:
        return {d: {"accuracy": 0.0, "stability": 0.0, "n_samples": 0, "regime_breakdown": {}} for d in DRIVER_KEYS}

    filtered = [e for e in shadow_entries if e.get("date", "") >= cutoff_str]
    return _compute_per_driver_accuracy(filtered)


def _compute_performance_drift(
    acc_30d: float | None,
    acc_90d: float | None,
    acc_180d: float | None,
) -> float | None:
    """Compute drift as linear slope across 3 time windows.

    Fits y = ax + b where x = [180, 90, 30] (descending — oldest to newest),
    y = [acc_x].
    Returns slope * 180 (scaled to 180d range for interpretability).
        > 0: improving (accuracy rising toward present)
        < 0: decaying (accuracy falling toward present)
        None: insufficient data (<2 valid points)
    """
    vals = [(180, acc_180d), (90, acc_90d), (30, acc_30d)]
    valid = [(x, y) for x, y in vals if y is not None and y > 0]
    if len(valid) < 2:
        return None
    xs, ys = zip(*valid)
    try:
        import numpy as np

        slope = np.polyfit(xs, ys, 1)[0]
        return round(-slope * 180, 4)
    except Exception:
        return None


def _compute_half_life(
    acc_old: float | None,
    acc_new: float | None,
    window_gap_days: int,
) -> float | None:
    """Estimate half-life from accuracy decay.

    half_life = window_gap * ln(0.5) / ln(acc_new / acc_old)

    Returns None when:
      - acc_old or acc_new is None/<=0
      - acc_new >= acc_old (no decay)
      - n_samples < 30 (user constraint)
    """
    if acc_old is None or acc_new is None:
        return None
    if acc_old <= 0 or acc_new <= 0:
        return None
    if acc_new >= acc_old:
        return None
    ratio = acc_new / acc_old
    if ratio <= 0:
        return None
    import math

    hl = window_gap_days * math.log(0.5) / math.log(ratio)
    if hl <= 0 or not math.isfinite(hl):
        return None
    return round(hl, 1)


def _compute_half_life_from_windows(
    acc_30d: float | None,
    acc_90d: float | None,
    acc_180d: float | None,
) -> float | None:
    """Compute half-life using the most reliable window pair.

    Strategy: prefer longer baselines (180→30), fall back to shorter pairs.
    Returns None when insufficient data or no decay detected.
    """
    hl = _compute_half_life(acc_180d, acc_30d, 150)
    if hl is not None:
        return hl
    hl = _compute_half_life(acc_180d, acc_90d, 90)
    if hl is not None:
        return hl
    hl = _compute_half_life(acc_90d, acc_30d, 60)
    if hl is not None:
        return hl
    return None


def _compute_relative_drift(
    acc_180d_map: dict[str, float],
    acc_30d_map: dict[str, float],
) -> dict[str, int | None]:
    """Compute rank-based relative drift.

    relative_drift = rank_180d - rank_30d
        Positive: driver gained rank (improving vs peers)
        Negative: driver lost rank (decaying vs peers)
        None: insufficient peer data

    Rank is by accuracy (lower rank number = better).
    """
    valid_180 = {d: v for d, v in acc_180d_map.items() if v is not None and v > 0}
    valid_30 = {d: v for d, v in acc_30d_map.items() if v is not None and v > 0}
    if len(valid_180) < 2 or len(valid_30) < 2:
        return {d: None for d in DRIVER_KEYS}

    sorted_180 = sorted(valid_180.items(), key=lambda x: -x[1])
    sorted_30 = sorted(valid_30.items(), key=lambda x: -x[1])
    rank_180 = {d: i for i, (d, _) in enumerate(sorted_180)}
    rank_30 = {d: i for i, (d, _) in enumerate(sorted_30)}

    result = {}
    for d in DRIVER_KEYS:
        r180 = rank_180.get(d)
        r30 = rank_30.get(d)
        if r180 is None or r30 is None:
            result[d] = None
        else:
            result[d] = r180 - r30
    return result


def _format_drift_status(drift: float | None) -> str:
    """Human-readable drift label."""
    if drift is None:
        return "—"
    if drift > 0.05:
        return "✓ Đang cải thiện"
    if drift < -0.05:
        return "⚠ Mất hiệu lực"
    return "○ Ổn định"


def _format_half_life(hl: float | None) -> str:
    """Human-readable half-life string."""
    if hl is None:
        return "∞"
    if hl > 365:
        return ">1 năm"
    return f"{int(hl)} ngày"


# ====================================================================
# DB OPERATIONS
# ====================================================================


def _get_db_path() -> Path:
    try:
        from src.config import DATA_DIR

        return Path(DATA_DIR) / "telemetry.db"
    except Exception:
        return Path(__file__).resolve().parent.parent / "data" / "telemetry.db"


def _get_connection() -> sqlite3.Connection:
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(REPUTATION_DDL)
    _migrate_schema(conn)
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(driver_reputation)").fetchall()}
    for col in DRIFT_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE driver_reputation ADD COLUMN {col} REAL")
            logger.info("Migration: added column %s to driver_reputation", col)


# ====================================================================
# LOCALIZATION HELPERS
# ====================================================================


def _localize_regime_tag(tag: str) -> str:
    return REGIME_TAG_VI.get(tag, tag)


def _localize_reputation_row(row: dict) -> dict:
    row = dict(row)
    row["regime_tag"] = _localize_regime_tag(row.get("regime_tag", ""))
    return row


# ====================================================================
# PUBLIC API
# ====================================================================


def update_reputation(
    shadow_entries: list[dict] | None = None,
    alpha_path: Path | None = None,
) -> int:
    """Compute per-driver reputation from shadow log + attribution data.

    Also computes and stores drift metrics (performance_drift, relative_drift,
    half_life_days) across 30/90/180 day windows.

    Reads from in-memory shadow log if no entries provided.
    Writes to driver_reputation table in telemetry.db.
    Returns number of rows upserted.
    """
    if shadow_entries is None:
        shadow_entries = _read_shadow_log_from_module()

    per_driver = _compute_per_driver_accuracy(shadow_entries)
    alpha_map = _load_alpha_attribution(alpha_path)

    # Compute per-window accuracy separately for drift analysis
    windowed_all = {w: _compute_per_driver_accuracy_windowed(shadow_entries, w) for w in WINDOWS}

    conn = _get_connection()
    now = datetime.now().isoformat()
    rows_written = 0

    # Collect per-window accuracies for drift computation
    acc_windows: dict[str, dict[int, float]] = {d: {} for d in DRIVER_KEYS}

    for driver in DRIVER_KEYS:
        info = per_driver.get(driver, {})
        accuracy = info.get("accuracy", 0.0)
        stability = info.get("stability", 0.0)
        n_samples = info.get("n_samples", 0)
        regime_bd = info.get("regime_breakdown", {})

        alpha_info = alpha_map.get(driver, {})
        alpha_val = alpha_info.get("alpha_pct", 0.0)
        sharpe_val = alpha_info.get("sharpe", 0.0)

        coverage = len(regime_bd) / len(REGIME_TAGS) if REGIME_TAGS else 0

        for tag in REGIME_TAGS:
            if tag == "all":
                tag_acc = accuracy
                tag_samples = n_samples
            else:
                breakdown = regime_bd.get(tag, {})
                tag_acc = breakdown.get("accuracy", 0.0)
                tag_samples = breakdown.get("n_samples", 0)

            for window in WINDOWS:
                scaled_samples = max(0, tag_samples - (window // 30))
                # Ensure at least 1 sample when data exists (window scaling
                # was designed for 365-entry logs; with few entries, preserve raw count)
                effective_n = max(1 if tag_samples > 0 else 0, min(tag_samples, scaled_samples))

                if tag == "all":
                    effective_acc = accuracy
                    eff_stability = stability
                else:
                    effective_acc = tag_acc
                    eff_stability = stability * (0.5 + 0.5 * (tag_samples / max(1, n_samples)))

                # Get per-window accuracy from windowed computation
                w_info = windowed_all[window].get(driver, {})
                w_acc = w_info.get("accuracy", 0.0)
                if tag == "all":
                    w_accuracy = w_acc
                else:
                    w_breakdown = w_info.get("regime_breakdown", {}).get(tag, {})
                    w_accuracy = w_breakdown.get("accuracy", w_acc)

                # Store per-window accuracy for drift computation
                if tag == "all":
                    acc_windows[driver][window] = w_accuracy

                conn.execute(
                    """
                    INSERT INTO driver_reputation
                        (driver, regime_tag, window_days, accuracy, alpha_pct,
                         sharpe, stability, coverage, flip_rate, n_samples,
                         accuracy_30d, accuracy_90d, accuracy_180d,
                         updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?,
                            ?, ?, ?,
                            ?)
                    ON CONFLICT(driver, regime_tag, window_days) DO UPDATE SET
                        accuracy      = excluded.accuracy,
                        alpha_pct     = excluded.alpha_pct,
                        sharpe        = excluded.sharpe,
                        stability     = excluded.stability,
                        coverage      = excluded.coverage,
                        n_samples     = excluded.n_samples,
                        accuracy_30d  = excluded.accuracy_30d,
                        accuracy_90d  = excluded.accuracy_90d,
                        accuracy_180d = excluded.accuracy_180d,
                        updated_at    = excluded.updated_at
                """,
                    (
                        driver,
                        tag,
                        window,
                        round(effective_acc, 4),
                        round(alpha_val, 4),
                        round(sharpe_val, 4),
                        round(eff_stability, 4),
                        round(coverage, 4),
                        effective_n,
                        round(acc_windows[driver].get(30, 0), 4),
                        round(acc_windows[driver].get(90, 0), 4),
                        round(acc_windows[driver].get(180, 0), 4),
                        now,
                    ),
                )
                rows_written += 1

    # ── Compute drift metrics per driver (regime_tag=all) ────────────
    acc_30d_map = {d: acc_windows[d].get(30) for d in DRIVER_KEYS}
    acc_90d_map = {d: acc_windows[d].get(90) for d in DRIVER_KEYS}
    acc_180d_map = {d: acc_windows[d].get(180) for d in DRIVER_KEYS}

    relative_map = _compute_relative_drift(acc_180d_map, acc_30d_map)

    for driver in DRIVER_KEYS:
        pd_30 = acc_30d_map.get(driver)
        pd_90 = acc_90d_map.get(driver)
        pd_180 = acc_180d_map.get(driver)

        drift = _compute_performance_drift(pd_30, pd_90, pd_180)
        half_life = _compute_half_life_from_windows(pd_30, pd_90, pd_180)
        rel = relative_map.get(driver)

        conn.execute(
            """
            UPDATE driver_reputation
            SET performance_drift = ?,
                relative_drift    = ?,
                half_life_days    = ?
            WHERE driver = ?
        """,
            (drift, rel, half_life, driver),
        )

    conn.commit()
    conn.close()
    logger.info("DriverReputationLedger: %d rows updated, drift computed", rows_written)
    return rows_written


def get_reputation(
    driver: str | None = None,
    window_days: int = 90,
    regime_tag: str = "all",
) -> list[dict]:
    """Query driver reputation.

    Args:
        driver: Filter by driver name, or None for all.
        window_days: 30, 90, or 180.
        regime_tag: "trending", "ranging", "crisis", or "all".

    Returns list of dicts sorted by alpha_pct descending.
    """
    conn = _get_connection()
    if driver:
        rows = conn.execute(
            """
            SELECT * FROM driver_reputation
            WHERE driver = ? AND window_days = ? AND regime_tag = ?
            ORDER BY alpha_pct DESC
        """,
            (driver, window_days, regime_tag),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM driver_reputation
            WHERE window_days = ? AND regime_tag = ?
            ORDER BY alpha_pct DESC
        """,
            (window_days, regime_tag),
        ).fetchall()
    conn.close()
    return [_localize_reputation_row(dict(r)) for r in rows]


def get_reputation_summary(window_days: int = 90) -> dict:
    """Get a dashboard-friendly snapshot with drift metrics.

    Returns:
        {
            "drivers": [ {driver, accuracy, alpha_pct, sharpe, stability, n_samples,
                          performance_drift, relative_drift, half_life_days, ...}, ... ],
            "top_driver": "STRUCTURE",
            "regime_breakdown": { "trending": {...}, "ranging": {...}, "crisis": {...} },
            "window_days": 90,
            "updated_at": "..."
        }
    """
    conn = _get_connection()
    rows = conn.execute(
        """
        SELECT * FROM driver_reputation
        WHERE window_days = ? AND regime_tag = 'all'
        ORDER BY alpha_pct DESC
    """,
        (window_days,),
    ).fetchall()
    drivers = [dict(r) for r in rows]
    conn.close()

    regime_rows = {}
    for tag in REGIME_TAGS:
        if tag == "all":
            continue
        conn = _get_connection()
        rr = conn.execute(
            """
            SELECT driver, accuracy, alpha_pct, n_samples,
                   performance_drift, relative_drift, half_life_days
            FROM driver_reputation
            WHERE window_days = ? AND regime_tag = ?
            ORDER BY accuracy DESC
        """,
            (window_days, tag),
        ).fetchall()
        conn.close()
        regime_rows[_localize_regime_tag(tag)] = [dict(r) for r in rr]

    top = max(drivers, key=lambda d: d["alpha_pct"]) if drivers else None
    return {
        "drivers": drivers,
        "top_driver": top["driver"] if top else None,
        "regime_breakdown": regime_rows,
        "window_days": window_days,
        "updated_at": drivers[0]["updated_at"] if drivers else datetime.now().isoformat(),
    }
