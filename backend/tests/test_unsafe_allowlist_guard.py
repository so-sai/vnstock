"""Test cổng chặn `unsafe` mới (`scripts/check_unsafe_allowlist.py`).

[WHY]
    Cổng này là Layer-2 bù cho 2 chỗ KHÔNG THỂ bật `#![forbid(unsafe_code)]`:
    `src/main.rs` (7 FFI Win32 hợp pháp) và `frontend/src-tauri`. Một cổng không có test
    sẽ sớm bị "nới" cho qua (thêm path vào allowlist, hoặc vô tình bỏ qua comment) — và
    khi đó lớp bảo vệ mạnh nhất của kernel biến mất mà không ai biết.

[INVARIANT]
    (a) cây sạch ⇒ exit 0;
    (b) unsafe ở file ngoài allowlist ⇒ exit 1;
    (c) unsafe THÊM vào file đã allowlist (vượt trần) ⇒ exit 1;
    (d) mất `#![forbid(unsafe_code)]` ⇒ exit 1;
    (e) chống vá quá tay: chữ "unsafe" trong comment/docstring/string KHÔNG bị tính.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(Path(__file__).resolve().parent.parent.parent.parents):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
SCRIPT = PROJECT_ROOT / "scripts" / "check_unsafe_allowlist.py"

_CLEAN_FILES = {
    "backend/crates/ptck_core/src/lib.rs": "#![forbid(unsafe_code)]\n\nuse pyo3::prelude::*;\n",
    "crates/lttb_wasm/src/lib.rs": "#![forbid(unsafe_code)]\n\npub fn f(x: &[f64]) -> f64 {\n    x[0]\n}\n",
    "src/main.rs": 'extern "system" {\n    fn A();\n}\n' + "fn f() {\n    unsafe {\n        A();\n    }\n}\n" * 7,
    "frontend/src-tauri/src/main.rs": "fn main() {}\n",
}


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_tree(tmp_path: Path, overrides: dict[str, str] | None = None) -> Path:
    files = dict(_CLEAN_FILES)
    files.update(overrides or {})
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    return tmp_path


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_clean_workspace_passes(tmp_path):
    """[WHY] Cây đúng hợp đồng hiện tại phải XANH; nếu không, cổng sẽ bị tắt ngay ngày đầu."""
    proc = _run(_make_tree(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "UNSAFE-GUARD] OK" in proc.stdout


def test_new_unsafe_outside_allowlist_fails(tmp_path):
    """[WHY] Đây là mục tiêu chính: kernel mới thêm `unsafe` (ví dụ đọc mảng thô) phải đỏ."""
    root = _make_tree(
        tmp_path,
        {"backend/crates/ptck_core/src/breadth.rs": "pub fn g() {\n    unsafe { }\n}\n"},
    )
    proc = _run(root)
    assert proc.returncode == 1
    assert "breadth.rs" in proc.stderr


def test_extra_unsafe_inside_allowlisted_file_fails(tmp_path):
    """[WHY] Allowlist theo TRẦN, không theo "file được miễn": thêm lời gọi FFI thứ 8 vào
    `src/main.rs` là thay đổi bề mặt FFI, phải được review riêng.
    """
    root = _make_tree(
        tmp_path,
        {"src/main.rs": _CLEAN_FILES["src/main.rs"] + "fn g() {\n    unsafe {\n        A();\n    }\n}\n"},
    )
    proc = _run(root)
    assert proc.returncode == 1
    assert "src/main.rs" in proc.stderr


def test_missing_forbid_fails(tmp_path):
    """[WHY] Ai xoá `#![forbid(unsafe_code)]` khỏi kernel thì mất lớp bảo vệ compile-time —
    cổng này phải bắt được, vì CI không tự biết ý định đó là cố ý hay nhầm.
    """
    root = _make_tree(
        tmp_path,
        {"crates/lttb_wasm/src/lib.rs": "pub fn f(x: &[f64]) -> f64 {\n    x[0]\n}\n"},
    )
    proc = _run(root)
    assert proc.returncode == 1
    assert "forbid(unsafe_code)" in proc.stderr


def test_unsafe_only_in_comments_and_strings_is_ignored(tmp_path):
    """[WHY] Chống vá quá tay: docstring `[WHY]` của chính script/crate có nhắc chữ "unsafe";
    nếu tính cả comment thì cổng tự đỏ và người ta sẽ gỡ cổng thay vì giữ nó.
    """
    root = _make_tree(
        tmp_path,
        {
            "backend/crates/ptck_core/src/breadth.rs": (
                "//! [WHY] Không dùng unsafe ở đây.\n"
                "// unsafe trong comment dòng\n"
                "/* unsafe trong comment khối */\n"
                'pub const MSG: &str = "unsafe";\n'
                "pub fn ok() -> u8 {\n    1\n}\n"
            )
        },
    )
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
