import sys
import os
from pathlib import Path
from datetime import datetime
import pandas as pd

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

def run_validation_protocol():
    """
    🧪 IRON GATE VALIDATION PROTOCOL (V5.0)
    Executes a 3-phase Walk-Forward Validation (WFT) to detect overfitting.
    """
    print("\n" + "="*65)
    print("🧪 [SENTINEL V5.0] INITIALIZING WALK-FORWARD VALIDATION")
    print("="*65)

    # 1. Define Phases
    # Train: 2020-2022 (Bear & Bull cycle)
    # Test: 2023-2024 (Recovery)
    # Forward: 2025-Present (Out-of-sample)
    phases = [
        {"name": "1. TRAIN (Bear/Bull)", "start": "2021-01-04", "end": "2022-12-31"},
        {"name": "2. TEST (Recovery)",  "start": "2023-01-01", "end": "2024-12-31"},
        {"name": "3. FORWARD (OOS)",    "start": "2025-01-01", "end": datetime.today().strftime('%Y-%m-%d')}
    ]

    bt = BacktestAlpha(rebalance_freq=10)
    all_metrics = []

    # 2. Execute Phases
    for ph in phases:
        print(f"\n⚡ EXECUTING PHASE: {ph['name']}")
        print(f"📅 Range: {ph['start']} to {ph['end']}")
        
        try:
            _, m = bt.run(start_date=ph['start'], end_date=ph['end'], top_n=5)
            m['phase'] = ph['name']
            all_metrics.append(m)
        except Exception as e:
            print(f"❌ PHASE FAILED: {ph['name']} | Error: {e}")

    # 3. Consolidation & Reporting
    if not all_metrics:
        print("\n⚠️ No validation data generated.")
        return

    report_df = pd.DataFrame(all_metrics)
    
    print("\n" + "="*85)
    print("🏆 IRON GATE SCORECARD: WALK-FORWARD VALIDATION (V5.0)")
    print("="*85)
    print(f"{'PHASE':<25} | {'ALPHA':>8} | {'SHARPE':>8} | {'MDD':>10} | {'WIN RATE':>10}")
    print("-" * 85)
    
    for _, row in report_df.iterrows():
        alpha_color = "✅" if row['alpha'] > 20 else "🟡" if row['alpha'] > 0 else "🔴"
        sharpe_color = "💎" if row['sharpe'] >= 1.0 else "⚙️" if row['sharpe'] >= 0.8 else "⚠️"
        
        print(f"{row['phase']:<25} | {row['alpha']:>7.1f}% {alpha_color} | {row['sharpe']:>8.2f} {sharpe_color} | {row['mdd']:>9.1f}% | {row['win_rate']:>9.1f}%")
    
    print("=" * 85)
    print("LEGEND: Alpha > 20% ✅ | Sharpe > 0.8 ⚙️ | Sharpe > 1.0 💎")
    
    # Final Verdict (V1.0.1: Realistic 0.7% Friction standards)
    forward_alpha = report_df[report_df['phase'].str.contains("FORWARD")]['alpha'].values[0] if not report_df[report_df['phase'].str.contains("FORWARD")].empty else 0
    if forward_alpha > 10:
        print("\n🔥 VERDICT: SYSTEM IS ROBUST - ALPHA SURVIVED FRICTION IN FORWARD TESTING.")
    elif forward_alpha > 0:
        print("\n🟡 VERDICT: SYSTEM IS FRAGILE - ALPHA DECAY DETECTED BUT POSITIVE.")
    else:
        print("\n🔴 VERDICT: SYSTEM OVERFIT - ALPHA COLLAPSED IN FORWARD TESTING.")

if __name__ == "__main__":
    run_validation_protocol()
