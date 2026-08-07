"""Telemetry Recorder — captures DecisionSnapshot from compute_v2 output"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

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
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.database.db_core import get_connection
from src.engine.regime_engine import detect_regime
from src.telemetry.models import DecisionSnapshot
from src.telemetry.storage import initialize_telemetry_database, save_snapshot


def _get_vnindex_level() -> float:
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT close FROM daily_ohlcv WHERE symbol = 'VNINDEX' ORDER BY date DESC LIMIT 1").fetchone()
            if row:
                return float(row[0])
    except Exception as e:
        logger.warning("[TELEMETRY] Cannot fetch VNINDEX level: %s", e)
    return 0.0


def _get_opportunity_symbols() -> list:
    try:
        from src.engine.screener_logic import run_screener

        signals = run_screener()
        if isinstance(signals, list):
            return [s.get("symbol", "") for s in signals[:10] if isinstance(s, dict)]
    except Exception as e:
        logger.warning("[TELEMETRY] Cannot fetch opportunities: %s", e)
    return []


def _get_holdings_health() -> str | None:
    try:
        from src.portfolio.exposure_engine import get_portfolio_heat

        heat = get_portfolio_heat()
        if heat > 70:
            return "STRESS"
        elif heat > 50:
            return "CAUTIOUS"
        return "HEALTHY"
    except Exception as e:
        logger.warning("[TELEMETRY] Cannot fetch holdings health: %s", e)
    return None


def _get_market_regime() -> str:
    try:
        verdict = detect_regime()
        return verdict.get("status", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def _build_dominant_signal(decision: dict) -> str:
    posture = decision.get("action", "HOLD")
    risk = decision.get("risk_state", "NORMAL")
    regime = _get_market_regime()
    conf = decision.get("confidence", 50)
    return f"{posture} | {regime} | Risk:{risk} | Conf:{conf}%"


def record_engine_fault(engine_name: str, error: str, date: str | None = None):
    """Ghi nhận lỗi engine vào telemetry DB để snapshot có thể đọc và báo cáo chính xác."""
    from src.telemetry.storage import get_telemetry_connection

    fault_date = date or datetime.now().strftime("%Y-%m-%d")
    try:
        with get_telemetry_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS engine_faults (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine TEXT NOT NULL,
                    error TEXT NOT NULL,
                    date TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO engine_faults (engine, error, date) VALUES (?, ?, ?)", (engine_name, error[:500], fault_date)
            )
        logger.info("[TELEMETRY] Recorded engine fault: %s | %s", engine_name, error[:80])
    except Exception as e:
        logger.warning("[TELEMETRY] Cannot record engine fault: %s", e)


def record_decision(decision_dict: dict) -> str | None:
    try:
        initialize_telemetry_database()

        snapshot = DecisionSnapshot(
            decision_id=decision_dict.get("decision_id", "unknown"),
            timestamp=datetime.fromisoformat(decision_dict.get("timestamp", datetime.now().isoformat())),
            posture=decision_dict.get("action", "HOLD"),
            risk_level=decision_dict.get("risk_state", "NORMAL"),
            confidence=float(decision_dict.get("confidence", 50)),
            dominant_signal=_build_dominant_signal(decision_dict),
            vnindex_level=_get_vnindex_level(),
            opportunity_symbols=_get_opportunity_symbols(),
            holdings_health=_get_holdings_health(),
            market_regime=_get_market_regime(),
            decision_weights=json.dumps(decision_dict.get("calibrated_weights", {}), ensure_ascii=False),
            engine_scores=json.dumps(decision_dict.get("engine_scores", {}), ensure_ascii=False),
        )

        ok = save_snapshot(snapshot)
        if ok:
            logger.info(
                "[TELEMETRY] Recorded decision %s | %s | conf=%.0f",
                snapshot.decision_id,
                snapshot.posture,
                snapshot.confidence,
            )
            return snapshot.decision_id
    except Exception as e:
        logger.error("[TELEMETRY] record_decision failed: %s", e)
    return None
