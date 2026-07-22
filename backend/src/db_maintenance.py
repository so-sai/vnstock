
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# Sentinel v2.1 (Anchor Fix)
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
    return root_path

PROJECT_ROOT = _hydrate_path()
from src.database.db_core import DB_PATH, get_connection

# ============================================================
# LOGGING
# ============================================================
LOG_DIR = PROJECT_ROOT / "backend" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"db_maintenance_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
if sys.platform == "win32":
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.stream = io.TextIOWrapper(h.stream.buffer, encoding='utf-8', line_buffering=True)
logger = logging.getLogger("PTCK_DB_MAINTENANCE")

# ============================================================
# MAINTENANCE TASKS
# ============================================================
def get_db_size_mb() -> float:
    if os.path.exists(DB_PATH):
        return os.path.getsize(DB_PATH) / (1024 * 1024)
    return 0.0

def vacuum_database():
    """Reclaim unused space from SQLite WAL + free pages."""
    logger.info("🧹 VACUUM: Đang dọn dẹp không gian trống...")
    size_before = get_db_size_mb()
    with get_connection() as conn:
        conn.execute("VACUUM;")
        conn.commit()
    size_after = get_db_size_mb()
    saved = size_before - size_after
    logger.info(f"✅ VACUUM hoàn tất. {size_before:.1f}MB -> {size_after:.1f}MB (Tiết kiệm {saved:.1f}MB)")
    return {"before_mb": round(size_before, 2), "after_mb": round(size_after, 2), "saved_mb": round(saved, 2)}

def compact_foreign_history(retention_days=730):
    """Xóa dữ liệu Foreign History cũ hơn retention_days (mặc định 2 năm)."""
    cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    logger.info(f"🗑️ Compacting market_foreign_history: Xóa dữ liệu trước {cutoff_date}...")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM market_foreign_history WHERE date < ?", (cutoff_date,))
        count_before = cursor.fetchone()[0]
        cursor.execute("DELETE FROM market_foreign_history WHERE date < ?", (cutoff_date,))
        deleted = cursor.rowcount
        conn.commit()
    logger.info(f"✅ Đã xóa {deleted} dòng foreign history cũ.")
    return {"table": "market_foreign_history", "deleted_rows": deleted}

def compact_regime_history(retention_days=1095):
    """Giữ lại regime_history 3 năm (đủ cho backtest)."""
    cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    logger.info(f"🗑️ Compacting regime_history: Xóa dữ liệu trước {cutoff_date}...")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM regime_history WHERE date < ?", (cutoff_date,))
        deleted = cursor.rowcount
        conn.commit()
    logger.info(f"✅ Đã xóa {deleted} dòng regime history cũ.")
    return {"table": "regime_history", "deleted_rows": deleted}

def analyze_tables():
    """Update query planner statistics."""
    logger.info("📊 ANALYZE: Cập nhật thống kê query planner...")
    with get_connection() as conn:
        conn.execute("ANALYZE;")
        conn.commit()
    logger.info("✅ ANALYZE hoàn tất.")

def wal_checkpoint():
    """Truncate WAL file để tránh phình to."""
    logger.info("🔧 WAL CHECKPOINT: Đang truncate WAL...")
    wal_path = DB_PATH + "-wal"
    wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    with get_connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()
    wal_size_after = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    logger.info(f"✅ WAL: {wal_size} bytes -> {wal_size_after} bytes")
    return {"wal_before": wal_size, "wal_after": wal_size_after}

def get_table_stats() -> dict:
    """Thống kê hiện tại của các bảng."""
    stats = {}
    with get_connection() as conn:
        cursor = conn.cursor()
        tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table_name,) in tables:
            count = cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            stats[table_name] = count
    return stats

def run_full_maintenance():
    """Chạy toàn bộ quy trình bảo trì DB."""
    logger.info("=" * 60)
    logger.info("🛡️ PTCK DATABASE MAINTENANCE - FULL RUN")
    logger.info("=" * 60)

    start_time = time.time()
    report = {
        "timestamp": datetime.now().isoformat(),
        "status": "FAILED",
        "db_size_mb_before": round(get_db_size_mb(), 2),
        "table_stats_before": get_table_stats(),
        "vacuum_result": {},
        "compact_results": [],
        "wal_result": {},
        "db_size_mb_after": 0,
        "duration_seconds": 0
    }

    try:
        # 1. WAL Checkpoint (nhanh, giải phóng lock)
        report["wal_result"] = wal_checkpoint()

        # 2. Compact old data
        report["compact_results"].append(compact_foreign_history(retention_days=730))
        report["compact_results"].append(compact_regime_history(retention_days=1095))

        # 3. VACUUM (tốn thời gian nhất, chạy sau khi đã xóa dữ liệu)
        report["vacuum_result"] = vacuum_database()

        # 4. ANALYZE
        analyze_tables()

        # 5. Final WAL checkpoint
        report["wal_result_final"] = wal_checkpoint()

        report["db_size_mb_after"] = round(get_db_size_mb(), 2)
        report["table_stats_after"] = get_table_stats()
        report["status"] = "SUCCESS"

    except Exception as e:
        logger.critical(f"💥 MAINTENANCE FAILED: {e}")
        report["status"] = f"FAILED: {str(e)}"
    finally:
        report["duration_seconds"] = round(time.time() - start_time, 2)

        # Save report
        report_path = LOG_DIR / "latest_maintenance_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)

        logger.info("=" * 60)
        logger.info(f"🏁 MAINTENANCE: {report['status']}")
        logger.info(f"📊 DB Size: {report['db_size_mb_before']}MB -> {report['db_size_mb_after']}MB")
        logger.info(f"⏱️ Duration: {report['duration_seconds']}s")
        logger.info(f"📄 Report: {report_path}")
        logger.info("=" * 60)

    return report

if __name__ == "__main__":
    import argparse
    import io
    from datetime import timedelta

    # Fix Windows console encoding
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description="PTCK Database Maintenance Tool")
    parser.add_argument("--full", action="store_true", help="Run full maintenance (VACUUM + Compact + ANALYZE)")
    parser.add_argument("--vacuum", action="store_true", help="Only run VACUUM")
    parser.add_argument("--stats", action="store_true", help="Show table statistics")
    args = parser.parse_args()

    if args.full:
        run_full_maintenance()
    elif args.vacuum:
        vacuum_database()
    elif args.stats:
        stats = get_table_stats()
        print("\nTABLE STATISTICS:")
        for table, count in sorted(stats.items()):
            print(f"  {table}: {count:,} rows")
        print(f"\nDB Size: {get_db_size_mb():.1f} MB")
    else:
        parser.print_help()
