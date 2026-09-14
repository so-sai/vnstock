"""a9x6 — A9 expanded: 6 questions on Gold/China/Japan vs M (DESCRIPTIVE, 0 trials).

M observable = regime_history (regime_score, status) + transmission_pit.
Q1 IC(Gold|M): R2(VN_fwd ~ M) vs +Gold, +Gold/VN.
Q2 Transitions: Gold moves vs M status flips (lead-lag counts).
Q3 2023-25 subsample + Gold/VN 2x2 joint table.
Q4 CNY beyond Gold: marginal R2 split.
Q5 Japan: missing (restated, no web import).
Q6 Asia-led regime: joint cells frequency/persistence/forward returns.
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


def daily(var: str, start: str, end: str) -> dict[str, float]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, value FROM macro_history WHERE variable=? AND date>=? AND date<=? ORDER BY date", (var, start, end)
    ).fetchall()
    con.close()
    out: dict[str, float] = {}
    for d, v in rows:
        if v is not None:
            out[d] = float(v)
    return out


def regime(start: str, end: str) -> dict[str, tuple[float, str]]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, regime_score, status FROM regime_history WHERE date>=? AND date<=? ORDER BY date", (start, end)
    ).fetchall()
    con.close()
    return {d: (s if s is not None else 0.0, st or "?") for d, s, st in rows}


def load_vn(start: str, end: str) -> dict[str, float]:
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT date, close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date>=? AND date<=? ORDER BY date", (start, end)
    ).fetchall()
    con.close()
    return {d: c for d, c in rows if c}


def ols_r2(X: list[list[float]], y: list[float]) -> float:
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
    ap = argparse.ArgumentParser(description="A9x6 expanded audit")
    ap.add_argument("--start", default="2021-04-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    g = daily("GOLD_XAU", a.start, a.end)
    cny = daily("USD_CNY", a.start, a.end)
    reg = regime(a.start, a.end)
    vn = load_vn(a.start, a.end)
    print(f"coverage: gold={len(g)} cny={len(cny)} regime_days={len(reg)} vn={len(vn)}")
    if reg:
        print(f"regime bounds: {min(reg)}..{max(reg)} statuses={sorted({s for _, s in reg.values()})}")
    cal = sorted(set(g) & set(cny) & set(reg) & set(vn))
    print(f"overlap days: {len(cal)} ({cal[0]}..{cal[-1]})" if cal else "NO OVERLAP")

    def chg(s: dict[str, float], d: str, lb: int = 20) -> float | None:
        ds = sorted(x for x in s if x <= d)
        if len(ds) <= lb:
            return None
        a_, b_ = s[ds[-1]], s[ds[-1 - lb]]
        return math.log(a_ / b_) if a_ > 0 and b_ > 0 else None

    def fwd(h: int, d: str, vdates: list[str], vc: dict[str, float]) -> float | None:
        try:
            i = vdates.index(d)
        except ValueError:
            return None
        if i + h >= len(vdates):
            return None
        p0, p1 = vc[vdates[i]], vc[vdates[i + h]]
        return math.log(p1 / p0) if p0 > 0 and p1 > 0 else None

    vdates = sorted(vn)
    rows = []
    for d in cal:
        cg, cc = chg(g, d), chg(cny, d)
        cv = chg(vn, d)
        if cg is None or cc is None or cv is None:
            continue
        sc, st = reg[d]
        gv = cg - cv  # gold-vs-VN 20d spread
        rows.append({"d": d, "score": sc, "status": st, "gold": cg, "cny": cc, "gv": gv, "vn20": cv})
    print(f"feature rows: {len(rows)}")
    res: dict = {
        "window": [a.start, a.end],
        "n": len(rows),
        "q5_japan": "UNTESTABLE (no JGB/JPY; $62B headline NOT imported)",
        "q1_ic_gold_given_m": {},
        "q4_cny_beyond_gold": {},
        "q3_subsample_2023_25": {},
        "q6_joint": {},
        "q2_transitions": {},
    }
    for h in (5, 20, 60):
        ys, Xm, Xg, Xc = [], [], [], []
        for r in rows:
            f = fwd(h, r["d"], vdates, vn)
            if f is None:
                continue
            ys.append(f)
            Xm.append([1.0, r["score"]])
            Xg.append([1.0, r["score"], r["gold"], r["gv"]])
            Xc.append([1.0, r["score"], r["gold"], r["gv"], r["cny"]])
        if len(ys) < 60:
            continue
        res["q1_ic_gold_given_m"][h] = {"n": len(ys), "r2_m": round(ols_r2(Xm, ys), 4), "r2_m_gold": round(ols_r2(Xg, ys), 4)}
        res["q4_cny_beyond_gold"][h] = {"r2_m_gold_cny": round(ols_r2(Xc, ys), 4)}
        print(
            f"h={h} n={len(ys)} R2[M]={res['q1_ic_gold_given_m'][h]['r2_m']} "
            f"R2[M+Gold]={res['q1_ic_gold_given_m'][h]['r2_m_gold']} "
            f"R2[+CNY]={res['q4_cny_beyond_gold'][h]['r2_m_gold_cny']}"
        )
    # Q3: 2023-25 + joint 2x2 (trailing-20d signs) with fwd20 stats
    sub = [r for r in rows if r["d"] >= "2023-01-01"]
    cells: dict[str, list[float]] = {}
    for r in sub:
        f = fwd(20, r["d"], vdates, vn)
        if f is None:
            continue
        key = ("G+" if r["gold"] > 0 else "G-") + ("V+" if r["vn20"] > 0 else "V-")
        cells.setdefault(key, []).append(f)
    for k, v in sorted(cells.items()):
        m = sum(v) / len(v)
        res["q3_subsample_2023_25"][k] = {
            "n": len(v),
            "mean_fwd20": round(m, 5),
            "hit_rate": round(sum(1 for x in v if x > 0) / len(v), 3),
        }
    print("Q3 2x2:", json.dumps(res["q3_subsample_2023_25"], ensure_ascii=False))
    # Q6: full-window joint + persistence (P(stay|cell))
    cells6: dict[str, list[float]] = {}
    seq = []
    for r in rows:
        f = fwd(20, r["d"], vdates, vn)
        key = ("G+" if r["gold"] > 0 else "G-") + ("V+" if r["vn20"] > 0 else "V-")
        seq.append(key)
        if f is not None:
            cells6.setdefault(key, []).append(f)
    for k, v in sorted(cells6.items()):
        m = sum(v) / len(v)
        res["q6_joint"][k] = {"n": len(v), "mean_fwd20": round(m, 5), "hit": round(sum(1 for x in v if x > 0) / len(v), 3)}
    stays = sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1])
    res["q6_joint"]["persistence"] = round(stays / max(len(seq) - 1, 1), 3)
    res["q6_joint"]["freq"] = {k: round(seq.count(k) / len(seq), 3) for k in sorted(set(seq))}
    print("Q6 2x2:", json.dumps(res["q6_joint"], ensure_ascii=False))
    # Q2: M status flips vs Gold 20d move in prior 20d window
    flips, pre_gold = 0, []
    prev_st = None
    for r in rows:
        if prev_st is not None and r["status"] != prev_st:
            flips += 1
            pre_gold.append(r["gold"])
        prev_st = r["status"]

    def med(xs: list[float]) -> float | None:
        return round(sorted(xs)[len(xs) // 2], 5) if xs else None

    res["q2_transitions"] = {
        "status_flips": flips,
        "median_gold20d_before_flip": med(pre_gold),
        "median_gold20d_all": med([r["gold"] for r in rows]),
    }
    print("Q2:", json.dumps(res["q2_transitions"], ensure_ascii=False))
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
