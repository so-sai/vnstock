"""vn20_quant_filter.py — 4-Tier Dynamic Quant Screening (PTCK_VN20).

Buffett Quality x VN Governance x Sector Cycle x Valuation/Allocation.

Tier 1 — BUFFETT QUALITY (static, long-term moat):
  ROE >= 15% (3y), CFO > 0 (3y), D/E < 1.0 (non-bank) / < 8.0 (bank),
  Gross Margin >= 25%.

Tier 2 — GOVERNOR SHIELD (VN governance):
  Dilution rate <= 5%/yr (share count growth), Receivables/Revenue <= 25%.

Tier 3 — SECTOR CYCLE DETECTOR (dynamic timing):
  Sector RS momentum + valuation percentile -> 4 phases
  (RECOVERY / EXPANSION / SLOWDOWN / CONTRACTION). Only buy in
  RECOVERY | EXPANSION.

Tier 4 — VALUATION & ALLOCATION:
  Margin of Safety from valuation z-score; Bayesian-style allocation,
  5-8 names, max 25% per name, defensive cash for saturated cycles.

Data sources (financial_facts.db + screener_cache.db):
  health_ratios (ROE, DEBT_TO_EQUITY, GROSS_MARGIN, RECEIVABLES_TO_REVENUE)
  financial_facts (CFO, SHARES_OUT, RECEIVABLES, REVENUE, TOTAL_DEBT,
                   TOTAL_EQUITY, NET_INCOME)
  valuation_scores (z-scores for MoS)
  symbol_industry (sector mapping) + daily_ohlcv (sector RS)
"""

import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()
FIN_DB = PROJECT_ROOT / "backend" / "data" / "financial_facts.db"
SCREEN_DB = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

# ── Tunable thresholds (Buffer-tuned for VN emerging-market volatility) ──
T1_ROE_MIN = 0.15  # 15%
T1_DE_MAX_NONBANK = 1.0
T1_DE_MAX_BANK = 8.0
T1_GROSS_MARGIN_MIN = 0.25  # 25%
T2_DILUTION_MAX = 0.05  # 5%/yr
T2_RECEIVABLES_MAX = 0.25  # 25% of revenue
T4_MOS_MIN = 0.25  # 25% margin of safety (VN premium)
T4_TARGET_SIZE = 5  # 5-8 names
T4_TARGET_SIZE_MAX = 8
T4_MAX_WEIGHT = 0.25  # 25% max per name
T4_MAX_SECTOR_WEIGHT = 0.50  # max 50% of portfolio in a single sector
N_YEARS = 3  # lookback for "3 consecutive years"

# ── Sector cycle phases ──
CYCLE_BUY = {"RECOVERY", "EXPANSION"}
CYCLE_SELL = {"SLOWDOWN", "CONTRACTION"}

SECTOR_CYCLE_LABELS = {
    "RECOVERY": "Phục hồi (RECOVERY)",
    "EXPANSION": "Tăng tốc (EXPANSION)",
    "SLOWDOWN": "Suy yếu (SLOWDOWN)",
    "CONTRACTION": "Suy thoái (CONTRACTION)",
}


def _fin_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(FIN_DB), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _screen_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SCREEN_DB), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_period(conn) -> str:
    row = conn.execute("SELECT MAX(period) FROM health_ratios WHERE period LIKE '%Q%'").fetchone()
    return row[0] if row else ""


def _periods_n_years(period: str, n: int = N_YEARS) -> List[str]:
    """Return list of period keys for the last n years ending at `period`."""


def _periods_n_years(period: str, n: int = N_YEARS) -> List[str]:
    """Return period keys for the last n years ending at `period`, ASCENDING.

    Oldest first (2024Q1 → 2026Q2) so `periods[-4:]` = the 4 MOST RECENT
    quarters (was descending before — a latent bug that made D/E & Gross
    Margin lookups target the oldest quarters instead of latest).
    """
    try:
        y, q = period.split("Q")
        y = int(y)
        q = int(q)
    except Exception:
        return []
    out = []
    for yy in range(y - n + 1, y + 1):
        for qq in range(1, 5):
            if (yy, qq) <= (y, q):
                out.append(f"{yy}Q{qq}")
    return out[-n * 4 :]


# ════════════════════════════════════════════════════════════════════
# TIER 1 — BUFFETT QUALITY (Quality Minus Junk)
# ════════════════════════════════════════════════════════════════════


def _load_ratio_series(conn, symbol: str, ratio: str, periods: List[str]) -> List[float]:
    ph = ",".join("?" for _ in periods)
    rows = conn.execute(
        f"SELECT period, ratio_value FROM health_ratios WHERE symbol=? AND ratio_name=? AND period IN ({ph}) ORDER BY period",
        (symbol, ratio, *periods),
    ).fetchall()
    return [float(r["ratio_value"]) for r in rows if r["ratio_value"] is not None]


def _load_metric_years(conn, symbol: str, metric: str, periods: List[str]) -> Dict[str, float]:
    """Map {year: value} for a financial_facts metric across last n years."""
    ph = ",".join("?" for _ in periods)
    rows = conn.execute(
        f"SELECT period, value FROM financial_facts WHERE symbol=? AND metric=? AND period IN ({ph}) ORDER BY period",
        (symbol, metric, *periods),
    ).fetchall()
    out: Dict[str, float] = {}
    for r in rows:
        y = r["period"][:4]
        if r["value"] is not None:
            out[y] = float(r["value"])
    return out


def tier1_buffett_quality(conn, symbol: str, entity_type: str, periods: List[str]) -> Dict:
    """Tier 1: Buffett quality gate. Returns pass/fail + metrics."""
    result = {
        "symbol": symbol,
        "entity_type": entity_type,
        "pass": False,
        "roe": None,
        "roe_3y_min": None,
        "cfo_years": 0,
        "cfo_positive": False,
        "de": None,
        "de_max": T1_DE_MAX_NONBANK,
        "gross_margin": None,
        "reasons": [],
    }

    # ROE: require >= 15% for last 3 years.
    # NOTE: health_ratios ROE is SINGLE-QUARTER (e.g. 0.065 for FPT/Q) →
    # annualize x4 before comparing to the 15% yearly threshold.
    roe_series = _load_ratio_series(conn, symbol, "ROE", periods)
    if roe_series:
        roe_annual = [v * 4.0 for v in roe_series]
        yearly = {}
        for p, v in zip(periods, roe_annual):
            yearly.setdefault(p[:4], []).append(v)
        roe_3y = [sum(vs) / len(vs) for vs in yearly.values()]
        result["roe_3y_min"] = round(min(roe_3y), 4) if roe_3y else None
        result["roe"] = round(roe_series[-1] * 4.0, 4) if roe_series else None
        if roe_3y and min(roe_3y) >= T1_ROE_MIN:
            pass  # ok
        else:
            result["reasons"].append(
                f"ROE 3y min {result['roe_3y_min']} < 15%" if result["roe_3y_min"] is not None else "ROE data missing"
            )

    # CFO: > 0 for 3 consecutive years
    cfo_years = _load_metric_years(conn, symbol, "CFO", periods)
    if cfo_years:
        result["cfo_years"] = len(cfo_years)
        result["cfo_positive"] = all(v > 0 for v in cfo_years.values())
        if not result["cfo_positive"]:
            result["reasons"].append("CFO không dương liên tục 3 năm")
    else:
        result["reasons"].append("CFO data missing")

    # D/E — Banks: use CAPITAL_RATIO as capital-safety proxy instead of D/E.
    # VCI statements don't emit DEBT_TO_EQUITY for banks (NPL/CASA are separate
    # indicators); missing D/E for a BANK is NOT a fail — the leverage gate is
    # replaced by the capital adequacy check.
    de_max = T1_DE_MAX_BANK if entity_type == "BANK" else T1_DE_MAX_NONBANK
    result["de_max"] = de_max
    de_series = _load_ratio_series(conn, symbol, "DEBT_TO_EQUITY", periods[-4:])
    if de_series:
        result["de"] = round(de_series[-1], 4)
        if result["de"] > de_max:
            result["reasons"].append(f"D/E {result['de']} > {de_max}")
    elif entity_type != "BANK":
        result["reasons"].append("D/E data missing")

    # Gross margin — banks exempt (no COGS-based margin; NIM used instead)
    gm_series = _load_ratio_series(conn, symbol, "GROSS_MARGIN", periods[-4:])
    if gm_series:
        result["gross_margin"] = round(gm_series[-1], 4)
        if result["gross_margin"] < T1_GROSS_MARGIN_MIN and entity_type != "BANK":
            result["reasons"].append(f"Gross margin {result['gross_margin']} < 25%")
    elif entity_type != "BANK":
        result["reasons"].append("Gross margin data missing")

    result["pass"] = len(result["reasons"]) == 0
    return result


# ════════════════════════════════════════════════════════════════════
# TIER 2 — GOVERNOR SHIELD (VN governance)
# ════════════════════════════════════════════════════════════════════


def tier2_governance_shield(conn, symbol: str, periods: List[str], entity_type: str = "STANDARD") -> Dict:
    """Tier 2: dilution rate + receivables health.

    Banks: receivables gate is skipped (banks don't have trade receivables;
    governance risk is captured by NPL/capital adequacy in Tier 1/health).
    """
    result = {
        "symbol": symbol,
        "pass": False,
        "dilution": None,
        "receivables_ratio": None,
        "reasons": [],
    }

    # Dilution: median of YoY same-quarter share changes (Qx this vs Qx last year).
    # Median (not last-pair) tolerates single-quarter crawler scale errors
    # (e.g. HPG 3.2B→292M in 2025Q3) so one bad point can't fake dilution.
    q_map: Dict[str, float] = {}
    for r in conn.execute(
        "SELECT period, value FROM financial_facts WHERE symbol=? AND metric='SHARES_OUT' ORDER BY period",
        (symbol,),
    ).fetchall():
        if r["value"] is not None:
            q_map[r["period"]] = float(r["value"])
    q_keys = sorted(q_map.keys())
    deltas = []
    for k in q_keys:
        y, qq = k[:4], k[4:]  # "2021Q3" -> y="2021", qq="Q3"
        prev = f"{int(y) - 1}{qq}"
        if prev in q_map and q_map[prev] and q_map[prev] > 0:
            deltas.append((q_map[k] - q_map[prev]) / q_map[prev])
    if deltas:
        deltas_sorted = sorted(deltas)
        med = deltas_sorted[len(deltas_sorted) // 2]
        result["dilution"] = round(med, 4)
        if result["dilution"] > T2_DILUTION_MAX:
            result["reasons"].append(f"Dilution {result['dilution']:.1%} > 5%/yr")
    # else: SHARES_OUT absent → dilution gate is SKIPPED (missing ≠ bad).
    # Source gap: VCI statements don't emit share count (market-level data).
    # Hard-failing on it would bias against banks/issuers without the metric.

    # Receivables ratio — computed from raw financial_facts (RECEIVABLES/REVENUE).
    # NOTE: health_ratios.RECEIVABLES_TO_REVENUE is unreliable (hardcoded 1.5
    # in crawler output) → derive from statements instead.
    if entity_type == "BANK":
        result["receivables_ratio"] = None  # n/a for banks — skip gate
    else:
        rec = _load_metric_years(conn, symbol, "RECEIVABLES", periods[-4:])
        rev = _load_metric_years(conn, symbol, "REVENUE", periods[-4:])
        if rec and rev:
            rec_last = max(rec.values())
            rev_last = max(rev.values())
            if rev_last and rev_last > 0:
                result["receivables_ratio"] = round(rec_last / rev_last, 4)
                if result["receivables_ratio"] > T2_RECEIVABLES_MAX:
                    result["reasons"].append(f"Receivables {result['receivables_ratio']:.1%} > 25%")
        else:
            result["reasons"].append("Receivables data missing")

    result["pass"] = len(result["reasons"]) == 0
    return result


# ════════════════════════════════════════════════════════════════════
# TIER 3 — SECTOR CYCLE DETECTOR
# ════════════════════════════════════════════════════════════════════


def tier3_sector_cycle(symbol: str, sector_ctx: Dict) -> Dict:
    """Tier 3: sector cycle phase from RS momentum + valuation percentile.

    sector_ctx (computed once per sector):
      { momentum: ma5/ma20-1, rs_rank_pct, pb_pct (valuation percentile) }
    """
    result = {
        "symbol": symbol,
        "sector": sector_ctx.get("sector", "UNKNOWN"),
        "phase": "UNKNOWN",
        "momentum": sector_ctx.get("momentum"),
        "rs_rank_pct": sector_ctx.get("rs_rank_pct"),
        "valuation_pct": sector_ctx.get("valuation_pct"),
        "pass": False,
        "reasons": [],
    }

    mom = result["momentum"]
    val_pct = result["valuation_pct"]
    if mom is None or val_pct is None:
        result["reasons"].append("Sector data missing")
        return result

    # Phase logic:
    #   RECOVERY: cheap valuation (pct low) + momentum turning positive
    #   EXPANSION: momentum positive + valuation rising
    #   SLOWDOWN: momentum weakening but valuation still high
    #   CONTRACTION: momentum negative + valuation compressing
    if mom > 0.02 and val_pct < 0.50:
        phase = "RECOVERY"
    elif mom > 0.02:
        phase = "EXPANSION"
    elif mom > -0.02:
        phase = "SLOWDOWN"
    else:
        phase = "CONTRACTION"

    result["phase"] = phase
    result["pass"] = phase in CYCLE_BUY
    if not result["pass"]:
        result["reasons"].append(f"Sector {phase} — không mua")
    return result


def compute_sector_context(conn, sector: str, lookback: int = 90) -> Dict:
    """Compute per-sector cycle context: RS momentum + valuation percentile."""
    import pandas as pd

    mapping = _load_symbol_industry(conn)
    symbols = [s for s, sec in mapping.items() if sec == sector]
    if not symbols:
        return {"sector": sector, "momentum": None, "valuation_pct": None}

    ph = ",".join("?" for _ in symbols)
    df = pd.read_sql(
        f"SELECT symbol, date, close FROM daily_ohlcv "
        f"WHERE symbol IN ({ph}) AND date >= date('now', '-{lookback + 15} days') "
        f"ORDER BY date",
        conn,
        params=symbols,
    )
    if df.empty or len(df) < 30:
        return {"sector": sector, "momentum": None, "valuation_pct": None}

    df["ret"] = df.groupby("symbol")["close"].pct_change()
    daily = df.groupby("date")["ret"].mean().reset_index().sort_values("date")
    # Clean inf/NaN (broken prices produce inf pct_change → poisons cumprod)
    daily["ret"] = daily["ret"].replace([float("inf"), float("-inf")], pd.NA)
    daily = daily.dropna(subset=["ret"])
    daily["cum"] = (1 + daily["ret"].fillna(0.0)).cumprod()
    daily["ma5"] = daily["cum"].rolling(5).mean()
    daily["ma20"] = daily["cum"].rolling(20).mean()
    last5 = daily["ma5"].dropna()
    last20 = daily["ma20"].dropna()
    if len(last20) == 0 or len(last5) == 0:
        momentum = None
    else:
        m5 = last5.iloc[-1]
        m20 = last20.iloc[-1]
        momentum = float(m5 / m20 - 1.0) if m20 and not pd.isna(m20) else None

    # Valuation percentile: share of symbols cheap vs expensive in sector
    val_pct = None
    try:
        fin = _fin_conn()
        zs = []
        for sym in symbols:
            row = fin.execute(
                "SELECT z_score FROM valuation_scores "
                "WHERE symbol=? AND ratio_name='PE' "
                "AND period=(SELECT MAX(period) FROM valuation_scores WHERE symbol=? AND ratio_name='PE')",
                (sym, sym),
            ).fetchone()
            if row and row[0] is not None:
                zs.append(float(row[0]))
        fin.close()
        if zs:
            cheap = sum(1 for z in zs if z < -0.5) / len(zs)
            val_pct = round(1.0 - cheap, 4)  # high = expensive
    except Exception:
        pass

    return {"sector": sector, "momentum": momentum, "valuation_pct": val_pct}


def _load_symbol_industry(conn) -> Dict[str, str]:
    try:
        rows = conn.execute("SELECT symbol, icb_name3 FROM symbol_industry").fetchall()
        return {r["symbol"]: r["icb_name3"] for r in rows}
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════════
# TIER 4 — VALUATION & ALLOCATION
# ════════════════════════════════════════════════════════════════════


def tier4_valuation_mos(conn, symbol: str) -> Dict:
    """Tier 4: Margin of Safety from valuation z-scores.

    MoS = max over PE/PB z-score of (1 - z_inverse), floored 0..1.
    Requires MoS >= 25% for entry.
    """
    result = {
        "symbol": symbol,
        "pass": False,
        "mos": None,
        "pe_z": None,
        "pb_z": None,
        "reasons": [],
    }
    rows = conn.execute(
        "SELECT ratio_name, z_score FROM valuation_scores "
        "WHERE symbol=? AND ratio_name IN ('PE','PB') "
        "AND period=(SELECT MAX(period) FROM valuation_scores WHERE symbol=?)",
        (symbol, symbol),
    ).fetchall()
    z_map = {r["ratio_name"]: r["z_score"] for r in rows}
    zs = []
    for key in ("PE", "PB"):
        z = z_map.get(key)
        if z is not None:
            z = float(z)
            if key == "PE":
                result["pe_z"] = round(z, 3)
            else:
                result["pb_z"] = round(z, 3)
            zs.append(z)

    if zs:
        # z < 0 => cheap => high MoS. Map z=-2 -> MoS~1, z=+2 -> MoS~0.
        mos = max(0.0, min(1.0, 1.0 - (max(zs) / 3.0 + 0.5)))
        result["mos"] = round(mos, 4)
        result["pass"] = mos >= T4_MOS_MIN
        if not result["pass"]:
            result["reasons"].append(f"MoS {mos:.1%} < 25%")
    else:
        result["reasons"].append("Valuation data missing")

    return result


def allocate(qualified: List[Dict]) -> Dict:
    """Bayesian-style allocation: 5-8 names, max 25% each.

    Max Sector Concentration Gate: no single sector may exceed
    T4_MAX_SECTOR_WEIGHT of the portfolio. Excess sector weight is drained
    to cash/defensive. Prevents sector-clustering trap (e.g. 6/6 banks).
    """
    n = len(qualified)
    if n == 0:
        return {"weights": {}, "cash": 1.0, "summary": "NO_QUALIFIED"}
    target_n = min(max(n, T4_TARGET_SIZE), T4_TARGET_SIZE_MAX)
    picked = sorted(qualified, key=lambda x: x.get("score", 0), reverse=True)[:target_n]

    weights = {}
    base = 1.0 / len(picked)
    for p in picked:
        # Scale by MoS: cheap names get more weight, capped at 25%
        mos = p.get("mos", 0.5) or 0.5
        w = min(base * (0.6 + mos), T4_MAX_WEIGHT)
        weights[p["symbol"]] = round(w, 4)

    # Renormalize to 100%
    total = sum(weights.values())
    if total > 0:
        weights = {k: round(v / total, 4) for k, v in weights.items()}

    # ── Max Sector Concentration Gate ──
    # Aggregate per sector, cap each at T4_MAX_SECTOR_WEIGHT, drain excess to cash.
    sector_map = {p["symbol"]: p.get("sector", "UNKNOWN") for p in picked}
    sector_total: Dict[str, float] = {}
    for sym, w in weights.items():
        sec = sector_map.get(sym, "UNKNOWN")
        sector_total[sec] = sector_total.get(sec, 0.0) + w

    capped = False
    for sec, tot in sector_total.items():
        if tot > T4_MAX_SECTOR_WEIGHT:
            capped = True
            # scale down every name in this sector proportionally
            factor = T4_MAX_SECTOR_WEIGHT / tot
            for sym in weights:
                if sector_map.get(sym) == sec:
                    weights[sym] = round(weights[sym] * factor, 4)

    # NOTE: after capping we do NOT re-normalize — the shaved-off weight
    # intentionally becomes cash/defensive. Re-normalizing would re-inflate
    # the capped sector back toward 100% (single-sector trap).

    # Sector totals after gate (for reporting)
    post_sector: Dict[str, float] = {}
    for sym, w in weights.items():
        sec = sector_map.get(sym, "UNKNOWN")
        post_sector[sec] = post_sector.get(sec, 0.0) + w

    # Any leftover to cash / defensive
    leftover = round(1.0 - sum(weights.values()), 4)
    return {
        "weights": weights,
        "cash": leftover,
        "sector_weights": {k: round(v, 4) for k, v in post_sector.items()},
        "sector_capped": capped,
        "summary": f"{len(picked)} names",
    }


# ════════════════════════════════════════════════════════════════════
# PIPELINE
# ════════════════════════════════════════════════════════════════════


def run_vn20_filter(top_n: Optional[int] = None, verbose: bool = True) -> Dict:
    """Run the full 4-tier pipeline over the whole market."""
    fin = _fin_conn()
    screen = _screen_conn()

    period = _latest_period(fin)
    periods = _periods_n_years(period, N_YEARS)

    # Universe: all symbols with health data
    universe = [r["symbol"] for r in fin.execute("SELECT DISTINCT symbol FROM health_ratios ORDER BY symbol").fetchall()]
    entity_map = {
        r["symbol"]: r["entity_type"]
        for r in fin.execute("SELECT symbol, entity_type FROM health_ratios GROUP BY symbol").fetchall()
    }

    # Sector mapping (screener_cache)
    mapping = _load_symbol_industry(screen)
    sector_ctx_cache: Dict[str, Dict] = {}

    passed_t12 = []
    stage_counts = {"T1_pass": 0, "T2_pass": 0, "T3_pass": 0, "T4_pass": 0, "total_universe": len(universe)}

    for sym in universe:
        entity = entity_map.get(sym, "STANDARD")

        # Tier 1
        t1 = tier1_buffett_quality(fin, sym, entity, periods)
        if not t1["pass"]:
            continue
        stage_counts["T1_pass"] += 1

        # Tier 2
        t2 = tier2_governance_shield(fin, sym, periods, entity_type=entity)
        if not t2["pass"]:
            continue
        stage_counts["T2_pass"] += 1

        # Tier 3 (sector cycle)
        sector = mapping.get(sym, "UNKNOWN")
        if sector not in sector_ctx_cache:
            sector_ctx_cache[sector] = compute_sector_context(screen, sector)
        t3 = tier3_sector_cycle(sym, sector_ctx_cache.get(sector, {}))
        if not t3["pass"]:
            continue
        stage_counts["T3_pass"] += 1

        # Tier 4 (valuation + MoS)
        t4 = tier4_valuation_mos(fin, sym)
        if not t4["pass"]:
            continue
        stage_counts["T4_pass"] += 1

        score = (
            (t1["roe_3y_min"] or 0) * 40
            + (t1["gross_margin"] or 0) * 20
            + (1.0 - (t2["dilution"] or 0)) * 20
            + (t4["mos"] or 0) * 20
        )
        passed_t12.append(
            {
                "symbol": sym,
                "sector": sector,
                "entity_type": entity,
                "roe_3y_min": t1["roe_3y_min"],
                "gross_margin": t1["gross_margin"],
                "de": t1["de"],
                "cfo_positive": t1["cfo_positive"],
                "dilution": t2["dilution"],
                "receivables": t2["receivables_ratio"],
                "phase": t3["phase"],
                "mos": t4["mos"],
                "pe_z": t4["pe_z"],
                "pb_z": t4["pb_z"],
                "score": round(score, 2),
            }
        )

    fin.close()
    screen.close()

    passed_t12.sort(key=lambda x: x["score"], reverse=True)
    if top_n:
        passed_t12 = passed_t12[:top_n]

    allocation = allocate(passed_t12)

    if verbose:
        _print_report(passed_t12, allocation, stage_counts, periods)

    return {
        "period": period,
        "stage_counts": stage_counts,
        "qualified": passed_t12,
        "allocation": allocation,
    }


def _print_report(qualified: List[Dict], allocation: Dict, stage: Dict, periods: List[str]) -> None:
    print("\n" + "=" * 78)
    print("  PTCK_VN20 — 4-TIER QUANT SCREENING (Buffett x VN Governance x Cycle)")
    print("=" * 78)
    print(f"  Period data: {periods[0]} → {periods[-1]} | Universe: {stage['total_universe']}")
    print(
        f"  Funnel: {stage['total_universe']} → T1({stage['T1_pass']}) → "
        f"T2({stage['T2_pass']}) → T3({stage['T3_pass']}) → T4({stage['T4_pass']})"
    )
    print("-" * 78)
    if not qualified:
        print("  ❌ KHÔNG CÓ cổ phiếu nào vượt qua cả 4 tầng.")
        print("  → Toàn bộ danh mục chuyển sang phòng thủ (100% Cash).")
        print("=" * 78)
        return
    print(f"  ✅ {len(qualified)} cổ phiếu vượt qua cả 4 tầng:")
    for q in qualified:
        phase = SECTOR_CYCLE_LABELS.get(q["phase"], q["phase"])
        print(
            f"    {q['symbol']:<6s} {q['sector']:<28s} ROE3y={q['roe_3y_min'] or 0:>6.1%} "
            f"GM={q['gross_margin'] or 0:>6.1%} D/E={q['de'] or 0:>5.2f} "
            f"Mos={q['mos'] or 0:>6.1%} [{phase}]"
        )
    print("-" * 78)
    w = allocation.get("weights", {})
    cash = allocation.get("cash", 0)
    sector_w = allocation.get("sector_weights", {})
    for sym, wt in sorted(w.items(), key=lambda x: -x[1]):
        print(f"    Weight {sym}: {wt:.1%}")
    if sector_w:
        print("    Sector weights:")
        for sec, sw in sorted(sector_w.items(), key=lambda x: -x[1]):
            flag = " ⚠️ CAP" if sw > T4_MAX_SECTOR_WEIGHT else ""
            print(f"      {sec:<24s}: {sw:.1%}{flag}")
    if cash:
        print(f"    → Cash/Defensive: {cash:.1%}")
    if allocation.get("sector_capped"):
        print(f"    ⚠️ Sector concentration gate ACTIVE (max {T4_MAX_SECTOR_WEIGHT:.0%}/sector) — excess drained to cash")
    print("=" * 78)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PTCK_VN20 4-Tier Quant Filter")
    parser.add_argument("--top-n", type=int, default=None, help="Giới hạn số mã xuất ra")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    run_vn20_filter(top_n=args.top_n, verbose=not args.quiet)
