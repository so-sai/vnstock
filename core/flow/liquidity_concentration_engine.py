"""
Liquidity Concentration Index (LCI) Engine v1.0
Phát hiện "breadth illusion" — thanh khoản bị co cụm vào vài mã đầu.
"""
import sys
import logging
from pathlib import Path
from typing import Optional

def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent
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

import pandas as pd
import numpy as np
from src.database.db_core import get_connection

logger = logging.getLogger(__name__)

# ───────────────────────────────
#  Quality labels for UI
# ───────────────────────────────

LIQUIDITY_CONCENTRATION_LABELS = {
    "LAN_TOA_THAT": {
        "label_vi": "Thanh khoản lan tỏa thực",
        "color": "green",
        "severity": 0.15,
    },
    "CO_CUM_VUA": {
        "label_vi": "Thanh khoản co cụm nhẹ",
        "color": "yellow",
        "severity": 0.45,
    },
    "CO_CUM_MANH": {
        "label_vi": "Thanh khoản co cụm mạnh",
        "color": "orange",
        "severity": 0.70,
    },
    "CO_CUM_CUC_DOAN": {
        "label_vi": "Thanh khoản co cụm cực đoan",
        "color": "red",
        "severity": 0.90,
    },
}

BREADTH_QUALITY_LABELS = {
    "LAN_TOA": "Độ rộng lan tỏa thực",
    "CO_CUM_THANH_KHOAN": "Thanh khoản tập trung vào nhóm dẫn dắt",
    "MAT_DAN_DAT": "Thị trường mất dẫn dắt",
}

# ───────────────────────────────
#  Metric 1: Top-N Volume Ratio
# ───────────────────────────────

def compute_top_n_volume_ratio(df: pd.DataFrame, n: int = 10) -> float:
    total_vol = df["volume"].sum()
    if total_vol == 0:
        return 0.0
    top_n_vol = df.nlargest(n, "volume")["volume"].sum()
    return round(top_n_vol / total_vol, 4)


# ───────────────────────────────
#  Metric 2: Weighted vs Median Return
# ───────────────────────────────

def compute_weighted_vs_median_return(df: pd.DataFrame) -> dict:
    if df.empty or "change_pct" not in df.columns:
        return {"weighted_return": 0.0, "median_return": 0.0, "divergence": 0.0}
    total_value = (df["close"] * df["volume"] * 1000).sum()
    if total_value == 0:
        return {"weighted_return": 0.0, "median_return": 0.0, "divergence": 0.0}
    weights = (df["close"] * df["volume"] * 1000) / total_value
    weighted_return = float(np.average(df["change_pct"], weights=weights))
    median_return = float(df["change_pct"].median())
    divergence = round(weighted_return - median_return, 4)
    return {
        "weighted_return": round(weighted_return, 4),
        "median_return": round(median_return, 4),
        "divergence": divergence,
    }


# ───────────────────────────────
#  Metric 3: Sector Concentration Entropy
# ───────────────────────────────

def compute_sector_entropy(df: pd.DataFrame) -> dict:
    df_dedup = df.drop_duplicates(subset="symbol", keep="first")
    symbol_vol = df_dedup.set_index("symbol")["volume"].dropna()
    sector_map = _load_sector_map()
    sector_volume = {}
    for sym, sector in sector_map.items():
        if sym in symbol_vol.index:
            vol = float(symbol_vol[sym])
            sector_volume[sector] = sector_volume.get(sector, 0.0) + vol
    total_vol = sum(sector_volume.values())
    if total_vol <= 0:
        return {"entropy_score": 0.0, "sector_shares": {}, "dominant_sector": ""}
    shares = {s: v / total_vol for s, v in sector_volume.items()}
    entropy = -sum(p * np.log(p) for p in shares.values() if p > 0)
    n_sectors = len(shares)
    max_entropy = np.log(max(n_sectors, 1))
    normalized_entropy = round(entropy / max_entropy, 4) if max_entropy > 0 else 1.0
    dominant = max(shares, key=shares.get) if shares else ""
    return {
        "entropy_score": normalized_entropy,
        "entropy_raw": round(entropy, 4),
        "sector_shares": {s: round(p, 4) for s, p in sorted(shares.items(), key=lambda x: -x[1])},
        "dominant_sector": dominant,
        "n_sectors_active": len(shares),
    }


def _load_sector_map() -> dict:
    try:
        with get_connection() as conn:
            df = pd.read_sql("SELECT symbol, icb_name2 FROM symbol_industry", conn)
        mapping = {}
        for _, row in df.iterrows():
            sym = str(row["symbol"]).strip()
            sec = str(row["icb_name2"]).strip() if row["icb_name2"] else "Khác"
            mapping[sym] = sec
        return mapping
    except Exception as e:
        logger.error(f"Sector map load failed: {e}")
        return {}


# ───────────────────────────────
#  Synthesis: LCI Score
# ───────────────────────────────

def compute_lci(target_date: Optional[str] = None) -> dict:
    latest_date, df, prev_df = _load_daily_data(target_date)
    if df.empty:
        return _default_lci()

    top10_ratio = compute_top_n_volume_ratio(df, 10)
    top20_ratio = compute_top_n_volume_ratio(df, 20)

    today = df.copy()
    return_metrics = _compute_returns(today, prev_df)
    sector_data = compute_sector_entropy(today)

    concentration_factor = top10_ratio * 0.4 + (1 - sector_data["entropy_score"]) * 0.3
    div_val = return_metrics["divergence"]
    if div_val is None or (isinstance(div_val, float) and np.isnan(div_val)):
        divergence_factor = 0.0
    else:
        divergence_factor = min(abs(div_val) * 2, 1.0) * 0.3
    lci_score = round(min(concentration_factor + divergence_factor, 1.0), 4)
    if np.isnan(lci_score):
        lci_score = 0.0

    dominant_symbols = today.nlargest(5, "volume")["symbol"].tolist()

    quality = _classify_quality(lci_score)
    risk_interpretation = _interpret_risk(quality, dominant_symbols, sector_data)

    return {
        "date": latest_date,
        "lci_score": lci_score,
        "market_breadth_quality": quality,
        "top10_volume_ratio": top10_ratio,
        "top20_volume_ratio": top20_ratio,
        "entropy_score": sector_data["entropy_score"],
        "entropy_raw": sector_data.get("entropy_raw", 0),
        "dominant_sector": sector_data.get("dominant_sector", ""),
        "n_sectors_active": sector_data.get("n_sectors_active", 0),
        "dominant_symbols": dominant_symbols,
        "return_divergence": return_metrics["divergence"],
        "weighted_return": return_metrics["weighted_return"],
        "median_return": return_metrics["median_return"],
        "risk_interpretation": risk_interpretation,
        "sector_shares": sector_data.get("sector_shares", {}),
    }


def get_lci_dashboard(target_date: Optional[str] = None) -> dict:
    lci = compute_lci(target_date)
    score = lci.get("lci_score", 0)
    quality = lci.get("market_breadth_quality", "LAN_TOA_THAT")
    label = LIQUIDITY_CONCENTRATION_LABELS.get(quality, {})
    return {
        **lci,
        "display_label": label.get("label_vi", ""),
        "display_color": label.get("color", "green"),
        "display_severity": label.get("severity", 0.0),
    }


# ───────────────────────────────
#  Internal helpers
# ───────────────────────────────

def _load_daily_data(target_date: Optional[str] = None) -> tuple:
    try:
        with get_connection() as conn:
            if target_date:
                df = pd.read_sql(
                    "SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date = ? AND symbol NOT LIKE '%INDEX%'",
                    conn, params=(target_date,)
                )
                prev = pd.read_sql(
                    "SELECT symbol, date, close FROM daily_ohlcv WHERE date < ? AND symbol NOT LIKE '%INDEX%' ORDER BY date DESC LIMIT 1",
                    conn, params=(target_date,)
                )
                return target_date, df, prev
            latest = pd.read_sql("SELECT MAX(date) as md FROM daily_ohlcv", conn)
            latest_date = latest["md"].iloc[0] if not latest.empty else ""
            if not latest_date:
                return "", pd.DataFrame(), pd.DataFrame()
            df = pd.read_sql(
                "SELECT symbol, date, close, volume FROM daily_ohlcv WHERE date = ? AND symbol NOT LIKE '%INDEX%'",
                conn, params=(latest_date,)
            )
            prev = pd.read_sql(
                "SELECT symbol, date, close FROM daily_ohlcv WHERE date < ? AND symbol NOT LIKE '%INDEX%' ORDER BY date DESC LIMIT 1000",
                conn, params=(latest_date,)
            )
            return latest_date, df, prev
    except Exception as e:
        logger.error(f"LCI data load failed: {e}")
        return "", pd.DataFrame(), pd.DataFrame()


def _compute_returns(today: pd.DataFrame, prev: pd.DataFrame) -> dict:
    if prev.empty:
        return {"weighted_return": 0.0, "median_return": 0.0, "divergence": 0.0}
    prev_latest = prev.groupby("symbol")["close"].last().reset_index()
    prev_latest.columns = ["symbol", "prev_close"]
    merged = today.merge(prev_latest, on="symbol", how="left")
    merged.loc[:, "change_pct"] = (merged["close"] / merged["prev_close"].replace(0, np.nan) - 1).fillna(0)
    merged.loc[:, "change_pct"] = merged["change_pct"].replace([np.inf, -np.inf], 0.0)
    total_value = (merged["close"] * merged["volume"] * 1000).sum()
    if total_value == 0:
        return {"weighted_return": 0.0, "median_return": 0.0, "divergence": 0.0}
    weights = (merged["close"] * merged["volume"] * 1000).fillna(0)
    wsum = weights.sum()
    if wsum == 0:
        return {"weighted_return": 0.0, "median_return": 0.0, "divergence": 0.0}
    weights = weights / wsum
    weighted_return = float(np.average(merged["change_pct"], weights=weights))
    median_return = float(merged["change_pct"].median())
    divergence = round(weighted_return - median_return, 4)
    return {
        "weighted_return": round(weighted_return, 4),
        "median_return": round(median_return, 4),
        "divergence": divergence,
    }


def _classify_quality(score: float) -> str:
    if score < 0.35:
        return "LAN_TOA_THAT"
    elif score < 0.55:
        return "CO_CUM_VUA"
    elif score < 0.75:
        return "CO_CUM_MANH"
    return "CO_CUM_CUC_DOAN"


def _interpret_risk(quality: str, symbols: list, sector_data: dict) -> str:
    interpretations = {
        "LAN_TOA_THAT": "Thanh khoản phân bố đều trên thị trường. Độ lan tỏa thực.",
        "CO_CUM_VUA": "Thanh khoản có dấu hiệu tập trung nhẹ ở nhóm dẫn dắt.",
        "CO_CUM_MANH": f"Thanh khoản tập trung cao vào nhóm dẫn dắt: {', '.join(symbols[:3])}.",
        "CO_CUM_CUC_DOAN": f"Thanh khoản co cụm cực đoan — {', '.join(symbols[:3])} chiếm phần lớn giá trị giao dịch.",
    }
    return interpretations.get(quality, "")


def _default_lci() -> dict:
    return {
        "date": "",
        "lci_score": 0.0,
        "market_breadth_quality": "LAN_TOA_THAT",
        "top10_volume_ratio": 0.0,
        "top20_volume_ratio": 0.0,
        "entropy_score": 0.0,
        "entropy_raw": 0.0,
        "dominant_sector": "",
        "n_sectors_active": 0,
        "dominant_symbols": [],
        "return_divergence": 0.0,
        "weighted_return": 0.0,
        "median_return": 0.0,
        "risk_interpretation": "Không đủ dữ liệu",
        "sector_shares": {},
    }


if __name__ == "__main__":
    import json
    lci = compute_lci()
    print(json.dumps(lci, ensure_ascii=False, indent=2, default=str))
