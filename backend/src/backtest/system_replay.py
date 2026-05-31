import os
import sys
import pandas as pd
import numpy as np
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
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()
from src.database.db_core import get_connection, optimize_sqlite_engine
from src.engine.decision_engine import merge_decisions
from src.database.timeline_manager import log_regime_state
from src.backtest.audit_generator import generate_audit_report

class ShadowExecutionTracker:
    """
    Tracks 'Mental Trades' for Model B picks to measure Alpha Precision and Latency.
    B2.2 Exit Stack: trailing stop, max_hold=10, momentum failure, profit decay.
    """
    def __init__(self):
        self.active_trades = []
        self.completed_trades = []
        self._atr_cache = {}

    def _get_atr(self, symbol, target_date, conn):
        key = (symbol, target_date)
        if key in self._atr_cache:
            return self._atr_cache[key]
        atr_query = f"""
            SELECT AVG(ABS(close - LAG(close, 1) OVER (ORDER BY date))) as atr14
            FROM daily_ohlcv
            WHERE symbol = '{symbol}' AND date <= '{target_date}'
            ORDER BY date DESC LIMIT 14
        """
        try:
            df = pd.read_sql(atr_query, conn)
            atr_val = df.iloc[0]['atr14'] if not df.empty and df.iloc[0]['atr14'] else 0
        except Exception:
            atr_val = 0
        self._atr_cache[key] = atr_val
        return atr_val

    def _get_prev_day(self, symbol, target_date, conn):
        prev_query = f"""
            SELECT close, volume FROM daily_ohlcv
            WHERE symbol = '{symbol}' AND date < '{target_date}'
            ORDER BY date DESC LIMIT 1
        """
        df = pd.read_sql(prev_query, conn)
        if df.empty:
            return None, None
        return df.iloc[0]['close'], df.iloc[0]['volume']

    def log_picks(self, picks, target_date, conn):
        for p in picks:
            symbol = p['symbol']
            price_query = f"SELECT close FROM daily_ohlcv WHERE symbol='{symbol}' AND date = '{target_date}'"
            res = pd.read_sql(price_query, conn)
            if res.empty: continue

            entry_price = res.iloc[0]['close']
            atr14 = self._get_atr(symbol, target_date, conn)

            self.active_trades.append({
                "symbol": symbol,
                "entry_date": target_date,
                "entry_price": entry_price,
                "entry_atr": atr14,
                "t_count": 0,
                "latency": -1,
                "peak_pnl": 0.0,
                "trailing_active": False,
                "trailing_stop": None,
                "exit_reason": None,
                "results": {},
            })

    def update_trades(self, target_date, conn):
        to_remove = []
        for trade in self.active_trades:
            symbol = trade['symbol']
            trade['t_count'] += 1

            query = f"SELECT close, volume, low FROM daily_ohlcv WHERE symbol='{symbol}' AND date = '{target_date}'"
            res = pd.read_sql(query, conn)
            if res.empty: continue

            row = res.iloc[0]
            current_close = row['close']
            current_volume = row['volume']
            today_low = row['low']
            ret = (current_close / trade['entry_price'] - 1) * 100

            trade['results'][f"T+{trade['t_count']}"] = round(ret, 2)

            # Latency Tracker
            if trade['latency'] == -1 and ret > 0.5:
                trade['latency'] = trade['t_count']

            # Track peak PNL
            if ret > trade['peak_pnl']:
                trade['peak_pnl'] = ret

            # Trigger trailing stop at +4%
            if not trade['trailing_active'] and ret >= 4.0:
                trade['trailing_active'] = True
                atr14 = self._get_atr(symbol, target_date, conn)
                trade['trailing_stop'] = trade['entry_price'] * (1 + (ret - max(2 * atr14 / trade['entry_price'] * 100, 1.5)) / 100)

            # Update trailing stop if active
            if trade['trailing_active'] and current_close > trade['trailing_stop']:
                atr14 = self._get_atr(symbol, target_date, conn)
                trail_dist = max(2 * atr14 / trade['entry_price'] * 100, 1.5)
                trade['trailing_stop'] = current_close * (1 - trail_dist / 100)

            # EXIT CONDITIONS
            exit_reason = None

            # 1. Trailing stop hit
            if trade['trailing_active'] and current_close <= trade['trailing_stop']:
                exit_reason = "TRAILING_STOP"

            # 2. Max hold = 10 sessions
            if trade['t_count'] >= 10:
                exit_reason = "MAX_HOLD"

            # 3. Momentum failure: close < yesterday_low + volume expansion
            if trade['peak_pnl'] > 2.0 and trade['latency'] != -1:
                prev_close, prev_vol = self._get_prev_day(symbol, target_date, conn)
                if prev_close and prev_vol:
                    if current_close < prev_close and current_volume > prev_vol * 1.3:
                        exit_reason = "MOMENTUM_FAILURE"

            # 4. Profit decay: peak was > 3%, now below 1% 
            if trade['peak_pnl'] > 3.0 and ret < 1.0 and trade['t_count'] >= 5:
                exit_reason = "PROFIT_DECAY"

            if exit_reason:
                trade['exit_reason'] = exit_reason
                trade['exit_return'] = round(ret, 2)
                self.completed_trades.append(trade)
                to_remove.append(trade)

        for t in to_remove:
            self.active_trades.remove(t)

    def get_audit_summary(self):
        if not self.completed_trades:
            return {"avg_latency": 0, "avg_t10": 0, "avg_t5": 0, "trade_count": 0, "exit_reasons": {}}

        latencies = [t['latency'] for t in self.completed_trades if t['latency'] != -1]
        t5_returns = [t['results'].get('T+5', 0) for t in self.completed_trades]
        t10_returns = [t['results'].get('T+10', 0) for t in self.completed_trades]
        exit_reasons = {}
        for t in self.completed_trades:
            r = t.get('exit_reason', 'UNKNOWN')
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        returns = [t.get('exit_return', 0) for t in self.completed_trades]

        return {
            "avg_latency": np.mean(latencies) if latencies else 0,
            "avg_t5": np.mean(t5_returns) if t5_returns else 0,
            "avg_t10": np.mean(t10_returns) if t10_returns else 0,
            "avg_exit_return": np.mean(returns) if returns else 0,
            "trade_count": len(self.completed_trades),
            "exit_reasons": exit_reasons,
        }

def run_stress_test(start_date, end_date):
    """
    STRESS REPLAY ENGINE v1.2 (Shadow Hardened).
    Includes the 4 Safety Hooks and Shadow Execution Tracker.
    """
    print(f"\n{'='*60}")
    print(f"[THE DRAGON SHIELD] 2023 SIDEWAY HELL REPLAY")
    print(f"Period: {start_date} to {end_date}")
    print(f"{'='*60}")

    # 1. Prepare Environment
    optimize_sqlite_engine()
    tracker = ShadowExecutionTracker()
    
    with get_connection() as conn:
        conn.execute("DELETE FROM regime_history")
    
    # 2. Fetch trading days
    with get_connection() as conn:
        df_days = pd.read_sql(f"SELECT DISTINCT date FROM daily_ohlcv WHERE symbol='VNINDEX' AND date BETWEEN '{start_date}' AND '{end_date}' ORDER BY date", conn)
    
    if df_days.empty:
        print("❌ Error: No historical data found.")
        return

    trading_days = df_days['date'].tolist()
    
    # 3. Simulation Loop
    results = []
    prev_status = None
    regime_flips = 0
    dead_zone_days = 0
    breadth_history = []
    
    for i, target_date in enumerate(trading_days):
        print(f"\n>>> REPLAY DAY {i+1}/{len(trading_days)}: [{target_date}]")
        
        try:
            # 3.1 Decision Logic
            verdict = merge_decisions(target_date=target_date)
            log_regime_state(verdict)
            
            # 3.2 Shadow Execution & Hook 4: Dead Zone
            with get_connection() as conn:
                tracker.update_trades(target_date, conn)
                if verdict['active_model'].startswith("B"):
                    if verdict['model_b']['top_picks']:
                        tracker.log_picks(verdict['model_b']['top_picks'], target_date, conn)
                    elif verdict['market_status'] == "RANGING":
                        dead_zone_days += 1
            
            # 3.3 Hook 3: Regime Flip Counter
            current_status = verdict['market_status']
            if prev_status and current_status != prev_status:
                regime_flips += 1
            prev_status = current_status
            
            # 3.4 Hook 2: Breadth Stability (Rolling 10D)
            breadth_history.append(verdict['details']['breadth_pct'])
            if len(breadth_history) > 10: breadth_history.pop(0)
            b_std = np.std(breadth_history) if len(breadth_history) >= 2 else 0.0
            
            # 3.5 Peak/DD Logic (Index-based)
            with get_connection() as conn:
                current_idx = pd.read_sql(f"SELECT close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date = '{target_date}'", conn).iloc[0]['close']
            
            # Extract context_source from the first pick if available
            model_b_context = "BLOCKED"
            if verdict['model_b']['top_picks']:
                model_b_context = verdict['model_b']['top_picks'][0].get('context_source', 'UNKNOWN')
            elif verdict['market_status'] == "RANGING":
                model_b_context = "BLOCKED"

            results.append({
                "date": target_date,
                "regime": verdict['regime_score'],
                "status": current_status,
                "consensus": verdict['consensus'],
                "idx_close": current_idx,
                "breadth_pct": verdict['details']['breadth_pct'],
                "breadth_std_10d": round(b_std, 2),
                "model_b_picks": verdict['model_b']['picks_count'],
                "model_b_context": model_b_context,
                "recovery": verdict['recovery']['status'],
                "block_reason": verdict['recovery'].get('block_reason', 'NONE')
            })
            
        except Exception as e:
            print(f"[ERROR] processing {target_date}: {e}")
            continue

    # 4. Final Audit Aggregation
    summary = tracker.get_audit_summary()
    total_ranging = sum(1 for r in results if r['status'] == "RANGING")
    
    df_results = pd.DataFrame(results)
    # Add scalar columns for the audit_generator (will be repeated but visible)
    df_results['regime_flips'] = regime_flips
    df_results['dead_zone_days'] = dead_zone_days
    df_results['ranging_total'] = total_ranging
    df_results['avg_latency'] = round(summary['avg_latency'], 2)
    df_results['avg_t5'] = round(summary['avg_t5'], 2)
    df_results['avg_t10'] = round(summary['avg_t10'], 2)
    df_results['avg_exit_return'] = round(summary['avg_exit_return'], 2)
    df_results['shadow_trades'] = summary['trade_count']
    df_results['exit_reasons'] = str(summary.get('exit_reasons', {}))

    # Max Drawdown Index calculation
    peak = df_results['idx_close'].cummax()
    df_results['drawdown'] = (peak - df_results['idx_close']) / peak * 100

    output_path = PROJECT_ROOT / "data" / "reports" / f"replay_audit_{start_date}_{end_date}.csv"
    os.makedirs(output_path.parent, exist_ok=True)
    df_results.to_csv(output_path, index=False)

    print(f"\n{'='*60}")
    print(f"[B2.2 REPLAY COMPLETE]")
    print(f"Total Flips: {regime_flips} | Dead Zone: {dead_zone_days}/{total_ranging} days")
    print(f"Shadow Alpha (T+10): {summary['avg_t10']:.2f}% | Latency: {summary['avg_latency']:.1f} days")
    print(f"Exit Return: {summary['avg_exit_return']:.2f}% | Trades: {summary['trade_count']}")
    print(f"Exit Reasons: {summary.get('exit_reasons', {})}")
    print(f"{'='*60}")

    generate_audit_report(str(output_path))

if __name__ == "__main__":
    # Mission 2: Sideway Hell 2023
    run_stress_test("2023-01-01", "2023-12-31")
