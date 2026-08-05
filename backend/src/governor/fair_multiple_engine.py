"""fair_multiple_engine.py — Fair Multiple Engine (Gordon Growth Model).

WHY: Time-series Z-Score answers "expensive vs own history". Cross-sectional
     answers "expensive vs peers". Neither answers the fundamental question:
     "Is the premium justified by fundamentals (ROE, growth, risk)?"

This engine computes absolute intrinsic valuation via Gordon Growth Model:
    Fair PB = (ROE - g) / (Ke - g)
    Fair PE = Fair PB / ROE

Where:
    Ke = Rf + β × ERP     (CAPM, sector-adjusted)
    g  = ROE × retention  (sustainable growth)

Outputs: fair_pe, fair_pb, margin_of_safety_pb, margin_of_safety_pe
"""

import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────────────
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

FINANCIAL_DB = DATA_DIR / "financial_facts.db"
SCREENER_DB = DATA_DIR / "screener_cache.db"

# ==============================================================================
# ADR-2026-001 — CROSS-DATABASE METADATA ROUTING
# ==============================================================================
# WHY: FairMultipleEngine cần xác định phân ngành (ICB Sector) để gán chỉ số
#   Cost of Equity (Ke) và Terminal Growth (g) chính xác cho từng mã.
#
# WARNING (DB FRAGMENTATION):
#   - Dữ liệu P/E, P/B, ROE nằm ở:   financial_facts.db (bảng valuation_scores)
#   - Dữ liệu Ngành (symbol_industry) nằm ở: screener_cache.db
#
# RULE: Mọi hàm cần tra cứu symbol_industry PHẢI dùng connection riêng trỏ về
#   screener_cache.db. KHÔNG được dùng chung connection của financial_facts.db.
#
# FUTURE: Nếu số lượng cross-DB query tăng >3, trích thành
#   backend/src/database/cross_db.py với connection-pool registry.
# ==============================================================================


def get_screener_db_connection() -> sqlite3.Connection:
    """Return a dedicated connection to screener_cache.db.

    WHY: Wrapper chuẩn hóa việc kết nối screener_cache.db để tránh lặp lại
    lỗi định tuyến CSDL (ADR-2026-001). Mọi module cần tra cứu symbol_industry
    hoặc daily_ohlcv PHẢI dùng hàm này.
    """
    return sqlite3.connect(str(SCREENER_DB))


def get_financial_db_connection() -> sqlite3.Connection:
    """Return a dedicated connection to financial_facts.db."""
    return sqlite3.connect(str(FINANCIAL_DB))


# ── ICB Supersector → Beta mapping ──────────────────────────────────
# WHY: Beta reflects systematic risk per industry. Values estimated for
#   Vietnam market (higher baseline vol than developed mkts).
#   Source: VN market 3Y regression vs VNINDEX (2023-2026 proxy).
BETA_BY_SECTOR = {
    "Ngân hàng": 1.05,
    "Bất động sản": 1.25,
    "Chứng khoán": 1.45,
    "Thép": 1.25,
    "Bán lẻ": 0.95,
    "Công nghệ": 1.00,
    "Dầu khí": 1.15,
    "Hóa chất": 1.10,
    "Điện": 0.75,
    "Thực phẩm": 0.70,
    "Xây dựng": 1.20,
    "Vận tải": 1.10,
    "Dệt may": 0.90,
    "Cao su": 0.85,
    "Khu công nghiệp": 1.15,
    "Cảng biển": 1.00,
    "Nước": 0.65,
    "Nhựa": 1.00,
    "Thủy sản": 0.95,
    "Y tế": 0.80,
}

DEFAULT_BETA = 1.10

# ── Default payout ratios by archetype ──────────────────────────────
PAYOUT_BY_ARCHETYPE = {
    "COMPOUNDER": 0.20,
    "FRANCHISE_BANK": 0.30,
    "STEADY_EARNER": 0.40,
    "REGULATED_UTILITY": 0.60,
    "RETAIL_PLATFORM": 0.20,
    "REIT_COMMERCIAL": 0.30,
    "ASSET_BANK": 0.35,
    "EXPORT_MANUFACTURER": 0.25,
    "CYCLICAL_HEAVY": 0.35,
    "REAL_ESTATE_DEVELOPER": 0.20,
    "UNKNOWN": 0.30,
}

# ── Macroeconomic assumptions (Vietnam 2026) ────────────────────────
DEFAULT_RF = 0.055  # 10Y Vietnam govt bond yield ~5.5%
DEFAULT_ERP = 0.10  # Equity risk premium ~10% (emerging market)


def _get_sector_for_symbol(symbol: str) -> str:
    """Lookup ICB supersector (icb_name3) for a symbol.

    ADR-2026-001: PHẢI dùng get_screener_db_connection() —
    symbol_industry nằm ở screener_cache.db, KHÔNG phải financial_facts.db.
    """
    try:
        conn = get_screener_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT icb_name3 FROM symbol_industry WHERE symbol = ?",
            (symbol.upper(),),
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0].strip())
        return "UNKNOWN"
    except sqlite3.Error:
        return "UNKNOWN"


def _get_beta(sector: str) -> float:
    """Get beta for sector, falling back to default."""
    return BETA_BY_SECTOR.get(sector, DEFAULT_BETA)


def _get_payout(archetype: str) -> float:
    """Get payout ratio for archetype."""
    return PAYOUT_BY_ARCHETYPE.get(archetype, 0.30)


def _get_current_price(symbol: str) -> Optional[float]:
    """Get latest close price from screener cache."""
    try:
        conn = sqlite3.connect(str(SCREENER_DB))
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol.upper(),),
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def compute_fair_multiple(
    symbol: str,
    roe: float,
    pe_current: float,
    pb_current: float,
    payoff_ratio: Optional[float] = None,
    archetype: str = "UNKNOWN",
    rf: float = DEFAULT_RF,
    erp: float = DEFAULT_ERP,
) -> Dict[str, Any]:
    """Compute fair PE/PB and margin of safety via Gordon Growth Model.

    Parameters
    ----------
    symbol : str
        Symbol to lookup sector beta.
    roe : float
        Current ROE (decimal, e.g. 0.18 for 18%).
    pe_current : float
        Current P/E ratio.
    pb_current : float
        Current P/B ratio.
    payoff_ratio : float, optional
        Dividend payout ratio (decimal). If None, inferred from archetype.
    archetype : str
        Business archetype key (from P2 classification).
    rf : float
        Risk-free rate (decimal).
    erp : float
        Equity risk premium (decimal).

    Returns
    -------
    Dict with keys:
        fair_pe, fair_pb: intrinsic fair multiples
        margin_of_safety_pct: overall margin of safety (%) — negative = expensive
        mos_pe_pct, mos_pb_pct: per-ratio margins
        ke: cost of equity used
        g: sustainable growth rate used
        beta: beta used
        sector: sector name
        status: "OK" or error message
    """
    result: Dict[str, Any] = {
        "fair_pe": None,
        "fair_pb": None,
        "margin_of_safety_pct": None,
        "mos_pe_pct": None,
        "mos_pb_pct": None,
        "ke": None,
        "g": None,
        "beta": None,
        "sector": None,
        "status": "OK",
    }

    # Guard: ROE must be positive and reasonable
    if roe is None or roe <= 0 or roe >= 1.0:
        result["status"] = f"INVALID_ROE={roe}"
        return result
    if pe_current is None or pe_current <= 0:
        result["status"] = f"INVALID_PE={pe_current}"
        return result
    if pb_current is None or pb_current <= 0:
        result["status"] = f"INVALID_PB={pb_current}"
        return result

    sector = _get_sector_for_symbol(symbol)
    beta = _get_beta(sector)
    payout = payoff_ratio if payoff_ratio is not None else _get_payout(archetype)
    retention = 1.0 - payout

    # Cost of Equity (CAPM)
    ke = rf + beta * erp

    # Sustainable growth rate
    g_raw = roe * retention
    # WHY: Trong mô hình Gordon Growth (P/B = (ROE-g)/(Ke-g)), tốc độ tăng trưởng dài hạn g
    # không thể vượt quá Chi phí vốn Ke trong dài hạn (t->inf). Với siêu cổ phiếu ROE cao (24%+),
    # g_raw có thể vượt Ke làm mẫu số (Ke - g) <= 0 gây phân kỳ. Chuẩn hoá tài chính: khống chế
    # g dài hạn <= Ke - 0.015 (hoặc tối đa 8% ngang tăng trưởng GDP danh nghĩa) để tính Fair Multiple.
    g = min(g_raw, ke - 0.015, 0.08)
    if g >= roe:
        g = roe * 0.95

    fair_pb = (roe - g) / (ke - g)

    # Fair PE from identity: P/B = P/E × ROE → P/E = P/B / ROE
    fair_pe = fair_pb / roe

    # Cap fair multiples at reasonable bounds (avoid extreme outputs)
    max_fair_pe = 25.0
    min_fair_pe = 5.0
    max_fair_pb = 8.0
    min_fair_pb = 0.3

    fair_pe = max(min_fair_pe, min(max_fair_pe, fair_pe))
    fair_pb = max(min_fair_pb, min(max_fair_pb, fair_pb))

    # Margin of Safety
    #   MoS = (Fair - Current) / Fair  → positive = undervalued
    mos_pe = (fair_pe - pe_current) / fair_pe if fair_pe > 0 else 0
    mos_pb = (fair_pb - pb_current) / fair_pb if fair_pb > 0 else 0
    # Overall MoS = average of PE-based and PB-based
    mos_overall = (mos_pe + mos_pb) / 2.0

    result.update(
        {
            "fair_pe": round(fair_pe, 2),
            "fair_pb": round(fair_pb, 2),
            "margin_of_safety_pct": round(mos_overall * 100, 1),
            "mos_pe_pct": round(mos_pe * 100, 1),
            "mos_pb_pct": round(mos_pb * 100, 1),
            "ke": round(ke, 4),
            "g": round(g, 4),
            "beta": round(beta, 2),
            "sector": sector,
        }
    )
    return result


# ── Standalone CLI ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fair Multiple Engine CLI")
    parser.add_argument("symbols", nargs="+", help="Symbols to evaluate")
    args = parser.parse_args()

    for sym in args.symbols:
        # Quick test with hardcoded values — production call via Governor
        print(f"\n{sym}:")
        print("  Sector beta lookup only (integrated via Governor)")
