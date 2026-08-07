"""
sector_state_engine.py — Phase 4, P1 (Perception Layer)

Evaluates all 19 ICB supersectors on 4 pillars:
  Momentum (0.35): RS, price trend, diffusion index
  Health   (0.25): aggregated company health scores
  Flow     (0.25): volume/value intensity relative to market
  Valuation(0.15): average valuation z-score per sector

Output: per-sector score + rotation phase + rotation chain.

Canonical standard: icb_name2 (19 Vietnamese supersectors).
"""

import json
import logging
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

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
SECTOR_DIR = DATA_DIR / "macro"
SECTOR_DIR.mkdir(parents=True, exist_ok=True)

from src.database.db_core import get_connection

# ── Pillar weights ────────────────────────────────────────────────

PILLAR_WEIGHTS = {
    "momentum": 0.35,
    "health": 0.25,
    "flow": 0.25,
    "valuation": 0.15,
}

# Rotation phase thresholds
ROTATION_PHASES = {
    "EARLY": "Dòng tiền bắt đầu vào ngành, RS cải thiện từ đáy",
    "MID": "Dòng tiền mạnh nhất, momentum và flow đồng thuận",
    "LATE": "Dòng tiền chững lại, valuation bắt đầu đắt",
    "WEAKENING": "RS suy yếu, flow giảm, chuẩn bị đảo chiều",
    "NEUTRAL": "Không có tín hiệu rõ ràng",
}

SECTOR_PHASE_THRESHOLDS = {
    "EARLY": {"momentum_min": 0.40, "flow_min": 0.40, "momentum_flow_min": 0.35},
    "MID": {"momentum_min": 0.60, "flow_min": 0.55, "momentum_flow_health": 0.50},
    "LATE": {"momentum_min": 0.55, "valuation_min": 0.60, "flow_max": 0.70},
    "WEAKENING": {"momentum_max": 0.45, "flow_max": 0.40},
}


@dataclass
class SectorState:
    """Per-sector evaluation output."""

    sector: str
    symbol_count: int
    momentum: float
    health: float
    flow: float
    valuation: float
    score: float
    phase: str
    phase_desc: str
    rs_vs_vnindex: float


@dataclass
class SectorRotationReport:
    """Aggregate sector rotation output."""

    date: str
    sectors: list[dict]
    top_sector: str | None
    top_score: float
    bottom_sector: str | None
    bottom_score: float
    rotation_chain: list[str]
    n_sectors_healthy: int
    n_sectors_weak: int


class SectorStateEngine:
    """
    Sector State Engine — P1 Perception Layer.

    Computes 4-pillar score for each of 19 ICB supersectors.
    Detects rotation phases and builds rotation chain.

    Usage:
        engine = SectorStateEngine()
        report = engine.analyze()
    """

    def __init__(self):
        self._dir = SECTOR_DIR

    # ── Public API ────────────────────────────────────────────────

    def analyze(self) -> SectorRotationReport:
        """Run full sector analysis pipeline."""
        mapping = self._load_icb_mapping()

        sectors = []
        for sector_name, symbols in sorted(mapping.items(), key=lambda x: -len(x[1])):
            if len(symbols) < 3:
                continue
            sector = self._evaluate_sector(sector_name, symbols)
            sectors.append(sector)

        # Sort by overall score descending
        sectors.sort(key=lambda s: s.score, reverse=True)

        top = sectors[0] if sectors else None
        bottom = sectors[-1] if sectors else None

        # Build rotation chain: sectors sorted by score, grouped by phase
        rotation_chain = self._detect_rotation_chain(sectors)

        n_healthy = sum(1 for s in sectors if s.score > 50)
        n_weak = sum(1 for s in sectors if s.score < 30)

        dt_str = datetime.now().strftime("%Y-%m-%d")
        report = SectorRotationReport(
            date=dt_str,
            sectors=[asdict(s) for s in sectors],
            top_sector=top.sector if top else None,
            top_score=round(top.score, 2) if top else 0,
            bottom_sector=bottom.sector if bottom else None,
            bottom_score=round(bottom.score, 2) if bottom else 0,
            rotation_chain=rotation_chain,
            n_sectors_healthy=n_healthy,
            n_sectors_weak=n_weak,
        )

        self._save_report(report)
        return report

    def get_latest(self) -> SectorRotationReport | None:
        """Load most recent report from persistence."""
        return self._load_latest()

    # ── Sector Evaluation ─────────────────────────────────────────

    def _evaluate_sector(self, sector: str, symbols: list[str]) -> SectorState:
        """Compute 4-pillar score for a single sector."""
        with get_connection() as conn:
            # 1. Momentum: avg RS relative to market over 60d
            momentum = self._compute_momentum(conn, symbols)

            # 2. Health: aggregate from financial_facts health_ratios
            health = self._compute_health(conn, symbols)

            # 3. Flow: volume intensity relative to market
            flow = self._compute_flow(conn, symbols)

            # 4. Valuation: avg z-score from financial_facts valuation_scores
            valuation = self._compute_valuation(conn, symbols)

            # 5. RS vs VNINDEX
            rs = self._compute_rs_vs_index(conn, symbols)

        # Weighted score: 0.35*M + 0.25*H + 0.25*F + 0.15*V
        score = (
            PILLAR_WEIGHTS["momentum"] * momentum
            + PILLAR_WEIGHTS["health"] * health
            + PILLAR_WEIGHTS["flow"] * flow
            + PILLAR_WEIGHTS["valuation"] * valuation
        ) * 100.0

        phase, desc = self._classify_phase(momentum, health, flow, valuation)

        return SectorState(
            sector=sector,
            symbol_count=len(symbols),
            momentum=round(momentum, 4),
            health=round(health, 4),
            flow=round(flow, 4),
            valuation=round(valuation, 4),
            score=round(score, 2),
            phase=phase,
            phase_desc=desc,
            rs_vs_vnindex=round(rs, 4),
        )

    # ── Pillar Computations ───────────────────────────────────────

    @staticmethod
    def _compute_momentum(conn, symbols: list[str]) -> float:
        """Average RS momentum across sector symbols.

        RS = (close / SMA20) normalized. Higher = stronger momentum.
        """
        if not symbols:
            return 0.0
        placeholders = ",".join("?" * len(symbols))
        try:
            df = pd.read_sql(
                f"SELECT symbol, date, close FROM daily_ohlcv "
                f"WHERE symbol IN ({placeholders}) "
                f"AND date >= date('now', '-90 days') "
                f"ORDER BY date",
                conn,
                params=symbols,
            )
        except Exception:
            return 0.0
        if df.empty:
            return 0.0

        df["sma20"] = df.groupby("symbol")["close"].transform(lambda x: x.rolling(20, min_periods=5).mean())
        df["rs"] = df["close"] / df["sma20"].replace(0, np.nan) - 1.0

        # Latest RS per symbol
        latest = df.groupby("symbol").last()["rs"].dropna()
        if latest.empty:
            return 0.0
        return float(np.clip(latest.mean(), -0.5, 0.5) / 0.5)  # normalize [-0.5,0.5] → [-1,1]

    @staticmethod
    def _compute_health(conn, symbols: list[str]) -> float:
        """Aggregate health scores from financial_facts health_ratios."""
        if not symbols:
            return 0.0
        placeholders = ",".join("?" * len(symbols))
        try:
            get_connection()  # same screener_cache, health_ratios in financial_facts.db
            # health_ratios is in financial_facts.db, need separate connection
            fin_conn = None
            fin_path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "financial_facts.db"
            if fin_path.exists():
                import sqlite3

                fin_conn = sqlite3.connect(str(fin_path))
                df = pd.read_sql(
                    f"SELECT symbol, score FROM health_ratios "
                    f"WHERE symbol IN ({placeholders}) "
                    f"AND date = (SELECT MAX(date) FROM health_ratios WHERE symbol IN ({placeholders}))",
                    fin_conn,
                    params=symbols + symbols,
                )
                fin_conn.close()
                if not df.empty:
                    return float(np.clip(df["score"].mean(), -1.0, 1.0))
        except Exception as e:
            logger.debug(f"Health fetch failed for sector: {e}")
        return 0.0

    @staticmethod
    def _compute_flow(conn, symbols: list[str]) -> float:
        """Volume intensity: sector avg volume / market avg volume.

        Higher = institutional interest, lower = neglected.
        """
        if not symbols:
            return 0.0
        placeholders = ",".join("?" * len(symbols))
        try:
            # Latest day: sector avg volume
            sector_vol = (
                pd.read_sql(
                    f"SELECT AVG(volume) as avg_vol FROM daily_ohlcv "
                    f"WHERE symbol IN ({placeholders}) "
                    f"AND date = (SELECT MAX(date) FROM daily_ohlcv)",
                    conn,
                    params=symbols,
                ).iloc[0]["avg_vol"]
                or 0
            )

            # Market avg volume (all symbols)
            market_vol = (
                pd.read_sql(
                    "SELECT AVG(volume) as avg_vol FROM daily_ohlcv WHERE date = (SELECT MAX(date) FROM daily_ohlcv)",
                    conn,
                ).iloc[0]["avg_vol"]
                or 1
            )

            ratio = sector_vol / market_vol if market_vol > 0 else 0
            return float(np.clip(np.log1p(ratio) / 5.0, -1.0, 1.0))
        except Exception as e:
            logger.debug(f"Flow fetch failed: {e}")
            return 0.0

    @staticmethod
    def _compute_valuation(conn, symbols: list[str]) -> float:
        """Average valuation z-score from financial_facts valuation_scores."""
        if not symbols:
            return 0.0
        placeholders = ",".join("?" * len(symbols))
        try:
            fin_path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "financial_facts.db"
            if not fin_path.exists():
                return 0.0
            import sqlite3

            fin_conn = sqlite3.connect(str(fin_path))
            df = pd.read_sql(
                f"SELECT symbol, score FROM valuation_scores "
                f"WHERE symbol IN ({placeholders}) "
                f"AND date = (SELECT MAX(date) FROM valuation_scores WHERE symbol IN ({placeholders}))",
                fin_conn,
                params=symbols + symbols,
            )
            fin_conn.close()
            if df.empty:
                return 0.0
            return float(np.clip(-df["score"].mean(), -1.0, 1.0))
            # Negative: z-score < 0 = cheap → positive valuation score
            # Positive: z-score > 0 = expensive → negative valuation score
        except Exception as e:
            logger.debug(f"Valuation fetch failed: {e}")
            return 0.0

    @staticmethod
    def _compute_rs_vs_index(conn, symbols: list[str]) -> float:
        """RS of sector vs VNINDEX over last 20 sessions."""
        if not symbols:
            return 0.0
        placeholders = ",".join("?" * len(symbols))
        try:
            # VNINDEX close
            vni = pd.read_sql(
                "SELECT date, close FROM daily_ohlcv WHERE symbol='VNINDEX' AND date >= date('now', '-40 days') ORDER BY date",
                conn,
            )
            if vni.empty or len(vni) < 20:
                return 0.0
            vni_ret = vni["close"].pct_change().tail(20).mean()

            # Sector avg return
            sec = pd.read_sql(
                f"SELECT date, close FROM daily_ohlcv "
                f"WHERE symbol IN ({placeholders}) "
                f"AND date >= date('now', '-40 days') "
                f"ORDER BY date",
                conn,
                params=symbols,
            )
            if sec.empty:
                return 0.0
            sec_ret = sec.groupby("date")["close"].mean().pct_change().tail(20).mean()
            rs = (sec_ret - vni_ret) if vni_ret is not None else 0
            return float(np.clip(rs * 20, -1.0, 1.0))  # scale
        except Exception:
            return 0.0

    # ── Phase Classification ───────────────────────────────────────

    @staticmethod
    def _classify_phase(momentum: float, health: float, flow: float, valuation: float) -> tuple[str, str]:
        """Classify sector rotation phase."""
        # Normalize to [0,1]
        m = float(np.clip((momentum + 1) / 2, 0, 1))
        float(np.clip((health + 1) / 2, 0, 1))
        f = float(np.clip((flow + 1) / 2, 0, 1))
        v = float(np.clip((valuation + 1) / 2, 0, 1))

        if m > 0.55 and f > 0.50 and v < 0.60:
            return "MID", ROTATION_PHASES["MID"]
        if m > 0.40 and f > 0.40 and m < 0.60:
            return "EARLY", ROTATION_PHASES["EARLY"]
        if m > 0.50 and v > 0.60:
            return "LATE", ROTATION_PHASES["LATE"]
        if m < 0.40 and f < 0.40:
            return "WEAKENING", ROTATION_PHASES["WEAKENING"]
        return "NEUTRAL", ROTATION_PHASES["NEUTRAL"]

    # ── Rotation Chain ─────────────────────────────────────────────

    @staticmethod
    def _detect_rotation_chain(sectors: list[SectorState]) -> list[str]:
        """Detect which sectors money is flowing from → to.

        Sort by score, group by phase.
        Returns ordered list showing the flow direction.
        """
        if not sectors:
            return []

        phase_order = {"EARLY": 0, "MID": 1, "LATE": 2, "WEAKENING": 3, "NEUTRAL": 4}
        sorted_sectors = sorted(sectors, key=lambda s: (phase_order.get(s.phase, 4), -s.score))

        chain = []
        seen = set()
        for s in sorted_sectors:
            if s.phase not in seen:
                chain.append(f"[{s.phase}]")
                seen.add(s.phase)
            chain.append(s.sector)

        # Add phase interpretation
        phases_present = [s.phase for s in sorted_sectors if s.phase != "NEUTRAL"]
        unique_phases = list(dict.fromkeys(phases_present))
        if unique_phases:
            summary = " → ".join(unique_phases)
            chain.append(f"Flow direction: {summary}")

        return chain

    # ── Data Loading ───────────────────────────────────────────────

    @staticmethod
    def _load_icb_mapping() -> dict[str, list[str]]:
        """Load ICB Level 2 mapping: sector_name → [symbols]."""
        try:
            with get_connection() as conn:
                df = pd.read_sql(
                    "SELECT symbol, icb_name2 FROM symbol_industry WHERE icb_name2 IS NOT NULL",
                    conn,
                )
            mapping = defaultdict(list)
            for _, row in df.iterrows():
                mapping[row["icb_name2"]].append(row["symbol"])
            return dict(mapping)
        except Exception as e:
            logger.error(f"Failed to load ICB mapping: {e}")
            return {}

    # ── Persistence ────────────────────────────────────────────────

    def _save_report(self, report: SectorRotationReport) -> None:
        path = self._dir / "sector_rotation_latest.json"
        path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_latest(self) -> SectorRotationReport | None:
        path = self._dir / "sector_rotation_latest.json"
        if not path.exists():
            return None
        try:
            return SectorRotationReport(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to load sector rotation: {e}")
            return None


# ── CLI Helper ────────────────────────────────────────────────────


def print_sector_report(report: SectorRotationReport) -> None:
    """Human-readable sector rotation report."""
    print(f"\n  {'=' * 70}")
    print(f"  SECTOR ROTATION REPORT — {report.date}")
    print(f"  {'=' * 70}")
    print(f"  Top:     {report.top_sector} ({report.top_score:.1f})")
    print(f"  Bottom:  {report.bottom_sector} ({report.bottom_score:.1f})")
    print(f"  Healthy: {report.n_sectors_healthy}  |  Weak: {report.n_sectors_weak}")
    print(f"  {'─' * 70}")
    print("  SECTOR RANKINGS:")
    print(f"  {'Sector':<30} {'Score':>6} {'Phase':>12} {'Mom':>5} {'Flow':>5} {'Val':>5}")
    print(f"  {'─' * 70}")
    for s in report.sectors[:10]:
        print(
            f"  {s['sector']:<30} {s['score']:>6.1f} {s['phase']:>12} "
            f"{s['momentum']:>5.2f} {s['flow']:>5.2f} {s['valuation']:>5.2f}"
        )
    if len(report.sectors) > 10:
        print(f"  {'...':>30} {'...':>6} {'...':>12}")
        for s in report.sectors[-3:]:
            print(
                f"  {s['sector']:<30} {s['score']:>6.1f} {s['phase']:>12} "
                f"{s['momentum']:>5.2f} {s['flow']:>5.2f} {s['valuation']:>5.2f}"
            )
    print(f"  {'─' * 70}")
    if report.rotation_chain:
        print("  ROTATION CHAIN:")
        for item in report.rotation_chain:
            print(f"    {item}")
    print(f"  {'=' * 70}\n")
