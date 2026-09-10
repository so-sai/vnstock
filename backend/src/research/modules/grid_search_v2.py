"""grid_search_v2.py — Grid v2 predeclared (linkage_grid_v2): 18 trials, trial_037-054.

Khac v1: universe top-30 (2021 liquidity rule), KHONG macro shield
(archived inert), lookback_breakout ∈ {15,20}, take = entry + RR*stop_dist
(RR ∈ {2.0,3.0}). DSR cong don N=54 (36 cu + 18 moi).

Usage:
  python backend/src/research/modules/grid_search_v2.py
    --grid backend/src/research/configs/linkage_grid_v2.json
    --prior backend/src/research/ledger/linkage_grid_results.json
    --ledger backend/src/research/ledger/evidence-ledger.jsonl
    --out backend/src/research/ledger/linkage_grid_v2_results.json
"""

from __future__ import annotations

import argparse
import json
import math
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


MOD_DIR = Path(__file__).resolve().parent
if str(MOD_DIR) not in sys.path:
    sys.path.insert(0, str(MOD_DIR))

from calculate_dsr import deflated_sharpe
from macro_flow_linkage import (
    VCP_WIN_L,
    VCP_WIN_S,
    _adv,
    _atr,
    _range_pct,
    load_bars,
    volume_thrust_detector,
)
from pure_ohlcv_wf import metrics


def simulate_v2(bars: list[dict], cfg: dict, k_vol: float, lb: int, atr_mult: float, rr: float, nav: float) -> list[dict]:
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    mas, s = [], 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= 20:
            s -= closes[i - 20]
        mas.append(s / min(i + 1, 20))
    trades: list[dict] = []
    pos = None
    equity = nav
    fee = cfg["costs"]["fee_bps_side"] / 10000.0
    tax = cfg["costs"]["tax_bps_sell"] / 10000.0
    slip_b = cfg["costs"]["slip_base_bps"] / 10000.0
    slip_p = cfg["costs"]["slip_per_pct_part_bps"] / 10000.0
    ex = cfg["exits_fixed"]
    for i in range(61, len(bars) - 1):
        b = bars[i]
        if pos is None:
            det = volume_thrust_detector(bars, i, k_vol=k_vol)
            if not det["signal"]:
                continue
            resist = max(x["high"] for x in bars[max(0, i - lb) : i])
            if not (b["close"] > mas[i] and b["close"] > resist):
                continue
            if _range_pct(bars, i, VCP_WIN_S) >= 0.5 * _range_pct(bars, i, VCP_WIN_L):
                continue
            a20 = _adv(vols, i)
            if a20 <= 0:
                continue
            entry = bars[i + 1]["open"] or b["close"]
            atr_v = _atr(bars, i)
            if entry <= 0 or atr_v <= 0:
                continue
            stop_dist = max(atr_mult * atr_v, entry * ex["stop_pct"] / 100.0)
            qty = int(equity * cfg["costs"]["risk_pct_nav"] / 100.0 / stop_dist)
            qty = max(0, min(qty, int(a20 * cfg["costs"]["adv_cap_pct"] / 100.0)))
            if qty <= 0:
                continue
            part = qty / a20 * 100.0
            fill = entry * (1 + slip_b + slip_p * part)
            pos = {
                "qty": qty,
                "fill": fill,
                "cost": qty * entry * fee,
                "date_in": bars[i + 1]["date"],
                "stop": fill - stop_dist,
                "take": fill + rr * stop_dist,
                "peak": fill,
                "held": 0,
                "notional": qty * fill,
            }
        else:
            pos["held"] += 1
            h, lo, c = bars[i]["high"], bars[i]["low"], bars[i]["close"]
            pos["peak"] = max(pos["peak"], h)
            trail = pos["peak"] - atr_mult * _atr(bars, i)
            s_hit = lo <= pos["stop"]
            t_hit = lo <= trail and pos["held"] > 1
            k_hit = h >= pos["take"]
            m_hit = pos["held"] >= ex["max_hold"]
            if s_hit or t_hit or k_hit or m_hit:
                px = pos["stop"] if s_hit else c
                net = (px - pos["fill"]) * pos["qty"] - pos["cost"] - pos["qty"] * px * (fee + tax)
                equity += net
                trades.append(
                    {
                        "date_in": pos["date_in"],
                        "date_out": b["date"],
                        "qty": pos["qty"],
                        "net": round(net, 2),
                        "reason": "STOP" if s_hit else "TRAIL" if t_hit else "TAKE" if k_hit else "TIME",
                        "notional": round(pos["notional"], 2),
                    }
                )
                pos = None
    return trades


def trade_moments(trades: list[dict], nav: float) -> tuple[float, float]:
    r = [t["net"] / nav for t in trades]
    n = len(r)
    if n < 3:
        return 0.0, 3.0
    m = sum(r) / n
    v = sum((x - m) ** 2 for x in r) / n
    if v <= 0:
        return 0.0, 3.0
    sd = math.sqrt(v)
    sk = sum(((x - m) / sd) ** 3 for x in r) / n
    ku = sum(((x - m) / sd) ** 4 for x in r) / n
    return sk, ku


def main() -> int:
    ap = argparse.ArgumentParser(description="Grid v2 — Tier-2 IS")
    ap.add_argument("--grid", required=True)
    ap.add_argument("--prior", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nav", type=float, default=100_000_000.0)
    a = ap.parse_args()

    cfg = json.loads(Path(a.grid).read_text(encoding="utf-8"))
    if cfg["end"] >= "2025-01-01":
        print("ERROR: IS window cham OOS.", file=sys.stderr)
        return 2
    combos = [
        (k, lb, ar, rr)
        for k in cfg["k_vol"]
        for lb in cfg["lookback_breakout"]
        for ar in cfg["atr_trail"]
        for rr in cfg["rr_take"]
    ]
    if len(combos) != cfg["n_declared"]:
        print(f"ERROR: combos={len(combos)} != {cfg['n_declared']}.", file=sys.stderr)
        return 2

    syms = cfg["universe"]
    bars = {s: load_bars(s, cfg["start"], cfg["end"]) for s in syms}
    for s, bs in bars.items():
        if any(b["date"] >= "2025-01-01" for b in bs):
            print(f"ERROR: bar OOS ({s}).", file=sys.stderr)
            return 2

    results: list[dict] = []
    with open(a.ledger, "a", encoding="utf-8") as led:
        for j, (kv, lb, ar, rr) in enumerate(combos, 37):
            pooled: list[dict] = []
            for s in syms:
                if len(bars[s]) < 80:
                    continue
                for t in simulate_v2(bars[s], cfg, kv, lb, ar, rr, a.nav):
                    t["symbol"] = s
                    pooled.append(t)
            m = metrics(pooled, a.nav)
            sk, ku = trade_moments(pooled, a.nav)
            rec = {
                "event": "grid_trial",
                "trial_id": j,
                "grid": cfg["grid_name"],
                "data_scope": "OHLCV_only",
                "params": {"k_vol": kv, "lookback": lb, "atr_trail": ar, "rr_take": rr},
                "symbols": syms,
                "window": [cfg["start"], cfg["end"]],
                "skew": round(sk, 4),
                "kurt": round(ku, 4),
                **m,
            }
            led.write(json.dumps(rec, ensure_ascii=False) + "\n")
            results.append(rec)
            print(
                f"trial {j:03d}/054 k={kv} lb={lb} atr={ar} rr={rr}: "
                f"n={m['n']} sharpe={m['sharpe']} pf={m['profit_factor']} "
                f"mdd={m['mdd_pct']}% wr={m['win_rate']}",
                flush=True,
            )

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    prior = json.loads(Path(a.prior).read_text(encoding="utf-8"))
    all_sh = [r["sharpe"] if math.isfinite(r["sharpe"]) else 0.0 for r in prior] + [
        r["sharpe"] if math.isfinite(r["sharpe"]) else 0.0 for r in results
    ]
    n_tot = len(all_sh)
    assert n_tot == cfg["cumulative_n"], f"N mismatch {n_tot}"
    var_t = variance(all_sh) if n_tot > 1 else 0.0
    top5 = sorted(results, key=lambda r: r["sharpe"], reverse=True)[:5]
    print(f"--- DSR top-5 (N={n_tot} cumulative) ---")
    verdicts = []
    for r in top5:
        try:
            d = deflated_sharpe(r["sharpe"], max(r["n"], 3), r["skew"], r["kurt"], var_t, n_tot)
        except ValueError as e:
            print(f"trial {r['trial_id']}: DSR ERROR {e}")
            continue
        gate = d["dsr"] > 0.95 and r["profit_factor"] != float("inf") and r["profit_factor"] >= 1.6 and r["mdd_pct"] > -15.0
        verdicts.append(
            {
                "trial_id": r["trial_id"],
                "params": r["params"],
                "sharpe": r["sharpe"],
                "n": r["n"],
                "dsr": round(d["dsr"], 4),
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
    print(f"GATE2: {sum(v['gate2_pass'] for v in verdicts)}/5 pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
