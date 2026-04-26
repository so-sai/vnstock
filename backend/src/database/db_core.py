import sys
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            # Săn lùng Root dựa trên các điểm neo độc bản (screener.py, .kit)
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import src.config

# Database file path managed by Elite Config (supports custom paths via .env)
DB_PATH = str(src.config.DATA_DIR / "screener_cache.db")


@contextmanager
def get_connection():
    """Quản lý kết nối SQLite dùng Context Manager"""
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def optimize_sqlite_engine():
    """Tối ưu hóa SQLite cho hiệu năng cao (WAL mode & Indexing)"""
    if not src.config.DATA_DIR.exists():
        src.config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. Kích hoạt chế độ WAL (Write-Ahead Logging) cho concurrency
        # Giúp UI đọc không bị block khi Engine đang ghi
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")

        # 2. TẠO BẢNG CHUẨN VỚI CONSTRAINT (Chống trùng lặp & Bảo đảm toàn vẹn)
        # Sử dụng PRIMARY KEY (symbol, date) thay vì UNIQUE để tối ưu index vật lý
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_ohlcv (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                adj_close REAL,
                volume INTEGER CHECK(volume >= 0),
                source TEXT,
                PRIMARY KEY (symbol, date)
            )
        """)

        # 3. Tạo Index phụ để Screener quét nhanh các query phức tạp
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol_date 
            ON daily_ohlcv(symbol, date);
        """)

        # 4. TẠO BẢNG PHÂN NGÀNH (Mỗi mã chỉ có 1 dòng dữ liệu ngành)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS symbol_industry (
                symbol TEXT PRIMARY KEY,
                icb_name2 TEXT, -- Supersector (Nhóm ngành)
                icb_name3 TEXT, -- Sector (Phân ngành)
                icb_name4 TEXT  -- Subsector (Phân ngành chi tiết)
            )
        """)

        # 5. TẠO BẢNG LỊCH SỬ KHỐI NGOẠI (Hỗ trợ Snapshot Accumulation)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_foreign_history (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                foreign_vol INTEGER,
                net_vol INTEGER,
                net_value REAL,
                PRIMARY KEY (symbol, date)
            )
        """)

        # 6. TẠO BẢNG DỮ LIỆU VĨ MÔ (OMO, Interbank...)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS macro_history (
                variable TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL,
                PRIMARY KEY (variable, date)
            )
        """)

        # 7. TẠO BẢNG LỊCH SỬ REGIME (Decision Visibility Timeline)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS regime_history (
                date TEXT PRIMARY KEY,
                regime_score REAL,
                status TEXT,
                breadth_pct REAL,
                breadth_velocity REAL,
                trend_score REAL,
                vol_score REAL,
                atr_ratio REAL,
                active_model TEXT,
                recovery_flag INTEGER
            )
        """)

        conn.commit()
    print("✅ Database Engine Optimized (WAL Mode Enabled & Indexed)")
def save_data_upsert(table_name, df, conn):
    """Lưu dữ liệu vào SQLite sử dụng cơ chế INSERT OR REPLACE (UPSERT)"""
    if df.empty:
        return
    
    # --- SENTINEL SAFE PATTERN: Data Sanitization ---
    df_save = df.copy()
    if 'date' in df_save.columns:
        df_save.loc[:, 'date'] = df_save['date'].astype(str)
    
    cursor = conn.cursor()
    columns = df_save.columns.tolist()
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)
    
    sql = f"INSERT OR REPLACE INTO {table_name} ({col_names}) VALUES ({placeholders})"
    
    # Chuyển đổi DataFrame thành list of tuples để thực thi batch
    data = [tuple(x) for x in df_save.values]
    cursor.executemany(sql, data)
    conn.commit()

