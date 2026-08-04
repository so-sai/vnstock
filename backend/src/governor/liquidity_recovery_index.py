"""liquidity_recovery_index.py — Chỉ Số Khôi Phục Thanh Khoản (LRI).

Tích hợp 5 biến số leading liquidity thành 1 chỉ báo liên tục [0, 1]:
  - S_Interbank (30%): Lãi suất liên ngân hàng qua đêm — phong vũ biểu nhạy nhất
  - S_USDVND (25%): Áp lực rút vốn ngoại / can thiệp SBV
  - S_OMO (20%): Hành động bơm hút ròng của SBV
  - S_FII (15%): Dòng vốn khối ngoại — lực cầu ngoại khối
  - S_Breadth (10%): Độ rộng dòng tiền toàn sàn

Khi LRI >= 0.8: Mở tối đa tỷ trọng (100% sức mua VN20)
Khi 0.3 <= LRI < 0.8: Giải ngân co giãn (VN20_Weight × LRI)
Khi LRI < 0.3: Ngắt mạch phòng thủ (100% Cash)

WHY: Thay thế VETO nhị phân bằng Dimmer Scaling — cho phép dòng vốn chảy
vào tài sản có MoS khổng lồ ngay cả khi thanh khoản chung còn thắt chặt.
Triệt tiêu "mâu thuẫn Kinh tế thực vs Thanh khoản Tài chính" — giai đoạn
kinh tế tăng trưởng mạnh nhưng chứng khoán ì ạch vì VETO đập bệt.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _hydrate_path():
    """Path Hydrator v2.1: Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()

import sqlite3

# ── Normalization thresholds ─────────────────────────────────────────
# Each component is normalized to [0, 1] where 1 = maximum liquidity (best).

# Interbank ON: lower = better (excess liquidity)
INTERBANK_LOW = 2.0  # < 2% → score 1.0 (plentiful liquidity)
INTERBANK_HIGH = 6.0  # > 6% → score 0.0 (tight liquidity)

# USD/VND deviation from equilibrium (24,000): smaller = better
USDVND_LOW = 0.0  # at equilibrium → score 1.0
USDVND_HIGH = 1500.0  # > 1500 deviation → score 0.0 (capital flight)

# OMO net injection: positive = better (injecting liquidity)
OMO_HIGH = 50000.0  # > 50k bn VND net inject → score 1.0
OMO_LOW = -50000.0  # < -50k bn VND net drain → score 0.0

# FII net flow: positive = better (foreign buying)
FII_HIGH = 500.0  # > 500 bn VND net buy → score 1.0
FII_LOW = -2000.0  # < -2000 bn VND net sell → score 0.0

# Breadth: higher = better (broader participation)
BREADTH_LOW = 30.0  # < 30% → score 0.0 (narrow)
BREADTH_HIGH = 70.0  # > 70% → score 1.0 (broad)


def _normalize(value: float, low: float, high: float, invert: bool = False) -> float:
    """Normalize value to [0, 1]. Invert=True for inverse indicators (lower=better)."""
    if value is None:
        return 0.5  # neutral when missing
    if invert:
        # For inverse: low value → high score
        score = (high - value) / (high - low) if high != low else 0.5
    else:
        # For direct: high value → high score
        score = (value - low) / (high - low) if high != low else 0.5
    return max(0.0, min(1.0, score))


@dataclass
class LRIResult:
    """Kết quả tính LRI."""

    lri: float  # Composite LRI [0, 1]
    s_interbank: float  # Normalized interbank score
    s_usdvnd: float  # Normalized USD/VND score
    s_omo: float  # Normalized OMO score
    s_fii: float  # Normalized FII flow score
    s_breadth: float  # Normalized breadth score
    regime: str  # 'AGGRESSIVE' | 'PROBE' | 'DEFENSIVE'
    max_allocation_pct: float  # Max allocation percentage (0-100)
    components_raw: dict  # Raw values for audit trail


class LiquidityRecoveryIndex:
    """Compute LRI from macro + market data.

    Weights:
      S_Interbank: 30% — Phong vũ biểu nhạy nhất của thanh khoản hệ thống
      S_USDVND:    25% — Áp lực rút vốn ngoại và can thiệp SBV
      S_OMO:       20% — Hành động trực tiếp của Ngân hàng Nhà nước
      S_FII:       15% — Lực cầu ngoại khối
      S_Breadth:   10% — Sự lan tỏa trên toàn sàn chứng khoán
    """

    WEIGHTS = {
        "interbank": 0.30,
        "usdvnd": 0.25,
        "omo": 0.20,
        "fii": 0.15,
        "breadth": 0.10,
    }

    # Regime thresholds
    AGGRESSIVE_THRESHOLD = 0.8  # LRI >= 0.8 → full allocation
    PROBE_THRESHOLD = 0.3  # LRI >= 0.3 → graduated allocation
    # LRI < 0.3 → defensive (100% cash)

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(PROJECT_ROOT / "backend" / "data" / "screener_cache.db")

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _fetch_latest_macro(self, variable: str) -> Optional[float]:
        """Fetch latest value for a macro variable."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT 1",
                (variable,),
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except Exception:
            return None
        finally:
            conn.close()

    def _fetch_latest_foreign_flow(self) -> Optional[float]:
        """Fetch latest 10-day cumulative foreign net flow (billion VND)."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT SUM(net_value) FROM ("
                "  SELECT date, net_value FROM market_foreign_history "
                "  WHERE symbol = 'TOTAL' ORDER BY date DESC LIMIT 10"
                ")"
            ).fetchone()
            if row and row[0] is not None:
                return float(row[0])
            # Fallback: sum all symbols' net_value for latest date
            row2 = conn.execute(
                "SELECT SUM(net_value) FROM market_foreign_history WHERE date = (SELECT MAX(date) FROM market_foreign_history)"
            ).fetchone()
            return float(row2[0]) if row2 and row2[0] is not None else None
        except Exception:
            return None
        finally:
            conn.close()

    def _fetch_latest_breadth(self) -> Optional[float]:
        """Fetch latest market breadth from regime_history."""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT breadth_pct FROM regime_history ORDER BY date DESC LIMIT 1").fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except Exception:
            return None
        finally:
            conn.close()

    def compute(self) -> LRIResult:
        """Compute LRI from latest available data.

        Returns LRIResult with composite score, component scores, and regime.
        """
        # Fetch raw data
        interbank_on = self._fetch_latest_macro("INTERBANK_ON")
        usd_vnd = self._fetch_latest_macro("USD_VND")
        fii_flow = self._fetch_latest_foreign_flow()
        breadth = self._fetch_latest_breadth()

        # OMO: use INTERBANK_ON as proxy (actual OMO data is sparse — only 1 record)
        # WHY: OMO net injection is inversely correlated with interbank rate.
        # When SBV injects liquidity, interbank drops; when draining, interbank rises.
        omo_proxy = -(interbank_on - 3.0) * 10000 if interbank_on is not None else None

        # USD/VND deviation from equilibrium
        usdvnd_dev = abs(usd_vnd - 24000.0) if usd_vnd is not None else None

        # Normalize each component
        s_interbank = _normalize(interbank_on, INTERBANK_LOW, INTERBANK_HIGH, invert=True)
        s_usdvnd = _normalize(usdvnd_dev, USDVND_LOW, USDVND_HIGH, invert=True)
        s_omo = _normalize(omo_proxy, OMO_LOW, OMO_HIGH)
        s_fii = _normalize(fii_flow, FII_LOW, FII_HIGH)
        s_breadth = _normalize(breadth, BREADTH_LOW, BREADTH_HIGH)

        # Compute weighted LRI
        lri = (
            self.WEIGHTS["interbank"] * s_interbank
            + self.WEIGHTS["usdvnd"] * s_usdvnd
            + self.WEIGHTS["omo"] * s_omo
            + self.WEIGHTS["fii"] * s_fii
            + self.WEIGHTS["breadth"] * s_breadth
        )
        lri = round(max(0.0, min(1.0, lri)), 4)

        # Determine regime
        if lri >= self.AGGRESSIVE_THRESHOLD:
            regime = "AGGRESSIVE"
            max_alloc = 100.0
        elif lri >= self.PROBE_THRESHOLD:
            regime = "PROBE"
            max_alloc = lri * 100.0  # Graduated allocation
        else:
            regime = "DEFENSIVE"
            max_alloc = 0.0

        return LRIResult(
            lri=lri,
            s_interbank=round(s_interbank, 4),
            s_usdvnd=round(s_usdvnd, 4),
            s_omo=round(s_omo, 4),
            s_fii=round(s_fii, 4),
            s_breadth=round(s_breadth, 4),
            regime=regime,
            max_allocation_pct=round(max_alloc, 2),
            components_raw={
                "interbank_on": interbank_on,
                "usd_vnd": usd_vnd,
                "usdvnd_deviation": usdvnd_dev,
                "omo_proxy": omo_proxy,
                "fii_flow_10d": fii_flow,
                "breadth_pct": breadth,
            },
        )


def compute_lri(db_path: Optional[str] = None) -> LRIResult:
    """Convenience function to compute LRI."""
    return LiquidityRecoveryIndex(db_path).compute()


if __name__ == "__main__":
    result = compute_lri()
    print("=" * 60)
    print("  LIQUIDITY RECOVERY INDEX (LRI)")
    print("=" * 60)
    print(f"  LRI Composite:  {result.lri:.4f}")
    print(f"  Regime:         {result.regime}")
    print(f"  Max Allocation: {result.max_allocation_pct:.1f}%")
    print("\n  Components:")
    raw = result.components_raw
    print(f"    S_Interbank:  {result.s_interbank:.4f}  (raw: {raw['interbank_on']})")
    print(f"    S_USDVND:     {result.s_usdvnd:.4f}  (raw: {raw['usd_vnd']}, dev: {raw['usdvnd_deviation']})")
    print(f"    S_OMO:        {result.s_omo:.4f}  (proxy: {raw['omo_proxy']})")
    print(f"    S_FII:        {result.s_fii:.4f}  (raw: {raw['fii_flow_10d']})")
    print(f"    S_Breadth:    {result.s_breadth:.4f}  (raw: {raw['breadth_pct']})")
    print(
        f"\n  Weights: Interbank={LiquidityRecoveryIndex.WEIGHTS['interbank']:.0%} "
        f"USDVND={LiquidityRecoveryIndex.WEIGHTS['usdvnd']:.0%} "
        f"OMO={LiquidityRecoveryIndex.WEIGHTS['omo']:.0%} "
        f"FII={LiquidityRecoveryIndex.WEIGHTS['fii']:.0%} "
        f"Breadth={LiquidityRecoveryIndex.WEIGHTS['breadth']:.0%}"
    )
    print(f"{'=' * 60}")
