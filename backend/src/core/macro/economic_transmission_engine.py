"""
economic_transmission_engine.py — Phase 4, P1 (Perception Layer)

Simulates the transmission chain: Liquidity → Credit → Confidence → Sector Rotation.

Captures the "Tiền nhiều hơn niềm tin" insight:
  High Liquidity + Low Credit = money is waiting, not flowing.
  High Liquidity + High Credit + High Confidence = money is deployed.

Usage:
    engine = EconomicTransmissionEngine()
    state = engine.compute()  # uses latest macro_history
    # {
    #   "liquidity": 92,
    #   "credit": 38,
    #   "confidence": 41,
    #   "transmission_phase": "LIQUIDITY_TRAP",
    #   "sector_probs": { ... }
    # }
"""

import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

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
TRANSMISSION_DIR = DATA_DIR / "macro"
TRANSMISSION_DIR.mkdir(parents=True, exist_ok=True)

from src.database.db_core import get_connection

# ── Transmission phase labels ─────────────────────────────────────

TRANSMISSION_PHASES = {
    "LIQUIDITY_TRAP": "Tiền nhiều + Tín dụng không chạy = capital waiting",
    "CREDIT_CRUNCH": "Tiền ít + Tín dụng tắc = recession risk",
    "HEALTHY_TRANSMISSION": "Tiền + Tín dụng + Niềm tin đồng bộ = bull market",
    "OVERHEATING": "Tín dụng nóng + Niềm tin quá cao = đỉnh chu kỳ",
    "RISK_OFF_FLIGHT": "Tiền giảm + Niềm tin thấp = flight to safety",
    "FRAGILE_STABILITY": "Tiền vừa + Tín dụng đang hồi = phục hồi non",
}


@dataclass
class TransmissionState:
    """Output of the Economic Transmission Engine."""

    date: str
    liquidity: float
    credit: float
    confidence: float
    transmission_phase: str
    transmission_phase_desc: str
    interbank_on: float
    interbank_3m: float
    interbank_spread: float
    dxy: float
    vix: float
    usd_vnd: float
    gold_xau: float
    transmission_score: float  # composite: (liquidity * credit * confidence)^(1/3)


class EconomicTransmissionEngine:
    """
    Economic Transmission Engine — P1, Perception Layer.

    Computes 3 latent states from macro data:
      1. Liquidity  [0-100]: availability of VND funding
      2. Credit     [0-100]: willingness to lend/borrow
      3. Confidence [0-100]: trust in the system (FX, risk appetite)

    Transmission chain detection:
      - If Liquidity > 60 and Credit < 40: LIQUIDITY_TRAP (tiền nhiều, không chạy)
      - If Liquidity < 30 and Credit < 30: CREDIT_CRUNCH
      - If all > 60: HEALTHY_TRANSMISSION
      - If Credit > 80 and Confidence > 80: OVERHEATING
      - If Liquidity < 40 and Confidence < 40: RISK_OFF_FLIGHT
      - Else: FRAGILE_STABILITY
    """

    def __init__(self):
        self._dir = TRANSMISSION_DIR

    # ── Public API ────────────────────────────────────────────────

    def compute(self) -> TransmissionState:
        """Fetch latest macro data and compute transmission state."""
        raw = self._fetch_macro()

        # 1. Liquidity: inverse of interbank ON rate
        # Lower ON rate = more liquidity
        ib_on = raw.get("INTERBANK_ON", 4.5)
        liquidity = float(np.clip(100 - (ib_on / 10.0) * 100, 0, 100))

        # 2. Credit: inverse of interbank 3M spread over ON
        # Narrow spread = credit flowing, wide spread = credit stress
        ib_3m = raw.get("INTERBANK_3M", ib_on + 1.0)
        ib_1w = raw.get("INTERBANK_1W", ib_on)
        spread_3m_on = max(ib_3m - ib_on, 0)
        spread_1w_on = max(ib_1w - ib_on, 0)
        avg_spread = (spread_3m_on + spread_1w_on) / 2.0

        # Credit score: low spread = high credit availability
        credit_raw = 100 - (avg_spread / 5.0) * 100
        credit = float(np.clip(credit_raw, 0, 100))

        # 3. Confidence: composite of DXY (inverse), VIX (inverse), USD_VND stability
        dxy = raw.get("DXY", 104)
        vix = raw.get("VIX", 18)
        usd_vnd = raw.get("USD_VND", 25400)
        gold = raw.get("GOLD_XAU", 2000)

        # DXY: lower = higher confidence for EM
        dxy_score = float(np.clip(100 - (max(dxy - 95, 0) / 25.0) * 100, 0, 100))
        # VIX: lower = higher confidence
        vix_score = float(np.clip(100 - (vix / 40.0) * 100, 0, 100))
        # USD_VND: lower = more stable
        vnd_score = float(np.clip(100 - ((usd_vnd - 24000) / 4000.0) * 100, 0, 100))
        # Gold: very high gold = risk-off = low confidence
        gold_score = float(np.clip(100 - (max(gold - 2000, 0) / 3000.0) * 100, 0, 100))

        confidence = float(
            np.clip(
                dxy_score * 0.30 + vix_score * 0.30 + vnd_score * 0.25 + gold_score * 0.15,
                0,
                100,
            )
        )

        # 4. Transmission phase
        phase, desc = self._classify_phase(liquidity, credit, confidence)

        # 5. Composite transmission score (geometric mean)
        eps = 1e-6
        transmission_score = round(float(np.cbrt(max(liquidity, eps) * max(credit, eps) * max(confidence, eps))), 2)

        dt_str = datetime.now().strftime("%Y-%m-%d")

        state = TransmissionState(
            date=dt_str,
            liquidity=round(liquidity, 1),
            credit=round(credit, 1),
            confidence=round(confidence, 1),
            transmission_phase=phase,
            transmission_phase_desc=desc,
            interbank_on=round(ib_on, 4),
            interbank_3m=round(ib_3m, 4),
            interbank_spread=round(ib_3m - ib_on, 4),
            dxy=round(dxy, 2),
            vix=round(vix, 2),
            usd_vnd=round(usd_vnd, 2),
            gold_xau=round(gold, 2),
            transmission_score=transmission_score,
        )

        self._save_state(state)
        return state

    def get_latest(self) -> TransmissionState | None:
        """Load most recent state from persistence."""
        return self._load_latest()

    # ── Classification ─────────────────────────────────────────────

    @staticmethod
    def _classify_phase(liq: float, c: float, conf: float) -> tuple[str, str]:
        """Classify the transmission phase from 3 latent scores."""
        if liq > 60 and c < 40:
            return "LIQUIDITY_TRAP", TRANSMISSION_PHASES["LIQUIDITY_TRAP"]
        if liq < 30 and c < 30 and conf < 40:
            return "CREDIT_CRUNCH", TRANSMISSION_PHASES["CREDIT_CRUNCH"]
        if liq > 60 and c > 60 and conf > 60:
            return "HEALTHY_TRANSMISSION", TRANSMISSION_PHASES["HEALTHY_TRANSMISSION"]
        if c > 80 and conf > 80:
            return "OVERHEATING", TRANSMISSION_PHASES["OVERHEATING"]
        if liq < 40 and conf < 40:
            return "RISK_OFF_FLIGHT", TRANSMISSION_PHASES["RISK_OFF_FLIGHT"]
        return "FRAGILE_STABILITY", TRANSMISSION_PHASES["FRAGILE_STABILITY"]

    # ── Data Fetching ──────────────────────────────────────────────

    @staticmethod
    def _fetch_macro() -> dict:
        """Fetch latest macro values for transmission computation."""
        sql = """
            SELECT variable, value FROM (
                SELECT variable, date, value,
                       ROW_NUMBER() OVER (PARTITION BY variable ORDER BY rowid DESC) as rn
                FROM macro_history
                WHERE variable IN (
                    'INTERBANK_ON','INTERBANK_1W','INTERBANK_3M',
                    'DXY','VIX','USD_VND','GOLD_XAU'
                )
            ) WHERE rn = 1
        """
        try:
            with get_connection() as conn:
                rows = conn.execute(sql).fetchall()
            return {r[0]: r[1] for r in rows}
        except Exception as e:
            logger.warning(f"Macro fetch failed: {e}")
            return {}

    # ── Persistence ────────────────────────────────────────────────

    def _save_state(self, state: TransmissionState) -> None:
        path = self._dir / "transmission_history.json"
        records = []
        if path.exists():
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError, ValueError:
                records = []
        records.append(asdict(state))
        if len(records) > 365:
            records = records[-365:]
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_latest(self) -> TransmissionState | None:
        path = self._dir / "transmission_history.json"
        if not path.exists():
            return None
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            return TransmissionState(**records[-1]) if records else None
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to load transmission history: {e}")
            return None


# ── CLI Helper ────────────────────────────────────────────────────


def print_transmission_report(state: TransmissionState) -> None:
    """Human-readable transmission report."""
    print(f"\n  {'=' * 60}")
    print(f"  ECONOMIC TRANSMISSION REPORT — {state.date}")
    print(f"  {'=' * 60}")
    print(f"  Transmission Phase: {state.transmission_phase}")
    print(f"  Description:        {state.transmission_phase_desc}")
    print(f"  Composite Score:    {state.transmission_score:.1f}/100")
    print(f"  {'─' * 60}")
    print("  LATENT STATES:")
    for label, val, color in [
        ("Liquidity", state.liquidity, "🟢" if state.liquidity > 60 else "🟡" if state.liquidity > 35 else "🔴"),
        ("Credit   ", state.credit, "🟢" if state.credit > 60 else "🟡" if state.credit > 35 else "🔴"),
        ("Confidence", state.confidence, "🟢" if state.confidence > 60 else "🟡" if state.confidence > 35 else "🔴"),
    ]:
        bar = "▓" * int(val // 5) + "░" * (20 - int(val // 5))
        print(f"    {label}: {val:5.1f} {color} |{bar}|")
    print(f"  {'─' * 60}")
    print("  RAW INPUTS:")
    print(f"    INTERBANK_ON:  {state.interbank_on:.2f}%   INTERBANK_3M: {state.interbank_3m:.2f}%")
    print(f"    Spread 3M-ON:  {state.interbank_spread:.2f}%")
    print(f"    DXY:           {state.dxy:.2f}      VIX: {state.vix:.2f}")
    print(f"    USD_VND:       {state.usd_vnd:.0f}    GOLD_XAU: {state.gold_xau:.0f}")
    print(f"  {'=' * 60}\n")
