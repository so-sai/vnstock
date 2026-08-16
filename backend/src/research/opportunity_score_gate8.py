"""opportunity_score_gate8.py — READ-ONLY Temporal Slot Allocation (Cadence Pacing).

Gate 8 câu hỏi chính (nút thắt duy nhất còn lại sau Gate 6/7):
    Budget=20 (ceiling) tiêu HẾT trong ~38 ngày đầu năm (front-loading) → bỏ lỡ
    cơ hội nửa cuối năm. Năm 2023: B cap=1 = −2.92% trong khi Oracle A k=1 =
    +1.85% → lỗi nằm ở TIMING/BUDGET, không phải feature. Cadence Pacing có thể
    phân bổ slot theo thời điểm alpha thực sự xuất hiện không?

3 BOUNDARY INVARIANTS (bất biến, không vi phạm):
    1. Budget 20 = TRẦN tối đa (ceiling). Không nới, không ép dùng hết 20 slots.
    2. OOS 2025 = DESCRIPTIVE ONLY. Mọi thiết kế/so sánh pacing trên IS 2022-2024.
    3. PIT: mọi quyết định giải ngân chỉ dựa trên info tại t <= as_of_date.

Ablation matrix (tất cả giữ: cap=1, budget=20, dedup, cost=0.45%, hold=20):
    0. BASELINE (Gate 6/7)        : pacing=None — FIFO, cạn ~ngày 38.
    1. QUARTERLY CAP = 5          : max 5 slot/quý, không dồn sang quý sau.
    2. MIN SPACING = 5, 10        : tối thiểu N phiên giữa 2 EXECUTE.
    3. DYNAMIC HURDLE 1.60→1.55   : ngưỡng M_value lũy tiến theo slot còn lại.
                                   (p_gain bị CẤM trong selection từ Gate 5 →
                                   hurdle map sang M_value: p_gain 0.60/0.55 =
                                   M_value 1.60/1.55, vì M_value = 1+mean(pct_rank).)
    4. REGIME-AWARE               : RANGING ≤2 slot/tháng; CRISIS/TRENDING nhanh hơn.

Chỉ số nghiệm thu:
    - Năm 2023: từ −2.92% tiến gần Oracle +1.85%.
    - Temporal dispersion: slot/Q1..Q4 + t_first→t_last (doy).
    - Toàn cục: Net R20 (IS/OOS), WinRate, Sharpe, MaxDD, NAV.
    - Luôn đọc policy có dùng ĐỦ slot không (ceiling ≠ quota).

READ-ONLY: không ghi DB, không sửa selection_layer/decision_budget/company_state.

Usage (từ project root):
  python -X utf8 backend/src/research/opportunity_score_gate8.py
"""

from __future__ import annotations

import sys
from pathlib import Path

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
from research.opportunity_score_gate6 import BUDGET, DAILY_CAP_ACTUAL, HOLD, simulate
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

COST = 0.0045  # round-trip baseline (Gate 6/7)
ORACLE_2023 = 1.85  # % — A k=1 2023 (feature alpha, không budget)
BASELINE_2023 = -2.92  # % — B cap=1 2023 (front-loading artifact)

# ── Ablation matrix (pacing dict → chuyển thẳng vào simulate) ──────────────
POLICIES: list[tuple[str, dict | None]] = [
    ("0_baseline", None),
    ("1_quarter_cap_5", {"quarter_cap": 5}),
    ("2_spacing_5", {"min_spacing": 5}),
    ("2b_spacing_10", {"min_spacing": 10}),
    ("3_hurdle_1.60_1.55", {"hurdle": (1.60, 1.55)}),
    ("4_regime_ranging2", {"regime_month_cap": {"RANGING": 2, "CRISIS": 3, "TRENDING": 3}}),
]

# ── Hurdle gốc từ spec là p_gain (CẤM) → map sang M_value ──────────────────
#   M_value = 1 + mean(pct_rank) → p_gain 0.60 ≈ M_value 1.60; 0.55 ≈ 1.55.
HURDLE_MAPPING_NOTE = (
    "p_gain bị CẤM trong selection (Gate 5). Dynamic Hurdle dùng M_value "
    "thay p_gain: 0.60→1.60, 0.55→1.55 (M_value = 1 + mean(pct_rank), range [1,2])."
)


def _fmt(v, fmt: str = "{:+.2f}") -> str:
    return fmt.format(v) if v is not None else "    -"


def _pol(res: dict) -> dict:
    """Rút 1 dòng tóm tắt policy từ kết quả simulate."""
    return {
        "net_r20": res.get("net_r20"),
        "IS_net_r20": res.get("IS_net_r20"),
        "OOS_net_r20": res.get("OOS_net_r20"),
        "win_rate": res.get("win_rate"),
        "pf": res.get("pf"),
        "sharpe_IS": res.get("IS_sharpe"),
        "nav": res.get("nav_total"),
        "maxdd": res.get("maxdd"),
        "n_trades": res.get("n_trades"),
        "n_used": res.get("n_trades"),  # ceiling=20, n_used có thể < 20
        "r20_2022": res["per_year"].get("2022", {}).get("net_r20"),
        "r20_2023": res["per_year"].get("2023", {}).get("net_r20"),
        "r20_2024": res["per_year"].get("2024", {}).get("net_r20"),
        "r20_2025": res["per_year"].get("2025", {}).get("net_r20"),
        "win_2023": res["per_year"].get("2023", {}).get("win_rate"),
        "first_doy": res.get("first_entry_doy"),
        "last_doy": res.get("last_entry_doy"),
        "q_disp": res.get("quarter_disp"),
    }


def _print_table(rows: list[dict], label: str) -> None:
    print(f"\n{label}")
    print("-" * 132)
    print(
        f"{'policy':<22}{'netR20%':>8}{'IS%':>7}{'OOS%':>7}{'win%':>6}{'PF':>6}"
        f"{'IS_sh':>6}{'NAV%':>9}{'MaxDD%':>8}{'n':>4}   "
        f"{'22':>6}{'23':>7}{'24':>7}{'25(OOS)':>9}"
    )
    for r in rows:
        print(
            f"{r['name']:<22}{_fmt(r['net_r20']):>8}{_fmt(r['IS_net_r20']):>7}{_fmt(r['OOS_net_r20']):>7}"
            f"{r['win_rate']:>6.1f}{_fmt(r['pf'], '{:.2f}'):>6}"
            f"{_fmt(r['sharpe_IS'], '{:.2f}'):>6}{_fmt(r['nav']):>9}{_fmt(r['maxdd']):>8}{r['n_trades']:>4}   "
            f"{_fmt(r['r20_2022']):>6}{_fmt(r['r20_2023']):>7}{_fmt(r['r20_2024']):>7}{_fmt(r['r20_2025']):>9}"
        )


def _print_dispersion(rows: list[dict]) -> None:
    print("\nTemporal dispersion (slot per quarter, first→last doy):")
    for r in rows:
        fd = r["first_doy"]
        ld = r["last_doy"]
        disp = "  ".join(
            f"{q}={''.join('#' * (r['q_disp'].get(str(y), {}).get(q, 0)) or '.')}"
            for y in YEARS
            for q in ("Q1", "Q2", "Q3", "Q4")
        )
        print(f"{r['name']:<22} first_doy={fd}  last_doy={ld}")
        print(f"{'':<22} {disp}")


def _verdict(rows: list[dict]) -> None:
    print("\n" + "=" * 118)
    print("VERDICT GATE 8 — Temporal Slot Allocation (Cadence Pacing)")
    print("=" * 118)
    print(f"  Oracle 2023 (feature alpha, k=1, không budget) = +{ORACLE_2023}%")
    print(f"  Baseline B 2023 (front-loading)                = {BASELINE_2023}%")

    base = rows[0]
    print(f"\n  So với baseline ({base['name']}) — SO SÁNH TRÊN IS 2022-2024:")
    improved = []
    for r in rows[1:]:
        d23 = r["r20_2023"] - base["r20_2023"]
        dis = r["IS_net_r20"] - base["IS_net_r20"]
        status = "cải thiện" if dis > 0 else "xấu hơn"
        print(
            f"    {r['name']:<22} IS Δ={dis:+.2f}pp ({status}), 2023 Δ={d23:+.2f}pp, "
            f"MaxDD={_fmt(r['maxdd']):>8}%, NAV={_fmt(r['nav'])}%, n={r['n_trades']}"
        )
        improved.append((r["name"], d23, r["r20_2023"], dis))

    best = max(improved, key=lambda x: x[3]) if improved else None
    print("\n  Pacing cải thiện IS nhiều nhất:", best[0] if best else "n/a")
    if best:
        print(f"    2023: {best[2]:+.2f}% (Oracle {ORACLE_2023}%, baseline {BASELINE_2023}%)")

    print("\n  Đọc kết quả (THẬN TRỌNG):")
    print("  - IS 2022-2024 là tập SO SÁNH/CHỌN pacing (bất biến #2). OOS 2025 chỉ descriptive.")
    print("  - Budget 20 là TRẦN: nếu pacing dùng <20 slot mà net tốt hơn → đúng thiết kế")
    print("    (không ép dùng hết, không nới). Cột n cho biết slot thực dùng.")
    print("  - Nếu pacing KHÔNG phá được năm âm 2023 → front-loading không phải nguyên nhân")
    print("    chính; cần soi tiếp (chính policy timing khác, không đổ lỗi feature).")
    print(f"  - {HURDLE_MAPPING_NOTE}")


def main() -> None:
    print("Gate 8 — Temporal Slot Allocation (Cadence Pacing) | READ-ONLY")
    print("=" * 118)
    print("Load data...")
    scored = build_scores(build_feature_matrix(load_pool(REPLAY_DIR)))
    symbols = sorted(scored["symbol"].unique())
    px = load_prices(symbols)
    close_pivot = px.pivot_table(index="date", columns="symbol", values="close").sort_index()

    rows = []
    for name, pacing in POLICIES:
        res = simulate(
            scored,
            close_pivot,
            daily_cap=DAILY_CAP_ACTUAL,
            budget=BUDGET,
            dedup=True,
            hold=HOLD,
            cost=COST,
            pacing=pacing,
        )
        if res.get("error"):
            print(f"[{name}] ERROR {res['error']}")
            continue
        r = _pol(res)
        r["name"] = name
        rows.append(r)
        print(f"  [{name}] done (n={r['n_trades']}, net_r20={r['net_r20']}%)")

    _print_table(rows, "So sánh toàn cục (net r20 trung bình 4 năm) + per-year")
    _print_dispersion(rows)
    _verdict(rows)


if __name__ == "__main__":
    main()
