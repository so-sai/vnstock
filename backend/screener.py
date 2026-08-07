import os
import sys
from pathlib import Path


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root"""
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            # Săn lùng Root dựa trên các điểm neo độc bản
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
# Add canonical libs to path
_LIBS = str(Path(PROJECT_ROOT) / "backend" / "libs")
if _LIBS not in sys.path:
    sys.path.insert(1, _LIBS)
import argparse
import json
import random
import sys
import time

import pandas as pd
from canonical import CanonicalAssetRegistry, Normalizer
from canonical.validator import ValidationError
from src.database.db_core import get_connection, optimize_sqlite_engine, save_data_upsert

_CANON = CanonicalAssetRegistry()
_NORM = Normalizer()


class EliteArmor:
    """
    Sentinel Throttling v1.0: Rate Limiting & Negative Caching (7-day TTL).
    API protection module.
    """

    def __init__(self, cache_file=".negative_cache.json"):
        self.cache_file = os.path.join(PROJECT_ROOT, cache_file)
        self.cache = self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Cleanup old entries (> 7 days)
                    now = time.time()
                    return {k: v for k, v in data.items() if now - v < 7 * 24 * 3600}
            except:
                return {}
        return {}

    def save_cache(self):
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(self.cache, f)

    def is_blacklisted(self, symbol):
        return symbol in self.cache

    def blacklist(self, symbol):
        self.cache[symbol] = time.time()

    def wait(self, duration=None, is_error=False):
        """
        Request Collapsing: Ngủ ngẫu nhiên để tránh burst limit.
        Negative Caching: Ngủ 60s+ nếu API báo lỗi.
        """
        if is_error:
            cooldown = random.uniform(45.0, 75.0)
            print(f"\n🚨 API ARMOR: Phat hien rủi ro (429/5xx). Cooldown {cooldown:.1f}s...")
            time.sleep(cooldown)
        else:
            sleep_time = duration if duration else random.uniform(1.8, 4.2)
            time.sleep(sleep_time)


ARMOR = EliteArmor()

from vnstock import Listing, Quote


def seed_industry_mapping():
    """
    Chiến dịch nạp dữ liệu phân ngành (ICB) cho toàn bộ thị trường.
    """
    print("\n" + "=" * 60)
    print("🌿 CHIẾN DỊCH: NẠP DỮ LIỆU PHÂN NGÀNH (ICB)")
    print("=" * 60)

    try:
        ls = Listing(source="VCI")
        df_ind = ls.symbols_by_industries(lang="vi")

        if df_ind.empty:
            print("❌ Lỗi: Không thể lấy dữ liệu ngành từ VCI.")
            return

        df_ind = df_ind.copy()
        # VCI Listing trả long-format: icb_name + icb_level (1..4), 1 dòng/level/mã.
        # Pivot về wide-format: 1 dòng/mã, icb_name2/3/4 = level 2/3/4.
        if "icb_name" in df_ind.columns and "icb_level" in df_ind.columns:
            pivot = df_ind.pivot_table(
                index="symbol",
                columns="icb_level",
                values="icb_name",
                aggfunc="first",
            )
            for lv in (2, 3, 4):
                col = f"icb_name{lv}"
                if lv in pivot.columns:
                    pivot[col] = pivot[lv]
                else:
                    pivot[col] = None
            df_to_save = pivot[["icb_name2", "icb_name3", "icb_name4"]].reset_index()
        else:
            cols_to_keep = ["symbol", "icb_name2", "icb_name3", "icb_name4"]
            df_to_save = df_ind[[c for c in cols_to_keep if c in df_ind.columns]].copy()

        with get_connection() as conn:
            df_to_save.to_sql("symbol_industry", conn, if_exists="replace", index=False)

        print(f"✅ Đã nạp thành công phân ngành cho {len(df_to_save)} mã.")
        print("—" * 60)

    except Exception as e:
        print(f"❌ Lỗi nạp dữ liệu ngành: {e}")


def seed_index_data():
    """
    Chiến dịch nạp dữ liệu Benchmark (VNINDEX, VN30) phục vụ Backtest.
    Mặc định nạp 3 năm để bao quát chu kỳ 2022-2025.
    """
    print("\n" + "=" * 60)
    print("📊 CHIẾN DỊCH: NẠP DỮ LIỆU BENCHMARK (INDEX)")
    print("=" * 60)

    indices = ["VNINDEX", "VN30"]

    for symbol in indices:
        print(f"🚀 Đang lấy lịch sử cho {symbol} (Source: KBS)...")
        try:
            q = Quote(symbol=symbol, source="KBS")
            # V4.5 STRESS TEST: Su dung 'start' tuong minh de chac chan co nam 2022
            df_hist = q.history(start="2021-01-01", interval="1D")

            if df_hist is not None and not df_hist.empty:
                df_hist = df_hist.copy()
                # KBS Index value scales by 1000 (e.g., 1.24 -> 1240)
                if symbol in ["VNINDEX", "VN30"]:
                    for col in ["open", "high", "low", "close"]:
                        if col in df_hist.columns:
                            # SMART SCALE: Chỉ nhân nếu dữ liệu ở dạng 1.x-1.x (không nhân nếu đã là 1xx.x)
                            if df_hist[col].mean() < 10:
                                df_hist.loc[:, col] = df_hist[col] * 1000

                df_hist.loc[:, "symbol"] = symbol
                df_hist.loc[:, "source"] = "KBS"

                df_hist = df_hist.rename(columns={"time": "date"})
                # BẮT BUỘC: Ép kiểu sang string YYYY-MM-DD
                df_hist["date"] = pd.to_datetime(df_hist["date"]).dt.strftime("%Y-%m-%d")

                # Sentinel Pattern: Preserve 'close' and mirror to 'adj_close'
                df_hist.loc[:, "adj_close"] = df_hist["close"]
                df_hist = df_hist.rename(columns={"time": "date"})

                cols_to_keep = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
                df_to_save = df_hist[[c for c in cols_to_keep if c in df_hist.columns]].copy()

                with get_connection() as conn:
                    save_data_upsert("daily_ohlcv", df_to_save, conn)
                print(f"✅ Đã lưu {len(df_to_save)} phiên cho {symbol}.")

            time.sleep(2)
        except Exception as e:
            print(f"⚠️ Lỗi nạp {symbol}: {e}")


def full_market_seeding():
    """
    Chiến dịch Full Market Seeding (V1.3-Hardened) dùng VCI Provider.
    Pattern: SAFE COPY-ON-WRITE & ELITE LOGGING.
    """
    start_time = time.time()
    optimize_sqlite_engine()

    stats = {"success": 0, "no_data": 0, "failed": 0}

    print("\n" + "=" * 60)
    print("🚀 CHIẾN DỊCH: FULL MARKET SEEDING (~1,700 MÃ)")
    print("=" * 60)

    try:
        ls = Listing(source="VCI")
        df_symbols = ls.all_symbols()
        all_symbols = sorted(df_symbols["symbol"].unique().tolist())
        total_symbols = len(all_symbols)
        print(f"✓ Tìm thấy {total_symbols} mã chứng khoán STOCK.")
    except Exception as e:
        print(f"❌ Không thể lấy danh sách mã: {e}")
        return

    with get_connection() as conn:
        try:
            done_symbols = pd.read_sql("SELECT DISTINCT symbol FROM daily_ohlcv", conn)["symbol"].tolist()
        except:
            done_symbols = []

    todo_symbols = [s for s in all_symbols if s not in done_symbols]
    print(f"✓ Đã nạp: {len(done_symbols)} mã. Cần nạp tiếp: {len(todo_symbols)} mã.")
    print("-" * 60)

    count = 0
    total_todo = len(todo_symbols)

    for symbol in todo_symbols:
        count += 1
        if ARMOR.is_blacklisted(symbol):
            print(f"[{count}/{total_todo}] Skipping (Blacklisted): {symbol}...", end="\r")
            continue

        print(f"[{count}/{total_todo}] Đang xử lý: {symbol}...", end="\r")

        try:
            q = Quote(symbol=symbol, source="VCI")
            df_hist = q.history(length="1Y", interval="1D")

            if df_hist is not None and not df_hist.empty:
                df_hist = df_hist.copy()

                # --- AUTO-HEALING V4.5: PHÁT HIỆN CHIA TÁCH (SPLIT DETECTION) ---
                # Ở VN, biên độ tối đa là 15% (UPCOM). Cú rớt >30% chắc chắn là Chia tách/Cổ tức.
                pct_change = df_hist["close"].pct_change().abs().max()
                if pct_change > 0.3:
                    print(f"\n⚠️  HEALING: Phát hiện biến động {pct_change * 100:.1f}% tại {symbol}. Tái nạp lịch sử 3 năm...")
                    # Fetching 3Y history usually forced adjusted prices on modern providers
                    df_hist = q.history(length="3Y", interval="1D")
                    if df_hist is None or df_hist.empty:
                        continue
                    df_hist = df_hist.copy()

                df_hist.loc[:, "symbol"] = symbol
                df_hist.loc[:, "source"] = "VCI"

                # Sentinel Pattern: Preserve 'close' and mirror to 'adj_close'
                df_hist.loc[:, "adj_close"] = df_hist["close"]
                df_hist = df_hist.rename(columns={"time": "date"})

                cols_to_keep = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
                df_to_save = df_hist[[c for c in cols_to_keep if c in df_hist.columns]].copy()

                with get_connection() as conn:
                    save_data_upsert("daily_ohlcv", df_to_save, conn)
                stats["success"] += 1
            else:
                stats["no_data"] += 1
                ARMOR.blacklist(symbol)  # Negative cache for no data

            ARMOR.wait()

            if count % 50 == 0:
                cooldown = random.uniform(30, 60)
                print(f"\n☕ Đã nạp cụm 50 mã. Nghỉ {cooldown:.1f}s...")
                time.sleep(cooldown)

        except ValueError as ve:
            if "Không tìm thấy dữ liệu" in str(ve):
                stats["no_data"] += 1
                ARMOR.blacklist(symbol)
            else:
                stats["failed"] += 1
        except Exception as e:
            stats["failed"] += 1
            print(f"\n⚠️ Lỗi nghiêm trọng tại {symbol}: {e}")
            ARMOR.wait(is_error=True)  # Negative caching trigger

    ARMOR.save_cache()

    duration = (time.time() - start_time) / 60

    print("\n\n" + "=" * 45)
    print("🏆 ELITE SUMMARY: SEEDING COMPLETED")
    print("=" * 45)
    print(f"✅ Thành công:     {stats['success']:>6}")
    print(f"🟡 Không dữ liệu:  {stats['no_data']:>6} (Upcom/Delisted/New)")
    print(f"🔴 Lỗi API:        {stats['failed']:>6}")
    print(f"⏱  Thời gian:      {duration:>8.1f} min")
    print("=" * 45)


def fetch_keyless_macro():
    """
    🌍 CHIẾN DỊCH: CẢM BIẾN VĨ MÔ KEYLESS (yfinance)
    Tải dữ liệu Vĩ mô cốt lõi không cần API Key.
    """
    import yfinance as yf

    print("\n" + "=" * 60)
    print("🌍 KHỞI ĐỘNG CẢM BIẾN VĨ MÔ (KEYLESS MODE)")
    print("=" * 60)

    tickers = {
        "DXY": "DX-Y.NYB",
        "USD_VND": "USDVND=X",
        "USD_CNY": "CNY=X",
        "USD_CNH": "CNH=X",
        "SH_COMP": "000001.SS",
        "COPPER_HG": "HG=F",
        "US2Y": "2YY=F",
        "US5Y": "^FVX",
        "US10Y": "^TNX",
        "US30Y": "^TYX",
        "BRENT_OIL": "BZ=F",
        "WTI_OIL": "CL=F",
        "BTC": "BTC-USD",
        "GOLD_XAU": "GC=F",
        "TIP_PRICE": "TIP",
    }

    try:
        # 1. Quét dữ liệu bằng yfinance (Miễn phí 100%)
        # V4.5 STRESS TEST: Tang period len 5y de bao phu toan bo nam 2021-2022
        print(f"📥 Đang tải {len(tickers)} cảm biến vĩ mô từ Yahoo Finance (5Y History)...")
        data = yf.download(list(tickers.values()), period="5y", interval="1d", progress=False)["Close"]

        # Mapping ngược lại tên thân thiện
        inv_map = {v: k for k, v in tickers.items()}
        data = data.rename(columns=inv_map)

        # 2. Chuyển đổi định dạng để đưa vào Vault (Melt sang Long format)
        df_melted = data.reset_index().melt(id_vars=["Date"], var_name="variable", value_name="value")
        df_melted.rename(columns={"Date": "date"}, inplace=True)
        df_melted["date"] = df_melted["date"].dt.strftime("%Y-%m-%d")
        df_melted = df_melted.dropna()

        # 3. Lưu vào CSDL (v1 — legacy path, giữ nguyên cho backward compat)
        with get_connection() as conn:
            save_data_upsert("macro_history", df_melted, conn)

        # 4. Canonical shadow write — normalize + validate + insert macro_history_v2
        v2_records = []
        v2_rejects = 0
        for _, row in df_melted.iterrows():
            try:
                rec = _NORM.normalize(
                    variable=row["variable"],
                    date=row["date"],
                    raw_value=row["value"],
                    source="yahoo",
                )
                v2_records.append(
                    {
                        "variable": rec.variable,
                        "date": rec.date,
                        "value": rec.value,
                        "asset_class": rec.asset_class.value,
                        "unit": rec.unit.value,
                        "source": rec.source.value,
                        "raw_value": rec.raw_value,
                        "raw_unit": rec.raw_unit,
                        "confidence": rec.confidence,
                    }
                )
            except ValueError, ValidationError:
                v2_rejects += 1

        if v2_records:
            df_v2 = pd.DataFrame(v2_records)
            with get_connection() as conn:
                save_data_upsert("macro_history_v2", df_v2, conn)
            print(f"✅ Canonical: {len(v2_records)} records → macro_history_v2 (rejected: {v2_rejects})")
        else:
            print(f"⚠️ Canonical: 0 records written (all {v2_rejects} rejected)")
        # 4. Kích hoạt Cảm biến Ngoại lệ (V4.4 Sentinel)
        from src.utils.macro_sensors import check_macro_exceptions  # Sync import

        check_macro_exceptions()

    except Exception as e:
        print(f"❌ Lỗi cảm biến Vĩ mô: {e}")


def backfill_diamonds_v45():
    """
    💎 CHIẾN DỊCH: BACKFILL TOP 300 DIAMONDS (V4.5)
    Tập trung hỏa lực vào 300 mã có thanh khoản tốt nhất 2023-2024.
    Mục tiêu: Xây dựng dữ liệu nền cho 'Tu dia 2022 Stress Test'.
    """
    print("\n" + "=" * 60)
    print("💎 CHIẾN DỊCH: BACKFILL TOP 300 DIAMONDS (5Y HISTORY)")
    print("=" * 60)

    with get_connection() as conn:
        query = """
            SELECT symbol, AVG(close * volume * 1000) as avg_val 
            FROM daily_ohlcv 
            WHERE symbol NOT IN ('VNINDEX', 'VN30') AND date >= '2023-01-01' 
            GROUP BY symbol 
            ORDER BY avg_val DESC 
            LIMIT 300
        """
        top_symbols = pd.read_sql(query, conn)["symbol"].tolist()

    print(f"✓ Tim thay {len(top_symbols)} ma Diamond de backfill.")

    stats = {"success": 0, "failed": 0}
    count = 0
    total = len(top_symbols)

    for symbol in top_symbols:
        count += 1
        print(f"[{count}/{total}] Backfilling: {symbol}...", end="\r")
        try:
            q = Quote(symbol=symbol, source="VCI")
            df_hist = q.history(length="5Y", interval="1D")  # 5Y to cover 2022 Gap

            if df_hist is not None and not df_hist.empty:
                df_hist = df_hist.copy()
                df_hist.loc[:, "symbol"] = symbol
                df_hist.loc[:, "source"] = "VCI"
                df_hist.loc[:, "adj_close"] = df_hist["close"]
                df_hist = df_hist.rename(columns={"time": "date"})
                # BẮT BUỘC: Ép kiểu sang string định dạng YYYY-MM-DD
                df_hist["date"] = pd.to_datetime(df_hist["date"]).dt.strftime("%Y-%m-%d")

                cols_to_keep = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
                df_to_save = df_hist[[c for c in cols_to_keep if c in df_hist.columns]].copy()

                with get_connection() as conn:
                    save_data_upsert("daily_ohlcv", df_to_save, conn)
                stats["success"] += 1

            time.sleep(random.uniform(1.5, 3.5))  # Bao ve API Quota
        except Exception as e:
            stats["failed"] += 1
            print(f"❌ Loi {symbol}: {e}")

    print(f"\n✅ Da backfill xong: {stats['success']} ma thành công!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PTCK_VNSTOCK Sentinel Utility")
    parser.add_argument("--mode", choices=["stock", "industry", "index", "macro", "diamonds"], default="stock")
    args = parser.parse_args()

    if args.mode == "stock":
        full_market_seeding()
    elif args.mode == "industry":
        seed_industry_mapping()
    elif args.mode == "index":
        # Khi nạp Index, nạp luôn Macro để đồng bộ "Nấc 0"
        fetch_keyless_macro()
        seed_index_data()
    elif args.mode == "macro":
        fetch_keyless_macro()
    elif args.mode == "diamonds":
        backfill_diamonds_v45()
