"""test_preregistration_hash.py — THỰC THI invariant #11 (anti-data-snooping).

VÌ SAO CẦN
----------
Một spec pre-registered chỉ có giá trị nếu **chứng minh được nó không bị sửa sau
khi nhìn thấy kết quả**. Lời hứa không kiểm chứng được thì vô nghĩa: sửa một
ngưỡng trong spec (0.50 → 0.45) là hành vi không thể phát hiện bằng mắt.

Test này so sha256 của spec với anchor đã commit. Sửa spec dù chỉ một ký tự ⇒ test
ĐỎ ⇒ buộc phải commit lại + cập nhật hash một cách có ý thức (và như vậy để lại
dấu vết trong git history).

LƯU Ý LINE-ENDING (quan trọng)
------------------------------
Repo bật `core.autocrlf = true`: checkout trên Windows biến LF → CRLF trong working
tree, làm sha256 "thô" khác nhau giữa các máy dù nội dung y hệt. Vì vậy anchor được
định nghĩa trên **nội dung đã chuẩn hoá LF**, và test cũng chuẩn hoá trước khi băm.
Không làm vậy thì test sẽ đỏ giả trên máy khác — và một guard đỏ giả sẽ bị người ta
tắt đi, đúng thứ chúng ta không muốn.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "backend" / "src")]

# MỘT nguồn sự thật cho việc băm/đối chiếu — không cài lại logic ở đây.
from src.backtest.preregistration import (  # noqa: E402
    SIDECAR_PATH,
    check_anchors,
    read_anchors,
    sha256_lf_normalized,
    verify_preregistration_lock,
)

SPEC_DIR = PROJECT_ROOT / "docs"


def _anchors() -> dict[str, str]:
    return read_anchors()


def _sha256_lf_normalized(path: Path) -> str:
    return sha256_lf_normalized(path)


def test_sidecar_ton_tai_va_co_anchor():
    sidecar = SPEC_DIR / "SPEC_TREND_ENGINE_2021_2025.sha256"
    assert sidecar.exists(), "thiếu file anchor pre-registration"
    anchors = _anchors()
    assert anchors, "sidecar không có dòng hash hợp lệ"
    for rel in anchors:
        assert (PROJECT_ROOT / rel).exists(), f"anchor trỏ tới file không tồn tại: {rel}"


def test_invariant_11_spec_khong_bi_sua_sau_khi_pre_register():
    """Invariant #11: hash của spec pre-registered phải KHỚP anchor đã commit.

    Nếu test này đỏ: spec đã bị sửa sau khi đăng ký trước. Nếu việc sửa là HỢP LỆ
    (ví dụ chốt mục 15 TRƯỚC khi chạy backtest), thì commit lại + cập nhật hash
    trong sidecar — sự thay đổi đó sẽ nằm trong git history để kiểm toán.
    """
    problems = check_anchors()
    assert not problems, "spec pre-registered ĐÃ BỊ SỬA mà không cập nhật anchor:\n  " + "\n  ".join(problems)


def test_verify_lock_nem_loi_khi_bi_sua():
    """Đường dùng bởi runner: phải NÉM lỗi, không trả về True/False im lặng."""
    info = verify_preregistration_lock(raise_on_fail=False)
    assert info["ok"] is True
    assert info["sha256"] == info["expected"]


def test_hash_ben_vong_line_ending(tmp_path):
    """Chuẩn hoá LF phải cho cùng hash với CRLF — nếu không, test sẽ đỏ giả trên máy khác.

    Dùng chính hàm của module (một nguồn sự thật), không tự băm lại.
    """
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(b"line1\nline2\n")
    crlf.write_bytes(b"line1\r\nline2\r\n")
    assert sha256_lf_normalized(lf) == sha256_lf_normalized(crlf)
