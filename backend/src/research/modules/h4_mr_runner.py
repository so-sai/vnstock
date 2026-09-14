"""h4_mr_runner.py — H4 Mean-Reversion RANGING (24 trials 091-114, N=114).

Entry (bar i, causal): market gate (breadth<-35 or ad<0.35, FIXED) AND
  close<=BB(20,2).lower AND vol<=k*ADV20 AND RSI14<=r.
Exit: stop s% | take=MA20-touch (mode MA20) or entry+1.5*stop (mode RR1.5)
  | max hold 15 (FIXED). Sizing 1% risk, cap 10% ADV; costs v2-identical.

Indicators precomputed ONCE per symbol (running O(1)); combos scan cheap.
Universe: BANK+STANDARD, >=80 bars IS (1302 syms, rule-locked).

Usage:
  python backend/src/research/modules/h4_mr_runner.py --grid .../h4_grid.json
    --prior <v1.json> <v2.json> <h3.json> --ledger .../evidence-ledger.jsonl
    --out .../h4_grid_results.json
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
from pure_ohlcv_wf import metrics


def load_universe(start: str, end: str) -> list[str]:
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT symbol FROM symbol_industry WHERE icb_name2 NOT IN ('Dịch vụ tài chính', 'Bảo hiểm')"
    ).fetchall()
    uni = {r[0] for r in rows}
    have = {
        r[0]
        for r in con.execute(
            "SELECT symbol FROM daily_ohlcv WHERE date>=? AND date<=? GROUP BY symbol HAVING COUNT(*)>=80", (start, end)
        ).fetchall()
    }
    con.close()
    return sorted(uni & have)


def load_bars(symbols: list[str], start: str, end: str) -> dict[str, list[tuple]]:
    con = sqlite3.connect(str(DB))
    out: dict[str, list[tuple]] = {}
    for s in symbols:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM daily_ohlcv "
            "WHERE symbol=? AND date>=? AND date<=? ORDER BY date",
            (s, start, end),
        ).fetchall()
        if len(rows) >= 80:
            out[s] = [(d, o, h, lo, c, v) for d, o, h, lo, c, v in rows]
    con.close()
    return out


def load_gate(start: str, end: str) -> dict[str, tuple[float, float]]:
    con = sqlite3.connect(str(DB))
    try:
        rows = con.execute(
            "SELECT date, breadth_pct, ad_ratio FROM market_wide_breadth_daily WHERE date>=? AND date<=?", (start, end)
        ).fetchall()
    except sqlite3.Error:
        rows = []
    con.close()
    return {d: (b, a) for d, b, a in rows}


def precompute(rows: list[tuple]) -> dict:
    """Running O(1) indicators. Returns dict of lists + dates."""
    n = len(rows)
    dates = [r[0] for r in rows]
    cl = [r[4] or 0.0 for r in rows]
    vo = [r[5] or 0 for r in rows]
    op = [r[1] or 0.0 for r in rows]
    hi = [r[2] or 0.0 for r in rows]
    lo = [r[3] or 0.0 for r in rows]
    ma20, rsi, bbl, adv = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
    s = ss = sv = 0.0
    gain = loss = 0.0
    for i in range(n):
        c = cl[i]
        s += c
        ss += c * c
        sv += vo[i]
        if i >= 20:
            s -= cl[i - 20]
            ss -= cl[i - 20] ** 2
            sv -= vo[i - 20]
        w = min(i + 1, 20)
        mean = s / w
        ma20[i] = mean
        var = max(ss / w - mean * mean, 0.0)
        bbl[i] = mean - 2.0 * math.sqrt(var)
        adv[i] = sv / w
        if i > 0:
            ch = c - cl[i - 1]
            gain = (ch if ch > 0 else 0.0) if i == 1 else (gain * 13 + (ch if ch > 0 else 0.0)) / 14
            loss = (-ch if ch < 0 else 0.0) if i == 1 else (loss * 13 + (-ch if ch < 0 else 0.0)) / 14
        rsi[i] = 100.0 - 100.0 / (1 + gain / loss) if loss > 0 else (100.0 if gain > 0 else 50.0)
    return {
        "dates": dates,
        "close": cl,
        "vol": vo,
        "open": op,
        "high": hi,
        "low": lo,
        "ma20": ma20,
        "rsi": rsi,
        "bbl": bbl,
        "adv": adv,
    }


def simulate(
    pre: dict,
    gate: dict[str, tuple[float, float]],
    cfg: dict,
    rsi_max: float,
    dry_k: float,
    stop_pct: float,
    mode: str,
    nav: float,
) -> list[dict]:
    fee = cfg["costs"]["fee_bps_side"] / 10000.0
    tax = cfg["costs"]["tax_bps_sell"] / 10000.0
    slip_b = cfg["costs"]["slip_base_bps"] / 10000.0
    slip_p = cfg["costs"]["slip_per_pct_part_bps"] / 10000.0
    gb, ga = cfg["market_gate_fixed"]["breadth_below"], cfg["market_gate_fixed"]["ad_ratio_below"]
    max_hold = cfg["exits_fixed"]["max_hold"]
    dates, cl, vo, op = pre["dates"], pre["close"], pre["vol"], pre["open"]
    n = len(dates)
    trades: list[dict] = []
    pos = None
    equity = nav
    for i in range(60, n - 1):
        dt = dates[i]
        if pos is None:
            g = gate.get(dt)
            if g is None or not (g[0] < gb or g[1] < ga):
                continue
            a20 = pre["adv"][i]
            if a20 <= 0 or cl[i] <= 0:
                continue
            if not (cl[i] <= pre["bbl"][i] and vo[i] <= dry_k * a20 and pre["rsi"][i] <= rsi_max):
                continue
            entry = op[i + 1] or cl[i]
            if entry <= 0:
                continue
            sd = entry * stop_pct / 100.0
            qty = int(equity * cfg["costs"]["risk_pct_nav"] / 100.0 / sd)
            qty = max(0, min(qty, int(a20 * cfg["costs"]["adv_cap_pct"] / 100.0)))
            if qty <= 0:
                continue
            part = qty / a20 * 100.0
            fill = entry * (1 + slip_b + slip_p * part)
            pos = {
                "qty": qty,
                "fill": fill,
                "cost": qty * entry * fee,
                "date_in": dates[i + 1],
                "stop": fill - sd,
                "take_rr": fill + 1.5 * sd,
                "held": 0,
                "notional": qty * fill,
            }
        else:
            pos["held"] += 1
            h, lo, c = pre["high"][i], pre["low"][i], cl[i]
            s_hit = lo <= pos["stop"]
            if mode == "MA20":
                k_hit = c >= pre["ma20"][i]
            else:
                k_hit = h >= pos["take_rr"]
            m_hit = pos["held"] >= max_hold
            if s_hit or k_hit or m_hit:
                px = pos["stop"] if s_hit else c
                net = (px - pos["fill"]) * pos["qty"] - pos["cost"] - pos["qty"] * px * (fee + tax)
                equity += net
                trades.append(
                    {
                        "date_in": pos["date_in"],
                        "date_out": dt,
                        "qty": pos["qty"],
                        "net": round(net, 2),
                        "reason": "STOP" if s_hit else "TAKE" if k_hit else "TIME",
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
    return (sum(((x - m) / sd) ** 3 for x in r) / n, sum(((x - m) / sd) ** 4 for x in r) / n)


PORTFOLIO_HEAT_CAP = 0.08  # 8% NAV
# heat_cap_rationale: prevent_naked_leverage_in_correlated_drawdown.
# Cap ap dung TRUOC khi tru phi. Vuot cap => tu choi mo vi the moi
# (khong downsize hien co, khong dong bot de mo cai moi).


def compute_heat(open_positions: list[dict], nav: float) -> float:
    """heat = sum(qty * stop_dist_money) / nav. stop_dist_money = qty*(fill-stop)."""
    if nav <= 0:
        return float("inf")
    return sum(p["qty"] * (p["fill"] - p["stop"]) for p in open_positions) / nav


def can_open_new(open_positions: list[dict], qty: float, stop_dist: float, nav: float) -> bool:
    return (compute_heat(open_positions, nav) + qty * stop_dist / nav) <= PORTFOLIO_HEAT_CAP * (1 + 1e-9)


def portfolio_simulate(
    pres: dict[str, dict],
    gate: dict[str, tuple[float, float]],
    cfg: dict,
    rsi_max: float,
    dry_k: float,
    stop_pct: float,
    mode: str,
    nav: float,
) -> tuple[list[dict], dict[str, float]]:
    """Mo phong portfolio-level: exits truoc, entries theo symbol sort,
    gate heat 8% NAV. Tra ve (trades, heat_by_date)."""
    fee = cfg["costs"]["fee_bps_side"] / 10000.0
    tax = cfg["costs"]["tax_bps_sell"] / 10000.0
    slip_b = cfg["costs"]["slip_base_bps"] / 10000.0
    slip_p = cfg["costs"]["slip_per_pct_part_bps"] / 10000.0
    gb, ga = cfg["market_gate_fixed"]["breadth_below"], cfg["market_gate_fixed"]["ad_ratio_below"]
    max_hold = cfg["exits_fixed"]["max_hold"]
    idx_of: dict[str, dict[str, int]] = {}
    all_dates: set[str] = set()
    for s, pc in pres.items():
        idx_of[s] = {d: i for i, d in enumerate(pc["dates"])}
        all_dates.update(pc["dates"])
    dates = sorted(all_dates)
    syms = sorted(pres)
    opens: list[dict] = []
    trades: list[dict] = []
    heat_by_date: dict[str, float] = {}
    equity = nav
    for dt in dates:
        for p in list(opens):
            pc = pres[p["symbol"]]
            i = idx_of[p["symbol"]].get(dt)
            if i is None or i < 1:
                continue
            p["held"] += 1
            h, lo, c = pc["high"][i], pc["low"][i], pc["close"][i]
            s_hit = lo <= p["stop"]
            if mode == "MA20":
                k_hit = c >= pc["ma20"][i]
            else:
                k_hit = h >= p["take_rr"]
            m_hit = p["held"] >= max_hold
            if s_hit or k_hit or m_hit:
                px = p["stop"] if s_hit else c
                net = (px - p["fill"]) * p["qty"] - p["cost"] - p["qty"] * px * (fee + tax)
                equity += net
                trades.append(
                    {
                        "symbol": p["symbol"],
                        "date_in": p["date_in"],
                        "date_out": dt,
                        "qty": p["qty"],
                        "net": round(net, 2),
                        "reason": "STOP" if s_hit else "TAKE" if k_hit else "TIME",
                        "notional": round(p["notional"], 2),
                    }
                )
                opens.remove(p)
        g = gate.get(dt)
        if g is None or not (g[0] < gb or g[1] < ga):
            heat_by_date[dt] = compute_heat(opens, nav)
            continue
        for s in syms:
            pc = pres[s]
            i = idx_of[s].get(dt)
            if i is None or i < 61 or i >= len(pc["dates"]) - 1:
                continue
            if any(p["symbol"] == s for p in opens):
                continue
            a20 = pc["adv"][i]
            if a20 <= 0 or pc["close"][i] <= 0:
                continue
            if not (pc["close"][i] <= pc["bbl"][i] and pc["vol"][i] <= dry_k * a20 and pc["rsi"][i] <= rsi_max):
                continue
            entry = pc["open"][i + 1] or pc["close"][i]
            if entry <= 0:
                continue
            sd = entry * stop_pct / 100.0
            qty = int(equity * cfg["costs"]["risk_pct_nav"] / 100.0 / sd)
            qty = max(0, min(qty, int(a20 * cfg["costs"]["adv_cap_pct"] / 100.0)))
            if qty <= 0:
                continue
            if not can_open_new(opens, qty, sd, nav):
                continue
            part = qty / a20 * 100.0
            fill = entry * (1 + slip_b + slip_p * part)
            opens.append(
                {
                    "symbol": s,
                    "qty": qty,
                    "fill": fill,
                    "cost": qty * entry * fee,
                    "date_in": pc["dates"][i + 1],
                    "stop": fill - sd,
                    "take_rr": fill + 1.5 * sd,
                    "held": 0,
                    "notional": qty * fill,
                }
            )
        heat_by_date[dt] = compute_heat(opens, nav)
    return trades, heat_by_date


def selftest_heat() -> dict:
    """Test 1 (causal): heat tai T giong nhau giua full vs cat chuoi.
    Test 2 (cap): 20 vi the gia dinh tong 10% NAV => phai co reject,
    heat accepted khong vuot cap."""
    syms = load_universe("2020-01-01", "2024-12-31")[:5]
    bars = load_bars(syms, "2020-01-01", "2024-12-31")
    pres = {s: precompute(bs) for s, bs in bars.items()}
    gate = load_gate("2020-01-01", "2024-12-31")
    cfg = {
        "costs": {
            "fee_bps_side": 15.0,
            "tax_bps_sell": 10.0,
            "slip_base_bps": 5.0,
            "slip_per_pct_part_bps": 20.0,
            "risk_pct_nav": 1.0,
            "adv_cap_pct": 10.0,
        },
        "market_gate_fixed": {"breadth_below": -35.0, "ad_ratio_below": 0.35},
        "exits_fixed": {"max_hold": 15},
    }
    _, heat_full = portfolio_simulate(pres, gate, cfg, 35, 0.6, 3.5, "MA20", 100_000_000.0)
    cut = "2022-06-30"
    pres_t = {
        s: {k: (v[: sum(1 for d in pc["dates"] if d <= cut)] if isinstance(v, list) else v) for k, v in pc.items()}
        for s, pc in pres.items()
    }
    gate_t = {d: v for d, v in gate.items() if d <= cut}
    _, heat_tr = portfolio_simulate(pres_t, gate_t, cfg, 35, 0.6, 3.5, "MA20", 100_000_000.0)
    causal_ok = all(abs(heat_full[d] - heat_tr[d]) < 1e-9 for d in heat_tr)
    # 20 vi the gia dinh, moi vi the 0.5% NAV (tong 10%): phai reject truoc khi het,
    # heat cua cac vi the duoc chap nhan khong vuot cap + eps float.
    trial_opens: list[dict] = []
    accepted = 0
    max_heat = 0.0
    for _ in range(20):
        if can_open_new(trial_opens, 1000, 500.0, 100_000_000.0):
            trial_opens.append({"qty": 1000, "fill": 10500.0, "stop": 10000.0})
            accepted += 1
            max_heat = max(max_heat, compute_heat(trial_opens, 100_000_000.0))
    cap_ok = accepted < 20 and max_heat <= PORTFOLIO_HEAT_CAP + 1e-6
    return {
        "causal_ok": bool(causal_ok),
        "checked_days": len(heat_tr),
        "accepted": accepted,
        "max_heat": round(max_heat, 6),
        "cap_ok": bool(cap_ok),
        "ok": bool(causal_ok and cap_ok),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="H4 mean-reversion grid — Tier-2")
    ap.add_argument("--grid", required=True)
    ap.add_argument("--prior", nargs="+", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nav", type=float, default=100_000_000.0)
    ap.add_argument("--selftest-heat", action="store_true")
    ap.add_argument("--only-trial", type=int, default=None, help="Re-run 1 trial ID (bug-fix revalidation, 0 trial cost)")
    ap.add_argument("--reval-out", default=None)
    a = ap.parse_args()
    if a.selftest_heat:
        r = selftest_heat()
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1
    cfg = json.loads(Path(a.grid).read_text(encoding="utf-8"))
    if cfg["end"] >= "2025-01-01":
        print("ERROR: IS window cham OOS.", file=sys.stderr)
        return 2
    combos = [
        (r, k, s, m) for r in cfg["rsi_max"] for k in cfg["dry_up_mult"] for s in cfg["stop_pct"] for m in cfg["exit_mode"]
    ]
    if a.only_trial is not None:
        rv, kv, sp, mo = combos[a.only_trial - 91]
        syms = load_universe(cfg["start"], cfg["end"])
        bars = load_bars(syms, cfg["start"], cfg["end"])
        pres = {s: precompute(bs) for s, bs in bars.items()}
        gate = load_gate(cfg["start"], cfg["end"])
        trades, heat = portfolio_simulate(pres, gate, cfg, rv, kv, sp, mo, a.nav)
        m = metrics(trades, a.nav)
        sk, ku = trade_moments(trades, a.nav)
        peak_heat = max(heat.values()) if heat else 0.0
        rec = {
            "event": "trial_revalidation",
            "trial_id": a.only_trial,
            "engine": "portfolio_heat_capped_8pct",
            "params": {"rsi_max": rv, "dry_up": kv, "stop": sp, "exit": mo},
            "trial_counted": False,
            "peak_heat": round(peak_heat, 4),
            "skew": round(sk, 4),
            "kurt": round(ku, 4),
            **m,
        }
        print(json.dumps(rec, ensure_ascii=False))
        if a.reval_out:
            p = Path(a.reval_out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    if len(combos) != cfg["n_declared"]:
        print(f"ERROR: combos={len(combos)} != {cfg['n_declared']}.", file=sys.stderr)
        return 2
    syms = load_universe(cfg["start"], cfg["end"])
    print(f"universe={len(syms)}", flush=True)
    bars = load_bars(syms, cfg["start"], cfg["end"])
    for s, bs in bars.items():
        if any(b[0] >= "2025-01-01" for b in bs):
            print(f"ERROR: bar OOS ({s}).", file=sys.stderr)
            return 2
    pre = {s: precompute(bs) for s, bs in bars.items()}
    gate = load_gate(cfg["start"], cfg["end"])
    if not gate:
        print("ERROR: market_wide_breadth_daily empty.", file=sys.stderr)
        return 2
    results = []
    with open(a.ledger, "a", encoding="utf-8") as led:
        for j, (rv, kv, sp, mo) in enumerate(combos, 91):
            pooled = []
            for s, pc in pre.items():
                for t in simulate(pc, gate, cfg, rv, kv, sp, mo, a.nav):
                    t["symbol"] = s
                    pooled.append(t)
            m = metrics(pooled, a.nav)
            sk, ku = trade_moments(pooled, a.nav)
            rec = {
                "event": "grid_trial",
                "trial_id": j,
                "grid": cfg["grid_name"],
                "data_scope": "OHLCV_only",
                "params": {"rsi_max": rv, "dry_up": kv, "stop": sp, "exit": mo},
                "window": [cfg["start"], cfg["end"]],
                "skew": round(sk, 4),
                "kurt": round(ku, 4),
                **m,
            }
            led.write(json.dumps(rec, ensure_ascii=False) + "\n")
            results.append(rec)
            print(
                f"trial {j:03d}/114 rsi={rv} dry={kv} stop={sp} exit={mo}: "
                f"n={m['n']} sharpe={m['sharpe']} pf={m['profit_factor']} "
                f"mdd={m['mdd_pct']}% wr={m['win_rate']}",
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
            dd = deflated_sharpe(r["sharpe"], max(r["n"], 3), r["skew"], r["kurt"], var_t, n_tot)
        except ValueError as e:
            print(f"trial {r['trial_id']}: DSR ERROR {e}")
            continue
        gate = dd["dsr"] > 0.95 and r["profit_factor"] != float("inf") and r["profit_factor"] >= 1.6 and r["mdd_pct"] > -15.0
        verdicts.append(
            {
                "trial_id": r["trial_id"],
                "params": r["params"],
                "sharpe": r["sharpe"],
                "n": r["n"],
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
    print(f"GATE2-H4: {sum(v['gate2_pass'] for v in verdicts)}/5 pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
