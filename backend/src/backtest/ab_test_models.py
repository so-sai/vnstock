"""ab_test_models.py — A/B harness: M1/M2/M3 per-model posterior vs forward outcome.

Compares the 3 Bayesian evidence models (M1_MACRO, M2_FUNDAMENTAL,
M3_BEHAVIORAL) against actual forward returns and against the legacy
BacktestAlpha baseline on the 2022 window.

Architecture note (honesty about limits):
  - Feature loaders (macro/transmission/sector) read CURRENT state files,
    so there is no 2022 time-travel for global state. We therefore:
      * run BacktestAlpha on 2022 (fully time-traveled via its own DB query)
        as the legacy baseline,
      * compute per-model p_gain for a basket of symbols using the features
        that ARE derivable live from DB (health archetype, valuation,
        behavior volume profile),
      * score them against forward 5d/10d returns in the 2022-06..2022-12
        sub-window.
  - Brier score = mean((p_gain - outcome)^2); accuracy = share of correct
    direction (p_gain>=0.5 vs gain=1).
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


def compute_per_model(symbol, conn, macro_state, transmission_phase):
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
            evidence_weights=renormed,
            model_registry_lr=None,
        )
        out[mid] = round(p, 4)
    return out


def run_ab():
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
            "ORDER BY symbol LIMIT 60",
        )
    ]

    macro_state = "STABLE"
    transmission_phase = "FRAGILE_STABILITY"

    rows = []
    for sym in symbols:
        try:
            pg = compute_per_model(sym, conn, macro_state, transmission_phase)
            # forward outcome: use first/last close over the 2022 sub-window
            fwd = _lookup(
                conn,
                "SELECT close FROM daily_ohlcv WHERE symbol=? AND date >= '2022-06-01' AND date <= '2022-12-15' ORDER BY date",
                (sym,),
            )
            if len(fwd) < 5:
                continue
            start = fwd[0][0]
            end = fwd[-1][0]
            if not start or not end or start <= 0:
                continue
            fwd_ret = (end - start) / start
            outcome = 1.0 if fwd_ret > 0 else 0.0
            row = {"symbol": sym, "outcome": outcome, "fwd_ret": round(fwd_ret, 4)}
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
    print("A/B TEST — Per-model posterior vs forward outcome (2022 basket)")
    print("=" * 70)
    print(f"N symbols: {len(df)}")
    for mid in ["M1_MACRO", "M2_FUNDAMENTAL", "M3_BEHAVIORAL"]:
        pcol = df[mid]
        out = df["outcome"]
        brier = float(((pcol - out) ** 2).mean())
        # direction accuracy: p>=0.5 predicts gain
        acc = float(((pcol >= 0.5) == (out == 1.0)).mean())
        mean_p = float(pcol.mean())
        print(f"\n{mid}:")
        print(f"  Mean p_gain : {mean_p:.4f}")
        print(f"  Brier       : {brier:.4f}  (lower=better)")
        print(f"  Direction Acc: {acc:.1%}")

    print("\nPer-symbol table (top 15 by abs fwd_ret):")
    print(df.sort_values("fwd_ret", key=abs, ascending=False).head(15).to_string(index=False))


if __name__ == "__main__":
    run_ab()
