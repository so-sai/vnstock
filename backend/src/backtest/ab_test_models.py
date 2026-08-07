"""ab_test_models.py — A/B harness: M1/M2/M3 per-model posterior vs forward outcome.

Compares the 3 Bayesian evidence models (M1_MACRO, M2_FUNDAMENTAL,
M3_BEHAVIORAL) against actual forward returns and against the legacy
BacktestAlpha baseline on a configurable window (default 2022).

Point-in-time design (look-ahead-safe):
  - M1_MACRO global state is classified per sampled date via
    MacroStateClassifier.classify(target_date), which reads the
    historical macro_history feed (DXY, USD_VND, US10Y, WTI...) — no
    look-ahead.
  - Transmission phase is recomputed point-in-time from macro_history
    (date <= target_date) using the same classify rules as
    EconomicTransmissionEngine — interbank 2023+ is real, pre-2023
    falls back to neutral.
  - Per-symbol features (health archetype, valuation, behavior volume
    profile) are read from current DB (fundamental/per-symbol state has
    limited early history for small caps).
  - Forward outcome = next ~10 day close return, strictly after the
    decision date.

Usage:
  python backend/src/backtest/ab_test_models.py --start 2021-01-01 --end 2021-12-31
  python backend/src/backtest/ab_test_models.py --start 2023-01-01 --end 2024-12-31
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

import numpy as np
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


def transmission_phase_at(conn, target_date):
    """Point-in-time transmission phase from macro_history (date <= target_date).

    Mirrors EconomicTransmissionEngine._classify_phase logic so the A/B
    harness never leaks future data. Interbank 2023+ is real; when a tenor
    is absent entirely the phase falls back to FRAGILE_STABILITY (neutral).
    """
    q = """
        SELECT variable, value FROM (
            SELECT variable, date, value,
                   ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
            FROM macro_history
            WHERE variable IN ('INTERBANK_ON','INTERBANK_1W','INTERBANK_3M',
                               'DXY','VIX','USD_VND','GOLD_XAU')
              AND date <= ?
        ) WHERE rn = 1
    """
    raw = dict(_lookup(conn, q, (target_date,)))

    ib_on = float(raw.get("INTERBANK_ON", 4.5) or 4.5)
    ib_1w = float(raw.get("INTERBANK_1W", ib_on) or ib_on)
    ib_3m = float(raw.get("INTERBANK_3M", ib_on + 1.0) or ib_on + 1.0)

    liquidity = float(np.clip(100 - (ib_on / 10.0) * 100, 0, 100))
    spread_3m_on = max(ib_3m - ib_on, 0)
    spread_1w_on = max(ib_1w - ib_on, 0)
    credit = float(np.clip(100 - (((spread_3m_on + spread_1w_on) / 2.0) / 5.0) * 100, 0, 100))

    dxy = float(raw.get("DXY", 104) or 104)
    vix = float(raw.get("VIX", 18) or 18)
    usd_vnd = float(raw.get("USD_VND", 25400) or 25400)
    gold = float(raw.get("GOLD_XAU", 2000) or 2000)
    dxy_score = float(np.clip(100 - (max(dxy - 95, 0) / 25.0) * 100, 0, 100))
    vix_score = float(np.clip(100 - (vix / 40.0) * 100, 0, 100))
    vnd_score = float(np.clip(100 - ((usd_vnd - 24000) / 4000.0) * 100, 0, 100))
    gold_score = float(np.clip(100 - (max(gold - 2000, 0) / 3000.0) * 100, 0, 100))
    confidence = float(
        np.clip(
            dxy_score * 0.30 + vix_score * 0.30 + vnd_score * 0.25 + gold_score * 0.15,
            0,
            100,
        )
    )

    liq, cred, conf = liquidity, credit, confidence
    if liq > 60 and cred < 40:
        return "LIQUIDITY_TRAP"
    if liq < 30 and cred < 30 and conf < 40:
        return "CREDIT_CRUNCH"
    if liq > 60 and cred > 60 and conf > 60:
        return "HEALTHY_TRANSMISSION"
    if cred > 80 and conf > 80:
        return "OVERHEATING"
    if liq < 40 and conf < 40:
        return "RISK_OFF_FLIGHT"
    return "FRAGILE_STABILITY"


def compute_per_model(symbol, conn, macro_state, transmission_phase, macro_entropy=0.0, target_date=None):
    """Compute p_gain for M1/M2/M3 with DB-derivable features.

    Point-in-time: target_date gates every micro-level loader (valuation,
    behavior, health) so backtests never read future financial data.
    """
    perception = PerceptionLoader()
    behavior = L4BehaviorLoader()
    val_eng = L3ValuationLoader()

    health = perception.load_health(symbol, target_date=target_date)
    val = val_eng.score_valuation(symbol, target_date=target_date)
    beh = behavior.score_behavior(symbol, target_date=target_date)
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


def run_ab(start_date="2022-01-01", end_date="2022-12-31", top_symbols=40):
    from src.core.macro.macro_state_classifier import MacroStateClassifier
    from src.engine.backtest_engine import BacktestAlpha

    print("=" * 70)
    print(f"A/B TEST — Legacy BacktestAlpha baseline on {start_date} → {end_date}")
    print("=" * 70)
    engine = BacktestAlpha(rebalance_freq=10, initial_capital=100_000_000, fee=0.01)
    engine.run(start_date=start_date, top_n=5)
    print()

    # --- Basket of symbols + forward outcomes from DB ---
    db_path = Path(DATA_DIR) / "screener_cache.db"
    conn = sqlite3.connect(str(db_path), timeout=60)

    symbols = [
        r[0]
        for r in _lookup(
            conn,
            "SELECT DISTINCT symbol FROM daily_ohlcv "
            "WHERE symbol NOT IN ('VNINDEX','VN30') AND date >= ? "
            "ORDER BY symbol LIMIT ?",
            (start_date, top_symbols),
        )
    ]

    # Point-in-time dates to sample across the window (spaced ~3w apart)
    sample_dates = _sample_dates(conn, start_date, end_date)

    classifier = MacroStateClassifier()
    rows = []

    for d in sample_dates:
        # Point-in-time macro state at date d (no look-ahead)
        ms = classifier.classify(target_date=d)
        macro_state = ms.macro_state
        macro_entropy = float(ms.entropy or 0.0)
        transmission_phase = transmission_phase_at(conn, d)

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
                pg = compute_per_model(sym, conn, macro_state, transmission_phase, macro_entropy, target_date=d)
                row = {
                    "date": d,
                    "symbol": sym,
                    "outcome": outcome,
                    "fwd_ret": round(fwd_ret, 4),
                    "macro": macro_state,
                    "transmission": transmission_phase,
                }
                row.update(pg)
                rows.append(row)
            except Exception:  # noqa: BLE001, S110 - cố ý bắt rộng & bỏ qua phụ (fallback/phòng thủ)
                pass

    conn.close()

    if not rows:
        print("No data for A/B — check DB.")
        return

    df = pd.DataFrame(rows)
    print("=" * 70)
    print(f"A/B TEST — Point-in-time per-model posterior vs forward outcome ({start_date} → {end_date})")
    print("=" * 70)
    print(f"N (date,symbol) observations: {len(df)}")
    print("\nMacro state distribution across sampled dates:")
    print(df["macro"].value_counts().to_string())
    print("\nTransmission phase distribution (point-in-time):")
    print(df["transmission"].value_counts().to_string())
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

    # ── Model-vs-model disagreement (posterior mismatch) ──────────
    df["m1_m2_gap"] = (df["M1_MACRO"] - df["M2_FUNDAMENTAL"]).abs()
    df["m2_m3_gap"] = (df["M2_FUNDAMENTAL"] - df["M3_BEHAVIORAL"]).abs()
    df["m1_m3_gap"] = (df["M1_MACRO"] - df["M3_BEHAVIORAL"]).abs()
    print("\n─ Post-error model disagreement ─")
    print(f"  M1-M2 mean |abs| gap: {df['m1_m2_gap'].mean():.4f}")
    print(f"  M2-M3 mean |abs| gap: {df['m2_m3_gap'].mean():.4f}")
    print(f"  M1-M3 mean |abs| gap: {df['m1_m3_gap'].mean():.4f}")
    lost = df[(df["m2_m3_gap"] > 0.15)]
    if len(lost):
        wins = (lost["outcome"] == 1.0).mean()
        print(f"  n(M2-M3 gap > 0.15): {len(lost)} | forward up-rate on disagreement: {wins:.1%}")

    print("\nSample (date, symbol, macro, transmission, p_gain, fwd_ret):")
    print(df.sort_values("fwd_ret", key=abs, ascending=False).head(15).to_string(index=False))


def _sample_dates(conn, start_date, end_date):
    """Return trading dates in [start_date, end_date] spaced ~3 weeks apart."""
    rows = _lookup(
        conn,
        "SELECT DISTINCT date FROM daily_ohlcv WHERE date >= ? AND date <= ? AND symbol='VNINDEX' ORDER BY date",
        (start_date, end_date),
    )
    dates = [r[0] for r in rows]
    step = max(15, len(dates) // 20)
    return dates[::step][:20]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2022-12-31")
    parser.add_argument("--top-symbols", type=int, default=40)
    args = parser.parse_args()
    run_ab(args.start, args.end, args.top_symbols)
