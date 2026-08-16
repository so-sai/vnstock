"""opportunity_score_gate7.py — READ-ONLY Walk-Forward / OOS Policy Validation.

Gate 7 trả lời: alpha +1.31% (Gate 6, B cap=1) có PHÒNG THỦ được ngoài mẫu không?

3 trụ cột:
  1. Year-by-year IS vs OOS — Net R20 có dương TỪNG NĂM không? Retention Sharpe
     (OOS/IS) có ≥ 70% không?
  2. Transaction cost sensitivity — quét cost 0.15/0.45/0.75/1.00%, tìm
     break-even friction (mức phí làm triệt tiêu alpha).
  3. Regime stability — CRISIS/RANGING/TRENDING: WinRate & Net R20 mỗi regime;
     RANGING (chiếm đa số thời gian) có giữ WinRate > 50% và Net R20 dương?

Ràng buộc dữ liệu (đã kiểm chứng replay_58_ts):
  - Replay DB chỉ tồn tại 2022-2025 (KHÔNG có 2026). OOS = 2025 DUY NHẤT.
  - Giá daily_ohlcv chạy tới 2026-08-14 → forward return H20 của entry 2025
    resolve đủ (không cần 2026 replay).
  - M_value = parameter-free (equal-rank, không fit weight) → "walk-forward" ở đây
    là kiểm định ĐỘ ỔN ĐỊNH TỪNG NĂM của policy cố định, KHÔNG phải re-fit.
  - Policy dưới test = Gate 6 Branch B cap=1 (config thật), budget=20 (ceiling).

READ-ONLY: không ghi DB, không sửa selection_layer/decision_budget/company_state.

Usage (từ project root):
  python -X utf8 backend/src/research/opportunity_score_gate7.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
from research.opportunity_score_gate6 import BUDGET, HOLD, simulate
from research.opportunity_score_research import (
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

COST_BASELINE = 0.0045
COST_SWEEP = (0.0015, 0.0045, 0.0075, 0.0100)  # 0.15% / 0.45% / 0.75% / 1.00%
RETENTION_TARGET = 70.0  # % — OOS/IS Sharpe


def _fmt(v, fmt: str = "{:.2f}") -> str:
    return fmt.format(v) if v is not None else "   -"


def print_year_table(d: dict) -> None:
    rows = d["per_year"]
    print(f"{'year':<6}{'n':>5}{'net r20%':>10}{'win%':>7}{'sharpe':>8}")
    print("-" * 40)
    for y in YEARS:
        g = rows.get(y)
        if not g:
            print(f"{y:<6}   -")
            continue
        print(f"{y:<6}{g['n']:>5}{g['net_r20'] * 100:>+10.2f}{g['win_rate']:>7.1f}{g['sharpe']:>8.2f}")
    print(
        f"IS(22-24) sharpe={_fmt(d['IS_sharpe'], '{:.2f}')}  OOS(25) sharpe={_fmt(d['OOS_sharpe'], '{:.2f}')}  "
        f"retention={_fmt(d['retention_pct'], '{:.1f}')}%"
    )


def print_regime_table(d: dict) -> None:
    print(f"{'regime':<16}{'n':>5}{'net r20%':>10}{'win%':>7}")
    print("-" * 40)
    for reg in ("CRISIS", "RANGING", "TRENDING", "CRISIS_WARNING", "UNKNOWN"):
        g = d["regime"].get(reg)
        if not g:
            continue
        print(f"{reg:<16}{g['n']:>5}{g['net_r20'] * 100:>+10.2f}{g['win_rate']:>7.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Gate 7 — Walk-Forward / OOS policy validation — READ-ONLY")
    ap.add_argument("--pool", choices=("all", "deploy"), default="all")
    ap.add_argument("--prefix", default="replay_58")
    args = ap.parse_args()

    pool = load_pool(REPLAY_DIR, prefix=args.prefix, pool=args.pool)
    print(f"[load] pool={args.pool}: rows={len(pool)} symbols={pool['symbol'].nunique()} dates={pool['date'].nunique()}")
    df = build_feature_matrix(pool)
    print(f"[features] matrix rows={len(df)} cols={len(df.columns)}")
    scored = build_scores(df)
    print(f"[score] M_value_all n={scored['M_value_all'].notna().sum():,}")

    symbols = sorted(scored["symbol"].unique())
    px = load_prices(symbols)
    close_pivot = px.pivot_table(index="date", columns="symbol", values="close").sort_index()

    rows = []
    policy = dict(daily_cap=1, budget=BUDGET, dedup=True, hold=HOLD)

    # ═══════════════════════════════════════════════════════════════════════
    # 1. YEAR-BY-YEAR / IS vs OOS
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 132)
    print("  1) YEAR-BY-YEAR / IS vs OOS — policy cố định (M_value, cap=1, budget=20)")
    print("     Net R20 có dương TỪNG NĂM? Retention Sharpe OOS/IS >= 70%?")
    print("     (IS=2022-24, OOS=2025 DESCRIPTIVE — replay 2026 không tồn tại)")
    print("=" * 132)
    base = simulate(scored, close_pivot, cost=COST_BASELINE, **policy)
    base.update({"pillar": "1_year", "cost": COST_BASELINE})
    rows.append(base)
    if base.get("error"):
        print("  ", base["error"])
    else:
        print_year_table(base)
        print(
            f"\n  IS(22-24) net r20={_fmt(base['IS_net_r20'], '{:+.2f}')}% | "
            f"OOS(25) net r20={_fmt(base['OOS_net_r20'], '{:+.2f}')}%"
        )
        ret = base["retention_pct"]
        if ret is None:
            print("  retention: KHÔNG tính được (IS sharpe <= 0 hoặc thiếu mẫu)")
        else:
            met = ret >= RETENTION_TARGET
            print(f"  retention = {ret:.1f}% (target {RETENTION_TARGET:.0f}%) → {'ĐẠT' if met else 'KHÔNG ĐẠT'}")
            if base["IS_sharpe"] is not None and abs(base["IS_sharpe"]) < 0.15:
                print(
                    "  ⚠ IS sharpe rất thấp → retention dễ 'đạt' một cách tầm thường; "
                    "đọc theo per-year net r20, không chỉ retention."
                )

    # ═══════════════════════════════════════════════════════════════════════
    # 2. TRANSACTION COST SENSITIVITY
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 132)
    print("  2) TRANSACTION COST SENSITIVITY — quét 0.15% → 1.00% round-trip")
    print("     Break-even friction = cost làm Net R20 trung bình = 0")
    print("=" * 132)
    print(f"{'cost%':>7}{'net r20%':>10}{'win%':>7}{'pf':>6}{'NAV%':>10}{'MaxDD%':>8}")
    print("-" * 60)
    cost_rows = []
    for c in COST_SWEEP:
        d = simulate(scored, close_pivot, cost=c, **policy)
        d.update({"pillar": "2_cost", "cost": c})
        rows.append(d)
        cost_rows.append(d)
        if d.get("error"):
            print(f"{c * 100:>6.2f}%  {d['error']}")
            continue
        print(
            f"{c * 100:>6.2f}%{d['net_r20']:>+10.2f}{d['win_rate']:>7.1f}"
            f"{_fmt(d['pf'], '{:.2f}') if d['pf'] is not None else '   -':>6}"
            f"{d['nav_total']:>+10.2f}{d['maxdd']:>+8.2f}"
        )
    ok = [d for d in cost_rows if not d.get("error") and len(d["per_year"]) >= 4]
    if ok:
        # Break-even = gross_r20 (vì net_r20 ≈ gross − cost mỗi trade round-trip).
        break_even = ok[0]["gross_r20"] / 100.0 if ok[0].get("gross_r20") is not None else None
        be_pct = break_even * 100 if break_even is not None else None
        be_txt = f"≈ {be_pct:.2f}%" if be_pct is not None else "n/a"
        print(
            f"\n  Break-even friction {be_txt} round-trip "
            f"(net_r20 = gross − cost → break-even = gross r20 = {ok[0]['gross_r20']:+.2f}%)"
        )
        print(
            f"  Tại cost 1.00%: net r20={ok[-1]['net_r20']:+.2f}% win={ok[-1]['win_rate']:.1f}% → "
            f"{'CÒN DƯƠNG (alpha chịu được stress friction)' if ok[-1]['net_r20'] > 0 else 'BỊ TRIỆT TIÊU'}"
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 3. REGIME STABILITY
    # ═══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 132)
    print("  3) REGIME STABILITY — phân rã theo trạng thái thị trường")
    print("     RANGING (đa số thời gian): WinRate > 50% và Net R20 dương?")
    print("=" * 132)
    print_regime_table(base)
    rng = base["regime"].get("RANGING")
    if rng:
        ok_rng = rng["win_rate"] > 50.0 and rng["net_r20"] > 0
        print(
            f"\n  RANGING: n={rng['n']} net r20={rng['net_r20'] * 100:+.2f}% win={rng['win_rate']:.1f}% "
            f"→ {'PASS' if ok_rng else 'KHÔNG PASS'}"
        )
    trd = base["regime"].get("TRENDING")
    if trd:
        print(
            f"  TRENDING: n={trd['n']} net r20={trd['net_r20'] * 100:+.2f}% win={trd['win_rate']:.1f}% "
            f"(yếu nhất kỳ vọng — value chạy chậm)"
        )

    # ── Verdict ────────────────────────────────────────────────────────────
    print("\n" + "=" * 132)
    print("  VERDICT GATE 7 — Walk-Forward / OOS policy validation")
    print("=" * 132)
    if base.get("error"):
        print("  base run lỗi.")
        return
    neg_years = [y for y in YEARS if base["per_year"].get(y) and base["per_year"][y]["net_r20"] < 0]
    pos_oos = (base["OOS_net_r20"] or 0) > 0
    ret_ok = base["retention_pct"] is not None and base["retention_pct"] >= RETENTION_TARGET
    wr_ok = base["win_rate"] > 50
    stress_ok = ok[-1]["net_r20"] > 0 if ok else False
    print(f"  - Net r20 dương TỪNG NĂM: {'CÓ' if not neg_years else 'KHÔNG — năm âm: ' + ', '.join(neg_years)}")
    print(f"  - OOS 2025 (desc): net r20={_fmt(base['OOS_net_r20'], '{:+.2f}')}% → {'dương' if pos_oos else 'âm'}")
    print(f"  - Retention Sharpe OOS/IS ≥ {RETENTION_TARGET:.0f}%: {'ĐẠT' if ret_ok else 'KHÔNG diễn giải được'}")
    print(f"  - WinRate baseline: {base['win_rate']:.1f}% → {'>50%' if wr_ok else '<=50%'}")
    print(
        f"  - Stress friction 1.00%: net r20={_fmt(ok[-1]['net_r20'], '{:+.2f}') if ok else 'n/a'}% → "
        f"{'CÒN SỐNG' if stress_ok else 'CHẾT'}"
    )

    # ── 2023 attribution: feature hay machinery timing? ───────────────────
    print("\n  Attribution năm âm (2023 cap=1):")
    oracle = simulate(scored, close_pivot, k=1, budget=None, dedup=False, cost=COST_BASELINE)
    if not oracle.get("error") and oracle["per_year"].get("2023"):
        o23 = oracle["per_year"]["2023"]
        b23 = base["per_year"]["2023"]
        print(
            f"  - M_value feature alpha (A oracle k=1) 2023: net r20={o23['net_r20'] * 100:+.2f}% "
            f"→ {'DƯƠNG' if o23['net_r20'] > 0 else 'ÂM'}"
        )
        print(
            f"  - B machinery (cap=1 budget=20) 2023: net r20={b23['net_r20'] * 100:+.2f}% "
            f"→ {'ÂM' if b23['net_r20'] < 0 else 'DƯƠNG'}"
        )
        print(
            "  → Nếu oracle dương mà machinery âm: năm âm do TIMING/BUDGET (20 slot tiêu ~ngày 38), "
            "KHÔNG phải feature fail. Đây là bài toán Temporal Slot Allocation (chưa làm ở gate này)."
        )

    print("\n  Đọc kết quả (THẬN TRỌNG):")
    print("  - M_value parameter-free → 'walk-forward' = stability của policy CỐ ĐỊNH, không phải re-fit.")
    print("  - 2025 là OOS DESCRIPTIVE (đã nhiễm theo AGENTS.md) — không dùng để chọn tham số.")
    print(
        "  - Nếu có năm âm (vd 2023 cap=1): pooled +1.31% KHÔNG phải per-year; cần đánh giá "
        "policy level (budget/timing) trước khi production, KHÔNG đổ lỗi feature."
    )
    print("  - PIT guard: chỉ period< & date<= — không nhìn tương lai ở bất kỳ bước nào.")

    result = pd.DataFrame(rows)
    out_dir = DATA_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"opportunity_score_gate7_audit_{args.pool}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\n  Saved: {out_path}")


if __name__ == "__main__":
    main()
