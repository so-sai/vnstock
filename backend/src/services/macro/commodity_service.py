"""commodity_service.py — Steel Crack Spread derived metric + staleness early warning.

Design (approved 2026-08-14):
  - Raw commodity prices live in macro_history (Single Source of Truth).
  - SteelCrackSpread is a derived in-memory metric, NEVER persisted.
  - Point-In-Time: every lookup filters date <= as_of_date (no look-ahead).
  - Fail-Closed: missing or EVICTed component => None (no fabricated spread).
  - Early warning is staged by publication cadence: warning@50% evict,
    critical@evict-1. Live mode writes system_health; replay mode stays silent.

Staleness matrix (publication cadence -> thresholds, days):
  IRON_ORE_62     daily     warning 4   critical 6   evict 7
  COKING_COAL_HCC weekly    warning 7   critical 13  evict 14
  HRC_CFR         1-2/month warning 15  critical 29  evict 30
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

# Default variable names in macro_history.
DEFAULT_HRC_VAR = "HRC_CFR"
DEFAULT_ORE_VAR = "IRON_ORE_62"
DEFAULT_COAL_VAR = "COKING_COAL_HCC"

# Publication-cadence staleness matrix. warning = early signal for operator to
# seed data manually; critical = imminent eviction; evict = hard fail-closed.
STALE_CONFIG: dict[str, dict[str, int]] = {
    "IRON_ORE_62": {"warning": 4, "critical": 6, "evict": 7},
    "COKING_COAL_HCC": {"warning": 7, "critical": 13, "evict": 14},
    "HRC_CFR": {"warning": 15, "critical": 29, "evict": 30},
}

# Generic fallback when a variable has no entry (aligned with macro_stale_tracker EVICT).
DEFAULT_EVICT_DAYS = 60

# system_health component prefix (schema is PRIMARY KEY(component) -> idempotent upsert).
SYSTEM_HEALTH_COMPONENT = "commodity_sensor"


@dataclass(frozen=True)
class SteelCrackSpreadResult:
    """Result of the Steel Crack Spread calculation with audit metadata."""

    as_of_date: str
    crack_spread: float
    hrc_price: float
    hrc_date: str
    iron_ore_price: float
    iron_ore_date: str
    coking_coal_price: float
    coking_coal_date: str
    ore_coef: float
    coal_coef: float


def _update_system_health(conn: sqlite3.Connection, var: str, status: str, detail: str) -> None:
    """Idempotent upsert into system_health (PRIMARY KEY(component))."""
    component = f"{SYSTEM_HEALTH_COMPONENT}:{var}"
    conn.execute(
        "INSERT OR REPLACE INTO system_health (component, status, last_error, updated_at) VALUES (?, ?, ?, datetime('now'))",
        (component, status, detail),
    )
    conn.commit()


def _check_and_audit_staleness(
    conn: sqlite3.Connection,
    var: str,
    delta_days: int,
    is_live: bool = True,
) -> bool:
    """Check staleness and audit early warnings.

    Returns True if data is still valid (< evict), False if EVICTed.
    In replay mode (is_live=False) warnings are skipped so backtests never
    pollute the live system_health ledger.
    """
    cfg = STALE_CONFIG.get(var)
    evict = cfg["evict"] if cfg else DEFAULT_EVICT_DAYS
    warning = cfg["warning"] if cfg else evict // 2
    critical = cfg["critical"] if cfg else evict - 1

    if delta_days >= evict:
        if is_live:
            logger.error(
                "[FAIL-CLOSED] %s trễ %dd >= %dd (EVICT). Vô hiệu hóa Cổng 2.",
                var,
                delta_days,
                evict,
            )
            _update_system_health(conn, var, "STALE_EVICTED", f"Trễ {delta_days} ngày - Dừng tính Spread")
        return False

    if is_live:
        if delta_days >= critical:
            logger.warning(
                "[CRITICAL] %s trễ %dd (sắp chạm EVICT %dd). CẦN NẠP NGAY!",
                var,
                delta_days,
                evict,
            )
            _update_system_health(conn, var, "STALE_CRITICAL", f"Trễ {delta_days} ngày - Cận kề ngắt mạch")
        elif delta_days >= warning:
            logger.warning(
                "[WARNING] %s trễ %dd >= %dd. Cần nạp thủ công.",
                var,
                delta_days,
                warning,
            )
            _update_system_health(conn, var, "STALE_WARNING", f"Trễ {delta_days} ngày")

    return True


def get_steel_crack_spread(
    conn: sqlite3.Connection,
    as_of_date: str | None = None,
    hrc_var: str = DEFAULT_HRC_VAR,
    ore_var: str = DEFAULT_ORE_VAR,
    coal_var: str = DEFAULT_COAL_VAR,
    ore_coef: float = 1.6,
    coal_coef: float = 0.5,
    allow_stale: bool = False,
    is_live: bool = True,
) -> SteelCrackSpreadResult | None:
    """Compute Steel Crack Spread at a Point-In-Time.

    Spread = HRC - (ore_coef * IronOre) - (coal_coef * CokingCoal)

    Every component is fetched as the latest record with date <= as_of_date.
    Returns None (fail-closed) when any component is missing, stale (unless
    allow_stale=True), or EVICTed. Live mode audits staleness to system_health;
    replay/backtest mode (is_live=False) never writes the ledger.
    """
    target_vars = (hrc_var, ore_var, coal_var)

    query = """
    WITH RankedMacro AS (
        SELECT
            variable, date, value, is_stale,
            ROW_NUMBER() OVER (PARTITION BY variable ORDER BY date DESC) as rn
        FROM macro_history
        WHERE variable IN (?, ?, ?)
          AND (? IS NULL OR date <= ?)
    )
    SELECT variable, date, value, is_stale
    FROM RankedMacro
    WHERE rn = 1;
    """
    cursor = conn.cursor()
    cursor.execute(query, (*target_vars, as_of_date, as_of_date))
    rows = cursor.fetchall()

    components: dict[str, tuple[str, float, int]] = {}
    for var_name, dt, val, stale in rows:
        components[var_name] = (dt, float(val), int(stale if stale is not None else 0))

    if len(components) < 3 or not all(v in components for v in target_vars):
        if is_live:
            logger.warning("[INSUFFICIENT_DATA] Thiếu 1 trong 3 biến spread: %s", target_vars)
        return None

    hrc_dt, hrc_val, hrc_stale = components[hrc_var]
    ore_dt, ore_val, ore_stale = components[ore_var]
    coal_dt, coal_val, coal_stale = components[coal_var]

    for var, dt, stale in ((hrc_var, hrc_dt, hrc_stale), (ore_var, ore_dt, ore_stale), (coal_var, coal_dt, coal_stale)):
        delta = (date.fromisoformat(as_of_date or dt) - date.fromisoformat(dt)).days
        if delta < 0:
            return None
        if stale and not allow_stale:
            if is_live:
                logger.warning("[STALE_REJECT] %s bị cờ is_stale=1 (ngày %s).", var, dt)
            return None
        if not _check_and_audit_staleness(conn, var, delta, is_live=is_live):
            return None

    spread_val = hrc_val - (ore_coef * ore_val) - (coal_coef * coal_val)
    effective_date = as_of_date or max(hrc_dt, ore_dt, coal_dt)

    return SteelCrackSpreadResult(
        as_of_date=effective_date,
        crack_spread=round(spread_val, 4),
        hrc_price=hrc_val,
        hrc_date=hrc_dt,
        iron_ore_price=ore_val,
        iron_ore_date=ore_dt,
        coking_coal_price=coal_val,
        coking_coal_date=coal_dt,
        ore_coef=ore_coef,
        coal_coef=coal_coef,
    )
