"""Cổng chặn — khai báo sàn Python (`requires-python`) phải khớp cú pháp THẬT của cây nguồn.

[WHY] Một khai báo sàn thấp hơn cú pháp đang dùng là một LỜI HỨA SAI có hậu quả thật: người
dùng trên bản Python đó cài được gói (`pip` chỉ đọc `requires-python`), rồi chết ngay khi
import với `SyntaxError` — lỗi không thể phát hiện bằng test, vì test chạy trên interpreter
đang có. Kèm theo đó là các trường mô tả (`classifiers`) và ghi chú tự nhận một dải phiên bản
không còn đúng, đúng loại "ghi chú sai" khó thấy nhất vì nó KHÔNG bao giờ nổ.

[ROOT CAUSE] `backend/pyproject.toml` là file cấu hình gốc của repo (CI + pre-commit đều chạy
`--config backend/pyproject.toml`) nhưng khối `[project]` của nó là bản sao metadata upstream
vnstock v3.5.0, viết khi cây nguồn còn chạy Python 3.10. Cây nguồn về sau dùng cú pháp chỉ hợp
lệ từ 3.14 (PEP 758: `except OSError, AttributeError, ValueError:` — không ngoặc) — 93 chỗ
trong backend/src — trong khi `requires-python` vẫn ghi `>=3.10`. Đúng loại lỗi
"local toolchain không phải oracle": venv 3.14, CI 3.14, `py_compile` xanh, nên không cổng nào
báo; chỉ khai báo là sai.

[INVARIANT]
  1. Nếu cây nguồn first-party có cú pháp PEP 758 thì MỌI `requires-python` first-party phải
     >= 3.14. Ngược lại nếu hạ sàn xuống < 3.14 thì cổng này phải ĐỎ (chứng minh bằng test
     `test_guard_rejects_floor_below_syntax`).
  2. Quét theo LỚP (mọi file first-party), không theo file đang sửa.
  3. Bỏ qua chuỗi và chú thích khi quét: tài liệu/hướng dẫn hay VIẾT RA cú pháp cũ để giải
     thích, và bản thân test này cũng chứa ví dụ ⇒ nếu không bỏ qua sẽ tự báo động giả.
  4. Thiếu file metadata ⇒ ĐỎ (fail-closed), không được skip: "không đọc được" không phải
     "không có vấn đề".
"""

from __future__ import annotations

import io
import re
import tokenize
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Khai báo sàn bắt buộc phải có (đã vào git).
FLOOR_DECLARATIONS = (
    "backend/pyproject.toml",
    "backend/crates/ptck_core/pyproject.toml",
)

#: Khai báo chỉ kiểm KHI CÓ MẶT. `backend/libs/` nằm trong .gitignore (vendor cài tại chỗ),
#: nên trên CI nó không tồn tại — bắt nó phải có sẽ làm cổng đỏ vì lý do chẳng liên quan.
OPTIONAL_DECLARATIONS = ("backend/libs/vnstock/pyproject.toml",)

#: Thư mục quét cú pháp (first-party). `libs/` là fork được vá tại chỗ nên vẫn tính.
SCAN_DIRS = ("backend/src", "backend/tests", "backend/scripts")

#: `except A, B:` hoặc `except A, B, C:` — KHÔNG ngoặc. Hợp lệ từ 3.14 (PEP 758).
PARENLESS_EXCEPT = re.compile(r"^\s*except\s+[A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)+\s*:")

#: Phiên bản nhỏ nhất mà cú pháp PEP 758 đòi hỏi.
PEP758_FLOOR = (3, 14)


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Các dòng CÓ MÃ chứa token `except` (bỏ chuỗi + chú thích)."""
    out: dict[int, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME and tok.string == "except":
                out[tok.start[0]] = tok.line
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # File không tokenize được là vấn đề riêng; ở đây bỏ qua để không che lỗi thật.
        pass
    return sorted(out.items())


def find_parenless_except(root: Path = PROJECT_ROOT) -> list[str]:
    """Mọi chỗ dùng `except A, B:` (first-party) — dạng 'đường_dẫn:dòng'."""
    hits: list[str] = []
    for rel in SCAN_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            for lineno, line in _code_lines(path):
                if PARENLESS_EXCEPT.match(line.split("#", 1)[0]):
                    hits.append(f"{path.relative_to(root).as_posix()}:{lineno}")
    return hits


def declared_floor(root: Path = PROJECT_ROOT) -> tuple[tuple[int, ...], list[str]]:
    """(sàn thấp nhất được khai báo, danh sách khai báo đã đọc)."""
    floors: list[tuple[int, ...]] = []
    seen: list[str] = []
    for rel in FLOOR_DECLARATIONS + OPTIONAL_DECLARATIONS:
        path = root / rel
        if not path.is_file():
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        spec = data.get("project", {}).get("requires-python")
        if not spec:
            continue
        match = re.search(r">=\s*(\d+)\.(\d+)", spec)
        if not match:
            raise AssertionError(f"{rel}: không đọc được sàn từ requires-python={spec!r}")
        floors.append((int(match.group(1)), int(match.group(2))))
        seen.append(f"{rel}={spec}")
    if not floors:
        raise AssertionError(
            "FAIL-CLOSED: không đọc được `requires-python` của bất kỳ gói first-party nào "
            f"({', '.join(FLOOR_DECLARATIONS)}) — thiếu khai báo không phải là 'không có vấn đề'."
        )
    return min(floors), seen


# ── Cổng ────────────────────────────────────────────────────────────────────
def assert_floor_covers_syntax(hits: list[str], floor: tuple[int, ...], seen: list[str] | None = None) -> None:
    """Luật cổng, tách riêng để test được cả HAI chiều (đạt và đỏ)."""
    if not hits:
        raise AssertionError(
            "Không tìm thấy `except A, B:` nào — nếu bạn vừa chuyển hết sang dạng có ngoặc thì "
            "hãy cập nhật cổng này (nó đang bảo vệ một bất biến không còn tồn tại)."
        )
    if floor < PEP758_FLOOR:
        raise AssertionError(
            f"FAIL: khai báo sàn Python = {floor[0]}.{floor[1]} nhưng cây nguồn dùng cú pháp PEP 758 "
            f"({len(hits)} chỗ, vd {hits[:3]}) chỉ hợp lệ từ 3.14 ⇒ người dùng 3.<sàn> cài được rồi "
            f"SyntaxError khi import. Khai báo đang đọc: {seen or 'n/a'}"
        )


def test_floor_declared_covers_pep758_syntax():
    """ĐIỀU KIỆN CHÍNH: có `except A, B:` trong cây nguồn ⇒ sàn khai báo phải >= 3.14."""
    hits = find_parenless_except()
    floor, seen = declared_floor()
    assert_floor_covers_syntax(hits, floor, seen)


def test_guard_rejects_floor_below_syntax():
    """[WHY] Chống cổng rỗng: cổng phải TỪNG ĐỎ khi sàn thấp hơn cú pháp — nếu không thì nó chỉ
    là trang trí. Chạy đúng luật cổng với sàn giả 3.10, kỳ vọng AssertionError."""
    hits = find_parenless_except()
    try:
        assert_floor_covers_syntax(hits, (3, 10), ["backend/pyproject.toml=>=3.10"])
    except AssertionError as exc:
        assert "chỉ hợp lệ từ 3.14" in str(exc), f"cổng đỏ vì lý do khác: {exc}"
        return
    raise AssertionError("CỔNG RỖNG: sàn giả 3.10 vẫn đi qua — cổng không chặn được gì.")


def test_floor_declarations_all_readable():
    """[WHY] Mọi khai báo phải tồn tại và đọc được: một file bị xoá/đổi khoá sẽ làm cổng mù."""
    missing = [rel for rel in FLOOR_DECLARATIONS if not (PROJECT_ROOT / rel).is_file()]
    assert not missing, f"FAIL-CLOSED: thiếu khai báo sàn: {missing}"


def test_optional_declaration_absent_does_not_break_gate(tmp_path):
    """[WHY] Cổng phải xanh trên CI, nơi `backend/libs/` (gitignored) KHÔNG tồn tại — một cổng đỏ
    vì lý do chẳng liên quan sẽ bị gỡ bỏ, và mất luôn cổng."""
    import shutil

    for rel in FLOOR_DECLARATIONS:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / rel, dst)
    floor, seen = declared_floor(tmp_path)
    assert floor == (3, 14), f"sàn đọc sai khi thiếu khai báo tuỳ chọn: {floor} {seen}"


def test_scanner_ignores_strings_and_comments():
    """[WHY] Tài liệu hay VIẾT RA cú pháp cũ để giải thích; nếu quét cả chuỗi thì cổng tự báo
    động giả rồi bị tắt trong im lặng — mất cổng."""
    sample = (
        '"""ví dụ: except OSError, AttributeError, ValueError:"""\n'
        "# except A, B:\n"
        "try:\n"
        "    pass\n"
        "except (TypeError, ValueError):\n"
        "    pass\n"
    )
    tmp = PROJECT_ROOT / "backend" / "tests" / "_tmp_scan_probe.py"
    try:
        tmp.write_text(sample, encoding="utf-8")
        code_lines = [line for _, line in _code_lines(tmp)]
        detected = [line for line in code_lines if PARENLESS_EXCEPT.match(line.split("#", 1)[0])]
        assert detected == [], f"quét cả chuỗi/chú thích hoặc nhận nhầm dạng có ngoặc: {detected}"
        # và phải NHẬN RA dạng không ngoặc khi nó thật sự có
        tmp.write_text("try:\n    pass\nexcept OSError, ValueError:\n    pass\n", encoding="utf-8")
        code_lines = [line for _, line in _code_lines(tmp)]
        detected = [line for line in code_lines if PARENLESS_EXCEPT.match(line.split("#", 1)[0])]
        assert len(detected) == 1, f"bỏ sót dạng không ngoặc: {detected}"
    finally:
        tmp.unlink(missing_ok=True)
