"""preregistration.py — khóa niêm phong spec pre-registered.

MỤC ĐÍCH
--------
Spec `SPEC_TREND_ENGINE_2021_2025.md` là một **pre-registration**: nó chỉ có giá trị
nếu chứng minh được rằng nó KHÔNG bị sửa sau khi nhìn thấy kết quả backtest. Lời hứa
không kiểm chứng được thì vô nghĩa — hạ ngưỡng Sharpe từ 0.5 xuống 0.45 là hành vi
không thể phát hiện bằng mắt.

Module này là **một nguồn sự thật duy nhất** cho việc băm/đối chiếu, dùng bởi:
  - `backend/tests/test_preregistration_hash.py` (chặn trong CI/pre-commit)
  - `backend/src/backtest/backtest_trend_2021_2025.py` (chặn ngay trước khi chạy)

ĐỊNH NGHĨA HASH: sha256 trên nội dung đã CHUẨN HOÁ LF. Repo bật `core.autocrlf=true`
nên checkout trên Windows đổi LF→CRLF trong working tree; băm thô sẽ lệch giữa các
máy dù nội dung y hệt — và một guard đỏ giả là guard sẽ bị người ta tắt.

GIỚI HẠN ĐÃ BIẾT: guard này chứng minh **tính toàn vẹn** (file == anchor), KHÔNG
chứng minh **tính đầy đủ** (file còn đủ mục). Xem `test_spec_structure.py`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DOCS = PROJECT_ROOT / "docs"
SPEC_PATH = DOCS / "SPEC_TREND_ENGINE_2021_2025.md"
SIDECAR_PATH = DOCS / "SPEC_TREND_ENGINE_2021_2025.sha256"

_ANCHOR_RE = re.compile(r"^([0-9a-f]{64})\s+(.+)$")


class PreRegistrationTamperError(RuntimeError):
    """Spec pre-registered đã bị sửa mà anchor không được cập nhật có ý thức."""


def sha256_lf_normalized(path: Path) -> str:
    """sha256 trên nội dung đã đổi CRLF → LF (bền với checkout)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_anchors(sidecar: Path | None = None) -> dict[str, str]:
    """Đọc sidecar → {đường dẫn tương đối: sha256}. Bỏ qua dòng comment/rỗng."""
    sidecar = sidecar or SIDECAR_PATH
    out: dict[str, str] = {}
    for line in sidecar.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = _ANCHOR_RE.match(line)
        if m:
            out[m.group(2).strip()] = m.group(1)
    return out


def check_anchors() -> list[str]:
    """Trả về danh sách mô tả sai lệch (rỗng = tất cả khớp)."""
    if not SIDECAR_PATH.exists():
        return [f"thiếu file anchor: {SIDECAR_PATH}"]
    anchors = read_anchors()
    if not anchors:
        return [f"sidecar không có dòng hash hợp lệ: {SIDECAR_PATH}"]

    problems: list[str] = []
    for rel, expected in anchors.items():
        target = PROJECT_ROOT / rel
        if not target.exists():
            problems.append(f"{rel}: anchor trỏ tới file KHÔNG TỒN TẠI")
            continue
        actual = sha256_lf_normalized(target)
        if actual != expected:
            problems.append(f"{rel}\n     anchor  : {expected}\n     hiện tại: {actual}")
    return problems


def verify_preregistration_lock(raise_on_fail: bool = True) -> dict:
    """Xác nhận spec còn nguyên vẹn so với pre-registration.

    raise_on_fail=True (mặc định) ⇒ ném PreRegistrationTamperError nếu lệch.
    Trả về dict tóm tắt để in log/report.
    """
    problems = check_anchors()
    info = {
        "ok": not problems,
        "spec": str(SPEC_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "anchor_file": str(SIDECAR_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "sha256": sha256_lf_normalized(SPEC_PATH) if SPEC_PATH.exists() else None,
        "expected": read_anchors().get(str(SPEC_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/")),
        "problems": problems,
    }
    if problems and raise_on_fail:
        raise PreRegistrationTamperError(
            "Spec pre-registered ĐÃ BỊ SỬA mà anchor không cập nhật.\n  "
            + "\n  ".join(problems)
            + "\n\nNếu sửa là CHỦ ĐÍCH: commit lại spec + cập nhật anchor (việc đó sẽ hiện trong git "
            "history để kiểm toán). Nếu KHÔNG chủ đích: khôi phục spec về bản đã niêm phong."
        )
    return info


__all__ = [
    "PROJECT_ROOT",
    "SPEC_PATH",
    "SIDECAR_PATH",
    "PreRegistrationTamperError",
    "sha256_lf_normalized",
    "read_anchors",
    "check_anchors",
    "verify_preregistration_lock",
]
