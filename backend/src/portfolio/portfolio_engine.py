"""
Portfolio State Machine v1.0
Điều phối vòng đời vị thế — Position Lifecycle Engine.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path


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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path


PROJECT_ROOT = _hydrate_path()

from src.database.portfolio_db import get_portfolio_connection
from src.portfolio import decision_fusion, exposure_engine, memory_engine, position_sizer, risk_budget

logger = logging.getLogger(__name__)

VALID_TRANSITIONS = {
    "WATCHLIST": ["ARMED", "CLOSED"],
    "ARMED": ["ENTERED", "WATCHLIST", "CLOSED"],
    "ENTERED": ["SCALE_IN", "REDUCED", "EXIT_PENDING", "CLOSED"],
    "SCALE_IN": ["REDUCED", "EXIT_PENDING", "CLOSED"],
    "REDUCED": ["EXIT_PENDING", "CLOSED"],
    "EXIT_PENDING": ["CLOSED"],
}


def _validate_transition(current_status: str, next_status: str) -> bool:
    allowed = VALID_TRANSITIONS.get(current_status, [])
    if next_status not in allowed:
        logger.error(f"Transition invalid: {current_status} -> {next_status}")
        return False
    return True


def _get_current_status(position_id: int) -> str:
    with get_portfolio_connection() as conn:
        row = conn.execute("SELECT status FROM position_lifecycle WHERE id=?", (position_id,)).fetchone()
    return row[0] if row else None


def transit_status(position_id: int, next_status: str, update_data: dict | None = None) -> dict:
    current = _get_current_status(position_id)
    if not current:
        return {"ok": False, "error": f"Position {position_id} not found"}
    if not _validate_transition(current, next_status):
        return {"ok": False, "error": f"Invalid transition: {current} -> {next_status}"}

    now = datetime.now().isoformat()
    sets = ["status=?", "updated_at=?"]
    params = [next_status, now]

    if update_data:
        for key in (
            "avg_cost",
            "current_size",
            "highest_price_held",
            "stop_loss_price",
            "take_profit_price",
            "thesis_notes",
            "conviction_score",
            "initial_risk_pct",
            "exit_reason",
            "context_source",
            "realized_pnl_pct",
        ):
            if key in update_data:
                sets.append(f"{key}=?")
                params.append(update_data[key])

    if next_status == "CLOSED":
        sets.append("closed_at=?")
        params.append(now)

    params.append(position_id)
    sql = f"UPDATE position_lifecycle SET {', '.join(sets)} WHERE id=?"

    with get_portfolio_connection() as conn:
        conn.execute(sql, params)

    return {
        "ok": True,
        "position_id": position_id,
        "from_status": current,
        "to_status": next_status,
    }


def create_position(
    symbol: str,
    regime_at_entry: str,
    entry_price: float,
    stop_loss_price: float,
    thesis_source: str,
    thesis_notes: str | None = None,
    take_profit_price: float | None = None,
    conviction_score: float = 0.0,
    initial_risk_pct: float = 0.0,
    context_source: str = "SECONDARY_STABLE",
) -> dict:
    with get_portfolio_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO position_lifecycle
                (symbol, status, regime_at_entry, entry_date, entry_price,
                 avg_cost, current_size, stop_loss_price, take_profit_price,
                 thesis_source, thesis_notes, conviction_score, initial_risk_pct, context_source)
            VALUES (?, 'ARMED', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                regime_at_entry,
                datetime.now().strftime("%Y-%m-%d"),
                entry_price,
                entry_price,
                stop_loss_price,
                take_profit_price,
                thesis_source,
                thesis_notes,
                conviction_score,
                initial_risk_pct,
                context_source,
            ),
        )
        position_id = cursor.lastrowid
    return {"ok": True, "position_id": position_id, "symbol": symbol, "status": "ARMED"}


def arm_and_size(
    symbol: str,
    regime_at_entry: str,
    entry_price: float,
    stop_loss_price: float,
    thesis_source: str,
    total_nav: float,
    cash_balance: float,
    signal_quality: float = 0.5,
    breadth_alignment: float = 0.5,
    liquidity_rank: float = 0.5,
    regime_confidence: float = 0.5,
    liquidity_20d: float = 1e9,
    thesis_notes: str | None = None,
    take_profit_price: float | None = None,
    active_sector_exposure: float = 0.0,
    model_a_verdict: str | None = None,
    model_b_verdict: str | None = None,
) -> dict:
    # Layer 3: Decision Fusion — arbitrate signal conflict first
    if model_a_verdict and model_b_verdict:
        fusion = decision_fusion.arbitrate(model_a_verdict, model_b_verdict, regime_at_entry)
        if fusion["action"] in ("STAND_DOWN", "FORCE_CASH", "MUTED"):
            return {"ok": False, "reason": f"FUSION: {fusion['reason']}", "conviction_score": 0.0}
        cm = fusion["confidence_multiplier"]
        regime_confidence = min(regime_confidence * cm, 1.0)

    # Exposure Engine: conviction dampener + regime memory bias + global throttle
    dampener_result = exposure_engine.calculate_total_dampener(thesis_source, regime_at_entry)
    current_heat = exposure_engine.get_portfolio_heat(nav=total_nav)
    throttle = exposure_engine.evaluate_global_risk_throttle(current_heat)

    if throttle["throttle_activated"]:
        return {"ok": False, "reason": f"THROTTLE: {throttle['risk_state']}", "conviction_score": 0.0}

    regime_confidence = regime_confidence * dampener_result["net_dampener"] * throttle["multiplier"]

    dv = position_sizer.calculate_conviction_score(signal_quality, breadth_alignment, liquidity_rank, regime_confidence)
    if dv < 0.4:
        return {"ok": False, "reason": "CONVICTION_TOO_LOW", "conviction_score": dv}

    sizer_result = position_sizer.compute_position_size(
        total_nav,
        cash_balance,
        regime_at_entry,
        entry_price,
        stop_loss_price,
        symbol=symbol,
        conviction_score=dv,
        current_sector_exposure=active_sector_exposure,
    )

    if sizer_result["status"] != "APPROVED":
        return {"ok": False, "reason": sizer_result["reason"], "conviction_score": dv}

    exposure_pct = sizer_result["nav_exposure_pct"]
    (total_nav * sizer_result["initial_risk_pct"] / 100) / total_nav * 100

    ok_gate, msg_gate = risk_budget.evaluate_gatekeeper(regime_at_entry, 0.0, 0.0, exposure_pct)
    if not ok_gate:
        return {"ok": False, "reason": f"GATEKEEPER: {msg_gate}", "conviction_score": dv}

    create_result = create_position(
        symbol, regime_at_entry, entry_price, stop_loss_price, thesis_source, thesis_notes, take_profit_price
    )
    if not create_result.get("ok"):
        return create_result

    pid = create_result["position_id"]
    transit_result = transit_status(
        pid,
        "ENTERED",
        {
            "current_size": sizer_result["shares"],
            "avg_cost": entry_price,
            "conviction_score": dv,
            "initial_risk_pct": sizer_result["initial_risk_pct"],
            "context_source": f"ARM_AND_SIZE_{thesis_source}",
        },
    )
    if not transit_result.get("ok"):
        return transit_result

    return {
        "ok": True,
        "position_id": pid,
        "symbol": symbol,
        "status": "ENTERED",
        "shares": sizer_result["shares"],
        "value_vnd": sizer_result["value_vnd"],
        "nav_exposure_pct": sizer_result["nav_exposure_pct"],
        "initial_risk_pct": sizer_result["initial_risk_pct"],
        "conviction_score": dv,
        "risk_unit_r": sizer_result["risk_unit_r"],
    }


def close_position(
    position_id: int,
    exit_price: float,
    exit_reason: str = "MANUAL",
    max_favorable_excursion: float | None = None,
    max_adverse_excursion: float | None = None,
) -> dict:
    pos = get_position(position_id)
    if not pos.get("ok", True) and "error" in pos:
        return {"ok": False, "error": f"Position {position_id} not found"}
    if pos.get("status") == "CLOSED":
        return {"ok": False, "error": "Already closed"}

    entry_price = pos.get("avg_cost") or pos.get("entry_price", 0)
    pos.get("current_size", 0)
    pnl_pct = round(((exit_price - entry_price) / entry_price) * 100, 2) if entry_price else 0.0
    sl_price = pos.get("stop_loss_price", 0)
    risk_per_unit = entry_price - sl_price
    r_multiple = round((exit_price - entry_price) / risk_per_unit, 2) if risk_per_unit > 0 else 0.0

    now = datetime.now().isoformat()
    entry_time = pos.get("entry_date", "")
    holding_days = None
    if entry_time:
        try:
            e = datetime.strptime(entry_time, "%Y-%m-%d")
            holding_days = (datetime.now() - e).days
        except ValueError:
            pass

    update_data = {
        "realized_pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
    }
    transit_result = transit_status(position_id, "CLOSED", update_data)
    if not transit_result.get("ok"):
        return transit_result

    thesis_source = pos.get("thesis_source", "UNKNOWN")
    regime = pos.get("regime_at_entry", "RANGING")
    entry_score = pos.get("conviction_score", 0.0)

    memory_engine.record_trade_memory(
        symbol=pos.get("symbol", "UNKNOWN"),
        model_source=thesis_source,
        regime=regime,
        entry_time=entry_time,
        exit_time=now,
        entry_score=entry_score,
        exit_score=0.0 if exit_reason in ("STOP_LOSS", "EMERGENCY") else entry_score,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple,
        holding_period_days=holding_days,
        max_favorable_excursion=max_favorable_excursion,
        max_adverse_excursion=max_adverse_excursion,
        exit_reason=exit_reason,
    )
    memory_engine.update_model_regime_stats(thesis_source, regime)

    streak = memory_engine.get_recent_model_streak(thesis_source)
    if streak["streak_losses"] >= 3:
        memory_engine.log_conviction_decay(
            thesis_source, regime, streak["streak_losses"], 0, 0.5, 1.0, f"POST_CLOSE_LOSS_STREAK_{streak['streak_losses']}"
        )

    return {
        "ok": True,
        "position_id": position_id,
        "symbol": pos.get("symbol"),
        "pnl_pct": pnl_pct,
        "r_multiple": r_multiple,
        "exit_reason": exit_reason,
    }


_COLS_CACHE = None


def _get_position_columns():
    global _COLS_CACHE
    if _COLS_CACHE is None:
        with get_portfolio_connection() as conn:
            _COLS_CACHE = [d[1] for d in conn.execute("PRAGMA table_info(position_lifecycle)").fetchall()]
    return _COLS_CACHE


def get_position(position_id: int) -> dict:
    with get_portfolio_connection() as conn:
        row = conn.execute("SELECT * FROM position_lifecycle WHERE id=?", (position_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "Not found"}
    return dict(zip(_get_position_columns(), row))


def get_active_positions() -> list:
    with get_portfolio_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM position_lifecycle
            WHERE status NOT IN ('CLOSED', 'WATCHLIST')
            ORDER BY created_at DESC
        """).fetchall()
    cols = _get_position_columns()
    return [dict(zip(cols, r)) for r in rows]


def get_open_positions() -> list:
    with get_portfolio_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM position_lifecycle
            WHERE status IN ('ENTERED', 'SCALE_IN', 'REDUCED')
            ORDER BY created_at DESC
        """).fetchall()
    cols = _get_position_columns()
    return [dict(zip(cols, r)) for r in rows]


def get_portfolio_summary() -> dict:
    with get_portfolio_connection() as conn:
        active = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(current_size), 0),
                   COALESCE(SUM(current_size * avg_cost), 0)
            FROM position_lifecycle WHERE status IN ('ENTERED','SCALE_IN','REDUCED')
        """).fetchone()
        snapshot = conn.execute("SELECT * FROM portfolio_telemetry ORDER BY snapshot_date DESC LIMIT 1").fetchone()
    return {
        "open_positions": active[0],
        "total_shares": active[1],
        "market_value_vnd": round(active[2], 0),
        "latest_snapshot": dict(zip(_get_telemetry_columns(), snapshot or [None] * 6)) if snapshot else None,
    }


def _get_telemetry_columns():
    with get_portfolio_connection() as conn:
        return [d[1] for d in conn.execute("PRAGMA table_info(portfolio_telemetry)").fetchall()]
