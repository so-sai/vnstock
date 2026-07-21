"""fallback_resolver.py — 3-tầng Fallback Data Pipeline cho API ngoại vi.

Kiến trúc:
  Tầng 1: Stale Cache  — dữ liệu cũ trong daily_ohlcv (nếu staleness < MAX_STALE)
  Tầng 2: Bootstrap    — dữ liệu từ bootstraps/ (macro) hoặc lịch sử xa hơn (OHLCV)
  Tầng 3: Synthetic    — dòng dữ liệu giả trong RAM với is_synthetic=True
                         TUYỆT ĐỐI KHÔNG ghi vào daily_ohlcv.

Khi is_synthetic=True → Governor nhận FORCE_LOCK_HDR (đóng băng giao dịch mới).
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.database.data_freshness import (
    MAX_STALE_HOURS,
    check_staleness,
    ensure_table,
    mark_api_status,
    resolve_staleness_for_consumer,
    upsert_freshness,
)
from src.database.db_core import get_connection

logger = logging.getLogger("PTCK_FALLBACK")

# Synthetic data constants
_SYNTHETIC_DECAY = 0.999  # mỗi phiên synthetic giảm 0.1%
_SYNTHETIC_VOLUME = 0  # volume = 0 cho synthetic bars


def _get_project_root() -> Path:
    from pathlib import Path
    import sys
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return current


PROJECT_ROOT = _get_project_root()
BOOTSTRAP_DIR = PROJECT_ROOT / "backend" / "data" / "bootstraps"


def resolve(
    symbol: str,
    date: str,
    max_stale_hours: float = MAX_STALE_HOURS,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """4-tầng Auto-Recovery Fallback Resolver.

    Args:
        symbol: Mã cổ phiếu
        date: Ngày cần dữ liệu (YYYY-MM-DD)
        max_stale_hours: Ngưỡng chấp nhận dữ liệu cũ (mặc định 48h)

    Returns:
        Tuple (row_dict, metadata)
        - row_dict: OHLCV dict hoặc None nếu không có dữ liệu
        - metadata: Dict {source, fallback, is_synthetic, staleness_hours, reason,
          trust_score, confidence_penalty}
          Khi is_synthetic=True → force_hdr = 1.0
    """
    # === TẦNG 0 + 0.5: Live Sources (Multi-Source Router) ===
    try:
        from src.data.multi_source_router import compute_confidence_penalty, route_live
        live_row, live_meta = route_live(symbol, date)
        if live_row is not None:
            meta = {
                "source": live_meta.get("source", "PRIMARY"),
                "provider": live_meta.get("provider", "KBS"),
                "fallback": live_meta.get("fallback", False),
                "is_synthetic": False,
                "staleness_hours": 0.0,
                "api_status": "OK",
                "reason": None,
                "trust_score": live_meta.get("trust_score", 0.95),
                "confidence_penalty": compute_confidence_penalty(live_meta),
                "timeout_ms": live_meta.get("timeout_ms", 0),
            }
            return live_row, meta
    except Exception as e:
        logger.debug(f"[TIER_0] route_live fail: {e}")

    # === TẦNG 1: Kiểm tra freshness ===
    source, staleness, api_status = check_staleness(symbol, date)

    # === TẦNG 1: Stale Cache ===
    row = _fetch_from_ohlcv(symbol, date)
    if row is not None:
        is_stale = staleness > max_stale_hours or source == "UNKNOWN"
        if not is_stale:
            return row, {
                "source": "CACHE",
                "fallback": False,
                "is_synthetic": False,
                "staleness_hours": round(staleness, 2),
                "api_status": api_status,
                "reason": None,
                "trust_score": 0.6,
                "confidence_penalty": 0.0,
            }
        if staleness <= max_stale_hours * 2:
            return row, {
                "source": "CACHE_STALE",
                "fallback": True,
                "is_synthetic": False,
                "staleness_hours": round(staleness, 2),
                "api_status": api_status,
                "reason": f"stale_cache — {round(staleness, 1)}h tuổi",
                "trust_score": 0.4,
                "confidence_penalty": -0.03,
            }

    # === TẦNG 2: Bootstrap ===
    bootstrap_row = _fetch_from_bootstrap(symbol, date)
    if bootstrap_row is not None:
        return bootstrap_row, {
            "source": "BOOTSTRAP",
            "fallback": True,
            "is_synthetic": False,
            "staleness_hours": -1,
            "api_status": "CACHE_MISS",
            "reason": "bootstrap — dữ liệu lịch sử không có API",
            "trust_score": 0.3,
            "confidence_penalty": -0.08,
        }

    # === TẦNG 3: Synthetic (RAM-only) ===
    synthetic = _build_synthetic(symbol, date)
    return synthetic, {
        "source": "SYNTHETIC",
        "fallback": True,
        "is_synthetic": True,
        "staleness_hours": MAX_STALE_HOURS,
        "api_status": "NO_DATA",
        "force_hdr": 1.0,
        "trust_score": 0.0,
        "confidence_penalty": -0.15,
        "reason": "synthetic — KHÔNG có dữ liệu thật, FORCE_LOCK_HDR",
    }


def resolve_batch(
    symbols: List[str],
    date: str,
    max_stale_hours: float = MAX_STALE_HOURS,
) -> Dict[str, Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]:
    """Batch resolve — trả về dict {symbol: (row, metadata)}."""
    result = {}
    for sym in symbols:
        result[sym] = resolve(sym, date, max_stale_hours)
    return result


def is_any_synthetic(results: Dict[str, Tuple]) -> bool:
    """Kiểm tra nếu bất kỳ symbol nào trong batch bị synthetic."""
    return any(
        meta.get("is_synthetic", False)
        for _, meta in results.values()
    )


def _fetch_from_ohlcv(symbol: str, date: str) -> Optional[Dict[str, Any]]:
    """Tầng 1: Lấy từ daily_ohlcv (cache cũ cũng được)."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT open, high, low, close, adj_close, volume "
                "FROM daily_ohlcv WHERE symbol=? AND date=?",
                (symbol, date),
            ).fetchone()
        if row:
            return {
                "open": float(row[0]),
                "high": float(row[1]),
                "low": float(row[2]),
                "close": float(row[3]),
                "adj_close": float(row[4]),
                "volume": int(row[5]),
            }
    except Exception as e:
        logger.debug(f"[FALLBACK_T1] {symbol}/{date}: {e}")
    return None


def _fetch_from_bootstrap(symbol: str, date: str) -> Optional[Dict[str, Any]]:
    """Tầng 2: Bootstrap từ bootstraps/ (macro) hoặc lịch sử daily_ohlcv xa hơn.

    Với OHLCV: không có bootstrap file cố định. Bootstrap = dữ liệu lịch sử
    cũ hơn trong daily_ohlcv (lần close gần nhất trước date).
    """
    # Thử bootstrap file cho macro data
    bootstrap_file = BOOTSTRAP_DIR / f"{symbol.lower()}_bootstrap.csv"
    if bootstrap_file.exists():
        try:
            df = pd.read_csv(bootstrap_file)
            match = df[df["date"] == date]
            if not match.empty:
                return match.iloc[0].to_dict()
        except Exception as e:
            logger.debug(f"[FALLBACK_T2] {symbol} bootstrap file: {e}")

    # Fallback chính: lấy bar gần nhất trước date từ daily_ohlcv
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT open, high, low, close, adj_close, volume "
                "FROM daily_ohlcv WHERE symbol=? AND date < ? "
                "ORDER BY date DESC LIMIT 1",
                (symbol, date),
            ).fetchone()
        if row:
            return {
                "open": float(row[0]),
                "high": float(row[1]),
                "low": float(row[2]),
                "close": float(row[3]),
                "adj_close": float(row[4]),
                "volume": 0,
            }
    except Exception as e:
        logger.debug(f"[FALLBACK_T2] {symbol}/{date}: {e}")
    return None


def _build_synthetic(symbol: str, date: str) -> Dict[str, Any]:
    """Tầng 3: Synthetic bar trong RAM — KHÔNG ghi vào daily_ohlcv.

    Dùng bar cuối cùng có thật × _SYNTHETIC_DECAY để tạo dữ liệu phẳng.
    volume = 0 để các chỉ báo kỹ thuật (ATR, ADX, PCA SDI) không bị méo.
    """
    last_bar = _fetch_from_ohlcv(symbol, date)
    if last_bar is None:
        last_bar = _fetch_from_bootstrap(symbol, date)

    if last_bar:
        decay = _SYNTHETIC_DECAY
        close = last_bar["close"] * decay
        return {
            "open": close * 0.999,
            "high": close * 1.001,
            "low": close * 0.998,
            "close": close,
            "adj_close": close,
            "volume": _SYNTHETIC_VOLUME,
        }

    # Không có bất kỳ dữ liệu nào — trả về flat bar
    return {
        "open": 1000.0,
        "high": 1000.5,
        "low": 999.5,
        "close": 1000.0,
        "adj_close": 1000.0,
        "volume": 0,
    }


def consume_with_fallback(
    symbol: str,
    date: str,
    max_stale_hours: float = MAX_STALE_HOURS,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Consumer-facing wrapper: resolve + ghi freshness + trả metadata.

    Đây là entry point duy nhất cho mọi consumer (daily_updater, engine, ...).
    """
    row, meta = resolve(symbol, date, max_stale_hours)

    # Ghi freshness tracking
    upsert_freshness(
        symbol=symbol,
        date=date,
        source=meta.get("source", "UNKNOWN"),
        api_status=meta.get("api_status", "UNKNOWN"),
        data_row=row,
        metadata={"fallback": meta.get("fallback", False),
                   "is_synthetic": meta.get("is_synthetic", False),
                   "reason": meta.get("reason")},
    )

    return row, meta
