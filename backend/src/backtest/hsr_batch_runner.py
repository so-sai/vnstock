from typing import Optional

import numpy as np
import pandas as pd
from backend.src.engine.drift_prevention import assess_drift
from backend.src.engine.driver_normalizer import driver_state_from_engine_outputs
from backend.src.engine.explain_layer import explain_snapshot
from backend.src.engine.explain_validator import validate_explanation
from backend.src.engine.hazard_engine import HazardTransitionEngine
from backend.src.engine.trader_concierge import trading_insight


def _driver_state_from_snapshot(breadth_health: float, flow: dict, structure: dict) -> dict:
    """Build driver_state dict from available batch-runner signals."""
    ds = driver_state_from_engine_outputs(
        breadth_health=breadth_health * 100,
        flow_bias=flow["flow_bias_score"],
        lcr_pct=structure["lcr_pct"],
    )
    return {
        "dominant": ds.dominant,
        "confidence": round(ds.confidence, 4),
        "entropy": round(ds.entropy, 4),
        "sharpness": round(ds.sharpness, 4),
        "distribution": {k: round(v, 4) for k, v in ds.distribution.items()},
    }


class RegimeROM:
    """
    Reduced-Order Model for regime state transitions.
    Stateful: temporal smoothing (EMA), inertia (Markov bias), hysteresis bands.

    Calibrated on Q1 2023 real engine data (59 days, 1.46 entropy, 50% persist):
      smoothing=0.05, trending_bias=0.0, crisis_bias=0.0,
      trending_threshold=0.48, crisis_threshold=0.35

    Real engine vs ROM delta (best effort):
      Entropy:     1.46 vs 1.33  (Δ 8.7%)
      Persist:     50%  vs 79%   (ROM still too stable — fundamental limit of
                                   single-score EMA vs multi-engine voting)
      Trans/100d:  49.2 vs 20.3  (ROM smooths raw composite score)
    """

    def __init__(
        self,
        smoothing: float = 0.05,
        trending_bias: float = 0.0,
        crisis_bias: float = 0.0,
        trending_threshold: float = 0.48,
        crisis_threshold: float = 0.35,
    ):
        self.prev_regime: Optional[str] = None
        self.momentum: float = 0.45
        self.smoothing = smoothing
        self.trending_bias = trending_bias
        self.crisis_bias = crisis_bias
        self.trending_threshold = trending_threshold
        self.crisis_threshold = crisis_threshold

    def evaluate(
        self,
        breadth_score: float,
        flow_score: float,
        recovery_score: float,
        driver_state: Optional[dict] = None,
    ) -> dict:
        raw = max(0.0, min(1.0, 0.5 * breadth_score + 0.35 * flow_score + 0.15 * recovery_score))

        # Adaptive smoothing from driver entropy
        smoothing = self.smoothing
        tt = self.trending_threshold
        ct = self.crisis_threshold
        if driver_state:
            entropy = driver_state.get("entropy", 0.5)
            confidence = driver_state.get("confidence", 0.5)
            # High entropy → more smoothing (wait for clarity)
            smoothing_mod = np.clip(0.5 + entropy / 1.5, 0.3, 2.0)
            smoothing = np.clip(self.smoothing * smoothing_mod, 0.01, 0.5)
            # Low confidence → wider hysteresis bands (avoid false switches)
            band_mod = np.clip(1.0 + (1.0 - confidence) * 0.3, 0.7, 1.3)
            tt = np.clip(self.trending_threshold * band_mod, 0.3, 0.7)
            ct = np.clip(self.crisis_threshold / band_mod, 0.15, 0.5)

        if breadth_score > 0.0:
            self.momentum = smoothing * self.momentum + (1 - smoothing) * raw

        inertia = self.momentum
        if self.prev_regime == "TRENDING" and self.trending_bias:
            inertia = min(1.0, self.momentum + self.trending_bias)
        elif self.prev_regime == "CRISIS" and self.crisis_bias:
            inertia = max(0.0, self.momentum - self.crisis_bias)

        if inertia > tt:
            regime = "TRENDING"
        elif inertia < ct:
            regime = "CRISIS"
        else:
            regime = "RANGING"

        self.prev_regime = regime
        return {
            "market_status": regime,
            "regime_score": round(inertia, 4),
            "momentum": round(self.momentum, 4),
            "adaptive_smoothing": round(smoothing, 4),
        }


class HSRBatchRunner:
    """
    Layer 2 orchestrator.
    Owns data (Feature Lattice). Engines are pure functions — no DB, no OHLCV.
    """

    def __init__(self, lattice_df: pd.DataFrame):
        self._lattice = lattice_df
        self._by_date: Optional[dict[str, pd.DataFrame]] = None

    # ── Lattice access ────────────────────────────────────────────────

    def _index(self):
        if self._by_date is not None:
            return self._by_date
        grouped = self._lattice.groupby(
            self._lattice["date"].apply(lambda x: x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x))
        )
        self._by_date = {k: grp for k, grp in grouped}
        return self._by_date

    def day(self, date_str: str) -> pd.DataFrame:
        return self._index().get(date_str)

    # ── Engine helpers (pure, no DB) ──────────────────────────────────

    def breadth(self, day_df: pd.DataFrame) -> dict:
        total = len(day_df)
        if total == 0:
            return {"health_score_ma20": 0.0, "total_active": 0, "above_ma20": 0}
        ma20_valid = day_df["ma_20"].notna()
        if ma20_valid.sum() == 0:
            return {"health_score_ma20": 0.0, "total_active": total, "above_ma20": 0}
        above = ((day_df["close"] > day_df["ma_20"]) & ma20_valid).sum()
        return {
            "health_score_ma20": round(above / ma20_valid.sum(), 4),
            "total_active": total,
            "above_ma20": int(above),
        }

    def structure(self, day_df: pd.DataFrame) -> dict:
        total = len(day_df)
        if total == 0:
            return {"lcr_pct": 0.0, "bdi_pct": 0.0, "bdi_signal": "CAN_BANG"}

        # BDI: % symbols with positive 5-day return
        ret5 = day_df["return_5d"].dropna()
        bdi_pct = (ret5 > 0).sum() / len(ret5) * 100 if len(ret5) > 0 else 50.0

        # BDI signal
        if bdi_pct > 60:
            bdi_signal = "TICH_CUC"
        elif bdi_pct > 40:
            bdi_signal = "CAN_BANG"
        else:
            bdi_signal = "TIEU_CUC"

        # LCR: top 10 by value concentration
        val = day_df["close"].abs() * day_df["volume"].abs()
        total_value = val.sum()
        top_count = min(10, val.notna().sum())
        if top_count == 0 or total_value == 0:
            return {"lcr_pct": 30.0, "bdi_pct": 50.0, "bdi_signal": "CAN_BANG"}
        top10_val = val.nlargest(top_count).sum()
        lcr_pct = top10_val / total_value * 100

        return {
            "lcr_pct": round(lcr_pct, 2),
            "bdi_pct": round(bdi_pct, 2),
            "bdi_signal": bdi_signal,
        }

    def flow(self, day_df: pd.DataFrame) -> dict:
        vol_ratio = day_df["volume_trend"].dropna()
        if len(vol_ratio) == 0:
            return {"flow_bias_score": 0.4, "flow_label": "UNKNOWN"}

        # Continuous score: % of symbols with expanding volume (ratio > 1.0)
        above_1 = (vol_ratio > 1.0).sum()
        score = above_1 / len(vol_ratio)

        if score > 0.5:
            label = "MO_RONG"
        elif score > 0.3:
            label = "DUY_TRI"
        else:
            label = "THU_HEP"

        return {"flow_bias_score": round(score, 4), "flow_label": label}

    def recovery(self, day_df: pd.DataFrame) -> dict:
        vn = day_df[day_df["symbol"] == "VNINDEX"]
        if len(vn) == 0:
            return {"status": "STANDBY", "reason": "NO_VNINDEX"}
        row = vn.iloc[0]
        ret_5d = row.get("return_5d")
        if pd.isna(ret_5d):
            return {"status": "STANDBY", "reason": "NO_RETURN_DATA"}
        is_recovering = ret_5d > 0.02
        return {
            "status": "RECOVERY" if is_recovering else "STANDBY",
            "return_5d": round(ret_5d, 4),
        }

    # ── Presentation layer (DBE/DPL/TTL compatible) ───────────────────

    def _trade_state(self, regime_status: str, regime_score: float, breadth_health: float) -> object:
        class _TS:
            def __init__(self):
                self.level = "NEUTRAL"
                self.score = 0.5
                self.status = "WAIT"

        ts = _TS()
        if regime_status == "TRENDING" and regime_score > 0.6:
            ts.level = "AGGRESSIVE"
            ts.score = 0.8
            ts.status = "HOLD"
        elif regime_status == "CRISIS" and regime_score < 0.4:
            ts.level = "DEFENSIVE"
            ts.score = 0.2
            ts.status = "CASH"
        return ts

    def _ssi(self, breadth_health: float, lcr_pct: float, flow_score: float) -> object:
        class _SSI:
            def __init__(self):
                self.score = 0.5

        ssi = _SSI()
        ssi.score = round(0.4 * breadth_health + 0.3 * (flow_score) + 0.3 * min(lcr_pct / 50, 1.0), 4)
        return ssi

    def _dcl(self, ssi_score: float, trade_state_level: str) -> object:
        class _DCL:
            def __init__(self):
                self.verdict = "HOLD"
                self.score = 0.5
                self.compensations_triggered = []

        dcl = _DCL()
        if ssi_score > 0.6 and trade_state_level in ("AGGRESSIVE", "NEUTRAL"):
            dcl.verdict = "BUY"
            dcl.score = 0.7
        elif ssi_score < 0.3 or trade_state_level == "DEFENSIVE":
            dcl.verdict = "SELL"
            dcl.score = 0.3
        return dcl

    def _dbe(self, regime_score: float, breadth_health: float, flow_score: float) -> dict:
        strength = round(0.5 * regime_score + 0.3 * breadth_health + 0.2 * flow_score, 4)
        confidence = round(abs(strength - 0.5) * 2, 4)
        return {
            "bias": "LONG" if strength > 0.5 else "SHORT",
            "strength": strength,
            "confidence": confidence,
        }

    def _dpl(self, dbe_history: list, dbe: dict) -> dict:
        dbe_history.append(dbe)
        if len(dbe_history) < 5:
            return {"persistent": False, "stability": 0.5}
        recent = dbe_history[-5:]
        strengths = [r["strength"] for r in recent]
        stability = 1.0 - np.std(strengths)
        bias_count = sum(1 for r in recent if r["bias"] == dbe["bias"])
        persistent = bias_count >= 4
        return {
            "persistent": persistent,
            "stability": round(stability, 4),
            "direction": dbe["bias"],
        }

    def _ttl(self, ttl_state: dict, dpl: dict, dbe: dict, regime_status: str) -> dict:
        result = {"triggered": False, "type": "NONE", "prev_regime": ttl_state.get("prev_regime")}
        prev = ttl_state.get("prev_regime")
        if prev and prev != regime_status:
            result["triggered"] = True
            result["type"] = "REGIME_SHIFT"
            result["from"] = prev
            result["to"] = regime_status
        ttl_state["prev_regime"] = regime_status
        return result

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self, dates: list[str]) -> list[dict]:
        snapshots = []
        dbe_history = []
        cache = self._index()
        rom = RegimeROM()

        prev_ets: Optional[float] = None
        for d in dates:
            day_df = cache.get(d)
            if day_df is None or len(day_df) == 0:
                continue

            # Layer 1: engine metrics from lattice
            b = self.breadth(day_df)
            s = self.structure(day_df)
            f = self.flow(day_df)
            r = self.recovery(day_df)

            # Layer 1b: driver state (control signal, computed before regime)
            breadth_health = b["health_score_ma20"]
            ds_dict = _driver_state_from_snapshot(breadth_health, f, s)

            # Layer 2: regime (stateful ROM with inertia, modulated by driver_state)
            regime = rom.evaluate(
                breadth_score=breadth_health,
                flow_score=f["flow_bias_score"],
                recovery_score=1.0 if r.get("status") == "RECOVERY" else 0.0,
                driver_state=ds_dict,
            )

            # Layer 3: presentation
            regime_status = regime["market_status"]
            regime_score = regime["regime_score"]

            ts = self._trade_state(regime_status, regime_score, breadth_health)
            ssi = self._ssi(breadth_health, s["lcr_pct"], f["flow_bias_score"])
            dcl = self._dcl(ssi.score, ts.level)
            dbe = self._dbe(regime_score, breadth_health, f["flow_bias_score"])
            self._dpl(dbe_history, dbe)

            snap = {
                "date": d,
                "regime_status": regime_status,
                "trade_state_level": ts.level,
                "breadth_health": round(breadth_health, 4),
                "lcr_pct": s["lcr_pct"],
                "bdi_signal": s["bdi_signal"],
                "flow_bias_score": round(f["flow_bias_score"], 4),
                "flow_label": f["flow_label"],
                "ssi_score": round(ssi.score, 4),
                "dcl_verdict": dcl.verdict,
                "dcl_score": round(dcl.score, 4),
                "compensations_triggered": len(dcl.compensations_triggered),
                "driver_state": _driver_state_from_snapshot(breadth_health, f, s),
                "narrative_vi": explain_snapshot(
                    ds_dict,
                    regime_status,
                    regime_score,
                )["báo_cáo_hệ_thống"],
                "hsr_quality": {
                    "capital_displacement": "MISSING_HISTORICAL_SOURCE",
                    "risk_governor": "MISSING_HISTORICAL_SOURCE",
                    "sentinel_verdict": "MISSING_HISTORICAL_SOURCE",
                    "breadth_source": "feature_lattice",
                },
            }
            snap["explain_validation"] = validate_explanation(snap)
            snap["drift_assessment"] = assess_drift(snap, prev_ets=prev_ets)
            snap["trading_insight"] = trading_insight(snap)
            prev_ets = snap["explain_validation"]["ets_score"]
            snapshots.append(snap)

        return snapshots

    # ── Hazard mode (replaces RegimeROM with HazardTransitionEngine) ─

    def run_hazard(
        self,
        dates: list[str],
        weights: Optional[dict[str, float]] = None,
        seed: Optional[int] = None,
    ) -> list[dict]:
        """
        Run batch with HazardTransitionEngine instead of deterministic ROM.

        Returns same snapshot format as run() for SRV compatibility,
        plus hazard diagnostics in each snapshot.

        Args:
            dates: Sorted list of date strings (YYYY-MM-DD).
            weights: Optional custom hazard weights (see hazard_engine.py).
            seed: Random seed for reproducibility.
        """
        snapshots = []
        dbe_history = []
        cache = self._index()
        engine = HazardTransitionEngine(weights=weights, seed=seed)
        prev_ets: Optional[float] = None

        for d in dates:
            day_df = cache.get(d)
            if day_df is None or len(day_df) == 0:
                continue

            b = self.breadth(day_df)
            s = self.structure(day_df)
            f = self.flow(day_df)

            breadth_health = b["health_score_ma20"]
            ds_dict = _driver_state_from_snapshot(breadth_health, f, s)

            regime = engine.evaluate(day_df, driver_state=ds_dict)

            regime_status = regime["market_status"]
            regime_score = regime["regime_score"]

            ts = self._trade_state(regime_status, regime_score, breadth_health)
            ssi = self._ssi(breadth_health, s["lcr_pct"], f["flow_bias_score"])
            dcl = self._dcl(ssi.score, ts.level)
            dbe = self._dbe(regime_score, breadth_health, f["flow_bias_score"])
            self._dpl(dbe_history, dbe)

            snap = {
                "date": d,
                "regime_status": regime_status,
                "trade_state_level": ts.level,
                "breadth_health": round(breadth_health, 4),
                "lcr_pct": s["lcr_pct"],
                "bdi_signal": s["bdi_signal"],
                "flow_bias_score": round(f["flow_bias_score"], 4),
                "flow_label": f["flow_label"],
                "ssi_score": round(ssi.score, 4),
                "dcl_verdict": dcl.verdict,
                "dcl_score": round(dcl.score, 4),
                "compensations_triggered": len(dcl.compensations_triggered),
                "hazard_rate": regime.get("hazard_rate", 0.0),
                "survival_prob": regime.get("survival_prob", 0.0),
                "regime_age": regime.get("regime_age", 0),
                "driver_state": _driver_state_from_snapshot(breadth_health, f, s),
                "narrative_vi": explain_snapshot(
                    ds_dict,
                    regime_status,
                    regime_score,
                    hazard_rate=regime.get("hazard_rate", 0.0),
                )["báo_cáo_hệ_thống"],
                "hsr_quality": {
                    "capital_displacement": "MISSING_HISTORICAL_SOURCE",
                    "risk_governor": "MISSING_HISTORICAL_SOURCE",
                    "sentinel_verdict": "MISSING_HISTORICAL_SOURCE",
                    "breadth_source": "feature_lattice",
                    "regime_source": "hazard_transition_engine",
                },
            }
            snap["explain_validation"] = validate_explanation(snap)
            snap["drift_assessment"] = assess_drift(snap, prev_ets=prev_ets)
            snap["trading_insight"] = trading_insight(snap)
            prev_ets = snap["explain_validation"]["ets_score"]
            snapshots.append(snap)

        return snapshots


class HazardBatchRunner(HSRBatchRunner):
    """
    Drop-in replacement for HSRBatchRunner that uses HazardTransitionEngine
    by default.

    Usage:
        runner = HazardBatchRunner(lattice_df, seed=42)
        snapshots = runner.run(dates)  # uses hazard engine internally
    """

    def __init__(self, lattice_df: pd.DataFrame, seed: Optional[int] = None):
        super().__init__(lattice_df)
        self._hazard_seed = seed
        self._hazard_weights: Optional[dict[str, float]] = None

    def set_weights(self, weights: dict[str, float]):
        self._hazard_weights = weights

    def run(self, dates: list[str]) -> list[dict]:
        return self.run_hazard(
            dates,
            weights=self._hazard_weights,
            seed=self._hazard_seed,
        )
