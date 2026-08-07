"""vn20_backtest.py — A/B Backtest: Static 25% vs Industry-Percentile Receivables Gate.

Point-in-Time quarterly rebalance from 2022Q1 to 2026Q2.
Measures CAGR, Max Drawdown, Sharpe Ratio for both scenarios.

WHY: The static 25% receivables cap caused Type II Error against B2B/IT companies
like FPT (67.2% receivables — structural, not governance risk). This backtest
quantifies the Alpha impact of enabling FPT (and similar) via Industry-Relative
Percentile Gate.
"""

import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent  # scripts/ → src/ → backend/
sys.path.insert(0, str(BACKEND))

FIN_DB = BACKEND / "data" / "financial_facts.db"
SCREEN_DB = BACKEND / "data" / "screener_cache.db"


def _quarters_between(start: str, end: str) -> list[str]:
    """Generate quarter strings from start to end inclusive. '2022Q1' -> ['2022Q1', ...]"""
    quarters = []
    y, q = int(start[:4]), int(start[5])
    ey, eq = int(end[:4]), int(end[5])
    while (y, q) <= (ey, eq):
        quarters.append(f"{y}Q{q}")
        q += 1
        if q > 4:
            q = 1
            y += 1
    return quarters


def _load_fin_data(db_path: Path, symbol: str, max_period: str) -> dict[str, dict[str, float]]:
    """Load financial_facts for symbol, only periods <= max_period (PIT)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT period, metric, value FROM financial_facts WHERE symbol=? AND value IS NOT NULL ORDER BY period",
        (symbol,),
    ).fetchall()
    conn.close()
    data: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["period"] <= max_period:
            data.setdefault(r["period"], {})[r["metric"]] = float(r["value"])
    return data


def _compute_receivables_ratio(fin_data: dict[str, dict[str, float]], last_n: int = 4) -> float | None:
    """Compute RECEIVABLES/REVENUE from latest N periods."""
    periods = sorted(fin_data.keys())[-last_n:]
    rec_vals, rev_vals = [], []
    for p in periods:
        m = fin_data.get(p, {})
        if "RECEIVABLES" in m and m["RECEIVABLES"] > 0:
            rec_vals.append(m["RECEIVABLES"])
        if "REVENUE" in m and m["REVENUE"] > 0:
            rev_vals.append(m["REVENUE"])
    if rec_vals and rev_vals:
        return max(rec_vals) / max(rev_vals)
    return None


def _compute_sector_pct75(
    fin_data_all: dict[str, dict[str, dict]], symbols: list[str], max_period: str, sector_map: dict[str, str]
) -> dict[str, float | None]:
    """Compute sector P75 of receivables ratio across symbols."""
    sector_ratios: dict[str, list[float]] = {}
    for sym in symbols:
        sector = sector_map.get(sym, "UNKNOWN")
        fin_data = fin_data_all.get(sym, {})
        # Filter to periods <= max_period
        filtered = {k: v for k, v in fin_data.items() if k <= max_period}
        ratio = _compute_receivables_ratio(filtered)
        if ratio is not None:
            sector_ratios.setdefault(sector, []).append(ratio)

    result = {}
    for sector, ratios in sector_ratios.items():
        if len(ratios) >= 3:
            sorted_r = sorted(ratios)
            idx = int(len(sorted_r) * 0.75)
            result[sector] = sorted_r[min(idx, len(sorted_r) - 1)]
        else:
            # Solo/small sector fallback: use max ratio (P100)
            result[sector] = max(ratios) if ratios else None
    return result


def _pass_t2_static(ratio: float | None) -> bool:
    """Old gate: ratio <= 25%."""
    if ratio is None:
        return True  # missing data → skip gate
    return ratio <= 0.25


def _pass_t2_percentile(ratio: float | None, sector_pct75: float | None) -> bool:
    """New gate: ratio <= 25% OR ratio <= sector_pct75."""
    if ratio is None:
        return True
    if ratio <= 0.25:
        return True
    if sector_pct75 is not None and ratio <= sector_pct75:
        return True
    return False


def _get_price_data(db_path: Path, symbol: str, start: str, end: str) -> dict[str, float]:
    """Get daily close prices for date range."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT date, close FROM daily_ohlcv WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
        (symbol, start, end),
    ).fetchall()
    conn.close()
    return {r["date"]: float(r["close"]) for r in rows if r["close"] and r["close"] > 0}


def run_backtest(start_q: str = "2022Q1", end_q: str = "2026Q2"):
    """Run A/B backtest comparing static vs percentile receivables gate."""
    fin = sqlite3.connect(str(FIN_DB))
    fin.row_factory = sqlite3.Row
    screen = sqlite3.connect(str(SCREEN_DB))
    screen.row_factory = sqlite3.Row

    # Verify health_ratios exists
    tables = [r[0] for r in fin.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "health_ratios" not in tables:
        print(f"ERROR: health_ratios not in {FIN_DB}. Available: {tables}")
        return

    # Load sector mapping
    try:
        sector_map = {
            r["symbol"]: r["icb_name3"] for r in screen.execute("SELECT symbol, icb_name3 FROM symbol_industry").fetchall()
        }
    except Exception:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        sector_map = {}

    # Get universe (symbols with health data)
    universe = [r["symbol"] for r in fin.execute("SELECT DISTINCT symbol FROM health_ratios ORDER BY symbol").fetchall()]

    # Load ALL fin data upfront (PIT filtering done later)
    print(f"Loading financial data for {len(universe)} symbols...")
    fin_data_all: dict[str, dict[str, dict]] = {}
    for sym in universe:
        rows = fin.execute(
            "SELECT period, metric, value FROM financial_facts WHERE symbol=? AND value IS NOT NULL ORDER BY period",
            (sym,),
        ).fetchall()
        for r in rows:
            fin_data_all.setdefault(sym, {}).setdefault(r["period"], {})[r["metric"]] = float(r["value"])

    # Get VNINDEX price for benchmark
    vnindex = {}
    rows = screen.execute(
        "SELECT date, close FROM daily_ohlcv "
        "WHERE symbol='VNINDEX' AND date BETWEEN '2022-01-01' AND '2026-08-03' ORDER BY date"
    ).fetchall()
    for r in rows:
        if r["close"] and r["close"] > 0:
            vnindex[r["date"]] = float(r["close"])

    # Quarterly rebalance dates
    quarters = _quarters_between(start_q, end_q)
    rebal_dates = {
        "2022Q1": "2022-03-31",
        "2022Q2": "2022-06-30",
        "2022Q3": "2022-09-30",
        "2022Q4": "2022-12-30",
        "2023Q1": "2023-03-31",
        "2023Q2": "2023-06-30",
        "2023Q3": "2023-09-29",
        "2023Q4": "2023-12-29",
        "2024Q1": "2024-03-28",
        "2024Q2": "2024-06-28",
        "2024Q3": "2024-09-30",
        "2024Q4": "2024-12-31",
        "2025Q1": "2025-03-31",
        "2025Q2": "2025-06-30",
        "2025Q3": "2025-09-30",
        "2025Q4": "2025-12-31",
        "2026Q1": "2026-03-31",
        "2026Q2": "2026-06-30",
    }

    # Track portfolios
    portfolio_a = []  # static gate
    portfolio_b = []  # percentile gate
    bench_returns = []

    print(f"\nBacktest: {start_q} → {end_q} ({len(quarters)} quarters)")
    print(f"Universe: {len(universe)} symbols\n")

    for i, q in enumerate(quarters[:-1]):
        max_period = q
        next_q = quarters[i + 1]
        rebal_date = rebal_dates.get(q, "2022-03-31")
        next_rebal = rebal_dates.get(next_q, "2022-06-30")

        # Compute sector P75 at this point
        sector_pct75 = _compute_sector_pct75(fin_data_all, universe, max_period, sector_map)

        # Filter symbols that pass T2 with each gate
        pass_a, pass_b = [], []
        for sym in universe:
            fd = {k: v for k, v in fin_data_all.get(sym, {}).items() if k <= max_period}
            ratio = _compute_receivables_ratio(fd)
            sector = sector_map.get(sym, "UNKNOWN")
            p75 = sector_pct75.get(sector)

            if _pass_t2_static(ratio):
                pass_a.append(sym)
            if _pass_t2_percentile(ratio, p75):
                pass_b.append(sym)

        # Compute returns for next quarter
        def portfolio_return(symbols: list[str]) -> float:
            if not symbols:
                return 0.0
            rets = []
            for sym in symbols[:20]:  # cap at 20
                prices = _get_price_data(SCREEN_DB, sym, rebal_date, next_rebal)
                if len(prices) < 2:
                    continue
                p_start = list(prices.values())[0]
                p_end = list(prices.values())[-1]
                if p_start > 0:
                    rets.append(p_end / p_start - 1.0)
            return sum(rets) / len(rets) if rets else 0.0

        ret_a = portfolio_return(pass_a)
        ret_b = portfolio_return(pass_b)

        # Benchmark return
        vn_start = None
        vn_end = None
        for d, v in sorted(vnindex.items()):
            if d >= rebal_date and vn_start is None:
                vn_start = v
            if d <= next_rebal:
                vn_end = v
        bench_ret = (vn_end / vn_start - 1.0) if vn_start and vn_end and vn_start > 0 else 0.0

        # New stocks in B but not in A (FPT-like beneficiaries)
        new_in_b = set(pass_b) - set(pass_a)

        print(
            f"  {q}: A={len(pass_a):2d} stocks ret={ret_a:+6.1%} | "
            f"B={len(pass_b):2d} stocks ret={ret_b:+6.1%} | "
            f"Bench={bench_ret:+6.1%} | "
            f"New in B: {', '.join(sorted(new_in_b)[:5]) or 'none'}"
        )

        portfolio_a.append({"quarter": q, "return": ret_a, "symbols": len(pass_a)})
        portfolio_b.append({"quarter": q, "return": ret_b, "symbols": len(pass_b), "new": list(new_in_b)})
        bench_returns.append({"quarter": q, "return": bench_ret})

    # Summary metrics
    def compute_metrics(returns: list[float], label: str):
        if not returns:
            return {}
        cum = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in returns:
            cum *= 1 + r
            peak = max(peak, cum)
            dd = (cum - peak) / peak
            max_dd = min(max_dd, dd)
        n = len(returns)
        cagr = cum ** (1 / max(n, 1)) - 1
        avg = sum(returns) / n
        std = (sum((r - avg) ** 2 for r in returns) / max(n - 1, 1)) ** 0.5
        sharpe = avg / std * (4**0.5) if std > 0 else 0  # annualized
        return {"label": label, "CAGR": cagr, "MaxDD": max_dd, "Sharpe": sharpe, "Cumulative": cum - 1}

    rets_a = [p["return"] for p in portfolio_a]
    rets_b = [p["return"] for p in portfolio_b]
    rets_bn = [p["return"] for p in bench_returns]

    m_a = compute_metrics(rets_a, "A: Static 25%")
    m_b = compute_metrics(rets_b, "B: Percentile P75")
    m_bn = compute_metrics(rets_bn, "Benchmark (VNINDEX)")

    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)
    for m in [m_a, m_b, m_bn]:
        print(f"  {m['label']:25s} | CAGR: {m['CAGR']:+6.1%} | MaxDD: {m['MaxDD']:+6.1%} | Sharpe: {m['Sharpe']:+5.2f}")

    # Alpha
    alpha_a = m_a["CAGR"] - m_bn["CAGR"]
    alpha_b = m_b["CAGR"] - m_bn["CAGR"]
    print("\n  Alpha vs Benchmark:")
    print(f"    A (Static):  {alpha_a:+6.1%}")
    print(f"    B (Percentile): {alpha_b:+6.1%}")
    print(f"    B - A (Percentile uplift): {alpha_b - alpha_a:+6.1%}")

    # New stocks impact
    all_new = set()
    for p in portfolio_b:
        all_new.update(p.get("new", []))
    if all_new:
        print(f"\n  Stocks enabled by Percentile Gate: {', '.join(sorted(all_new))}")

    fin.close()
    screen.close()
    return {"A": m_a, "B": m_b, "Benchmark": m_bn}


if __name__ == "__main__":
    run_backtest("2022Q1", "2026Q2")
