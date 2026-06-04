
import os
import sys
import io
import time
import json
import random
import logging
import pandas as pd
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
# Ensure backend/ is in sys.path so 'src' package is importable
backend_dir = PROJECT_ROOT / "backend"
if backend_dir.is_dir() and str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
import src.config
from vnstock import Trading, Quote, Listing
from src.database.db_core import get_connection, save_data_upsert, optimize_sqlite_engine
# Canonical Asset Registry
_LIBS = PROJECT_ROOT / "backend" / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))
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

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
if sys.platform == "win32":
    console_handler.stream = io.TextIOWrapper(console_handler.stream.buffer, encoding='utf-8', line_buffering=True)

logger = logging.getLogger("PTCK_UPDATER")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

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
@retry_with_backoff("update_vnindex", max_retries=3, base_delay=5)
def update_vnindex(target_date: str):
    logger.info(f"📊 Cập nhật VNINDEX cho ngày {target_date}...")
    try:
        q_idx = Quote(symbol='VNINDEX', source='kbs')
        df_idx = q_idx.history(start=target_date, end=target_date)
        if df_idx is not None and not df_idx.empty:
            df_idx = df_idx.rename(columns={'time': 'date'})
            df_idx['symbol'] = 'VNINDEX'
            df_idx['source'] = 'kbs'
            if 'adj_close' not in df_idx.columns:
                df_idx['adj_close'] = df_idx['close']
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
            logger.info(f"✅ VNINDEX: {len(df_idx)} dòng đã lưu (canonical validated).")
            return len(df_idx)
        else:
            logger.info(f"⚠️ VNINDEX: Không có dữ liệu cho {target_date}.")
            return 0
    except Exception as e:
        logger.error(f"❌ VNINDEX error: {e}")
        raise

@retry_with_backoff("update_market_batch", max_retries=2, base_delay=10)
def update_market_batch(symbols: list, target_date: str, armor: EliteArmor):
    batch_size = 50
    success = 0
    failed = 0
    skipped = 0

    t = Trading(source='kbs')

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(symbols) + batch_size - 1) // batch_size

        active_batch = [s for s in batch if not armor.is_blacklisted(s)]
        skipped += len(batch) - len(active_batch)

        if not active_batch:
            logger.info(f"📦 Batch {batch_num}/{total_batches}: Tất cả mã bị blacklist. Skip.")
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

                success += len(df_save)
                logger.info(f"📦 Batch {batch_num}/{total_batches}: ✅ {len(df_save)} mã.")
            else:
                logger.warning(f"📦 Batch {batch_num}/{total_batches}: ⚠️ No data.")
                failed += len(active_batch)
        except Exception as e:
            logger.error(f"📦 Batch {batch_num}/{total_batches}: ❌ {e}")
            failed += len(active_batch)
            for s in active_batch:
                armor.blacklist(s)
            armor.throttling(is_error=True)
            continue

        armor.throttling(is_error=False)

    return success, failed, skipped

# ============================================================
# 6. POST-UPDATE: ENGINE RECALCULATION
# ============================================================
def run_post_update_engines():
    logger.info("🧮 Chạy lại các Engine sau cập nhật...")
    results = {}

    try:
        from src.engine.breadth_engine import run_breadth_analysis
        breadth = run_breadth_analysis()
        results['breadth'] = 'OK'
        logger.info(f"✅ Breadth Engine: OK")
    except Exception as e:
        results['breadth'] = f'FAIL: {e}'
        logger.error(f"❌ Breadth Engine: {e}")

    try:
        from src.engine.rs_ranker import calculate_rs_score
        rs = calculate_rs_score()
        results['rs_ranker'] = 'OK'
        logger.info(f"✅ RS Ranker: OK")
    except Exception as e:
        results['rs_ranker'] = f'FAIL: {e}'
        logger.error(f"❌ RS Ranker: {e}")

    try:
        from src.engine.screener_logic import run_screener
        signals = run_screener()
        results['screener'] = 'OK'
        logger.info(f"✅ Screener: {len(signals)} signals")
    except Exception as e:
        results['screener'] = f'FAIL: {e}'
        logger.error(f"❌ Screener: {e}")

    try:
        from src.engine.capital_displacement_engine import run_scan
        cd = run_scan()
        results['capital_displacement'] = cd['classification']
        logger.info(f"✅ Capital Displacement: {cd['classification']} ({cd['conviction']})")
    except Exception as e:
        results['capital_displacement'] = f'FAIL: {e}'
        logger.error(f"❌ Capital Displacement: {e}")

    try:
        from src.engine.capital_flow_forecasting_engine import run_forecast
        fc = run_forecast()
        results['flow_forecast'] = fc['regime_forecast']['projected_regime']
        logger.info(f"✅ Flow Forecast: {fc['regime_forecast']['projected_regime']} (conf: {fc['regime_forecast']['confidence']})")
    except Exception as e:
        results['flow_forecast'] = f'FAIL: {e}'
        logger.error(f"❌ Flow Forecast: {e}")

    try:
        from src.telemetry.evaluator import run_telemetry_evaluation
        telemetry_results = run_telemetry_evaluation()
        results['telemetry_evaluated'] = len(telemetry_results)
        logger.info(f"✅ Telemetry: {len(telemetry_results)} outcomes evaluated")
    except Exception as e:
        results['telemetry'] = f'FAIL: {e}'
        logger.warning(f"⚠️ Telemetry: {e}")

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
def run_daily_update(target_date=None):
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
        "vnindex_rows": 0,
        "market_success": 0,
        "market_failed": 0,
        "market_skipped": 0,
        "engine_results": {},
        "duration_seconds": 0,
        "timestamp": datetime.now().isoformat()
    }

    try:
        # Step 1: VNINDEX
        report["vnindex_rows"] = update_vnindex(target_date)

        # Step 2: Market Batch Update
        with get_connection() as conn:
            symbols_in_db = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX', 'VN30')"
            ).fetchall()]

        armor = EliteArmor()
        logger.info(f"📦 Tổng số mã cần cập nhật: {len(symbols_in_db)}")

        success, failed, skipped = update_market_batch(symbols_in_db, target_date, armor)
        report["market_success"] = success
        report["market_failed"] = failed
        report["market_skipped"] = skipped

        armor.save()

        # Step 3: Post-update Engines
        report["engine_results"] = run_post_update_engines()

        # Step 4: Light Maintenance
        run_light_maintenance()

        report["status"] = "SUCCESS"

    except Exception as e:
        logger.critical(f"💥 DAILY UPDATE THẤT BẠI NGHIÊM TRỌNG: {e}")
        report["status"] = f"FAILED: {str(e)}"
    finally:
        report["duration_seconds"] = round(time.time() - start_time, 2)

        # Save report
        report_path = LOG_DIR / "latest_update_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)

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
    import io

    # Fix Windows console encoding
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description="PTCK Daily Updater (Production-Grade)")
    parser.add_argument("--date", type=str, default=None, help="Target date (YYYY-MM-DD)")
    args = parser.parse_args()

    run_daily_update(args.date)
