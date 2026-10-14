"""run_oos_2026.py — KIỂM ĐỊNH OUT-OF-SAMPLE 2026 cho Spec V2 OOS v1.0.

Tài liệu nguồn: `docs/SPEC_TREND_ENGINE_V2_MOMENTUM_REGIME_OOS.md`.
Cửa sổ: 2026-01-01 → 2026-10-09. **ĐỌC MỘT LẦN** (spec §0) — không chạy lại sau khi thấy số.

HAI ỨNG VIÊN (spec §1)
    C1 = V5_distressed_only : Momentum + Regime + Health != DISTRESSED
    C2 = V2_momentum_regime : Momentum + Regime (bỏ health)

LUẬT UNIVERSE MỚI (spec §3 — khai báo trước, khác duy nhất so với spec cũ)
    Universe tại T = mã có >= 200 phiên SẠCH trong 250 phiên gần nhất tính đến T.
    Lý do: cửa sổ 2026 chỉ ~195 phiên nên luật cũ ("200 phiên trong NĂM dương lịch") cho
    universe RỖNG. Luật mới còn PIT đúng hơn: luật cũ để tư cách giao dịch của tháng 1 bị
    quyết bởi dữ liệu tới tháng 12.

Bất biến: mọi ngưỡng đọc từ module đã niêm phong; file này KHÔNG chứa ngưỡng giao dịch nào
(ngoài 200/250 là luật universe do spec §3 quy định và đã hash).
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.backtest import backtest_trend_2021_2025 as bt  # noqa: E402
from src.backtest import trend_loop as tl  # noqa: E402
from src.backtest.ablation_arms import ARMS  # noqa: E402
from src.backtest.preregistration import (  # noqa: E402
    sha256_lf_normalized,
    verify_preregistration_lock,
)

OOS_START = "2026-01-01"
OOS_END = "2026-10-09"
OUT_DIR = PROJECT_ROOT / "backend" / "reports" / "backtest" / "oos_2026"
OOS_SPEC = PROJECT_ROOT / "docs" / "SPEC_TREND_ENGINE_V2_MOMENTUM_REGIME_OOS.md"

# Luật universe của spec §3 (đã hash trong spec, không phải ngưỡng giao dịch).
UNIVERSE_MIN_CLEAN_SESSIONS = 200
UNIVERSE_LOOKBACK_SESSIONS = 250

CANDIDATES = {
    "C1_momentum_regime_distressed_only": "V5_distressed_only",
    "C2_momentum_regime": "V2_momentum_regime",
}

# Tiêu chí đạt — spec §5.
MIN_SHARPE = 0.50
MAX_MAXDD_PCT = 12.0
MIN_TRADES = 25


class TrailingUniverse:
    """Universe cửa sổ trượt: >=200 phiên sạch trong 250 phiên gần nhất (spec §3)."""

    def __init__(self, ma) -> None:
        clean = ma.close.notna()  # panel đã lọc is_stale=0 + VALID_ROW_SQL ở prepare()
        counts = clean.rolling(UNIVERSE_LOOKBACK_SESSIONS, min_periods=1).sum()
        self._ok = counts >= UNIVERSE_MIN_CLEAN_SESSIONS
        self._index = {d: i for i, d in enumerate(ma.days)}

    def at(self, date: str) -> set[str]:
        i = self._index.get(date)
        if i is None:
            return set()
        row = self._ok.iloc[i]
        return {s for s, v in row.items() if bool(v)}


def run_candidate(candidate: str, scenario: str) -> dict:
    arm_name = CANDIDATES[candidate]
    portfolio = bt.Portfolio(cost=bt.COST_SCENARIOS[scenario])
    stats = tl.simulate(
        portfolio,
        start=OOS_START,
        end=OOS_END,
        min_sessions=UNIVERSE_MIN_CLEAN_SESSIONS,
        arm=ARMS[arm_name],
        universe_factory=TrailingUniverse,
    )
    navs = [v for _, v in portfolio.nav_history]
    return {
        "arm": candidate,
        "arm_config": arm_name,
        "cost_scenario": scenario,
        "sessions": stats["days"],
        "entries": stats["entries"],
        "trades": len(portfolio.trades),
        "full_exits": stats["full_exits"],
        "partial_exits": stats["partial"],
        "emergency_exits": stats["emergency"],
        "candidates": stats["candidates"],
        "candidates_archetype_none": stats.get("candidates_archetype_none", 0),
        "candidates_distressed_rejected": stats.get("candidates_distressed_rejected", 0),
        "pct_days_flat": round((1 - stats["days_with_positions"] / (stats["days"] or 1)) * 100, 4),
        "sharpe": bt.sharpe(navs),
        "max_drawdown_pct": bt.max_drawdown(navs),
        "calmar": bt.calmar(navs, len(navs)),
        "cagr_pct": bt.cagr(navs, len(navs)),
        "win_rate_pct": bt.win_rate(portfolio.trades),
        "profit_factor": bt.profit_factor(portfolio.trades),
        "total_return_pct": (navs[-1] / navs[0] - 1.0) * 100 if len(navs) > 1 else 0.0,
        "passes": (
            bt.sharpe(navs) >= MIN_SHARPE
            and bt.max_drawdown(navs) <= MAX_MAXDD_PCT
            and stats["entries"] >= MIN_TRADES
        ),
    }


def main() -> int:
    info = verify_preregistration_lock()
    print(f"[niêm phong] ok={info['ok']} · {info['spec']}")
    if not info["ok"]:
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = [run_candidate(a, s) for a in CANDIDATES for s in ("primary", "robustness")]
    for r in results:
        print(
            f"  {r['arm']:<36} {r['cost_scenario']:<10} entries={r['entries']:>3} "
            f"sharpe={r['sharpe']:+.4f} maxdd={r['max_drawdown_pct']:.2f}% "
            f"win={r['win_rate_pct']:.1f}% PF={r['profit_factor']:.3f} "
            f"flat={r['pct_days_flat']:.1f}% {'ĐẠT' if r['passes'] else 'không đạt'}",
            flush=True,
        )

    prim = {r["arm"]: r for r in results if r["cost_scenario"] == "primary"}
    c1, c2 = prim["C1_momentum_regime_distressed_only"], prim["C2_momentum_regime"]
    # Cả hai kịch bản chi phí đều phải đạt (spec §5).
    ok = {
        "C1": all(r["passes"] for r in results if r["arm"].startswith("C1")),
        "C2": all(r["passes"] for r in results if r["arm"].startswith("C2")),
    }
    if ok["C1"] and ok["C2"]:
        branch = "1 — cổng health ĐÁNG GIỮ ⇒ paper-trade C1 ở mức paper 1 tháng (không tiền thật)"
    elif ok["C1"] and not ok["C2"]:
        branch = "2 — health là yếu tố quyết định ⇒ paper-trade C1"
    elif ok["C2"] and not ok["C1"]:
        branch = "3 — cổng health LÀ VẬT CẢN ⇒ bỏ hẳn health; paper-trade C2"
    else:
        branch = "4 — Framework KHÔNG vượt OOS ⇒ không promote, dừng nhánh, viết báo cáo FAIL"
    print(f"\nNHÁNH {branch}")
    print(f"C1: sharpe={c1['sharpe']:+.4f} maxdd={c1['max_drawdown_pct']:.2f}% entries={c1['entries']}")
    print(f"C2: sharpe={c2['sharpe']:+.4f} maxdd={c2['max_drawdown_pct']:.2f}% entries={c2['entries']}")

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "spec": "docs/SPEC_TREND_ENGINE_V2_MOMENTUM_REGIME_OOS.md",
        "spec_sha256": sha256_lf_normalized(OOS_SPEC) if OOS_SPEC.exists() else None,
        "window": [OOS_START, OOS_END],
        "criteria": {"min_sharpe": MIN_SHARPE, "max_maxdd_pct": MAX_MAXDD_PCT, "min_trades": MIN_TRADES},
        "universe_rule": (
            f">= {UNIVERSE_MIN_CLEAN_SESSIONS} phiên sạch trong "
            f"{UNIVERSE_LOOKBACK_SESSIONS} phiên gần nhất (spec §3)"
        ),
        "branch": branch,
        "candidates_pass": ok,
        "results": results,
    }
    with (OUT_DIR / "oos_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    with (OUT_DIR / "oos_arms.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"đã ghi → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
