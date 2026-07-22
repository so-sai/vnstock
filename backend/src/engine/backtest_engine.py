import os
import sys
from pathlib import Path


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root"""
    if getattr(sys, 'frozen', False):
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
    return root_path

PROJECT_ROOT = _hydrate_path()

import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

import src.config
from src.engine.rs_ranker import compute_rs_components


class BacktestAlpha:
    def __init__(self, rebalance_freq=10, initial_capital=100_000_000, fee=0.0015, slippage=0.002):
        """
        Động cơ Backtest Alpha v1.0.1 (Adaptive Alpha Core)
        - rebalance_freq: 10 phiên
        - fee: 0.15% mỗi chiều (0.3% total)
        - slippage: 0.2% mỗi chiều (0.4% total)
        => Tổng ma sát (Friction): ~0.7% cho mỗi vòng quay.
        """
        self.rebalance_freq = rebalance_freq
        self.initial_capital = initial_capital
        self.fee = fee
        self.slippage = slippage
        self.total_friction = (self.fee + self.slippage) * 2
        self.results = None

    def detect_market_regime(self, df_bench, lookback=14, percentile_window=120):
        """
        🧪 REGIME ENGINE V1.0.1: ADX + Rolling Percentile
        Phân loại thị trường: TREND, SIDEWAY, NEUTRAL
        """
        df = df_bench.copy()
        df = df.sort_values('date')

        # 1. Tính True Range (TR) và Directional Movement (+DM, -DM)
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['bench_close'].shift())
        df['tr2'] = abs(df['low'] - df['bench_close'].shift())
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)

        df['up_move'] = df['high'] - df['high'].shift()
        df['down_move'] = df['low'].shift() - df['low']

        df['+dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
        df['-dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)

        # Smoothed (RMA tương đương Wilder's Smoothing)
        tr_smooth = df['tr'].ewm(alpha=1/lookback, adjust=False).mean()
        plus_di = 100 * (df['+dm'].ewm(alpha=1/lookback, adjust=False).mean() / tr_smooth)
        minus_di = 100 * (df['-dm'].ewm(alpha=1/lookback, adjust=False).mean() / tr_smooth)

        # DX và ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        df['adx'] = dx.ewm(alpha=1/lookback, adjust=False).mean()

        # 2. Xếp hạng ADX Động (Rolling Percentile)
        df['adx_percentile'] = df['adx'].rolling(percentile_window).rank(pct=True)

        # 3. Phân loại Regime (V1.0.1 Thresholds)
        conditions = [
            (df['adx_percentile'] >= 0.60), # Top 40% ADX -> TREND
            (df['adx_percentile'] <= 0.35)  # Bottom 35% ADX -> SIDEWAY
        ]
        choices = ['TREND', 'SIDEWAY']
        df['regime'] = np.select(conditions, choices, default='NEUTRAL')

        return df.set_index('date')[['adx', 'adx_percentile', 'regime']]

    def get_adaptive_params(self, regime):
        """
        Trọng số RS và Exposure Ratio thay đổi theo trạng thái thị trường.
        """
        if regime == 'TREND':
            # Bám trend ngắn hạn quyết liệt (1M=50%)
            return {'1m': 0.50, '3m': 0.30, '6m': 0.15, '1y': 0.05}, 1.0
        elif regime == 'SIDEWAY':
            # Bỏ qua nhiễu, nhìn 6M/1Y + Giảm NAV xuống 30%
            return {'1m': 0.10, '3m': 0.20, '6m': 0.40, '1y': 0.30}, 0.3
        else: # NEUTRAL
            # Cân bằng (V1.0 Baseline)
            return {'1m': 0.30, '3m': 0.30, '6m': 0.25, '1y': 0.15}, 0.7

    def run(self, start_date='2023-01-01', end_date=None, top_n=5):
        """ Thực thi mô phỏng chiến lược """
        if end_date is None:
            end_date = datetime.today().strftime('%Y-%m-%d')

        print(f"\n[HEARTBEAT] Engine started for {start_date} to {end_date}")
        print("🚀 ĐANG KHỞI ĐỘNG CỖ MÁY XUYÊN KHÔNG (BACKTEST V5.0)...")
        print(f"📅 Giai đoạn: {start_date} -> {end_date} | Rebalance: {self.rebalance_freq} phiên")

        # 1. Tải dữ liệu với cơ chế Retry siêu cấp (chống lock DB khi đang seeding)
        import time
        df_stocks = pd.DataFrame()
        df_bench = pd.DataFrame()

        for attempt in range(5):
            try:
                db_path = os.path.join(src.config.DATA_DIR, "screener_cache.db")
                conn = sqlite3.connect(db_path, timeout=60)

                # Tải Stocks: lấy cả close (cho P&L/pivot_price) và adj_close (cho RS Momentum)
                stock_query = f"SELECT symbol, date, close, adj_close, volume FROM daily_ohlcv WHERE symbol NOT IN ('VNINDEX', 'VN30') AND date >= '{start_date}' AND date <= '{end_date}'"
                df_stocks = pd.read_sql(stock_query, conn)

                # Tải Benchmark (lấy thêm high, low để tính ADX cho Regime Engine)
                bench_query = f"SELECT date, high, low, adj_close as bench_close FROM daily_ohlcv WHERE symbol = 'VNINDEX' AND date >= '{start_date}' AND date <= '{end_date}'"
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
        df_stocks['date'] = pd.to_datetime(df_stocks['date'], errors='coerce')
        df_bench['date'] = pd.to_datetime(df_bench['date'], errors='coerce')

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

        # --- V4.6 IRON GATE: LIQUIDITY SHIELD (5B avg + 5B current) ---
        print("🏗️  Step 2.2: Calculating Market Value & Breadth (MA50)...")
        pivot_value = pivot_price * pivot_vol * 1000
        avg_value_20 = pivot_value.rolling(20).mean()

        # --- V1.0.2: MARKET BREADTH (Survival Filter) ---
        pivot_ma50 = pivot_price.rolling(50).mean()
        stocks_above_ma50 = (pivot_price > pivot_ma50).sum(axis=1)
        # Chỉ tính trên các mã có thanh khoản tối thiểu (valid_universe cơ bản)
        total_market_stocks = (pivot_value.rolling(20).mean() >= 2_000_000_000).sum(axis=1) # Dùng 2B làm baseline độ rộng
        market_breadth_df = (stocks_above_ma50 / total_market_stocks).fillna(0)

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

        # Tính Ma trận các thành phần RS (V1.0.1: Adaptive Core)
        print("🏗️  Step 2.4: Computing RS Components Matrix...")
        rs_comps = compute_rs_components(df_stocks)

        # --- V1.0.1: KHỞI ĐỘNG REGIME ENGINE ---
        print("🏗️  Step 2.4b: Initializing Regime Engine (ADX Percentile)...")
        regime_df = self.detect_market_regime(df_bench)

        # Baseline RS Matrix (cho Sector/Diffusion logic - V1.0 compatibility)
        rs_matrix_baseline = (rs_comps['1m'] * 0.4 + rs_comps['3m'] * 0.3 +
                             rs_comps['6m'] * 0.2 + rs_comps['1y'] * 0.1).rank(pct=True, axis=1) * 100

        # --- V4.1: TÍNH TOÁN SECTOR VELOCITY (NẤC 1.5) ---
        print("🏗️  Step 2.5: Mapping Sectors...")
        # Chuyển Ma trận RS sang Industry RS Matrix (Date x Industry)
        # Loại bỏ các cột không có mapping ngành để Groupby không lỗi
        mapped_symbols = [s for s in rs_matrix_baseline.columns if s in symbol_to_industry]
        industry_mapping_series = pd.Series(symbol_to_industry)[mapped_symbols]

        industry_rs_matrix = rs_matrix_baseline[mapped_symbols].T.groupby(industry_mapping_series).mean().T
        # Tính Tốc độ thay đổi (Velocity) trong 5 phiên
        industry_rs_velocity = industry_rs_matrix.diff(5)

        # --- V1.0.4: L0.5 CHINA SHIELD (Hysteresis) ---
        print("🏗️  Step 2.6: Computing L0.5 China Nexus Hysteresis...")
        # 1. USDCNY Flag (Breakout MA20 * 1.01)
        usdcny = pivot_macro['USD_CNY'] if 'USD_CNY' in pivot_macro.columns else pd.Series(0, index=pivot_macro.index)
        usdcny_ma20 = usdcny.rolling(20).mean()
        usdcny_flag = usdcny > (usdcny_ma20 * 1.01)

        # 2. SHCOMP Flag (Bearish < MA200)
        shcomp = pivot_macro['SH_COMP'] if 'SH_COMP' in pivot_macro.columns else pd.Series(1, index=pivot_macro.index)
        shcomp_ma200 = shcomp.rolling(200).mean()
        shcomp_flag = shcomp < shcomp_ma200

        # 3. China Risk Hysteresis (5/7 days)
        china_risk_today = usdcny_flag & shcomp_flag
        china_risk_count = china_risk_today.rolling(7).sum()
        is_china_risk_active = china_risk_count >= 5

        # 3. MÔ PHỎNG CHIẾN LƯỢC (V1.0.4 - Shadow Audit Ready)
        all_dates = sorted(pivot_price.index.unique())
        equity = [self.initial_capital]
        current_holdings = []
        portfolio_returns = []

        # V1.0.4 Forensic & Shadow Tracking
        entry_prices = {}
        entry_dates_idx = {} # Track index ngày mua
        shadow_log = []
        stop_loss_count = 0
        macro_save_count = 0
        regime_history = []
        trade_log = []
        turnover_count = 0

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

                # 🛡️ Cảm biến L1b: CNY Stability (V5.0)
                # Nếu NDT biến động > 1.5% trong 10 phiên -> Cảnh báo rủi ro rút vốn
                if 'USD_CNY' in pivot_macro.columns:
                    cny = pivot_macro.loc[yesterday, 'USD_CNY']
                    cny_ma = macro_ma20.loc[yesterday, 'USD_CNY']
                    if cny > cny_ma * 1.015:
                        is_macro_safe_l1 = False
                        # macro_save_count += 1

                # 🛡️ Cảm biến L2: BTC & Gold (Trọng số 30%)
                btc = pivot_macro.loc[yesterday, 'BTC'] if 'BTC' in pivot_macro.columns else 0
                btc_ma = macro_ma20.loc[yesterday, 'BTC'] if 'BTC' in macro_ma20.columns else 0
                gold = pivot_macro.loc[yesterday, 'GOLD_XAU'] if 'GOLD_XAU' in pivot_macro.columns else 0
                gold_ma = macro_ma20.loc[yesterday, 'GOLD_XAU'] if 'GOLD_XAU' in macro_ma20.columns else 0

                # Nếu BTC sập > 10% hoặc Vàng tăng sốc > 5% -> Ngừng mua mới
                if (btc < btc_ma * 0.90) or (gold > gold_ma * 1.05):
                    allow_new_buy = False

            # --- 3.1. TÁI CƠ CẤU (Bản vá V1.0.4: Periodic Buy) ---
            if yesterday in rebalance_dates:
                current_regime = regime_df.loc[yesterday, 'regime'] if yesterday in regime_df.index else 'NEUTRAL'
                regime_history.append(current_regime)

                weights, exposure_ratio = self.get_adaptive_params(current_regime)

                # --- V1.0.4: L0.5 China Scale ---
                if yesterday in is_china_risk_active.index and is_china_risk_active.loc[yesterday]:
                    exposure_ratio *= 0.7 # Scale down by 30% on regional stress

                # Tính RS Score Động
                rs_at_date = {k: rs_comps[k].loc[yesterday] for k in rs_comps}
                weighted_rs_raw = (rs_at_date['1m'] * weights['1m'] +
                                  rs_at_date['3m'] * weights['3m'] +
                                  rs_at_date['6m'] * weights['6m'] +
                                  rs_at_date['1y'] * weights['1y']).fillna(0)

                daily_rs_rank = weighted_rs_raw.rank(pct=True) * 100

                # Universe Selection (V1.0.2 Survival logic stays in Rebalance)
                val_yesterday = avg_value_20.loc[yesterday]
                price_yesterday = pivot_price.loc[yesterday]
                ma20_yesterday = pivot_ma20.loc[yesterday]
                perf_6m_yesterday = rs_comps['6m'].loc[yesterday]
                value_current_day = pivot_value.loc[yesterday] if yesterday in pivot_value.index else pd.Series(0, index=val_yesterday.index)

                valid_universe = (
                    (price_yesterday >= 10) &
                    (val_yesterday >= 10_000_000_000) &
                    (value_current_day >= 10_000_000_000) &
                    (price_yesterday > ma20_yesterday) &
                    (perf_6m_yesterday > 0)
                )

                filtered_rs = daily_rs_rank[valid_universe]
                target_nav_ratio = exposure_ratio

                # Macro Council
                if not is_macro_safe_l1:
                    target_nav_ratio = 0.0
                elif not allow_new_buy:
                    target_nav_ratio = 0.3

                # Market Breadth Filter (L0.3)
                current_breadth = market_breadth_df.get(yesterday, 0)
                if current_breadth < 0.15:
                    target_nav_ratio = 0.0
                elif current_breadth < 0.35:
                    target_nav_ratio = 0.3

                # Đi vốn (Capital Allocation - Selective)
                max_slots = int(top_n * target_nav_ratio)

                # SCOUT NEW POSITIONS
                candidate_holdings = filtered_rs.sort_values(ascending=False).index.tolist()
                new_holdings = []

                # Ưu tiên các mã cũ còn sống
                for t in current_holdings:
                    if len(new_holdings) < max_slots:
                        new_holdings.append(t)

                # Điền slot mới
                for c in candidate_holdings:
                    if len(new_holdings) < max_slots and c not in new_holdings:
                        new_holdings.append(c)

                # Turnover Tracking
                if i > 1 and set(new_holdings) != set(current_holdings):
                    equity[-1] *= (1 - self.total_friction)
                    turnover_count += 1

                current_holdings = new_holdings
                for ticker in current_holdings:
                    if ticker not in entry_prices:
                        entry_prices[ticker] = pivot_price.loc[today, ticker]
                        entry_dates_idx[ticker] = i

            # --- 3.2. CẢM BIẾN SINH TỒN (Bản vá V1.0.4: Daily Sell / Option B) ---
            to_remove = []
            for ticker in list(current_holdings):
                today_price = pivot_price.loc[today, ticker]
                entry = entry_prices.get(ticker, today_price)
                price_pct = (today_price / entry) - 1

                # Rule 1: Stop-Loss (-7% or Panic)
                today_vol = pivot_vol.loc[today, ticker]
                ticker_avg_vol = avg_value_20.loc[today, ticker] / (today_price * 1000) if today_price > 0 else 1
                is_panic_sell = (price_pct < -0.05) and (today_vol > ticker_avg_vol * 1.5)
                sl_threshold = -0.07 # Brutal baseline

                # Rule 2: Absolute Momentum Breached (MA20 or 6M)
                ma20_t = pivot_ma20.loc[today, ticker]
                is_momentum_dead = (today_price < ma20_t * 0.98) # Thủng MA20 2%

                # Rule 3: Cooldown Expiry Check (Only sell if held > 15 days OR SL hit)
                days_held = i - entry_dates_idx.get(ticker, i)

                if is_panic_sell or price_pct < sl_threshold or (is_momentum_dead and days_held >= 15):
                    to_remove.append(ticker)
                    trade_log.append(price_pct)
                    stop_loss_count += 1

            for ticker in to_remove:
                if ticker in current_holdings:
                    current_holdings.remove(ticker)

                # V1.0.4 Shadow Log (Sell)
                shadow_log.append({
                    'date': today.strftime('%Y-%m-%d'),
                    'symbol': ticker,
                    'signal': 'SELL',
                    'reason': 'STOP_LOSS' if (price_pct < sl_threshold or is_panic_sell) else 'MOMENTUM_EXPIRY',
                    'model_price': today_price,
                    'entry_price': entry,
                    'hold_days': days_held
                })

                if ticker in entry_prices: del entry_prices[ticker]
                if ticker in entry_dates_idx: del entry_dates_idx[ticker]

            # --- V1.0.4: Shadow-Live Telemetry (DAILY APPEND) ---
            # Chỉ ghi log chi tiết nếu có sự kiện hoặc để báo cáo sức khỏe hằng ngày
            shadow_log.append({
                'date': today.strftime('%Y-%m-%d'),
                'breadth_pct': float(current_breadth * 100),
                'china_risk_active': bool(is_china_risk_active.get(today, False)),
                'macro_safe_l1': bool(is_macro_safe_l1),
                'target_nav_ratio': float(target_nav_ratio),
                'eligible_count': int(len(filtered_rs)),
                'holdings_count': len(current_holdings),
                'cash_ratio': float(1.0 - (len(current_holdings) / top_n) if top_n > 0 else 1.0)
            })

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
        print("📊 BÁO CÁO ĐÁNH GIÁ ALPHA v4.3 (Macro Hub + Risk Shield)")
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

        # 5. LƯU KẾT QUẢ & SHADOW LOG
        output_dir = os.path.join(src.config.PROJECT_ROOT, "data", "output")
        os.makedirs(output_dir, exist_ok=True)
        results_df.to_json(os.path.join(output_dir, "backtest_results.json"),
                           orient='records', force_ascii=False, indent=4)

        # Shadow Log Export (V1.0.4)
        shadow_df = pd.DataFrame(shadow_log)
        if not shadow_df.empty:
            shadow_df.to_json(os.path.join(output_dir, "shadow_signals.json"),
                             orient='records', force_ascii=False, indent=4)

        metrics = {
            'total_ret': total_ret,
            'bench_ret': bench_ret,
            'alpha': total_ret - bench_ret,
            'sharpe': sharpe,
            'info_ratio': info_ratio,
            'mdd': mdd,
            'win_rate': (np.array(portfolio_returns) > 0).sum() / len(portfolio_returns) * 100,
            'regime_dist': pd.Series(regime_history).value_counts(normalize=True).to_dict(),
            'trade_log': trade_log,
            'turnover': turnover_count
        }

        return results_df, metrics

if __name__ == "__main__":
    bt = BacktestAlpha(rebalance_freq=10)
    # BẢN ĐỒ ALPHA TOÀN DIỆN 2023-2026
    bt.run(start_date='2023-01-01')

