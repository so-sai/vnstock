"""migrate_quant_schema.py — Migration: tách quant.db khỏi SCHEMA_SHADOW.

Kiểm tra shadow_cao.db có dữ liệu quant bị ghi nhầm không và khôi phục.

Usage:
    python backend/src/tools/migrate_quant_schema.py [--dry-run] [--force]
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent.parent
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
DATA_DIR = PROJECT_ROOT / "backend" / "data"
QUANT_DB = DATA_DIR / "quant.db"
SHADOW_CAO_DB = DATA_DIR / "shadow_cao.db"


def get_tables(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    return tables


def get_row_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    conn.close()
    return count


def check_shadow_for_quant_data(shadow_path: Path) -> dict:
    """Kiểm tra shadow_cao.db có chứa dữ liệu đáng lẽ thuộc quant.db không.

    Vì quant.db trước đây dùng SCHEMA_SHADOW, các bảng trong shadow_cao.db
    và quant.db giống hệt nhau. Nếu quant.db trống nhưng shadow_cao.db có
    dữ liệu ở các bảng shadow_*, có thể do nhầm lẫn ghi vào sai DB.
    """
    result = {"found_data": False, "shadow_tables": {}, "quant_tables": {}}

    shadow_tables = get_tables(shadow_path)
    quant_tables = get_tables(QUANT_DB)

    for t in shadow_tables:
        count = get_row_count(shadow_path, t)
        result["shadow_tables"][t] = count

    for t in quant_tables:
        count = get_row_count(QUANT_DB, t)
        result["quant_tables"][t] = count

    # Quant đáng lẽ có quant_rs_scores và factor_backtests (schema mới)
    expected_new = {"quant_rs_scores", "factor_backtests"}
    actual_new = set(quant_tables)
    missing = expected_new - actual_new

    # Nếu shadow_cao.db có dữ liệu decision_logs mà quant.db không có gì
    shadow_data_tables = {t: c for t, c in result["shadow_tables"].items() if c > 0}
    quant_data_tables = {t: c for t, c in result["quant_tables"].items() if c > 0}

    result["needs_migration"] = bool(shadow_data_tables) and not bool(quant_data_tables)
    result["missing_new_tables"] = list(missing)

    return result


def run_migration(shadow_path: Path, dry_run: bool = False) -> bool:
    """Tái tạo quant.db với SCHEMA_QUANT.

    Dữ liệu cũ từ SCHEMA_SHADOW (nếu có) được giữ nguyên trong shadow_cao.db
    vì không thể tự động xác định bản ghi nào thuộc quant hay shadow.
    Người dùng cần kiểm tra thủ công nếu cần tách.
    """
    from src.init_db import SCHEMA_QUANT, init_one

    if not QUANT_DB.exists():
        print(f"  [INFO] {QUANT_DB.name} chưa tồn tại — sẽ tạo mới.")

    if dry_run:
        print(f"  [DRY-RUN] Sẽ tạo lại {QUANT_DB.name} với SCHEMA_QUANT")
        print("  [DRY-RUN] Các bảng mới: quant_rs_scores, factor_backtests")
        return True

    ok = init_one(str(QUANT_DB), SCHEMA_QUANT)
    if ok:
        print(f"  [OK] {QUANT_DB.name} đã được khởi tạo với SCHEMA_QUANT")
    else:
        print(f"  [FAIL] Không thể khởi tạo {QUANT_DB.name}")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Migration quant.db schema")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra, không ghi")
    parser.add_argument("--force", action="store_true", help="Ghi đè schema cũ")
    args = parser.parse_args()

    print("=" * 60)
    print("  QUANT SCHEMA MIGRATION — Kiểm tra & khôi phục")
    print("=" * 60)

    # Bước 1: Kiểm tra trạng thái
    print(f"\n  Quant DB:  {QUANT_DB} ({'exists' if QUANT_DB.exists() else 'missing'})")
    print(f"  Shadow DB: {SHADOW_CAO_DB} ({'exists' if SHADOW_CAO_DB.exists() else 'missing'})")

    shadow_path = SHADOW_CAO_DB if SHADOW_CAO_DB.exists() else QUANT_DB
    if not shadow_path.exists():
        print("  [SKIP] Không tìm thấy database nào để kiểm tra.")
        return

    report = check_shadow_for_quant_data(shadow_path)

    print("\n  📊 Bảng trong shadow_cao.db:")
    for t, c in report["shadow_tables"].items():
        status = f"{c} rows" if c > 0 else "0 rows"
        print(f"    - {t}: {status}")

    print("\n  📊 Bảng trong quant.db (schema cũ SCHEMA_SHADOW):")
    for t, c in report["quant_tables"].items():
        status = f"{c} rows" if c > 0 else "0 rows"
        print(f"    - {t}: {status}")

    if report["missing_new_tables"]:
        print(f"\n  ⚠ Thiếu bảng mới: {', '.join(report['missing_new_tables'])}")

    if report["needs_migration"]:
        print("\n  ⚠ Phát hiện: shadow_cao.db có dữ liệu nhưng quant.db trống.")
        print("  → Có thể dữ liệu quant đã bị ghi nhầm vào shadow_cao.db trước đây.")
        print("  → Không thể tự động tách — cần kiểm tra thủ công nội dung shadow tables.")
    else:
        print("\n  ✅ Không phát hiện dữ liệu bị ghi nhầm.")

    # Bước 2: Thực hiện migration
    print()
    if args.dry_run or (not args.force and report.get("missing_new_tables")):
        run_migration(shadow_path, dry_run=True)
        if not args.force:
            print("\n  Dùng --force để ghi đè schema quant.db")
    else:
        run_migration(shadow_path)

    print(f"\n  {'=' * 50}")
    print("  Kết luận:")
    print("  - quant.db hiện dùng: SCHEMA_QUANT ✅")
    print("  - shadow_cao.db giữ nguyên: SCHEMA_SHADOW ✅")
    print("  - Nếu cần trích xuất dữ liệu cũ từ shadow_cao.db sang quant.db,")
    print('    dùng: python -c "import sqlite3; ..." để SELECT/INSERT thủ công.')
    print(f"  {'=' * 50}")


if __name__ == "__main__":
    main()
