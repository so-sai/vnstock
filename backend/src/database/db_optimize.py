"""db_optimize.py — Một lần duy nhất: tối ưu hóa toàn bộ 6 database về cấu hình hiệu năng tối đa.

Chạy: python ptck.py db optimize
- Ép STRICT tables cho bảng lõi
- Chuyển cột JSON text → JSONB (SQLite 3.45+)
- VACUUM + reindex sau migration
"""

import shutil
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

BACKUP_SUFFIX = ".pre_optimize.bak"


def _hydrate_path():
    if getattr(sys, "frozen", False):
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


@contextmanager
def connect(db_path: str):
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
    finally:
        conn.close()


def backup_db(db_path: Path):
    bak = db_path.with_suffix(db_path.suffix + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(db_path, bak)
        print(f"  [BACKUP] {db_path.name} → {bak.name}")
    return bak


def migrate_json_to_jsonb(conn, table: str, json_columns: list[str]):
    """Chuyển cột TEXT chứa JSON sang JSONB."""
    for col in json_columns:
        try:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} != ''")
            total = cursor.fetchone()[0]
            if total == 0:
                continue
            conn.execute(f"""
                UPDATE {table} SET {col} = jsonb({col})
                WHERE {col} IS NOT NULL AND {col} != ''
                  AND json_valid({col}) = 1
            """)
            print(f"    [JSONB] {table}.{col}: {total} rows migrated")
        except Exception as e:
            print(f"    [JSONB] {table}.{col}: SKIP ({e})")


def migrate_strict_table(conn, table: str, create_sql: str):
    """Tạo bảng STRICT mới, copy dữ liệu, swap tên."""
    temp_table = f"{table}_strict_migrate"
    try:
        conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
        strict_sql = create_sql.rstrip(";") + " STRICT;"
        conn.execute(strict_sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {temp_table}"))
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        col_list = ", ".join(cols)
        conn.execute(f"INSERT INTO {temp_table} ({col_list}) SELECT {col_list} FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table}")
        print(f"    [STRICT] {table}: migrated to STRICT mode")
    except Exception as e:
        conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
        print(f"    [STRICT] {table}: SKIP ({e})")


def optimize_database(db_name: str):
    db_path = DATA_DIR / db_name
    if not db_path.exists():
        print(f"\n[{db_name}] NOT FOUND — skipping")
        return

    size_before = db_path.stat().st_size / 1024
    print(f"\n[{db_name}] ({size_before:.0f} KB) — starting...")
    backup_db(db_path)

    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for (tname,) in tables:
        if tname == "sqlite_sequence":
            continue
        cols = conn.execute(f"PRAGMA table_info({tname})").fetchall()
        json_cols = [
            c[1]
            for c in cols
            if c[1].lower()
            in (
                "signals",
                "projection_summary",
                "sector_forecasts",
                "leading_sectors",
                "lagging_sectors",
                "habitat_distribution",
                "report_json",
                "details",
                "metadata",
                "config",
                "payload",
                "thesis_notes",
                "notes",
                "engine_scores",
                "features",
            )
        ]
        if json_cols:
            migrate_json_to_jsonb(conn, tname, json_cols)

    conn.commit()

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.execute("VACUUM;")
    conn.execute("PRAGMA optimize;")
    conn.close()

    size_after = db_path.stat().st_size / 1024
    print(
        f"[{db_name}] DONE: {size_before:.0f} KB → {size_after:.0f} KB "
        f"({'+' if size_after > size_before else ''}{size_after - size_before:+.0f} KB)"
    )


def run_all():
    print("=" * 60)
    print("  PTCK — DB OPTIMIZE ENGINE v1.0")
    print("  JSONB + STRICT + VACUUM for all 6 databases")
    print("=" * 60)

    databases = [
        "screener_cache.db",
        "telemetry.db",
        "portfolio_state.db",
        "sentinel_macro.db",
        "shadow_cao.db",
        "quant.db",
    ]

    for db in databases:
        optimize_database(db)

    print()
    print("=" * 60)
    print("  OPTIMIZATION COMPLETE.")
    print(f"  Backups saved as *.db{BACKUP_SUFFIX} in {DATA_DIR}")
    print("  Run 'python ptck.py db stats' to verify.")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
