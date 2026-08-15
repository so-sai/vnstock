"""opportunity_score_gate6.py — READ-ONLY Governor integration test (Gate 6).

Câu hỏi: SELECTION alpha của M_value có sống sót qua Execution/Budget machinery
của Governor không?

  A. Oracle-like daily top-K  : mỗi ngày rank cross-section theo M_value, mua
       top-K, giữ H=20 phiên, không budget/cap/dedup. Đây là "ceiling" của tín
       hiệu khi giao dịch thật (turnover, cost, MaxDD, concentration thật).
  B. Actual Governor integration: cùng tín hiệu M_value nhưng đi qua machinery
       thật của Selection Layer (`selection_layer.rank_day`/`select_year`):
       budget = 20 slot/năm (ceiling), daily_cap, dedup 1 symbol/năm.

   So sánh: nếu A tốt nhưng B xấu → lỗi ở execution/budget/state machinery.
   Nếu A và B cùng tốt → bằng chứng Feature→Selection→Governor→Realized Alpha.

Lưu ý (đã kiểm chứng replay_58_ts): OPEN/SCALE_IN deployment candidates CHỈ tồn
tại năm 2022 (p_gain < floor 0.55 → 2023-25 không có candidate). Do đó Branch B
KHÔNG dùng ledger deployment rows (p_gain-gated) mà SYNTHESIZE candidate pool =
toàn bộ cross-section có M_value, rồi áp machinery thật. Đây là điểm khác biệt
cốt lõi với replay cũ (p_gain đã làm "không có gì để chọn").

Protocol:
  - M_value_all (6 valuation features, available-feature normalization, Gate 3A).
  - K sensitivity: A(K) với K = 1, 3, 5, 10; B(cap) với cap = 1 (config thật),
    3, 5, 10. Budget LUÔN = 20/năm — constraint, KHÔNG phải biến tối ưu alpha.
  - H = 20 phiên giữ (nhất quán forward return). Cost round-trip = 0.0045
    (đồng bộ `multi_factor_fusion.TRANSACTION_COST`).
  - Metric: n positions, turnover, gross/net return, WinRate, PF, MaxDD (net),
    concentration (HHI), regime entry, first-entry timing, IS 2022-24 vs
    OOS 2025 (descriptive), overlap H20/H60/H120 của positions đã chọn.
  - p_gain hoàn toàn loại khỏi selection. Không chọn K chỉ vì số đẹp.

READ-ONLY: không ghi replay DB / screener / financial_facts. Không sửa
selection_layer.py / decision_budget.py / company_state.py.

Usage (từ project root):
  python -X utf8 backend/src/research/opportunity_score_gate6.py
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

# ── Path hydration (Sentinel v2.1 Anchor) ──────────────────────────────────
_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current
while PROJECT_ROOT != PROJECT_ROOT.parent:
    if (PROJECT_ROOT / "AGENTS.md").exists() and (PROJECT_ROOT / "backend").is_dir():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
REPLAY_DIR = DATA_DIR / "replays" / "replay_58_ts"

sys.path.insert(0, str(BACKEND / "src"))
from research.opportunity_score_gate5 import build_scores
from research.opportunity_score_research import (
    MIN_SYMBOLS_PER_DAY,
    build_feature_matrix,
    load_pool,
    load_prices,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

YEARS = ("2022", "2023", "2024", "2025")
IS_YEARS = ("2022", "2023", "2024")
OOS_YEARS = ("2025",)

HOLD = 20
TRANSACTION_COST = 0.0045  # 0.45% round-trip (fee + tax + slippage) — multi_factor_fusion
BUDGET = 20  # ceiling năm — constraint, không tối ưu
DAILY_CAP_ACTUAL = 1  # config Selection Layer thật (DEFAULT_DAILY_CAP)
K_VALUES = (1, 3, 5, 10)
CAP_VALUES = (1, 3, 5, 10)
COST_PER_SIDE = TRANSACTION_COST / 2.0


# ═══════════════════════════════════════════════════════════════════════════
# SIMULATION ENGINE (trade-level, fixed-hold, equal-weight)
# ═══════════════════════════════════════════════════════════════════════════


def simulate(
    scored: pd.DataFrame,
    close_pivot: pd.DataFrame,
    *,
    k: int | None = None,
    daily_cap: int | None = None,
    budget: int | None = None,
    dedup: bool = False,
    hold: int = HOLD,
) -> dict:
    """Chạy 1 config: selection theo M_value → mở position → giữ `hold` phiên.

    Args:
        k: top-K mỗi ngày (Oracle A).
        daily_cap: tối đa EXECUTE/ngày (Branch B machinery).
        budget: tổng slot/năm (None = không giới hạn — Oracle A).
        dedup: mỗi symbol EXECUTE tối đa 1 lần/năm (Branch B).

    Trả về dict tổng hợp (trade + NAV + concentration + regime + overlap).
    """
    df = scored.dropna(subset=["M_value_all", "r20"]).copy()
    if df.empty:
        return {"error": "no_scored_rows"}

    dates = sorted(df["date"].unique())
    ret_pivot = close_pivot.pct_change()
    n_days = len(dates)
    by_date = {d: g for d, g in df.groupby("date", sort=True)}

    open_pos: dict[str, int] = {}  # symbol -> entry index
    trades: list[dict] = []
    executed_year: dict[str, set[str]] = defaultdict(set)

    # per-day portfolio return & concentration
    daily_gross: list[float] = []
    daily_cost: list[float] = []
    daily_n_open: list[int] = []

    remaining = budget if budget is not None else math.inf
    cur_year: str | None = None

    for idx, date in enumerate(dates):
        g = by_date[date]
        if len(g) < MIN_SYMBOLS_PER_DAY:
            daily_gross.append(0.0)
            daily_cost.append(0.0)
            daily_n_open.append(len(open_pos))
            continue

        year = date[:4]
        if year != cur_year:
            cur_year = year
            if budget is not None:
                remaining = budget

        # ── selection ────────────────────────────────────────────────────
        top = g.sort_values("M_value_all", ascending=False)
        if dedup:
            top = top[~top["symbol"].isin(executed_year[year])]

        if budget is not None and remaining <= 0:
            selected = top.iloc[0:0]
        else:
            cap = daily_cap if daily_cap is not None else k
            cap = min(cap, int(remaining)) if budget is not None else cap
            selected = top.head(cap)

        # ── open new positions (buy at close[t]) ────────────────────────
        n_entries = 0
        for sym in selected["symbol"]:
            if sym in open_pos:
                continue
            open_pos[sym] = idx
            if dedup:
                executed_year[year].add(sym)
            if budget is not None:
                remaining -= 1
            n_entries += 1

        # ── close positions at hold ─────────────────────────────────────
        n_exits = 0
        for sym, t0 in list(open_pos.items()):
            if idx - t0 >= hold:
                entry_date = dates[t0]
                entry_row = df[(df["date"] == entry_date) & (df["symbol"] == sym)]
                if entry_row.empty:
                    del open_pos[sym]
                    continue
                r20 = float(entry_row["r20"].iloc[0])
                r60 = float(entry_row["r60"].iloc[0]) if "r60" in entry_row else None
                r120 = float(entry_row["r120"].iloc[0]) if "r120" in entry_row else None
                regime = str(entry_row["regime"].iloc[0]) if entry_row["regime"].notna().any() else "UNKNOWN"
                trades.append(
                    {
                        "date": entry_date,
                        "year": year,
                        "symbol": sym,
                        "regime": regime,
                        "r20": r20,
                        "r60": r60,
                        "r120": r120,
                        "net_r20": r20 - TRANSACTION_COST,
                    }
                )
                del open_pos[sym]
                n_exits += 1

        # ── daily return: mean over open positions (entry d+1 .. exit d) ─
        open_syms = [s for s in open_pos if open_pos[s] < idx <= open_pos[s] + hold]
        open_ret = []
        for s in open_syms:
            if date not in ret_pivot.index or s not in ret_pivot.columns:
                continue
            v = ret_pivot.at[date, s]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                open_ret.append(float(v))
        gross = float(np.mean(open_ret)) if open_ret else 0.0
        n_open_now = max(1, len(open_syms))
        cost_drag = COST_PER_SIDE * (n_entries + n_exits) / n_open_now
        daily_gross.append(gross)
        daily_cost.append(cost_drag)
        daily_n_open.append(len(open_syms))

    if not trades:
        return {"error": "no_trades"}

    # ── NAV (net of cost) + MaxDD ────────────────────────────────────────
    net_ret = np.array(daily_gross) - np.array(daily_cost)
    nav = np.cumprod(1.0 + net_ret)
    peak = np.maximum.accumulate(nav)
    maxdd = float(np.min(nav / peak - 1.0))

    t = pd.DataFrame(trades)
    gross_r20 = float(t["r20"].mean())
    net_r20 = float(t["net_r20"].mean())
    wr = float((t["net_r20"] > 0).mean())
    gains = t.loc[t["net_r20"] > 0, "net_r20"].sum()
    losses = -t.loc[t["net_r20"] < 0, "net_r20"].sum()
    pf = float(gains / losses) if losses > 0 else float("inf")

    n_open_arr = np.array(daily_n_open)
    hhi = np.mean(1.0 / np.maximum(1, n_open_arr))
    hhi_max = float(1.0 / max(1, int(n_open_arr.max())))

    # ── first-entry timing + budget exhaustion (B: 20 slot tiêu khi nào?) ──
    first_entries: dict[str, int] = {}
    last_entries: dict[str, int] = {}
    for year, g in t.groupby("year"):
        doy = pd.to_datetime(g["date"]).dt.dayofyear
        first_entries[str(year)] = int(doy.min())
        last_entries[str(year)] = int(doy.max())

    # ── regime distribution ──────────────────────────────────────────────
    regime_stats: dict[str, dict] = {}
    for reg, g in t.groupby("regime"):
        regime_stats[str(reg)] = {
            "n": int(len(g)),
            "net_r20": round(float(g["net_r20"].mean()), 4),
        }

    # ── overlap H20/H60/H120 (alpha có dai không?) ───────────────────────
    overlap = {"spearman_20_60": None, "spearman_20_120": None, "pct_same_sign": None}
    tt = t.dropna(subset=["r60", "r120"])
    if len(tt) >= 10:
        overlap["spearman_20_60"] = _spearman(tt["r20"], tt["r60"])
        overlap["spearman_20_120"] = _spearman(tt["r20"], tt["r120"])
        overlap["pct_same_sign"] = round(float((np.sign(tt["r20"]) == np.sign(tt["r120"])).mean() * 100), 1)

    per_year = {
        str(y): {
            "n": int(len(g)),
            "net_r20": round(float(g["net_r20"].mean()), 4),
        }
        for y, g in t.groupby("year")
    }

    is_t = t[t["year"].isin(IS_YEARS)]
    oos_t = t[t["year"].isin(OOS_YEARS)]

    return {
        "n_trades": len(t),
        "n_positions_year": round(len(t) / max(1, len(set(t["year"]))), 1),
        "turnover_year": round(len(t) / max(1, n_days / 249), 1),
        "gross_r20": round(gross_r20 * 100, 2),
        "net_r20": round(net_r20 * 100, 2),
        "win_rate": round(wr * 100, 1),
        "pf": round(pf, 2) if math.isfinite(pf) else None,
        "nav_total": round(float(nav[-1] - 1.0) * 100, 2),
        "maxdd": round(maxdd * 100, 2),
        "avg_concurrent": round(float(np.mean(n_open_arr)), 1),
        "hhi": round(hhi, 4),
        "hhi_max": round(hhi_max, 4),
        "first_entry_doy": first_entries,
        "last_entry_doy": last_entries,
        "regime": regime_stats,
        "overlap": overlap,
        "per_year": per_year,
        "IS_net_r20": round(float(is_t["net_r20"].mean() * 100), 2) if len(is_t) else None,
        "OOS_net_r20": round(float(oos_t["net_r20"].mean() * 100), 2) if len(oos_t) else None,
    }


def _spearman(x, y) -> float | None:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 10 or np.unique(x).size < 2 or np.unique(y).size < 2:
        return None
    from scipy.stats import spearmanr

    try:
        rho = spearmanr(x, y).statistic
    except ValueError, TypeError:
        return None
    if rho is None or math.isnan(rho):
        return None
    return round(float(rho), 3)


# ═══════════════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════════════


def _fmt(v, fmt: str = "{:.2f}") -> str:
    return fmt.format(v) if v is not None else "   -"


def print_branch(label: str, d: dict) -> None:
    if d.get("error"):
        print(f"\n### {label}\n  {d['error']}")
        return
    print(f"\n### {label}")
    print(f"  n_trades={d['n_trades']}  (~{d['n_positions_year']}/năm, turnover ~{d['turnover_year']}/năm)")
    print(
        f"  gross r20={_fmt(d['gross_r20'], '{:+.2f}')}% | net r20={_fmt(d['net_r20'], '{:+.2f}')}% "
        f"| WinRate={_fmt(d['win_rate'], '{:.0f}')}% | PF={_fmt(d['pf'], '{:.2f}')}"
    )
    print(
        f"  NAV={_fmt(d['nav_total'], '{:+.2f}')}% | MaxDD(net)={_fmt(d['maxdd'], '{:.2f}')}% "
        f"| avg_concurrent={_fmt(d['avg_concurrent'], '{:.1f}')} | HHI={_fmt(d['hhi'], '{:.4f}')}"
    )
    print(f"  first/last-entry (doy/năm): {d['first_entry_doy']} / {d['last_entry_doy']}")
    print(
        f"  IS(2022-24) net r20={_fmt(d['IS_net_r20'], '{:+.2f}')}% "
        f"| OOS(2025 desc) net r20={_fmt(d['OOS_net_r20'], '{:+.2f}')}%"
    )
    print(
        f"  overlap H20-H60 ρ={d['overlap']['spearman_20_60']} "
        f"| H20-H120 ρ={d['overlap']['spearman_20_120']} | same-sign={d['overlap']['pct_same_sign']}%"
    )
    print(f"  per-year net r20: {d['per_year']}")
    print(f"  regime entry: {d['regime']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Gate 6 — Governor integration test (A oracle vs B machinery) — READ-ONLY")
    ap.add_argument("--pool", choices=("all", "deploy"), default="all")
    ap.add_argument("--prefix", default="replay_58")
    args = ap.parse_args()

    pool = load_pool(REPLAY_DIR, prefix=args.prefix, pool=args.pool)
    print(f"[load] pool={args.pool}: rows={len(pool)} symbols={pool['symbol'].nunique()} dates={pool['date'].nunique()}")
    df = build_feature_matrix(pool)
    print(f"[features] matrix rows={len(df)} cols={len(df.columns)}")
    scored = build_scores(df)
    print(f"[score] M_value_all n={scored['M_value_all'].notna().sum():,}")

    # close pivot cho MTM/NAV
    symbols = sorted(scored["symbol"].unique())
    px = load_prices(symbols)
    close_pivot = px.pivot_table(index="date", columns="symbol", values="close")
    close_pivot = close_pivot.sort_index()

    rows = []
    results: dict[str, dict] = {}

    # ── A. Oracle-like daily top-K ─────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  A. ORACLE-LIKE DAILY TOP-K (M_value, H=20, cost=0.45% round-trip, KHÔNG budget/cap/dedup)")
    print("     → ceiling của tín hiệu khi giao dịch thật")
    print("=" * 132)
    for k in K_VALUES:
        d = simulate(scored, close_pivot, k=k, budget=None, dedup=False)
        d.update({"branch": "A_oracle", "k": k, "cap": None})
        rows.append(d)
        results[f"A_k{k}"] = d
        print_branch(f"A: Oracle top-{k}", d)

    # ── B. Actual Governor integration ─────────────────────────────────────
    print("\n" + "=" * 132)
    print("  B. ACTUAL GOVERNOR INTEGRATION (M_value qua Selection Layer machinery)")
    print("     budget=20/năm ceiling (constraint), dedup 1 symbol/năm, daily_cap=config")
    print("     cap=1 là config THẬT; cap=3/5/10 để test daily_cap có giết alpha không")
    print("=" * 132)
    for cap in CAP_VALUES:
        d = simulate(scored, close_pivot, k=None, daily_cap=cap, budget=BUDGET, dedup=True)
        d.update({"branch": "B_governor", "k": None, "cap": cap})
        rows.append(d)
        results[f"B_cap{cap}"] = d
        print_branch(f"B: Governor cap={cap} budget={BUDGET} dedup", d)

    # ── Verdict ────────────────────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  VERDICT GATE 6 — A vs B (selection alpha có sống qua machinery?)")
    print("=" * 132)
    a = results.get("A_k5")
    b = results.get("B_cap1")
    if a and b and not a.get("error") and not b.get("error"):
        print(f"  A oracle k=5 : net r20={a['net_r20']:+.2f}%  MaxDD={a['maxdd']:.2f}%  NAV={a['nav_total']:+.2f}%")
        print(
            f"  B governor cap=1 (config thật): net r20={b['net_r20']:+.2f}%  "
            f"MaxDD={b['maxdd']:.2f}%  NAV={b['nav_total']:+.2f}%"
        )
        delta = b["net_r20"] - a["net_r20"]
        print(f"  Δ B−A = {delta:+.2f}pp net r20")
        a_pos = a["net_r20"] > 0.5
        b_pos = b["net_r20"] > 0.5
        if a_pos and b_pos:
            verdict = "A & B CÙNG TỐT → Feature→Selection→Governor→Realized Alpha có bằng chứng. "
            verdict += "Chênh lệch B−A nằm trong chi phí budget/execution, không phải feature."
            print(f"  → {verdict}")
        elif a_pos and not b_pos:
            verdict = "A TỐT, B XẤU → lỗi execution/budget/state machinery "
            verdict += "(điều tra selection_layer/daily_cap/dedup, KHÔNG phải feature)."
            print(f"  → {verdict}")
        elif not a_pos and not b_pos:
            verdict = "CẢ A & B ĐỀU KHÔNG ĐẠT → nghi ngờ từ representation, quay lại Gate 3A/5."
            print(f"  → {verdict}")
        else:
            verdict = "A XẤU, B TỐT → nghịch lý: machinery cải thiện alpha?? kiểm tra budget leak/lookahead."
            print(f"  → {verdict}")
        print(
            f"  LƯU Ý machinery: B cap=1 tiêu hết 20 slot trong ~{max(b['last_entry_doy'].values())} ngày đầu năm "
            f"(first/last doy: {b['first_entry_doy']}/{b['last_entry_doy']}) — budget như ceiling "
            f"KHÔNG phải quota trải đều; đây là đặc tính cấu trúc, cần kiểm tra khi deploy thật."
        )
    print("\n  GUARDRAIL:")
    print("  - Budget = 20/năm LUÔN là constraint; K/cap sensitivity để đo độ nhạy, KHÔNG chọn K vì số đẹp.")
    print("  - p_gain loại khỏi selection. OOS 2025 chỉ descriptive — không chọn K/cost/threshold từ OOS.")
    print("  - B là replication machinery (synthesized candidates), chưa phải replay thật qua Governor DB.")

    result = pd.DataFrame(rows)
    out_dir = DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"opportunity_score_gate6_audit_{args.pool}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n  Saved: {out_path}")


if __name__ == "__main__":
    main()
