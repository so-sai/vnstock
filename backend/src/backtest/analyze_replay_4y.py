"""analyze_replay_4y.py — Read-only Attribution & Performance audit toàn chu kỳ 2022-2025.

READ-ONLY: không gọi resolve_outcomes(), không ghi vào replay DB. Chỉ đọc
các EXECUTE đã RESOLVED (return_pct, opportunity_cost) từ 4 file replay
trong backend/data/replays/ và tính Equity Curve (compounding, base 100tr),
Sharpe, Win Rate, Max Drawdown, Profit Factor, Expectancy.

Contamination boundary (BẮT BUỘC):
  2022-2024  → bằng chứng chính thức cho historical audit (IS).
  2025       → DESCRIPTIVE ONLY / OOS CONTAMINATED. Không được dùng làm
               bằng chứng chọn tham số hoặc parameter selection.
Script không tự phân biệt 2025 — người đọc output phải áp dụng boundary này.

Output assumptions:
  - return_pct = H20 (20 phiên giao dịch sau ngày quyết định), PIT strict.
  - Equity curve compounding toàn bộ BASE_CAPITAL theo từng EXECUTE.
  - Sharpe annualized *sqrt(252) trên phân phối return_pct từng trade.
  - opportunity_cost = R_best_eligible_rejected - R_executed (H20).
"""

from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
DATA = BACKEND / "data" / "replays"
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

YEARS = ("2022", "2023", "2024", "2025")
BASE_CAPITAL = 100_000_000.0


def load_executes(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT decision_id, date, symbol, action, return_pct, return_pct_60, "
            "return_pct_120, opportunity_cost, opportunity_cost_60, opportunity_cost_120, "
            "counterfactual_symbol, counterfactual_return, entry_price, p_gain "
            "FROM decision_ledger WHERE decision='EXECUTE' AND outcome_status='RESOLVED' "
            "ORDER BY date"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def metrics(trades: list[dict]) -> dict:
    wins = [t for t in trades if (t.get("return_pct") or 0) > 0]
    losses = [t for t in trades if (t.get("return_pct") or 0) <= 0]
    n = len(trades)
    w = len(wins)
    n_losses = len(losses)
    gross_win = sum(t["return_pct"] for t in wins)
    gross_loss = sum(t["return_pct"] for t in losses)

    eq = BASE_CAPITAL
    equity = [eq]
    for t in trades:
        r = (t.get("return_pct") or 0.0) / 100.0
        eq *= 1.0 + r
        equity.append(eq)
    total_return = (eq - BASE_CAPITAL) / BASE_CAPITAL * 100.0

    peak = BASE_CAPITAL
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100.0 if peak else 0.0
        max_dd = max(max_dd, dd)

    returns = [(t.get("return_pct") or 0.0) / 100.0 for t in trades]
    mean = sum(returns) / n if n else 0.0
    var = sum((x - mean) ** 2 for x in returns) / n if n else 0.0
    std = math.sqrt(var)
    sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0

    expectancy = (gross_win + gross_loss) / n if n else 0.0
    profit_factor = gross_win / abs(gross_loss) if gross_loss else (float("inf") if gross_win else 0.0)

    avg_oc = [t.get("opportunity_cost") for t in trades if t.get("opportunity_cost") is not None]
    avg_oc60 = [t.get("opportunity_cost_60") for t in trades if t.get("opportunity_cost_60") is not None]

    return {
        "n": n,
        "win_rate": w / n * 100.0 if n else 0.0,
        "avg_win": gross_win / w if w else 0.0,
        "avg_loss": gross_loss / n_losses if n_losses else 0.0,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "final_equity": eq,
        "avg_oc20": sum(avg_oc) / len(avg_oc) if avg_oc else None,
        "avg_oc60": sum(avg_oc60) / len(avg_oc60) if avg_oc60 else None,
    }


def main() -> None:
    print("=" * 78)
    print("ATTRIBUTION & PERFORMANCE — TOÀN CHU KỲ 2022-2025 (Selection v0, budget ceiling 20/năm)")
    print(f"Base vốn: {BASE_CAPITAL:.0f} VND | PIT strict | Budget 20/năm | H=20/60/120")
    print("READ-ONLY audit | 2022-2024 = IS evidence | 2025 = DESCRIPTIVE (OOS)")
    print("=" * 78, flush=True)

    all_trades = []
    for y in YEARS:
        db = DATA / f"replay_{y}.db"
        if not db.exists():
            print(f"  [SKIP] {db.name} không tồn tại", flush=True)
            continue
        trades = load_executes(db)
        trades = [t for t in trades if t.get("return_pct") is not None]
        n_resolved = len(trades)
        m = metrics(trades)
        all_trades.extend(trades)
        badge = "DESCRIPTIVE" if y == "2025" else "IS"
        print(
            f"  {y} [{badge}]: resolved={n_resolved} | {m['n']} trades | "
            f"WinRate={m['win_rate']:.1f}% | Ret={m['total_return']:+.1f}% | "
            f"Sharpe={m['sharpe']:.3f} | MaxDD={m['max_drawdown']:.1f}% | "
            f"PF={m['profit_factor']:.2f} | Exp={m['expectancy']:+.2f}pp | "
            f"OC20={m['avg_oc20'] if m['avg_oc20'] is not None else 'n/a'}",
            flush=True,
        )
    print("-" * 78)

    all_trades.sort(key=lambda t: t["date"])
    m = metrics(all_trades)
    print(f"  TOÀN KỲ 2022-2025: {m['n']} trades", flush=True)
    print(f"    Win Rate        : {m['win_rate']:.1f}%")
    print(f"    Avg Win / Loss  : {m['avg_win']:+.2f}% / {m['avg_loss']:+.2f}%")
    print(f"    Expectancy      : {m['expectancy']:+.2f}pp / trade")
    print(f"    Profit Factor   : {m['profit_factor']:.2f}")
    print(f"    Total Return    : {m['total_return']:+.1f}%")
    print(f"    Final Equity    : {m['final_equity']:.0f} VND")
    print(f"    Sharpe (annual) : {m['sharpe']:.3f}")
    print(f"    Max Drawdown    : {m['max_drawdown']:.1f}%")
    print(f"    Avg OppCost H20 : {m['avg_oc20'] if m['avg_oc20'] is not None else 'n/a'}")
    print(f"    Avg OppCost H60 : {m['avg_oc60'] if m['avg_oc60'] is not None else 'n/a'}")

    oc_pos = sum(1 for t in all_trades if (t.get("opportunity_cost") or 0) > 0)
    oc_neg = sum(1 for t in all_trades if (t.get("opportunity_cost") or 0) < 0)
    print(f"    OppCost>0 (chọn tốt hơn alt): {oc_pos} | OppCost<0 (bỏ lỡ alt tốt hơn): {oc_neg}")
    print("=" * 78)


if __name__ == "__main__":
    main()
