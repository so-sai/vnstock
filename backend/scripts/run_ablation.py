"""run_ablation.py — chạy Ablation Study (docs/SPEC_TREND_ENGINE_ABLATION_STUDY.md v1.0).

DIAGNOSTIC ONLY — không ô nào được promote (§6 R4).

TRÌNH TỰ BẮT BUỘC
-----------------
  1. Xác minh niêm phong (cả hai spec phải khớp anchor).
  2. Chạy 4 ô × 2 kịch bản chi phí.
  3. **Kiểm R1**: V4 phải trùng baseline niêm phong tới 12 chữ số có nghĩa. Lệch ⇒ study
     VÔ HIỆU, in rõ, và KHÔNG in bảng diễn giải (để không ai đọc số của một harness sai).
  4. Tính tác nhân chính (R3) và in cảnh báo "KHÔNG ĐỦ LỰC" cho ô < 30 lệnh (R2).

Ghi vào `backend/reports/backtest/ablation/` — thư mục RIÊNG, không ghi đè kết quả niêm phong.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT / "backend", PROJECT_ROOT / "backend" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.backtest import backtest_trend_2021_2025 as bt  # noqa: E402
from src.backtest import trend_loop as tl  # noqa: E402
from src.backtest.ablation_arms import ARMS, HARNESS_CONTROL_ARM  # noqa: E402
from src.backtest.preregistration import (  # noqa: E402
    sha256_lf_normalized,
    verify_preregistration_lock,
)

OUT_DIR = PROJECT_ROOT / "backend" / "reports" / "backtest" / "ablation"
ABLATION_SPEC = PROJECT_ROOT / "docs" / "SPEC_TREND_ENGINE_ABLATION_STUDY.md"
BASELINE_JSON = PROJECT_ROOT / "backend" / "reports" / "backtest" / "backtest_summary_primary.json"
MIN_TRADES_FOR_CONCLUSION = 30  # §6 R2 — đồng bộ luật 30 mẫu độc lập của cổng hit-rate
SIG_DIGITS = 12  # §6 R1


def _sig(x: float | None) -> str:
    return "None" if x is None else f"{float(x):.{SIG_DIGITS}g}"


def run_arm(name: str, scenario: str) -> dict:
    portfolio = bt.Portfolio(cost=bt.COST_SCENARIOS[scenario])
    stats = tl.simulate(
        portfolio,
        start=bt.WINDOW_START,
        end=bt.WINDOW_END,
        min_sessions=bt.MIN_SESSIONS_PER_YEAR,
        arm=ARMS[name],
    )
    navs = [v for _, v in portfolio.nav_history]
    days = stats["days"] or 1
    trades_total = stats["entries"] + stats["partial"]  # mỗi phần bán là một dòng trade
    return {
        "arm": name,
        "switches": ARMS[name].describe(),
        "cost_scenario": scenario,
        "sessions": stats["days"],
        "entries": stats["entries"],
        "trades": len(portfolio.trades),
        "full_exits": stats["full_exits"],
        "partial_exits": stats["partial"],
        "emergency_exits": stats["emergency"],
        "candidates": stats["candidates"],
        "candidates_archetype_none": stats.get("candidates_archetype_none", 0),
        "days_with_positions": stats["days_with_positions"],
        "time_in_market_pct": round(stats["days_with_positions"] / days * 100, 4),
        "avg_positions": round(stats["sum_positions"] / days, 4),
        "max_positions_held": stats["max_positions_held"],
        "pct_days_flat": round((1 - stats["days_with_positions"] / days) * 100, 4),
        # ĐƠN VỊ: sharpe/calmar/profit_factor là TỈ LỆ; max_drawdown/cagr/win_rate ĐÃ là %.
        # Nhân thêm 100 ở đây từng làm MaxDD hiện "323%" và khiến R1 đỏ OAN (2026-10-09) —
        # suýt nữa thì đổ lỗi cho harness trong khi lỗi nằm ở chính script chấm điểm này.
        "sharpe": bt.sharpe(navs),
        "max_drawdown_pct": bt.max_drawdown(navs),
        "calmar": bt.calmar(navs, len(navs)),
        "cagr_pct": bt.cagr(navs, len(navs)),
        "win_rate_pct": bt.win_rate(portfolio.trades),
        "profit_factor": bt.profit_factor(portfolio.trades),
        "total_return_pct": (navs[-1] / navs[0] - 1.0) * 100 if len(navs) > 1 else 0.0,
        "insufficient_sample": stats["entries"] < MIN_TRADES_FOR_CONCLUSION,
    }


def main() -> int:
    info = verify_preregistration_lock()
    print(f"[niêm phong] ok={info['ok']} · spec={info['spec']}")
    if not info["ok"]:
        print("DỪNG: niêm phong không khớp.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for name in ARMS:
        for scenario in ("primary", "robustness"):
            r = run_arm(name, scenario)
            results.append(r)
            flag = " ⚠KHÔNG ĐỦ LỰC" if r["insufficient_sample"] else ""
            print(
                f"  {name:<20} {scenario:<11} entries={r['entries']:>3} "
                f"sharpe={_sig(r['sharpe'])} maxdd={r['max_drawdown_pct']:.2f}% "
                f"flat={r['pct_days_flat']:.1f}%{flag}",
                flush=True,
            )

    # ── §6 R1: toàn vẹn harness ──
    r1 = {"checked": False, "ok": None, "diffs": []}
    if BASELINE_JSON.exists():
        base = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
        v4 = next(r for r in results if r["arm"] == HARNESS_CONTROL_ARM and r["cost_scenario"] == "primary")
        pairs = [
            ("sharpe", v4["sharpe"], base.get("sharpe")),
            ("calmar", v4["calmar"], base.get("calmar")),
            ("max_drawdown_pct", v4["max_drawdown_pct"], base.get("max_drawdown_pct")),
            # `loop_stats` KHÔNG có trong file baseline (export_results ghi file TRƯỚC khi
            # run_backtest gắn loop_stats vào dict trả về) ⇒ đối chiếu bằng số trade đã ghi.
            ("trades", v4["trades"], base.get("total_trades")),
        ]
        for field, got, exp in pairs:
            if exp is None:
                r1["diffs"].append(f"{field}: baseline thiếu khoá")
                continue
            same = _sig(got) == _sig(exp) if isinstance(exp, float) else got == exp
            if not same:
                r1["diffs"].append(f"{field}: V4={_sig(got)} vs baseline={_sig(exp)}")
        r1["checked"] = True
        r1["ok"] = not r1["diffs"]
    print(f"\n[R1] toàn vẹn harness (V4 == baseline tới {SIG_DIGITS} chữ số): "
          f"{'ĐẠT ✅' if r1['ok'] else 'LỆCH ❌'}")
    for d in r1["diffs"]:
        print("     ", d)

    # ── §6 R3: tác nhân chính (chỉ khi R1 đạt) ──
    attribution = None
    if r1["ok"]:
        prim = {r["arm"]: r["sharpe"] for r in results if r["cost_scenario"] == "primary"}
        v1, v2, v3, v4 = (prim[k] for k in ARMS)
        attribution = {
            "organ_main_effect": (v3 + v4) / 2 - (v1 + v2) / 2,
            "regime_main_effect": (v2 + v4) / 2 - (v1 + v3) / 2,
            "interaction": (v4 - v3) - (v2 - v1),
            "note": "IN-SAMPLE (cửa sổ 2021-2025 đã dùng cho baseline) — chỉ để gán tác nhân",
        }
        print("\n[R3] tác nhân chính (Sharpe, primary):")
        print(f"     organ  : {attribution['organ_main_effect']:+.4f}")
        print(f"     regime : {attribution['regime_main_effect']:+.4f}")
        print(f"     tương tác: {attribution['interaction']:+.4f}")
    else:
        print("\n[R3] BỎ QUA — harness chưa đạt R1, diễn giải số bây giờ là vô nghĩa.")

    # ── §6 R7: luật phán quyết cho V5 (chốt trước khi chạy, spec v1.1) ──
    r7 = None
    if r1["ok"]:
        prim_ = {r["arm"]: r for r in results if r["cost_scenario"] == "primary"}
        v2 = float(prim_["V2_momentum_regime"]["sharpe"])
        v5 = float(prim_["V5_distressed_only"]["sharpe"])
        delta = v5 - v2
        if delta < -0.05:
            key, verdict = "B", "chặn DISTRESSED bóp nghẹt cơ hội ⇒ BỎ SẠCH organ"
        elif delta > 0.05 and v5 >= 0.50:
            key, verdict = "C", "loại doanh nghiệp kiệt quệ TẠO ALPHA ⇒ tiền đề cho spec kế tiếp"
        elif abs(delta) <= 0.05:
            key, verdict = "A", "chặn DISTRESSED không mất cơ hội ⇒ giữ làm bảo hiểm phá sản"
        else:
            key, verdict = "D", "hướng dương nhưng chưa đạt ngưỡng niêm phong 0,50 — NGOÀI A/B/C"
        n_none = prim_["V5_distressed_only"]["candidates_archetype_none"]
        r7 = {
            "scenario": key,
            "verdict": verdict,
            "v2_sharpe": v2,
            "v5_sharpe": v5,
            "delta": delta,
            "threshold": 0.05,
            "v5_candidates_archetype_none": n_none,
            "degenerate_to_v2": n_none > 0 and abs(delta) < 1e-12,
        }
        print(f"\n[R7] V5 vs V2: V2={v2:.4f} · V5={v5:.4f} · Δ={delta:+.4f} (ngưỡng ±0,05)")
        print(f"     KỊCH BẢN {key}: {verdict}")
        print(f"     ứng viên archetype=None ở V5: {n_none}"
              + ("  ⚠ V5 suy biến thành V2" if r7["degenerate_to_v2"] else ""))

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "spec": "docs/SPEC_TREND_ENGINE_ABLATION_STUDY.md",
        "spec_sha256": sha256_lf_normalized(ABLATION_SPEC) if ABLATION_SPEC.exists() else None,
        "baseline_window": [bt.WINDOW_START, bt.WINDOW_END],
        "r1_harness_integrity": r1,
        "attribution": attribution,
        "r7_v5_decision": r7,
        "arms": results,
    }
    with (OUT_DIR / "ablation_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    cols = list(results[0].keys())
    with (OUT_DIR / "ablation_arms.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    print(f"\nđã ghi → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
