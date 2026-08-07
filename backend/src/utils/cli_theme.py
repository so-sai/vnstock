"""cli_theme.py — Zero-Dependency ANSI Semantic Color Palette & VT100 Enabler.

WHY:
  Đạt chuẩn Zero-Dependency (không phụ thuộc colorama / rich). Sử dụng ctypes
  để bật SetConsoleMode(ENABLE_VIRTUAL_TERMINAL_PROCESSING) trên Windows Console,
  hoạt động trực tiếp trên PowerShell, CMD, Linux, macOS.

  Tự động kiểm tra cờ NO_COLOR (https://no-color.org) để đảm bảo không làm bẩn
  file log hoặc stdout khi output được redirect / pipe vào file.
"""

import os
import sys
from pathlib import Path

# ── Sentinel v2.2 (AGENTS.md Anchor) ────────────────────────────────
_candidate = Path(sys.executable).resolve().parent
if Path(sys.executable).stem.lower().startswith("python"):
    _p = Path(__file__).resolve().parent.parent.parent
    for _par in [_p] + list(_p.parents):
        if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
            _candidate = _par
            break
PROJECT_ROOT = _candidate
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _is_color_supported() -> bool:
    """Check if color rendering is allowed."""
    if os.getenv("NO_COLOR"):
        return False
    return True


def init_terminal_colors():
    """Enable VT100 virtual terminal processing on Windows if supported."""
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            # STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:  # noqa: BLE001, S110 - cố ý bắt rộng & bỏ qua phụ (fallback/phòng thủ)
            pass


# Automatically attempt VT100 enablement on import
init_terminal_colors()


class Color:
    RED = "\033[1;31m"
    GREEN = "\033[1;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[1;36m"
    DIM = "\033[2;37m"
    RESET = "\033[0m"


def c_red(text: str) -> str:
    if not _is_color_supported():
        return text
    return f"{Color.RED}{text}{Color.RESET}"


def c_green(text: str) -> str:
    if not _is_color_supported():
        return text
    return f"{Color.GREEN}{text}{Color.RESET}"


def c_yellow(text: str) -> str:
    if not _is_color_supported():
        return text
    return f"{Color.YELLOW}{text}{Color.RESET}"


def c_cyan(text: str) -> str:
    if not _is_color_supported():
        return text
    return f"{Color.CYAN}{text}{Color.RESET}"


def c_dim(text: str) -> str:
    if not _is_color_supported():
        return text
    return f"{Color.DIM}{text}{Color.RESET}"


def badge(text: str, color_code: str) -> str:
    if not _is_color_supported():
        return text
    return f"{color_code}{text}{Color.RESET}"


def color_mos(mos: float | None) -> str:
    """Format Margin of Safety with semantic color."""
    if mos is None:
        return c_dim("MoS: N/A")
    text = f"MoS: {mos:+.1f}%"
    if mos >= 20.0:
        return c_green(text)
    elif mos >= 0.0:
        return c_yellow(text)
    else:
        return c_red(text)


def color_status(status: str) -> str:
    """Format status / risk level with semantic color."""
    st = str(status).upper()
    if st in ("GREEN", "PASS", "OK", "OPEN", "SCALE_IN"):
        return c_green(status)
    elif st in ("YELLOW", "ORANGE", "WARN", "CAUTION", "HOLD", "REDUCE", "WAIT"):
        return c_yellow(status)
    elif st in ("RED", "FAIL", "CRITICAL", "VETO", "AVOID", "ABORT"):
        return c_red(status)
    else:
        return c_dim(status)
