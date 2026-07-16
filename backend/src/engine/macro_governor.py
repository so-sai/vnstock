"""macro_governor.py — Cấp Quyết định Vĩ mô (Macro Governor Gatekeeper)

Kiến trúc Two-Tier:
  Tier 1 (Macro Governor): DXY, USDVND, OMO, Foreign flows, Gold/Yield
  Tier 2 (Micro Executor): PerSymbolAbsorption (chỉ active khi Tier 1 an toàn)

Tích hợp FX Risk Premium (FXRP):
  adjusted_macro_risk = macro_risk x (1.0 + FXRP_clipped)

Usage:
  from src.engine.macro_governor import MacroGovernor
  state = MacroGovernor().assess()
"""
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


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
    return root_path


PROJECT_ROOT = _hydrate_path()
import src.config

from src.database.db_core import get_connection

DATA_DIR = src.config.DATA_DIR
logger = logging.getLogger("PTCK_SYSTEM")

# --- Constants ---------------------------------------------------------------
MACRO_LOOKBACK = 120  # Ngày cho phân tích macro
FX_LOOKBACK = 30     # Ngày cho FX risk premium

# Trọng số cảm biến
W_DXY = 0.30
W_USDVND = 0.25
W_FOREIGN = 0.20
W_OMO = 0.15
W_GOLD = 0.10

# Ngưỡng rủi ro
DXY_STRESS = 106.0
DXY_CRISIS = 108.0
USDVND_STRESS = 25500.0
USDVND_CRISIS = 25800.0
INTERBANK_ON_STRESS = 8.0
INTERBANK_ON_CRISIS = 12.0
CONFIDENCE_MIN = 50.0  # % — ngưỡng tối thiểu cho phép micro executor


class MacroGovernor:
    """Macro Governor Gatekeeper — Two-Tier Architecture (Tier 1).

    Giám sát:
      1. DXY (sức mạnh USD)
      2. USDVND (áp lực tỷ giá)
      3. Foreign Flow (dòng vốn ngoại)
      4. OMO Stress (Interbank ON + VGB10Y)
      5. Gold/Yield Divergence (capital flight proxy)

    Đầu ra:
      - state: LIQUIDATION_CASCADE | TURBULENT | RANGING | ACCUMULATION
      - confidence: 0-100% (an toàn của macro)
      - hdr_override: 1.0 nếu rủi ro, None nếu để micro tự quyết
    """

    def __init__(self):
        self.data: Dict[str, pd.DataFrame] = {}
        self.scores: Dict[str, float] = {}
        self.macro_risk: float = 0.0
        self.fxrp: float = 0.0
        self.state: str = "UNKNOWN"
        self.confidence: float = 0.0
        self.hdr_override: Optional[float] = None

    # --- Data Fetching -------------------------------------------------------

    def _fetch_macro_series(self, variable: str, days: int = MACRO_LOOKBACK) -> pd.DataFrame:
        """Tải chuỗi dữ liệu vĩ mô từ macro_history."""
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with get_connection() as conn:
            df = pd.read_sql(
                """SELECT date, value FROM macro_history
                   WHERE variable = ? AND date >= ?
                   ORDER BY date""",
                conn, params=[variable, start]
            )
        if not df.empty:
            # De-duplicate: giữ unique(date) gần nhất
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df.drop_duplicates(subset="date", keep="last").sort_values("date")
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna()
        return df

    def _fetch_foreign_flow_accum(self, days: int = 10) -> float:
        """Tổng net foreign flow (billion VND) trong N ngày gần nhất."""
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with get_connection() as conn:
            row = conn.execute(
                """SELECT SUM(net_value) FROM market_foreign_history
                   WHERE date >= ? AND net_value IS NOT NULL""",
                (start,)
            ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    # --- Sensor Scoring ------------------------------------------------------

    def _score_dxy(self) -> Tuple[float, Dict]:
        """Điểm rủi ro DXY [0, 1]."""
        df = self._fetch_macro_series("DXY")
        if df.empty or len(df) < 5:
            return 0.3, {"status": "INSUFFICIENT_DATA", "latest": None}

        latest = float(df["value"].iloc[-1])
        if latest >= DXY_CRISIS:
            score = 1.0
        elif latest >= DXY_STRESS:
            score = 0.5 + 0.5 * (latest - DXY_STRESS) / (DXY_CRISIS - DXY_STRESS)
        else:
            score = 0.0

        # Momentum: DXY đang tăng nhanh?
        if len(df) >= 10:
            momentum = (df["value"].iloc[-1] - df["value"].iloc[-10]) / df["value"].iloc[-10]
            if momentum > 0.02:
                score = min(score + 0.15, 1.0)

        return min(score, 1.0), {"latest": round(latest, 2), "momentum": round(momentum, 4) if 'momentum' in dir() else 0.0}

    def _score_usdvnd(self) -> Tuple[float, Dict]:
        """Điểm rủi ro USDVND [0, 1]."""
        df = self._fetch_macro_series("USD_VND")
        if df.empty or len(df) < 5:
            return 0.3, {"status": "INSUFFICIENT_DATA", "latest": None}

        latest = float(df["value"].iloc[-1])
        if latest >= USDVND_CRISIS:
            score = 1.0
        elif latest >= USDVND_STRESS:
            score = 0.5 + 0.5 * (latest - USDVND_STRESS) / (USDVND_CRISIS - USDVND_STRESS)
        else:
            score = 0.0

        if len(df) >= 10:
            momentum = (df["value"].iloc[-1] - df["value"].iloc[-10]) / df["value"].iloc[-10]
            if momentum > 0.01:
                score = min(score + 0.20, 1.0)

        return min(score, 1.0), {"latest": round(latest, 2), "momentum": round(momentum, 4) if 'momentum' in dir() else 0.0}

    def _score_foreign_flow(self) -> Tuple[float, Dict]:
        """Điểm rủi ro dòng vốn ngoại [0, 1]."""
        cum_10d = self._fetch_foreign_flow_accum(days=10)
        cum_3d = self._fetch_foreign_flow_accum(days=3)

        if cum_10d > -500:
            score = 0.0
        elif cum_10d > -1000:
            score = 0.5
        elif cum_10d > -2000:
            score = 0.75
        else:
            score = 1.0

        # Acceleration: 3d sell intensifying
        if cum_3d < cum_10d * 0.4:
            score = min(score + 0.15, 1.0)

        return min(score, 1.0), {"cum_10d_bn": round(cum_10d, 1), "cum_3d_bn": round(cum_3d, 1)}

    def _score_omo(self) -> Tuple[float, Dict]:
        """Điểm rủi ro OMO [0, 1]."""
        df_on = self._fetch_macro_series("INTERBANK_ON")
        df_yield = self._fetch_macro_series("VGB10Y")
        score = 0.0
        details = {}

        if not df_on.empty:
            latest = float(df_on["value"].iloc[-1])
            if latest >= INTERBANK_ON_CRISIS:
                s_on = 1.0
            elif latest >= INTERBANK_ON_STRESS:
                s_on = 0.5 + 0.5 * (latest - INTERBANK_ON_STRESS) / (INTERBANK_ON_CRISIS - INTERBANK_ON_STRESS)
            else:
                s_on = 0.0
            if len(df_on) >= 5:
                trend = (df_on["value"].iloc[-1] - df_on["value"].iloc[-5]) / max(df_on["value"].iloc[-5], 1e-6)
                if trend > 0.05:
                    s_on = min(s_on + 0.10, 1.0)
            score += 0.6 * s_on
            details["interbank_on"] = round(latest, 2)

        if not df_yield.empty:
            latest_y = float(df_yield["value"].iloc[-1])
            if latest_y > 5.5:
                s_y = 1.0
            elif latest_y > 4.5:
                s_y = (latest_y - 4.5) / 1.0
            else:
                s_y = 0.0
            score += 0.4 * s_y
            details["vgb10y"] = round(latest_y, 2)

        return min(score, 1.0), details

    def _score_gold_yield(self) -> Tuple[float, Dict]:
        """Điểm rủi ro Gold/Yield divergence [0, 1] — capital flight proxy."""
        df_gold = self._fetch_macro_series("GOLD_XAU")
        df_yield = self._fetch_macro_series("US_REAL_YIELD")
        score = 0.0
        details = {}

        if not df_gold.empty and len(df_gold) >= 10:
            gold_pct = (df_gold["value"].iloc[-1] - df_gold["value"].iloc[-10]) / max(df_gold["value"].iloc[-10], 1e-6)
            details["gold_10d_chg_pct"] = round(gold_pct * 100, 2)
            if gold_pct > 0.05:
                score += 0.5

        if not df_yield.empty and len(df_yield) >= 10:
            yield_chg = (df_yield["value"].iloc[-1] - df_yield["value"].iloc[-10])
            details["real_yield_10d_chg"] = round(yield_chg, 4)
            if yield_chg < 0:
                score += 0.5  # Yield declining = risk

        return min(score, 1.0), details

    # --- FX Risk Premium -----------------------------------------------------

    def _compute_fxrp(self) -> Tuple[float, Dict]:
        """FX Risk Premium — Phần bù rủi ro tỷ giá.

        FXRP = (USDVND_deviation) x (DXY_momentum / DXY_vol)
        Làm phóng đại macro_risk khi DXY + USDVND đồng thời căng thẳng.
        """
        df_usdvnd = self._fetch_macro_series("USD_VND", days=FX_LOOKBACK * 3)
        df_dxy = self._fetch_macro_series("DXY", days=FX_LOOKBACK)
        details = {}

        if df_usdvnd.empty or len(df_usdvnd) < 10:
            return 0.0, {"status": "INSUFFICIENT_DATA"}

        # USDVND deviation from 90-day mean (PPP proxy)
        ppp_90d = float(df_usdvnd["value"].mean())
        usdvnd_latest = float(df_usdvnd["value"].iloc[-1])
        deviation = (usdvnd_latest - ppp_90d) / max(ppp_90d, 1e-6)

        if df_dxy.empty or len(df_dxy) < 5:
            dxy_component = 0.0
        else:
            # DXY momentum / volatility
            returns = df_dxy["value"].pct_change().dropna().values[-FX_LOOKBACK:]
            dxy_mom = float(np.mean(returns)) if len(returns) > 0 else 0.0
            dxy_vol = float(np.std(returns)) if len(returns) > 0 else 0.01
            dxy_component = abs(dxy_mom) / max(dxy_vol, 0.001)

        fxrp_raw = max(deviation, 0) * dxy_component
        fxrp = float(np.clip(fxrp_raw, 0.0, 0.50))  # Clamp 0-50%

        details.update({
            "usdvnd_ppp_90d": round(ppp_90d, 0),
            "usdvnd_deviation_pct": round(deviation * 100, 2),
            "dxy_component": round(dxy_component, 4),
            "fxrp_raw": round(fxrp_raw, 4),
        })
        return fxrp, details

    # --- Main Assessment -----------------------------------------------------

    def assess(self) -> Dict:
        """Đánh giá Macro Governor toàn diện."""
        dxy_score, dxy_d = self._score_dxy()
        usdvnd_score, usdvnd_d = self._score_usdvnd()
        foreign_score, foreign_d = self._score_foreign_flow()
        omo_score, omo_d = self._score_omo()
        gold_score, gold_d = self._score_gold_yield()
        fxrp, fxrp_d = self._compute_fxrp()

        # Macro risk composite
        macro_risk = (
            W_DXY * dxy_score +
            W_USDVND * usdvnd_score +
            W_FOREIGN * foreign_score +
            W_OMO * omo_score +
            W_GOLD * gold_score
        )
        macro_risk = np.clip(macro_risk, 0.0, 1.0)

        # FX Risk Premium amplification
        adj_macro_risk = macro_risk * (1.0 + fxrp)
        adj_macro_risk = np.clip(adj_macro_risk, 0.0, 1.0)

        # State classification
        if adj_macro_risk > 0.70:
            state = "LIQUIDATION_CASCADE"
        elif adj_macro_risk > 0.50:
            state = "TURBULENT"
        elif adj_macro_risk > 0.30:
            state = "RANGING"
        else:
            state = "ACCUMULATION"

        # Governor confidence (0-100%)
        confidence = max(100.0 - adj_macro_risk * 100.0, 0.0)

        # Global HDR override
        if confidence < CONFIDENCE_MIN:
            hdr_override = 1.0  # Cash-only
        else:
            hdr_override = None  # Let micro executor decide

        self.macro_risk = float(adj_macro_risk)
        self.fxrp = float(fxrp)
        self.state = state
        self.confidence = float(confidence)
        self.hdr_override = hdr_override

        # SEL — Structural Evolution Layer (Tier 1.5)
        try:
            from src.engine.structure_evolution import StructureEvolutionLayer
            self.sel_result = StructureEvolutionLayer().assess()
            sel_hdr = self.sel_result.get("hdr_limit")
            sel_state = self.sel_result.get("state")
            if sel_state == "SURVIVAL_MODE":
                # Survival Mode overrides EVERYTHING
                self.state = "SEL_SURVIVAL"
                self.hdr_override = 1.0
                self.confidence = 0.0
                logger.warning(
                    f"[MACRO_GOV] SEL Survival Mode active — "
                    f"W1={self.sel_result['w1']:.4f} > θ_novelty={self.sel_result['theta_novelty']:.4f}. "
                    f"Global HDR locked at 1.0."
                )
            elif sel_hdr is not None and sel_hdr > 0:
                # Structural shift detected — SEL imposes additional HDR constraint
                current_hdr = 1.0 if self.hdr_override else 0.0
                if sel_hdr > current_hdr:
                    self.hdr_override = sel_hdr
                    self.state = f"SEL_SHIFT_{self.state}"
                    logger.info(
                        f"[MACRO_GOV] SEL structural shift — W1={self.sel_result['w1']:.4f}. "
                        f"HDR override={sel_hdr:.2f}"
                    )
        except Exception as e:
            logger.warning(f"[MACRO_GOV] SEL assessment failed: {e}")
            self.sel_result = {"status": "FAILED"}

        result = {
            "status": "OK",
            "timestamp": datetime.now().isoformat(),
            "state": state,
            "macro_risk": round(adj_macro_risk, 4),
            "confidence": round(confidence, 2),
            "hdr_override": hdr_override,
            "fx_risk_premium": round(fxrp, 4),
            "sensors": {
                "dxy": {"score": round(dxy_score, 4), "details": dxy_d},
                "usdvnd": {"score": round(usdvnd_score, 4), "details": usdvnd_d},
                "foreign_flow": {"score": round(foreign_score, 4), "details": foreign_d},
                "omo": {"score": round(omo_score, 4), "details": omo_d},
                "gold_yield": {"score": round(gold_score, 4), "details": gold_d},
            },
            "fx_risk_premium_details": fxrp_d,
            "weights": {
                "dxy": W_DXY, "usdvnd": W_USDVND,
                "foreign_flow": W_FOREIGN, "omo": W_OMO, "gold_yield": W_GOLD,
            },
            "structure_evolution": {
                "w1": self.sel_result.get("w1", 0) if hasattr(self, 'sel_result') else 0,
                "sel_state": self.sel_result.get("state", "UNKNOWN") if hasattr(self, 'sel_result') else "UNKNOWN",
                "best_match": self.sel_result.get("best_match_regime", "N/A") if hasattr(self, 'sel_result') else "N/A",
                "theta_novelty": self.sel_result.get("theta_novelty", 0) if hasattr(self, 'sel_result') else 0,
                "survival_active": self.sel_result.get("survival", {}).get("active", False) if hasattr(self, 'sel_result') else False,
            },
        }
        from src.utils.localization import log_structured
        log_structured(logger, "MACRO_GOV", state, {
            "macro_risk": round(adj_macro_risk, 4),
            "confidence": round(confidence, 2),
            "fxrp": round(fxrp, 4),
            "hdr_override": hdr_override,
            "sensors": {k: round(v, 4) for k, v in [
                ("dxy", dxy_score), ("usdvnd", usdvnd_score),
                ("foreign", foreign_score), ("omo", omo_score),
                ("gold", gold_score)]},
        })
        return result

    @staticmethod
    def assess_global() -> Dict:
        """Static wrapper để gọi trực tiếp."""
        return MacroGovernor().assess()

    @staticmethod
    def print_report(result: Dict, lang: str = "vi"):
        """In báo cáo Macro Governor CLI."""
        from src.utils.localization import translate, localize_state

        t = lambda key: translate(key, lang)
        hdr_str = f"{t('locked')} (HDR={result['hdr_override']:.2f})" if result['hdr_override'] else t('open')
        state_vi = localize_state(result['state'], lang)

        print(f"\n{'=' * 65}")
        print(f"  MACRO GOVERNOR GATEKEEPER — {t('tier1_gov')}")
        print(f"{'=' * 65}")
        print(f"  {t('state'):20s}: {state_vi:<25s}")
        print(f"  {t('macro_risk'):20s}: {result['macro_risk']:.2%}")
        print(f"  {t('governor_conf'):20s}: {result['confidence']:.1f}%")
        print(f"  {t('hdr_global'):20s}: {hdr_str}")
        print(f"  {t('fx_risk_premium'):20s}: {result['fx_risk_premium']:.4f}")
        print(f"\n  -- Sensors --")
        for name, s in result["sensors"].items():
            print(f"  {name:15s}: risk={s['score']:.4f}  {s['details']}")
        print(f"\n  -- FXRP Details --")
        for k, v in result.get("fx_risk_premium_details", {}).items():
            print(f"  {k:30s}: {v}")
        print(f"\n  -- Weights --")
        for k, v in result["weights"].items():
            print(f"  {k:15s}: {v}")
        print(f"{'=' * 65}")
        print()
