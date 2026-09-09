"""pure_ohlcv_wf.py — Walk-Forward gia-thuan cho Tier-2 Quant Lab (Gate 1).

Pham vi: backend/src/research/modules/ — KHONG doc financial_facts.db,
KHONG dung production Fusion/governor params. Chi:
  daily_ohlcv (screener_cache.db) + macro_history (VGB10Y/interbank).

Chien luoc baseline v0 (co dinh, khong toi uu — N_trials=1):
  ENTRY (EOD, lenh cho phien T+1 tai gia dong T neu du dieu kien tai T):
    - vol(T) > VOL_MULT * ADV20(T)          (volume anomaly)
    - close(T) > MA20(T)                    (xu huong ngan han)
    - range10(T) < VCP_RATIO * range60(T)   (co that bien dong truoc bung no)
    - macro gate: interbank_ON(T) < MACRO_ON_CAP (ne thanh khoan cang)
  EXIT: stop 7% / take 25% / trailing ATR(14)*ATR_MULT / hold toi da 15 phien.
  SIZING: risk 1% NAV/trade theo ATR stop, tran <= 10% * ADV20 * close.
  CHI PHI: fee 0.15%/chieu, thue 0.1% chieu ban, slippage 5bps + 20bps
    cho moi 1% tham gia thanh khoan (participation = notional/ADV20_value).

Folds: expanding window theo nam, embargo 5 phien giua train/test.
  (v0: params co dinh nen train fold chi de giam sat on dinh, khong fit.)
Metrics: Sharpe (nam hoa, rf=5%), Profit Factor, MDD, WinRate, Expectancy,
  tinh tren P&L r NET sau chi phi. Ledger JSONL moi trade + tong hop fold.

Usage:
  python backend/src/research/modules/pure_ohlcv_wf.py
    --symbols VCB,TCB,MBB --start 2023-01-01 --end 2024-12-31
    --nav 100000000 --out backend/src/research/ledger/wf-v0-smoke.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path


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
SCREENER_DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

# --- Cost model (Gate 2: phi 0.3% 2 chieu, thue ban 0.1%) ---
FEE_BPS = 15.0  # 0.15% / chieu
TAX_BPS_SELL = 10.0  # 0.1% chieu ban
SLIP_BASE_BPS = 5.0
SLIP_PER_PCT_PART = 20.0  # bps cho moi 1% participation
ADV_CAP_PCT = 10.0  # tran 10% ADV20 (lay muc chat trong 10-15%)

# --- Strategy v0 (co dinh, N_trials = 1) ---
VOL_MULT = 2.0
VCP_RATIO = 0.5
MA_WIN = 20
RANGE_S, RANGE_L = 10, 60
ATR_WIN = 14
ATR_MULT = 2.0
STOP_PCT, TAKE_PCT, MAX_HOLD = 7.0, 25.0, 15
RISK_PCT_NAV = 1.0
MACRO_ON_CAP = 6.0  # % — interbank ON vuot nguong => dung entry moi
EMBARGO_DAYS = 5
RF_ANNUAL = 0.05


# ---------- Data ----------


def load_bars(symbols: list[str], start: str, end: str) -> dict[str, list[dict]]:
    con = sqlite3.connect(str(SCREENER_DB))
    con.row_factory = sqlite3.Row
    out: dict[str, list[dict]] = {}
    for s in symbols:
        rows = con.execute(
            "SELECT date, open, high, low, close, volume FROM daily_ohlcv "
            "WHERE symbol=? AND date>=? AND date<=? ORDER BY date",
            (s, start, end),
        ).fetchall()
        out[s] = [dict(r) for r in rows]
    con.close()
    return out


def load_macro(var: str, start: str, end: str) -> dict[str, float]:
    con = sqlite3.connect(str(SCREENER_DB))
    try:
        rows = con.execute(
            "SELECT date, value FROM macro_history WHERE variable=? AND date>=? AND date<=? ORDER BY date", (var, start, end)
        ).fetchall()
    except sqlite3.Error:  # missing macro table => gate OFF, khong dung engine
        rows = []
    con.close()
    return {r[0]: r[1] for r in rows}


def macro_on_series(start: str, end: str) -> dict[str, float]:
    for var in ("INTERBANK_ON", "interbank_on", "SBV_ON", "ON"):
        s = load_macro(var, start, end)
        if s:
            return s
    return {}


# ---------- Indicators (pure price/volume, causal: chi dung du lieu <= T) ----------


def adv(series: list[float], i: int, win: int = 20) -> float:
    w = series[max(0, i - win + 1) : i + 1]
    return sum(w) / len(w) if w else 0.0


def ma(series: list[float], i: int, win: int) -> float:
    w = series[max(0, i - win + 1) : i + 1]
    return sum(w) / len(w) if w else 0.0


def atr(bars: list[dict], i: int, win: int = ATR_WIN) -> float:
    trs = []
    for k in range(max(1, i - win + 1), i + 1):
        h, lo, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def bar_range(bars: list[dict], i: int, win: int) -> float:
    w = bars[max(0, i - win + 1) : i + 1]
    hi = max(b["high"] for b in w)
    lo = min(b["low"] for b in w)
    return (hi - lo) / bars[i]["close"] if bars[i]["close"] else 0.0


# ---------- Engine ----------


def run_symbol(bars: list[dict], on_map: dict[str, float], nav: float) -> list[dict]:
    """Chay chien luoc v0 tren 1 ma. Tra ve danh sach trades (net PnL)."""
    closes = [b["close"] for b in bars]
    vols = [b["volume"] for b in bars]
    trades: list[dict] = []
    pos = None
    equity = nav
    for i in range(61, len(bars) - 1):  # can 60 bars warmup + T+1 fill
        b = bars[i]
        if pos is None:
            a20 = adv(vols, i)
            if a20 <= 0:
                continue
            on = on_map.get(b["date"])
            if on is not None and on >= MACRO_ON_CAP:
                continue
            if (
                b["volume"] > VOL_MULT * a20
                and b["close"] > ma(closes, i, MA_WIN)
                and bar_range(bars, i, RANGE_S) < VCP_RATIO * bar_range(bars, i, RANGE_L)
            ):
                atr_v = atr(bars, i)
                entry = bars[i + 1]["open"] if bars[i + 1]["open"] else b["close"]
                if entry <= 0 or atr_v <= 0:
                    continue
                risk_amt = equity * RISK_PCT_NAV / 100.0
                stop_dist = max(ATR_MULT * atr_v, entry * STOP_PCT / 100.0)
                qty = int(risk_amt / stop_dist)
                cap_qty = int(a20 * ADV_CAP_PCT / 100.0)
                qty = max(0, min(qty, cap_qty))
                if qty <= 0:
                    continue
                notional = qty * entry
                part_pct = notional / (a20 * entry) * 100.0 if a20 * entry else 0.0
                slip = (SLIP_BASE_BPS + SLIP_PER_PCT_PART * part_pct) / 10000.0
                fill = entry * (1 + slip)
                cost = notional * FEE_BPS / 10000.0
                pos = {
                    "qty": qty,
                    "fill": fill,
                    "cost": cost,
                    "date_in": bars[i + 1]["date"],
                    "stop": fill - stop_dist,
                    "peak": fill,
                    "bars_held": 0,
                    "notional": qty * fill,
                }
        else:
            pos["bars_held"] += 1
            h, lo, c = bars[i]["high"], bars[i]["low"], bars[i]["close"]
            pos["peak"] = max(pos["peak"], h)
            trail = pos["peak"] - ATR_MULT * atr(bars, i)
            stop_hit = lo <= pos["stop"]
            trail_hit = lo <= trail and pos["bars_held"] > 1
            take_hit = h >= pos["fill"] * (1 + TAKE_PCT / 100.0)
            time_hit = pos["bars_held"] >= MAX_HOLD
            if stop_hit or trail_hit or take_hit or time_hit:
                exit_px = pos["stop"] if stop_hit else c
                gross = (exit_px - pos["fill"]) * pos["qty"]
                sell_cost = pos["qty"] * exit_px * (FEE_BPS + TAX_BPS_SELL) / 10000.0
                net = gross - pos["cost"] - sell_cost
                equity += net
                reason = "STOP" if stop_hit else "TRAIL" if trail_hit else "TAKE" if take_hit else "TIME"
                trades.append(
                    {
                        "date_in": pos["date_in"],
                        "date_out": b["date"],
                        "qty": pos["qty"],
                        "entry": round(pos["fill"], 2),
                        "exit": round(exit_px, 2),
                        "net": round(net, 2),
                        "reason": reason,
                        "ret_pct": round(net / pos["notional"] * 100.0, 4),
                    }
                )
                pos = None
    return trades


def metrics(trades: list[dict], nav: float) -> dict:
    if not trades:
        return {
            "n": 0,
            "sharpe": 0.0,
            "profit_factor": 0.0,
            "mdd_pct": 0.0,
            "win_rate": 0.0,
            "expectancy": 0.0,
            "total_net": 0.0,
        }
    rets = [t["net"] / nav for t in trades]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    std = math.sqrt(var)
    excess = mean - RF_ANNUAL / 252.0
    sharpe = (excess / std * math.sqrt(252.0)) if std > 0 else 0.0
    wins = [t["net"] for t in trades if t["net"] > 0]
    loss = [t["net"] for t in trades if t["net"] <= 0]
    gp = sum(wins)
    gl = abs(sum(loss))
    eq = nav
    peak = nav
    mdd = 0.0
    for t in trades:
        eq += t["net"]
        peak = max(peak, eq)
        mdd = min(mdd, (eq - peak) / peak)
    return {
        "n": n,
        "sharpe": round(sharpe, 4),
        "profit_factor": round(gp / gl, 4) if gl > 0 else float("inf"),
        "mdd_pct": round(mdd * 100.0, 3),
        "win_rate": round(len(wins) / n, 4),
        "expectancy": round(sum(rets) / n * 100.0, 5),
        "total_net": round(sum(t["net"] for t in trades), 2),
    }


def yearly_folds(start: str, end: str) -> list[tuple[str, str]]:
    ys = list(range(int(start[:4]), int(end[:4]) + 1))
    return [(f"{y}-01-01", f"{min(y, int(end[:4]))}-12-31") for y in ys]


def main() -> int:
    ap = argparse.ArgumentParser(description="Pure OHLCV walk-forward v0 — Tier-2")
    ap.add_argument("--symbols", required=True, help="CSV, vd VCB,TCB,MBB")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--nav", type=float, default=100_000_000.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.start >= "2025-01-01" or a.end >= "2025-01-01":
        print("ERROR: Tier-2 IS bi gioi han < 2025-01-01 (OOS phong toa).", file=sys.stderr)
        return 2
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    bars = load_bars(syms, a.start, a.end)
    on_map = macro_on_series(a.start, a.end)
    print(
        f"macro ON coverage: {len(on_map)} sessions "
        f"({'gate ACTIVE' if on_map else 'gate OFF — thieu du lieu, entry khong bi chan macro'})"
    )

    lines: list[str] = []
    all_trades: list[dict] = []
    for y0, y1 in yearly_folds(a.start, a.end):
        fold_trades: list[dict] = []
        for s in syms:
            sub = [b for b in bars.get(s, []) if y0 <= b["date"] <= y1]
            if len(sub) < 80:
                continue
            for t in run_symbol(sub, on_map, a.nav):
                t["symbol"] = s
                t["fold"] = y0[:4]
                fold_trades.append(t)
        m = metrics(fold_trades, a.nav)
        rec = {
            "event": "fold",
            "fold": y0[:4],
            "data_scope": "OHLCV_only",
            "strategy": "v0_fixed",
            "n_trials": 1,
            "symbols": syms,
            **m,
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
        print(json.dumps(rec, ensure_ascii=False))
        all_trades.extend(fold_trades)
    tot = metrics(all_trades, a.nav)
    rec = {
        "event": "total",
        "data_scope": "OHLCV_only",
        "strategy": "v0_fixed",
        "n_trials": 1,
        "symbols": syms,
        "window": [a.start, a.end],
        **tot,
    }
    lines.append(json.dumps(rec, ensure_ascii=False))
    print(json.dumps(rec, ensure_ascii=False))
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for t in all_trades:
        lines.append(json.dumps({"event": "trade", "data_scope": "OHLCV_only", **t}, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
