"""Test `system_state.lock(reason=...)` — re-assert khoá vốn có ghi vết (Phương án b).

[WHY]
  2026-10-19: file `system_state.json` ghi `portfolio_critical_lock = False` trong khi giả định vận hành
  là ENTRY_LOCK = TRUE. Nguyên nhân cấu trúc: `stale_manager` gọi `_unlock_state()` khi stale% dưới
  ngưỡng (dòng 154-156) ⇒ "hết kẹt vốn" tự mở khoá. Chính sách 100% tiền mặt cần một **lớp phủ**:
  mỗi sáng trước ATO, re-assert khoá. Để lớp phủ có thể kiểm toán, lần khoá phải ghi LÝ DO.

[ROOT CAUSE]
  `lock(total_stale_pct: float)` không có tham số lý do ⇒ không phân biệt được "khoá do kẹt vốn" với
  "khoá do chính sách", và snippet vận hành `lock(reason=...)` sẽ TypeError.

[INVARIANT]
  1. `lock()` gọi được KHÔNG tham số (tương thích ngược với callers cũ) và vẫn đặt cờ True.
  2. `lock(reason="X")` ghi `last_lock_reason = "X"` để audit.
  3. Thiếu/hỏng file ⇒ `is_locked()` = True (fail-closed) — không thay đổi.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.portfolio import system_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(system_state, "_STATE_PATH", tmp_path / "system_state.json")


# [WHY] Tương thích ngược: mọi caller hiện tại gọi `lock(pct)` hoặc `lock(0.0)`; thêm tham số không được
# làm vỡ chúng (đây là lớp bảo vệ an toàn vốn, không được phép TypeError lúc chạy cron).
def test_lock_khong_tham_so_van_hoat_dong():
    system_state.lock(0.0)
    assert system_state.is_locked() is True


# [WHY] Snippet vận hành của chủ hệ thống là `lock(reason='DAILY_GOVERNOR_CRISIS_OVERLAY')`; nếu signature
# không nhận `reason` thì job 07:00 sẽ chết âm thầm mỗi sáng — đúng kiểu lỗi im lặng cần chặn.
def test_lock_nhan_reason_va_ghi_vet():
    system_state.lock(reason="DAILY_GOVERNOR_CRISIS_OVERLAY")
    st = system_state.get_state()
    assert st["portfolio_critical_lock"] is True
    assert st["last_lock_reason"] == "DAILY_GOVERNOR_CRISIS_OVERLAY"


# [WHY] Unlock vẫn phải mở được (đường tự phục hồi của stale manager), nhưng KHÔNG được xoá vết lý do
# khoá gần nhất — người kiểm toán cần biết vì sao nó từng bị khoá.
def test_unlock_mo_duoc_va_giu_vet_ly_do():
    system_state.lock(reason="DAILY_GOVERNOR_CRISIS_OVERLAY")
    system_state.unlock()
    st = system_state.get_state()
    assert st["portfolio_critical_lock"] is False
    assert st["last_lock_reason"] == "DAILY_GOVERNOR_CRISIS_OVERLAY"


# [WHY] Fail-closed bất biến: file không tồn tại ⇒ coi như ĐANG KHOÁ. Đây là dòng cuối cùng bảo vệ
# tiền của hệ thống khi state hỏng.
def test_file_thieu_thi_coi_nhu_dang_khoa(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(system_state, "_STATE_PATH", tmp_path / "khong_ton_tai.json")
    assert system_state.is_locked() is True
