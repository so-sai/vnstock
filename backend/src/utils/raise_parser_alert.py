"""
raise_parser_alert — Standardized [PTCK_ALERT] decorator cho crawler modules.

Pattern:
    @raise_parser_alert(source="VIETSTOCK", recovery="ptck.py cafef-crawl --source vietstock --playwright")
    def parse_bctt_payload(payload):
        ...

Khi parser raise exception:
    1. In [PTCK_ALERT] với source, mô tả, và lệnh CLI khắc phục
    2. Trả về giá trị fail-safe (mặc định [])
    3. Ghi log structured error
"""

import functools
import logging
import sys
import traceback
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

PTCK_ALERT_FMT = (
    "\n"
    "  ╔══════════════════════════════════════════════════════════════╗\n"
    "  ║  [PTCK_ALERT] {source:<49} ║\n"
    "  ╠══════════════════════════════════════════════════════════════╣\n"
    "  ║  {message:<55} ║\n"
    "  ║  Position forced to 0.0 (fail-safe mode)                    ║\n"
    "  ║  Run: {recovery:<51} ║\n"
    "  ╚══════════════════════════════════════════════════════════════╝\n"
)


def raise_parser_alert(
    source: str = "UNKNOWN",
    message: str = "Parser structure changed — data unavailable.",
    recovery: str = "ptck.py cafef-crawl",
    fail_safe: Any = None,
) -> Callable:
    """Decorator: catch parser exceptions, print [PTCK_ALERT], return fail-safe.

    Args:
        source: Tên nguồn dữ liệu (VIETSTOCK, CAFEF, VNDIRECT, TCBS, SBV...)
        message: Mô tả lỗi
        recovery: Câu lệnh CLI khắc phục
        fail_safe: Giá trị trả về khi lỗi (mặc định [] cho parser)

    Usage:
        @raise_parser_alert(source="VIETSTOCK", recovery="ptck.py cafef-crawl --source vietstock")
        def parse_bctt_payload(payload):
            ...
    """
    if fail_safe is None:
        fail_safe = []

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception:
                alert = PTCK_ALERT_FMT.format(
                    source=source,
                    message=message,
                    recovery=recovery,
                )
                print(alert, file=sys.stderr)
                logger.error(
                    "[PTCK_ALERT] %s — %s. Recovery: %s\n%s",
                    source,
                    message,
                    recovery,
                    traceback.format_exc(),
                )
                return fail_safe

        return wrapper

    return decorator
