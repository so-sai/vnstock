"""Lightweight in-process Event Bus cho Hard Shutdown IPC."""

from datetime import datetime, timezone
from typing import Any, Callable

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
        except Exception:
            pass


def clear():
    _HANDLERS.clear()


HARD_SHUTDOWN = "hard_shutdown"
DOUBLE_SIGNAL = "double_signal"
BREAK_GLASS_OVERRIDE = "break_glass_override"
