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
            # Săn lùng Root dựa trên các điểm neo độc bản (seed_data.py, .kit)
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "seed_data.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd
import numpy as np
import sqlite3
import src.config
from src.database.db_core import get_connection
from src.engine.rs_engine import compute_rs_matrix

class BacktestAlpha:
    def __init__(self, rebalance_freq=10, initial_capital=100_000_000, fee=0.003):
        """
        Động cơ Backtest Alpha v2.1 (Sentinel Ready)
        - rebalance_freq: 10 phiên (2 tuần)
        - fee: 0.3% mỗi vòng quay
        """
        self.rebalance_freq = rebalance_freq
        self.initial_capital = initial_capital
        self.fee = fee
        self.results = None

    def run(self, start_date='2023-01-01', top_n=10):
        """ Thực thi mô phỏng chiến lược """
        print(f"\n[HEARTBEAT] Engine started for {start_date}")
        print(f"🚀 ĐANG KHỞI ĐỘNG CỖ MÁY XUYÊN KHÔNG (BACKTEST V2.1)...")
        print(f"📅 Giai đoạn: {start_date} -> Present | Rebalance: {self.rebalance_freq} phiên")
        
        # 1. Tải dữ liệu với cơ chế Retry siêu cấp (chống lock DB khi đang seeding)
        import time 
        df_stocks = pd.DataFrame()
        df_bench = pd.DataFrame()
        
        for attempt in range(5):
            try:
                db_path = os.path.join(src.config.DATA_DIR, "screener_cache.db")
                conn = sqlite3.connect(db_path, timeout=60)
                
                # Tải Stocks: lấy cả close (cho P&L/pivot_price) và adj_close (cho RS Momentum)
                stock_query = f"SELECT symbol, date, close, adj_close, volume FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX', 'VN30') AND date >= '{start_date}'"
                df_stocks = pd.read_sql(stock_query, conn)
                
                # Tải Benchmark (dùng adj_close để phản ánh giá thực sau chia cổ tức/tách cổ phiếu)
                bench_query = f"SELECT date, adj_close as bench_close FROM daily_ohlcv WHERE symbol LIKE '%VNINDEX%' AND date >= '{start_date}'"
                df_bench = pd.read_sql(bench_query, conn)
                
                # Tải Phân ngành ICB (V4.0 Sector Rotation)
                df_ind = pd.read_sql("SELECT symbol, icb_name2 FROM symbol_industry", conn)
                symbol_to_industry = dict(zip(df_ind['symbol'], df_ind['icb_name2']))
                
                print(f"DEBUG: Vault={db_path}")
                print(f"DEBUG: StartDate={start_date} | Stocks={len(df_stocks)} | Bench={len(df_bench)}")
                print(f"DEBUG: Industries Mapped={len(symbol_to_industry)}")
                
                if df_bench.empty:
                    # Forensic check why bench is empty
                    exists = pd.read_sql("SELECT count(*) as cnt FROM daily_ohlcv WHERE symbol LIKE '%VNINDEX%'", conn)
                    print(f"DEBUG: Total VNINDEX rows in Vault: {exists['cnt'][0]}")
                
                conn.close()
                if not df_bench.empty:
                    break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    print(f"⏳ Vault đang bận (Seeding in progress). Thử lại lần {attempt+1}/5...")
                    time.sleep(2 * attempt)
                else:
                    raise e
        
        if df_stocks.empty or df_bench.empty:
            print(f"⚠️ Thiếu dữ liệu Stock ({len(df_stocks)}) hoặc Benchmark ({len(df_bench)}).")
            # Kiểm tra xem có bất kỳ dữ liệu nào của VNINDEX không
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                available = pd.read_sql("SELECT DISTINCT symbol FROM daily_ohlcv WHERE symbol LIKE '%INDEX%'", conn)
                print(f"🔎 Các mã Index hiện có trong Vault: {available['symbol'].tolist()}")
                conn.close()
            except: pass
            return None

        if df_stocks.empty or df_bench.empty:
            print(f"⚠️ Thiếu dữ liệu Stock ({len(df_stocks)}) hoặc Benchmark ({len(df_bench)}).")
            # Hiển thị date range hiện có để debug
            return None

        # --- SENTINEL SAFE PATTERN ---
        df_stocks = df_stocks.copy()
        df_bench = df_bench.copy()
        df_stocks.loc[:, 'date'] = pd.to_datetime(df_stocks['date'], errors='coerce')
        df_bench.loc[:, 'date'] = pd.to_datetime(df_bench['date'], errors='coerce')
        
        # BẮT BUỘC: Ép kiểu số để tránh lỗi 'NoneType' arithmetic
        df_stocks['close'] = pd.to_numeric(df_stocks['close'], errors='coerce')
        df_stocks['volume'] = pd.to_numeric(df_stocks['volume'], errors='coerce')
        df_bench['bench_close'] = pd.to_numeric(df_bench['bench_close'], errors='coerce')
        
        if df_stocks.empty or df_bench.empty:
            print(f"⚠️ Vault rỗng cho giai đoạn từ {start_date}. Cỗ máy không thể khai hỏa.")
            return None
        
        # 2. Xây dựng Ma trận Giá & RS
        print("🏗️  Step 2.1: Pivoting Price/Vol...")
        # V4.6 SNIPER: Sử dụng adj_close cho pivot_price
        pivot_price = df_stocks.pivot_table(index='date', columns='symbol', values='close', aggfunc='max')
        pivot_vol = df_stocks.pivot_table(index='date', columns='symbol', values='volume', aggfunc='max')
        
        # --- V4.6: CẢNH BÁO XU HƯỚNG (MA20 TREND FILTER) ---
        print("🏗️  Step 2.1b: Calculating MA20 for Trend Filter...")
        pivot_ma20 = pivot_price.rolling(20).mean()
        
        # --- V4.1: LIQUIDITY SHIELD (5B VNĐ) ---
        print("🏗️  Step 2.2: Calculating Market Value...")
        pivot_value = pivot_price * pivot_vol * 1000 
        avg_value_20 = pivot_value.rolling(20).mean()
        
        # --- V3.0: TẢI DỮ LIỆU VĨ MÔ (NẤC 0) ---
        print("🏗️  Step 2.3: Loading Macro Data...")
        conn = sqlite3.connect(db_path)
        df_macro = pd.read_sql("SELECT * FROM macro_history", conn)
        conn.close()
        
        # Đồng bộ hóa định dạng ngày & Xử lý trùng lặp (tránh lỗi Pivot)
        df_macro['date'] = pd.to_datetime(df_macro['date'], errors='coerce')
        df_macro = df_macro.drop_duplicates(subset=['date', 'variable'], keep='last')
        pivot_macro = df_macro.pivot_table(index='date', columns='variable', values='value', aggfunc='max').ffill()
        
        # Tính MA20 cho Macro (Cầu dao vĩ mô)
        macro_ma20 = pivot_macro.rolling(20).mean()
        
        # Tính Ma trận RS Rank (Tận dụng bộ não rs_engine)
        print("🏗️  Step 2.4: Computing RS Matrix...")
        rs_matrix = compute_rs_matrix(df_stocks)
        
        # --- V4.1: TÍNH TOÁN SECTOR VELOCITY (NẤC 1.5) ---
        print("🏗️  Step 2.5: Mapping Sectors...")
        # Chuyển Ma trận RS sang Industry RS Matrix (Date x Industry)
        # Loại bỏ các cột không có mapping ngành để Groupby không lỗi
        mapped_symbols = [s for s in rs_matrix.columns if s in symbol_to_industry]
        industry_mapping_series = pd.Series(symbol_to_industry)[mapped_symbols]
        
        industry_rs_matrix = rs_matrix[mapped_symbols].T.groupby(industry_mapping_series).mean().T
        # Tính Tốc độ thay đổi (Velocity) trong 5 phiên
        industry_rs_velocity = industry_rs_matrix.diff(5)
        
        # 3. MÔ PHỎNG CHIẾN LƯỢC (V4.1 - Strategic Momentum)
        all_dates = sorted(pivot_price.index.unique())
        equity = [self.initial_capital]
        current_holdings = []
        portfolio_returns = []
        
        # V2.2/V3.0 State Tracking
        entry_prices = {}
        stop_loss_count = 0
        macro_save_count = 0 
        
        # V3.0: Tập trung hỏa lực (Top 5 thay vì 10)
        top_n = 5 
        
        rebalance_dates = all_dates[::self.rebalance_freq]
        daily_pct_change = pivot_price.pct_change(fill_method=None)

        print(f"DEBUG: Mo phong qua {len(all_dates)} phien giao dich...")

        for i in range(1, len(all_dates)):
            today = all_dates[i]
            yesterday = all_dates[i-1]
            
            # --- 3.0: HỘI ĐỒNG VĨ MÔ (MACRO COUNCIL - V4.3) ---
            # L1: Cầu dao chính (DXY, FX) -> Quyết định Sống còn (100% Cash)
            # L2: Cảm biến Khẩu vị (BTC, Gold) -> Quyết định Tác chiến (Stop-Buy)
            is_macro_safe_l1 = True # Primary
            allow_new_buy = True    # Tactical
            
            if yesterday in pivot_macro.index:
                # 🛡️ Cảm biến L1: Tỷ giá & DXY (Trọng số 50%)
                dxy = pivot_macro.loc[yesterday, 'DXY'] if 'DXY' in pivot_macro.columns else 0
                dxy_ma = macro_ma20.loc[yesterday, 'DXY'] if 'DXY' in macro_ma20.columns else 0
                fx = pivot_macro.loc[yesterday, 'USD_VND'] if 'USD_VND' in pivot_macro.columns else 0
                fx_ma = macro_ma20.loc[yesterday, 'USD_VND'] if 'USD_VND' in macro_ma20.columns else 0
                
                if (dxy > dxy_ma * 1.01) or (fx > fx_ma * 1.005):
                    is_macro_safe_l1 = False
                    macro_save_count += 1
                
                # 🛡️ Cảm biến L2: BTC & Gold (Trọng số 30%)
                btc = pivot_macro.loc[yesterday, 'BTC'] if 'BTC' in pivot_macro.columns else 0
                btc_ma = macro_ma20.loc[yesterday, 'BTC'] if 'BTC' in macro_ma20.columns else 0
                gold = pivot_macro.loc[yesterday, 'GOLD_XAU'] if 'GOLD_XAU' in pivot_macro.columns else 0
                gold_ma = macro_ma20.loc[yesterday, 'GOLD_XAU'] if 'GOLD_XAU' in macro_ma20.columns else 0
                
                # Nếu BTC sập > 10% hoặc Vàng tăng sốc > 5% -> Ngừng mua mới
                if (btc < btc_ma * 0.90) or (gold > gold_ma * 1.05):
                    allow_new_buy = False

            # --- 3.1. TÁI CƠ CẤU (REBALANCE) ---
            if yesterday in rebalance_dates:
                # --- V4.6: BỘ LỌC KỈ CƯƠNG (MA20 + LIQUIDITY) ---
                val_yesterday = avg_value_20.loc[yesterday]
                price_yesterday = pivot_price.loc[yesterday]
                ma20_yesterday = pivot_ma20.loc[yesterday]
                
                # Sniper Filter: Giá > 10k, Thanh khoản > 2 Tỷ, VÀ Giá > MA20
                valid_universe = (price_yesterday >= 10) & (val_yesterday >= 2_000_000_000) & (price_yesterday > ma20_yesterday)
                
                # Rank RS chỉ trên các mã đủ thanh khoản
                filtered_rs = rs_matrix.loc[yesterday][valid_universe]
                
                # --- V4.5: PHỄU LỌC ÁP SUẤT (MULTI-GATE ELASTIC NAV) ---
                target_nav_ratio = 1.0 # Default full
                
                # Nấc 0: Vĩ mô (Liquidity Gate)
                if not is_macro_safe_l1:
                    target_nav_ratio = 0.0
                elif not allow_new_buy:
                    target_nav_ratio = 0.3
                
                # Nấc 1: Lan tỏa ngành (Sector Diffusion)
                if target_nav_ratio > 0.3:
                    if yesterday in industry_rs_matrix.index:
                        s_rs = industry_rs_matrix.loc[yesterday]
                        s_vel = industry_rs_velocity.loc[yesterday]
                        sector_combined_score = s_rs + s_vel
                        
                        # Chỉ lấy các ngành có trong Universe
                        current_industries = filtered_rs.index.map(symbol_to_industry).unique()
                        sector_combined_score = sector_combined_score[sector_combined_score.index.isin(current_industries)]
                        top_3_sectors = sector_combined_score.sort_values(ascending=False).head(3).index.tolist()
                        
                        # Tính Diffusion Index cho Top 3 ngành
                        # (Proxy: % mã trong Top 3 ngành có RS cá nhân > 60)
                        top_sector_mask = filtered_rs.index.map(symbol_to_industry).isin(top_3_sectors)
                        top_sector_stocks = filtered_rs[top_sector_mask]
                        if not top_sector_stocks.empty:
                            diffusion = (top_sector_stocks > 60).sum() / len(top_sector_stocks)
                            if diffusion < 0.4:
                                target_nav_ratio = 0.7 # Hạ nhiệt khi kém lan tỏa
                        
                        filtered_rs = filtered_rs[top_sector_mask]
                
                # --- V4.5: ĐI VỐN (CAPITAL ALLOCATION) ---
                if target_nav_ratio == 0:
                    new_holdings = []
                elif target_nav_ratio == 0.3:
                    # Chế độ Phòng thủ: Chỉ giữ 30% NAV (Top 2 mã cũ hoặc mạnh nhất)
                    new_holdings = filtered_rs.sort_values(ascending=False).head(2).index.tolist()
                else:
                    new_holdings = filtered_rs.sort_values(ascending=False).head(top_n).index.tolist()
                
                # Trừ phí giao dịch khi đảo danh mục
                if i > 1 and new_holdings != current_holdings:
                    equity[-1] *= (1 - self.fee)
                
                current_holdings = new_holdings
                
                # Cập nhật giá vốn mua (Entry Price)
                for ticker in current_holdings:
                    entry_prices[ticker] = pivot_price.loc[today, ticker]

            # --- 3.2. CẢM BIẾN STOP-LOSS (INTRA-PERIOD - V4.5) ---
            # V4.5 Rumor Shield: Cắt ngay -5% nếu Vol nổ > 1.5x Trung bình (Tháo chạy)
            to_remove = []
            for ticker in current_holdings:
                today_price = pivot_price.loc[today, ticker]
                entry = entry_prices.get(ticker, today_price)
                price_pct = (today_price / entry) - 1
                
                # Tính Z-Vol (So với trung bình 20 ngày)
                today_vol = pivot_vol.loc[today, ticker]
                # Proxy cho Average Volume từ avg_value_20 (vì avg_value_20 = price * volume * 1000)
                ticker_avg_vol = avg_value_20.loc[today, ticker] / (today_price * 1000) if today_price > 0 else 1
                
                is_panic_sell = (price_pct < -0.05) and (today_vol > ticker_avg_vol * 1.5)
                
                # SL Thường (Dynamic)
                sl_threshold = -0.05 if (not is_macro_safe_l1 or not allow_new_buy) else -0.07
                
                if is_panic_sell or price_pct < sl_threshold:
                    to_remove.append(ticker)
                    stop_loss_count += 1
            
            for ticker in to_remove:
                current_holdings.remove(ticker)
                if ticker in entry_prices: del entry_prices[ticker]

            # --- 3.3. TÍNH TOÁN LỢI NHUẬN (Bình quân Equal-Weighted) ---
            if current_holdings:
                total_day_return_sum = daily_pct_change.loc[today, current_holdings].sum()
                day_return = total_day_return_sum / top_n
            else:
                day_return = 0
                
            portfolio_returns.append(day_return)
            equity.append(equity[-1] * (1 + day_return))

        # 4. FORENSIC REPORTING
        results_df = pd.DataFrame({'date': all_dates, 'portfolio': equity})
        
        # Đảm bảo date chuẩn hóa trước khi merge
        results_df['date'] = pd.to_datetime(results_df['date'])
        df_bench['date'] = pd.to_datetime(df_bench['date'])
        
        results_df = pd.merge(results_df, df_bench[['date', 'bench_close']], on='date', how='left')
        
        # Filling missing benchmark days if any
        results_df['bench_close'] = results_df['bench_close'].ffill()
        
        # Calculate daily returns for Sharpe/IR
        results_df.loc[:, 'port_daily_ret'] = results_df['portfolio'].pct_change(fill_method=None).fillna(0)
        results_df.loc[:, 'bench_daily_ret'] = results_df['bench_close'].pct_change(fill_method=None).fillna(0)
        results_df.loc[:, 'excess_ret'] = results_df['port_daily_ret'] - results_df['bench_daily_ret']

        results_df.loc[:, 'bench_ret'] = results_df['bench_close'] / results_df['bench_close'].iloc[0]
        results_df.loc[:, 'port_ret'] = results_df['portfolio'] / self.initial_capital
        
        total_ret = (results_df['port_ret'].iloc[-1] - 1) * 100
        bench_ret = (results_df['bench_ret'].iloc[-1] - 1) * 100
        
        # --- ALPHA SCORECARD METRICS (Annualized) ---
        rf_annual = 0.05
        rf_daily = rf_annual / 252
        
        mean_daily_ret = results_df['port_daily_ret'].mean()
        std_daily_ret = results_df['port_daily_ret'].std()
        sharpe = (mean_daily_ret - rf_daily) / std_daily_ret * np.sqrt(252) if std_daily_ret > 0 else 0
        
        mean_excess_ret = results_df['excess_ret'].mean()
        tracking_error = results_df['excess_ret'].std()
        info_ratio = (mean_excess_ret / tracking_error) * np.sqrt(252) if tracking_error > 0 else 0

        # Risk: Max Drawdown
        cum_max = results_df['portfolio'].cummax()
        drawdown = (results_df['portfolio'] - cum_max) / cum_max
        mdd = drawdown.min() * 100
        
        print("\n" + "="*45)
        print(f"📊 BÁO CÁO GIÁM ĐỊNH ALPHA v4.3 (Macro Hub + Risk Shield)")
        print("="*45)
        print(f"💰 Lợi nhuận Tổng:       {total_ret:>8.2f}%")
        print(f"📈 VN-Index Return:     {bench_ret:>8.2f}%")
        print(f"🔥 ALPHA (Vượt trội):    {total_ret - bench_ret:>8.2f}%")
        print(f"💎 Sharpe Ratio:         {sharpe:>8.2f}")
        print(f"📉 Information Ratio:    {info_ratio:>8.2f}")
        print(f"📉 Max Drawdown:         {mdd:>8.2f}%")
        print(f"🌍 Macro 'Stay in Cash': {macro_save_count:>8} sessions")
        print(f"🛡️  Stop-Loss Triggers:  {stop_loss_count:>8}")
        print(f"🎲 Win Rate (Rebalance): {((np.array(portfolio_returns) > 0).sum() / len(portfolio_returns) * 100):>8.2f}%")
        print("="*45)

        # 5. LƯU KẾT QUẢ
        output_dir = os.path.join(src.config.PROJECT_ROOT, "data", "output")
        os.makedirs(output_dir, exist_ok=True)
        results_df.to_json(os.path.join(output_dir, "backtest_results.json"), 
                           orient='records', force_ascii=False, indent=4)
        
        return results_df

if __name__ == "__main__":
    bt = BacktestAlpha(rebalance_freq=10)
    # BẢN ĐỒ ALPHA TOÀN DIỆN 2023-2026
    bt.run(start_date='2023-01-01')
