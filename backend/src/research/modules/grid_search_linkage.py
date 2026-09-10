"""grid_search_linkage.py — Grid N=36 predeclared (linkage_grid_v1) tren IS.

Doc grid JSON -> chay moi combo tren full window -> append DU 36 trials
(ke ca thua) vao ledger -> top-5 Sharpe -> DSR voi N=36 -> verdict Gate 2.

CAU LENH (operator chay):
  python backend/src/research/modules/grid_search_linkage.py
    --grid backend/src/research/configs/linkage_grid_v1.json
    --ledger backend/src/research/ledger/evidence-ledger.jsonl
    --out backend/src/research/ledger/linkage_grid_results.json
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
    _adv,
    _atr,
    cash_shield,
    load_bars,
    load_macro,
    volume_thrust_detector,
)
from pure_ohlcv_wf import metrics


def simulate(
    bars: list[dict], on_map: dict[str, dict[str, float]], cfg: dict, theta: float, k_vol: float, atr_mult: float, nav: float
) -> list[dict]:
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    mas = []
    s = 0.0
    for i, c in enumerate(closes):  # MA20 causal
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
    for i in range(61, len(bars) - 1):
        b = bars[i]
        if pos is None:
            det = volume_thrust_detector(bars, i, k_vol=k_vol)
            if not det["signal"]:
                continue
            if b["close"] <= mas[i]:
                continue
            sh = cash_shield(b["date"], theta, **on_map)
            if sh["shield"]:
                continue
            a20 = _adv(vols, i)
            if a20 <= 0:
                continue
            entry = bars[i + 1]["open"] or b["close"]
            atr_v = _atr(bars, i)
            if entry <= 0 or atr_v <= 0:
                continue
            ex = cfg["exits_fixed"]
            stop_dist = max(atr_mult * atr_v, entry * ex["stop_pct"] / 100.0)
            qty = int(equity * cfg["costs"]["risk_pct_nav"] / 100.0 / stop_dist)
            qty = max(0, min(qty, int(a20 * cfg["costs"]["adv_cap_pct"] / 100.0)))
            if qty <= 0:
                continue
            part = qty * entry / (a20 * entry) * 100.0
            fill = entry * (1 + slip_b + slip_p * part)
            pos = {
                "qty": qty,
                "fill": fill,
                "cost": qty * entry * fee,
                "date_in": bars[i + 1]["date"],
                "stop": fill - stop_dist,
                "peak": fill,
                "held": 0,
                "notional": qty * fill,
            }
        else:
            pos["held"] += 1
            h, lo, c = bars[i]["high"], bars[i]["low"], bars[i]["close"]
            pos["peak"] = max(pos["peak"], h)
            trail = pos["peak"] - atr_mult * _atr(bars, i)
            ex = cfg["exits_fixed"]
            s_hit = lo <= pos["stop"]
            t_hit = lo <= trail and pos["held"] > 1
            k_hit = h >= pos["fill"] * (1 + ex["take_pct"] / 100.0)
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
    ap = argparse.ArgumentParser(description="Grid N=36 linkage — Tier-2 IS")
    ap.add_argument("--grid", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nav", type=float, default=100_000_000.0)
    a = ap.parse_args()

    cfg = json.loads(Path(a.grid).read_text(encoding="utf-8"))
    if cfg["end"] >= "2025-01-01" or cfg["start"] >= "2025-01-01":
        print("ERROR: IS window cham OOS phong toa (>=2025-01-01).", file=sys.stderr)
        return 2
    combos = [(t, k, r) for t in cfg["theta_rate_pp"] for k in cfg["k_vol"] for r in cfg["atr_trail"]]
    if len(combos) != cfg["n_declared"]:
        print(f"ERROR: combos={len(combos)} != n_declared={cfg['n_declared']}.", file=sys.stderr)
        return 2

    syms = cfg["universe"]
    bars = {s: load_bars(s, cfg["start"], cfg["end"]) for s in syms}
    for s, bs in bars.items():
        if any(b["date"] >= "2025-01-01" for b in bs):
            print(f"ERROR: bar OOS lot vao IS ({s}).", file=sys.stderr)
            return 2
    macro = {
        "us10y": load_macro("US10Y", cfg["start"], cfg["end"]),
        "ib_on": load_macro("INTERBANK_ON", cfg["start"], cfg["end"]),
        "fed_rate": load_macro("FED_TARGET_RATE", cfg["start"], cfg["end"]),
    }

    results: list[dict] = []
    with open(a.ledger, "a", encoding="utf-8") as led:
        for tid, (th, kv, ar) in enumerate(combos, 1):
            pooled: list[dict] = []
            for s in syms:
                sub = bars[s]
                if len(sub) < 80:
                    continue
                for t in simulate(sub, macro, cfg, th, kv, ar, a.nav):
                    t["symbol"] = s
                    pooled.append(t)
            m = metrics(pooled, a.nav)
            sk, ku = trade_moments(pooled, a.nav)
            rec = {
                "event": "grid_trial",
                "trial_id": tid,
                "n_declared": cfg["n_declared"],
                "grid": cfg["grid_name"],
                "data_scope": "OHLCV_only",
                "params": {"theta_pp": th, "k_vol": kv, "atr_trail": ar},
                "symbols": syms,
                "window": [cfg["start"], cfg["end"]],
                "skew": round(sk, 4),
                "kurt": round(ku, 4),
                **m,
            }
            led.write(json.dumps(rec, ensure_ascii=False) + "\n")
            results.append(rec)
            print(
                f"trial {tid:02d}/36 th={th} k={kv} atr={ar}: "
                f"n={m['n']} sharpe={m['sharpe']} pf={m['profit_factor']} "
                f"mdd={m['mdd_pct']}% wr={m['win_rate']}",
                flush=True,
            )

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    # DSR top-5 voi N=36, var tren 36 Sharpe
    sharpes = [r["sharpe"] if math.isfinite(r["sharpe"]) else 0.0 for r in results]
    var_t = variance(sharpes) if len(sharpes) > 1 else 0.0
    top5 = sorted(results, key=lambda r: r["sharpe"], reverse=True)[:5]
    print("--- DSR top-5 (N=36) ---")
    verdicts = []
    for r in top5:
        n = max(r["n"], 3)
        try:
            d = deflated_sharpe(r["sharpe"], n, r["skew"], r["kurt"], var_t, 36)
        except ValueError as e:
            print(f"trial {r['trial_id']}: DSR ERROR {e}")
            continue
        gate = d["dsr"] > 0.95 and r["profit_factor"] != float("inf") and r["profit_factor"] >= 1.6 and r["mdd_pct"] > -15.0
        verdicts.append(
            {
                "trial_id": r["trial_id"],
                "params": r["params"],
                "sharpe": r["sharpe"],
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
                    "n_declared": 36,
                    "data_scope": "OHLCV_only",
                    "verdicts": verdicts,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    n_pass = sum(v["gate2_pass"] for v in verdicts)
    print(f"GATE2: {n_pass}/5 top trials pass (DSR>0.95 & PF>=1.6 & MDD>-15%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
