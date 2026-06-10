"""
Fix Duplicate Dates Script

Phát hiện và xóa duplicate entries trong daily_ohlcv.
- Có entries với format ngày 'YYYY-MM-DD' và 'YYYY-MM-DD 07:00:00'
- Giữ lại entry có giá hợp lý hơn (thousands vs raw VND)
"""
import sys, os, logging, sqlite3
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

import src.config
from src.database.db_core import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FIX_DUPLICATES")

def find_duplicate_dates(conn) -> list:
    """Find symbols with duplicate dates (with timestamp)."""
    rows = conn.execute("""
        SELECT symbol, date, COUNT(*) as cnt
        FROM daily_ohlcv
        WHERE date LIKE '% %'
        GROUP BY symbol, SUBSTR(date, 1, 10)
        HAVING COUNT(*) > 1
    """).fetchall()
    return rows

def fix_duplicates(dry_run: bool = True) -> dict:
    """Remove duplicate entries, keeping the one with better data."""
    logger.info(f"{'='*60}")
    logger.info(f"FIX DUPLICATE DATES {'(DRY RUN)' if dry_run else 'LIVE EXECUTION'}")
    logger.info(f"{'='*60}")
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=10000")
    
    duplicates = find_duplicate_dates(conn)
    logger.info(f"Found {len(duplicates)} duplicate date groups")
    
    removed = 0
    kept = 0
    
    conn.execute("BEGIN TRANSACTION")
    try:
        for symbol, date_with_ts, cnt in duplicates:
            base_date = date_with_ts.split(' ')[0]
            
            # Get all rows for this base date
            rows = conn.execute("""
                SELECT rowid, date, close, open, high, low
                FROM daily_ohlcv
                WHERE symbol = ? AND date LIKE ? || '%'
                ORDER BY date
            """, (symbol, base_date)).fetchall()
            
            if len(rows) < 2:
                continue
            
            # Find the row with the most reasonable price (>= 100 VND)
            best_row = None
            for row in rows:
                close = row[2]  # close price
                if close and close >= 100:
                    best_row = row
                    break
            
            # If no row >= 100, keep the first one
            if not best_row:
                best_row = rows[0]
            
            # Remove duplicate rows
            for row in rows:
                if row[0] != best_row[0]:  # Not the best row
                    if not dry_run:
                        conn.execute("DELETE FROM daily_ohlcv WHERE rowid=?", (row[0],))
                    removed += 1
                    logger.debug(f"  Remove: {symbol} {row[1]} (close={row[2]})")
                else:
                    # Update the best row to have clean date format
                    if not dry_run:
                        conn.execute(
                            "UPDATE daily_ohlcv SET date=? WHERE rowid=?",
                            (base_date, row[0])
                        )
                    kept += 1
        
        if not dry_run:
            conn.commit()
            logger.info("Transaction committed.")
        else:
            conn.rollback()
            logger.info("Dry run: rollback, no changes.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error: {e}")
        raise
    finally:
        conn.close()
    
    logger.info(f"\nRESULTS:")
    logger.info(f"  Removed: {removed} duplicate rows")
    logger.info(f"  Kept: {kept} rows")
    
    return {"removed": removed, "kept": kept, "dry_run": dry_run}

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fix Duplicate Dates")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true", help="Live execution")
    args = parser.parse_args()
    
    dry_run = not args.live
    fix_duplicates(dry_run=dry_run)
