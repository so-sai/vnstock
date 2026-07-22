import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("interbank_zscore")

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
from src.database.db_core import get_connection


def _fetch_interbank_data(variable: str, min_rows: int = 30) -> pd.DataFrame:
    """Fetch interbank time series from macro_history.
    
    Returns DataFrame with ['date', 'value'] sorted ascending.
    If insufficient data, returns empty DataFrame.
    """
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT date, value FROM macro_history WHERE variable = ? ORDER BY date ASC",
            conn,
            params=(variable,),
        )
    if len(df) < min_rows:
        logger.warning(f"{variable}: chỉ có {len(df)} rows, cần >= {min_rows}")
        return pd.DataFrame()
    return df


def compute_dual_ewma_z(
    values: np.ndarray,
    lambda_fast: float = 0.5,
    lambda_slow: float = 0.1,
    seed_window: int = 30,
) -> dict:
    """Dual-Z EWMA with PREDICTIVE Z-Score calculation.
    
    Uses PRIOR state (μ_{t-1}, σ_{t-1}) to compute Z before updating,
    so a spike is measured against what was expected, not diluted by
    the spike itself.
    
    INITIALIZATION STRATEGY:
        - μ₀ = mean of first seed_window values
        - σ₀ = std of first seed_window values (clamped >= 0.01)
        - After ~30 iterations, initial seed contributes <0.001% to fast EWMA
          → zero propagation error by the time we reach the 13% spike
    """
    n = len(values)
    if n < seed_window:
        return {"z_fast": 0.0, "z_slow": 0.0, "mu_fast": float(np.mean(values)), "sigma_fast": 1.0}

    mu_f = np.zeros(n)
    var_f = np.zeros(n)
    mu_s = np.zeros(n)
    var_s = np.zeros(n)

    mu_f[0] = float(np.mean(values[:seed_window]))
    var_f[0] = float(max(np.var(values[:seed_window]), 0.0001))
    mu_s[0] = mu_f[0]
    var_s[0] = var_f[0]

    for i in range(1, n):
        x = float(values[i])

        mu_f[i] = lambda_fast * x + (1.0 - lambda_fast) * mu_f[i - 1]
        var_f[i] = lambda_fast * (x - mu_f[i]) ** 2 + (1.0 - lambda_fast) * var_f[i - 1]

        mu_s[i] = lambda_slow * x + (1.0 - lambda_slow) * mu_s[i - 1]
        var_s[i] = lambda_slow * (x - mu_s[i]) ** 2 + (1.0 - lambda_slow) * var_s[i - 1]

    # Apply sigma floor (30bps = 0.3) to avoid extreme Z from bootstrap's narrow variance
    sigma_floor = 0.3

    # Fast EWMA array for recovery gate (predictive Z at each step)
    z_fast_arr = np.zeros(n)
    for i in range(1, n):
        prior_sigma_f = max(np.sqrt(max(var_f[i - 1], 0.0001)), sigma_floor)
        z_fast_arr[i] = (values[i] - mu_f[i - 1]) / prior_sigma_f

    # Current Z (predictive: against PRIOR state)
    sigma_f = max(np.sqrt(max(var_f[-2], 0.0001)), sigma_floor) if n >= 2 else sigma_floor
    sigma_s = max(np.sqrt(max(var_s[-2], 0.0001)), sigma_floor) if n >= 2 else sigma_floor
    current = float(values[-1])
    prior_mu_f = float(mu_f[-2]) if n >= 2 else mu_f[-1]
    prior_mu_s = float(mu_s[-2]) if n >= 2 else mu_s[-1]
    z_fast = (current - prior_mu_f) / sigma_f
    z_slow = (current - prior_mu_s) / sigma_s

    return {
        "z_fast": round(float(z_fast), 2),
        "z_slow": round(float(z_slow), 2),
        "mu_fast": round(float(mu_f[-1]), 4),
        "sigma_fast": round(float(sigma_f), 4),
        "mu_slow": round(float(mu_s[-1]), 4),
        "sigma_slow": round(float(sigma_s), 4),
        "z_fast_history": [round(float(x), 2) for x in z_fast_arr],
        "current_value": round(current, 2),
        "n": n,
    }


def assess_term_structure() -> dict:
    """Phân tích cấu trúc kỳ hạn (Term Structure) của đường cong lãi suất liên ngân hàng.

    Sử dụng 7 kỳ hạn từ SBV để phát hiện:
    - Đảo ngược đường cong (Inverted Yield Curve) — short-term > long-term
    - Stress thanh khoản qua Term Premium Ratio
    - Số lượng cặp kỳ hạn bị đảo ngược

    Returns:
        dict với:
          - status: 'NORMAL' | 'WARNING' | 'INVERTED_STRESS'
          - inversion_count: số cặp bị đảo ngược (0-6)
          - term_premium_ratio: ON_rate / 9M_rate
          - curve_slope: mô tả hình dạng đường cong
          - details: chi tiết từng kỳ hạn
    """
    with get_connection() as conn:
        df = pd.read_sql(
            """SELECT variable, value FROM macro_history 
               WHERE variable IN ('INTERBANK_ON','INTERBANK_1W','INTERBANK_2W',
                                  'INTERBANK_1M','INTERBANK_3M','INTERBANK_6M','INTERBANK_9M')
               AND date = (SELECT MAX(date) FROM macro_history WHERE variable LIKE 'INTERBANK_%')
               ORDER BY CASE variable
                   WHEN 'INTERBANK_ON' THEN 1
                   WHEN 'INTERBANK_1W' THEN 2
                   WHEN 'INTERBANK_2W' THEN 3
                   WHEN 'INTERBANK_1M' THEN 4
                   WHEN 'INTERBANK_3M' THEN 5
                   WHEN 'INTERBANK_6M' THEN 6
                   WHEN 'INTERBANK_9M' THEN 7
               END""",
            conn,
        )
    if df.empty or len(df) < 3:
        return {"status": "INSUFFICIENT_DATA", "inversion_count": 0}

    rates = dict(zip(df["variable"], df["value"]))
    tenors = ["INTERBANK_ON", "INTERBANK_1W", "INTERBANK_2W",
              "INTERBANK_1M", "INTERBANK_3M", "INTERBANK_6M", "INTERBANK_9M"]
    labels = ["ON", "1W", "2W", "1M", "3M", "6M", "9M"]
    values = [rates.get(t) for t in tenors]

    # Đếm số cặp bị đảo ngược: short > long
    inversion_count = 0
    for i in range(len(values) - 1):
        if values[i] is not None and values[i + 1] is not None and values[i] > values[i + 1]:
            inversion_count += 1

    # Term Premium Ratio: ON / 9M
    on_rate = values[0]
    long_rate = values[-1]
    term_premium = (on_rate / long_rate) if (on_rate and long_rate and long_rate > 0) else 1.0

    # Xác định trạng thái
    if inversion_count >= 5 or term_premium > 1.5:
        status = "INVERTED_STRESS"
    elif inversion_count >= 3 or term_premium > 1.2:
        status = "WARNING"
    else:
        status = "NORMAL"

    # Mô tả hình dạng
    if inversion_count >= 4:
        curve_slope = "ĐẢO NGƯỢC toàn bộ (short > long)"
    elif inversion_count >= 2:
        curve_slope = "ĐẢO NGƯỢC một phần"
    elif term_premium > 1.15:
        curve_slope = "Phẳng + premium short-term"
    else:
        curve_slope = "Dốc chuẩn (long > short)"

    details = {labels[i]: values[i] for i in range(len(labels)) if values[i] is not None}
    return {
        "status": status,
        "inversion_count": inversion_count,
        "term_premium_ratio": round(term_premium, 4),
        "curve_slope": curve_slope,
        "details": details,
    }


def _check_sbv_alert() -> bool:
    """Kiểm tra file alert SBV structure change."""
    try:
        from src.services.macro.interbank_seeder import _is_sbv_alert_active
        return _is_sbv_alert_active()
    except Exception:
        return False


def assess_interbank_risk() -> dict:
    """Full interbank risk assessment: fetch data + compute Dual-Z + Recovery Gate.
    
    Returns:
        dict with:
          - zscore: computed Dual-Z values
          - veto: 'ACTIVE' | 'RELEASED' | 'STRUCTURE_UNKNOWN' | 'INSUFFICIENT_DATA'
          - signal: 'SYSTEMIC_LIQUIDITY_SHOCK', 'WARNING', 'NORMAL', or 'CRITICAL'
          - reason: explanation string
    """
    # ── STRUCTURE_UNKNOWN: SBV sensor blind → force 0.0 immediately ──
    if _check_sbv_alert():
        logger.critical("[RISK] SBV STRUCTURE CHANGED — sensor blind, veto=STRUCTURE_UNKNOWN")
        return {
            "veto": "STRUCTURE_UNKNOWN",
            "signal": "CRITICAL",
            "reason": "SBV HTML structure changed — sensor blind. Position forced to 0.0.",
            "zscore": {"z_fast": 0.0, "z_slow": 0.0},
            "stale_override": True,
            "hours_stale": 0,
            "last_date": None,
            "term_structure": {"status": "STRUCTURE_UNKNOWN"},
            "sbv_alert_active": True,
        }

    df = _fetch_interbank_data("INTERBANK_ON", min_rows=30)
    if df.empty:
        return {
            "veto": "INSUFFICIENT_DATA",
            "signal": "UNKNOWN",
            "reason": "INTERBANK_ON có < 30 rows, không thể tính Z-Score",
        }

    values = df["value"].values.astype(np.float64)
    zs = compute_dual_ewma_z(values)

    on_rate = float(values[-1])
    is_liquidity_crisis = on_rate >= 15.0

    z_fast = zs["z_fast"]
    z_slow = zs["z_slow"]
    z_history = zs.get("z_fast_history", [])

    # TTL: force-release nếu dữ liệu >48h tuổi VÀ Z>10 (stale data từ low-reliability source)
    last_date = str(df["date"].iloc[-1])
    hours_stale = 999
    try:
        from datetime import datetime
        last_dt = datetime.strptime(last_date, "%Y-%m-%d")
        hours_stale = (datetime.now() - last_dt).total_seconds() / 3600
    except Exception:
        pass
    stale_override = hours_stale >= 48 and abs(z_fast) > 10

    # Recovery Gate: count consecutive days (ending today) with |Z_fast| < 1.5
    recovery_days = 0
    if abs(z_fast) < 1.5:
        for z in reversed(z_history):
            if abs(z) < 1.5:
                recovery_days += 1
            else:
                break

    # Decision logic
    if stale_override:
        # Stale data override: veto hết hạn, force release
        status = {
            "veto": "RELEASED",
            "signal": "WARNING",
            "reason": (
                f"Z_fast={z_fast} NHƯNG dữ liệu đã cũ ({hours_stale:.0f}h). "
                f"TTL force-release. Cần data mới để đánh giá lại. "
                f"Data date={last_date}, source=SBV."
            ),
        }
    elif z_fast > 3.0 or z_slow > 2.5:
        # SHOCK: check if recovery gate applies
        if recovery_days >= 2:
            status = {
                "veto": "RELEASED",
                "signal": "WARNING",
                "reason": (
                    f"Z_fast={z_fast} cho thấy shock đã qua. "
                    f"Recovery gate: {recovery_days}/2 ngày Z_fast<1.5. "
                    f"Auto-release kích hoạt. Z_slow={z_slow} vẫn cảnh báo dài hạn."
                ),
            }
        else:
            status = {
                "veto": "ACTIVE",
                "signal": "SYSTEMIC_LIQUIDITY_SHOCK",
                "reason": (
                    f"Z_fast={z_fast} (ngưỡng SHOCK > 3.0). "
                    f"Recovery days: {recovery_days}/2. "
                    f"Interbank rate = {zs['current_value']}%. "
                    f"Absolute VETO đang hoạt động."
                ),
            }
    elif z_fast > 2.0 or z_slow > 1.5:
        status = {
            "veto": "NONE",
            "signal": "WARNING",
            "reason": f"Z_fast={z_fast}, Z_slow={z_slow}. Căng thẳng nhưng chưa tới ngưỡng veto.",
        }
    else:
        status = {
            "veto": "NONE",
            "signal": "NORMAL",
            "reason": f"Z_fast={z_fast}, Z_slow={z_slow}. Trạng thái bình thường.",
        }

    return {
        "zscore": zs,
        "veto": status["veto"],
        "signal": status["signal"],
        "reason": status["reason"],
        "date": str(df["date"].iloc[-1]) if len(df) else None,
        "stale_override": stale_override,
        "hours_stale": round(hours_stale, 1),
        "last_date": last_date if len(df) else None,
        "on_rate": round(on_rate, 2),
        "is_liquidity_crisis": is_liquidity_crisis,
        "recovery_days": recovery_days,
        "term_structure": assess_term_structure(),
    }


if __name__ == "__main__":
    import io
    import sys
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = assess_interbank_risk()
    print("\n=== INTERBANK RISK ASSESSMENT ===")
    for k, v in result.items():
        if k == "zscore":
            print(f"  {k}:")
            for zk, zv in v.items():
                if zk == "z_fast_history":
                    print(f"    {zk}: [{zv[0]}, {zv[-2]}, {zv[-1]}] (last 3)")
                else:
                    print(f"    {zk}: {zv}")
        else:
            print(f"  {k}: {v}")
