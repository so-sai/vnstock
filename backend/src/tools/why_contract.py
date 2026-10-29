"""LINTER `[WHY]` — REPORT-ONLY (AGENTS.md luật E).

[WHY] Luật E bắt mọi hằng số/ngưỡng/hệ số điều chỉnh phải có comment
`# [WHY]`, nhưng trước module này KHÔNG có máy nào kiểm. Hệ quả đã thấy: một
hằng số "lạ" không có lý do thì người sau hoặc (a) sợ, không dám sửa, hoặc (b)
"dọn dẹp" đúng chỗ có lý do thật. Cả hai đều làm hỏng dữ liệu lặng lẽ. Linter
này không sửa gì, chỉ biến "bắt buộc" thành số đo được.

[ROOT CAUSE] Việc kiểm từng chỗ bằng mắt không mở rộng được: `backend/src` có
hàng trăm hằng số ngưỡng (biên trần/sàn, lưới tick, ngưỡng ADV, phạt trượt
giá...). Không có danh sách máy sinh thì không ai biết mức tuân thủ đang tăng
hay giảm sau mỗi commit.

[INVARIANT]
(1) REPORT-ONLY: `main()` trả 0 kể cả khi có vi phạm; chỉ `--strict` mới trả 1.
    Linter chặn CI sẽ bị tắt vào đúng lúc cần nó nhất (khi đang sửa dở).
(2) KHÔNG BAO GIỜ tự sửa code và không đọc/ghi DB.
(3) Mọi kết luận phải kèm `file:dòng` + nguyên văn dòng code để người đọc tự
    kiểm — cấm chỉ in số lượng.
(4) File không parse được ⇒ ghi nhận `parse-error`, KHÔNG bỏ qua im lặng
    (Missing > Fake: bỏ qua sẽ làm báo cáo "sạch" một cách giả tạo).
(5) Phạm vi nhận diện là CỐ Ý HẸP và khai báo rõ: (a) hằng số cấp module/lớp
    có giá trị số (kể cả biểu thức số), (b) literal số |x| ≥ 2 trong điều kiện
    `if`/`while`/`assert`. Không nhận diện ngưỡng ẩn trong dict/call để tránh
    báo nhầm tràn lan — hạn chế này ghi ở đây thay vì giả vờ đã phủ hết.

Chạy:
    python -m src.tools.why_contract --root src
    python ptck.py why-audit --root src --top 10
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# [WHY] Đánh dấu hợp lệ. So khớp không phân biệt hoa/thường vì trong repo tồn
# tại cả `# [WHY]:` và `# [why]`; bắt phân biệt sẽ đẻ ra vi phạm giả chỉ vì
# chính tả, và người ta sẽ sửa comment thay vì đọc nó.
WHY_MARK = "[WHY]"

# [WHY] 0/1/-1 là hằng số CẤU TRÚC (khởi tạo, đếm, chỉ số), không phải ngưỡng
# nghiệp vụ. Báo chúng sẽ làm báo cáo đầy nhiễu và che mất các ngưỡng thật —
# đúng loại "cảnh báo giả" đã làm Sentinel bị bỏ qua.
TRIVIAL_NUMBERS = frozenset({0, 1, -1, 0.0, 1.0, -1.0})

# [WHY] Ngưỡng so sánh |x| < 2 (tức 0 và ±1) là kiểm tra tồn tại/đếm, không
# phải ngưỡng lọc. Dùng 2 làm mốc để không phải liệt kê từng ca.
MIN_THRESHOLD_ABS = 2


@dataclass(frozen=True)
class Finding:
    """Một vi phạm (hoặc một file không parse được)."""

    path: str
    lineno: int
    category: str
    name: str
    detail: str = ""
    code: str = ""


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _trailing_has_why(line: str) -> bool:
    """`# [WHY]` nằm trong phần comment của dòng."""
    if "#" not in line:
        return False
    return WHY_MARK in line.split("#", 1)[1].upper()


def _leading_block_has_why(lines: list[str], lineno: int) -> bool:
    """Block comment LIỀN TRÊN dòng `lineno` (1-based) có `[WHY]` không.

    [WHY] Bắt buộc liền kề (không cho phép dòng trống ngăn cách): một comment
    cách 2 dòng trở lên thường nói về khối khác, nhận nó sẽ làm linter báo
    "sạch" cho đúng chỗ đang thiếu.
    """
    idx = lineno - 2
    while idx >= 0:
        stripped = lines[idx].strip()
        if not stripped or not stripped.startswith("#"):
            return False
        if WHY_MARK in stripped.upper():
            return True
        idx -= 1
    return False


def _has_why(lines: list[str], lineno: int) -> bool:
    if 0 <= lineno - 1 < len(lines) and _trailing_has_why(lines[lineno - 1]):
        return True
    return _leading_block_has_why(lines, lineno)


def _is_number(node: ast.AST | None) -> bool:
    """Literal số hoặc biểu thức số (unary/binop) — KHÔNG tính bool."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int | float) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return _is_number(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_number(node.left) and _is_number(node.right)
    return False


def _is_trivial(node: ast.AST | None) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return _is_trivial(node.operand)
    if isinstance(node, ast.Constant):
        return node.value in TRIVIAL_NUMBERS
    return False


def _is_numeric_threshold(node: ast.AST | None) -> bool:
    """Literal số có |x| ≥ 2 (bỏ 0/±1 vì là kiểm tra cấu trúc)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return _is_numeric_threshold(node.operand)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        if isinstance(node.value, bool):
            return False
        return abs(float(node.value)) >= MIN_THRESHOLD_ABS
    return False


def _enclosing_function(tree: ast.AST, lineno: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= lineno <= end and (best is None or start >= best.lineno):
                best = node
    return best


def _module_constants(tree: ast.Module) -> list[ast.Assign | ast.AnnAssign]:
    out: list[ast.Assign | ast.AnnAssign] = []
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            out.append(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.Assign | ast.AnnAssign):
                    out.append(sub)
    return out


def _assign_targets(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in targets if isinstance(t, ast.Name)]


def _assign_value(node: ast.Assign | ast.AnnAssign) -> ast.AST | None:
    return node.value


def _condition_nodes(tree: ast.AST) -> list[ast.If | ast.While | ast.Assert]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.If | ast.While | ast.Assert)]


def _is_arity_check(node: ast.AST) -> bool:
    """`len(x) == 2` / `len(x) < 3` là kiểm tra SỐ PHẦN TỬ, không phải ngưỡng.

    [WHY] Đo lượt quét đầu trên `backend/src` (29/10/2026): nhóm
    `literal-threshold` có 701 mục, trong đó phần lớn là `if len(x) == 2` /
    `if len(x) >= 2` — kiểm tra cấu trúc dữ liệu, không phải quyết định nghiệp
    vụ. Giữ chúng lại thì báo cáo đầy nhiễu và người đọc sẽ bỏ qua cả những
    ngưỡng thật (ví dụ `if score >= 80` phân loại pha). Lọc theo BẢN CHẤT
    (đại lượng đo vs. độ dài cấu trúc), không phải để "cho sạch số".
    """
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"


def _literal_in_compare(test: ast.AST) -> ast.AST | None:
    """Trả literal số (|x| ≥ 2) đầu tiên trong biểu thức so sánh, nếu có.

    [WHY] Bỏ qua cặp so sánh có một phía là `len(...)` (xem `_is_arity_check`).
    """
    nodes = [test]
    while nodes:
        node = nodes.pop()
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(_is_arity_check(side) for side in sides):
                continue
            for side in sides:
                if _is_numeric_threshold(side):
                    return side
        elif isinstance(node, ast.BoolOp):
            nodes.extend(node.values)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            nodes.append(node.operand)
    return None


def scan_file(path: Path) -> list[Finding]:
    """Quét một file. Không raise vì lỗi nội dung file (ghi `parse-error`)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=str(path))
    except (SyntaxError, ValueError) as exc:
        return [Finding(str(path), getattr(exc, "lineno", 0) or 0, "parse-error", path.name, str(exc)[:160])]

    lines = _read_lines(path)
    findings: list[Finding] = []

    for node in _module_constants(tree):
        value = _assign_value(node)
        if value is None or not _is_number(value) or _is_trivial(value):
            continue
        if _has_why(lines, node.lineno):
            continue
        for name in _assign_targets(node):
            if not name.isupper():
                continue
            code = lines[node.lineno - 1].strip() if node.lineno - 1 < len(lines) else ""
            findings.append(
                Finding(
                    str(path),
                    node.lineno,
                    "constant",
                    name,
                    "hằng số số học cấp module/lớp không có comment [WHY]",
                    code,
                )
            )

    for node in _condition_nodes(tree):
        literal = _literal_in_compare(node.test)
        if literal is None:
            continue
        if _has_why(lines, node.lineno):
            continue
        func = _enclosing_function(tree, node.lineno)
        if func is not None and _has_why(lines, func.lineno):
            continue
        code = lines[node.lineno - 1].strip() if node.lineno - 1 < len(lines) else ""
        findings.append(
            Finding(
                str(path),
                node.lineno,
                "literal-threshold",
                func.name if func is not None else "<module>",
                f"ngưỡng literal {ast.unparse(literal)} trong điều kiện không có [WHY]",
                code,
            )
        )

    return findings


def scan_files(paths: list[Path] | tuple[Path, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_file(Path(path)))
    return findings


def scan_root(root: Path | str) -> list[Finding]:
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"không tìm thấy thư mục {root}")
    return scan_files(sorted(root.rglob("*.py")))


def _relative(path: str, root: Path | None) -> str:
    try:
        return str(Path(path).resolve().relative_to(root.resolve())) if root else path
    except ValueError:
        return path


def format_report(findings: list[Finding], root: Path | str | None = None, top: int = 20) -> str:
    """Báo cáo dạng text: tổng theo nhóm + chi tiết theo file (kèm nguyên văn)."""
    root_path = Path(root) if root is not None else None
    lines: list[str] = []
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.category] = counts.get(f.category, 0) + 1

    lines.append("=" * 88)
    lines.append("LINTER [WHY] — REPORT-ONLY (AGENTS.md luật E: hằng số/ngưỡng phải có lý do)")
    lines.append("=" * 88)
    total = len(findings)
    by_cat = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "không có"
    lines.append(f"Tổng vi phạm: {total} | theo nhóm: {by_cat}")
    lines.append("Phạm vi nhận diện: hằng số số học cấp module/lớp; literal số |x|≥2 trong if/while/assert.")
    lines.append("Báo cáo này KHÔNG chặn CI (chỉ --strict mới đổi exit code).")
    lines.append("")

    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        grouped.setdefault(f.path, []).append(f)

    for path in sorted(grouped):
        items = sorted(grouped[path], key=lambda x: (x.lineno, x.category))
        lines.append(f"[{_relative(path, root_path)}] {len(items)} mục")
        for f in items[:top]:
            code = f" | {f.code}" if f.code else ""
            lines.append(f"  {f.lineno:>5}: {f.category:<18} {f.name:<28}{code}")
            if f.detail:
                lines.append(f"         ↳ {f.detail}")
        if len(items) > top:
            lines.append(f"  ... và {len(items) - top} mục nữa (dùng --top để xem thêm)")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="why-audit",
        description="Linter [WHY] report-only: liệt kê hằng số/ngưỡng thiếu lý do (không sửa gì).",
    )
    parser.add_argument(
        "--root",
        default=str(BACKEND_DIR / "src"),
        help="Thư mục quét (mặc định backend/src)",
    )
    parser.add_argument("--top", type=int, default=20, help="Số mục in tối đa mỗi file (mặc định 20)")
    parser.add_argument("--json", action="store_true", help="Xuất JSON thô")
    parser.add_argument(
        "--category",
        default=None,
        choices=["constant", "literal-threshold", "parse-error"],
        help="Chỉ hiện một nhóm",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Đổi exit code thành 1 khi có vi phạm (mặc định: report-only, luôn 0)",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(args.root)
    try:
        findings = scan_root(root)
    except FileNotFoundError as exc:
        print(f"FAIL_CLOSED: {exc}")
        return 2

    if args.category:
        findings = [f for f in findings if f.category == args.category]

    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        print(format_report(findings, root=root, top=args.top))

    if args.strict and findings:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
