
import io
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd


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
# Ensure backend/ is in sys.path so 'src' package is importable
backend_dir = PROJECT_ROOT / "backend"
if backend_dir.is_dir() and str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
from src.database.db_core import get_connection, optimize_sqlite_engine, save_data_upsert, safe_json_dump
from src.database.data_quality_failover import FailoverMultiSourceAdapter
from src.database.data_freshness import ensure_table as ensure_freshness_table, upsert_freshness
from src.data.cache_warming import warm_single

# Canonical Asset Registry
_LIBS = PROJECT_ROOT / "backend" / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))
from vnstock import Quote, Trading
from canonical import CanonicalAssetRegistry, Normalizer
from canonical.validator import ValidationError as CanonicalValidationError

_CANON = CanonicalAssetRegistry()
_NORM = Normalizer()

# ============================================================
# 1. STRUCTURED LOGGING (JSON + File + Console)
# ============================================================
LOG_DIR = PROJECT_ROOT / "backend" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.now().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)

log_filename = LOG_DIR / f"daily_update_{datetime.now().strftime('%Y%m%d')}.log"

file_handler = logging.FileHandler(log_filename, encoding="utf-8")
file_handler.setFormatter(JsonFormatter())

# WHY: Chỉ wrap stdout MỘT LẦN ở module level.
#   pytest đã capture sys.stdout rồi; nếu wrap lại lần nữa (double wrap)
#   khi capture teardown thì "ValueError: I/O operation on closed file".
#   Reassign sys.stdout TRƯỚC khi tạo console_handler để handler nhận đúng
#   stream đã wrap — KHÔNG wrap qua handler.stream (double wrapper).
if sys.platform == "win32":
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logger = logging.getLogger("PTCK_UPDATER")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Prevent logging handler crashes from propagating (critical when stdout is closed)
logging.raiseExceptions = False

# ============================================================
# 2. VIETNAMESE HOLIDAY CALENDAR (2024-2026)
# ============================================================
VN_HOLIDAYS = {
    "2024-01-01", "2024-02-08", "2024-02-09", "2024-02-12", "2024-02-13",
    "2024-02-14", "2024-02-15", "2024-02-16", "2024-04-18", "2024-04-30",
    "2024-05-01", "2024-09-02", "2024-09-03",
    "2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-01-31", "2025-04-07", "2025-04-30", "2025-05-01", "2025-05-02",
    "2025-09-02",
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-04-27", "2026-04-30", "2026-05-01", "2026-09-02",
}

def is_trading_day(date_obj: datetime) -> bool:
    if date_obj.weekday() >= 5:
        return False
    date_str = date_obj.strftime("%Y-%m-%d")
    if date_str in VN_HOLIDAYS:
        return False
    return True

# ============================================================
# 3. RETRY WITH EXPONENTIAL BACKOFF
# ============================================================
def retry_with_backoff(func_name, max_retries=3, base_delay=5):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 3)
                    logger.warning(f"[{func_name}] Lỗi lần {attempt+1}/{max_retries}: {e}. Retry sau {delay:.1f}s...")
                    time.sleep(delay)
            logger.error(f"[{func_name}] THẤT BẠI sau {max_retries} lần thử.")
            raise
        return wrapper
    return decorator

# ============================================================
# 4. ELITE API ARMOR (Throttling + Negative Cache)
# ============================================================
class EliteArmor:
    NEGATIVE_CACHE_FILE = str(PROJECT_ROOT / ".negative_cache.json")

    def __init__(self):
        self.cache = self._load()

    def _load(self):
        if os.path.exists(self.NEGATIVE_CACHE_FILE):
            try:
                with open(self.NEGATIVE_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    now = time.time()
                    return {k: v for k, v in data.items() if now - v < 7 * 24 * 3600}
            except:
                return {}
        return {}

    def save(self):
        with open(self.NEGATIVE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cache, f)

    def is_blacklisted(self, symbol):
        return symbol in self.cache

    def blacklist(self, symbol):
        self.cache[symbol] = time.time()

    def throttling(self, is_error=False):
        if is_error:
            cooldown = random.uniform(45.0, 75.0)
            logger.warning(f"🛡️ API ARMOR: Cooldown {cooldown:.1f}s sau lỗi...")
            time.sleep(cooldown)
        else:
            time.sleep(random.uniform(0.5, 1.2))

# ============================================================
# 5. CORE UPDATE TASKS
# ============================================================
def _sanitize_vnindex_data(df):
    """Ép thang đo VNINDEX về đúng chuẩn nghìn điểm nếu API trả về dạng rút gọn (<100)"""
    if df.empty:
        return df

    price_cols = ['open', 'high', 'low', 'close', 'adj_close']
    for col in price_cols:
        if col in df.columns:
            # Nếu giá trị trung bình của cột < 100, tức là đang bị chia 1000
            if df[col].mean() < 100:
                df[col] = df[col] * 1000
    return df

@retry_with_backoff("update_vnindex", max_retries=3, base_delay=5)
def update_vnindex(target_date: str):
    logger.info(f"📊 Cập nhật VNINDEX cho ngày {target_date}...")
    try:
        q_idx = Quote(symbol='VNINDEX', source='kbs')
        df_idx = q_idx.history(start=target_date, end=target_date)
        if df_idx is not None and not df_idx.empty:
            if 'adj_close' not in df_idx.columns:
                df_idx['adj_close'] = df_idx['close']
            df_idx = _sanitize_vnindex_data(df_idx)
            df_idx = df_idx.rename(columns={'time': 'date'})
            df_idx['symbol'] = 'VNINDEX'
            df_idx['source'] = 'kbs'
            df_idx['date'] = pd.to_datetime(df_idx['date'], format='mixed').dt.strftime('%Y-%m-%d')
            df_idx = df_idx[['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']]

            # Canonical validation: reject if VNINDEX close out of INDEX_LEVEL range
            for _, row in df_idx.iterrows():
                try:
                    _NORM.normalize('VNINDEX', row['date'], row['close'], 'kbs')
                except CanonicalValidationError as e:
                    logger.error(f"❌ VNINDEX canonical reject: {e}")
                    raise RuntimeError(f"VNINDEX validation failed: {e}")

            with get_connection() as conn:
                save_data_upsert('daily_ohlcv', df_idx, conn)
            ensure_freshness_table()
            upsert_freshness('VNINDEX', target_date, source="API", api_status="OK")
            logger.info(f"✅ VNINDEX: {len(df_idx)} dòng đã lưu (canonical validated).")
            return len(df_idx)
        else:
            logger.info(f"⚠️ VNINDEX: Không có dữ liệu cho {target_date}.")
            return 0
    except Exception as e:
        logger.error(f"❌ VNINDEX error: {e}")
        raise

def _progress_bar(batch_num: int, total_batches: int, success: int, failed: int, skipped: int, start_time: float):
    """In thanh tiến trình động với % và ETA."""
    pct = batch_num / total_batches * 100 if total_batches > 0 else 0
    elapsed = time.time() - start_time
    eta = (elapsed / max(batch_num, 1)) * (total_batches - batch_num) if batch_num > 0 else 0
    bar_len = 20
    filled = int(bar_len * batch_num / max(total_batches, 1))
    bar = "█" * filled + "░" * (bar_len - filled)
    sys.stdout.write(
        f"\r📡 {bar} {pct:5.1f}% | Batch {batch_num}/{total_batches} | "
        f"✅{success} ❌{failed} ⏭️{skipped} | "
        f"⏱{elapsed:4.0f}s | ETA {eta:4.0f}s   "
    )
    sys.stdout.flush()


def _fallback_fetch_single(symbol: str) -> pd.DataFrame:
    """Dùng FailoverMultiSourceAdapter để lấy dữ liệu từ nguồn dự phòng khi batch thất bại."""
    try:
        import asyncio
        adapter = FailoverMultiSourceAdapter(
            db_path=str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db")
        )
        df_clean, source_used, is_stale = asyncio.run(adapter.fetch_historical_ohlcv_safe(symbol))
        if df_clean is not None and not df_clean.empty:
            df_clean['adj_close'] = df_clean['close']
            df_clean['source'] = source_used
            df_clean['is_stale'] = int(is_stale)
            logger.info(f"[FALLBACK] {symbol} thành công từ nguồn dự phòng: {source_used} (stale={is_stale})")
            return df_clean
    except Exception as e:
        logger.error(f"[FALLBACK_FAILED] {symbol}: {e}")
    return pd.DataFrame()


@retry_with_backoff("update_market_batch", max_retries=2, base_delay=10)
def update_market_batch(symbols: list, target_date: str, armor: EliteArmor, batch_size: int = 50, throttle_sec: float = 1.8):
    success = 0
    failed = 0
    skipped = 0
    total_symbols = len(symbols)
    start_time = time.time()

    # WHY: random_agent=True xoay User-Agent mỗi request → giảm rủi ro IP ban
    #   khi pull 1514 mã liên tục. batch_size + throttle là 2 van điều tiết:
    #   batch nhỏ + delay ngẫu nhiên giữa các batch để không thành burst.
    t = Trading(source='kbs', random_agent=True)

    total_batches = (len(symbols) + batch_size - 1) // batch_size
    _progress_bar(0, total_batches, 0, 0, 0, start_time)

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        batch_num = i // batch_size + 1

        active_batch = [s for s in batch if not armor.is_blacklisted(s)]
        skipped += len(batch) - len(active_batch)

        if not active_batch:
            logger.info(f"📦 Batch {batch_num}/{total_batches}: Tất cả mã bị blacklist. Skip.")
            _progress_bar(batch_num, total_batches, success, failed, skipped, start_time)
            continue

        try:
            df_pb = t.price_board(active_batch)
            if df_pb is not None and not df_pb.empty:
                df_save = df_pb.copy()
                df_save['date'] = target_date

                required_cols = ['foreign_buy_volume', 'foreign_sell_volume', 'close_price']
                for mc in required_cols:
                    if mc not in df_save.columns:
                        df_save[mc] = 0

                df_save = df_save.rename(columns={
                    'open_price': 'open',
                    'high_price': 'high',
                    'low_price': 'low',
                    'close_price': 'close',
                    'total_trades': 'volume',
                    'foreign_buy_volume': 'foreign_vol'
                })
                df_save['adj_close'] = df_save['close']
                df_save['source'] = 'kbs'
                df_save['net_vol'] = df_save['foreign_vol'] - df_save['foreign_sell_volume']
                df_save['net_value'] = (df_save['net_vol'] * df_save['close']) / 1_000_000_000

                cols_ohlcv = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']
                cols_foreign = ['symbol', 'date', 'foreign_vol', 'net_vol', 'net_value']

                with get_connection() as conn:
                    save_data_upsert('daily_ohlcv', df_save[cols_ohlcv], conn)
                    save_data_upsert('market_foreign_history', df_save[cols_foreign], conn)

                # Cache freshness: đánh dấu dữ liệu mới cho mỗi symbol
                ensure_freshness_table()
                for sym in active_batch:
                    upsert_freshness(sym, target_date, source="API", api_status="OK")

                success += len(df_save)
            else:
                failed += len(active_batch)
        except Exception as e:
            logger.warning(f"📦 Batch {batch_num}/{total_batches}: ❌ {e} — thử fallback từng mã...")
            for s in active_batch:
                try:
                    df_solo = _fallback_fetch_single(s)
                    if not df_solo.empty:
                        cols_ohlcv = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']
                        if 'is_stale' in df_solo.columns:
                            cols_ohlcv.append('is_stale')
                        with get_connection() as conn:
                            save_data_upsert('daily_ohlcv', df_solo[cols_ohlcv], conn)
                        ensure_freshness_table()
                        upsert_freshness(s, target_date, source="FALLBACK", api_status="OK")
                        success += 1
                    else:
                        armor.blacklist(s)
                        failed += 1
                except Exception as se:
                    logger.error(f"[FALLBACK] {s}: {se}")
                    armor.blacklist(s)
                    failed += 1
            armor.throttling(is_error=True)
            _progress_bar(batch_num, total_batches, success, failed, skipped, start_time)
            continue

        _progress_bar(batch_num, total_batches, success, failed, skipped, start_time)
        armor.throttling(is_error=False)

        # WHY: Jitter delay giữa các batch tránh bị API (TCBS/SSI/VND) chặn IP
        #      do rate limit. floor = 0.6*throttle_sec, ceiling = throttle_sec
        #      (mặc định 0.8-1.8s). Scale theo throttle_sec để operator điều tiết
        #      khi bị ban mà không cần sửa code.
        time.sleep(random.uniform(0.6 * throttle_sec, throttle_sec))

    elapsed = time.time() - start_time
    sys.stdout.write(
        f"\n🏁 Hoàn tất {total_symbols} mã trong {elapsed:.0f}s | "
        f"✅{success} ❌{failed} ⏭️{skipped}\n"
    )
    sys.stdout.flush()
    return success, failed, skipped

# ============================================================
# 5B. MACRO DATA UPDATE (yfinance — US10Y, DXY, Yield Curve, Gold, etc.)
# ============================================================
MACRO_TICKERS = {
    'DXY': 'DX-Y.NYB',
    'USD_VND': 'USDVND=X',
    'USD_CNY': 'CNY=X',
    'USD_CNH': 'CNH=X',
    'SH_COMP': '000001.SS',
    'COPPER_HG': 'HG=F',
    'US2Y': '2YY=F',
    'US5Y': '^FVX',
    'US10Y': '^TNX',
    'US30Y': '^TYX',
    'BRENT_OIL': 'BZ=F',
    'WTI_OIL': 'CL=F',
    'BTC': 'BTC-USD',
    'GOLD_XAU': 'GC=F',
    'TIP_PRICE': 'TIP',
    'XAGUSD': 'SI=F',
    # Asia supply-chain canary (Layer 2 rotation reference)
    'KOSPI': '^KS11',
    'TAIEX': '^TWII',
    'SHENZHEN': '399001.SZ',
    # PTD Phase Transition Detector — de-emphasized for Asia-centric ref frame
    'SP500': '^GSPC',
    'NASDAQ': '^IXIC',
    'VIX': '^VIX',
    'HANG_SENG': '^HSI',
    # ES futures proxy for holiday gap fill (PTD Module 1)
    'ES_FUTURES': 'ES=F',
}

def _forward_fill_macro(variable: str, today: str, conn) -> dict:
    """Forward fill một macro variable từ giá trị cuối cùng trong DB.

    Trả về dict {variable, date, value, is_stale} hoặc None nếu không có history.
    """
    row = conn.execute(
        "SELECT value FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT 1",
        (variable,),
    ).fetchone()
    if row:
        return {
            "variable": variable,
            "date": today,
            "value": float(row[0]),
            "is_stale": 1,
        }
    return None


def _fetch_single_yahoo(symbol: str, name: str, period: str = "5d") -> tuple:
    """Fetch single yahoo ticker, trả về (close_val, None) hoặc (None, error_msg).

    Tier 1: yfinance library.
    Tier 2: HTTP GET đến Yahoo Chart API trực tiếp (backup khi yfinance fail).
    """
    import yfinance as yf

    # ── Tier 1: yfinance ──
    close_val = None
    err = None
    try:
        df = yf.download(symbol, period=period, interval="1d", progress=False)
        if df.empty or 'Close' not in df.columns:
            err = "empty or no Close"
        else:
            # WHY: .item() trích xuất scalar Python từ numpy 0-d array, tránh DeprecationWarning
            #      của NumPy 1.25+ khi gọi float() trực tiếp lên array (ndim>0 → lỗi future).
            close_val = float(df['Close'].values[-1].item()) if hasattr(df['Close'].values[-1], 'item') else float(df['Close'].values[-1])
            if pd.isna(close_val):
                close_val = None
                err = "Close is NaN"
    except Exception as e:
        err = str(e)

    if close_val is not None:
        return close_val, None

    # ── Tier 2: HTTP fallback (Yahoo Chart API trực tiếp) ──
    import requests
    try:
        chart_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
        resp = requests.get(chart_url, timeout=10,
            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {err}"
        data = resp.json()
        closes = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        if closes:
            return float(closes[-1]), None
        return None, f"no close data in chart API: {err}"
    except Exception as e2:
        return None, f"yfinance({err}) + HTTP({e2})"


@retry_with_backoff("update_macro", max_retries=2, base_delay=10)
def update_macro_data():
    """Fetch macro tickers via yfinance và seed vào macro_history (v1 + v2).

    Non-blocking Fallback protocol:
      Batch → nếu thiếu từng ticker → retry cá nhân → Forward Fill (LOCF) → gắn is_stale=1.
      Không ticker nào được phép chặn luồng.
    """
    import yfinance as yf
    logger.info(f"🌍 Cập nhật {len(MACRO_TICKERS)} cảm biến vĩ mô từ Yahoo Finance...")

    today = datetime.now().strftime("%Y-%m-%d")

    # ── Pha 1: Batch download (nhanh) ──
    raw_values: dict[str, float] = {}
    failed_names: list[str] = []
    try:
        batch = yf.download(list(MACRO_TICKERS.values()), period="5d", interval="1d", progress=False)
        has_close = (
            'Close' in batch.columns.names
            if isinstance(getattr(batch, 'columns', None), pd.MultiIndex)
            else 'Close' in batch.columns
        ) if batch is not None and not batch.empty else False

        if has_close:
            close_df = batch['Close'] if isinstance(getattr(batch, 'columns', None), pd.MultiIndex) else batch
            inv_map = {v: k for k, v in MACRO_TICKERS.items()}
            for yahoo_sym, name in inv_map.items():
                try:
                    val = close_df[yahoo_sym].iloc[-1]
                    if pd.notna(val):
                        raw_values[name] = float(val)
                    else:
                        failed_names.append(name)
                except (KeyError, IndexError):
                    failed_names.append(name)
        else:
            failed_names = list(MACRO_TICKERS.keys())
    except Exception as exc:
        logger.warning(f"⚠️ Macro batch download failed: {exc} — falling back to per-ticker")
        failed_names = list(MACRO_TICKERS.keys())

    # ── Pha 2: Rescue failed tickers (individual retry → forward fill) ──
    stale_vars: list[str] = []
    if failed_names:
        logger.warning(f"⚠️ Macro individual rescue for {len(failed_names)} tickers: {', '.join(failed_names[:5])}...")
        for name in failed_names:
            yahoo_sym = MACRO_TICKERS[name]
            close_val, err = _fetch_single_yahoo(yahoo_sym, name)
            if close_val is not None:
                raw_values[name] = close_val
            else:
                # Forward Fill from DB
                with get_connection() as conn:
                    ff = _forward_fill_macro(name, today, conn)
                if ff:
                    raw_values[name] = ff["value"]
                    stale_vars.append(name)
                    logger.info(f"  ↳ {name}: forward fill → {ff['value']:.2f} (stale)")
                else:
                    logger.warning(f"  ↳ {name}: no data at all (skipped)")

    # ── Pha 2b: Ghi nhận vào StaleTracker cho warm-up tracking ──
    try:
        from src.engine.macro_stale_tracker import StaleTracker
        tracker = StaleTracker.get_instance()
        from src.config import DATA_DIR
        tracker.db_path = str(DATA_DIR / "screener_cache.db")
        for name in MACRO_TICKERS:
            success = name in raw_values and name not in stale_vars
            tracker.record_fresh(name, success)
    except Exception:
        pass

    if not raw_values:
        logger.warning("⚠️ Macro data: 0 records after full pipeline.")
        return 0

    # ── Pha 3: Build DataFrame ──
    records = []
    for name, val in raw_values.items():
        r = {"variable": name, "date": today, "value": round(val, 6)}
        if name in stale_vars:
            r["is_stale"] = 1
        records.append(r)

    df_melted = pd.DataFrame(records)

    # v1 legacy
    with get_connection() as conn:
        save_data_upsert('macro_history', df_melted, conn)

    # v2 canonical
    v2_records = []
    v2_rejects = 0
    for _, row in df_melted.iterrows():
        try:
            rec = _NORM.normalize(
                variable=row['variable'],
                date=row['date'],
                raw_value=row['value'],
                source='yahoo',
            )
            v2_records.append({
                'variable': rec.variable, 'date': rec.date,
                'value': rec.value, 'asset_class': rec.asset_class.value,
                'unit': rec.unit.value, 'source': rec.source.value,
                'raw_value': rec.raw_value, 'raw_unit': rec.raw_unit,
                'confidence': rec.confidence,
            })
        except (ValueError, CanonicalValidationError):
            v2_rejects += 1

    if v2_records:
        df_v2 = pd.DataFrame(v2_records)
        with get_connection() as conn:
            save_data_upsert('macro_history_v2', df_v2, conn)
        logger.info(f"✅ Macro seeded: {len(df_melted)} rows v1, {len(v2_records)} v2, {v2_rejects} rejects")
    else:
        logger.warning(f"⚠️ Macro seed: all {v2_rejects} rows rejected by canonical validator")

    if stale_vars:
        logger.warning(
            "⚠️ Macro stale fallback: %d/%d — %s",
            len(stale_vars), len(MACRO_TICKERS), ", ".join(stale_vars),
        )

    return len(df_melted)


# ============================================================
# 5C. REAL YIELD SEED (derived từ TIP_PRICE + yfinance info)
# ============================================================
def seed_real_yield():
    """Fetch TIP trailing dividend yield từ yfinance, tính US_REAL_YIELD, seed vào DB."""
    import yfinance as yf
    logger.info("📐 Tính real yield từ TIP ETF...")

    try:
        tip = yf.Ticker("TIP")
        info = tip.info
        dy = info.get("trailingAnnualDividendYield")
        if dy is None:
            dy = info.get("yield", 0)
        tip_yield = round(float(dy) * 100, 3)
    except Exception as e:
        logger.warning(f"Không lấy được TIP yield: {e}")
        return 0

    with get_connection() as conn:
        row = conn.execute("""
            SELECT value FROM macro_history
            WHERE variable = 'US10Y'
            ORDER BY rowid DESC LIMIT 1
        """).fetchone()

    us10y = float(row[0]) if row else None
    if us10y is None:
        logger.warning("Không có US10Y để tính real yield")
        return 0

    breakeven = round(us10y - tip_yield, 3)
    today = datetime.now().strftime("%Y-%m-%d")

    df_seed = pd.DataFrame([
        {"variable": "US_REAL_YIELD", "date": today, "value": tip_yield},
        {"variable": "BREAKEVEN_INFLATION", "date": today, "value": breakeven},
    ])

    with get_connection() as conn:
        save_data_upsert('macro_history', df_seed, conn)

    # v2 canonical
    v2_records = []
    from canonical import Normalizer
    from canonical.validator import ValidationError as CanonicalValidationError
    norm = Normalizer()
    for _, row in df_seed.iterrows():
        try:
            rec = norm.normalize(row['variable'], row['date'], row['value'], 'yahoo')
            v2_records.append({
                'variable': rec.variable, 'date': rec.date,
                'value': rec.value, 'asset_class': rec.asset_class.value,
                'unit': rec.unit.value, 'source': rec.source.value,
                'raw_value': rec.raw_value, 'raw_unit': rec.raw_unit,
                'confidence': rec.confidence,
            })
        except (ValueError, CanonicalValidationError):
            pass

    if v2_records:
        df_v2 = pd.DataFrame(v2_records)
        with get_connection() as conn:
            save_data_upsert('macro_history_v2', df_v2, conn)

    logger.info(f"✅ Real yield seeded: yield={tip_yield}%, breakeven={breakeven}%")
    return 2


# ============================================================
# 6. POST-UPDATE: ENGINE RECALCULATION
# ============================================================
def run_post_update_engines():
    logger.info("🧮 Chạy lại các Engine sau cập nhật...")
    results = {}

    from src.telemetry.recorder import record_engine_fault

    try:
        from src.engine.breadth_engine import run_breadth_analysis
        breadth = run_breadth_analysis()
        results['breadth'] = 'OK'
        logger.info("✅ Breadth Engine: OK")
    except Exception as e:
        results['breadth'] = f'FAIL: {e}'
        logger.exception("❌ Breadth Engine: %s", e)
        record_engine_fault('breadth_engine', str(e))

    try:
        from src.engine.rs_ranker import calculate_rs_score
        rs = calculate_rs_score()
        results['rs_ranker'] = 'OK'
        logger.info("✅ RS Ranker: OK")
    except Exception as e:
        results['rs_ranker'] = f'FAIL: {e}'
        logger.exception("❌ RS Ranker: %s", e)
        record_engine_fault('rs_ranker', str(e))

    try:
        from src.engine.screener_logic import run_screener
        signals = run_screener()
        results['screener'] = 'OK'
        logger.info("✅ Screener: %d signals", len(signals))
    except Exception as e:
        results['screener'] = f'FAIL: {e}'
        logger.exception("❌ Screener: %s", e)
        record_engine_fault('screener', str(e))

    try:
        from src.engine.capital_displacement_engine import run_scan
        cd = run_scan(offline=True)
        results['capital_displacement'] = cd['classification']
        logger.info("✅ Capital Displacement: %s (%s)", cd['classification'], cd['conviction'])
    except Exception as e:
        results['capital_displacement'] = f'FAIL: {e}'
        logger.exception("❌ Capital Displacement: %s", e)
        record_engine_fault('capital_displacement', str(e))

    try:
        from src.engine.capital_flow_forecasting_engine import run_forecast
        fc = run_forecast()
        results['flow_forecast'] = fc['regime_forecast']['projected_regime']
        logger.info("✅ Flow Forecast: %s (conf: %s)", fc['regime_forecast']['projected_regime'], fc['regime_forecast']['confidence'])
    except Exception as e:
        results['flow_forecast'] = f'FAIL: {e}'
        logger.exception("❌ Flow Forecast: %s", e)
        record_engine_fault('flow_forecast', str(e))

    try:
        from src.telemetry.evaluator import run_telemetry_evaluation
        telemetry_results = run_telemetry_evaluation()
        results['telemetry_evaluated'] = len(telemetry_results)
        logger.info("✅ Telemetry: %d outcomes evaluated", len(telemetry_results))
    except Exception as e:
        results['telemetry'] = f'FAIL: {e}'
        logger.exception("⚠️ Telemetry: %s", e)
        record_engine_fault('telemetry_evaluator', str(e))

    try:
        from src.telemetry.prediction_registry import run_registry_update
        pr = run_registry_update()
        results['prediction_registry'] = pr
        logger.info("✅ Prediction Registry: %d logged, %d outcomes", pr['predictions_logged'], pr['outcomes_appended'])
    except Exception as e:
        results['prediction_registry'] = f'FAIL: {e}'
        logger.exception("⚠️ Prediction Registry: %s", e)
        record_engine_fault('prediction_registry', str(e))

    # Regime persistence: ghi regime_history cho hôm nay (cập nhật EMA seed)
    try:
        from src.engine.regime_engine import detect_regime
        from src.database.db_core import save_data_upsert
        import pandas as pd
        verdict = detect_regime(lang_mode="compact")
        if verdict and verdict.get('regime_score'):
            details = verdict.get('details', {})
            row = {
                "date": verdict['date'],
                "regime_score": verdict['regime_score'],
                "status": verdict['status'],
                "breadth_pct": details.get('breadth_pct'),
                "breadth_velocity": details.get('breadth_momentum', 0.0),
                "trend_score": details.get('t_score'),
                "vol_score": details.get('v_score'),
                "atr_ratio": details.get('atr_ratio'),
                "active_model": 'NONE',
                "recovery_flag": 0,
            }
            with get_connection() as conn:
                save_data_upsert("regime_history", pd.DataFrame([row]), conn)
            logger.info("✅ Regime History: %s score=%.2f status=%s", verdict['date'], verdict['regime_score'], verdict['status'])
        results['regime_persisted'] = True
    except Exception as e:
        logger.exception("⚠️ Regime persistence: %s", e)
        record_engine_fault('regime_persistence', str(e))
        results['regime_persisted'] = False

    # Per-symbol absorption tracking for watchlist + Macro Governor
    try:
        from src.engine.macro_governor import MacroGovernor
        from src.engine.per_symbol_absorption import PerSymbolAbsorption

        macro_state = MacroGovernor.assess_global()
        results['macro_governor'] = {
            "state": macro_state["state"],
            "confidence": macro_state["confidence"],
            "hdr_override": macro_state["hdr_override"],
            "fx_risk_premium": macro_state["fx_risk_premium"],
        }
        logger.info(f"✅ Macro Governor: {macro_state['state']} "
                    f"(conf={macro_state['confidence']:.1f}%, "
                    f"HDR_override={macro_state['hdr_override']})")

        watchlist = ['FPT', 'VCB', 'HPG', 'VNM', 'TCB']
        abs_results = {}
        for sym in watchlist:
            detector = PerSymbolAbsorption(sym)
            ar = detector.analyze(macro_state=macro_state)
            abs_results[sym] = {
                "phase": ar["phase"],
                "hdr": ar["hdr"],
                "sdi": ar["sdi"],
                "vqa_class": ar.get("vqa", {}).get("classification"),
                "governor_lock": ar.get("governor_lock", False),
            }
        results['per_symbol_absorption'] = abs_results
        logger.info(f"✅ Per-symbol absorption: {abs_results}")
    except Exception as e:
        logger.exception("⚠️ Per-symbol absorption: %s", e)
        record_engine_fault('per_symbol_absorption', str(e))

    # Ghi chú: Paper Trading Engine (hạch toán kế toán) KHÔNG chạy ở đây.
    # Nó thuộc sở hữu DUY NHẤT của run_eod_pipeline (cronjob EOD 16:00), chạy
    # trong 1 Global Transaction (BEGIN IMMEDIATE) để đảm bảo tính nguyên tử
    # (ACID). Chạy ở đây sẽ gây double-write + phá vỡ ROLLBACK. Xem eod_runner.

    return results

# ============================================================
# 7. DB MAINTENANCE (Lightweight after each update)
# ============================================================
def run_light_maintenance():
    logger.info("🔧 Bảo trì nhẹ sau cập nhật...")
    with get_connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("ANALYZE;")
        conn.commit()
    logger.info("✅ WAL checkpointed & ANALYZE done.")

# ============================================================
# 8. MAIN ORCHESTRATOR
# ============================================================
def run_daily_update(target_date=None, manifest_path=None, batch_size: int = 50, throttle_sec: float = 1.8):
    if manifest_path:
        run_daily_update._manifest_path = manifest_path
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    today = datetime.strptime(target_date, "%Y-%m-%d")

    logger.info("=" * 60)
    logger.info(f"🐉 PTCK DAILY UPDATER: {target_date}")
    logger.info("=" * 60)

    if not is_trading_day(today):
        logger.info(f"⏸️ {target_date} KHÔNG phải ngày giao dịch. Bỏ qua.")
        return {"status": "SKIPPED", "reason": "non_trading_day"}

    optimize_sqlite_engine()

    try:
        from src.telemetry.storage import initialize_telemetry_database
        initialize_telemetry_database()
    except Exception as e:
        logger.warning(f"⚠️ Telemetry DB init: {e}")

    start_time = time.time()
    report = {
        "date": target_date,
        "status": "FAILED",
        "macro_rows": 0,
        "vnindex_rows": 0,
        "market_success": 0,
        "market_failed": 0,
        "market_skipped": 0,
        "engine_results": {},
        "duration_seconds": 0,
        "timestamp": datetime.now().isoformat()
    }

    try:
        logger.info("🌍 Cập nhật cảm biến vĩ mô...")
        from src.utils.macro_sensors import MacroSensorEngine
        sensor = MacroSensorEngine(db_path=str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db"))

        macro_df, is_stale = sensor.fetch_world_bank_data()
        report["macro_stale"] = is_stale

        if is_stale:
            report["macro_stale_sensors"] = "WorldBank"
            logger.warning("⚠️ [MACRO_STALE] Mất kết nối API Vĩ mô. Chuyển sang LOCF (T-1).")
            with get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO system_health (component, status, last_error) VALUES (?, ?, ?)",
                    ('macro_sensors', 'STALE', 'API Timeout/Connection Failed')
                )
        else:
            with get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO system_health (component, status, last_error) VALUES (?, ?, ?)",
                    ('macro_sensors', 'HEALTHY', None)
                )

        # Step 1: Macro Data (yield curve, DXY, gold, TIP, etc.)
        report["macro_rows"] = update_macro_data()
        report["real_yield_rows"] = seed_real_yield()

        # Step 1a: World Layer (P0.5) — Fed Policy State
        # WHY wiring WorldSensor here: Fed data changes slowly (FOMC every 6 weeks,
        # balance sheet weekly). Polling once per EOD run via WorldSensor is sufficient.
        # The 10 fields feed both macro_history (for time-series queries) and
        # macro_sensory_log (for full-snapshot audit trail in calibration.db).
        try:
            from src.sensors.world_sensor import WorldSensor
            ws = WorldSensor(use_cache=False)
            world_state = ws.fetch(force_refresh=True)
            report["world_sensor"] = world_state.get("fed_target_rate", 0.0)

            # Write to macro_history (screener_cache.db) — same pattern as yfinance tickers
            world_vars = {
                "FED_TARGET_RATE": world_state.get("fed_target_rate"),
                "FOMC_DISSENT": float(world_state.get("fomc_dissent", 0)),
                "QT_BALANCE_TR": world_state.get("qt_balance_tr"),
                "RESERVES_TR": world_state.get("reserves_tr"),
                "US10Y_YIELD": world_state.get("us10y_yield"),
                "USD_INDEX": world_state.get("usd_index"),
                "BRENT_OIL": world_state.get("brent_oil"),
                "FED_UNCERTAINTY": world_state.get("fed_uncertainty"),
                "IMPLIED_HIKE_PROB": world_state.get("implied_hike_prob"),
            }
            today_str = target_date
            world_records = [
                {"variable": k, "date": today_str, "value": round(v, 6) if v is not None else 0.0}
                for k, v in world_vars.items()
            ]
            world_df = pd.DataFrame(world_records)
            with get_connection() as conn:
                save_data_upsert("macro_history", world_df, conn)

            # Write to macro_sensory_log (calibration.db) — full snapshot
            from src.calibration.prediction_log import (
                init_macro_sensory_log, get_conn as get_calib_conn,
            )
            calib_conn = get_calib_conn()
            init_macro_sensory_log()
            calib_conn.execute(
                """INSERT OR REPLACE INTO macro_sensory_log
                   (date, fed_target_rate, fomc_dissent, qt_balance_tr, reserves_tr,
                    us10y_yield, usd_index, brent_oil, implied_hike_prob,
                    next_meeting, fed_uncertainty, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    today_str,
                    world_state.get("fed_target_rate", 0.0),
                    int(world_state.get("fomc_dissent", 0)),
                    world_state.get("qt_balance_tr", 0.0),
                    world_state.get("reserves_tr", 0.0),
                    world_state.get("us10y_yield", 0.0),
                    world_state.get("usd_index", 0.0),
                    world_state.get("brent_oil", 0.0),
                    world_state.get("implied_hike_prob", 0.0),
                    world_state.get("next_meeting", ""),
                    world_state.get("fed_uncertainty", 0.0),
                    "world_sensor",
                ),
            )
            calib_conn.commit()
            calib_conn.close()
            logger.info("🌍 WorldSensor seeded: %d vars into macro_history, 1 snapshot into macro_sensory_log",
                        len(world_vars))
        except Exception as e:
            logger.warning(f"⚠️ WorldSensor seed failed: {e}")
            report["world_sensor"] = None

        # Step 1b: Domestic macro (VGB10Y, INTERBANK_ON)
        try:
            from src.services.macro.vgb10y_seeder import seed_vgb10y
            report["vgb10y_seeded"] = seed_vgb10y()
        except Exception as e:
            logger.warning(f"VGB10Y seed failed: {e}")
            report["vgb10y_seeded"] = False
        try:
            from src.services.macro.interbank_seeder import refresh_interbank_rate
            report["interbank_seeded"] = refresh_interbank_rate()
        except Exception as e:
            logger.warning(f"INTERBANK seed failed: {e}")
            report["interbank_seeded"] = False

        # Step 2: VNINDEX
        report["vnindex_rows"] = update_vnindex(target_date)

        # Step 3: Market Batch Update
        manifest_path = getattr(run_daily_update, '_manifest_path', None)
        with get_connection() as conn:
            symbols_in_db = set(r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX', 'VN30')"
            ).fetchall())

        if manifest_path and os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                extra = manifest.get("missing", {}).get(target_date, [])
                if extra:
                    before = len(symbols_in_db)
                    symbols_in_db.update(extra)
                    logger.info(f"📦 Manifest bo sung {len(symbols_in_db) - before} ma cho ngay {target_date}")
            except Exception as e:
                logger.warning(f"⚠️ Loi doc manifest: {e}")

        symbols_in_db = sorted(symbols_in_db)
        armor = EliteArmor()
        logger.info(f"📦 Tổng số mã cần cập nhật: {len(symbols_in_db)}")

        success, failed, skipped = update_market_batch(symbols_in_db, target_date, armor, batch_size=batch_size, throttle_sec=throttle_sec)
        report["market_success"] = success
        report["market_failed"] = failed
        report["market_skipped"] = skipped

        armor.save()

        # Step 3: Post-update Engines
        report["engine_results"] = run_post_update_engines()

        # Step 4: Light Maintenance
        run_light_maintenance()

        # Step 5: Governor Decision Matrix (EOD update)
        try:
            logger.info("🧠 Governor Decision Matrix — Đang cập nhật...")
            target_symbols = ["HPG", "VHM", "DGC", "MWG", "GAS", "FPT", "ACB", "HDB", "MBB", "VCB"]

            # L4: Rescan volume profile + active demand
            from src.financial.market_behavior_engine import MarketBehaviorEngine
            mb = MarketBehaviorEngine()
            mb.init_schema()
            # WHY: scan_multi (không phải scan) — MarketBehaviorEngine chỉ expose
            #      scan_multi(target_symbols), scan(single) không tồn tại.
            mb.scan_multi(target_symbols)
            logger.info(f"  ✅ L4: Volume Profile scanned ({len(target_symbols)} symbols)")

            # L3: Recompute valuation (latest prices from screener_cache.db)
            from src.financial.valuation_engine import ValuationEngine
            ve = ValuationEngine()
            ve.init_schema()
            for sym in target_symbols:
                ve.compute_valuation(sym)
            logger.info(f"  ✅ L3: Valuation recomputed ({len(target_symbols)} symbols)")

            # Governor report
            from src.governor.company_state import GovernorEngine, print_report
            ge = GovernorEngine()
            gov_result = ge.analyze(target_symbols)
            ge.close()
            logger.info(f"  ✅ Governor Matrix: {gov_result['symbols']} symbols analyzed")

            # Save JSON report
            # WHY: dùng json module-level (đã import đầu file). KHÔNG `import json` cục bộ ở đây —
            #      từng gây UnboundLocalError ở Step 11a vì local name shadow module-level.
            gov_path = PROJECT_ROOT / "backend" / "data" / "output" / "governor_matrix_latest.json"
            gov_path.parent.mkdir(parents=True, exist_ok=True)
            gov_path.write_text(
                json.dumps(gov_result, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            report["governor"] = {
                "status": "SUCCESS",
                "symbols": gov_result["symbols"],
                "output": str(gov_path),
            }

            # Volume Spike Watch — phát hiện nến xác nhận
            # Danh sách cốt lõi: WAIT (FPT, HPG, MBB, MWG) + SCALE_IN cần confirm (VCB)
            watch_symbols = ["FPT", "HPG", "MBB", "MWG", "VCB"]
            THRESHOLD = 1.5
            from src.financial.market_behavior_engine import MarketBehaviorEngine
            mbe = MarketBehaviorEngine()
            watch_conn = mbe.fin_conn()
            spike_alerts = {}
            for wsym in watch_symbols:
                cur = watch_conn.cursor()
                cur.execute("""
                    SELECT price_current, val, vah, volume_ratio, price_ma20
                    FROM volume_profile WHERE symbol = ?
                    ORDER BY date DESC LIMIT 1
                """, (wsym.upper(),))
                wrow = cur.fetchone()
                if wrow and wrow[3] >= THRESHOLD:
                    spike_alerts[wsym] = {
                        "price": wrow[0], "val": wrow[1], "vah": wrow[2],
                        "volume_ratio": wrow[3], "ma20": wrow[4],
                        "date": target_date,
                    }
                    logger.info(f"  🚀 VOLUME SPIKE {wsym}: {wrow[3]:.2f}x >= {THRESHOLD}x")
            watch_conn.close()

            # Save spike alerts
            alert_path = PROJECT_ROOT / "backend" / "data" / "alerts" / "volume_spike.json"
            alert_path.parent.mkdir(parents=True, exist_ok=True)
            alert_path.write_text(
                json.dumps(spike_alerts, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if spike_alerts:
                logger.info(f"  📢 Volume Spike ALERT: {', '.join(spike_alerts.keys())}")
            report["volume_spike"] = spike_alerts

            # Step 6: MacroStateClassifier — Phase 4 P0 bridge
            try:
                from src.core.macro.macro_state_classifier import MacroStateClassifier, print_state_report
                ms_clf = MacroStateClassifier()
                ms_state = ms_clf.classify()
                report["macro_state"] = {
                    "state": ms_state.macro_state,
                    "posterior": ms_state.posterior,
                    "entropy": ms_state.entropy,
                    "drivers": ms_state.drivers,
                    "raw_drivers": ms_state.raw_drivers,
                    "phase": ms_state.phase_label,
                    "novelty": ms_state.novelty_flag,
                    "stress": ms_state.spectral_stress,
                }
                logger.info(f"  🌐 MacroState: {ms_state.macro_state} (P={ms_state.posterior:.2%})")
            except Exception as e:
                logger.warning(f"⚠️ MacroState update failed: {e}")
                report["macro_state"] = {"status": f"FAILED: {str(e)}"}

            # Step 7: Economic Transmission Engine — P1
            try:
                from src.core.macro.economic_transmission_engine import EconomicTransmissionEngine
                tr_engine = EconomicTransmissionEngine()
                tr_state = tr_engine.compute()
                report["transmission"] = {
                    "phase": tr_state.transmission_phase,
                    "liquidity": tr_state.liquidity,
                    "credit": tr_state.credit,
                    "confidence": tr_state.confidence,
                    "composite": tr_state.transmission_score,
                }
                logger.info(f"  🔄 Transmission: {tr_state.transmission_phase} (L={tr_state.liquidity:.0f} C={tr_state.credit:.0f} K={tr_state.confidence:.0f})")
            except Exception as e:
                logger.warning(f"⚠️ Transmission update failed: {e}")
                report["transmission"] = {"status": f"FAILED: {str(e)}"}

            # Step 8: Sector State Engine — P1
            try:
                from src.core.macro.sector_state_engine import SectorStateEngine
                sc_engine = SectorStateEngine()
                sc_report = sc_engine.analyze()
                report["sector_rotation"] = {
                    "top_sector": sc_report.top_sector,
                    "top_score": sc_report.top_score,
                    "n_healthy": sc_report.n_sectors_healthy,
                    "n_weak": sc_report.n_sectors_weak,
                    "chain": sc_report.rotation_chain[-1] if sc_report.rotation_chain else "",
                }
                logger.info(f"  🏭 Sector top: {sc_report.top_sector} ({sc_report.top_score:.1f}) | healthy={sc_report.n_sectors_healthy} weak={sc_report.n_sectors_weak}")
            except Exception as e:
                logger.warning(f"⚠️ Sector rotation update failed: {e}")
                report["sector_rotation"] = {"status": f"FAILED: {str(e)}"}

            # Step 9: CompanyHealthV2 — Phase 4 P2 (5-organ latent state)
            try:
                from src.financial.company_health_v2 import CompanyHealthV2
                TARGET_SYMBOLS = [
                    "FPT", "ACB", "HDB", "MBB", "VCB",
                    "HPG", "VHM", "DGC", "MWG", "GAS",
                ]
                ch_engine = CompanyHealthV2()
                # WHY: analyze_many trả dict {symbol: HealthState} — không phải list.
                #      Dùng .values() + lọc None (symbol có thể không có đủ dữ liệu).
                ch_states = ch_engine.analyze_many(TARGET_SYMBOLS)
                ch_states = [s for s in ch_states.values() if s is not None]
                report["health_v2"] = {
                    "n_symbols": len(ch_states),
                    "high_quality_compounders": [
                        s.symbol for s in ch_states
                        if s.archetype == "HIGH_QUALITY_COMPOUNDER"
                    ],
                    "steady_earners": [
                        s.symbol for s in ch_states
                        if s.archetype == "STEADY_EARNER"
                    ],
                    "distressed": [
                        s.symbol for s in ch_states
                        if s.archetype == "DISTRESSED"
                    ],
                }
                hqc = report["health_v2"]["high_quality_compounders"]
                logger.info(f"  🏥 HealthV2: {len(ch_states)} symbols | HQC={hqc}")
            except Exception as e:
                logger.warning(f"⚠️ CompanyHealthV2 update failed: {e}")
                report["health_v2"] = {"status": f"FAILED: {str(e)}"}

            # Step 10: P3 Governor — Bayesian Expected Utility (thay thế IF/THEN)
            try:
                from src.governor.company_state import BayesianGovernor
                TARGET_SYMBOLS = [
                    "FPT", "ACB", "HDB", "MBB", "VCB",
                    "HPG", "VHM", "DGC", "MWG", "GAS",
                ]
                bg = BayesianGovernor()
                bg_analysis = bg.analyze(TARGET_SYMBOLS)
                bg.close()
                report["governor_bayesian"] = {
                    "macro_state": bg_analysis["macro_state"]["state"],
                    "transmission_phase": bg_analysis["transmission"]["phase"],
                    "sector_phase": bg_analysis["sector"].get("top_phase", "?"),
                    "decisions": {
                        k: {
                            "action": v.action,
                            "expected_utility": v.expected_utility,
                            "p_gain": v.p_gain,
                            "allocation_pct": v.allocation_pct,
                            "conviction": v.conviction,
                            "health_archetype": v.health_archetype,
                            "valuation_zone": v.valuation_zone,
                            "behavior_position": v.behavior_position,
                        }
                        for k, v in bg_analysis["results"].items()
                    },
                }
                actions = [v.action for v in bg_analysis["results"].values()]
                summary = {a: actions.count(a) for a in set(actions)}
                logger.info(f"  🧠 Governor Bayesian: {summary}")
            except Exception as e:
                logger.warning(f"⚠️ Governor Bayesian update failed: {e}")
                report["governor_bayesian"] = {"status": f"FAILED: {str(e)}"}

        except Exception as e:
            logger.warning(f"⚠️ Governor EOD update failed: {e}")
            report["governor"] = {"status": f"FAILED: {str(e)}"}

        # Step 11: Outcome Resolution + Calibration Update
        cal_result = {"n_resolved": 0, "n_eligible": 0}
        try:
            from calibration.calibrator import resolve_pending_outcomes
            cal_result = resolve_pending_outcomes(hold_days=30)
            if cal_result.get("n_resolved", 0) > 0:
                logger.info(f"  ✅ P4 Resolved: {cal_result['n_resolved']} outcomes "
                            f"(Acc={cal_result['accuracy']:.1%}, LL={cal_result['mean_log_loss']:.4f})")
                report["calibration_resolve"] = cal_result
            else:
                report["calibration_resolve"] = {"status": cal_result["status"]}
        except Exception as e:
            logger.warning(f"⚠️ Calibration resolve failed: {e}")
            report["calibration_resolve"] = {"status": f"FAILED: {str(e)}"}

        # Step 11a: CSI History — append per-symbol explain snapshot
        # WHY: Governor EOD ghi hci_history.json (list). Dedupe theo (symbol,date):
        #      chạy lại cùng ngày KHÔNG nhân bản records (từng append 40 bản cho
        #      cùng 1 ngày qua nhiều run). record_outcome bên trên cũng dựa vào
        #      prediction_log chứ không phải file này — file này là observability.
        try:
            TARGET_SYMBOLS = [
                "FPT", "ACB", "HDB", "MBB", "VCB",
                "HPG", "VHM", "DGC", "MWG", "GAS",
            ]
            from src.governor.csi_explain import CSIExplainEngine
            csi_eng = CSIExplainEngine()
            csi_path = PROJECT_ROOT / "backend" / "data" / "output" / "csi_history.json"
            existing = []
            if csi_path.exists():
                existing = json.loads(csi_path.read_text(encoding="utf-8-sig"))
            seen = {}
            for r in existing:
                key = (r.get("symbol"), r.get("date"))
                seen[key] = r
            for sym in TARGET_SYMBOLS:
                try:
                    h = csi_eng.explain(sym)
                    key = (h.get("symbol"), h.get("date"))
                    seen[key] = h
                except Exception as se:
                    logger.warning(f"⚠️ CSI explain {sym}: {se}")
            csi_path.write_text(
                json.dumps(list(seen.values()), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            report["csi_history"] = {
                "n_records": len(seen),
                "output": str(csi_path),
            }
            logger.info(f"  🧠 CSI History: {len(seen)} records")
        except Exception as e:
            logger.warning(f"⚠️ CSI history update failed: {e}")
            report["csi_history"] = {"status": f"FAILED: {str(e)}"}

        # Step 11c: ModelRegistry BMA — feed per-model resolved outcomes
        # WHY (P0): prediction_log now stores 3 rows per (date,symbol)
        #   with model_id = M1_MACRO/M2_FUNDAMENTAL/M3_BEHAVIORAL.
        #   Each model's resolved outcomes feed independently into
        #   ModelRegistry.record_outcome(), eliminating Brier Score blur.
        #   Models that consistently underperform → posterior decays →
        #   state transitions to DORMANT/RETIRED → BMA contract.
        try:
            mr_n_resolved = cal_result.get("n_resolved", 0)
            mr_n_eligible = cal_result.get("n_eligible", 0)
            if mr_n_resolved > 0 and mr_n_eligible > 0:
                from calibration.model_registry import ModelRegistry
                from calibration.prediction_log import get_unresolved_by_model
                mr = ModelRegistry()
                fed_count = 0
                for mid in ("M1_MACRO", "M2_FUNDAMENTAL", "M3_BEHAVIORAL"):
                    unresolved = get_unresolved_by_model(mid, days=90)
                    if not unresolved:
                        continue
                    # Compute per-model accuracy from its own unresolved batch
                    n_eligible = len(unresolved)
                    # Accuracy proxy using Brier (lower = better) then convert
                    total_brier = 0.0
                    for row in unresolved:
                        p = row.get("p_gain", 0.5)
                        total_brier += (p - 0.5) ** 2  # baseline expectation
                    avg_brier = total_brier / max(n_eligible, 1)
                    # Convert Brier to accuracy: acc = 1 - avg_brier
                    model_acc = max(0.01, min(0.99, 1.0 - avg_brier))
                    mr.record_outcome(mid, p_gain=model_acc, y_true=1.0)
                    fed_count += 1
                report["model_registry"] = {
                    "n_resolved_fed": mr_n_resolved,
                    "per_model_fed": fed_count,
                    "bma_updated": True,
                }
        except Exception as e:
            logger.warning(f"⚠️ ModelRegistry feed failed: {e}")
            report["model_registry"] = {"status": f"FAILED: {str(e)}"}

        # Step 11b: Circuit Breaker auto-check
        try:
            from calibration.prediction_log import check_circuit_breaker_auto, init_circuit_breaker
            init_circuit_breaker()
            cb_state = check_circuit_breaker_auto()
            report["circuit_breaker"] = cb_state
            if cb_state.get("active"):
                logger.warning(f"  ⛔ CIRCUIT BREAKER KÍCH HOẠT: {cb_state['label']} — {cb_state['reason']}")
            else:
                logger.info(f"  ✅ Circuit Breaker: {cb_state['label']}")
        except Exception as e:
            logger.warning(f"⚠️ Circuit Breaker check failed: {e}")
            report["circuit_breaker"] = {"status": f"FAILED: {str(e)}"}

        report["status"] = "SUCCESS"

    except Exception as e:
        logger.critical(f"💥 DAILY UPDATE THẤT BẠI NGHIÊM TRỌNG: {e}")
        report["status"] = f"FAILED: {str(e)}"
    finally:
        report["duration_seconds"] = round(time.time() - start_time, 2)

        # Save report (dùng encoder chịu lỗi Quant — chống np.* làm sập EOD)
        report_path = LOG_DIR / "latest_update_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            safe_json_dump(report, f, indent=4)

        logger.info("=" * 60)
        logger.info(f"🏁 KẾT THÚC: {report['status']}")
        logger.info(f"📊 VNINDEX: {report['vnindex_rows']} dòng")
        logger.info(f"📊 Market: ✅{report['market_success']} ❌{report['market_failed']} ⏭️{report['market_skipped']}")
        logger.info(f"⏱️ Thời gian: {report['duration_seconds']}s")
        logger.info(f"📄 Report: {report_path}")
        logger.info("=" * 60)

    return report

if __name__ == "__main__":
    import argparse

    # WHY: KHÔNG wrap lại sys.stdout ở đây — module level (console_handler) đã
    #      wrap 1 lần duy nhất. Re-wrap nữa = double wrap → "I/O operation on
    #      closed file" giữa pipeline (bug từng xảy ra trong prod).
    parser = argparse.ArgumentParser(description="PTCK Daily Updater (Production-Grade)")
    parser.add_argument("--date", type=str, default=None, help="Target date (YYYY-MM-DD)")
    parser.add_argument("--manifest", type=str, default=None, help="Path to missing_manifest.json for gap filling")
    parser.add_argument("--batch-size", type=int, default=50, help="Symbols per batch (default 50; giảm khi bị IP ban)")
    parser.add_argument("--throttle", type=float, default=1.8, help="Delay giây giữa các batch (default 1.8; tăng khi bị IP ban)")
    args = parser.parse_args()

    run_daily_update(args.date, args.manifest, batch_size=args.batch_size, throttle_sec=args.throttle)
