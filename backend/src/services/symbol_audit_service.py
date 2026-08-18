"""symbol-audit 4-gate deep-dive — hội tụ 4 cổng từ DB sống.

WHY: Tổng hợp Bảng Giải phẫu cho 1 mã — (1) Hàng hóa/Ngành, (2) Chất lượng &
Định giá VN20, (3) Dòng tiền & Volume Profile, (4) Kỹ thuật & Động lượng — 100%
đọc từ financial_facts.db + screener_cache.db, không cần thao tác thủ công.

Zero-Hallucination: mọi nguồn thiếu → NO_DATA (None/[]), không bịa số.
Mọi read đều PIT-safe (target_date <= hiện tại).
"""

import sqlite3
from datetime import date

import pandas as pd

from src.services.macro.commodity_service import get_steel_crack_spread

# ratio_name được phép đưa vào Cổng 2 (nguồn: DISTINCT valuation_scores).
VALUATION_RATIOS = ("PE", "PB", "PS", "EV_EBITDA", "ROE", "ROA")

# health ratios ưu tiên cho Cổng 2 (nguồn: DISTINCT health_ratios).
HEALTH_RATIOS = ("ROE", "CFO_TO_NET_INCOME", "DEBT_TO_EQUITY", "CAPITAL_RATIO")


def _latest_valuation(fin: sqlite3.Connection, symbol: str) -> list[dict]:
    """Valuation mới nhất cho symbol (z_score/percentile/zone) — chỉ 1 kỳ gần nhất."""
    latest = fin.execute("SELECT MAX(period) FROM valuation_scores WHERE symbol = ?", (symbol,)).fetchone()
    if latest is None or latest[0] is None:
        return []
    rows = fin.execute(
        """
        SELECT period, ratio_name, ratio_value, z_score, percentile, zone
        FROM valuation_scores
        WHERE symbol = ? AND period = ? AND ratio_name IN ({})
        ORDER BY ratio_name
        """.format(",".join("?" * len(VALUATION_RATIOS))),
        (symbol, latest[0], *VALUATION_RATIOS),
    ).fetchall()
    return [
        {
            "period": r[0],
            "ratio_name": r[1],
            "ratio_value": r[2],
            "z_score": r[3],
            "percentile": r[4],
            "zone": r[5],
        }
        for r in rows
    ]


def _latest_health(fin: sqlite3.Connection, symbol: str) -> list[dict]:
    """Health ratios mới nhất cho symbol (ROE, CFO/NI, DEBT/EQ, ...) — chỉ 1 kỳ."""
    latest = fin.execute("SELECT MAX(period) FROM health_ratios WHERE symbol = ?", (symbol,)).fetchone()
    if latest is None or latest[0] is None:
        return []
    rows = fin.execute(
        """
        SELECT period, ratio_name, ratio_value
        FROM health_ratios
        WHERE symbol = ? AND period = ? AND ratio_name IN ({})
        ORDER BY ratio_name
        """.format(",".join("?" * len(HEALTH_RATIOS))),
        (symbol, latest[0], *HEALTH_RATIOS),
    ).fetchall()
    return [{"period": r[0], "ratio_name": r[1], "ratio_value": r[2]} for r in rows]


def _latest_cfo(fin: sqlite3.Connection, symbol: str) -> dict | None:
    """CFO mới nhất từ financial_facts."""
    row = fin.execute(
        """
        SELECT period, value FROM financial_facts
        WHERE symbol = ? AND metric = 'CFO'
        ORDER BY period DESC LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return {"period": row[0], "value": row[1]}


def _latest_volume_profile(fin: sqlite3.Connection, symbol: str) -> dict | None:
    """Volume profile mới nhất (POC/VAL/VAH/volume_ratio/MA)."""
    row = fin.execute(
        """
        SELECT date, price_current, poc, vah, val, volume_ratio, price_ma20, price_ma50
        FROM volume_profile
        WHERE symbol = ?
        ORDER BY date DESC LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    return {
        "date": row[0],
        "price": row[1],
        "poc": row[2],
        "vah": row[3],
        "val": row[4],
        "volume_ratio": row[5],
        "ma20": row[6],
        "ma50": row[7],
    }


def _technical_from_ohlcv(scr: sqlite3.Connection, symbol: str) -> dict:
    """Kỹ thuật tính từ daily_ohlcv (close/MA20/MA50/RSI14/momentum).

    RSI14 dùng Wilder smoothing theo chuẩn repo (xray_service).
    Zero-Hallucination: < 15 phiên → NO_DATA (không đủ để tính MA50).
    """
    rows = scr.execute(
        """
        SELECT date, close FROM daily_ohlcv
        WHERE symbol = ?
        ORDER BY date ASC
        """,
        (symbol,),
    ).fetchall()
    if len(rows) < 15:
        return {"close": None, "ma20": None, "ma50": None, "rsi14": None, "mom_20d": None, "mom_1y": None}

    df = pd.DataFrame(rows, columns=["date", "close"])
    close_series = df["close"].astype(float)

    close = float(close_series.iloc[-1])
    ma20 = float(close_series.rolling(20).mean().iloc[-1]) if len(close_series) >= 20 else None
    ma50 = float(close_series.rolling(50).mean().iloc[-1]) if len(close_series) >= 50 else None

    # RSI14 (Wilder)
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14).mean().iloc[-1]
    rsi14 = None
    if avg_loss == 0:
        rsi14 = 100.0
    else:
        rsi14 = float(100 - 100 / (1 + avg_gain / avg_loss))

    mom_20d = None
    if len(close_series) >= 21:
        mom_20d = float((close_series.iloc[-1] / close_series.iloc[-21] - 1) * 100)
    mom_1y = None
    if len(close_series) >= 252:
        mom_1y = float((close_series.iloc[-1] / close_series.iloc[-252] - 1) * 100)

    return {"close": close, "ma20": ma20, "ma50": ma50, "rsi14": rsi14, "mom_20d": mom_20d, "mom_1y": mom_1y}


def _sector_info(scr: sqlite3.Connection, symbol: str) -> dict:
    """Ngành của symbol (icb_name2/3) từ symbol_industry."""
    row = scr.execute(
        "SELECT icb_name2, icb_name3 FROM symbol_industry WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if row is None:
        return {"icb_name2": None, "icb_name3": None}
    return {"icb_name2": row[0], "icb_name3": row[1]}


def _crack_spread(scr: sqlite3.Connection, icb2: str | None, icb3: str | None) -> dict | None:
    """Crack spread thép — chỉ hiển thị cho mã ngành kim loại/thép.

    WHY: crack spread là chỉ số hàng hóa toàn thị trường; hiển thị nó cho VCB (ngân
    hàng) là nhiễu. Chỉ bật khi symbol thuộc ngành thép/kim loại (fail-closed nếu
    thiếu 1 trong 3 biến macro).
    """
    _STEEL_KEYWORDS = ("thép", "steel", "kim loại", "metal")
    sector_txt = " ".join(t for t in (icb2 or "", icb3 or "")).lower()
    if not any(k in sector_txt for k in _STEEL_KEYWORDS):
        return None
    res = get_steel_crack_spread(scr, is_live=False)
    if res is None:
        return None
    return {
        "as_of_date": res.as_of_date,
        "crack_spread": res.crack_spread,
        "hrc_price": res.hrc_price,
        "iron_ore_price": res.iron_ore_price,
        "coking_coal_price": res.coking_coal_price,
    }


def build_symbol_audit(
    symbol: str,
    fin: sqlite3.Connection,
    scr: sqlite3.Connection,
) -> dict:
    """Xây dựng bảng giải phẫu 4 cổng cho 1 symbol.

    Args:
        symbol: Mã cổ phiếu (VD HPG).
        fin: Connection tới financial_facts.db.
        scr: Connection tới screener_cache.db.
    """
    symbol = symbol.upper()

    sector = _sector_info(scr, symbol)
    valuation = _latest_valuation(fin, symbol)
    health = _latest_health(fin, symbol)
    vp = _latest_volume_profile(fin, symbol)
    cfo = _latest_cfo(fin, symbol)
    tech = _technical_from_ohlcv(scr, symbol)

    return {
        "symbol": symbol,
        "as_of_date": date.today().isoformat(),
        "gate1": {
            "icb_name2": sector["icb_name2"],
            "icb_name3": sector["icb_name3"],
            # Crack spread chỉ hiển thị cho ngành thép/kim loại, fail-closed khi thiếu.
            "crack_spread": _crack_spread(scr, sector["icb_name2"], sector["icb_name3"]),
        },
        "gate2": {
            "valuation": valuation,
            "health": health,
        },
        "gate3": {
            "volume_profile": vp,
            "cfo": cfo,
        },
        "gate4": tech,
    }
