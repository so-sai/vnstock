"""
macro_state_classifier.py — Bridge: PTD Pipeline → Governor-Consumable MacroState.

Architecture: Phase 4, P0 (Perception Layer)
  Input:  macro_history (37 variables)
  Process: construct 7D driver vector → PTDEngine.step() → MacroState
  Output: persistent classifiable state with discrete label + continuous drivers.

Usage:
    classifier = MacroStateClassifier()
    state = classifier.classify()             # fetch latest data, run pipeline
    latest = classifier.get_latest_state()    # cached without recompute
"""

import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


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
    if str(root_path / "backend") not in sys.path:
        sys.path.insert(0, str(root_path / "backend"))
    return root_path


PROJECT_ROOT = _hydrate_path()
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
MACRO_STATE_DIR = DATA_DIR / "macro"
MACRO_STATE_DIR.mkdir(parents=True, exist_ok=True)

from src.database.db_core import get_connection

# ── PTD Pipeline imports ──────────────────────────────────────────
from src.services.macro.ptd_engine import MacroState, PTDEngine

# ── Driver mapping configuration ──────────────────────────────────
# 7-dim driver vector: [Liquidity, Inflation, Growth, Energy, Risk, AI_Capex, Trust]

DRIVER_LABELS = [
    "Liquidity",
    "Inflation",
    "Growth",
    "Energy",
    "Risk",
    "AI_Capex",
    "Trust",
]

STATE_LABELS = [
    "LIQUIDITY_EXPANSION",
    "INFLATION_SHOCK",
    "CREDIT_STRESS",
    "RISK_OFF",
    "RECOVERY",
    "STABLE",
    "PRE_CREDIT_EXPANSION",
    "AI_BOOM",
]


@dataclass
class ClassifiedMacroState:
    """Consumer-friendly MacroState output — discrete + continuous.

    Fields:
        date: snapshot date
        macro_state: discrete label (CREDIT_STRESS, AI_BOOM, ...)
        posterior: confidence in the label [0,1]
        entropy: Shannon entropy of narrative/regime distribution (low = clear signal)
        drivers: normalized 7D driver vector [-2, 2]
        raw_drivers: raw macro values {DXY, INTERBANK_ON, USD_VND, BRENT_OIL, ...}
        phase_label: PTD phase (SILENT_DISTRIBUTION_HEAD / RE_ACCUMULATION_BOTTOM)
        phase_confidence: phase confidence
        novelty_flag: True if current obs is novel vs historical templates
        spectral_stress: stress level from eigenvalue decomposition
        position_scalar: blended position sizing penalty
        risk_on_scalar: scalar for high-beta assets
        defensive_scalar: scalar for defensive assets
        raw_narrative_probs: dict of narrative probabilities
        raw_regime_weights: list of regime mixture weights
        raw_n_regime_components: number of active regimes
    """

    date: str
    macro_state: str
    posterior: float
    entropy: float
    drivers: dict[str, float]
    raw_drivers: dict[str, float]
    phase_label: str
    phase_confidence: float
    novelty_flag: bool
    spectral_stress: str
    position_scalar: float
    risk_on_scalar: float
    defensive_scalar: float
    raw_narrative_probs: dict[str, float]
    raw_regime_weights: list[float]
    raw_n_regime_components: int


class MacroStateClassifier:
    """
    MacroStateClassifier — Bridge between macro_history and Governor.

    Architecture:
        1. fetch_driver_vector() — query macro_history → 7D normalized array
        2. classify() — feed vector into PTDEngine pipeline, produce ClassifiedMacroState
        3. get_latest_state() — return cached state without recompute
        4. get_history(days=30) — return recent state history from persistence
    """

    def __init__(
        self,
        persistence_dir: Path = MACRO_STATE_DIR,
        warmup_steps: int = 10,
    ):
        self._dir = persistence_dir
        self._warmup = warmup_steps
        self._engine: Optional[PTDEngine] = None
        self._latest: Optional[ClassifiedMacroState] = None
        self._is_warm = False

    # ── Public API ────────────────────────────────────────────────

    def classify(self, target_date: Optional[str] = None) -> ClassifiedMacroState:
        """Fetch latest macro data, run PTD pipeline, persist state.

        Args:
            target_date: Optional date override (used for backfill/replay).

        Returns:
            ClassifiedMacroState with discrete + continuous fields.
        """
        # Lazy init PTDEngine
        if self._engine is None:
            self._engine = PTDEngine()

        # 1. Fetch driver vector + raw macro values
        obs, raw_drivers = self._fetch_driver_vector(target_date)

        # 2. Warmup if needed (BPIMM needs sequential updates to converge)
        if not self._is_warm:
            self._warmup_bpimm(obs)

        # 3. Run aligner for spectral features
        aligner_features = self._fetch_aligner_features(target_date)

        # 4. PTD pipeline step
        macro_state: MacroState = self._engine.step(obs, aligner_features)

        # 5. Map to discrete state label (now receives raw_drivers for interbank override)
        state_label, posterior = self._classify_state(macro_state, raw_drivers)

        # 6. Compute Shannon entropy from narrative probabilities
        narrative_probs = {k: v for k, v in macro_state.narrative_probs.items() if k != "entropy"}
        entropy_vals = np.array(list(narrative_probs.values()))
        entropy_vals = np.clip(entropy_vals, 1e-10, 1.0)
        shannon_entropy = float(-np.sum(entropy_vals * np.log2(entropy_vals)))
        # Normalize to [0, 1] (max entropy = log2(num_categories))
        max_entropy = np.log2(max(len(entropy_vals), 2))
        entropy_norm = round(shannon_entropy / max_entropy, 4) if max_entropy > 0 else 0.0

        # 7. Build consumer-friendly output
        dt_str = target_date or datetime.now().strftime("%Y-%m-%d")
        result = ClassifiedMacroState(
            date=dt_str,
            macro_state=state_label,
            posterior=round(posterior, 4),
            entropy=entropy_norm,
            drivers={label: round(float(macro_state.driver_vector[i]), 4) for i, label in enumerate(DRIVER_LABELS)},
            raw_drivers=raw_drivers,
            phase_label=macro_state.phase_label,
            phase_confidence=round(macro_state.phase_confidence, 4),
            novelty_flag=macro_state.novelty_flag,
            spectral_stress=macro_state.spectral_stress,
            position_scalar=round(macro_state.position_scalar, 4),
            risk_on_scalar=round(macro_state.risk_on_scalar, 4),
            defensive_scalar=round(macro_state.defensive_scalar, 4),
            raw_narrative_probs={k: round(float(v), 4) for k, v in macro_state.narrative_probs.items()},
            raw_regime_weights=[round(float(w), 4) for w in macro_state.regime_weights],
            raw_n_regime_components=macro_state.n_regime_components,
        )

        # 8. Persist
        self._save_state(result)
        self._latest = result

        return result

    def get_latest_state(self) -> Optional[ClassifiedMacroState]:
        """Return cached state without recomputing."""
        if self._latest is not None:
            return self._latest
        return self._load_latest()

    def get_history(self, days: int = 30) -> list[ClassifiedMacroState]:
        """Return recent state history from persistence."""
        return self._load_history(days)

    def reset(self) -> None:
        """Reset PTD engine (clear filter state)."""
        if self._engine is not None:
            self._engine.reset()
        self._is_warm = False
        self._latest = None

    # ── Driver Construction ───────────────────────────────────────

    def _fetch_driver_vector(self, target_date: Optional[str] = None) -> tuple[np.ndarray, dict]:
        """Query macro_history and map to 7D driver vector.

        Returns:
            (obs_7d, raw_drivers)
            obs_7d: normalized 7D array for BPIMM [-2, 2]
            raw_drivers: dict of original macro values for Governor consumption

        Mapping (macro variable → driver index):
          [0] Liquidity:  INTERBANK_ON (inverse), INTERBANK_1W trend
          [1] Inflation:  BREAKEVEN_INFLATION, GOLD_XAU trend
          [2] Growth:     COPPER_HG, VNINDEX level
          [3] Energy:     BRENT_OIL normalized
          [4] Risk:       VIX, DXY (risk-on/off proxy)
          [5] AI_Capex:   NASDAQ (tech proxy)
          [6] Trust:      USD_VND stability, SOV risk premium
        """
        date_filter = ""
        params = []
        if target_date:
            date_filter = "AND date <= ?"
            params.append(target_date)

        sql = f"""
            SELECT variable, date, value
            FROM (
                SELECT variable, date, value,
                       ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
                FROM macro_history
                WHERE variable IN (
                    'INTERBANK_ON','INTERBANK_1W','INTERBANK_3M',
                    'BREAKEVEN_INFLATION','GOLD_XAU',
                    'COPPER_HG',
                    'BRENT_OIL','WTI_OIL',
                    'VIX','DXY',
                    'NASDAQ',
                    'USD_VND','US10Y','VGB10Y',
                    'US2Y','US5Y','US30Y',
                    'SP500'
                ) {date_filter}
            ) WHERE rn = 1
        """

        try:
            with get_connection() as conn:
                rows = conn.execute(sql, params).fetchall()
        except Exception as e:
            logger.warning(f"Macro query failed: {e} — using neutral vector")
            return np.zeros(7), {}

        macro = {r[0]: r[2] for r in rows}

        # --- Raw drivers for Governor ---
        raw_drivers = {
            "DXY": round(macro.get("DXY", 0), 2),
            "INTERBANK_ON": round(macro.get("INTERBANK_ON", 0), 4),
            "INTERBANK_1W": round(macro.get("INTERBANK_1W", 0), 4),
            "INTERBANK_3M": round(macro.get("INTERBANK_3M", 0), 4),
            "USD_VND": round(macro.get("USD_VND", 0), 2),
            "BRENT_OIL": round(macro.get("BRENT_OIL", 0), 2),
            "WTI_OIL": round(macro.get("WTI_OIL", 0), 2),
            "GOLD_XAU": round(macro.get("GOLD_XAU", 0), 2),
            "US10Y": round(macro.get("US10Y", 0), 4),
            "VGB10Y": round(macro.get("VGB10Y", 0), 2),
            "VIX": round(macro.get("VIX", 0), 2),
            "NASDAQ": round(macro.get("NASDAQ", 0), 2),
            "SP500": round(macro.get("SP500", 0), 2),
            "COPPER_HG": round(macro.get("COPPER_HG", 0), 2),
            "BREAKEVEN_INFLATION": round(macro.get("BREAKEVEN_INFLATION", 0), 4),
            "US2Y": round(macro.get("US2Y", 0), 4),
            "US5Y": round(macro.get("US5Y", 0), 4),
            "US30Y": round(macro.get("US30Y", 0), 4),
        }

        # --- Driver 0: Liquidity ---
        # Bayesian marginalization: chỉ tính từ biến CÓ THẬT trong kho (date<=target).
        # Thiếu INTERBANK trước 2023 → liquidity neutral 0.5 (uninformative),
        # KHÔNG ép hằng số 4.5% (định kiến 2025) làm nhiễu chu kỳ 2021-2022.
        _ib_components = []
        for _k in ("INTERBANK_ON", "INTERBANK_1W"):
            if _k in macro and macro[_k] is not None:
                _ib_components.append(float(macro[_k]))
        if _ib_components:
            ib = sum(_ib_components) / len(_ib_components)
            liquidity = 1.0 - float(np.clip(ib / 10.0, 0.0, 1.0))
        else:
            liquidity = 0.5

        # --- Driver 1: Inflation ---
        # Dùng GOLD (có từ 2021) + BREAKEVEN (chỉ 2026+). Nếu thiếu breakeven,
        # marginalize: chỉ dùng gold component và chuẩn hóa lại (không thêm 0.4 weight chết).
        _inf_components = []
        _inf_weights = []
        if "BREAKEVEN_INFLATION" in macro and macro["BREAKEVEN_INFLATION"] is not None:
            _inf_components.append(float(np.clip(macro["BREAKEVEN_INFLATION"] / 3.0, 0.0, 1.0)))
            _inf_weights.append(0.6)
        if "GOLD_XAU" in macro and macro["GOLD_XAU"] is not None:
            _inf_components.append(float(np.clip(macro["GOLD_XAU"] / 3000.0, 0.0, 1.0)))
            _inf_weights.append(0.4)
        if _inf_components:
            inflation = float(
                np.clip(
                    sum(c * w for c, w in zip(_inf_components, _inf_weights)) / sum(_inf_weights),
                    0.0,
                    1.0,
                )
            )
        else:
            inflation = 0.5

        # --- Driver 2: Growth ---
        if "COPPER_HG" in macro and macro["COPPER_HG"] is not None:
            growth = float(np.clip(macro["COPPER_HG"] / 6.0, 0.0, 1.0))
        else:
            growth = 0.5

        # --- Driver 3: Energy ---
        if "BRENT_OIL" in macro and macro["BRENT_OIL"] is not None:
            energy = float(np.clip(macro["BRENT_OIL"] / 120.0, 0.0, 1.0))
        elif "WTI_OIL" in macro and macro["WTI_OIL"] is not None:
            energy = float(np.clip(macro["WTI_OIL"] / 120.0, 0.0, 1.0))
        else:
            energy = 0.5

        # --- Driver 4: Risk ---
        # VIX chỉ có 2025+. Trước đó marginalize: chỉ dùng DXY (có từ 2021).
        _risk_components = []
        _risk_weights = []
        if "VIX" in macro and macro["VIX"] is not None:
            _risk_components.append(float(np.clip(macro["VIX"] / 40.0, 0.0, 1.0)))
            _risk_weights.append(0.5)
        if "DXY" in macro and macro["DXY"] is not None:
            _risk_components.append(float(np.clip((macro["DXY"] - 95) / 30.0, 0.0, 1.0)))
            _risk_weights.append(0.5)
        if _risk_components:
            risk = float(
                np.clip(
                    sum(c * w for c, w in zip(_risk_components, _risk_weights)) / sum(_risk_weights),
                    0.0,
                    1.0,
                )
            )
        else:
            risk = 0.5

        # --- Driver 5: AI_Capex ---
        # NASDAQ chỉ có 2025+. Thiếu → neutral 0.5 (không ép 18000).
        if "NASDAQ" in macro and macro["NASDAQ"] is not None:
            ai_capex = float(np.clip((macro["NASDAQ"] - 10000) / 20000.0, 0.0, 1.0))
        else:
            ai_capex = 0.5

        # --- Driver 6: Trust ---
        _trust_components = []
        _trust_weights = []
        if "USD_VND" in macro and macro["USD_VND"] is not None:
            _trust_components.append(float(np.clip((macro["USD_VND"] - 24000) / 3000.0, 0.0, 1.0)))
            _trust_weights.append(0.5)
        if "US10Y" in macro and macro["US10Y"] is not None:
            _trust_components.append(float(np.clip(macro["US10Y"] / 6.0, 0.0, 1.0)))
            _trust_weights.append(0.5)
        if _trust_components:
            trust = float(
                np.clip(
                    sum(c * w for c, w in zip(_trust_components, _trust_weights)) / sum(_trust_weights),
                    0.0,
                    1.0,
                )
            )
        else:
            trust = 0.5

        obs = np.array([liquidity, inflation, growth, energy, risk, ai_capex, trust])
        obs_norm = np.clip((obs - 0.5) * 4.0, -2.0, 2.0)

        logger.info(
            f"Driver vector: L={liquidity:.2f} I={inflation:.2f} G={growth:.2f} "
            f"E={energy:.2f} R={risk:.2f} A={ai_capex:.2f} T={trust:.2f} "
            f"| ib={raw_drivers['INTERBANK_ON']:.2f}% dxy={raw_drivers['DXY']:.2f}"
        )

        return obs_norm, raw_drivers

    def _fetch_aligner_features(self, target_date: Optional[str] = None) -> Optional[dict]:
        """Fetch TimeSeriesAligner features for spectral stress computation."""
        try:
            from src.services.macro.time_series_aligner import TimeSeriesAligner

            aligner = TimeSeriesAligner()
            features = aligner.compute_eigenvalues(target_date=target_date)
            return features
        except Exception as e:
            logger.debug(f"Aligner features unavailable: {e}")
            return None

    # ── PTD Warmup ────────────────────────────────────────────────

    def _warmup_bpimm(self, obs: np.ndarray) -> None:
        """Feed sequential warmup steps to stabilize BPIMM filter bank."""
        if self._engine is None:
            return

        n_warm = self._warmup
        for i in range(n_warm):
            noise = np.random.randn(7) * 0.01 * (n_warm - i) / n_warm
            self._engine.step(obs + noise)

        self._is_warm = True
        logger.info(f"BPIMM warmed up with {n_warm} steps")

    # ── State Classification Logic ────────────────────────────────

    @staticmethod
    def _classify_state(macro_state: MacroState, raw_drivers: dict) -> tuple[str, float]:
        """Map PTD pipeline output to a discrete macro state label.

        Priority:
          1. Interbank rate (if > 6% → CREDIT_STRESS / RISK_OFF)
          2. Narrative-based classification (PTD output)
          3. Phase-based (coordinator spectral phase)
          4. Driver vector heuristic (fallback)

        Args:
            macro_state: PTD MacroState dataclass.
            raw_drivers: raw macro values for override detection.

        Returns:
            (state_label, posterior_probability)
        """
        probs = macro_state.narrative_probs
        drivers = macro_state.driver_vector
        phase = macro_state.phase_label

        # ── Priority 1: INTERBANK override ──
        # Interbank > 6% on any tenor = credit stress / risk-off
        interbank = raw_drivers.get("INTERBANK_ON", 0)
        interbank_1w = raw_drivers.get("INTERBANK_1W", interbank)
        interbank_3m = raw_drivers.get("INTERBANK_3M", interbank)
        ib_max = max(interbank, interbank_1w, interbank_3m)

        if ib_max > 6.0:
            stress_posterior = min(0.45 + (ib_max - 6.0) * 0.05, 0.85)
            # VIX > 25 or DXY > 106 confirms risk-off
            vix = raw_drivers.get("VIX", 0)
            dxy = raw_drivers.get("DXY", 100)
            if ib_max > 8.0:
                return "CREDIT_STRESS", round(stress_posterior, 4)
            if vix > 25 or dxy > 106:
                return "RISK_OFF", round(stress_posterior, 4)
            return "CREDIT_STRESS", round(stress_posterior, 4)

        # ── Priority 2: Narrative-based classification ──
        narrative_map = {
            "LIQUIDITY_EXPANSION": "LIQUIDITY_EXPANSION",
            "LIQUIDITY_CRUNCH": "CREDIT_STRESS",
            "INFLATION": "INFLATION_SHOCK",
            "ENERGY_SHOCK": "INFLATION_SHOCK",
            "AI_BOOM": "AI_BOOM",
            "RECOVERY": "RECOVERY",
        }

        narrative_probs = {k: v for k, v in probs.items() if k not in ("entropy",)}
        if narrative_probs:
            best_narrative = max(narrative_probs, key=narrative_probs.get)
            best_prob = narrative_probs[best_narrative]
            if best_narrative in narrative_map and best_prob > 0.20:
                return narrative_map[best_narrative], round(float(best_prob), 4)

        # ── Priority 3: Phase-based fallback ──
        if phase == "SILENT_DISTRIBUTION_HEAD":
            return "LIQUIDITY_EXPANSION", macro_state.phase_confidence
        elif phase == "RE_ACCUMULATION_BOTTOM":
            return "RECOVERY", macro_state.phase_confidence

        # ── Priority 4: Driver vector heuristic ──
        risk = drivers[4] if len(drivers) > 4 else 0
        liquidity = drivers[0] if len(drivers) > 0 else 0

        if risk > 0.5 and liquidity < -0.3:
            return "CREDIT_STRESS", 0.40
        elif risk > 0.3:
            return "RISK_OFF", 0.45
        elif liquidity > 0.5 and risk < -0.3:
            return "LIQUIDITY_EXPANSION", 0.50
        elif liquidity > 0.3 and risk < 0:
            return "RECOVERY", 0.45
        else:
            return "STABLE", 0.40

    # ── Persistence ───────────────────────────────────────────────

    def _save_state(self, state: ClassifiedMacroState) -> None:
        """Append state to history file."""
        path = self._dir / "macro_state_history.json"
        records = []
        if path.exists():
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError, ValueError:
                records = []
        records.append(asdict(state))
        # Keep last 365 entries
        if len(records) > 365:
            records = records[-365:]
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_latest(self) -> Optional[ClassifiedMacroState]:
        """Load most recent state from history file."""
        path = self._dir / "macro_state_history.json"
        if not path.exists():
            return None
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not records:
                return None
            return ClassifiedMacroState(**records[-1])
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to load macro state history: {e}")
            return None

    def _load_history(self, days: int = 30) -> list[ClassifiedMacroState]:
        """Load recent history from persistence."""
        path = self._dir / "macro_state_history.json"
        if not path.exists():
            return []
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            return [ClassifiedMacroState(**r) for r in records[-days:]]
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to load macro state history: {e}")
            return []


# ── Standalone CLI helper ─────────────────────────────────────────


def print_state_report(state: ClassifiedMacroState) -> None:
    """Print a human-readable MacroState report."""
    print(f"\n  {'=' * 60}")
    print(f"  MACRO STATE REPORT — {state.date}")
    print(f"  {'=' * 60}")
    print(f"  State:       {state.macro_state}")
    print(f"  Posterior:   {state.posterior:.2%}")
    print(f"  Entropy:     {state.entropy:.4f} ({'LOW=clear' if state.entropy < 0.4 else 'HIGH=uncertain'})")
    print(f"  Phase:       {state.phase_label} ({state.phase_confidence:.2%})")
    print(f"  Novelty:     {'⚠ DETECTED' if state.novelty_flag else '✅ None'}")
    print(f"  Stress:      {state.spectral_stress}")
    print(f"  {'─' * 60}")
    print("  RAW MACRO DRIVERS:")
    for k, v in state.raw_drivers.items():
        if v != 0:
            print(f"    {k:>20}: {v}")
    print(f"  {'─' * 60}")
    print("  NORMALIZED DRIVERS:")
    for label, val in state.drivers.items():
        bar_count = max(0, min(20, int(abs(val) * 10)))
        bar = "▓" * bar_count + "░" * (20 - bar_count)
        direction = "▲" if val > 0 else "▼" if val < 0 else "─"
        print(f"    {label:>12}: {val:+7.3f} {direction} |{bar}|")
    print(f"  {'─' * 60}")
    print("  POSITION SCALARS:")
    print(f"    General:   {state.position_scalar:.2%}")
    print(f"    Risk-On:   {state.risk_on_scalar:.2%}")
    print(f"    Defensive: {state.defensive_scalar:.2%}")
    print(f"  {'─' * 60}")
    print(f"  Regimes: {state.raw_regime_weights}")
    print(f"  Narratives: {state.raw_narrative_probs}")
    print(f"  {'=' * 60}\n")
