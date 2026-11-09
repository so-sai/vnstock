#!/usr/bin/env python
"""Chặn `unsafe` MỚI ngoài allowlist — "compiler/reviewer làm hộ" ở lớp CI.

[WHY]
    Hai crate thuần số học (`ptck_core`, `lttb_wasm`) đã bật `#![forbid(unsafe_code)]`,
    nhưng `src/main.rs` (binary Win32) buộc phải giữ 7 `unsafe` hợp pháp
    (`AttachConsole`, `GetLocalTime`, `CreateMutexA`, `OpenMutexA`, `WaitForSingleObject`,
    `ReleaseMutex`, `CloseHandle`). Không thể `forbid` toàn workspace ⇒ cần một cổng chặn
    **sự xuất hiện mới** thay vì chặn mọi thứ: mỗi file có trần đếm, vượt trần là đỏ.

[ROOT CAUSE được bảo vệ]
    Trước 09/11/2026 không có cổng nào: ai thêm `unsafe` vào kernel cũng lọt, và crate này
    lại là nơi tính breadth/NAV — chỗ mà một lỗi bộ nhớ biến thành con số sai lặng lẽ.

[INVARIANT]
    (1) `unsafe` ngoài allowlist ⇒ exit 1;
    (2) `unsafe` trong file allowlist VƯỢT trần ⇒ exit 1 (thêm mới vẫn bị bắt);
    (3) comment/docstring có chữ "unsafe" KHÔNG bị tính (chống vá quá tay — tài liệu
        `[WHY]` của chính crate này nhắc chữ đó);
    (4) 2 crate thuần phải còn `#![forbid(unsafe_code)]`; ai xoá ⇒ exit 1.

    Đây là Layer-2 (CI) bổ sung cho Layer-1 (`forbid` ở compile-time): `forbid` chỉ bảo vệ
    crate bật nó, còn script này bảo vệ các điểm nối không thể bật (Win32/Tauri).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# [WHY] Trần đếm theo file thay vì "được phép unsafe": thêm một lời gọi Win32 mới vào
# `src/main.rs` làm thay đổi bề mặt FFI và phải được review riêng, nên phải đỏ.
ALLOWLIST: dict[str, int] = {
    "src/main.rs": 7,
    "frontend/src-tauri/src/main.rs": 0,
    # [WHY] `src/template_main.rs` là KHUÔN FFI sqlite thủ công, hiện KHÔNG được compile
    # (root Cargo.toml chỉ khai `[[bin]] path = "src/main.rs"`). Giữ trần 10 để không nổ oan,
    # nhưng nếu ai thêm nó vào build thì bề mặt FFI thay đổi và phải review riêng — cố ý
    # không đặt về 0 để tránh sửa file khuôn ngoài phạm vi.
    "src/template_main.rs": 10,
}

# [WHY] Hai crate này là kernel số học thuần; nếu mất `forbid` thì lớp bảo vệ mạnh nhất
# (compile-time) biến mất mà không ai biết.
FORBID_REQUIRED: tuple[str, ...] = (
    "backend/crates/ptck_core/src/lib.rs",
    "crates/lttb_wasm/src/lib.rs",
)

SKIP_DIRS = {".git", "target", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}

_UNSAFE_RE = re.compile(r"\bunsafe\b")
_FORBID_RE = re.compile(r"#!\[forbid\(unsafe_code\)\]")


def strip_comments(src: str) -> str:
    """Bỏ comment dòng/khối + string literal để chữ "unsafe" trong tài liệu không bị đếm.

    [WHY] Không strip thì mọi `//! [WHY] ... unsafe ...` sẽ làm script tự báo sai — đúng
    kiểu "vá quá tay" khiến người ta gỡ cổng thay vì sửa code.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == "/" and nxt == "*":
            depth = 1
            i += 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth += 1
                    i += 2
                elif src.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue
        if ch == '"':
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def count_unsafe(path: Path) -> int:
    src = path.read_text(encoding="utf-8", errors="replace")
    return len(_UNSAFE_RE.findall(strip_comments(src)))


def iter_rust_files(root: Path):
    for p in root.rglob("*.rs"):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def check(root: Path) -> list[str]:
    problems: list[str] = []
    for rel in FORBID_REQUIRED:
        p = root / rel
        if not p.exists():
            problems.append(f"THIẾU FILE: {rel} (không kiểm chứng được #![forbid(unsafe_code)])")
            continue
        if not _FORBID_RE.search(p.read_text(encoding="utf-8", errors="replace")):
            problems.append(f"THIẾU #![forbid(unsafe_code)]: {rel}")

    for p in iter_rust_files(root):
        rel = p.relative_to(root).as_posix()
        n = count_unsafe(p)
        cap = ALLOWLIST.get(rel, 0)
        if n > cap:
            if rel in ALLOWLIST:
                problems.append(f"{rel}: {n} unsafe > trần {cap} (thêm lời gọi FFI mới?)")
            else:
                problems.append(f"{rel}: {n} unsafe ngoài allowlist (crate thuần phải có 0)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chặn unsafe mới ngoài allowlist")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="thư mục gốc repo (mặc định: cha của scripts/)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    problems = check(root)
    if problems:
        print("[UNSAFE-GUARD] FAIL — phát hiện unsafe ngoài hợp đồng:", file=sys.stderr)
        for prob in problems:
            print(f"  - {prob}", file=sys.stderr)
        print(
            "  Nếu đây là FFI hợp pháp mới: cập nhật ALLOWLIST trong scripts/check_unsafe_allowlist.py"
            " kèm [WHY] và review riêng.",
            file=sys.stderr,
        )
        return 1
    print("[UNSAFE-GUARD] OK — không có unsafe mới ngoài allowlist; forbid còn nguyên.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
