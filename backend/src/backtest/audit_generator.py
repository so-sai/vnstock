import os
import sys
from pathlib import Path

import pandas as pd


# Sentinel v2.1 (Anchor Fix)
def _hydrate_path():
    if getattr(sys, "frozen", False):
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


def generate_audit_report(csv_path):
    """
    Analyzes the Replay Audit CSV and calculates the 4 Institutional Pillars + 4 Safety Hooks.
    """
    if not os.path.exists(csv_path):
        print(f"❌ Error: Replay file not found at {csv_path}")
        return

    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"], format="mixed")

    # --- 1. INSTITUTIONAL PILLARS ---
    max_dd = df["drawdown"].max()

    # Recovery Delay (Index V-Shape)
    bottom_idx = df["idx_close"].idxmin()
    bottom_date = df.loc[bottom_idx, "date"]
    recovery_events = df[(df["date"] >= bottom_date) & (df["recovery"] == "RECOVERY_ACTIVE")]
    delay = (recovery_events.iloc[0]["date"] - bottom_date).days if not recovery_events.empty else -1

    survival_rate = 100 - max_dd

    # False Recovery Rate
    false_count = 0
    recovery_indices = df[df["recovery"] == "RECOVERY_ACTIVE"].index
    for idx in recovery_indices:
        future = df.iloc[idx + 1 : idx + 6]
        if any(future["status"] == "CRISIS") or any(future["idx_close"] < df.loc[idx, "idx_close"]):
            false_count += 1

    # --- 2. THE 4 SAFETY HOOKS (MISSION 2 SPECIAL) ---
    regime_flips = df["regime_flips"].iloc[0] if "regime_flips" in df.columns else 0
    dead_zone_days = df["dead_zone_days"].iloc[0] if "dead_zone_days" in df.columns else 0
    ranging_total = df["ranging_total"].iloc[0] if "ranging_total" in df.columns else 1
    avg_latency = df["avg_latency"].iloc[0] if "avg_latency" in df.columns else 0
    avg_t10 = df["avg_t10"].iloc[0] if "avg_t10" in df.columns else 0

    dead_zone_pct = (dead_zone_days / ranging_total) * 100
    breadth_stability = df["breadth_std_10d"].mean() if "breadth_std_10d" in df.columns else 0

    # Print Report
    print("\n" + "#" * 65)
    print("      INSTITUTIONAL STRESS TEST AUDIT: MISSION 2 (2023)")
    print("#" * 65)
    print(f"Target Period:  {df['date'].min().date()} to {df['date'].max().date()}")
    print(f"Regime Flips:   {regime_flips} times | Breadth Stability: {breadth_stability:.2f} (Avg Std)")
    print("-" * 65)
    print("CORE PILLARS:")
    print(f"1. MAX DRAWDOWN:         {max_dd:>8.2f}%  (Target: < 20%)")
    print(f"2. RECOVERY DELAY:       {delay:>8} Days  (Target: <= 10 Days)")
    print(f"3. CAPITAL SURVIVAL:     {survival_rate:>8.2f}%  (Target: > 85%)")
    print(f"4. FALSE RECOVERY RATE:  {false_count:>8} Times (Target: < 3)")
    print("-" * 65)
    print("MODEL B (MEAN REVERSION) HARDENING:")
    print(f"5. SHADOW ALPHA (T+10):  {avg_t10:>8.2f}%  (Primary Alpha)")
    print(f"6. SIGNAL LATENCY:       {avg_latency:>8.1f} Days  (Target: < 5 Days)")
    print(f"7. DEAD ZONE RATIO:      {dead_zone_pct:>8.1f}%  (Target: < 40%)")
    print("-" * 65)

    # Audit Verdict for Mission 2
    pillar_pass = max_dd < 20 and (delay != -1 and delay <= 10) and false_count < 3
    hook_pass = avg_latency < 8 and dead_zone_pct < 45

    verdict = "✅ PASS" if pillar_pass and hook_pass else "❌ FAIL"
    print(f"FINAL AUDIT VERDICT: {verdict}")
    if not hook_pass:
        if avg_latency >= 8:
            print("   ⚠️ ADVISORY: Model B latency is too high. Tuning needed.")
        if dead_zone_pct >= 45:
            print("   ⚠️ ADVISORY: Thresholds are too restrictive (High Dead Zone).")

    print("#" * 65 + "\n")

    return {
        "max_dd": max_dd,
        "delay": delay,
        "survival": survival_rate,
        "false_recoveries": false_count,
        "avg_latency": avg_latency,
        "dead_zone_pct": dead_zone_pct,
        "verdict": verdict,
    }


if __name__ == "__main__":
    import glob

    # Find latest audit file
    files = glob.glob("data/reports/replay_audit_*.csv")
    if files:
        latest = max(files, key=os.path.getctime)
        generate_audit_report(latest)
    else:
        print("No audit CSV files found. Run system_replay.py first.")
