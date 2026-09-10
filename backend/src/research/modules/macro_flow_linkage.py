"""macro_flow_linkage.py — Lien ket Lai suat/TPCP -> Dong tien Co phieu/Vang (Tier-2).

Pham vi: backend/src/research/modules/ — chi doc:
  daily_ohlcv + macro_history (screener_cache.db). CAM financial_facts.db.

A-priori hypothesis:
  Khi ap luc lai suat (rate pressure) tang dot bien, dong tien rut khoi
  tai san rui ro -> chan mua moi (Cash Shield). Khi ap luc on dinh,
  diem bung no khoi luong sau co that (VCP + volume thrust) co xac suat
  tiep dien cao hon.

DU LIEU THAT (khao sat 2026-09-10, ghi nhan cung de tranh snooping):
  - VGB10Y: chi 91 rows tu 2026-06-11, flat 4.542 (seed) -> LOAI khoi IS.
  - US10Y: tu 2021-04-05 (thay the VGB10Y cho IS).
  - INTERBANK_ON: tu 2023-01-02 (gate chi kich hoat khi co du lieu).
  - FED_TARGET_RATE: tu 2021-01-01 (muc nen chi phi von).
  - GOLD_XAU: tu 2021-04-05 (do lech pha dong tien vang vs co phieu).
  - VIX: tu 2022-01-03 (bien dong toan cau).
  Component thieu du lieu tai T => bo qua + danh dau degraded (khong che).

Tat ca ham chi dung du lieu co date <= T (causal). Kiem chung bang test
lookahead-invariance: cat chuoi tai D, tin hieu date<=D phai giong he
nhu chay full (xem __main__ --selftest).
"""

from __future__ import annotations

import argparse
import json
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

# Mac dinh grid (DE XUAT, chua chay — N do operator duyet; xem ledger):
# Don vi: diem phan tram (pp) thay doi trong 60 phien — on dinh thang do
# hon % ROC (buoc 25bp cua FED tren nen 5% da la 5% ROC, v.v.).
THETA_RATE_GRID = [0.5, 0.75, 1.0]  # pp thay doi ap luc lai suat
K_VOL_GRID = [1.3, 1.5, 1.8, 2.0]  # he so volume thrust
ATR_TRAIL_GRID = [1.5, 2.0, 2.5]  # trailing multiplier
VCP_WIN_S, VCP_WIN_L = 10, 60
PRESSURE_LOOKBACK = 60  # phien


# ---------- Loaders (causal: WHERE date <= T tai diem su dung) ----------


def load_series(table: str, var_col: str, var: str, start: str, end: str) -> dict[str, float]:
    con = sqlite3.connect(str(SCREENER_DB))
    try:
        rows = con.execute(
            f"SELECT date, value FROM {table} WHERE {var_col}=? AND date>=? AND date<=? ORDER BY date",
            (var, start, end),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    con.close()
    return {r[0]: float(r[1]) for r in rows if r[1] is not None}


def load_macro(var: str, start: str, end: str) -> dict[str, float]:
    return load_series("macro_history", "variable", var, start, end)


def load_bars(symbol: str, start: str, end: str) -> list[dict]:
    con = sqlite3.connect(str(SCREENER_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT date, open, high, low, close, volume FROM daily_ohlcv WHERE symbol=? AND date>=? AND date<=? ORDER BY date",
        (symbol, start, end),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------- Macro pressure index (chi du lieu <= T) ----------


def _pp_change(series: dict[str, float], date: str, lookback: int) -> float | None:
    """Thay doi tuyet doi (diem phan tram) v(T) - v(T-lookback).

    Dung pp thay vi % ROC: lai suat nen thap (0-5%) bien % ROC cua buoc
    25bp thanh 5-10%, giu shield dong ~moi luc. None neu thieu moc.
    """
    dates = sorted(d for d in series if d <= date)
    if len(dates) < 2:
        return None
    base_d = dates[-1]
    past = [d for d in dates if d <= base_d]
    anchor = past[0] if len(past) <= lookback else past[-1 - lookback]
    return series[base_d] - series[anchor]


def macro_pressure_index(
    date: str,
    us10y: dict[str, float],
    ib_on: dict[str, float],
    fed_rate: dict[str, float],
    lookback: int = PRESSURE_LOOKBACK,
) -> dict:
    """Tra ve {pressure, blocked_components, degraded}.

    pressure = max thay doi pp cua cac kenh co du lieu. Khong co kenh
    nao => pressure=None (gate OFF, khong chan).
    """
    comps: dict[str, float] = {}
    for name, s in (("US10Y", us10y), ("IB_ON", ib_on), ("FED", fed_rate)):
        r = _pp_change(s, date, lookback)
        if r is not None:
            comps[name] = r
    if not comps:
        return {"pressure": None, "components": {}, "degraded": True}
    return {"pressure": max(comps.values()), "components": comps, "degraded": False}


def cash_shield(date: str, theta_rate: float, **series: dict[str, float]) -> dict:
    """True => chan mua moi tai date (ap luc lai suat vuot nguong)."""
    mpi = macro_pressure_index(date, **series)
    p = mpi["pressure"]
    return {"shield": bool(p is not None and p > theta_rate), **mpi, "theta": theta_rate}


# ---------- Volume thrust + VCP (chi du lieu <= i) ----------


def _adv(vols: list[float], i: int, win: int = 20) -> float:
    w = vols[max(0, i - win + 1) : i + 1]
    return sum(w) / len(w) if w else 0.0


def _atr(bars: list[dict], i: int, win: int = 14) -> float:
    trs = []
    for k in range(max(1, i - win + 1), i + 1):
        hi, lo, pc = bars[k]["high"], bars[k]["low"], bars[k - 1]["close"]
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _range_pct(bars: list[dict], i: int, win: int) -> float:
    w = bars[max(0, i - win + 1) : i + 1]
    hi = max(b["high"] for b in w)
    lo = min(b["low"] for b in w)
    c = bars[i]["close"]
    return (hi - lo) / c if c else 0.0


def volume_thrust_detector(
    bars: list[dict],
    i: int,
    k_vol: float = 1.5,
    vcp_ratio: float = 0.5,
) -> dict:
    """Danh gia tin hieu tai bar i — chi doc bars[<=i]."""
    if i < VCP_WIN_L or i >= len(bars):
        return {"signal": False, "reason": "warmup"}
    vols = [b["volume"] for b in bars[: i + 1]]
    a20 = _adv(vols, i)
    if a20 <= 0:
        return {"signal": False, "reason": "no_adv"}
    b = bars[i]
    vol_ok = b["volume"] > k_vol * a20
    vcp_ok = _range_pct(bars, i, VCP_WIN_S) < vcp_ratio * _range_pct(bars, i, VCP_WIN_L)
    resist = max(x["high"] for x in bars[max(0, i - 20) : i])
    breakout_ok = b["close"] > resist
    sig = vol_ok and vcp_ok and breakout_ok
    return {"signal": sig, "date": b["date"], "vol_ok": vol_ok, "vcp_ok": vcp_ok, "breakout_ok": breakout_ok}


def scan_signals(
    bars: list[dict],
    macro: dict[str, dict[str, float]],
    theta_rate: float,
    k_vol: float,
) -> list[dict]:
    """Quet toan chuoi: shield macro + thrust. Moi bar i chi thay du lieu <=i."""
    out: list[dict] = []
    for i in range(len(bars)):
        det = volume_thrust_detector(bars, i, k_vol=k_vol)
        if not det["signal"]:
            continue
        sh = cash_shield(bars[i]["date"], theta_rate, **macro)
        if sh["shield"]:
            continue
        out.append({"date": bars[i]["date"], "pressure": sh["pressure"]})
    return out


# ---------- Selftest: lookahead-invariance ----------


def selftest(symbol: str = "VCB", start: str = "2023-01-01", end: str = "2024-12-31") -> dict:
    bars = load_bars(symbol, start, end)
    macro = {
        "us10y": load_macro("US10Y", start, end),
        "ib_on": load_macro("INTERBANK_ON", start, end),
        "fed_rate": load_macro("FED_TARGET_RATE", start, end),
    }
    full = scan_signals(bars, macro, theta_rate=1.0, k_vol=1.5)
    if len(bars) < 100:
        return {"ok": False, "reason": "not enough bars"}
    cut = len(bars) * 2 // 3
    cut_date = bars[cut]["date"]
    trunc = [b for b in bars if b["date"] <= cut_date]
    macro_t = {k: {d: v for d, v in s.items() if d <= cut_date} for k, s in macro.items()}
    part = scan_signals(trunc, macro_t, theta_rate=1.0, k_vol=1.5)
    full_cut = [s for s in full if s["date"] <= cut_date]
    ok = [s["date"] for s in part] == [s["date"] for s in full_cut]
    return {
        "ok": ok,
        "symbol": symbol,
        "cut_date": cut_date,
        "n_truncated": len(part),
        "n_full_cut": len(full_cut),
        "n_full_total": len(full),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Macro-flow linkage — Tier-2")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--symbols", default="VCB,TCB,MBB")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--theta", type=float, default=1.0)
    ap.add_argument("--kvol", type=float, default=1.5)
    a = ap.parse_args()
    if a.selftest:
        r = selftest()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    macro = {
        "us10y": load_macro("US10Y", a.start, a.end),
        "ib_on": load_macro("INTERBANK_ON", a.start, a.end),
        "fed_rate": load_macro("FED_TARGET_RATE", a.start, a.end),
    }
    cov = {k: len(v) for k, v in macro.items()}
    print(f"macro coverage: {cov}")
    total = 0
    for s in [x.strip().upper() for x in a.symbols.split(",") if x.strip()]:
        bars = load_bars(s, a.start, a.end)
        sigs = scan_signals(bars, macro, a.theta, a.kvol)
        total += len(sigs)
        print(f"{s}: bars={len(bars)} signals={len(sigs)} first3={[x['date'] for x in sigs[:3]]}")
    print(f"TOTAL signals={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
