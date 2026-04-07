import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path

def _hydrate_path():
    """Zero-Friction Sentinel v2.1"""
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / ".kit").exists() or (current / "src").is_dir() or (current / "screener.py").exists():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path

PROJECT_ROOT = _hydrate_path()
from src.engine.backtest_engine import BacktestAlpha

def run_forensic_audit():
    print("\n" + "="*65)
    print("🕵️  ALPHA FORGE V1.0.1: FORENSIC AUDIT (OOS 2025-2026)")
    print("="*65)

    bt = BacktestAlpha(rebalance_freq=10)
    
    # 1. Audit Year 2025
    print("\n[Audit] Running Year 2025...")
    _, m2025 = bt.run(start_date='2025-01-01', end_date='2025-12-31')
    
    # 2. Audit Year 2026 (Forward)
    print("\n[Audit] Running Year 2026...")
    _, m2026 = bt.run(start_date='2026-01-01')

    # 3. Aggregation & Metrics
    def calc_expectancy(trade_log):
        if not trade_log: return 0
        df = pd.Series(trade_log)
        win_rate = (df > 0).mean()
        avg_win = df[df > 0].mean() if not df[df > 0].empty else 0
        avg_loss = df[df < 0].mean() if not df[df < 0].empty else 0
        return (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    exp2025 = calc_expectancy(m2025['trade_log'])
    exp2026 = calc_expectancy(m2026['trade_log'])

    print("\n" + "="*70)
    print("🔬 FINAL DIAGNOSTIC: BINARY TRUTH")
    print("="*70)
    print(f"{'METRIC':<25} | {'YEAR 2025':>15} | {'YEAR 2026':>15}")
    print("-" * 70)
    print(f"{'Alpha (%)':<25} | {(m2025['total_ret'] - m2025['bench_ret']):>14.2f}% | {(m2026['total_ret'] - m2026['bench_ret']):>14.2f}%")
    print(f"{'Sharpe Ratio':<25} | {m2025['sharpe']:>15.2f} | {m2026['sharpe']:>15.2f}")
    print(f"{'Max Drawdown (%)':<25} | {m2025['mdd']:>14.2f}% | {m2026['mdd']:>14.2f}%")
    print(f"{'Trade Expectancy':<25} | {exp2025:>15.4f} | {exp2026:>15.4f}")
    print(f"{'Turnover (Trades)':<25} | {m2025['turnover']:>15} | {m2026['turnover']:>15}")
    
    print("-" * 70)
    print("📡 REGIME DISTRIBUTION (% of Time)")
    dist25 = m2025['regime_dist']
    dist26 = m2026['regime_dist']
    print(f"{'TRENDing Mode':<25} | {(dist25.get('TREND', 0)*100):>14.1f}% | {(dist26.get('TREND', 0)*100):>14.1f}%")
    print(f"{'SIDEWAY Mode':<25} | {(dist25.get('SIDEWAY', 0)*100):>14.1f}% | {(dist26.get('SIDEWAY', 0)*100):>14.1f}%")
    print(f"{'NEUTRAL Mode':<25} | {(dist25.get('NEUTRAL', 0)*100):>14.1f}% | {(dist26.get('NEUTRAL', 0)*100):>14.1f}%")
    print("="*70)

if __name__ == "__main__":
    run_forensic_audit()
