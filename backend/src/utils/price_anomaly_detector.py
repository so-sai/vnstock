import sys, os, json, logging
from pathlib import Path
from datetime import datetime

def _hydrate_path():
    if getattr(sys, 'frozen', False):
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
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path
PROJECT_ROOT = _hydrate_path()

import pandas as pd
from src.database.db_core import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ANOMALY_DETECTOR")

VN30_SYMBOLS = {
    'VCB', 'CTG', 'BID', 'HPG', 'FPT', 'MSN', 'VNM', 'VIC', 'VRE', 'VHM',
    'SSI', 'MWG', 'ACB', 'VPB', 'MBB', 'TCB', 'TPB', 'HDB', 'STB', 'EIB',
    'SHB', 'GAS', 'POW', 'PLX', 'SAB', 'BVH', 'VJC', 'PNJ', 'KDH', 'NVL',
}

THRESHOLDS = {
    "daily_gap_pct": 15.0,
    "unit_multiplier_check": True,
    "duplicate_date_check": True,
}

def check_daily_gap(df: pd.DataFrame, symbol: str) -> list:
    anomalies = []
    if len(df) < 2:
        return anomalies
    df = df.sort_values('date').reset_index(drop=True)
    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        p_close = prev.get('adj_close') or prev.get('close')
        c_close = curr.get('adj_close') or curr.get('close')
        if p_close and c_close and p_close > 0:
            pct = abs((c_close - p_close) / p_close) * 100
            if pct > THRESHOLDS["daily_gap_pct"]:
                anomalies.append({
                    "symbol": symbol,
                    "date": str(curr['date']),
                    "type": "PRICE_GAP",
                    "prev_close": float(p_close),
                    "curr_close": float(c_close),
                    "gap_pct": round(pct, 2),
                    "severity": "CRITICAL" if pct > 25 else "WARNING",
                })
    return anomalies

def check_unit_multiplier(db_symbols: list) -> list:
    anomalies = []
    for symbol in db_symbols[:200]:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT date, close FROM daily_ohlcv WHERE symbol=? ORDER BY date DESC LIMIT 3",
                conn, params=(symbol,)
            )
        if len(df) < 2:
            continue
        vals = df['close'].dropna().values
        if len(vals) < 2:
            continue
        max_v, min_v = max(vals), min(vals)
        if min_v > 0 and max_v / min_v > 500:
            anomalies.append({
                "symbol": symbol,
                "type": "UNIT_MISMATCH",
                "max_close": float(max_v),
                "min_close": float(min_v),
                "ratio": round(max_v / min_v, 1),
                "dates": list(df['date'].values),
                "severity": "CRITICAL",
            })
    return anomalies

def check_duplicate_dates(db_symbols: list) -> list:
    anomalies = []
    with get_connection() as conn:
        for symbol in db_symbols:
            df = pd.read_sql(
                "SELECT date, COUNT(*) as cnt FROM daily_ohlcv WHERE symbol=? GROUP BY date HAVING cnt > 1",
                conn, params=(symbol,)
            )
            if not df.empty:
                anomalies.append({
                    "symbol": symbol,
                    "type": "DUPLICATE_DATES",
                    "duplicates": [{"date": str(r['date']), "count": int(r['cnt'])} for _, r in df.iterrows()],
                    "severity": "WARNING",
                })
    return anomalies

def run_anomaly_scan(limit_symbols: int = None) -> dict:
    results = {"scan_time": datetime.now().isoformat(), "total_anomalies": 0, "anomalies": []}

    with get_connection() as conn:
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX', 'VN30')"
        ).fetchall()]

    if limit_symbols:
        symbols = symbols[:limit_symbols]

    logger.info(f"Quét {len(symbols)} symbols...")

    # 1. Daily gap on VN30
    vn30 = [s for s in symbols if s in VN30_SYMBOLS]
    for sym in vn30:
        with get_connection() as conn:
            df = pd.read_sql(
                "SELECT date, close, adj_close FROM daily_ohlcv WHERE symbol=? ORDER BY date",
                conn, params=(sym,)
            )
        if not df.empty:
            results["anomalies"].extend(check_daily_gap(df, sym))

    # 2. Unit mismatch scan (top 200)
    results["anomalies"].extend(check_unit_multiplier(symbols))

    # 3. Duplicate dates
    results["anomalies"].extend(check_duplicate_dates(vn30))

    results["total_anomalies"] = len(results["anomalies"])

    logger.info(f"Tìm thấy {results['total_anomalies']} dị thường.")

    report_path = PROJECT_ROOT / "backend" / "data" / "anomaly_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"Báo cáo lưu tại: {report_path}")

    return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Price Anomaly Detector")
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn số lượng mã quét")
    args = parser.parse_args()

    results = run_anomaly_scan(args.limit)
    if results["total_anomalies"] > 0:
        for a in results["anomalies"]:
            print(f"  [{a.get('severity','INFO')}] {a['symbol']}: {a['type']}")
            if 'gap_pct' in a:
                print(f"    Gap: {a['gap_pct']}% (prev={a['prev_close']} -> curr={a['curr_close']})")
    else:
        print("Không phát hiện dị thường. Kho dữ liệu sạch.")
