"""
Gold Spread Engine v1.2
Tính Domestic Premium = SJC (bán ra) - XAUUSD quy đổi sang lượng
SJC niêm yết theo lượng (37.5g), XAUUSD theo troy ounce (31.1035g)
Công thức chuẩn:
  xau_vnd_per_luong = xau_usd * usd_vnd * (37.5 / 31.1034768)
  premium_vnd = sjc_sell - xau_vnd_per_luong
  premium_pct = premium_vnd / xau_vnd_per_luong * 100

Bổ sung v1.2: get_premium_driver() — phân tích nguyên nhân premium thay đổi
  Decomposition bằng finite difference:
    baseline = giá trị cách đây N phiên
    contribution[v] = premium(all_current) - premium(v=baseline, others=current)
    dominant_driver = variable có |contribution| lớn nhất
"""

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent.parent
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
_LIBS = str(Path(PROJECT_ROOT) / "backend" / "libs")
if _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)

import pandas as pd
from canonical import CanonicalAssetRegistry

from src.database.db_core import get_connection
from src.services.macro.gold_service import get_gold_dashboard
from src.services.macro.gold_world_service import fetch_world_gold_live

_CANON = CanonicalAssetRegistry()

logger = logging.getLogger(__name__)

TROY_OUNCE_GRAMS = 31.1034768
VIETNAM_TAEL_GRAMS = 37.5
OUNCE_TO_TAEL = VIETNAM_TAEL_GRAMS / TROY_OUNCE_GRAMS


def analyze_domestic_premium() -> dict:
    """
    Tính Domestic Premium với quy đổi đơn vị chuẩn:
      - SJC: giá bán ra (VND/lượng)
      - XAUUSD: USD/troy ounce → quy đổi sang VND/lượng

    Returns dict với premium regime và các metric liên quan.
    """
    try:
        # 1. SJC giá bán ra (live)
        dashboard = get_gold_dashboard()
        sjc_price = dashboard.get("sjc_sell", 0)
        if sjc_price == 0:
            return _default_premium()

        # 2. XAUUSD (live → fallback → DB)
        xau = fetch_world_gold_live()
        if xau is None:
            with get_connection() as conn:
                df = pd.read_sql(
                    "SELECT value FROM macro_history WHERE variable = 'GOLD_XAU' ORDER BY date DESC LIMIT 1",
                    conn,
                )
                xau = float(df.iloc[0]["value"]) if not df.empty else None
        if xau is None:
            return _default_premium()

        # 3. USD/VND (DB → canonical fallback)
        usd_vnd = None
        try:
            with get_connection() as conn:
                df = pd.read_sql(
                    "SELECT value FROM macro_history WHERE variable = 'USD_VND' ORDER BY date DESC LIMIT 1",
                    conn,
                )
                if not df.empty:
                    usd_vnd = float(df.iloc[0]["value"])
        except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
            logger.debug("USD_VND không đọc được từ DB — fallback canonical")

        # Validate via canonical registry; use spec mid-range as fallback
        spec = _CANON.get("USD_VND")
        if usd_vnd is not None and spec:
            if usd_vnd < spec.min_value or usd_vnd > spec.max_value:
                logger.warning(f"USD_VND {usd_vnd} outside canonical range [{spec.min_value}, {spec.max_value}]")
                usd_vnd = None
        if usd_vnd is None and spec:
            usd_vnd = (spec.min_value + spec.max_value) / 2.0
            logger.info(f"USD_VND fallback: using canonical mid-range {usd_vnd}")

        # 4. Quy đổi: USD/oz → VND/lượng
        xau_vnd_per_oz = xau * usd_vnd
        xau_vnd_per_luong = xau_vnd_per_oz * OUNCE_TO_TAEL

        premium_vnd = sjc_price - xau_vnd_per_luong
        premium_pct = (premium_vnd / xau_vnd_per_luong) * 100 if xau_vnd_per_luong > 0 else 0.0

        # 5. Classify premium regime
        if premium_pct > 5.0:
            regime = "PREMIUM_SURGE"
            signal = "Cầu trú ẩn nội địa cực mạnh — Méo mó thanh khoản"
        elif premium_pct > 2.0:
            regime = "PREMIUM_ELEVATED"
            signal = "Cầu trú ẩn nội địa tăng — Tâm lý phòng thủ"
        elif premium_pct > -1.0:
            regime = "PREMIUM_NORMAL"
            signal = "Chênh lệch trong biên độ bình thường"
        else:
            regime = "PREMIUM_DISCOUNT"
            signal = "Vàng trong nước rẻ hơn thế giới — Tâm lý ổn định"

        result = {
            "premium_regime": regime,
            "premium_pct": round(premium_pct, 2),
            "premium_vnd": round(premium_vnd, 0),
            "sjc_sell": round(sjc_price, 0),
            "xau_usd_per_oz": round(xau, 2),
            "xau_vnd_per_luong": round(xau_vnd_per_luong, 0),
            "xau_vnd_per_oz": round(xau_vnd_per_oz, 0),
            "usd_vnd": round(usd_vnd, 0),
            "ounce_to_tael_factor": round(OUNCE_TO_TAEL, 4),
            "signal": signal,
            "timestamp": int(datetime.now().timestamp()),
        }
        result["display"] = _vi_display(result)
        return result
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Premium analysis failed: {e}")
        return _default_premium()


REGIME_LABELS = {
    "PREMIUM_SURGE": "Chênh lệch rất cao",
    "PREMIUM_ELEVATED": "Chênh lệch cao",
    "PREMIUM_NORMAL": "Chênh lệch bình thường",
    "PREMIUM_DISCOUNT": "Giá trong nước thấp hơn thế giới",
}

REGIME_SEVERITY = {
    "PREMIUM_SURGE": {"severity": 0.95, "color": "red", "level": 3},
    "PREMIUM_ELEVATED": {"severity": 0.65, "color": "orange", "level": 2},
    "PREMIUM_NORMAL": {"severity": 0.30, "color": "green", "level": 1},
    "PREMIUM_DISCOUNT": {"severity": 0.15, "color": "blue", "level": 0},
}

DRIVER_LABELS = {
    "xau_usd": "Giá vàng thế giới",
    "usd_vnd": "Tỷ giá USD/VND",
    "sjc_sell": "Giá bán vàng SJC",
}

DIRECTION_LABELS = {
    "increase": "tăng",
    "decrease": "giảm",
    "stable": "ổn định",
}


def _vi_display(result: dict) -> dict:
    """Map engine result keys sang tiếng Việt cho UI."""
    regime = result.get("premium_regime", "PREMIUM_NORMAL")
    pct = result.get("premium_pct", 0)
    vnd = result.get("premium_vnd", 0)
    sjc = result.get("sjc_sell", 0)
    xau_vnd = result.get("xau_vnd_per_luong", 0)

    severity = REGIME_SEVERITY.get(regime, {"severity": 0.0, "color": "gray", "level": 0})

    return {
        "chenh_lech_hien_tai": f"{pct:+.2f}%",
        "chenh_lech_trieu_dong": f"{vnd / 1e6:+.1f} trieu/luong",
        "gia_vang_trong_nuoc": f"{sjc / 1e6:,.1f} trieu/luong",
        "gia_vang_the_gioi_quy_doi": f"{xau_vnd / 1e6:,.1f} trieu/luong",
        "trang_thai": REGIME_LABELS.get(regime, regime),
        "mau": severity["color"],
        "muc_do": severity["level"],
        "severity": severity["severity"],
    }


def _vi_driver_display(driver: dict) -> dict:
    """Map driver result keys sang tiếng Việt."""
    dominant = driver.get("dominant_driver", "unknown")
    label = DRIVER_LABELS.get(dominant, "Không xác định")
    contrib = driver.get("contributions", {}).get(dominant, 0)
    val_dir = driver.get("dominant_value_direction", "không rõ")

    return {
        "nguyen_nhan_chinh": label,
        "muc_do_anh_huong": f"{contrib:.1f}%",
        "huong_bien_dong": val_dir,
        "dien_giai": f"{label} {val_dir} — nguyen nhan chinh lam bien dong chenh lech ({contrib:.0f}% anh huong)",
    }


def _compute_premium(sjc_sell: float, xau_usd: float, usd_vnd: float) -> tuple:
    """Tính premium từ 3 biến đầu vào. Trả về (xau_vnd_per_luong, premium_vnd, premium_pct)."""
    xau_vnd_per_oz = xau_usd * usd_vnd
    xau_vnd_per_luong = xau_vnd_per_oz * OUNCE_TO_TAEL
    premium_vnd = sjc_sell - xau_vnd_per_luong
    premium_pct = (premium_vnd / xau_vnd_per_luong) * 100 if xau_vnd_per_luong > 0 else 0.0
    return xau_vnd_per_luong, premium_vnd, premium_pct


def get_premium_driver(lookback_days: int = 5) -> dict:
    """
    Phân tích nguyên nhân thay đổi premium.
    Decomposition bằng finite difference:
      - Giữ 2 biến ở giá trị hiện tại, thay 1 biến về baseline
      - contribution = chênh lệch premium so với hiện tại
      - dominant_driver = biến có |contribution| lớn nhất

    Returns dict với dominant_driver + contribution percentages.
    """
    try:
        current = analyze_domestic_premium()
        sjc_cur = current.get("sjc_sell", 0)
        xau_cur = current.get("xau_usd_per_oz", 0)
        usd_cur = current.get("usd_vnd", 0)
        p_cur = current.get("premium_pct", 0)

        if sjc_cur == 0 or xau_cur == 0:
            return _default_driver()

        # Baseline từ macro_history
        base_date = None
        try:
            with get_connection() as conn:
                dates = pd.read_sql(
                    "SELECT DISTINCT date FROM macro_history WHERE variable = 'GOLD_XAU' ORDER BY date DESC LIMIT ?",
                    conn,
                    params=(lookback_days + 5,),
                )
                if len(dates) > lookback_days:
                    base_date = dates.iloc[lookback_days]["date"]
        except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
            logger.debug("get_premium_driver: không đọc được baseline date — fallback default")

        if not base_date:
            return _default_driver()

        # Query baseline values
        xau_base = None
        usd_base = None
        try:
            with get_connection() as conn:
                df = pd.read_sql(
                    "SELECT variable, value FROM macro_history WHERE date = ? AND variable IN ('GOLD_XAU', 'USD_VND')",
                    conn,
                    params=(base_date,),
                )
                for _, row in df.iterrows():
                    if row["variable"] == "GOLD_XAU":
                        xau_base = float(row["value"])
                    elif row["variable"] == "USD_VND":
                        usd_base = float(row["value"])
        except sqlite3.Error, TypeError, ValueError, KeyError, IndexError:
            logger.debug("get_premium_driver: không đọc được baseline value — fallback default")

        if xau_base is None or usd_base is None:
            return _default_driver()

        # SJC baseline: approximate by assuming SJC moved with XAU_VND
        _, _, p_base_all = _compute_premium(sjc_cur, xau_base, usd_base)
        _, _, p_xau_only = _compute_premium(sjc_cur, xau_cur, usd_base)
        _, _, p_usd_only = _compute_premium(sjc_cur, xau_base, usd_cur)

        # Contributions: delta from all-baseline
        delta_xau = p_xau_only - p_base_all
        delta_usd = p_usd_only - p_base_all
        delta_sjc = p_cur - p_base_all - delta_xau - delta_usd

        drivers = {
            "xau_usd": delta_xau,
            "usd_vnd": delta_usd,
            "sjc_sell": delta_sjc,
        }

        # Track actual driver value direction (up/down)
        driver_value_dir = {}
        if xau_cur != xau_base:
            driver_value_dir["xau_usd"] = "tăng" if xau_cur > xau_base else "giảm"
        else:
            driver_value_dir["xau_usd"] = "đi ngang"
        if usd_cur != usd_base:
            driver_value_dir["usd_vnd"] = "tăng" if usd_cur > usd_base else "giảm"
        else:
            driver_value_dir["usd_vnd"] = "đi ngang"
        driver_value_dir["sjc_sell"] = "không rõ"  # SJC không có baseline

        total_abs = sum(abs(v) for v in drivers.values())
        if total_abs == 0:
            return _default_driver()

        contributions = {k: round(v / total_abs * 100, 1) for k, v in drivers.items()}
        dominant = max(drivers, key=lambda k: abs(drivers[k]))

        driver_labels = {
            "xau_usd": "Giá vàng thế giới (XAUUSD)",
            "usd_vnd": "Tỷ giá USD/VND",
            "sjc_sell": "Giá vàng SJC trong nước",
        }

        result = {
            "dominant_driver": dominant,
            "dominant_label": driver_labels.get(dominant, dominant),
            "dominant_value_direction": driver_value_dir.get(dominant, "không rõ"),
            "contributions": contributions,
            "direction": "increase" if drivers[dominant] > 0 else "decrease",
            "current_premium_pct": round(p_cur, 2),
            "baseline_date": base_date,
            "baseline_premium_pct": round(p_base_all, 2),
            "abs_change_pct": round(p_cur - p_base_all, 2),
        }
        result["display"] = _vi_driver_display(result)
        return result
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.error(f"Premium driver analysis failed: {e}")
        return _default_driver()


def _default_driver() -> dict:
    result = {
        "dominant_driver": "unknown",
        "dominant_label": "Không đủ dữ liệu",
        "dominant_value_direction": "không rõ",
        "contributions": {"xau_usd": 0, "usd_vnd": 0, "sjc_sell": 0},
        "direction": "stable",
        "current_premium_pct": 0.0,
        "baseline_date": None,
        "baseline_premium_pct": 0.0,
        "abs_change_pct": 0.0,
    }
    result["display"] = _vi_driver_display(result)
    return result


def _default_premium() -> dict:
    result = {
        "premium_regime": "PREMIUM_NORMAL",
        "premium_pct": 0.0,
        "premium_vnd": 0.0,
        "sjc_sell": 0.0,
        "xau_usd_per_oz": 0.0,
        "xau_vnd_per_luong": 0.0,
        "xau_vnd_per_oz": 0.0,
        "usd_vnd": 0.0,
        "ounce_to_tael_factor": round(OUNCE_TO_TAEL, 4),
        "signal": "Không có dữ liệu",
        "timestamp": int(datetime.now().timestamp()),
    }
    result["display"] = _vi_display(result)
    return result
