"""
Price Unit Normalization Script

Phát hiện và chuẩn hóa đơn vị giá trong daily_ohlcv.
- Dữ liệu cũ (trước 2026-04-17): lưu ở đơn vị "nghìn đồng" (ví dụ: 23.65)
- Dữ liệu mới (sau 2026-04-17): lưu ở đơn vị "VNĐ gốc" (ví dụ: 23650)
=> Chuẩn hóa tất cả về VNĐ gốc (x1000)
"""
import sys, os, json, logging, sqlite3
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
logger = logging.getLogger("UNIT_NORMALIZER")

# Date threshold: data after this date is assumed to be in raw VND
THRESHOLD_DATE = "2026-04-17"

def detect_unit_boundary(conn, symbol: str) -> list:
    """Tìm ranh giới chuyển đổi đơn vị cho một mã."""
    rows = conn.execute(
        "SELECT date, close, open, high, low, volume FROM daily_ohlcv WHERE symbol=? ORDER BY date",
        (symbol,)
    ).fetchall()
    
    transitions = []
    for i in range(1, len(rows)):
        prev_close = rows[i-1][1]
        curr_close = rows[i][1]
        if prev_close and curr_close and prev_close > 0:
            ratio = curr_close / prev_close
            if ratio > 500 or (ratio < 0.01 and ratio > 0):
                transitions.append({
                    "date": rows[i][0],
                    "prev_close": prev_close,
                    "curr_close": curr_close,
                    "ratio": round(ratio, 2),
                })
    return transitions

def normalize_symbol(conn, symbol: str, dry_run: bool = True) -> dict:
    """Chuẩn hóa đơn vị giá cho một mã cổ phiếu."""
    rows = conn.execute(
        "SELECT rowid, date, close, open, high, low, adj_close, volume FROM daily_ohlcv WHERE symbol=? ORDER BY date",
        (symbol,)
    ).fetchall()
    
    if not rows:
        return {"symbol": symbol, "status": "EMPTY"}
    
    # Detect the unit boundary: find rows where price jumps by >500x within a few days
    transitions = detect_unit_boundary(conn, symbol)
    
    if not transitions:
        # Check if ALL prices are < 200 (thousands format)
        closes = [r[2] for r in rows if r[2] is not None]
        if closes and all(c < 200 for c in closes):
            # All data is in thousands -> normalize to raw VND
            logger.info(f"  {symbol}: {len(closes)} rows in thousands format -> normalize to raw VND")
            if not dry_run:
                for r in rows:
                    rowid = r[0]
                    factor = 1000.0
                    conn.execute(
                        "UPDATE daily_ohlcv SET close=close*?, open=open*?, high=high*?, low=low*?, adj_close=adj_close*? WHERE rowid=?",
                        (factor, factor, factor, factor, factor, rowid)
                    )
            return {"symbol": symbol, "status": "NORMALIZED", "rows": len(rows), "old_unit": "thousands"}
        
        # Check if ALL prices are > 1000 (raw VND format)
        if closes and all(c >= 1000 for c in closes):
            return {"symbol": symbol, "status": "ALREADY_RAW_VND", "rows": len(rows)}
        
        # Mixed or unclear - skip
        return {"symbol": symbol, "status": "SKIPPED_MIXED", "rows": len(rows)}
    
    # Has transitions - normalize old data (all rows BEFORE the transition) to raw VND
    first_transition_date = transitions[0]["date"]
    old_rows = [r for r in rows if str(r[1]) < first_transition_date]
    new_rows = [r for r in rows if str(r[1]) >= first_transition_date]
    
    if old_rows and all(r[2] and r[2] < 200 for r in old_rows if r[2]):
        logger.info(f"  {symbol}: {len(old_rows)} old rows ({old_rows[0][1]} -> {old_rows[-1][1]}) in thousands -> x1000")
        if not dry_run:
            for r in old_rows:
                rowid = r[0]
                conn.execute(
                    "UPDATE daily_ohlcv SET close=close*?, open=open*?, high=high*?, low=low*?, adj_close=adj_close*? WHERE rowid=?",
                    (1000.0, 1000.0, 1000.0, 1000.0, 1000.0, rowid)
                )
        return {"symbol": symbol, "status": "PARTIAL_FIX", "old_rows": len(old_rows), "new_rows": len(new_rows)}
    
    return {"symbol": symbol, "status": "NO_FIX_NEEDED"}

def run_normalization(dry_run: bool = True, limit: int = None) -> dict:
    logger.info(f"{'='*60}")
    logger.info(f"PRICE UNIT NORMALIZATION {'(DRY RUN)' if dry_run else 'LIVE EXECUTION'}")
    logger.info(f"{'='*60}")
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=10000")
    
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX','VN30') ORDER BY symbol"
    ).fetchall()]
    
    if limit:
        symbols = symbols[:limit]
    
    logger.info(f"Quét {len(symbols)} symbols...")
    
    results = {
        "mode": "DRY_RUN" if dry_run else "LIVE",
        "scan_time": datetime.now().isoformat(),
        "total_symbols": len(symbols),
        "report": []
    }
    
    conn.execute("BEGIN TRANSACTION")
    try:
        for i, symbol in enumerate(symbols):
            if i > 0 and i % 100 == 0:
                logger.info(f"  Tiến độ: {i}/{len(symbols)}")
            result = normalize_symbol(conn, symbol, dry_run)
            if result["status"] not in ("ALREADY_RAW_VND", "NO_FIX_NEEDED", "EMPTY"):
                results["report"].append(result)
        
        if not dry_run:
            conn.commit()
            logger.info("✅ Giao dịch đã commit.")
        else:
            conn.rollback()
            logger.info("⏹️  Dry run: rollback, không có thay đổi.")
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Lỗi: {e}")
        raise
    finally:
        conn.close()
    
    # Summary
    by_status = {}
    for r in results["report"]:
        s = r["status"]
        by_status[s] = by_status.get(s, 0) + 1
    
    logger.info(f"\nKẾT QUẢ:")
    for status, count in sorted(by_status.items()):
        logger.info(f"  {status}: {count} symbols")
    logger.info(f"Tổng số symbols cần xử lý: {len(results['report'])}")
    
    report_path = PROJECT_ROOT / "backend" / "data" / "unit_normalization_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Báo cáo: {report_path}")
    
    return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Price Unit Normalization")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry run (default)")
    parser.add_argument("--live", action="store_true", help="Live execution")
    parser.add_argument("--limit", type=int, default=None, help="Symbol limit for testing")
    args = parser.parse_args()
    
    dry_run = not args.live
    run_normalization(dry_run=dry_run, limit=args.limit)
