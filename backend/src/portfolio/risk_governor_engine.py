"""
Risk Governor Engine — institutional-grade risk layer.
Controls drawdown, leverage, correlation, and VaR limits.
Feeds into exposure_engine as the outermost risk gate.

Architecture:
    portfolio positions + market data
                |
                v
    risk_governor_engine
        ├─ drawdown governor
        ├─ VaR limit
        ├─ correlation concentration
        └─ leverage throttle
                |
                v
    exposure_engine (dampener chain)
                |
                v
    portfolio_engine.arm_and_size()
"""

import sys
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

import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from src.database.db_core import get_connection
from src.database.portfolio_db import get_portfolio_connection

logger = logging.getLogger(__name__)

MAX_DRAWDOWN_PCT = 15.0
MAX_LEVERAGE = 1.0
MAX_SECTOR_EXPOSURE = 0.30
MAX_SINGLE_NAME = 0.10
VAR_CONFIDENCE = 0.95
VAR_LOOKBACK = 60
CORRELATION_LIMIT = 0.70
CORRELATION_WINDOW = 60


def get_portfolio_snapshot() -> dict:
    try:
        with get_portfolio_connection() as conn:
            positions = pd.read_sql(
                "SELECT symbol, status, entry_price, current_size, total_cost, sector "
                "FROM position_lifecycle WHERE status IN ('ENTERED', 'SCALE_IN', 'REDUCED')",
                conn,
            )
            telemetry = pd.read_sql("SELECT * FROM portfolio_telemetry ORDER BY date DESC LIMIT 1", conn)
    except Exception:
        positions = pd.DataFrame()
        telemetry = pd.DataFrame()

    total_nav = float(telemetry["total_equity"].iloc[0]) if not telemetry.empty else 100_000_000
    cash = float(telemetry["cash"].iloc[0]) if not telemetry.empty else 0

    return {
        "positions": positions,
        "total_nav": total_nav,
        "cash": cash,
    }


def get_market_prices(symbols: list) -> dict:
    if not symbols:
        return {}
    try:
        with get_connection() as conn:
            placeholders = ",".join(["?"] * len(symbols))
            df = pd.read_sql(
                f"SELECT symbol, close FROM daily_ohlcv "
                f"WHERE symbol IN ({placeholders}) AND date = (SELECT MAX(date) FROM daily_ohlcv)",
                conn,
                params=symbols,
            )
            return dict(zip(df["symbol"], df["close"]))
    except Exception:
        return {}


def compute_drawdown() -> dict:
    try:
        with get_portfolio_connection() as conn:
            nav_series = pd.read_sql("SELECT date, total_equity FROM portfolio_telemetry ORDER BY date", conn)
    except Exception:
        return {"current_drawdown_pct": 0.0, "peak_nav": 0, "status": "NO_DATA"}

    if nav_series.empty or len(nav_series) < 2:
        return {"current_drawdown_pct": 0.0, "peak_nav": 0, "status": "INSUFFICIENT"}

    nav_series["peak"] = nav_series["total_equity"].cummax()
    latest = nav_series.iloc[-1]
    current_nav = float(latest["total_equity"])
    peak_nav = float(latest["peak"])
    dd_pct = round((peak_nav - current_nav) / peak_nav * 100, 2) if peak_nav > 0 else 0.0

    if dd_pct >= MAX_DRAWDOWN_PCT:
        status = "EMERGENCY"
    elif dd_pct >= MAX_DRAWDOWN_PCT * 0.7:
        status = "WARNING"
    elif dd_pct >= MAX_DRAWDOWN_PCT * 0.4:
        status = "CAUTION"
    else:
        status = "SAFE"

    return {
        "current_drawdown_pct": dd_pct,
        "peak_nav": round(peak_nav),
        "current_nav": round(current_nav),
        "status": status,
        "max_allowed_drawdown": MAX_DRAWDOWN_PCT,
        "remaining_buffer": round(MAX_DRAWDOWN_PCT - dd_pct, 2),
    }


def compute_var(symbols: list, weights: list | None = None) -> dict:
    if not symbols:
        return {"var_95_pct": 0.0, "status": "NO_POSITIONS"}

    if weights is None:
        weights = [1.0 / len(symbols)] * len(symbols)

    try:
        with get_connection() as conn:
            placeholders = ",".join(["?"] * len(symbols))
            df = pd.read_sql(
                f"SELECT symbol, date, close FROM daily_ohlcv WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
                conn,
                params=symbols,
            )
    except Exception:
        return {"var_95_pct": 0.0, "status": "NO_DATA"}

    if df.empty:
        return {"var_95_pct": 0.0, "status": "NO_DATA"}

    df["return"] = df.groupby("symbol")["close"].pct_change()
    pivot = df.pivot(index="date", columns="symbol", values="return").dropna()

    if pivot.empty:
        return {"var_95_pct": 0.0, "status": "INSUFFICIENT"}

    recent = pivot.tail(min(VAR_LOOKBACK, len(pivot)))

    portfolio_returns = recent.dot(weights)
    var_95 = float(np.percentile(portfolio_returns, (1 - VAR_CONFIDENCE) * 100))
    var_95_pct = round(abs(var_95) * 100, 2)

    return {
        "var_95_pct": var_95_pct,
        "var_95_vnd": 0,
        "lookback_days": len(recent),
        "status": "OK",
    }


def compute_correlation_risk(positions_df: pd.DataFrame) -> dict:
    if positions_df.empty or len(positions_df) < 2:
        return {
            "avg_correlation": 0.0,
            "high_corr_pairs": [],
            "risk_level": "LOW",
            "high_corr_count": 0,
            "correlation_limit": CORRELATION_LIMIT,
            "status": "INSUFFICIENT",
        }

    symbols = positions_df["symbol"].tolist()
    try:
        with get_connection() as conn:
            placeholders = ",".join(["?"] * len(symbols))
            df = pd.read_sql(
                f"SELECT symbol, date, close FROM daily_ohlcv WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
                conn,
                params=symbols,
            )
    except Exception:
        return {
            "avg_correlation": 0.0,
            "high_corr_pairs": [],
            "risk_level": "LOW",
            "high_corr_count": 0,
            "correlation_limit": CORRELATION_LIMIT,
            "status": "NO_DATA",
        }

    if df.empty:
        return {
            "avg_correlation": 0.0,
            "high_corr_pairs": [],
            "risk_level": "LOW",
            "high_corr_count": 0,
            "correlation_limit": CORRELATION_LIMIT,
            "status": "NO_DATA",
        }

    df["return"] = df.groupby("symbol")["close"].pct_change()
    pivot = df.pivot(index="date", columns="symbol", values="return").dropna()
    recent = pivot.tail(min(CORRELATION_WINDOW, len(pivot)))

    if recent.empty or len(recent.columns) < 2:
        return {
            "avg_correlation": 0.0,
            "high_corr_pairs": [],
            "risk_level": "LOW",
            "high_corr_count": 0,
            "correlation_limit": CORRELATION_LIMIT,
            "status": "INSUFFICIENT",
        }

    corr_matrix = recent.corr()
    high_pairs = []
    total_corr = 0.0
    pair_count = 0

    for i in range(len(corr_matrix.columns)):
        for j in range(i + 1, len(corr_matrix.columns)):
            col_i = corr_matrix.columns[i]
            col_j = corr_matrix.columns[j]
            corr_val = float(corr_matrix.iloc[i, j])
            total_corr += abs(corr_val)
            pair_count += 1
            if abs(corr_val) >= CORRELATION_LIMIT:
                high_pairs.append(
                    {
                        "symbol_a": col_i,
                        "symbol_b": col_j,
                        "correlation": round(corr_val, 3),
                    }
                )

    avg_corr = round(total_corr / pair_count, 3) if pair_count > 0 else 0.0

    if len(high_pairs) >= 3:
        risk_level = "HIGH"
    elif len(high_pairs) >= 1:
        risk_level = "MODERATE"
    else:
        risk_level = "LOW"

    return {
        "avg_correlation": avg_corr,
        "high_corr_pairs": high_pairs,
        "high_corr_count": len(high_pairs),
        "correlation_limit": CORRELATION_LIMIT,
        "risk_level": risk_level,
        "status": "OK",
    }


def compute_leverage(snapshot: dict) -> dict:
    nav = snapshot["total_nav"]
    positions_df = snapshot["positions"]

    if positions_df.empty:
        return {"gross_exposure_pct": 0.0, "net_exposure_pct": 0.0, "leverage": 0.0, "status": "NO_POSITIONS"}

    symbols = positions_df["symbol"].tolist()
    prices = get_market_prices(symbols)

    gross_exposure = 0.0
    for _, row in positions_df.iterrows():
        sym = row["symbol"]
        price = prices.get(sym, float(row["entry_price"]))
        qty = float(row["current_size"])
        gross_exposure += price * qty * 1000

    gross_pct = round(gross_exposure / nav * 100, 2) if nav > 0 else 0.0
    net_pct = round((gross_exposure - snapshot["cash"]) / nav * 100, 2) if nav > 0 else 0.0
    leverage = round(gross_exposure / max(nav, 1), 2)

    if leverage > MAX_LEVERAGE:
        status = "OVER_LEVERAGED"
    elif leverage > MAX_LEVERAGE * 0.7:
        status = "CAUTION"
    else:
        status = "SAFE"

    return {
        "gross_exposure_vnd": round(gross_exposure),
        "gross_exposure_pct": gross_pct,
        "net_exposure_pct": net_pct,
        "leverage": leverage,
        "max_leverage": MAX_LEVERAGE,
        "status": status,
    }


def evaluate_risk_governance() -> dict:
    print(f"\n{'=' * 70}")
    print("  RISK GOVERNOR ENGINE")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 70}")

    snapshot = get_portfolio_snapshot()

    drawdown = compute_drawdown()
    leverage = compute_leverage(snapshot)
    var_data = compute_var(snapshot["positions"]["symbol"].tolist() if not snapshot["positions"].empty else [])
    correlation = compute_correlation_risk(snapshot["positions"])

    veto_flags = []

    if drawdown["status"] == "EMERGENCY":
        veto_flags.append(
            {
                "rule": "MAX_DRAWDOWN",
                "severity": "CRITICAL",
                "message": f"Drawdown {drawdown['current_drawdown_pct']}% exceeds {MAX_DRAWDOWN_PCT}% limit",
                "action": "LOCK_ALL",
            }
        )
    elif drawdown["status"] == "WARNING":
        veto_flags.append(
            {
                "rule": "DRAWDOWN_WARNING",
                "severity": "HIGH",
                "message": f"Drawdown {drawdown['current_drawdown_pct']}% approaching limit",
                "action": "REDUCE_ONLY",
            }
        )

    if leverage["status"] == "OVER_LEVERAGED":
        veto_flags.append(
            {
                "rule": "LEVERAGE_LIMIT",
                "severity": "CRITICAL",
                "message": f"Leverage {leverage['leverage']}x exceeds {MAX_LEVERAGE}x limit",
                "action": "REDUCE_ONLY",
            }
        )
    elif leverage["status"] == "CAUTION":
        veto_flags.append(
            {
                "rule": "LEVERAGE_WARNING",
                "severity": "MEDIUM",
                "message": f"Leverage {leverage['leverage']}x approaching limit",
                "action": "REDUCE_NEW_SIZE",
            }
        )

    if correlation["risk_level"] == "HIGH":
        veto_flags.append(
            {
                "rule": "CORRELATION_CONCENTRATION",
                "severity": "HIGH",
                "message": f"High correlation: {correlation['high_corr_count']} pairs above {CORRELATION_LIMIT}",
                "action": "DIVERSIFY",
            }
        )

    if var_data.get("var_95_pct", 0) > 3.0:
        veto_flags.append(
            {
                "rule": "VAR_LIMIT",
                "severity": "MEDIUM",
                "message": f"VaR(95%) {var_data['var_95_pct']}% exceeds 3% threshold",
                "action": "REDUCE_SIZE",
            }
        )

    # Determine overall governor state
    severities = [v["severity"] for v in veto_flags]
    if "CRITICAL" in severities:
        governor_state = "LOCKDOWN"
        trading_multiplier = 0.0
    elif "HIGH" in severities:
        governor_state = "RESTRICTED"
        trading_multiplier = 0.25
    elif "MEDIUM" in severities:
        governor_state = "CAUTION"
        trading_multiplier = 0.50
    else:
        governor_state = "NORMAL"
        trading_multiplier = 1.0

    verdict = {
        "timestamp": datetime.now().isoformat(),
        "governor_state": governor_state,
        "trading_multiplier": trading_multiplier,
        "drawdown": drawdown,
        "leverage": leverage,
        "var": var_data,
        "correlation": correlation,
        "veto_flags": veto_flags,
        "summary": {
            "state": governor_state,
            "active_vetoes": len(veto_flags),
            "critical_count": severities.count("CRITICAL"),
            "high_count": severities.count("HIGH"),
            "medium_count": severities.count("MEDIUM"),
        },
    }

    print(f"\nGovernor State: {governor_state}")
    print(f"Trading Multiplier: {trading_multiplier}")
    print(f"Active Vetoes: {len(veto_flags)}")
    for v in veto_flags:
        print(f"  [{v['severity']:8s}] {v['rule']}: {v['message']}")
    print(f"\nDrawdown: {drawdown['current_drawdown_pct']}% ({drawdown['status']})")
    print(f"Leverage: {leverage['leverage']}x ({leverage['status']})")
    print(f"Avg Correlation: {correlation.get('avg_correlation', 0)} ({correlation.get('risk_level', 'N/A')})")
    print(f"VaR(95%): {var_data.get('var_95_pct', 'N/A')}%")
    print(f"{'=' * 70}")

    _store_risk_verdict(verdict)
    _export_risk_json(verdict)

    return verdict


def _store_risk_verdict(verdict: dict):
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_governance_history (
                    date TEXT PRIMARY KEY,
                    governor_state TEXT,
                    trading_multiplier REAL,
                    drawdown_pct REAL,
                    drawdown_status TEXT,
                    leverage REAL,
                    leverage_status TEXT,
                    avg_correlation REAL,
                    var_95_pct REAL,
                    active_vetoes INTEGER,
                    veto_details TEXT
                )
            """)
            dd = verdict.get("drawdown", {})
            lev = verdict.get("leverage", {})
            corr = verdict.get("correlation", {})
            var_data = verdict.get("var", {})
            row = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "governor_state": verdict.get("governor_state", ""),
                "trading_multiplier": verdict.get("trading_multiplier", 1.0),
                "drawdown_pct": dd.get("current_drawdown_pct", 0),
                "drawdown_status": dd.get("status", ""),
                "leverage": lev.get("leverage", 0),
                "leverage_status": lev.get("status", ""),
                "avg_correlation": corr.get("avg_correlation", 0),
                "var_95_pct": var_data.get("var_95_pct", 0),
                "active_vetoes": len(verdict.get("veto_flags", [])),
                "veto_details": json.dumps(verdict.get("veto_flags", []), ensure_ascii=False),
            }
            cols = ", ".join(row.keys())
            vals = ", ".join(["?"] * len(row))
            conn.execute(f"INSERT OR REPLACE INTO risk_governance_history ({cols}) VALUES ({vals})", list(row.values()))
            conn.commit()
    except Exception as e:
        logger.warning(f"Store risk verdict error: {e}")


def _export_risk_json(verdict: dict):
    out_path = Path(str(PROJECT_ROOT)) / "backend" / "data" / "output" / "risk_governance.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)
    print(f"Saved to: {out_path}")


if __name__ == "__main__":
    if sys.platform == "win32":
        import io

        if isinstance(sys.stdout, io.TextIOWrapper):
            if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
                try:
                    sys.stdout.reconfigure(encoding="utf-8")
                except Exception:
                    pass
        elif hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    evaluate_risk_governance()
