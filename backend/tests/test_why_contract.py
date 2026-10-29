"""Test cho linter `[WHY]` report-only (AGENTS.md luật E).

[WHY] Luật E nói rõ: mọi hằng số/ngưỡng/hệ số điều chỉnh PHẢI có comment
`# [WHY]`. Nhưng "bắt buộc" mà không có máy kiểm thì sau vài tháng sẽ không ai
biết chỗ nào thiếu — và tệ hơn, người sau sẽ "dọn dẹp" đúng những hằng số có
lý do thật. Bộ test này khoá hai điều: (a) linter phát hiện ĐÚNG ca thiếu, (b)
linter KHÔNG báo nhầm ca đã có `[WHY]` (nếu báo nhầm thì người ta sẽ tắt nó đi,
và cả cơ chế chết).

[WHY] Test cũng khoá tính chất report-only: `main()` trả 0 khi chỉ báo cáo, kể
cả khi có vi phạm — chỉ `--strict` mới trả 1. Nếu mặc định chặn CI thì một
commit đang sửa dở sẽ bị chặn vì lý do không liên quan.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.tools.why_contract import Finding, format_report, main, scan_files, scan_root  # noqa: E402


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_constant_without_why_is_reported(tmp_path):
    """[WHY] Ca gốc: hằng số ngưỡng không có lý do là đúng thứ luật E cấm."""
    src = _write(tmp_path, "m.py", "LIMIT = 0.15\n")
    findings = scan_files([src])
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, Finding)
    assert f.lineno == 1
    assert f.name == "LIMIT"
    assert f.category == "constant"


def test_constant_with_leading_why_is_clean(tmp_path):
    """[WHY] Chống báo nhầm: block comment `[WHY]` ngay trên hằng số là hợp lệ."""
    src = _write(tmp_path, "m.py", "# [WHY]: 15% là biên UPCoM — sửa sẽ sai luật.\nLIMIT = 0.15\n")
    assert scan_files([src]) == []


def test_constant_with_trailing_why_is_clean(tmp_path):
    """[WHY] Comment cùng dòng cũng là bằng chứng hợp lệ — nếu chỉ nhận block
    phía trên thì người viết sẽ phải đổi style vô ích."""
    src = _write(tmp_path, "m.py", "LIMIT = 0.15  # [WHY]: biên UPCoM theo quy chế\n")
    assert scan_files([src]) == []


def test_trivial_constants_are_not_reported(tmp_path):
    """[WHY] 0/1/-1 là hằng số cấu trúc (khởi tạo, đếm), không phải ngưỡng; báo
    chúng sẽ làm báo cáo đầy nhiễu và che mất các ngưỡng thật."""
    src = _write(tmp_path, "m.py", "EMPTY = 0\nONE = 1\nNEG = -1\nZERO_F = 0.0\n")
    assert scan_files([src]) == []


def test_numeric_expression_constant_is_detected(tmp_path):
    """[WHY] Ngưỡng thường viết dưới dạng biểu thức (`0.05 / 2`, `2 * 86400`);
    chỉ bắt literal đơn sẽ bỏ sót đúng các ca khó đọc nhất."""
    src = _write(tmp_path, "m.py", "HALF_FEE = 0.0015 / 2\n")
    findings = scan_files([src])
    assert [f.name for f in findings] == ["HALF_FEE"]


def test_literal_threshold_in_function_without_why_is_reported(tmp_path):
    """[WHY] Ngưỡng nằm trong `if` (không phải hằng số module) vẫn là ngưỡng —
    luật E áp cho cả công thức lẫn khối xử lý dữ liệu, không chỉ tên biến."""
    src = _write(
        tmp_path,
        "m.py",
        "def f(x):\n    if x > 127:\n        return 1\n    return 0\n",
    )
    findings = scan_files([src])
    cats = {f.category for f in findings}
    assert "literal-threshold" in cats
    hit = [f for f in findings if f.category == "literal-threshold"][0]
    assert hit.lineno == 2


def test_literal_threshold_in_function_with_why_is_clean(tmp_path):
    """[WHY] Chống báo nhầm ở cấp hàm: docstring/comment `[WHY]` của hàm giải
    thích ngưỡng bên trong nó."""
    src = _write(
        tmp_path,
        "m.py",
        "def f(x):\n    # [WHY]: 127 là trần hợp lệ của trường giá trị này.\n    if x > 127:\n        return 1\n    return 0\n",
    )
    assert scan_files([src]) == []


def test_syntax_error_is_reported_not_crashed(tmp_path):
    """[WHY] File đang sửa dở có thể không parse được. Linter phải BÁO việc đó
    thay vì chết giữa lượt quét (fail-closed nhưng phải nhìn thấy được)."""
    src = _write(tmp_path, "bad.py", "def broken(:\n    pass\n")
    findings = scan_files([src])
    assert len(findings) == 1
    assert findings[0].category == "parse-error"


def test_main_is_report_only_by_default(tmp_path, capsys):
    """[WHY] Report-only là điều kiện mở của NQ-08: mặc định KHÔNG chặn; chỉ
    `--strict` mới đổi exit code. Đảo mặc định ⇒ chặn oan mọi commit."""
    _write(tmp_path, "m.py", "LIMIT = 3.5\n")
    assert main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "LIMIT" in out
    assert main(["--root", str(tmp_path), "--strict"]) == 1


def test_format_report_counts_by_category(tmp_path):
    """[WHY] Báo cáo phải có số TỔNG theo nhóm: một danh sách phẳng 300 dòng
    không giúp Hội đồng quyết định nên vá nhóm nào trước."""
    _write(tmp_path, "a.py", "X = 5.0\n")
    _write(tmp_path, "b.py", "def g(v):\n    if v > 9:\n        return 1\n    return 0\n")
    text = format_report(scan_root(tmp_path), root=tmp_path)
    assert "constant" in text
    assert "literal-threshold" in text


def test_comparison_outside_condition_is_out_of_documented_scope(tmp_path):
    """[WHY] Khoá PHẠM VI đã khai báo (invariant 5): `return v > 9` không phải
    điều kiện nên linter cố ý KHÔNG báo. Test này tồn tại để lần sau ai mở rộng
    phạm vi phải sửa cả docstring/hợp đồng, không âm thầm đổi hành vi."""
    src = _write(tmp_path, "m.py", "def g(v):\n    return v > 9\n")
    assert scan_files([src]) == []


def test_len_comparison_is_not_a_threshold(tmp_path):
    """[WHY] `len(x) == 2` là kiểm tra số phần tử, không phải ngưỡng nghiệp vụ;
    báo nó sẽ làm nhiễu báo cáo và che các ngưỡng thật (`score >= 80`)."""
    src = _write(
        tmp_path,
        "m.py",
        "def f(xs):\n    if len(xs) >= 2:\n        return 1\n    return 0\n",
    )
    assert scan_files([src]) == []


def test_cli_why_audit_wires_strict_exit_code():
    """[WHY] Linter đúng nhưng không nối vào CLI thì vô dụng; và nếu dispatcher
    `main()` nuốt giá trị trả về thì `--strict` im lặng thành exit 0 — đúng loại
    lỗi "kiểm mà không chặn" khó phát hiện nhất. Test này chạy CLI THẬT để khoá
    cả hai: report-only mặc định trả 0, `--strict` trả 1."""
    ptck = BACKEND.parent / "ptck.py"
    base = [
        sys.executable,
        str(ptck),
        "why-audit",
        "--root",
        str(BACKEND / "src"),
        "--top",
        "1",
    ]
    report_only = subprocess.run(
        base,
        cwd=str(BACKEND.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert report_only.returncode == 0, report_only.stderr[-2000:]
    assert "LINTER [WHY]" in report_only.stdout

    strict = subprocess.run(
        [*base, "--strict"],
        cwd=str(BACKEND.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert strict.returncode == 1, strict.stderr[-2000:]


def test_scan_real_backend_src_smoke():
    """[WHY] Chạy trên cây thật để chắc linter không chết trên cú pháp đặc thù
    của repo (match/case, decorator, type alias...). Không assert số vi phạm —
    đó là số đo, không phải hợp đồng."""
    findings = scan_root(BACKEND / "src")
    assert isinstance(findings, list)
    assert all(f.category in {"constant", "literal-threshold", "parse-error"} for f in findings)
