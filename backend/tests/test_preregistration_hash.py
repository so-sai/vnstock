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

import hashlib
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = PROJECT_ROOT / "docs"


def _sha256_lf_normalized(path: Path) -> str:
    """sha256 trên nội dung đã đổi CRLF → LF (bền với checkout)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _anchors(sidecar: Path) -> dict[str, str]:
    """Đọc sidecar: {đường dẫn tương đối: sha256}."""
    out: dict[str, str] = {}
    for line in sidecar.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([0-9a-f]{64})\s+(.+)$", line)
        if m:
            out[m.group(2).strip()] = m.group(1)
    return out


def test_sidecar_ton_tai_va_co_anchor():
    sidecar = SPEC_DIR / "SPEC_TREND_ENGINE_2021_2025.sha256"
    assert sidecar.exists(), "thiếu file anchor pre-registration"
    anchors = _anchors(sidecar)
    assert anchors, "sidecar không có dòng hash hợp lệ"
    for rel in anchors:
        assert (PROJECT_ROOT / rel).exists(), f"anchor trỏ tới file không tồn tại: {rel}"


def test_invariant_11_spec_khong_bi_sua_sau_khi_pre_register():
    """Invariant #11: hash của spec pre-registered phải KHỚP anchor đã commit.

    Nếu test này đỏ: spec đã bị sửa sau khi đăng ký trước. Nếu việc sửa là HỢP LỆ
    (ví dụ chốt mục 15 TRƯỚC khi chạy backtest), thì commit lại + cập nhật hash
    trong sidecar — sự thay đổi đó sẽ nằm trong git history để kiểm toán.
    """
    sidecar = SPEC_DIR / "SPEC_TREND_ENGINE_2021_2025.sha256"
    anchors = _anchors(sidecar)
    mismatches = []
    for rel, expected in anchors.items():
        actual = _sha256_lf_normalized(PROJECT_ROOT / rel)
        if actual != expected:
            mismatches.append(f"{rel}\n   anchor : {expected}\n   hiện tại: {actual}")
    assert not mismatches, "spec pre-registered ĐÃ BỊ SỬA mà không cập nhật anchor:\n  " + "\n  ".join(mismatches)


def test_hash_ben_vong_line_ending():
    """Chuẩn hoá LF phải cho cùng hash với CRLF — nếu không, test sẽ đỏ giả trên máy khác."""
    lf = b"line1\nline2\n"
    crlf = b"line1\r\nline2\r\n"
    assert hashlib.sha256(lf).hexdigest() == hashlib.sha256(crlf.replace(b"\r\n", b"\n")).hexdigest()
