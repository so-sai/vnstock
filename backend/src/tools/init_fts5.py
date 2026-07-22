"""init_fts5.py — Khởi tạo bảng ảo FTS5 cho Instant Search.

Root cause đã xác minh: `symbol_fts` table không tồn tại trên DB production
(`screener_cache.db`). Nguyên nhân: code trong `db_core.py:100-105` chỉ tạo FTS
khi `optimize_sqlite_engine()` được gọi — nếu DB được tạo từ code base cũ,
bảng FTS sẽ không tự xuất hiện.

Script này:
1. Tạo bảng ảo `symbol_fts` (nếu chưa có)
2. Populate từ `symbol_industry` (1 dòng / mã, lấy symbol + icb_name2/3/4)
3. Verify: gọi thử MATCH query với "VCB" → phải trả ≥1 dòng

Usage::

    python -m backend.src.tools.init_fts5
    python -m backend.src.tools.init_fts5 --verify   # mặc định bật
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent.parent
        root = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root = current
                break
            current = current.parent
    for p in (root, root / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root


PROJECT_ROOT = _hydrate_path()

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("init_fts5")


def init_fts5_table(verbose: bool = True) -> int:
    """
    Tạo bảng ảo symbol_fts + populate từ symbol_industry.
    Trả về số dòng đã insert.
    Idempotent: chạy nhiều lần vẫn an toàn.
    """
    from src.database.db_core import get_connection

    with get_connection() as conn:
        cur = conn.cursor()

        # 1. Tạo bảng ảo nếu chưa có
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(
                symbol, icb_name2, icb_name3, icb_name4,
                tokenize='unicode61'
            )
        """)
        if verbose:
            logger.info("[1/3] Bảng ảo symbol_fts đã đảm bảo tồn tại")

        # 2. Clear & repopulate (idempotent)
        cur.execute("DELETE FROM symbol_fts")
        cur.execute("""
            INSERT INTO symbol_fts(rowid, symbol, icb_name2, icb_name3, icb_name4)
            SELECT rowid, symbol, icb_name2, icb_name3, icb_name4
            FROM symbol_industry
            WHERE symbol IS NOT NULL AND symbol != ''
        """)
        inserted = cur.rowcount
        conn.commit()

        if verbose:
            logger.info(f"[2/3] Đã populate {inserted} mã CK từ symbol_industry vào symbol_fts")

        # 3. Verify schema
        cur.execute("SELECT count(*) AS n FROM symbol_fts")
        total = cur.fetchone()["n"]
        if verbose:
            logger.info(f"[3/3] Tổng số dòng trong symbol_fts: {total}")

        return inserted


def verify_fts5(verbose: bool = True) -> bool:
    """
    Test query thật với các từ khóa phổ biến.
    Trả về True nếu tất cả test pass.
    """
    import sqlite3

    from src.database.db_core import DB_PATH

    test_cases = ["VCB", "VNM", "Ngân hàng", "Bất động sản"]
    all_ok = True

    with sqlite3.connect(DB_PATH, timeout=5) as conn:
        for q in test_cases:
            try:
                safe = q.replace('"', '""')
                rows = conn.execute(
                    'SELECT symbol, icb_name2 FROM symbol_fts WHERE symbol_fts MATCH ? LIMIT 3',
                    (f'"{safe}"*',),
                ).fetchall()
                ok = len(rows) > 0
                status = "✅" if ok else "⚠️"
                if verbose:
                    logger.info(f"  {status} Query '{q}' → {len(rows)} kết quả: {[r[0] for r in rows]}")
                if not ok:
                    all_ok = False
            except Exception as e:
                logger.error(f"  ❌ Query '{q}' thất bại: {e}")
                all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Khởi tạo bảng FTS5 cho Instant Search")
    parser.add_argument("--verify", action="store_true", default=True, help="Verify bằng test query (mặc định: bật)")
    parser.add_argument("--no-verify", dest="verify", action="store_false", help="Bỏ qua verify")
    args = parser.parse_args()

    print("=" * 60)
    print("  PTCK — INIT FTS5 (Instant Search)")
    print("=" * 60)

    try:
        inserted = init_fts5_table()
        print()
        if args.verify:
            print("── Verify bằng test query ──")
            ok = verify_fts5()
            print()
            if ok:
                print("✅ FTS5 hoạt động. Search 'vcb' trên UI giờ sẽ trả kết quả.")
            else:
                print("⚠️ FTS5 đã init nhưng verify chưa đủ — kiểm tra log ở trên.")
        else:
            print(f"✅ Đã insert {inserted} dòng vào symbol_fts.")
    except Exception as e:
        logger.error(f"Init FTS5 thất bại: {e}")
        sys.exit(1)

    print("=" * 60)


if __name__ == "__main__":
    main()
