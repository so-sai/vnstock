import sys
import os
from pathlib import Path

def _hydrate_path():
    """Zero-Friction Sentinel v2.1: Tự động định vị Project Root (Bulletproof Anchor)"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "seed_data.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
import time
import pandas as pd
import src.config
from src.database.db_core import get_connection

def run_screener():
    """
    Screener V1 (Sentinel Hardened): Breakout 20d + Volume Spike 1.5x
    """
    start_time = time.time()

    # 1. Load Data (Debug Mode: Giới hạn từ 2025)
    with get_connection() as conn:
        df = pd.read_sql(
            """
            SELECT symbol, date, open, high, low, adj_close AS close, volume
            FROM daily_ohlcv
            WHERE date >= '2025-01-01'
        """,
            conn,
        )

    if df.empty:
        print("❌ Lỗi: Không có dữ liệu để quét. Hãy chạy 'python seed_data.py' trước.")
        return pd.DataFrame()

    # --- SENTINEL SAFE PATTERN ---
    df = df.copy()
    df.loc[:, "date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"])

    # === VECTORIZED ENGINE V1 (SENTINEL VERSION) ===
    g = df.groupby("symbol")

    # Dùng loc cho toàn bộ tính toán trung gian để tránh SettingWithCopyWarning
    df.loc[:, "vol_ma20"] = g["volume"].transform(lambda x: x.rolling(20).mean())
    df.loc[:, "vol_spike"] = df["volume"] > 1.5 * df["vol_ma20"]

    # 2. Breakout Đỉnh 20 ngày (Dùng shift(1) để tránh lookahead bias)
    df.loc[:, "high_20"] = g["high"].transform(lambda x: x.shift(1).rolling(20).max())
    df.loc[:, "breakout"] = df["close"] > df["high_20"]

    # 3. Kết hợp Tín hiệu
    df.loc[:, "signal"] = (df["vol_spike"]) & (df["breakout"])

    # 4. Trích xuất kết quả phiên mới nhất
    latest_date = df["date"].max()
    result = df[(df["date"] == latest_date) & (df["signal"] == True)].copy()

    # Đo lường hiệu năng
    runtime = time.time() - start_time

    # === BÁO CÁO KẾT QUẢ ===
    print("\n" + "=" * 50)
    print(f"🎯 KẾT QUẢ SCREENER V1 (Ngày: {latest_date.strftime('%Y-%m-%d')})")
    print("=" * 50)
    print(f"⏱️ Runtime: {runtime:.2f}s")
    print(f"📊 Số mã quét: {df['symbol'].nunique()}")
    print(f"✅ Tín hiệu Breakout + Vol 1.5x (V1): {len(result)}\n")

    if not result.empty:
        # Sắp xếp theo Volume để ưu tiên chất lượng thanh khoản
        output = result[["symbol", "close", "volume", "vol_ma20"]].sort_values("volume", ascending=False).copy()
        print(output.to_string(index=False))
    else:
        print("Không có mã nào phát tín hiệu hôm nay thỏa mãn V1.")

    return result

if __name__ == "__main__":
    run_screener()
