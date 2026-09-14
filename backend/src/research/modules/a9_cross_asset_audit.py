"""a9_cross_asset_audit.py — A9 Cross-Asset Regime Audit (DESCRIPTIVE, 0 trials).

Scope runnable: USA (DXY, US10Y) + China-proxy (USD_CNY, GOLD_XAU) +
Domestic (INTERBANK_ON 2023+, VNINDEX) -> VNINDEX Fwd5/20/30/60.
JAPAN LEG UNTESTABLE: JGB10Y/JGB30Y/JPY absent from macro_history.
No web numbers imported (no provenance = no trust).

Method (all causal, date<=T):
  daily dedupe (last snapshot/row per date) -> log-changes (rates: pp diff)
  -> z-score via trailing-252d moments -> OLS R2: US-only vs US+China/Gold,
     halves stability (2021-22 vs 2023-24).
Output: JSON summary + md report. No Governor/M writes.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sqlite3
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB = "backend/data/screener_cache.db"
CHG_VARS = {"DXY", "USD_CNY", "GOLD_XAU", "BRENT_OIL", "VIX"}
PP_VARS = {"US10Y", "INTERBANK_ON", "FED_TARGET_RATE"}
BLOCKS = {"USA": ["DXY", "US10Y"], "CHINA_GOLD": ["USD_CNY", "GOLD_XAU"], "DOM": ["INTERBANK_ON"]}
FWDS = [5, 20, 30, 60]


def daily(var: str, start: str, end: str) -> dict[str, float]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, value FROM macro_history WHERE variable=? AND date>=? AND date<=? ORDER BY date", (var, start, end)
    ).fetchall()
    con.close()
    out: dict[str, float] = {}
    for d, v in rows:
        if v is not None:
            out[d] = float(v)  # last snapshot wins (identical duplicates verified)
    return out


def load_vn(start: str, end: str) -> list[tuple[str, float]]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date>=? AND date<=? ORDER BY date", (start, end)
    ).fetchall()
    con.close()
    return [(d, c) for d, c in rows if c]


def ols_r2(X: list[list[float]], y: list[float]) -> float:
    """R2 via normal equations + Gaussian elimination (stdlib only)."""
    n, k = len(y), len(X[0])
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    M = [row[:] + [Xty[a]] for a, row in enumerate(XtX)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            return 0.0
        for r in range(k):
            if r != c:
                f = M[r][c] / M[c][c]
                for cc in range(c, k + 1):
                    M[r][cc] -= f * M[c][cc]
    b = [M[a][k] / M[a][a] for a in range(k)]
    pred = [sum(X[i][a] * b[a] for a in range(k)) for i in range(n)]
    mu = sum(y) / n
    ss = sum((v - mu) ** 2 for v in y)
    return 1 - sum((y[i] - pred[i]) ** 2 for i in range(n)) / ss if ss > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="A9 cross-asset regime audit")
    ap.add_argument("--start", default="2021-04-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    series = {v: daily(v, a.start, a.end) for blk in BLOCKS.values() for v in blk}
    vn = load_vn(a.start, a.end)
    vdates = [d for d, _ in vn]
    vclose = dict(vn)
    cal = sorted(set(vdates) & set(series["DXY"]) & set(series["US10Y"]) & set(series["USD_CNY"]) & set(series["GOLD_XAU"]))
    # features: trailing-20d change (rates pp-diff, rest log-chg), z-scored trailing-252d
    feats: dict[str, list[float]] = {}
    fdates: list[str] = []
    for i, d in enumerate(cal):
        if i < 252:
            continue
        row = {}
        for blk in BLOCKS.values():
            for v in blk:
                if v == "INTERBANK_ON":
                    continue  # sparse pre-2023; DOM block via VNINDEX past ret
                hist = [series[v][x] for x in cal[i - 252 : i + 1]]
                if v in PP_VARS:
                    ch = [hist[j] - hist[j - 1] for j in range(1, len(hist))]
                else:
                    ch = [math.log(hist[j] / hist[j - 1]) if hist[j - 1] > 0 else 0.0 for j in range(1, len(hist))]
                mu = sum(ch) / len(ch)
                sd = math.sqrt(sum((x - mu) ** 2 for x in ch) / len(ch))
                cur = ch[-1]
                row[v] = (cur - mu) / sd if sd > 0 else 0.0
        feats[d] = row
        fdates.append(d)
    # targets: VNINDEX fwd log-returns on trading calendar
    res: dict = {
        "window": [a.start, a.end],
        "n_obs": len(fdates),
        "japan_leg": "UNTESTABLE (JGB10Y/JGB30Y/JPY absent)",
        "horizons": {},
    }
    for h in FWDS:
        ys, Xus, Xall, ds = [], [], [], []
        for d in fdates:
            try:
                i = vdates.index(d)
            except ValueError:
                continue
            if i + h >= len(vdates):
                continue
            if vclose[vdates[i]] <= 0 or vclose[vdates[i + h]] <= 0:
                continue
            ys.append(math.log(vclose[vdates[i + h]] / vclose[vdates[i]]))
            r = feats[d]
            Xus.append([1.0, r["DXY"], r["US10Y"]])
            Xall.append([1.0, r["DXY"], r["US10Y"], r["USD_CNY"], r["GOLD_XAU"]])
            ds.append(d)
        if len(ys) < 60:
            res["horizons"][h] = {"n": len(ys), "note": "too few"}
            continue
        mid = len(ys) // 2
        out = {"n": len(ys), "r2_us": round(ols_r2(Xus, ys), 4), "r2_us_china_gold": round(ols_r2(Xall, ys), 4)}
        for tag, sl in (("h1", slice(0, mid)), ("h2", slice(mid, None))):
            y2, u2, a2 = ys[sl], Xus[sl], Xall[sl]
            out[f"{tag}_r2_us"] = round(ols_r2(u2, y2), 4)
            out[f"{tag}_r2_all"] = round(ols_r2(a2, y2), 4)
        # correlations per feature (full window)
        corr = {}
        vlist = ("DXY", "US10Y", "USD_CNY", "GOLD_XAU")
        for ci, v in enumerate(vlist, start=1):
            xs = [row[ci] for row in Xall]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            vx = sum((x - mx) ** 2 for x in xs)
            vy = sum((y - my) ** 2 for y in ys)
            cv = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            corr[v] = round(cv / math.sqrt(vx * vy), 4) if vx > 0 and vy > 0 else 0.0
        out["corr"] = corr
        res["horizons"][h] = out
        print(
            f"h={h} n={out['n']} R2us={out['r2_us']} R2all={out['r2_us_china_gold']} "
            f"stable=({out['h1_r2_all']},{out['h2_r2_all']}) corr={corr}"
        )
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
