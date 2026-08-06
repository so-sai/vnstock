"""Stress-Test: INTERBANK_ON spike to 9.5%.

Simulates a 2011-2012 style interbank liquidity shock.
Updates INTERBANK_ON in macro_history, runs VN20 pipeline,
then restores original value.
"""

import os
import sqlite3
import sys
from pathlib import Path

os.environ["PYTHONUTF8"] = "1"

DB_PATH = Path("backend/data/screener_cache.db")
STRESSED_RATE = 9.5
TARGET_DATE = "2026-08-06"


def get_current_interbank() -> float | None:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT value FROM macro_history WHERE variable = 'INTERBANK_ON' ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()
    return float(row[0]) if row else None


def inject_stressed_rate(rate: float) -> None:
    """UPDATE existing row or INSERT if not exists."""
    conn = sqlite3.connect(str(DB_PATH))
    existing = conn.execute(
        "SELECT value FROM macro_history WHERE variable = 'INTERBANK_ON' AND date = ?",
        (TARGET_DATE,),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE macro_history SET value = ? WHERE variable = 'INTERBANK_ON' AND date = ?",
            (rate, TARGET_DATE),
        )
    else:
        conn.execute(
            "INSERT INTO macro_history (variable, date, value) VALUES ('INTERBANK_ON', ?, ?)",
            (TARGET_DATE, rate),
        )
    conn.commit()
    conn.close()


def restore_original(rate: float | None) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    if rate is not None:
        conn.execute(
            "UPDATE macro_history SET value = ? WHERE variable = 'INTERBANK_ON' AND date = ?",
            (rate, TARGET_DATE),
        )
    conn.commit()
    conn.close()


def compute_lri() -> object:
    sys.path.insert(0, "backend")
    from src.governor.liquidity_recovery_index import LiquidityRecoveryIndex

    lri_engine = LiquidityRecoveryIndex()
    return lri_engine.compute(target_date=TARGET_DATE)


def main():
    print("=" * 90)
    print("  STRESS-TEST: INTERBANK_ON SPIKE -> 9.5%")
    print("=" * 90)

    # 1. Get current rate
    original = get_current_interbank()
    print(f"\n  Current INTERBANK_ON: {original}%")

    # 2. Compute baseline LRI
    print("\n  [BASELINE] Computing LRI with current rate...")
    baseline = compute_lri()
    print(f"    LRI = {baseline.lri}  Regime = {baseline.regime}")
    print(f"    S_Interbank = {baseline.s_interbank:.4f}")
    print(f"    Max Allocation = {baseline.max_allocation_pct}%")

    # 3. Inject stressed rate
    print(f"\n  [STRESS] Injecting INTERBANK_ON = {STRESSED_RATE}%...")
    inject_stressed_rate(STRESSED_RATE)

    # Verify injection
    verify = get_current_interbank()
    print(f"  [STRESS] Verified INTERBANK_ON = {verify}%")

    # 4. Compute stressed LRI
    print("  [STRESS] Computing LRI with stressed rate...")
    stressed = compute_lri()
    print(f"    LRI = {stressed.lri}  Regime = {stressed.regime}")
    print(f"    S_Interbank = {stressed.s_interbank:.4f}")
    print(f"    Max Allocation = {stressed.max_allocation_pct}%")

    # 5. Run VN20 under stress
    print("\n  [STRESS] Running VN20 pipeline under stress...")
    import subprocess

    result = subprocess.run(
        [sys.executable, "ptck.py", "vn20"],
        capture_output=True,
        text=True,
        cwd=os.getcwd(),
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    print(result.stdout)

    # 6. Restore original
    print(f"\n  [RESTORE] Restoring INTERBANK_ON = {original}%...")
    restore_original(original)
    print("  [RESTORE] Done.")

    # 7. Comparison summary
    print("\n" + "=" * 90)
    print("  STRESS-TEST COMPARISON")
    print("=" * 90)
    print(f"  {'Metric':<30} {'Baseline':>15} {'Stressed':>15} {'Delta':>15}")
    print("  " + "-" * 75)
    delta_rate = STRESSED_RATE - (original or 0)
    delta_lri = stressed.lri - baseline.lri
    delta_sib = stressed.s_interbank - baseline.s_interbank
    delta_alloc = stressed.max_allocation_pct - baseline.max_allocation_pct
    regime_shift = "YES" if baseline.regime != stressed.regime else "NO"
    print(f"  {'INTERBANK_ON':<30} {original!s:>14}% {STRESSED_RATE!s:>14}% {delta_rate:>+14.1f}%")
    print(f"  {'LRI Score':<30} {baseline.lri:>15} {stressed.lri:>15} {delta_lri:>+15.4f}")
    print(f"  {'Regime':<30} {baseline.regime:>15} {stressed.regime:>15} {regime_shift:>15}")
    print(
        f"  {'S_Interbank (30% weight)':<30} {baseline.s_interbank:>15.4f} {stressed.s_interbank:>15.4f} {delta_sib:>+15.4f}"
    )
    print(
        f"  {'Max Allocation %':<30} {baseline.max_allocation_pct:>14.1f}%"
        f" {stressed.max_allocation_pct:>14.1f}% {delta_alloc:>+14.1f}%"
    )
    print("=" * 90)


if __name__ == "__main__":
    main()
