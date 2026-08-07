import json
import logging
import math
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root"""
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            # Săn lùng Root dựa trên các điểm neo độc bản (screener.py, .kit)
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))

    backend_dir = root_path / "backend"
    if backend_dir.exists() and str(backend_dir) not in sys.path:
        sys.path.append(str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()
import src.config

logger = logging.getLogger(__name__)

# Database file path managed by Elite Config (supports custom paths via .env)
DB_PATH = str(src.config.DATA_DIR / "screener_cache.db")


# ============================================================================
#  JSON SERIALIZATION BOUNDARY — Numpy/Pandas → pure storage format
# ============================================================================
# Hạ tầng serialize tập trung cho TOÀN BỘ luồng xuất dữ liệu PTCK. Đặt tại
# db_core.py vì đây là RANH GIỚI LƯU TRỮ duy nhất mà mọi module đều import:
# nơi các kiểu của hệ sinh thái Quant (numpy scalar/array, pandas, Decimal,
# datetime) buộc phải chuyển thành JSON thuần trước khi ghi CSDL/log/file.
#
# Chịu lỗi (Fault Tolerance): json.dumps mặc định KHÔNG serialize được
# np.bool_ / np.integer / np.floating / np.ndarray → ném TypeError, có thể
# làm SẬP cả chu trình EOD tự động (vd healing_illusion là np.bool_ sinh ra
# từ phép so sánh numpy). NumpyEncoder chặn triệt để mọi kiểu này một chỗ,
# thay cho các encoder cục bộ rải rác dễ bỏ sót.
class NumpyEncoder(json.JSONEncoder):
    """Custom JSON encoder ép kiểu Numpy/Pandas/Decimal/datetime → JSON thuần.

    Xử lý:
      - np.bool_                 → bool
      - np.integer (mọi độ rộng) → int
      - np.floating              → float (NaN/Inf → None để JSON hợp lệ)
      - np.ndarray               → list (đệ quy qua tolist())
      - Decimal                  → float
      - datetime / date          → ISO 8601 string
      - đối tượng có .to_dict()  → dict (pydantic-lite / dataclass tiện ích)
      - pydantic BaseModel       → dict (model_dump) — thay _PydanticEncoder cũ
    """

    def default(self, obj):
        # Lazy import numpy — db_core không hard-depend numpy lúc import.
        try:
            import numpy as np
        except Exception:  # pragma: no cover  # noqa: BLE001 - lazy import numpy: thiếu thư viện → tiếp tục encoder thuần
            np = None

        if np is not None:
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                f = float(obj)
                return None if (math.isnan(f) or math.isinf(f)) else f
            if isinstance(obj, np.ndarray):
                # tolist() rồi sanitize NaN/Inf → None (JSON hợp lệ RFC 8259)
                return _sanitize_for_json(obj.tolist())
            # np.datetime64 → ISO string
            if isinstance(obj, np.datetime64):
                return str(obj)

        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        # float thường nhưng NaN/Inf (không phải numpy) — chuẩn hóa về None
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        # Đối tượng tiện ích có .to_dict()
        to_dict = getattr(obj, "to_dict", None)
        if callable(to_dict):
            try:
                return to_dict()
            except Exception:  # pragma: no cover  # noqa: BLE001, S110 - helper to_dict hỏng → thử model_dump rồi default
                pass
        # pydantic BaseModel (model_dump)
        model_dump = getattr(obj, "model_dump", None)
        if callable(model_dump):
            try:
                return model_dump()
            except Exception:  # pragma: no cover  # noqa: BLE001, S110 - model_dump hỏng → trả json mặc định
                pass
        return super().default(obj)


def _sanitize_for_json(obj):
    """Đệ quy chuẩn hóa NaN/Inf → None TRƯỚC khi json.dumps.

    Lý do bắt buộc: np.float64 LÀ subclass của float Python → bộ mã hóa C của
    json đi đường tắt (fast path) serialize thẳng, KHÔNG gọi default() của
    NumpyEncoder → NaN/Infinity lọt ra thành JSON KHÔNG hợp lệ (RFC 8259).
    Pre-sanitize đảm bảo output luôn hợp lệ, chịu lỗi tuyệt đối.
    """
    if isinstance(obj, float):  # gồm cả np.float64 (subclass của float)
        return None if (math.isnan(obj) or math.isinf(obj)) else float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def safe_json_dumps(obj, *, ensure_ascii: bool = False, **kwargs) -> str:
    """json.dumps chịu lỗi kiểu Quant — LUÔN dùng cho mọi output PTCK.

    Chống TypeError do np.bool_/np.integer/np.floating/np.ndarray… làm sập
    chu trình EOD, và chống NaN/Inf (np.float64) tạo JSON không hợp lệ.
    """
    kwargs.setdefault("cls", NumpyEncoder)
    return json.dumps(_sanitize_for_json(obj), ensure_ascii=ensure_ascii, **kwargs)


def safe_json_dump(obj, fp, *, ensure_ascii: bool = False, **kwargs) -> None:
    """json.dump (ghi file) chịu lỗi kiểu Quant — dùng cho persist file/log."""
    kwargs.setdefault("cls", NumpyEncoder)
    json.dump(_sanitize_for_json(obj), fp, ensure_ascii=ensure_ascii, **kwargs)


@contextmanager
def get_connection():
    """Quản lý kết nối SQLite dùng Context Manager với PRAGMA tối ưu & check_same_thread=False"""
    conn = sqlite3.connect(DB_PATH, timeout=15.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA cache_size=-20000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA mmap_size=268435456;")
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.row_factory = sqlite3.Row
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

        # 5. TẠO BẢNG ẢO FTS5 CHO TÌM KIẾM TOÀN VĂN (Instant Search)
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(
                symbol, icb_name2, icb_name3, icb_name4,
                tokenize='unicode61'
            )
        """)
        refresh_fts5_index(conn)

        # 6. TẠO BẢNG LỊCH SỬ KHỐI NGOẠI (Hỗ trợ Snapshot Accumulation)
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
                is_stale INTEGER DEFAULT 0,
                PRIMARY KEY (variable, date)
            )
        """)

        # Migration: add is_stale column to macro_history if missing
        try:
            cursor.execute("ALTER TABLE macro_history ADD COLUMN is_stale INTEGER DEFAULT 0")
        except (sqlite3.Error, TypeError, ValueError) as _e:
            logger.debug("ALTER TABLE macro_history.is_stale đã tồn tại hoặc lỗi (bỏ qua): %s", _e)

        # Migration: add is_stale to daily_ohlcv (needed by EliteArmor stale detection)
        try:
            cursor.execute("ALTER TABLE daily_ohlcv ADD COLUMN is_stale INTEGER DEFAULT 0")
        except (sqlite3.Error, TypeError, ValueError) as _e:
            logger.debug("ALTER TABLE daily_ohlcv.is_stale đã tồn tại hoặc lỗi (bỏ qua): %s", _e)

        # 6b. TẠO BẢNG SỨC KHỎE HỆ THỐNG (System Health Ledger cho Governor Engine)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_health (
                component TEXT NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (component)
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

        # 8. TẠO BẢNG LỊCH SỬ CAPITAL DISPLACEMENT (Reference Case Vault)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS capital_displacement_history (
                date TEXT PRIMARY KEY,
                classification TEXT,
                conviction TEXT,
                signals TEXT,
                top1_symbol TEXT,
                top1_concentration REAL,
                wl_top1_symbol TEXT,
                wl_top1_concentration REAL,
                top3_concentration REAL,
                top10_concentration REAL DEFAULT 0,
                bank_share REAL DEFAULT 0,
                sector_breadth REAL DEFAULT 0,
                defensive_avg_chg REAL DEFAULT 0,
                vnindex_close REAL DEFAULT 0,
                vnindex_chg REAL DEFAULT 0,
                vnindex_vol_ratio REAL DEFAULT 0
            )
        """)

        # 9. TẠO BẢNG DỰ BÁO DÒNG TIỀN (Flow Forecasting)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flow_forecast_history (
                date TEXT PRIMARY KEY,
                projected_regime TEXT,
                regime_confidence TEXT,
                regime_prob_trending REAL,
                regime_prob_ranging REAL,
                regime_prob_crisis REAL,
                flow_velocity REAL,
                rotation_velocity REAL,
                flow_dispersion REAL,
                projection_summary TEXT,
                sector_forecasts TEXT,
                leading_sectors TEXT,
                lagging_sectors TEXT
            )
        """)

        # 10. TẠO BẢNG LỊCH SỬ RSI REGIME (Momentum Habitat)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rsi_regime_history (
                date TEXT PRIMARY KEY,
                total_scanned INTEGER,
                bull_count INTEGER,
                bear_count INTEGER,
                aligned_count INTEGER,
                misaligned_count INTEGER,
                habitat_distribution TEXT,
                report_json TEXT
            )
        """)

        # 10b. TẠO BẢNG SENSOR VALIDATION (Upstream Evidence — P(Crisis|Signal))
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sensor_validation (
                sensor        TEXT NOT NULL,
                signal_date   TEXT NOT NULL,
                signal_type   TEXT NOT NULL,
                signal_value  REAL,
                crisis_flag   INTEGER NOT NULL DEFAULT 0,
                lead_days     INTEGER,
                horizon_days  INTEGER NOT NULL DEFAULT 20,
                PRIMARY KEY (sensor, signal_date, signal_type)
            )
        """)

        # 11. TẠO BẢNG IPO CALENDAR CHO HUD / STATIC SEED
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ipo_calendar (
                symbol TEXT PRIMARY KEY,
                listing_date TEXT NOT NULL,
                listing_price REAL NOT NULL DEFAULT 0,
                listing_volume INTEGER NOT NULL DEFAULT 0,
                market_cap_listing REAL NOT NULL DEFAULT 0,
                sector TEXT NOT NULL DEFAULT 'UNKNOWN',
                exchange TEXT NOT NULL DEFAULT 'HOSE',
                aftermarket_return_pct REAL DEFAULT 0,
                days_listed INTEGER DEFAULT 0,
                source TEXT DEFAULT 'STATIC_SEED',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        conn.commit()
    print("[OK] Database Engine Optimized (WAL Mode Enabled & Indexed)")


def refresh_fts5_index(conn):
    """Đồng bộ dữ liệu từ symbol_industry sang chỉ mục FTS5."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM symbol_fts")
    cursor.execute("""
        INSERT INTO symbol_fts(rowid, symbol, icb_name2, icb_name3, icb_name4)
        SELECT rowid, symbol, icb_name2, icb_name3, icb_name4 FROM symbol_industry
        WHERE symbol IS NOT NULL
    """)
    conn.commit()
    cnt = cursor.execute("SELECT changes()").fetchone()[0]
    if cnt:
        print(f"[FTS5] Indexed {cnt} symbols for instant search")


def save_data_upsert(table_name, df, conn):
    """Lưu dữ liệu vào SQLite sử dụng cơ chế INSERT OR REPLACE (UPSERT)"""
    if df.empty:
        return

    # --- SENTINEL SAFE PATTERN: Data Sanitization ---
    df_save = df.copy()
    if "date" in df_save.columns:
        df_save["date"] = df_save["date"].astype(str)
        # WRITE-TIME GUARD (fix 06/08/2026): pandas datetime64[ns] .astype(str)
        # sinh 'YYYY-MM-DD 07:00:00' -> phá PRIMARY KEY (symbol,date) vì khác khóa
        # plain, tạo 59,219 dòng datetime vô hình với exact-match nhưng ô nhiễm
        # range-query. Chuẩn hóa mọi date về dạng 'YYYY-MM-DD'.
        df_save["date"] = [
            d[:10] if isinstance(d, str) and len(d) > 10 and d[4] == "-" and d[7] == "-" else d for d in df_save["date"]
        ]

    cursor = conn.cursor()
    columns = df_save.columns.tolist()
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)

    sql = f"INSERT OR REPLACE INTO {table_name} ({col_names}) VALUES ({placeholders})"

    # Chuyển đổi DataFrame thành list of tuples để thực thi batch
    data = [tuple(x) for x in df_save.values]
    cursor.executemany(sql, data)
    conn.commit()
