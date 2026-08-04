"""test_quant_warning_gate.py — Scoped Warning Gate cho phân hệ quant (CI/CD Layer 3).

WHY (khắc phục điểm mù của lint tĩnh):
  Ruff bắt lỗi cú pháp/import/format nhưng KHÔNG bắt API bị deprecate khi chạy (vd
  pandas `SeriesGroupBy.pct_change` mặc định fill_method). Bằng cách chạy pytest với
  `-W error::FutureWarning` trên CHỈ phân hệ quant, mọi FutureWarning bùng phát trong
  mã định lượng đều trở thành FAIL — triệt tiêu vĩnh viễn API deprecate trước khi chạm
  git. Phân vùng scope (quant only) tránh nhiễu warning từ thư viện ngoài ở module khác.

  Lệnh (khớp .github/workflows/ci.yml → quant-gate):
    python -m pytest tests/test_vn20_quant_filter.py -W error::FutureWarning -q
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND / "libs"))


def test_pct_change_uses_explicit_fill_method():
    """pct_change trong vn20_quant_filter phải khai báo fill_method=None.

    WHY: pandas mặc định fill_method='ffill' cho GroupBy.pct_change sắp bị loại bỏ
    (FutureWarning). Nếu code lại bỏ tham số này, FutureWarning tái xuất → gate fail.
    """
    src = (BACKEND / "src" / "quant" / "vn20_quant_filter.py").read_text(encoding="utf-8-sig")
    assert "pct_change(fill_method=None)" in src, (
        "vn20_quant_filter phải dùng pct_change(fill_method=None) — bỏ qua sẽ tái sinh FutureWarning pandas"
    )


def test_no_bare_pct_change_in_quant():
    """CẤM gọi pct_change() trần trong toàn phân hệ quant — bắt buộc khai báo fill_method."""
    quant_dir = BACKEND / "src" / "quant"
    violations = []
    for py in sorted(quant_dir.rglob("*.py")):
        for i, line in enumerate(py.read_text(encoding="utf-8-sig").splitlines(), 1):
            stripped = line.strip()
            if ".pct_change()" in stripped and not stripped.startswith("#"):
                violations.append(f"{py.relative_to(BACKEND)}:{i}: {stripped}")
    assert not violations, "Quant dùng pct_change() trần (thiếu fill_method):\n" + "\n".join(violations)
