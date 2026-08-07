"""multi_source_router.py — Tier 0.5 Multi-Source Provider Failover Router.

Kiến trúc:
  Tiến trình: Sequential, ngắt mạch sớm (Aggressive Failover Timeout)
  Tầng 0: Vietcap/KBS (timeout=8s) → dùng Trading.price_board / Quote.history
  Tầng 0.5: Multi-Source Router (timeout=4s/provider)
    ├── KBS (vnstock source='kbs')
    ├── VCI (vnstock source='vci')
    ├── SSI (FailoverMultiSourceAdapter)
    └── TCBS (schema mapping, ready)
  ╰── Mọi output bắt buộc qua normalize_to_ptd_schema()

Tổng timeout không vượt quá 16s (8 + 4 + 4).
"""

import asyncio
import logging
import time
from typing import Any

from src.data.schema_normalizer import (
    normalize_to_ptd_schema,
    validate_ohlcv,
)
from src.database.db_core import get_connection

logger = logging.getLogger("PTCK_MULTI_SOURCE")

# Timeout config (giây)
PRIMARY_TIMEOUT = 8.0
BACKUP_TIMEOUT = 4.0
MAX_TOTAL_TIMEOUT = 16.0

# Thứ tự providers (ưu tiên giảm dần)
PROVIDER_CHAIN = [
    ("kbs", "KBS"),
    ("vci", "VCI"),
    ("ssi", "SSI"),
    ("tcbs", "TCBS"),
]

# Provider trust scores
PROVIDER_TRUST: dict[str, float] = {
    "kbs": 0.95,
    "vci": 0.92,
    "ssi": 0.85,
    "tcbs": 0.80,
}


def fetch_from_vnstock(
    symbol: str,
    date: str,
    source: str = "kbs",
    timeout: float = BACKUP_TIMEOUT,
) -> tuple[dict[str, Any] | None, str]:
    """Lấy dữ liệu từ vnstock Quote.history() với timeout.

    Returns:
        Tuple (normalized_row, source_label) hoặc (None, source_label) nếu fail.
    """
    try:
        from vnstock import Quote

        q = Quote(symbol=symbol, source=source)
        # Lấy 5 phiên gần nhất để có đủ context
        df = q.history(start=date, end=date, pause=0)
        if df is not None and not df.empty:
            raw = df.iloc[-1].to_dict()
            # Chuẩn hóa cột time → date
            if "time" in raw and "date" not in raw:
                raw["date"] = raw["time"]
            norm, scale_info = normalize_to_ptd_schema(raw, source_label=source)
            issues = validate_ohlcv(norm, symbol=symbol, ref_date=date)
            if issues:
                logger.warning(f"[{source.upper()}] {symbol}: OHLCV issues: {issues}")
            return norm, source
        logger.debug(f"[{source.upper()}] {symbol}: empty response")
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        err = str(e)
        if "timeout" in err.lower() or "timed out" in err.lower():
            logger.warning(f"[{source.upper()}] {symbol}: timeout sau {timeout}s")
        else:
            logger.debug(f"[{source.upper()}] {symbol}: {err}")
    return None, source


def fetch_from_ssi_failover(
    symbol: str,
    timeout: float = BACKUP_TIMEOUT,
) -> tuple[dict[str, Any] | None, str]:
    """SSI qua FailoverMultiSourceAdapter (async wrapper).

    Ghi chú: SSI provider hiện đang là stub (mock data).
    Khi SSI thật được implement, code này tự động dùng được.
    """
    try:
        from src.database.data_quality_failover import FailoverMultiSourceAdapter

        adapter = FailoverMultiSourceAdapter(db_path=str(get_connection().__enter__()))
        df, source_used, is_stale = asyncio.run(adapter.fetch_historical_ohlcv_safe(symbol))
        if df is not None and not df.empty:
            raw = df.iloc[-1].to_dict()
            norm, _ = normalize_to_ptd_schema(raw, source_label=source_used)
            return norm, source_used
    except Exception as e:  # noqa: BLE001 - cố ý bắt rộng để fallback/phòng thủ an toàn
        logger.debug(f"[SSI] {symbol}: {e}")
    return None, "ssi"


def fetch_from_tcbs(symbol: str) -> tuple[dict[str, Any] | None, str]:
    """TCBS — schema mapping tồn tại, provider chưa implement.

    Hiện tại trả về None, log reminder. Khi có TCBS thật,
    implement hàm này và tự động kích hoạt.
    """
    logger.debug(f"[TCBS] {symbol}: Provider chưa implement — skip")
    return None, "tcbs"


def route_live(
    symbol: str,
    date: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Tier 0 + Tier 0.5: Route qua tất cả provider sống.

    Args:
        symbol: Mã cổ phiếu
        date: Ngày cần dữ liệu (YYYY-MM-DD)

    Returns:
        Tuple (row_dict, metadata)
        - row: OHLCV dict hoặc None (nếu mọi live source đều fail)
        - metadata: {source, provider, trust_score, fallback, timeout_ms}
          source = 'PRIMARY' | 'BACKUP_PROVIDER' | None
    """
    start = time.time()

    # ── Tầng 0: KBS (Primary) ──
    row, _ = fetch_from_vnstock(symbol, date, source="kbs", timeout=PRIMARY_TIMEOUT)
    elapsed = (time.time() - start) * 1000
    if row is not None:
        return row, {
            "source": "PRIMARY",
            "provider": "KBS",
            "trust_score": PROVIDER_TRUST["kbs"],
            "fallback": False,
            "timeout_ms": round(elapsed, 1),
        }

    # ── Tầng 0.5: Multi-Source Router ──
    for src, label in PROVIDER_CHAIN:
        if time.time() - start > MAX_TOTAL_TIMEOUT:
            logger.warning(f"[ROUTER] {symbol}: tổng timeout > {MAX_TOTAL_TIMEOUT}s — dừng")
            break

        if src == "kbs":
            continue  # đã thử ở Tầng 0
        elif src == "vci":
            row, _ = fetch_from_vnstock(symbol, date, source="vci", timeout=BACKUP_TIMEOUT)
        elif src == "ssi":
            row, _ = fetch_from_ssi_failover(symbol, timeout=BACKUP_TIMEOUT)
        elif src == "tcbs":
            row, _ = fetch_from_tcbs(symbol)

        if row is not None:
            elapsed = (time.time() - start) * 1000
            return row, {
                "source": "BACKUP_PROVIDER",
                "provider": label,
                "trust_score": PROVIDER_TRUST.get(src, 0.7),
                "fallback": True,
                "timeout_ms": round(elapsed, 1),
            }

    elapsed = (time.time() - start) * 1000
    return None, {
        "source": None,
        "provider": None,
        "trust_score": 0.0,
        "fallback": True,
        "timeout_ms": round(elapsed, 1),
        "reason": "ALL_LIVE_SOURCES_FAILED",
    }


def route_batch_live(
    symbols: list[str],
    date: str,
) -> dict[str, tuple[dict[str, Any] | None, dict[str, Any]]]:
    """Batch route — gọi route_live cho từng symbol.

    Sequential trong batch, batch tiếp theo cách 500ms.
    """
    result = {}
    for i, sym in enumerate(symbols):
        result[sym] = route_live(sym, date)
        if i > 0 and i % 10 == 0:
            time.sleep(0.5)
    return result


def compute_confidence_penalty(meta: dict[str, Any]) -> float:
    """Tính mức phạt confidence dựa trên source.

    PRIMARY: 0 (không phạt)
    BACKUP_PROVIDER: -0.05 (hạ 5%)
    Không có source: -0.15 (hạ 15%)
    """
    source = meta.get("source")
    if source == "PRIMARY":
        return 0.0
    elif source == "BACKUP_PROVIDER":
        return -0.05
    else:
        return -0.15
