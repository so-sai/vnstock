"""Lightweight in-process Event Bus cho Hard Shutdown IPC."""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_HANDLERS: dict[str, list[Callable]] = {}


def subscribe(event: str, callback: Callable):
    _HANDLERS.setdefault(event, []).append(callback)


def unsubscribe(event: str, callback: Callable):
    handlers = _HANDLERS.get(event, [])
    if callback in handlers:
        handlers.remove(callback)


def emit(event: str, data: Any = None):
    for cb in _HANDLERS.get(event, []):
        try:
            cb(data)
        except Exception:  # noqa: BLE001 - batch isolation: 1 callback lỗi không dừng các callback khác
            logger.debug("Event handler %s lỗi (bỏ qua, không ảnh hưởng handler khác)", getattr(cb, "__name__", cb))


def clear():
    _HANDLERS.clear()


HARD_SHUTDOWN = "hard_shutdown"
DOUBLE_SIGNAL = "double_signal"
BREAK_GLASS_OVERRIDE = "break_glass_override"
