"""
Data Baking Forge (Phase 12.5) — Backfill historical OHLCV for watchlist stocks.
Ingests 2023→2025 data via KBS API, normalizes, and bakes into daily_ohlcv.
"""
import sys, time, random, logging
from pathlib import Path

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
    libs_dir = root_path / "backend" / "libs" / "vnstock"
    if libs_dir.is_dir() and str(libs_dir) not in sys.path:
        sys.path.append(str(libs_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd
from datetime import datetime
from vnstock import Quote
from src.database.db_core import get_connection, save_data_upsert

logger = logging.getLogger("data_baking_forge")
logging.basicConfig(level=logging.INFO, format="%(message)s")

TICKERS = [
    'HPG', 'MBB', 'STB', 'VTO', 'VNM', 'VTP', 'MWG',
    'SSI', 'QNS', 'TLG', 'VGI', 'VIB', 'TCB',
]
START_DATE = "2023-01-01"
END_DATE = "2025-12-02"

def bake_ticker(symbol: str) -> int:
    logger.info(f"-> Baking {symbol} from {START_DATE} to {END_DATE}...")
    try:
        q = Quote(symbol=symbol, source='kbs')
        df = q.history(start=START_DATE, end=END_DATE, interval='1D')
        if df is None or df.empty:
            logger.warning(f"   {symbol}: No data returned from API.")
            return 0
        df = df.rename(columns={'time': 'date'})
        df['symbol'] = symbol
        df['source'] = 'kbs'
        if 'adj_close' not in df.columns:
            df['adj_close'] = df['close']
        df['date'] = pd.to_datetime(df['date'], format='mixed', errors='coerce').dt.strftime('%Y-%m-%d')
        df = df.dropna(subset=['date'])
        # Quote.history returns prices ÷1000; multiply back to full VND
        for c in ['open', 'high', 'low', 'close', 'adj_close']:
            if c in df.columns:
                df[c] = df[c] * 1000.0
        cols = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']
        df = df[[c for c in cols if c in df.columns]]
        with get_connection() as conn:
            save_data_upsert('daily_ohlcv', df, conn)
        logger.info(f"   {symbol}: {len(df)} rows baked successfully.")
        return len(df)
    except Exception as e:
        logger.error(f"   {symbol}: FAILED — {e}")
        return 0

def verify_bake(symbols: list):
    logger.info("\n=== VERIFYING BAKE ===")
    with get_connection() as conn:
        for sym in symbols:
            cur = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_ohlcv WHERE symbol=?", (sym,))
            r = cur.fetchone()
            if r and r[2] > 0:
                logger.info(f"  {sym}: {r[2]} rows  [{r[0]} → {r[1]}]")
            else:
                logger.warning(f"  {sym}: NO DATA")

def run():
    logger.info(f"=== DATA BAKING FORGE ===")
    logger.info(f"Target: {len(TICKERS)} tickers")
    logger.info(f"Range:  {START_DATE} → {END_DATE}")
    logger.info(f"Source: KBS (KB Securities)")
    logger.info(f"========================================\n")

    total_rows = 0
    success = 0
    for i, sym in enumerate(TICKERS, 1):
        print(f"[{i}/{len(TICKERS)}] ", end="", flush=True)
        n = bake_ticker(sym)
        if n > 0:
            total_rows += n
            success += 1
        delay = random.uniform(0.5, 1.2)
        print(f"   Throttle {delay:.1f}s...")
        time.sleep(delay)

    logger.info(f"\n=== BAKE COMPLETE: {success}/{len(TICKERS)} tickers, {total_rows} total rows ===")
    verify_bake(TICKERS)
    logger.info("\n=== FORGE SHUTDOWN ===")

if __name__ == "__main__":
    run()
