"""cache_guard.py — Clear global in-memory caches (Test Contract 4: Isolation Guard).

Ngăn ô nhiễm trạng thái (State Contamination) giữa các bài test: mọi cache/bộ nhớ
đệm toàn cục phải được xóa sạch sau mỗi test case để kết quả KHÔNG phụ thuộc
thứ tự chạy (test order dependency).

Danh sách cache toàn cục đã biết (bổ sung khi phát hiện mới):
  - services.macro.gold_world_service.GOLD_CACHE
  - services.macro.silver_world_service.SILVER_CACHE
  - engine.money_flow_engine.MoneyFlowEngine._session_cache
  - services.xray_service._mfe_instance (singleton)
  - api.routes.v1_i18n._cache
  - utils.localization._TERMINAL_UTF8_CACHE

Module Sentinel v2.1 (Anchor Fix) — mọi module src/ phải có _hydrate_path.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _hydrate_path() -> Path:
    """Path Hydrator v2.1 (Anchor Fix): Auto-locate Project Root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = _hydrate_path()

# Đăng ký các cache toàn cục: (module_path, attribute_path, giá trị reset)
_CACHE_TARGETS: list[tuple[str, str, object]] = [
    ("src.services.macro.gold_world_service", "GOLD_CACHE", {}),
    ("src.services.macro.silver_world_service", "SILVER_CACHE", {}),
    ("src.engine.money_flow_engine", "MoneyFlowEngine._session_cache", {}),
    ("src.services.xray_service", "_mfe_instance", None),
    ("src.api.routes.v1_i18n", "_cache", {}),
    ("src.utils.localization", "_TERMINAL_UTF8_CACHE", None),
]


def clear_all_caches() -> int:
    """Xóa sạch toàn bộ cache toàn cục đã đăng ký. Trả về số cache đã reset.

    Idempotent + không crash khi module chưa import được (batch isolation).
    """
    cleared = 0
    for mod_path, attr_path, reset_value in _CACHE_TARGETS:
        try:
            module = __import__(mod_path, fromlist=["*"])
            target = module
            parts = attr_path.split(".")
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    if hasattr(target, part):
                        setattr(target, part, reset_value)
                        cleared += 1
                else:
                    target = getattr(target, part)
        except Exception:  # noqa: BLE001 - batch isolation: 1 module lỗi không dừng việc clear khác
            continue
    return cleared


if __name__ == "__main__":
    print(f"Cleared {clear_all_caches()} global caches.")
