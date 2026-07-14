import sys
from pathlib import Path


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
    for p in (root_path, root_path / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path

PROJECT_ROOT = _hydrate_path()

import numpy as np
import pandas as pd

from src.database.db_core import get_connection


def _calc_adx(df, period=14):
    df = df.copy()
    plus_dm = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(period).mean()
    return adx


_VI_KEY_MAP = {
    "timestamp": "thời_gian",
    "ngay": "ngày",
    "layer_1_drivers": "lực_đẩy_5_phiên",
    "layer_2_settlement": "vùng_định_cư_dòng_tiền",
    "layer_3_reputation": "thẩm_phán_tín_nhiệm",
    "layer_4_drift": "cảnh_báo_lệch_pha",
    "delta_real_yield": "lợi_suất_thực_mỹ",
    "delta_breakeven": "kỳ_vọng_lạm_phát",
    "delta_dxy": "sức_mạnh_đô_la",
    "delta_gs_ratio": "tỷ_lệ_vàng_bạc",
    "delta_breadth": "độ_rộng_thị_trường_vn",
    "confidence_level": "mức_tin_cậy",
    "vgb10y_quality": "chất_lượng_dữ_liệu_vgb10y",
    "interbank_status": "trạng_thái_lãi_suất_liên_ngân_hàng",
    "has_drift": "có_lệch_pha",
    "drift_reason": "lý_do",
}

_VI_VALUE_MAP = {
    "CASH_SHELTER": "HẦM_TRÚ_ẨN_TIỀN_MẶT",
    "HARD_ASSET_SHELTER": "HẦM_TRÚ_ẨN_TÀI_SẢN_CỨNG",
    "EQUITY_EXPANSION": "BUNG_XÕA_CỔ_PHIẾU",
    "TRANSITION_STATE": "LUÂN_CHUYỂN_NGẦM",
    "HIGH_CONFIDENCE": "CAO",
    "LOW_CONFIDENCE_MACRO_VN": "THẤP_VÌ_THIẾU_DỮ_LIỆU_VĨ_MÔ_TRONG_NƯỚC",
    "REAL": "THẬT",
    "NO_DATA": "KHÔNG_CÓ_DỮ_LIỆU",
    "ESTIMATED": "ƯỚC_TÍNH_NỘI_SUY",
}


class CrossMarketFlowMap:
    def __init__(self, macro_data: dict, regime_snapshot: dict):
        self.macro = macro_data
        self.snapshot = regime_snapshot
        self.flow_map = {}
        self._macro_stale_mask: dict = {}

    def localize(self, data: dict = None) -> dict:
        """Trả về bản thuần Việt của toàn bộ báo cáo."""
        src = data if data is not None else self.flow_map
        result = {}
        for k, v in src.items():
            vk = _VI_KEY_MAP.get(k, k)
            if isinstance(v, dict):
                result[vk] = self.localize(v)
            elif isinstance(v, str) and v in _VI_VALUE_MAP:
                result[vk] = _VI_VALUE_MAP[v]
            else:
                result[vk] = v
        return result

    def _get_macro_history(self, variable: str, offset: int = 0) -> float:
        """Truy vấn macro_history để lấy giá trị T-offset (0 = hiện tại, 5 = T-5).
        Trả về giá trị thô; nếu bản ghi có is_stale = 1, hạ lưu sẽ chiết khấu trọng số.
        """
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT value, COALESCE(is_stale, 0) AS is_stale "
                    "FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT 1 OFFSET ?",
                    (variable, offset)
                ).fetchone()
                if row:
                    self._macro_stale_mask[variable] = bool(row[1])
                    return float(row[0])
                return 0.0
        except Exception:
            return 0.0

    def _get_breadth_history(self, offset: int = 0) -> float:
        """Truy vấn regime_history để lấy breadth_pct tại T-offset."""
        try:
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT breadth_pct FROM regime_history ORDER BY date DESC LIMIT 1 OFFSET ?",
                    (offset,)
                ).fetchone()
                return float(row[0]) if row else 50.0
        except Exception:
            return 50.0

    def _get_gs_ratio(self, offset: int = 0) -> float:
        """Tính Gold/Silver Ratio từ GOLD_XAU / XAGUSD (không có sẵn trong macro_history)."""
        gold = self._get_macro_history('GOLD_XAU', offset)
        silver = self._get_macro_history('XAGUSD', offset)
        return round(gold / silver, 2) if silver else 0.0

    def _calc_adx_t3(self) -> float:
        """Tính ADX T-3 từ daily_ohlcv để tránh migration DB."""
        try:
            with get_connection() as conn:
                df = pd.read_sql(
                    "SELECT high, low, close FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date DESC LIMIT 20",
                    conn
                )
            if len(df) < 17:
                return 0.0
            df = df.iloc[::-1].reset_index(drop=True)
            adx_series = _calc_adx(df)
            if adx_series is None or len(adx_series) < 4:
                return 0.0
            return float(adx_series.iloc[-4])
        except Exception:
            return 0.0

    def _layer_1_driver_registry(self) -> dict:
        ry_t = self.macro.get('us_real_yield', 0) or 0
        be_t = self.macro.get('breakeven_inflation', 0) or 0
        dxy_t = self.macro.get('dxy_index', 0) or 0
        gs_t = self.macro.get('gold_silver_ratio', 0) or 0
        breadth_t = self.snapshot.get('regime', {}).get('do_rong', 50) or 50

        ry_t5 = self._get_macro_history('US_REAL_YIELD', 5)
        be_t5 = self._get_macro_history('BREAKEVEN_INFLATION', 5)
        dxy_t5 = self._get_macro_history('DXY', 5)
        gs_t5 = self._get_gs_ratio(5)
        breadth_t5 = self._get_breadth_history(5)

        return {
            "delta_real_yield": round(float(ry_t) - ry_t5, 4),
            "delta_breakeven": round(float(be_t) - be_t5, 4),
            "delta_dxy": round(float(dxy_t) - dxy_t5, 4),
            "delta_gs_ratio": round(gs_t - gs_t5, 4),
            "delta_breadth": round(float(breadth_t) - breadth_t5, 2),
        }

    def _layer_2_settlement_zone(self, drivers: dict) -> str:
        if drivers['delta_dxy'] > 0 and drivers['delta_real_yield'] > 0:
            return "CASH_SHELTER"
        if drivers['delta_breakeven'] > 0 and drivers['delta_gs_ratio'] > 0:
            return "HARD_ASSET_SHELTER"
        if drivers['delta_gs_ratio'] < 0 and (self.snapshot.get('regime', {}).get('do_rong', 0) or 0) > 50.0:
            return "EQUITY_EXPANSION"
        return "TRANSITION_STATE"

    def _layer_3_reputation_ledger(self) -> dict:
        vgb_q = self.macro.get('vgb10y_data_quality', 'NO_DATA')
        ib_rate = self.macro.get('interbank_rate')

        return {
            "confidence_level": "LOW_CONFIDENCE_MACRO_VN"
            if vgb_q in ('ESTIMATED', 'NO_DATA') or ib_rate is None
            else "HIGH_CONFIDENCE",
            "vgb10y_quality": vgb_q,
            "interbank_status": "REAL" if ib_rate is not None else "NO_DATA",
        }

    def _layer_4_drift_detector(self) -> dict:
        adx_t = self.snapshot.get('regime', {}).get('adx', 0) or 0
        adx_t3 = self._calc_adx_t3()
        current_regime = self.snapshot.get('regime', {}).get('trang_thai', 'RANGING')

        delta_adx = float(adx_t) - adx_t3
        drift_warning = delta_adx > 5.0 and current_regime == 'RANGING'

        return {
            "has_drift": drift_warning,
            "drift_reason": "LATE-CYCLE OBSERVABILITY GAP: ADX spike trong regime RANGING"
            if drift_warning else "",
        }

    def execute_pipeline(self) -> dict:
        drivers = self._layer_1_driver_registry()
        self.flow_map = {
            "timestamp": self.snapshot.get('thoi_gian_tao'),
            "ngay": self.snapshot.get('ngay'),
            "layer_1_drivers": drivers,
            "layer_2_settlement": self._layer_2_settlement_zone(drivers),
            "layer_3_reputation": self._layer_3_reputation_ledger(),
            "layer_4_drift": self._layer_4_drift_detector(),
        }
        return self.flow_map
