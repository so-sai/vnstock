import sys
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
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import io
import logging
import random
import time
from datetime import datetime

import pandas as pd
from vnstock import Quote

from src.database.db_core import get_connection, save_data_upsert
from src.engine.data_quality import danh_gia_chat_luong_du_lieu

# ── Encoding ──────────────────────────────────────────────
if sys.platform == "win32":
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
LOG_DIR = PROJECT_ROOT / "backend" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("PTCK_BACKFILL")
logger.setLevel(logging.INFO)

_fh = logging.FileHandler(LOG_DIR / f"backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding='utf-8')
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)

_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_ch)

# ── Tham số mặc định ──────────────────────────────────────
NGUONG_PHIEN = 200
MOC_THOI_GIAN_MAC_DINH = "2021-01-01"
BATCH_SIZE = 30
# WIN11 BLACK-SCREEN BUG (2026-08-01):
#   Increase throttle delay to avoid concurrent GPU access
#   when multiple scheduled tasks run on the same machine.
#   Also prevents VCI rate-limit triggers from overlapping.
THROTTLE_MIN = 1.5
THROTTLE_MAX = 3.0
COOLDOWN_LOI = 30.0
TOI_DA_THU_LAI = 3
# Deadline cứng mỗi source trong _fetch_lich_su (giây). VCI GraphQL mặc định
# timeout=30s/request → tuần tự 751 mã sẽ treo vô hạn khi bị rate-limit.
# Tăng từ 12.0s → 25.0s để hỗ trợ mã UPCoM/thanh khoản thấp phản hồi chậm.
VCI_TIMEOUT = 25.0
# Multi-Source Fallback: thử VCI trước, nếu thất bại thì chuyển sang
# TCBS → DNSE → KBS. Thứ tự ưu tiên theo tốc độ phản hồi và chất lượng dữ liệu.
FALLBACK_SOURCES = ['vci', 'tcbs', 'dnse', 'kbs']
# Sau bao nhiêu lần timeout liên tiếp thì recreate HTTPS session pool
MAX_CONSECUTIVE_TIMEOUTS = 3


def _lay_danh_sach_can_backfill() -> list:
    """Trả về danh sách symbol có số phiên < ngưỡng, cần backfill."""
    dg = danh_gia_chat_luong_du_lieu()
    if dg.empty:
        return []
    thieu = dg[dg['so_phien'] < NGUONG_PHIEN]
    # Chỉ backfill mã từng có dữ liệu trước đây (có ngày_min sau mốc)
    # Loại mã < 20 phiên (mã mới thực sự)
    can_bf = thieu[thieu['so_phien'] >= 20].copy()
    ds = sorted(can_bf['symbol'].tolist())
    logger.info(f"Tìm thấy {len(ds)} mã cần backfill (≥20 phiên hiện có, <{NGUONG_PHIEN} phiên).")
    logger.info(f"  {len(thieu) - len(ds)} mã khác có <20 phiên (bỏ qua — có thể là mã mới thực sự).")
    return ds


def _lay_ngay_hien_tai(symbol: str) -> str:
    """Lấy ngày giao dịch gần nhất của symbol trong DB."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM daily_ohlcv WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row[0] if row and row[0] else None


def _fetch_lich_su(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Gọi API Quote.history() để lấy dữ liệu lịch sử.

    Multi-Source Fallback (VCI → TCBS → DNSE → KBS):
    Khi VCI bị rate-limit hoặc timeout, tự động chuyển sang nguồn thay thế.
    Mỗi source chạy trong thread riêng, chờ tối đa VCI_TIMEOUT giây rồi bỏ qua.

    WIN11 BLACK-SCREEN BUG (2026-08-01):
      --disable-gpu trong Playwright prevents GPU context acquisition
      when monitor is off (Modern Standby S0 → GPU D3 cold → TDR timeout).
      This function does NOT use Playwright; it uses HTTP APIs only.
      However, the throttle delay (1.5–3.0s) prevents concurrent GPU
      access from overlapping scheduled tasks.
    """
    import threading

    df = pd.DataFrame()
    consecutive_timeouts = 0
    for source in FALLBACK_SOURCES:
        box = {}

        def _run(src=source):
            try:
                q = Quote(symbol=symbol, source=src)
                box["df"] = q.history(start=start, end=end, pause=0)
            except Exception as e:
                box["err"] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(VCI_TIMEOUT)
        if t.is_alive():
            consecutive_timeouts += 1
            logger.warning(
                f"_fetch_lich_su: {symbol} {source} TIMEOUT >{VCI_TIMEOUT}s — bỏ qua"
            )
            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                logger.warning(
                    f"_fetch_lich_su: {symbol} {consecutive_timeouts} consecutive timeouts — "
                    f"recreate HTTPS session pool"
                )
                # Force garbage collection to release stale HTTPS connections
                import gc
                gc.collect()
                consecutive_timeouts = 0
            continue
        consecutive_timeouts = 0
        if "err" in box:
            df = pd.DataFrame()
            continue
        df = box.get("df")
        if df is not None and not df.empty:
            df = df.copy()
            df['source'] = source
            break
        df = pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    if 'adj_close' not in df.columns and 'close' in df.columns:
        df['adj_close'] = df['close']
    # Chuẩn hóa tên cột
    rename_map = {}
    for col in df.columns:
        if col == 'time':
            rename_map['time'] = 'date'
        elif col == 'open_price':
            rename_map['open_price'] = 'open'
        elif col == 'high_price':
            rename_map['high_price'] = 'high'
        elif col == 'low_price':
            rename_map['low_price'] = 'low'
        elif col == 'close_price':
            rename_map['close_price'] = 'close'
        elif col == 'total_trades':
            rename_map['total_trades'] = 'volume'
    if rename_map:
        df = df.rename(columns=rename_map)
    # Đảm bảo các cột bắt buộc
    for col in ['open', 'high', 'low', 'close', 'adj_close']:
        if col not in df.columns:
            df[col] = 0.0
    if 'volume' not in df.columns:
        df['volume'] = 0
    df['symbol'] = symbol
    # Chuẩn hóa ngày
    df['date'] = pd.to_datetime(df['date'], format='mixed').dt.strftime('%Y-%m-%d')
    cols = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'source']
    df = df[[c for c in cols if c in df.columns]]
    return df


def _kiem_tra_symbol_co_san(symbol: str) -> bool:
    """Kiểm tra symbol có tồn tại trong DB không (đã từng seed)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM daily_ohlcv WHERE symbol = ? LIMIT 1", (symbol,)
        ).fetchone()
        return row is not None


def backfill(symbols: list = None,
             start: str = None,
             end: str = None,
             dry_run: bool = False,
             verbose: bool = True) -> dict:
    """
    Engine khôi phục dữ liệu lịch sử cho các mã thiếu dữ liệu.

    Args:
        symbols: Danh sách symbol cần backfill (None = tự động phát hiện)
        start: Ngày bắt đầu (YYYY-MM-DD, mặc định 2021-01-01)
        end: Ngày kết thúc (YYYY-MM-DD, mặc định hôm nay)
        dry_run: False = ghi vào DB, True = chỉ xem không ghi
        verbose: In chi tiết từng mã

    Returns:
        Dict chứa thống kê kết quả
    """
    print("\n" + "=" * 70)
    print("  ═══ ENGINE KHÔI PHỤC DỮ LIỆU LỊCH SỬ (BACKFILL) ═══")
    print("=" * 70)

    if dry_run:
        print("  🔷 Chế độ: DRY RUN — sẽ không ghi vào database")
    else:
        print("  🔷 Chế độ: THỰC THI — sẽ ghi dữ liệu vào database")
    print()

    # 1. Xác định danh sách cần backfill
    tu_dong = False
    if symbols is None:
        symbols = _lay_danh_sach_can_backfill()
        tu_dong = True
        if not symbols:
            print("  ✅ Không có mã nào cần backfill.")
            print("=" * 70)
            return {'total': 0, 'success': 0, 'failed': 0, 'dry_run': dry_run}

    if start is None:
        start = MOC_THOI_GIAN_MAC_DINH
    if end is None:
        end = datetime.now().strftime('%Y-%m-%d')

    print(f"  📅 Phạm vi thời gian: {start} → {end}")
    print(f"  🎯 Số lượng mã: {len(symbols)}")
    if tu_dong:
        logger.info(f"Mục tiêu: {len(symbols)} mã tự động phát hiện.")
    else:
        logger.info(f"Mục tiêu: {len(symbols)} mã theo danh sách chỉ định.")

    if dry_run:
        print("\n  📋 DANH SÁCH MÃ SẼ BACKFILL (DRY RUN):")
        print(f"  {'Mã':<8} {'Phiên hiện':>10} {'Ngày cuối':>12}")
        print(f"  {'─'*8} {'─'*10} {'─'*12}")
        dg = danh_gia_chat_luong_du_lieu()
        for s in symbols:
            row = dg[dg['symbol'] == s]
            if not row.empty:
                r = row.iloc[0]
                print(f"  {s:<8} {r['so_phien']:>10} {r['ngay_max']:>12}")
        print(f"\n  → {len(symbols)} mã sẽ được backfill từ {start} đến {end}.")
        print("    (chạy lại với --apply hoặc bỏ --dry-run để thực thi)")
        print(f"\n  ⏱  Thời gian ước tính: ~{len(symbols) * 3} giây (chưa tính cooldown)")
        print("=" * 70)
        return {'total': len(symbols), 'success': 0, 'failed': 0, 'dry_run': True}

    # 2. THỰC THI BACKFILL
    print()
    tong = len(symbols)
    thanh_cong = 0
    that_bai = 0
    bo_qua = 0
    tong_dong_moi = 0
    blacklist = {}
    source_counter = {s: 0 for s in FALLBACK_SOURCES}
    bat_dau = time.time()

    for idx, symbol in enumerate(symbols, 1):
        # Kiểm tra blacklist
        if symbol in blacklist and time.time() - blacklist[symbol] < 3600:
            logger.info(f"  [{idx}/{tong}] {symbol}: ⏭ Bỏ qua (blacklist tạm thời)")
            bo_qua += 1
            continue

        if verbose:
            print(f"\r  [{idx}/{tong}] {symbol}...", end='', flush=True)

        for lan_thu in range(TOI_DA_THU_LAI):
            try:
                df = _fetch_lich_su(symbol, start, end)

                if df.empty:
                    if verbose:
                        print(f"\r  [{idx}/{tong}] {symbol}: ⚠ Không có dữ liệu lịch sử từ API")
                    that_bai += 1
                    blacklist[symbol] = time.time()
                    break

                # Đếm dòng hiện tại trong DB
                with get_connection() as conn:
                    dong_truoc = conn.execute(
                        "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol = ?", (symbol,)
                    ).fetchone()[0]

                # Upsert tất cả dữ liệu từ API (INSERT OR REPLACE xử lý trùng lặp)
                try:
                    with get_connection() as conn:
                        save_data_upsert('daily_ohlcv', df, conn)
                except Exception as e:
                    logger.error(f"  [{idx}/{tong}] {symbol}: ❌ Lỗi ghi DB: {e}")
                    that_bai += 1
                    blacklist[symbol] = time.time()
                    break

                # Đếm dòng sau khi upsert
                with get_connection() as conn:
                    dong_sau = conn.execute(
                        "SELECT COUNT(*) FROM daily_ohlcv WHERE symbol = ?", (symbol,)
                    ).fetchone()[0]
                dong_moi = dong_sau - dong_truoc

                if dong_moi == 0:
                    if verbose:
                        print(f"\r  [{idx}/{tong}] {symbol}: ✅ Đã đầy đủ (giữ nguyên {dong_truoc} dòng)")
                    thanh_cong += 1
                    src = df['source'].iloc[0] if 'source' in df.columns else 'unknown'
                    source_counter[src] = source_counter.get(src, 0) + 1
                    break

                tong_dong_moi += dong_moi
                thanh_cong += 1
                src = df['source'].iloc[0] if 'source' in df.columns else 'unknown'
                source_counter[src] = source_counter.get(src, 0) + 1
                if verbose:
                    print(f"\r  [{idx}/{tong}] {symbol}: ✅ +{dong_moi} dòng ({dong_truoc}→{dong_sau}) [src={src}]")
                break

            except Exception as e:
                logger.warning(f"  [{idx}/{tong}] {symbol}: ⚠ Lỗi lần {lan_thu+1}/{TOI_DA_THU_LAI}: {e}")
                if lan_thu < TOI_DA_THU_LAI - 1:
                    thoi_gian_cho = COOLDOWN_LOI * (2 ** lan_thu)
                    time.sleep(thoi_gian_cho)
                else:
                    logger.error(f"  [{idx}/{tong}] {symbol}: ❌ Thất bại sau {TOI_DA_THU_LAI} lần thử.")
                    that_bai += 1
                    blacklist[symbol] = time.time()

        # Throttle giữa các request — Exponential Backoff + jitter
        # WIN11 BLACK-SCREEN BUG: Delay prevents concurrent GPU access
        # from overlapping scheduled tasks (VCI + Close Cycle both use
        # Playwright which triggers TDR when monitor is off).
        if idx < tong:
            base_delay = random.uniform(THROTTLE_MIN, THROTTLE_MAX)
            # Exponential backoff on consecutive failures
            if that_bai > 0 and that_bai == (idx - thanh_cong - bo_qua):
                backoff = base_delay * (2 ** min(that_bai, 5))
                jitter = random.uniform(0, backoff * 0.3)
                total_delay = backoff + jitter
                logger.info(
                    f"  [{idx}/{tong}] Backoff delay: {total_delay:.1f}s "
                    f"(base={base_delay:.1f}s, failures={that_bai})"
                )
            else:
                total_delay = base_delay
            time.sleep(total_delay)

    # 3. KẾT QUẢ
        # Xóa dòng status cũ
    thoi_gian = time.time() - bat_dau

    print()
    print("=" * 70)
    print("  ═══ KẾT QUẢ BACKFILL ═══")
    print("=" * 70)
    print(f"  ✅ Thành công: {thanh_cong}/{tong}")
    print(f"  ❌ Thất bại:   {that_bai}")
    print(f"  ⏭ Bỏ qua:     {bo_qua}")
    print(f"  📦 Dòng mới:   {tong_dong_moi}")
    print(f"  ⏱  Thời gian:  {thoi_gian:.1f}s ({thoi_gian/60:.1f} phút)")
    if blacklist:
        print(f"  🚫 Blacklist:  {len(blacklist)} mã bị tạm khóa")

    # Source distribution audit
    total_success = sum(source_counter.values())
    if total_success > 0:
        print(f"  📊 Phân bổ nguồn dữ liệu (Thành công: {total_success}):")
        for src in FALLBACK_SOURCES:
            count = source_counter.get(src, 0)
            pct = (count / total_success * 100) if total_success > 0 else 0
            bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
            print(f"     {src:<6s} [{count:>3}] {pct:5.1f}% |{bar}|")
        # Recommend reordering if TCBS/DNSE handled >30% of load
        fallback_pct = sum(source_counter.get(s, 0) for s in ['tcbs', 'dnse', 'kbs'])
        if fallback_pct > total_success * 0.3:
            logger.warning(
                f"⚠ VCI handled only {source_counter.get('vci', 0)}/{total_success} "
                f"({source_counter.get('vci', 0)/total_success*100:.0f}%). "
                f"Consider increasing VCI_TIMEOUT or checking rate-limit status."
            )
    print()

    # Cập nhật báo cáo chất lượng dữ liệu
    if thanh_cong > 0:
        print("  📊 Đang cập nhật báo cáo chất lượng dữ liệu...")
        from src.engine.data_quality import xuat_bao_cao as xbc
        xbc()

    print("=" * 70)

    return {
        'total': tong,
        'success': thanh_cong,
        'failed': that_bai,
        'skipped': bo_qua,
        'new_rows': tong_dong_moi,
        'execution_time': thoi_gian,
        'dry_run': dry_run,
    }


if __name__ == "__main__":
    backfill(dry_run=True)
