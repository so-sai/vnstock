"""Pre-commit gate: chặn API NumPy 1.x đã bị BỎ ở NumPy 2.

Vì sao cần: `ndarray.ptp()` từng hợp lệ và nay raise `AttributeError`. Nó không làm
hỏng lúc import, không làm đỏ linter, không làm hỏng test nào khác — nó chỉ nổ đúng
một nhánh (nhánh có dữ liệu) nên rất dễ lọt qua review. Đây cùng họ với vụ pandas 3
`.loc[:, col] =` đổi dtype: API bị bỏ ở major version, hỏng lặng lẽ.

Dùng `ast` chứ KHÔNG dùng regex: regex từng bắt nhầm chính docstring mô tả mẫu cấm
(bài học từ `check_pandas3_pattern.py`). Cây AST không có khái niệm chuỗi trang trí.

Dùng như pre-commit hook (nhận danh sách file staged từ argv) hoặc chạy tay.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Phương thức của ndarray bị NumPy 2 bỏ. Dạng ĐÚNG còn lại: np.ptp(arr).
BANNED_METHODS: dict[str, str] = {
    "ptp": "NumPy 2 đã bỏ ndarray.ptp() — dùng np.ptp(arr)",
}
# Chủ sở hữu được phép: gọi trên MODULE numpy thì hợp lệ (np.ptp(arr)).
ALLOWED_OWNERS = {"np", "numpy"}

SKIP_DIRS = (".venv", "site-packages", "__pycache__", ".git")


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _owner_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find_violations(path: Path) -> list[tuple[int, str]]:
    """Trả về [(dòng, thông điệp)] cho mọi lời gọi bị cấm."""
    try:
        src = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        # Không đọc được ⇒ KHÔNG được coi là sạch (fail-closed, bài học từ hook cũ).
        return [(-1, "không đọc được file để xác minh — coi như KHÔNG sạch")]
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []  # để ruff/bước sau báo lỗi cú pháp, tránh báo trùng
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in BANNED_METHODS:
            continue
        if _owner_name(fn.value) in ALLOWED_OWNERS:
            continue
        out.append((node.lineno, BANNED_METHODS[fn.attr]))
    return out


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv if a.endswith(".py") and not should_skip(Path(a))]
    if not targets:
        return 0
    total = 0
    for path in targets:
        for line, msg in find_violations(path):
            loc = f"{path}:{line}" if line > 0 else str(path)
            print(f"[numpy2] ❌ {loc}: {msg}")
            total += 1
    if total:
        print(f"\n[numpy2] {total} vi phạm — số này sẽ raise AttributeError trên NumPy 2.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
