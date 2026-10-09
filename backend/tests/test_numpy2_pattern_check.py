"""Test guard NumPy 2 — phải CHỨNG MINH nó bắt được lỗi, không chỉ chạy không lỗi.

Một guard không tự chứng minh được nó chặn được lỗi thì không phải guard: vụ
`ndarray.ptp()` đã sống trong repo qua nhiều lần commit vì không ai kiểm.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]


def _load():
    """Nạp script bằng đường dẫn: backend/scripts không phải package."""
    path = PROJECT_ROOT / "backend" / "scripts" / "check_numpy2_pattern.py"
    spec = importlib.util.spec_from_file_location("check_numpy2_pattern", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_numpy2_pattern"] = mod
    spec.loader.exec_module(mod)
    return mod


chk = _load()


def _write(tmp_path: Path, code: str) -> Path:
    p = tmp_path / "sample.py"
    p.write_text(code, encoding="utf-8")
    return p


def test_bat_duoc_ptp_tren_mang(tmp_path):
    """Dạng SAI đã từng có trong gold_paper_ledger.py."""
    p = _write(tmp_path, "import numpy as np\ndef f(dv):\n    return dv.ptp() > 0\n")
    v = chk.find_violations(p)
    assert len(v) == 1
    assert v[0][0] == 3, f"phải trỏ đúng dòng 3, nhận {v}"


def test_cho_qua_np_ptp(tmp_path):
    """Dạng ĐÚNG: np.ptp(arr) phải được tha."""
    p = _write(tmp_path, "import numpy as np\ndef f(dv):\n    return np.ptp(dv) > 0\n")
    assert chk.find_violations(p) == []


def test_khong_bat_nham_trong_chuoi_va_docstring(tmp_path):
    """Regex từng bắt nhầm docstring mô tả mẫu cấm. AST thì không."""
    code = '"""Cấm gọi dv.ptp() — dùng np.ptp(dv)."""\nS = "x.ptp()"\n'
    p = _write(tmp_path, code)
    assert chk.find_violations(p) == []


def test_file_khong_doc_duoc_thi_coi_la_KHONG_sach(tmp_path):
    """fail-closed: không xác minh được ≠ sạch."""
    p = tmp_path / "bin.py"
    p.write_bytes(b"\xff\xfe\x00\x01 import numpy")
    v = chk.find_violations(p)
    assert v and v[0][0] == -1


def test_repo_hien_tai_khong_con_vi_pham():
    """Quét toàn bộ src — sau khi vá 2 site ở gold_paper_ledger, phải sạch."""
    bad: list[str] = []
    for f in (PROJECT_ROOT / "backend" / "src").rglob("*.py"):
        for line, msg in chk.find_violations(f):
            bad.append(f"{f}:{line} {msg}")
    assert not bad, "còn API NumPy 1.x:\n  " + "\n  ".join(bad)


def test_skip_thu_muc_khong_lien_quan():
    assert chk.should_skip(Path("backend/.venv/Lib/site-packages/x.py"))
    assert chk.should_skip(Path("backend/src/__pycache__/x.py"))
    assert not chk.should_skip(Path("backend/src/engine/rs_ranker.py"))
