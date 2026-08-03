"""ab_test_models.py — A/B harness: M1/M2/M3 per-model posterior vs forward outcome.

Compares the 3 Bayesian evidence models (M1_MACRO, M2_FUNDAMENTAL,
M3_BEHAVIORAL) against actual forward returns and against the legacy
BacktestAlpha baseline on the 2022 window.

Point-in-time design (look-ahead-safe):
  - M1_MACRO global state is classified per sampled date via
    MacroStateClassifier.classify(target_date), which reads the
    historical macro_history feed (DXY, USD_VND, US10Y, WTI...) — no
    look-ahead.
  - Per-symbol features (health archetype, valuation, behavior volume
    profile) are read from current DB (fundamental/per-symbol state has
    limited 2022 history for small caps).
  - Transmission falls back to neutral (interbank data starts 2023).
  - Forward outcome = next ~10 day close return, strictly after the
    decision date.
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def _hydrate_path():
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return current


ROOT = _hydrate_path()
for p in (ROOT / "backend" / "src", ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import sqlite3

import pandas as pd

from src.config import DATA_DIR
from src.governor.company_state import (
    L3ValuationLoader,
    L4BehaviorLoader,
    PerceptionLoader,
    compute_gain_probability,
)


def _lookup(conn, q, params=()):
    return conn.execute(q, params).fetchall()


def compute_per_model(symbol, conn, macro_state, transmission_phase, macro_entropy=0.0):
    """Compute p_gain for M1/M2/M3 with DB-derivable features."""
    perception = PerceptionLoader()
    behavior = L4BehaviorLoader()
    val_eng = L3ValuationLoader()

    health = perception.load_health(symbol)
    val = val_eng.score_valuation(symbol)
    beh = behavior.score_behavior(symbol)
    sector_phase = perception.load_sector().get("top_phase", "NEUTRAL")

    health_arch = health.get("archetype", "STEADY_EARNER")
    val_zone = val.get("overall_zone", "FAIR")
    beh_pos = beh.get("position", "IN_VA")

    model_weights_map = {
        "M1_MACRO": {"macro": 0.20, "transmission": 0.13, "sector": 0.10},
        "M2_FUNDAMENTAL": {"health": 0.11, "capital_allocation": 0.13, "valuation": 0.09},
        "M3_BEHAVIORAL": {"behavior": 0.09},
    }
    out = {}
    for mid, mw in model_weights_map.items():
        total_w = sum(mw.values())
        renormed = {k: v / total_w for k, v in mw.items()}
        p, _, _ = compute_gain_probability(
            macro_state=macro_state,
            transmission_phase=transmission_phase,
            sector_phase=sector_phase,
            health_archetype=health_arch,
            valuation_zone=val_zone,
            behavior_position=beh_pos,
            capital_allocation="TRANSITIONAL",
            macro_entropy=macro_entropy,
            evidence_weights=renormed,
            model_registry_lr=None,
        )
        out[mid] = round(p, 4)
    return out


def run_ab():
    from src.core.macro.macro_state_classifier import MacroStateClassifier
    from src.engine.backtest_engine import BacktestAlpha

    print("=" * 70)
    print("A/B TEST — Legacy BacktestAlpha baseline on 2022")
    print("=" * 70)
    engine = BacktestAlpha(rebalance_freq=10, initial_capital=100_000_000, fee=0.01)
    engine.run(start_date="2022-01-01", top_n=5)
    print()

    # --- Basket of symbols + forward outcomes from DB ---
    db_path = Path(DATA_DIR) / "screener_cache.db"
    conn = sqlite3.connect(str(db_path), timeout=60)

    symbols = [
        r[0]
        for r in _lookup(
            conn,
            "SELECT DISTINCT symbol FROM daily_ohlcv "
            "WHERE symbol NOT IN ('VNINDEX','VN30') AND date >= '2022-06-01' "
            "ORDER BY symbol LIMIT 40",
        )
    ]

    # Point-in-time dates to sample across the 2022 sub-window (spaced ~3w apart)
    sample_dates = [
        "2022-06-06",
        "2022-06-27",
        "2022-07-18",
        "2022-08-08",
        "2022-08-29",
        "2022-09-19",
        "2022-10-10",
        "2022-10-31",
        "2022-11-21",
        "2022-12-12",
    ]

    classifier = MacroStateClassifier()
    rows = []

    for d in sample_dates:
        # Point-in-time macro state at date d (no look-ahead)
        ms = classifier.classify(target_date=d)
        macro_state = ms.macro_state
        macro_entropy = float(ms.entropy or 0.0)
        transmission_phase = "FRAGILE_STABILITY"  # no 2022 interbank data

        # fetch next 5 trading days closes for forward outcome (point-in-time fwd)
        fwd_map = {}
        for r in _lookup(
            conn,
            "SELECT symbol, date, close FROM daily_ohlcv WHERE date > ? AND date <= date(?, '+10 days')",
            (d, d),
        ):
            fwd_map.setdefault(r[0], []).append(r[2])

        for sym in symbols:
            try:
                fwd = [x for x in fwd_map.get(sym, []) if x]
                if len(fwd) < 2:
                    continue
                fwd_ret = (fwd[-1] - fwd[0]) / fwd[0]
                outcome = 1.0 if fwd_ret > 0 else 0.0
                pg = compute_per_model(sym, conn, macro_state, transmission_phase, macro_entropy)
                row = {
                    "date": d,
                    "symbol": sym,
                    "outcome": outcome,
                    "fwd_ret": round(fwd_ret, 4),
                    "macro": macro_state,
                }
                row.update(pg)
                rows.append(row)
            except Exception:
                pass

    conn.close()

    if not rows:
        print("No data for A/B — check DB.")
        return

    df = pd.DataFrame(rows)
    print("=" * 70)
    print("A/B TEST — Point-in-time per-model posterior vs forward outcome (2022)")
    print("=" * 70)
    print(f"N (date,symbol) observations: {len(df)}")
    print("\nMacro state distribution across sampled 2022 dates:")
    print(df["macro"].value_counts().to_string())
    for mid in ["M1_MACRO", "M2_FUNDAMENTAL", "M3_BEHAVIORAL"]:
        pcol = df[mid]
        out = df["outcome"]
        brier = float(((pcol - out) ** 2).mean())
        acc = float(((pcol >= 0.5) == (out == 1.0)).mean())
        mean_p = float(pcol.mean())
        std_p = float(pcol.std())
        print(f"\n{mid}:")
        print(f"  Mean p_gain      : {mean_p:.4f}   (discrimination std: {std_p:.4f})")
        print(f"  Brier            : {brier:.4f}  (lower=better)")
        print(f"  Direction Acc    : {acc:.1%}")

    print("\nSample (date, symbol, macro, p_gain, fwd_ret) — note p_gain now varies by date:")
    print(df.sort_values("fwd_ret", key=abs, ascending=False).head(15).to_string(index=False))


if __name__ == "__main__":
    run_ab()
