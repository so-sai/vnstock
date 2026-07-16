"""database_guardian.py — Disaster Recovery Pipeline cho PTCK Database.

Chạy ngầm qua Cronjob/Task Scheduler lúc 23:00 hàng ngày.

Quy trình:
  Bước 1 — Integrity Check: PRAGMA integrity_check (Thread Isolation)
  Bước 2 — Online Backup: sqlite3.Connection.backup()
  Retention — Rolling 7-day, auto-prune

Thread Isolation:
  integrity_check được bọc trong async function chạy trên luồng riêng
  để không blocking UI (Tauri) khi DB phình to.

Usage:
  python -m backend.src.database.database_guardian
  python ptck.py db backup
"""
import asyncio
import io
import json
import logging
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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
    return root_path


PROJECT_ROOT = _hydrate_path()
import src.config

DATA_DIR = src.config.DATA_DIR
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = PROJECT_ROOT / "backend" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"guardian_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
if sys.platform == "win32":
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            if hasattr(h.stream, 'buffer') and not isinstance(h.stream, io.TextIOWrapper):
                try:
                    h.stream = io.TextIOWrapper(h.stream.buffer, encoding='utf-8', line_buffering=True)
                except Exception:
                    pass
logger = logging.getLogger("PTCK_DB_GUARDIAN")

BACKUP_PREFIX = "screener_cache"
BACKUP_SUFFIX = ".bak"
MAX_BACKUPS = 7
RETENTION_DAYS = 7
DB_PATH = DATA_DIR / "screener_cache.db"
ALERT_FILE = DATA_DIR / "guardian_alert.json"


def integrity_check(db_path: Path = DB_PATH) -> dict:
    """Bước 1: Thực thi PRAGMA integrity_check.

    Returns:
        {"status": "ok"} hoặc {"status": "error", "details": [...]}
    """
    logger.info("=" * 60)
    logger.info("Bước 1: INTEGRITY CHECK — Đang quét toàn vẹn database...")
    logger.info("=" * 60)

    if not db_path.exists():
        msg = f"Database không tồn tại: {db_path}"
        logger.critical(msg)
        return {"status": "error", "details": [msg]}

    size_mb = db_path.stat().st_size / (1024 * 1024)
    logger.info(f"Dung lượng database: {size_mb:.1f} MB")

    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        conn.close()

        results = [row[0] for row in rows]
        if all(r == "ok" for r in results):
            logger.info("✅ INTEGRITY CHECK: PASS — Database lành mạnh.")
            return {"status": "ok", "details": results}
        else:
            logger.critical("❌ INTEGRITY CHECK: FAIL — Database bị corrupted!")
            for r in results:
                if r != "ok":
                    logger.critical(f"   Lỗi: {r}")
            _trigger_alert("integrity_check_failed", results)
            return {"status": "error", "details": results}
    except Exception as e:
        logger.critical(f"❌ INTEGRITY CHECK: EXCEPTION — {e}")
        _trigger_alert("integrity_check_exception", str(e))
        return {"status": "error", "details": [str(e)]}


def _trigger_alert(check_type: str, details):
    """Ghi file cảnh báo khẩn cấp — Push Notification/Email integration point."""
    alert = {
        "timestamp": datetime.now().isoformat(),
        "type": check_type,
        "severity": "CRITICAL",
        "database": str(DB_PATH),
        "details": details if isinstance(details, list) else [details],
        "message": f"[GUARDIAN] {check_type}: screener_cache.db cần can thiệp thủ công!"
    }
    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        json.dump(alert, f, indent=2, ensure_ascii=False)
    logger.critical(f"🚨 Alert file created: {ALERT_FILE}")
    logger.critical(f"📧 Push Notification / Email sẽ được gửi từ đây (integration pending)")


def online_backup(db_path: Path = DB_PATH, backup_dir: Path = BACKUP_DIR) -> dict:
    """Bước 2: Sao lưu Trực tuyến — sqlite3.Connection.backup().

    Cấm shutil.copy2 / cp vì WAL đang ghi nén.
    Dùng SQLite Online Backup API (single-threaded snapshot).
    """
    logger.info("=" * 60)
    logger.info("Bước 2: ONLINE BACKUP — Đang sao lưu qua SQLite Backup API...")
    logger.info("=" * 60)

    if not db_path.exists():
        msg = f"Database không tồn tại: {db_path}"
        logger.critical(msg)
        return {"status": "error", "message": msg}

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    backup_filename = f"{BACKUP_PREFIX}_{timestamp}{BACKUP_SUFFIX}"
    backup_path = backup_dir / backup_filename

    size_before = db_path.stat().st_size / (1024 * 1024)
    logger.info(f"Nguồn: {db_path} ({size_before:.1f} MB)")
    logger.info(f"Đích: {backup_path}")

    try:
        src_conn = sqlite3.connect(str(db_path), timeout=10)
        dst_conn = sqlite3.connect(str(backup_path), timeout=10)

        start = time.time()
        src_conn.backup(dst_conn, pages=-1, progress=None)
        elapsed = time.time() - start

        dst_conn.close()
        src_conn.close()

        size_after = backup_path.stat().st_size / (1024 * 1024)
        logger.info(f"✅ ONLINE BACKUP: THÀNH CÔNG — {elapsed:.2f}s, {size_after:.1f} MB")
        return {
            "status": "ok",
            "backup_path": str(backup_path),
            "size_mb": round(size_after, 2),
            "duration_seconds": round(elapsed, 2)
        }
    except Exception as e:
        logger.critical(f"❌ ONLINE BACKUP: THẤT BẠI — {e}")
        if backup_path.exists():
            backup_path.unlink()
        _trigger_alert("online_backup_failed", str(e))
        return {"status": "error", "message": str(e)}


def prune_old_backups(backup_dir: Path = BACKUP_DIR) -> dict:
    """Chính sách luân chuyển: giữ tối đa MAX_BACKUPS, xóa file quá RETENTION_DAYS ngày."""
    logger.info("=" * 60)
    logger.info("RETENTION: Đang dọn dẹp backup cũ...")
    logger.info("=" * 60)

    if not backup_dir.exists():
        logger.info("Thư mục backup chưa tồn tại, bỏ qua.")
        return {"status": "ok", "deleted": [], "kept": 0}

    pattern = f"{BACKUP_PREFIX}_*{BACKUP_SUFFIX}"
    backups = sorted(backup_dir.glob(pattern))
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    deleted = []
    kept = []

    for bak in backups:
        try:
            date_str = bak.stem.replace(f"{BACKUP_PREFIX}_", "")
            date_str = date_str.split("_")[0]
            bak_date = datetime.strptime(date_str, "%Y%m%d")
            bak_age_days = (datetime.now() - bak_date).days

            if bak_age_days > RETENTION_DAYS:
                bak.unlink()
                deleted.append(str(bak.name))
                logger.info(f"  🗑️ Đã xóa: {bak.name} ({bak_age_days} ngày tuổi)")
            else:
                kept.append(str(bak.name))
        except (ValueError, IndexError):
            bak.unlink()
            deleted.append(str(bak.name))
            logger.info(f"  🗑️ Đã xóa (format lỗi): {bak.name}")

    if len(kept) > MAX_BACKUPS:
        excess = sorted(kept)[:len(kept) - MAX_BACKUPS]
        for name in excess:
            bak = backup_dir / name
            bak.unlink()
            deleted.append(name)
            kept.remove(name)
            logger.info(f"  🗑️ Đã xóa (vượt cap {MAX_BACKUPS}): {name}")

    logger.info(f"✅ Giữ lại {len(kept)} bản backup, đã xóa {len(deleted)} bản cũ.")
    return {"status": "ok", "deleted": deleted, "kept": kept}


def run_guardian_cycle(dry_run: bool = False) -> dict:
    """Chu trình Guardian hoàn chỉnh: Check → Backup → Prune."""
    logger.info("=" * 60)
    logger.info("🛡️ PTCK DATABASE GUARDIAN — Disaster Recovery Pipeline")
    logger.info(f"📅 {datetime.now().isoformat()}")
    logger.info("=" * 60)

    report = {
        "timestamp": datetime.now().isoformat(),
        "database": str(DB_PATH),
        "integrity_check": {},
        "online_backup": {},
        "prune": {},
        "status": "FAILED",
        "duration_seconds": 0
    }
    start = time.time()

    try:
        integrity_result = integrity_check()
        report["integrity_check"] = integrity_result
        if integrity_result["status"] != "ok":
            report["status"] = f"BLOCKED: integrity_check failed — {integrity_result['details']}"
            logger.critical("🚫 Chu trình backup bị hủy do integrity_check thất bại!")
            report["duration_seconds"] = round(time.time() - start, 2)
            return report

        if dry_run:
            logger.info("🏁 DRY-RUN: Bỏ qua backup, chỉ kiểm tra integrity.")
            report["status"] = "DRY_RUN_PASSED"
            report["duration_seconds"] = round(time.time() - start, 2)
            return report

        backup_result = online_backup()
        report["online_backup"] = backup_result
        if backup_result["status"] != "ok":
            report["status"] = f"BLOCKED: online_backup failed — {backup_result['message']}"
            report["duration_seconds"] = round(time.time() - start, 2)
            return report

        prune_result = prune_old_backups()
        report["prune"] = prune_result
        report["status"] = "SUCCESS"

    except Exception as e:
        logger.critical(f"💥 GUARDIAN CYCLE FAILED: {e}")
        report["status"] = f"FAILED: {str(e)}"
    finally:
        report["duration_seconds"] = round(time.time() - start, 2)
        report_path = DATA_DIR / "guardian_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info("=" * 60)
        logger.info(f"🏁 GUARDIAN: {report['status']}")
        logger.info(f"⏱️ Duration: {report['duration_seconds']}s")
        logger.info(f"📄 Report: {report_path}")
        logger.info("=" * 60)

    return report


# ── Thread Pool for Isolation ────────────────────────────────────────
_GUARDIAN_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="guardian_io"
)


async def async_integrity_check(db_path: Path = DB_PATH) -> dict:
    """integrity_check chạy trên luồng riêng — không block asyncio event loop.

    Dành cho Tauri UI / FastAPI async endpoints.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _GUARDIAN_EXECUTOR, integrity_check, db_path
    )


async def async_run_guardian_cycle(dry_run: bool = False) -> dict:
    """Full guardian cycle chạy trên luồng riêng."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _GUARDIAN_EXECUTOR, run_guardian_cycle, dry_run
    )


def list_backups(backup_dir: Path = BACKUP_DIR) -> list:
    """Liệt kê tất cả bản backup hiện có."""
    if not backup_dir.exists():
        return []
    pattern = f"{BACKUP_PREFIX}_*{BACKUP_SUFFIX}"
    backups = []
    for bak in sorted(backup_dir.glob(pattern), reverse=True):
        size_mb = bak.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(bak.stat().st_mtime)
        backups.append({
            "name": bak.name,
            "size_mb": round(size_mb, 2),
            "modified": mtime.isoformat()
        })
    return backups


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PTCK Database Guardian — Disaster Recovery Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra integrity, không backup")
    parser.add_argument("--list", action="store_true", help="Liệt kê các bản backup hiện có")
    parser.add_argument("--check", action="store_true", help="Chỉ chạy integrity check")
    args = parser.parse_args()

    if args.list:
        backups = list_backups()
        if backups:
            print(f"\n📋 BACKUPS ({len(backups)} bản):")
            for b in backups:
                print(f"  {b['name']:45s} {b['size_mb']:>8.2f} MB  {b['modified']}")
        else:
            print("\n📭 Không có bản backup nào.")
    elif args.check:
        result = integrity_check()
        if result["status"] == "ok":
            print("✅ INTEGRITY CHECK: PASS")
        else:
            print("❌ INTEGRITY CHECK: FAIL")
            for d in result["details"]:
                print(f"   {d}")
    else:
        run_guardian_cycle(dry_run=args.dry_run)
