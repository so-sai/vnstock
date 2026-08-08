"""
test_dangling_imports.py — AST Static Check: mọi import tĩnh phải resolve được.

WHY (2026-08-08): khi `grid_search.py` bị xóa (refactor merge v1→v2), các import site
còn trỏ sang module cũ (grid_search_v2, walk_forward_harness, test_determinism) — chỉ
phát hiện khi chạy lại full suite. Test này chặn sớm ở CI, không cần git history:

  - Walk mọi .py trong backend/src + ptck.py
  - Parse bằng ast → resolve mọi import tĩnh về file thật trong cây hiện tại
  - FAIL nếu module nội bộ (top-level thuộc src) KHÔNG tồn tại file/package/namespace

Không phụ thuộc lịch sử git → không false-positive khi file được rename có chủ đích.
Không import module (không side-effect) — thuần AST.

Ghi chú scope:
  - Dynamic import bằng string (importlib.import_module("...")) KHÔNG bắt — runtime guard.
  - stdlib / site-packages (json, numpy, concurrent...) bỏ qua khi không nằm trong src.
"""

import ast
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
_SRC_DIR = _BACKEND_DIR / "src"


def _iter_python_files():
    """Tất cả .py trong backend/src (đệ quy) + ptck.py ở project root."""
    seen = set()
    for py in _SRC_DIR.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        seen.add(py)
    ptck = _PROJECT_ROOT / "ptck.py"
    if ptck.exists():
        seen.add(ptck)
    return sorted(seen)


def _internal_roots() -> set[str]:
    """Top-level names importable từ backend/src (file .py + dir namespace)."""
    roots = set()
    for p in _SRC_DIR.iterdir():
        if p.is_dir() and p.name != "__pycache__":
            roots.add(p.name)
        elif p.is_file() and p.suffix == ".py" and p.name != "__init__.py":
            roots.add(p.stem)
    roots.add("src")
    return roots


def _resolve_absolute(module_dotted: str, src_dir: Path) -> bool:
    """Module 'a.b.c' → tồn tại khi a/b/c.py | a/b/c/__init__.py | a/b/c/ (namespace)."""
    parts = module_dotted.split(".")
    if parts[0] == "src":
        parts = parts[1:]
        if not parts:
            return True  # import src → src/__init__.py
    if not parts:
        return False
    base = src_dir.joinpath(*parts)
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists() or (base.is_dir())


def _resolve_relative(level: int, module: str | None, current_file: Path, src_dir: Path) -> bool:
    """Relative import theo filesystem (không cần __package__)."""
    # Vị trí package của module chứa import
    pkg_dir = current_file.parent
    for _ in range(level - 1):
        pkg_dir = pkg_dir.parent
    if module:
        parts = module.split(".")
        target = pkg_dir.joinpath(*parts)
    else:
        target = pkg_dir
    return target.with_suffix(".py").exists() or (target / "__init__.py").exists() or target.is_dir()


def _collect_dangling() -> list[str]:
    roots = _internal_roots()
    dangling: list[str] = []
    for py in _iter_python_files():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            dangling.append(f"{py}: parse failed ({e})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in roots and not _resolve_absolute(alias.name, _SRC_DIR):
                        dangling.append(f"{py}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    if node.module is None:
                        continue
                    top = node.module.split(".")[0]
                    if top in roots and not _resolve_absolute(node.module, _SRC_DIR):
                        dangling.append(f"{py}:{node.lineno}: from {node.module} import ...")
                else:
                    if not _resolve_relative(node.level, node.module, py, _SRC_DIR):
                        rel = f"from {' ' * (node.level - 1) * 2}... "
                        rel += f"{node.module or ''}".rstrip()
                        dangling.append(f"{py}:{node.lineno}: {rel} (relative)")

    # De-dup giữ nguyên thứ tự
    seen = set()
    return [d for d in dangling if not (d in seen or seen.add(d))]


class TestDanglingImports:
    def test_no_import_to_missing_module(self):
        dangling = _collect_dangling()
        assert not dangling, (
            "Phát hiện import trỏ về module KHÔNG tồn tại trong cây hiện tại:\n  "
            + "\n  ".join(dangling)
            + "\n\nLỗi này thường xảy ra sau khi xóa/rename module mà chưa cập nhật import site."
        )

    def test_scans_ptck_and_src(self):
        files = _iter_python_files()
        assert any("ptck.py" == p.name for p in files), "ptck.py phải được quét"
        assert any("grid_search_v2.py" == p.name for p in files), "src/backtest phải được quét"

    def test_internal_root_detection(self):
        roots = _internal_roots()
        assert "backtest" in roots
        assert "governor" in roots
        assert "src" in roots

    def test_resolver_finds_namespace_package(self):
        # backtest là namespace package (không __init__.py) — resolver phải nhận diện
        assert _resolve_absolute("backtest", _SRC_DIR)
        assert _resolve_absolute("backtest.grid_search_v2", _SRC_DIR)
        assert _resolve_absolute("src.backtest.grid_search_v2", _SRC_DIR)

    def test_resolver_flags_missing_module(self):
        assert not _resolve_absolute("backtest.grid_search", _SRC_DIR)  # đã xóa
        assert not _resolve_absolute("src.nonexistent_pkg.module", _SRC_DIR)
