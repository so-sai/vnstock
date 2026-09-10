"""h3_gold_runner.py — H3 Macro->Gold rotation (CASH <-> GOLD_XAU), IS 2021-2024.

Pre-declared grid h3_grid.json: d(3) x MA_g(3) x H(2) = 18 trials (073-090).
Cumulative N = 72 + 18 = 90 for DSR. OOS >=2025-01-01 fail-fast.

Signal (causal, date<=T only):
  ENTER gold: US10Y 60d pp-drop > d AND gold close > MA_g AND IB_ON < 6.0
    (IB gate OFF where INTERBANK_ON missing, i.e. pre-2023).
  EXIT to cash: US10Y 60d pp-rise > d OR trailing ATR(14,2.0) OR hold >= H.
Position 100% rotation. Costs: 0.15%/side + 5bps slip, no sell tax (draft).
Metrics on DAILY equity returns (rf 5% ann.). Gold series deduped 1/date.

Usage:
  python backend/src/research/modules/h3_gold_runner.py --grid .../h3_grid.json
    --prior .../linkage_grid_v2_results.json --ledger .../evidence-ledger.jsonl
    --out .../h3_grid_results.json
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from statistics import variance


def _hydrate_path() -> Path:
    """Path Hydrator v2.1 (Anchor Fix): Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()
DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"
MOD_DIR = Path(__file__).resolve().parent
if str(MOD_DIR) not in sys.path:
    sys.path.insert(0, str(MOD_DIR))
from calculate_dsr import deflated_sharpe


def daily_series(var: str, start: str, end: str) -> list[tuple[str, float]]:
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT date, MAX(value) FROM macro_history WHERE variable=? AND date>=? AND date<=? GROUP BY date ORDER BY date",
        (var, start, end),
    ).fetchall()
    con.close()
    return [(d, float(v)) for d, v in rows if v is not None]


def pp_change(vals: list[float], i: int, lb: int) -> float | None:
    if i < 1:
        return None
    j = max(0, i - lb)
    return vals[i] - vals[j]


def run_combo(
    dates: list[str],
    gold: list[float],
    us10y_d: dict[str, float],
    ib_d: dict[str, float],
    cfg: dict,
    d: float,
    ma_w: int,
    h_max: int,
    nav: float,
) -> dict:
    fee = cfg["costs"]["fee_bps_side"] / 10000.0
    slip = cfg["costs"]["slip_bps"] / 10000.0
    lb = cfg["rate_lookback"]
    u_dates = sorted(us10y_d)
    u_vals = [us10y_d[x] for x in u_dates]
    u_idx = {x: i for i, x in enumerate(u_dates)}
    equity, in_gold, entry_px, peak, held = nav, False, 0.0, 0.0, 0
    eq_curve: list[float] = []
    trades: list[float] = []  # round-trip nets
    ma_s = 0.0
    prev = None
    trs: list[float] = []
    for i, (dt, px) in enumerate(zip(dates, gold)):
        if px <= 0:
            eq_curve.append(equity if not in_gold else equity * px / entry_px)
            continue
        ma_s += px
        if i >= ma_w:
            ma_s -= gold[i - ma_w]
        ma = ma_s / min(i + 1, ma_w)
        if prev is not None and prev > 0:
            trs.append(abs(px - prev))
            if len(trs) > cfg["atr_win"]:
                trs.pop(0)
        atr = sum(trs) / len(trs) if trs else 0.0
        ui = u_idx.get(dt)
        roc = pp_change(u_vals, ui, lb) if ui is not None else None
        ib = ib_d.get(dt)
        if not in_gold:
            gate = (ib is None) or (ib < cfg["ib_cap_fixed"])
            if roc is not None and roc < -d and px > ma and gate and i >= max(ma_w, 60):
                fill = px * (1 + slip)
                cost = equity * fee
                equity -= cost
                in_gold, entry_px, peak, held = True, fill, fill, 0
                trades.append({"open_cost": cost, "entry": fill, "net": None})
        else:
            held += 1
            peak = max(peak, px)
            trail = peak - cfg["atr_mult_fixed"] * atr if atr else 0.0
            exit_sig = (roc is not None and roc > d) or (atr > 0 and px <= trail and held > 1) or held >= h_max
            if exit_sig:
                fill = px * (1 - slip)
                gross = equity * (fill / entry_px - 1)
                sc = equity * (fill / entry_px) * fee
                net = gross - sc
                equity += net
                t = trades.pop()
                t["net"] = round(gross - t["open_cost"] - sc, 2)
                t["date_out"] = dt
                t["reason"] = (
                    "RATE" if (roc is not None and roc > d) else "TRAIL" if (atr > 0 and px <= trail and held > 1) else "TIME"
                )
                trades.append(t)
                in_gold = False
        eq_curve.append(equity if not in_gold else equity * px / entry_px)
        prev = px
    if in_gold:  # force flat at end
        fill = gold[-1] * (1 - slip)
        gross = equity * (fill / entry_px - 1)
        sc = equity * (fill / entry_px) * fee
        t = trades.pop()
        t["net"] = round(gross - t["open_cost"] - sc, 2)
        t["date_out"] = dates[-1]
        t["reason"] = "FLAT_END"
        trades.append(t)
        equity += t["net"]
    rets = [(eq_curve[i] / eq_curve[i - 1] - 1) for i in range(1, len(eq_curve)) if eq_curve[i - 1] > 0]
    n = len(rets)
    if n < 30:
        return {
            "n_days": n,
            "n_trades": 0,
            "sharpe": 0.0,
            "profit_factor": 0.0,
            "mdd_pct": 0.0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "total_net": 0.0,
            "skew": 0.0,
            "kurt": 3.0,
        }
    m = sum(rets) / n
    v = sum((x - m) ** 2 for x in rets) / n
    sd = math.sqrt(v)
    sh = ((m - 0.05 / 252.0) / sd * math.sqrt(252.0)) if sd > 0 else 0.0
    sk = sum(((x - m) / sd) ** 3 for x in rets) / n if sd > 0 else 0.0
    ku = sum(((x - m) / sd) ** 4 for x in rets) / n if sd > 0 else 3.0
    nets = [t["net"] for t in trades if t["net"] is not None]
    gp = sum(x for x in nets if x > 0)
    gl = abs(sum(x for x in nets if x <= 0))
    peak_e, mdd = eq_curve[0], 0.0
    for e in eq_curve:
        peak_e = max(peak_e, e)
        mdd = min(mdd, (e - peak_e) / peak_e)
    wins = sum(1 for x in nets if x > 0)
    return {
        "n_days": n,
        "n_trades": len(nets),
        "sharpe": round(sh, 4),
        "profit_factor": round(gp / gl, 4) if gl > 0 else float("inf"),
        "mdd_pct": round(mdd * 100.0, 3),
        "win_rate": round(wins / len(nets), 4) if nets else 0.0,
        "expectancy": round(sum(nets) / nav / len(nets) * 100.0, 5) if nets else 0.0,
        "total_net": round(sum(nets), 2),
        "skew": round(sk, 4),
        "kurt": round(ku, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="H3 gold grid — Tier-2")
    ap.add_argument("--grid", required=True)
    ap.add_argument("--prior", nargs="+", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nav", type=float, default=100_000_000.0)
    a = ap.parse_args()
    cfg = json.loads(Path(a.grid).read_text(encoding="utf-8"))
    if cfg["end"] >= "2025-01-01":
        print("ERROR: IS window cham OOS.", file=sys.stderr)
        return 2
    combos = [(dd, mg, hh) for dd in cfg["d_pp"] for mg in cfg["ma_g"] for hh in cfg["h_hold"]]
    if len(combos) != cfg["n_declared"]:
        print(f"ERROR: combos={len(combos)} != {cfg['n_declared']}.", file=sys.stderr)
        return 2
    gx = daily_series(cfg["gold_series"], cfg["start"], cfg["end"])
    if any(d >= "2025-01-01" for d, _ in gx):
        print("ERROR: bar OOS.", file=sys.stderr)
        return 2
    dates = [d for d, _ in gx]
    gold = [v for _, v in gx]
    us10y = dict(daily_series(cfg["macro"]["us10y"], cfg["start"], cfg["end"]))
    ib = dict(daily_series(cfg["macro"]["ib_on"], cfg["start"], cfg["end"]))
    print(f"bars: gold={len(gx)} us10y={len(us10y)} ib_on={len(ib)}")
    results = []
    with open(a.ledger, "a", encoding="utf-8") as led:
        for j, (dd, mg, hh) in enumerate(combos, 73):
            m = run_combo(dates, gold, us10y, ib, cfg, dd, mg, hh, a.nav)
            rec = {
                "event": "grid_trial",
                "trial_id": j,
                "grid": cfg["grid_name"],
                "data_scope": "OHLCV_only",
                "params": {"d_pp": dd, "ma_g": mg, "h_hold": hh},
                "window": [cfg["start"], cfg["end"]],
                **m,
            }
            led.write(json.dumps(rec, ensure_ascii=False) + "\n")
            results.append(rec)
            print(
                f"trial {j:03d}/090 d={dd} ma={mg} h={hh}: "
                f"trades={m['n_trades']} sharpe={m['sharpe']} "
                f"pf={m['profit_factor']} mdd={m['mdd_pct']}%",
                flush=True,
            )
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    prior_sh = []
    for p in a.prior:
        for r in json.loads(Path(p).read_text(encoding="utf-8")):
            s = r.get("sharpe", 0.0)
            prior_sh.append(s if math.isfinite(s) else 0.0)
    new_sh = [r["sharpe"] if math.isfinite(r["sharpe"]) else 0.0 for r in results]
    all_sh = prior_sh + new_sh
    n_tot = len(all_sh)
    assert n_tot == cfg["cumulative_n"], f"N mismatch {n_tot}"
    var_t = variance(all_sh) if n_tot > 1 else 0.0
    top5 = sorted(results, key=lambda r: r["sharpe"], reverse=True)[:5]
    print(f"--- DSR top-5 (N={n_tot} cumulative) ---")
    verdicts = []
    for r in top5:
        try:
            dd = deflated_sharpe(r["sharpe"], max(r["n_days"], 30), r["skew"], r["kurt"], var_t, n_tot)
        except ValueError as e:
            print(f"trial {r['trial_id']}: DSR ERROR {e}")
            continue
        gate = dd["dsr"] > 0.95 and r["profit_factor"] != float("inf") and r["profit_factor"] >= 1.6 and r["mdd_pct"] > -15.0
        verdicts.append(
            {
                "trial_id": r["trial_id"],
                "params": r["params"],
                "sharpe": r["sharpe"],
                "trades": r["n_trades"],
                "dsr": round(dd["dsr"], 4),
                "pf": r["profit_factor"],
                "mdd": r["mdd_pct"],
                "gate2_pass": bool(gate),
            }
        )
        print(json.dumps(verdicts[-1], ensure_ascii=False))
    with open(a.ledger, "a", encoding="utf-8") as led:
        led.write(
            json.dumps(
                {
                    "event": "grid_dsr_verdict",
                    "grid": cfg["grid_name"],
                    "n_cumulative": n_tot,
                    "data_scope": "OHLCV_only",
                    "verdicts": verdicts,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print(f"GATE2-H3: {sum(v['gate2_pass'] for v in verdicts)}/5 pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
