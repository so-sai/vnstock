"""vn20_quant_filter.py — 4-Tier Dynamic Quant Screening (PTCK_VN20).

Buffett Quality x VN Governance x Sector Cycle x Valuation/Allocation.

Tier 1 — BUFFETT QUALITY (static, long-term moat):
  DUAL-BRANCH:
    STANDARD (sản xuất/thương mại): ROE >= 15% (3y), CFO > 0 (3y),
      D/E <= 1.0, Gross Margin >= 25%.
    BANK (ngân hàng — SBV-based, không dùng CFO/GM):
      ROE >= 15% (3y), NPL <= 2.5%, NIM >= 1.8%, CAR >= 5.0%,
      D/E <= 8.0 (nếu có).

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

import logging
import sqlite3
import sys
from pathlib import Path


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

logger = logging.getLogger(__name__)

# ── Tunable thresholds (Buffer-tuned for VN emerging-market volatility) ──
T1_ROE_MIN = 0.15  # 15%
T1_DE_MAX_NONBANK = 1.0
T1_DE_MAX_BANK = 8.0
T1_GROSS_MARGIN_MIN = 0.25  # 25%
T1_GROSS_MARGIN_MIN_STEEL = 0.15  # 15% — ngoại lệ ngành Thép (commodity, biên gộp thấp bẩm sinh)
# ── Bank-specific T1 gate (BCTC ngân hàng không có CFO/GM/D/E theo chuẩn SBV) ──
T1_BANK_NPL_MAX = 0.025  # NPL <= 2.5% (chuẩn SBV)
T1_BANK_NIM_MIN = 0.018  # NIM >= 1.8%
T1_BANK_CAR_MIN = 0.05  # Capital adequacy >= 5.0%
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


# ── Dynamic MoS (Biên An Toàn Động) 2026-08-07 ─────────────────────────
# WHY: Ngưỡng MoS tĩnh 25% gây Type II Error (bỏ lỡ) cho cổ phiếu trụ cột
# chất lượng cao trong giai đoạn tích lũy. MoS min giờ là hàm của Regime
# thị trường (rủi ro vĩ mô) và Chất lượng doanh nghiệp (ROE annual).
#   MoS_base:  CRISIS/BEARISH→25%, RANGING/RECOVERY→20%, EXPANSION/BULL→15%
#   Δ_quality: ROE_annual >= 20% → được ưu đãi 5% (hạ ngưỡng)
#   Sàn tuyệt đối: 10% (không bao giờ nhận định giá quá đắt).
MOS_BASE_CRISIS = 0.25
MOS_BASE_RANGING = 0.20
MOS_BASE_EXPANSION = 0.15
MOS_QUALITY_DISCOUNT = 0.05
MOS_ELITE_ROE_MIN = 0.20
MOS_FLOOR = 0.10


def get_dynamic_mos_threshold(regime: str, roe_annual: float | None) -> float:
    """Tính ngưỡng Biên An Toàn động theo Regime thị trường + ROE annual.

    MoS_min = MoS_base(Regime) - Δ_quality(ROE >= 20% ? 5% : 0%), sàn 10%.
    Pure function — không đụng DB, dễ test TDD.
    """
    regime_upper = (regime or "RANGING").upper()

    # 1. Base MoS theo Regime
    if "CRISIS" in regime_upper or "BEAR" in regime_upper:
        base_mos = MOS_BASE_CRISIS
    elif "EXPANSION" in regime_upper or "BULL" in regime_upper:
        base_mos = MOS_BASE_EXPANSION
    else:  # RANGING / RECOVERY / DEFAULT
        base_mos = MOS_BASE_RANGING

    # 2. Ưu đãi 5% cho Siêu cổ phiếu (ROE_annual >= 20%)
    quality_discount = MOS_QUALITY_DISCOUNT if (roe_annual is not None and roe_annual >= MOS_ELITE_ROE_MIN) else 0.0

    return round(max(MOS_FLOOR, base_mos - quality_discount), 4)


def _latest_market_regime(conn) -> str:
    """Đọc regime thị trường mới nhất từ screener_cache.regime_history.

    Map status nội bộ (TRENDING/RANGING/CRISIS) → bucket Dynamic MoS:
      TRENDING (uptrend) → EXPANSION, RANGING → RANGING, CRISIS → CRISIS.
    Thiếu dữ liệu → RANGING (ngưỡng giữa, an toàn).
    """
    try:
        row = conn.execute("SELECT status FROM regime_history ORDER BY date DESC, rowid DESC LIMIT 1").fetchone()
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return "RANGING"
    if not row or not row["status"]:
        return "RANGING"
    s = str(row["status"]).upper()
    if s in ("TRENDING",):
        return "EXPANSION"
    if s in ("CRISIS", "CRISIS_WARNING"):
        return "CRISIS"
    return "RANGING"


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


def _periods_n_years(period: str, n: int = N_YEARS) -> list[str]:
    """Return list of period keys for the last n years ending at `period`."""


def _periods_n_years(period: str, n: int = N_YEARS) -> list[str]:
    """Return period keys for the last n years ending at `period`, ASCENDING.

    Oldest first (2024Q1 → 2026Q2) so `periods[-4:]` = the 4 MOST RECENT
    quarters (was descending before — a latent bug that made D/E & Gross
    Margin lookups target the oldest quarters instead of latest).
    """
    try:
        y, q = period.split("Q")
        y = int(y)
        q = int(q)
    except TypeError, ValueError, KeyError, IndexError:
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


def _load_ratio_series(conn, symbol: str, ratio: str, periods: list[str]) -> list[float]:
    ph = ",".join("?" for _ in periods)
    rows = conn.execute(
        f"SELECT period, ratio_value FROM health_ratios WHERE symbol=? AND ratio_name=? AND period IN ({ph}) ORDER BY period",
        (symbol, ratio, *periods),
    ).fetchall()
    return [float(r["ratio_value"]) for r in rows if r["ratio_value"] is not None]


def _latest_ratio(conn, symbol: str, ratio: str, periods: list[str]) -> float | None:
    """Latest (most recent) value of a health_ratio within periods, or None."""
    series = _load_ratio_series(conn, symbol, ratio, periods)
    return series[-1] if series else None


def _load_metric_years(conn, symbol: str, metric: str, periods: list[str]) -> dict[str, float]:
    """Map {year: value} for a financial_facts metric across last n years."""
    ph = ",".join("?" for _ in periods)
    rows = conn.execute(
        f"SELECT period, value FROM financial_facts WHERE symbol=? AND metric=? AND period IN ({ph}) ORDER BY period",
        (symbol, metric, *periods),
    ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        y = r["period"][:4]
        if r["value"] is not None:
            out[y] = float(r["value"])
    return out


def tier1_buffett_quality(conn, symbol: str, entity_type: str, periods: list[str], is_steel: bool = False) -> dict:
    """Tier 1: Buffett quality gate. Returns pass/fail + metrics.

    DUAL-BRANCH (T1-Bank refactor 2026-08-06):
      - BANK:   BCTC ngân hàng không có CFO/GM/D/E ý nghĩa theo chuẩn Buffett.
                Thay bằng bộ chỉ số SBV: NPL <= 2.5%, NIM >= 1.8%, CAR >= 5%.
                D/E (nếu có) dùng ngưỡng lỏng 8.0; CFO/GM bỏ qua.
      - STANDARD: giữ nguyên tiêu chuẩn Buffett: CFO > 0 (3y), GM >= 25%,
                D/E <= 1.0 (thiếu GM/D/E -> FAIL vì là doanh nghiệp thật).

    is_steel (T1-Steel exception 2026-08-06): ngành Thép là commodity, biên gộp
    thường 15-22% nên dùng ngưỡng GM >= 15% thay vì 25% (Type II Error tránh loại
    sạch thép VN khỏi T1). Không ảnh hưởng ROE/CFO/D/E.
    """
    is_bank = entity_type == "BANK"
    result = {
        "symbol": symbol,
        "entity_type": entity_type,
        "is_bank": is_bank,
        "pass": False,
        "roe": None,
        "roe_3y_min": None,
        "cfo_years": 0,
        "cfo_positive": False,
        "de": None,
        "de_max": T1_DE_MAX_BANK if is_bank else T1_DE_MAX_NONBANK,
        "gross_margin": None,
        "npl": None,
        "nim": None,
        "car": None,
        "reasons": [],
    }

    # ROE: require >= 15% for last 3 years (mọi ngành — bắt buộc).
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
        if not roe_3y or min(roe_3y) < T1_ROE_MIN:
            result["reasons"].append(
                f"ROE 3y min {result['roe_3y_min']} < 15%" if result["roe_3y_min"] is not None else "ROE data missing"
            )
    else:
        result["reasons"].append("ROE data missing")

    if is_bank:
        # ── NHÁNH BANK ──
        result["npl"] = _latest_ratio(conn, symbol, "NPL_RATIO", periods[-4:])
        result["nim"] = _latest_ratio(conn, symbol, "NIM", periods[-4:])
        result["car"] = _latest_ratio(conn, symbol, "CAPITAL_RATIO", periods[-4:])

        if result["npl"] is not None and result["npl"] > T1_BANK_NPL_MAX:
            result["reasons"].append(f"Bank NPL {result['npl']:.1%} > {T1_BANK_NPL_MAX:.1%}")
        # NOTE: health_ratios NIM là QUARTERLY (NII quarterly / loans) — annualize x4
        # trước khi so với ngưỡng 1.8%/năm, giống ROE (vn20_quant_filter.py:208).
        if result["nim"] is not None:
            result["nim"] = round(result["nim"] * 4.0, 4)
            if result["nim"] < T1_BANK_NIM_MIN:
                result["reasons"].append(f"Bank NIM {result['nim']:.1%} < {T1_BANK_NIM_MIN:.1%}")
        if result["car"] is not None and result["car"] < T1_BANK_CAR_MIN:
            result["reasons"].append(f"Bank CAR {result['car']:.1%} < {T1_BANK_CAR_MIN:.1%}")

        # Banks: D/E missing is NOT a fail — capital adequacy (CAR) replaces it.
        de_series = _load_ratio_series(conn, symbol, "DEBT_TO_EQUITY", periods[-4:])
        if de_series:
            result["de"] = round(de_series[-1], 4)
            if result["de"] > T1_DE_MAX_BANK:
                result["reasons"].append(f"D/E {result['de']} > {T1_DE_MAX_BANK}")
    else:
        # ── NHÁNH STANDARD ──
        # CFO: > 0 for 3 consecutive years
        cfo_years = _load_metric_years(conn, symbol, "CFO", periods)
        if cfo_years:
            result["cfo_years"] = len(cfo_years)
            result["cfo_positive"] = all(v > 0 for v in cfo_years.values())
            if not result["cfo_positive"]:
                result["reasons"].append("CFO không dương liên tục 3 năm")
        else:
            result["reasons"].append("CFO data missing")

        # D/E
        de_series = _load_ratio_series(conn, symbol, "DEBT_TO_EQUITY", periods[-4:])
        if de_series:
            result["de"] = round(de_series[-1], 4)
            if result["de"] > T1_DE_MAX_NONBANK:
                result["reasons"].append(f"D/E {result['de']} > {T1_DE_MAX_NONBANK}")
        else:
            result["reasons"].append("D/E data missing")

        # Gross margin (ngoại lệ ngành Thép: GM >= 15% thay vì 25% chung)
        gm_min = T1_GROSS_MARGIN_MIN_STEEL if is_steel else T1_GROSS_MARGIN_MIN
        gm_series = _load_ratio_series(conn, symbol, "GROSS_MARGIN", periods[-4:])
        if gm_series:
            result["gross_margin"] = round(gm_series[-1], 4)
            if result["gross_margin"] < gm_min:
                result["reasons"].append(f"Gross margin {result['gross_margin']} < {gm_min:.0%}")
        else:
            result["reasons"].append("Gross margin data missing")

    result["pass"] = len(result["reasons"]) == 0
    return result


# ════════════════════════════════════════════════════════════════════
# TIER 2 — GOVERNOR SHIELD (VN governance)
# ════════════════════════════════════════════════════════════════════


def tier2_governance_shield(
    conn, symbol: str, periods: list[str], entity_type: str = "STANDARD", sector_pct75: float | None = None
) -> dict:
    """Tier 2: dilution rate + receivables health.

    Banks: receivables gate is skipped (banks don't have trade receivables;
    governance risk is captured by NPL/capital adequacy in Tier 1/health).

    Receivables logic (Industry-Relative Percentile Gate):
      - Pass if ratio <= T2_RECEIVABLES_MAX (25%) — safe harbor for low-receivables sectors
      - Pass if ratio <= sector_pct75 — company is in top 75% of its industry
      - Fail if ratio exceeds both thresholds — excessive receivables vs peers
      WHY: Static 25% cap causes Type II Error against B2B/IT companies (FPT 67.2%)
      where high receivables are structural, not governance risk.
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
    q_map: dict[str, float] = {}
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
                # Industry-Relative Percentile Gate (dual-condition):
                #   1. Safe harbor: ratio <= 25% → always pass (low-receivables sectors)
                #   2. Sector relative: ratio <= sector_pct75 → pass (top 75% of industry)
                #   3. Fail if exceeds both thresholds
                ratio = result["receivables_ratio"]
                if ratio <= T2_RECEIVABLES_MAX:
                    pass  # safe harbor
                elif sector_pct75 is not None and ratio <= sector_pct75:
                    pass  # within industry norm
                else:
                    p75_str = f"{sector_pct75:.0%}" if sector_pct75 is not None else "N/A"
                    result["reasons"].append(f"Receivables {ratio:.1%} > {p75_str} sector P75")
        else:
            result["reasons"].append("Receivables data missing")

    result["pass"] = len(result["reasons"]) == 0
    return result


# ════════════════════════════════════════════════════════════════════
# TIER 3 — SECTOR CYCLE DETECTOR
# ════════════════════════════════════════════════════════════════════


def tier3_sector_cycle(symbol: str, sector_ctx: dict) -> dict:
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


def compute_sector_context(conn, sector: str, lookback: int = 90) -> dict:
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

    df.loc[:, "ret"] = df.groupby("symbol")["close"].pct_change(fill_method=None)
    daily = df.groupby("date")["ret"].mean().reset_index().sort_values("date")
    # Clean inf/NaN (broken prices produce inf pct_change → poisons cumprod)
    # WHY: .loc[:, "ret"] thay vì daily["ret"]=... — chained assignment kích hoạt
    # pandas FutureWarning (copy-vs-view); đây là lệnh gán đơn trên cột độc lập.
    daily.loc[:, "ret"] = daily["ret"].replace([float("inf"), float("-inf")], pd.NA)
    daily = daily.dropna(subset=["ret"])
    daily.loc[:, "cum"] = (1 + daily["ret"].fillna(0.0)).cumprod()
    daily.loc[:, "ma5"] = daily["cum"].rolling(5).mean()
    daily.loc[:, "ma20"] = daily["cum"].rolling(20).mean()
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
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError) as _e:
        logger.debug("Không tính được valuation_pct từ valuation_scores (bỏ qua): %s", _e)

    return {"sector": sector, "momentum": momentum, "valuation_pct": val_pct}


def _load_symbol_industry(conn) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT symbol, icb_name3 FROM symbol_industry").fetchall()
        return {r["symbol"]: r["icb_name3"] for r in rows}
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return {}


def _load_steel_symbols(conn) -> set:
    """Set các symbol thuộc ngành Thép theo ICB (icb_name4 'Thép và sản phẩm thép').

    WHY: Ngành thép VN (HPG/HSG/NKG/TLH...) là commodity có biên gộp thường
    15-22%, dưới ngưỡng Buffett 25%. T1 cần ngoại lệ GM >= 15% cho nhóm này —
    xác định chính xác qua ICB cấp 4 thay vì đoán bằng tên mã."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM symbol_industry WHERE icb_name4 LIKE '%Thép%' OR icb_name4 LIKE '%thép%'"
        ).fetchall()
        return {r["symbol"] for r in rows}
    except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
        return set()


def _compute_sector_receivables_p75(fin_conn, screen_conn, universe: list[str], periods: list[str]) -> dict[str, float | None]:
    """Compute 75th percentile of RECEIVABLES/REVENUE per sector.

    Two-layer scan:
      Layer 1: Scan ALL stocks from symbol_industry (full market, ~1555 symbols)
        to maximize sector coverage. Only stocks with RECEIVABLES+REVENUE data
        contribute to the ratio distribution.
      Layer 2: For sectors with < 3 stocks having data, use max ratio (P100)
        as fallback threshold. Solo stocks always pass (ratio ≤ their own max).

    Returns {sector_name: p75_ratio_or_p100_fallback}.
    WHY: Industry-Relative Percentile Gate replaces static 25% cap to avoid
    Type II Error against B2B/IT companies where high receivables are structural.
    Full-market scan ensures sectors like IT (FPT) get maximum peer context.
    """
    # Layer 1: Load ALL symbols from symbol_industry (full market)
    all_mapping = _load_symbol_industry(screen_conn)
    all_symbols = list(all_mapping.keys())

    # Compute RECEIVABLES/REVENUE ratio for every symbol with data
    sector_ratios: dict[str, list[float]] = {}
    for sym in all_symbols:
        sector = all_mapping.get(sym, "UNKNOWN")
        rec = _load_metric_years(fin_conn, sym, "RECEIVABLES", periods[-4:])
        rev = _load_metric_years(fin_conn, sym, "REVENUE", periods[-4:])
        if rec and rev:
            rec_last = max(rec.values())
            rev_last = max(rev.values())
            if rev_last and rev_last > 0:
                ratio = rec_last / rev_last
                sector_ratios.setdefault(sector, []).append(ratio)

    # Layer 2: Compute P75 per sector, with P100 fallback for small sectors
    result: dict[str, float | None] = {}
    for sector, ratios in sector_ratios.items():
        if len(ratios) >= 3:
            sorted_r = sorted(ratios)
            idx = int(len(sorted_r) * 0.75)
            result[sector] = round(sorted_r[min(idx, len(sorted_r) - 1)], 4)
        else:
            # WHY: Sectors with < 3 symbols use max ratio (P100) as threshold.
            # Solo stocks always pass (their ratio ≤ their own max). This avoids
            # Type II Error where a single B2B/IT stock (e.g. FPT) fails T2 simply
            # because no peers exist in the universe for industry-relative comparison.
            result[sector] = round(max(ratios), 4) if ratios else None
    return result


# ════════════════════════════════════════════════════════════════════
# TIER 4 — VALUATION & ALLOCATION
# ════════════════════════════════════════════════════════════════════


def tier4_valuation_mos(conn, symbol: str, regime: str = "RANGING", roe_annual: float | None = None) -> dict:
    """Tier 4: Margin of Safety from valuation z-scores.

    MoS = max over PE/PB z-score of (1 - z_inverse), floored 0..1.
    Ngưỡng tối thiểu ĐỘNG theo Regime + ROE (Dynamic MoS 2026-08-07):
      CRISIS→25%, RANGING→20%, EXPANSION→15%; ROE>=20% → ưu đãi 5%;
      sàn tuyệt đối 10%.
    """
    result = {
        "symbol": symbol,
        "pass": False,
        "mos": None,
        "mos_threshold": get_dynamic_mos_threshold(regime, roe_annual),
        "mos_regime": (regime or "RANGING").upper(),
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
        th = result["mos_threshold"]
        result["pass"] = mos >= th
        if not result["pass"]:
            result["reasons"].append(f"MoS {mos:.1%} < {th:.1%}")
    else:
        result["reasons"].append("Valuation data missing")

    return result


def allocate(qualified: list[dict]) -> dict:
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
    sector_total: dict[str, float] = {}
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
    post_sector: dict[str, float] = {}
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


def run_vn20_filter(top_n: int | None = None, verbose: bool = True) -> dict:
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
    steel_symbols = _load_steel_symbols(screen)
    sector_ctx_cache: dict[str, dict] = {}

    # Industry-Relative Percentile: pre-compute sector P75 for receivables gate
    sector_pct75 = _compute_sector_receivables_p75(fin, screen, universe, periods)

    # Market regime cho Dynamic MoS (đọc 1 lần, dùng cho cả pipeline)
    market_regime = _latest_market_regime(screen)

    passed_t12 = []
    stage_counts = {"T1_pass": 0, "T2_pass": 0, "T3_pass": 0, "T4_pass": 0, "total_universe": len(universe)}

    for sym in universe:
        entity = entity_map.get(sym, "STANDARD")

        # Tier 1
        t1 = tier1_buffett_quality(fin, sym, entity, periods, is_steel=sym in steel_symbols)
        if not t1["pass"]:
            continue
        stage_counts["T1_pass"] += 1

        # Tier 2 (with industry-relative receivables percentile)
        sector = mapping.get(sym, "UNKNOWN")
        t2 = tier2_governance_shield(fin, sym, periods, entity_type=entity, sector_pct75=sector_pct75.get(sector))
        if not t2["pass"]:
            continue
        stage_counts["T2_pass"] += 1

        # Tier 3 (sector cycle)
        if sector not in sector_ctx_cache:
            sector_ctx_cache[sector] = compute_sector_context(screen, sector)
        t3 = tier3_sector_cycle(sym, sector_ctx_cache.get(sector, {}))
        if not t3["pass"]:
            continue
        stage_counts["T3_pass"] += 1

        # Tier 4 (valuation + MoS động)
        t4 = tier4_valuation_mos(fin, sym, market_regime, t1["roe"])
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
                "mos_threshold": t4["mos_threshold"],
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


def _print_report(qualified: list[dict], allocation: dict, stage: dict, periods: list[str]) -> None:
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
