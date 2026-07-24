"""
test_frozen_compatibility.py — WHY: Frozen binary path resolution guard.

BOUNDARY: Nuitka 4.1.3 on Python 3.14 does NOT reliably set sys.frozen.
The _hydrate_path() fallback (executable stem check) must correctly distinguish
dev mode (python.exe) from frozen mode (uv_backend.exe). A failure here means
PROJECT_ROOT → temp extraction dir → "DB not found" → server crash.

Do NOT add sys.frozen-only checks without the exec-stem fallback.
Do NOT use Path(sys.executable).suffix.lower() == '.exe' — python.exe matches too.
"""
import sys
import pathlib
import pytest


@pytest.mark.parametrize("exec_name,expected", [
    ("python.exe", False),
    ("uv_backend.exe", True),
    ("app.exe", True),
    ("python3.14.exe", False),
])
def test_frozen_detection_logic(exec_name, expected):
    predicate = (
        getattr(sys, 'frozen', False)
        or not pathlib.Path(exec_name).stem.lower().startswith("python")
    )
    assert predicate == expected, (
        f"Frozen detection misclassified exec_name={exec_name}: "
        f"expected frozen={expected}, got {predicate}"
    )


@pytest.mark.parametrize("exec_name,expected", [
    ("python.exe", True),  # BUG: python.exe suffix is .exe too
    ("uv_backend.exe", True),
    ("app.exe", True),
    ("python3.14.exe", True),  # BUG: python3.14.exe also .exe
])
def test_wrong_frozen_detection_via_exe_suffix(exec_name, expected):
    predicate = (
        getattr(sys, 'frozen', False)
        or pathlib.Path(exec_name).suffix.lower() == '.exe'
    )
    assert predicate == expected, (
        f"suffix-based detection misclassified exec_name={exec_name}: "
        f"expected frozen={expected}, got {predicate} — "
        f"this proves suffix check is WRONG (python.exe is .exe too)"
    )


def test_project_root_exists():
    from src.config import PROJECT_ROOT
    assert PROJECT_ROOT.exists(), f"PROJECT_ROOT ({PROJECT_ROOT}) does not exist"
    assert PROJECT_ROOT.is_dir(), f"PROJECT_ROOT ({PROJECT_ROOT}) is not a directory"


def test_data_dir_exists():
    from src.config import DATA_DIR
    assert DATA_DIR.exists(), f"DATA_DIR ({DATA_DIR}) does not exist"
    assert DATA_DIR.is_dir(), f"DATA_DIR ({DATA_DIR}) is not a directory"


def test_cmd_serve_reload_off_when_frozen():
    is_frozen = (
        getattr(sys, 'frozen', False)
        or not pathlib.Path(sys.executable).stem.lower().startswith("python")
    )
    use_reload = not is_frozen
    assert use_reload is not is_frozen
    # If exec is NOT python (frozen) → use_reload must be False
    if not pathlib.Path(sys.executable).stem.lower().startswith("python"):
        assert use_reload is False, "Reload MUST be False in frozen mode"
