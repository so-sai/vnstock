"""decompose_replay_by_regime.py — Regime-conditioned audit cho 80 EXECUTE 2022-2025.

READ-ONLY: không ghi vào replay DB hay screener_cache.db.
Join 80 EXECUTE (decision_ledger) với regime_history (screener_cache.db) theo date.
Xuất bảng per-regime: EXEC | WinRate | MeanR20 | PF | MaxDD | Sharpe | Mean p_gain
+ Evidence Coverage + Decision Quality + Calibration error (p_gain vs realized).

Contamination boundary: 2025 = DESCRIPTIVE (OOS). Bảng tổng tách riêng 2022-2024 (IS).
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

from src.config import DATA_DIR

YEARS = ("2022", "2023", "2024", "2025")
REGIMES = ("CRISIS", "CRISIS_WARNING", "RANGING", "TRENDING")
BASE_CAPITAL = 100_000_000.0


def load_executes() -> list[dict]:
    out = []
    for y in YEARS:
        c = sqlite3.connect(str(DATA / f"replay_{y}.db"))
        c.row_factory = sqlite3.Row
        try:
            rows = c.execute(
                "SELECT date, symbol, decision_id, p_gain, return_pct, "
                "decision_quality, n_independent_evidence, outcome_status "
                "FROM decision_ledger WHERE decision='EXECUTE' AND outcome_status='RESOLVED'"
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["year"] = y
                d["is_2025"] = y == "2025"
                out.append(d)
        finally:
            c.close()
    return out


def load_regimes() -> dict[str, str]:
    c = sqlite3.connect(str(DATA_DIR / "screener_cache.db"))
    try:
        rows = c.execute("SELECT date, status FROM regime_history").fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        c.close()


def regime_metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    rets = [(t.get("return_pct") or 0.0) / 100.0 for t in trades]
    wins = sum(1 for r in rets if r > 0)
    gross_win = sum(r for r in rets if r > 0) * 100.0
    gross_loss = sum(r for r in rets if r <= 0) * 100.0
    eq = BASE_CAPITAL
    peak = BASE_CAPITAL
    max_dd = 0.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    total_return = (eq - BASE_CAPITAL) / BASE_CAPITAL * 100.0
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / n
    std = math.sqrt(var)
    sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0
    pf = gross_win / abs(gross_loss) if gross_loss else (float("inf") if gross_win else 0.0)
    return {
        "n": n,
        "win_rate": wins / n * 100.0,
        "mean_r20": mean * 100.0,
        "pf": pf,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "total_return": total_return,
    }


def _calib(tr: list[dict]) -> float:
    """Calibration error = mean(|p_gain - realized win-rate-to-date-scaled|)... đơn giản: mean(p_gain) - mean(win)."""
    if not tr:
        return float("nan")
    pg = [t.get("p_gain") or 0.0 for t in tr]
    rets = [t.get("return_pct") or 0.0 for t in tr]
    mean_pg = sum(pg) / len(pg)
    realized = sum(1 for r in rets if r > 0) / len(rets) * 100.0
    return mean_pg * 100.0 - realized


def main() -> None:
    trades = load_executes()
    regimes = load_regimes()
    for t in trades:
        t["regime"] = regimes.get(t["date"], "NO_REGIME")

    print("=" * 100)
    print("REGIME DECOMPOSITION — 80 EXECUTE 2022-2025 (PIT regime at decision date)")
    print("Regime từ regime_history (screener_cache.db) theo đúng ngày quyết định")
    print("=" * 100)
    hdr = f"{'Regime':<18}{'EXEC':>6}{'WinRate':>9}{'MeanR20':>9}{'PF':>7}{'MaxDD':>8}{'Sharpe':>8}{'Ret':>9}{'meanPg':>8}"
    print(hdr)
    print("-" * 100)

    per_regime: dict[str, list[dict]] = {r: [] for r in REGIMES}
    for t in trades:
        per_regime.setdefault(t["regime"], []).append(t)

    for r in REGIMES:
        tr = per_regime.get(r, [])
        if not tr:
            print(f"{r:<18}{0:>6}")
            continue
        m = regime_metrics(tr)
        mean_pg = sum(t.get("p_gain") or 0.0 for t in tr) / len(tr)
        print(
            f"{r:<18}{m['n']:>6}{m['win_rate']:>8.1f}%{m['mean_r20']:>8.2f}%{m['pf']:>7.2f}"
            f"{m['max_dd']:>7.1f}%{m['sharpe']:>8.3f}{m['total_return']:>8.1f}%{mean_pg * 100:>7.1f}%"
        )
    for r in ("NO_REGIME",):
        tr = per_regime.get(r, [])
        if tr:
            print(f"{r:<18}{len(tr):>6}  (dates thiếu regime_history)")
    print("-" * 100)

    is_trades = [t for t in trades if not t["is_2025"]]
    desc_trades = [t for t in trades if t["is_2025"]]

    def print_block(label: str, tr: list[dict]) -> None:
        m = regime_metrics(tr)
        dq = [t.get("decision_quality") for t in tr if t.get("decision_quality")]
        cov = [t.get("n_independent_evidence") for t in tr]
        calib = _calib(tr)
        avg_cov = sum(cov) / len(cov) if cov else 0
        print(f"\n  {label}: n={m['n']}")
        print(
            f"    WinRate={m['win_rate']:.1f}% | MeanR20={m['mean_r20']:+.2f}% | PF={m['pf']:.2f} | "
            f"MaxDD={m['max_dd']:.1f}% | Sharpe={m['sharpe']:.3f} | Ret={m['total_return']:+.1f}%"
        )
        print(f"    DecisionQuality={sorted(set(dq))} | AvgEvidenceCount={avg_cov:.2f} | CalibErr(pg%−win%)=±{calib:.1f}")

    print_block("IS 2022-2024 (evidence chính thức)", is_trades)
    print_block("DESCRIPTIVE 2025 (OOS contaminated)", desc_trades)
    print_block("TOÀN KỲ 2022-2025", trades)
    print("=" * 100)


if __name__ == "__main__":
    main()
