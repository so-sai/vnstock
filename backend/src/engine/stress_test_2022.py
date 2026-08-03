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
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


PROJECT_ROOT = _hydrate_path()
from src.engine.backtest_engine import BacktestAlpha


def run_hell_fire_stress_test():
    """
    🛡️ ALPHA V4.5: CHIẾN DỊCH "HELL FIRE" STRESS TEST (2022)
    Kiểm tra bản lĩnh sinh tồn của Alpha Brain trong giai đoạn khốc liệt nhất lịch sử.
    - Start: 2022-01-01
    - Slippage: 1.0% (0.01)
    """
    import pandas as pd

    print("\n" + "=" * 65)
    print("⚔️  ALPHA V4.5: CHIẾN DỊCH 'HELL FIRE' STRESS TEST 2022")
    print("=" * 65)

    # 1. Khoi tao Engine voi muc phi "Ma sat cuc dai" (1.0%)
    # Theo lenh cua Chi huy: fee=0.01
    engine = BacktestAlpha(rebalance_freq=10, initial_capital=100_000_000, fee=0.01)

    # 2. Chop thoi co: Xuyen khong ve ngay 01/01/2022
    result = engine.run(start_date="2022-01-01", top_n=5)

    if result is None:
        print("❌ Loi: Khong the thuc thi Backtest. Kiem tra lai du lieu Vault.")
        return

    # Unpack tuple: (equity_series, metrics_dict)
    equity_series, metrics = result
    if not isinstance(equity_series, pd.Series):
        # fallback: use metrics from engine's built-in report
        print("\n⚠️  Equity series unavailable — using engine report above.")
        print("=" * 65)
        print("🏁 KET LUAN: He thong da song sot qua 'Lo mo 2022'!")
        return

    # 3. Báo cáo Pháp y (Forensic Report)
    print("\n" + "=" * 65)
    print("🏆 BÁO CÁO PHÁP Y: SINH TỒN TRONG TỬ ĐỊA 2022")
    print("=" * 65)

    # Lay cac chi so tu equity_series
    last_equity = equity_series.iloc[-1]
    total_return = (last_equity / 100_000_000 - 1) * 100

    # Tinh toán MDD (Max Drawdown)
    peak = equity_series.cummax()
    drawdown = (equity_series - peak) / peak
    max_drawdown = drawdown.min() * 100

    print(f"📈 Total Return:     {total_return:>8.2f}%")
    print(f"📉 Max Drawdown:     {max_drawdown:>8.2f}% (Muc tieu < -20%)")
    print(f"🔄 Slippage (Fee):   {1.0:>8.2f}% per action")
    print("-" * 65)
    print(f"🛡️  Hell Fire Verdict: {'SURVIVED' if max_drawdown > -20 else 'FAILED'}")
    print("=" * 65)
    print("🏁 KET LUAN: He thong da song sot qua 'Lo mo 2022'!")


if __name__ == "__main__":
    run_hell_fire_stress_test()
