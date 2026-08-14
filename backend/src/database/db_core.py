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


# ============================================================================
#  INGESTION SCALE GUARD — Data Contract cho daily_ohlcv (VND-scale bắt buộc)
# ============================================================================
# Lỗi đợt 2 (2026-08-14): 8 mã UNIVERSE (HPG/SSI/MBB/STB/TCB/VIB/MWG/VNM) có
# TOÀN BỘ lịch sử max(close)<1000 → nguồn KBS/VCI trả "nghìn đồng" (HPG=22.1)
# bị lưu nguyên như penny. Guard tại BIÊN NẠP chặn từ gốc, không chờ migration.
#
# Quy tắc bất biến (Ingestion Invariant) — thứ tự quyết định:
#   scale×1000 ⟺ close<1000 ∧ (
#       symbol ∈ ACTIVE_UNIVERSE                       → trụ: không bao giờ penny
#       ∨ (DB-hist close≥1000 ∧ avg-vol≥50k)          → VND-scale đã xác nhận
#       ∨ (UNKNOWN ∧ ADV_20d từ DF ≥ 50k)             → thanh khoản thật ≠ penny
#   )
#   PENNY_KNOWN (lịch sử gần đây ĐỀU <1000)      → HARD EXCLUSION, không nhân.
#   HIGH_PRICE_LOW_VOL (close≥1000 nhưng vol mỏng) → KHÔNG nhân (thiếu provenance).
#   UNKNOWN_LOW_ADV                                → KHÔNG nhân (thiếu provenance).
#
# Chống False Positive (nhân nhầm penny 800→800,000): cổ phiếu có lịch sử gần
# đây toàn <1000 VND là penny thật theo provenance (vd ACM 500 VND, ADV cao vì
# giá rẻ); hoặc giá cao nhưng volume mỏng như băng phiếu (A32 100 cp, APT 0) —
# tuyệt đối không nhân. Chống False Negative (bỏ sót trụ thị giá <100k):
# UNIVERSE là lớp quy tắc cứng KHÔNG phụ thuộc ADV/DB.
#
# Audit Provenance: mỗi lần kích hoạt log WARNING `INGESTION_SCALE_TRANSFORM`
# kèm symbol/date để truy vết. Hàm PURE (không phụ thuộc DB global) → unit-test
# ranh giới giá độc lập.
INGESTION_SCALE_THRESHOLD = 1000.0
INGESTION_ADV20_LIQUID = 50_000.0
_SCALE_PRICE_COLS = ("open", "high", "low", "close", "adj_close")


def _recent_price_level(conn, symbol, min_date, limit=60):
    """Dải close + thanh khoản gần nhất (PIT, trước min_date) của symbol trong DB.

    Trả về (has_any, max_close, avg_vol): has_any=True nếu có lịch sử.
    """
    rows = conn.execute(
        "SELECT MAX(close), AVG(volume) FROM ("
        " SELECT close, volume FROM daily_ohlcv WHERE symbol=? AND date<?"
        " ORDER BY date DESC LIMIT ?"
        ")",
        (symbol, min_date, limit),
    ).fetchone()
    if rows is None or rows[0] is None:
        return False, 0.0, 0.0
    return True, float(rows[0]), float(rows[1] or 0.0)


def _adv20_from_df(df_symbol):
    """ADV_20d ước lượng từ volume 20 phiên gần nhất trong DF của 1 symbol."""
    vols = df_symbol["volume"].dropna().tail(20).astype(float).tolist()
    if not vols:
        return 0.0
    return sum(vols) / len(vols)


def _ingestion_scale_guard(df, conn):
    """Ép daily_ohlcv về VND-scale tại biên nạp (write-time, không phụ thuộc read).

    Trả về df đã transform (copy). Áp dụng chi khi có cột symbol/close/volume.
    Thứ tự quyết định (xem docstring INVARIANT ở module):
      1. close>=1000                          → giữ (đã VND).
      2. symbol ∈ ACTIVE_UNIVERSE             → ×1000 (quy tắc cứng).
      3. DB-history gần đây có close≥1000     → ×1000 (VND-scale đã xác nhận).
      4. DB-history gần đây ĐỀU <1000         → penny thật, KHÔNG nhân (hard).
      5. UNKNOWN (không có lịch sử DB)        → ADV20≥50k mới nhân, ngược lại giữ.
    """
    from backtest.unified_system_replay import UNIVERSE as ACTIVE_UNIVERSE

    if "symbol" not in df.columns or "close" not in df.columns:
        return df
    df_out = df.copy()
    if "volume" not in df_out.columns:
        df_out["volume"] = 0.0
    date_col = "date" if "date" in df_out.columns else None
    price_cols = [c for c in _SCALE_PRICE_COLS if c in df_out.columns]

    universe_set = set(ACTIVE_UNIVERSE)
    min_date = None
    if date_col is not None:
        dates = df_out[date_col].dropna().astype(str).tolist()
        if dates:
            min_date = min(dates)

    for sym, grp in df_out.groupby("symbol", sort=False):
        idx = grp.index
        scale_needed = grp["close"].astype(float) < INGESTION_SCALE_THRESHOLD
        if not scale_needed.any():
            continue
        sym = str(sym)
        if sym in universe_set:
            force = True
            reason = "ACTIVE_UNIVERSE"
        elif min_date is not None:
            has_hist, hist_max, hist_avg_vol = _recent_price_level(conn, sym, min_date)
            if has_hist and hist_max >= INGESTION_SCALE_THRESHOLD and hist_avg_vol >= INGESTION_ADV20_LIQUID:
                # Lịch sử VND-scale (close≥1000) + thanh khoản thật (avg vol≥50k)
                # → đủ provenance là mã có scale VND, dữ liệu mới <1000 là lỗi.
                force = True
                reason = "DB_HISTORY_VND"
            elif has_hist and hist_max >= INGESTION_SCALE_THRESHOLD:
                # close≥1000 nhưng volume THẤP (A32 100 cp, APT/DCS ~0): giá cao do
                # thanh khoản mỏng — KHÔNG đủ bằng chứng là VND-scale → không nhân
                # (tránh FP với penny giá 800 lịch sử lẫn close 1400).
                force = False
                reason = "DB_HISTORY_HIGH_PRICE_LOW_VOL"
            elif has_hist:
                # Lịch sử gần đây ĐỀU <1000 → penny thật (provenance đủ) → HARD.
                force = False
                reason = "KNOWN_PENNY"
            else:
                # Chưa từng có trong DB → cần bằng chứng thanh khoản.
                adv = _adv20_from_df(grp)
                if adv >= INGESTION_ADV20_LIQUID:
                    force = True
                    reason = f"ADV20={adv:.0f}"
                else:
                    force = False
                    reason = "UNKNOWN_LOW_ADV"
        else:
            force = False
            reason = "NO_DATE"
        if force:
            for c in price_cols:
                df_out.loc[idx, c] = df_out.loc[idx, c].astype(float) * 1000.0
            n = int(scale_needed.sum())
            logger.warning(
                "INGESTION_SCALE_TRANSFORM symbol=%s rows=%d reason=%s (close<1000 → VND x1000)",
                sym,
                n,
                reason,
            )
    return df_out


def save_data_upsert(table_name, df, conn):
    """Lưu dữ liệu vào SQLite sử dụng cơ chế INSERT OR REPLACE (UPSERT)"""
    if df.empty:
        return

    # --- INGESTION SCALE GUARD (Data Contract VND-scale cho OHLCV) ---
    # Chặn nguồn trả "nghìn đồng" (KBS/VCI) ghi close<1000 như penny ngay tại
    # biên nạp — trước sanitization date để min_date PIT đúng. Không đụng bảng
    # khác (macro/regime): chỉ daily_ohlcv có hợp đồng VND.
    if table_name == "daily_ohlcv":
        df = _ingestion_scale_guard(df, conn)

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
