"""Test `StalePositionManager.status()["critical_lock"]` phải phản ánh ENTRY_LOCK BỀN.

[WHY]
  AGENTS.md quy định: "An toàn vốn: `ENTRY_LOCK` = `system_state.json:portfolio_critical_lock` ...
  Kiểm bằng `ptck.py stale status`". Nhưng đo 2026-10-19: file ghi `portfolio_critical_lock = True`
  sau khi gọi `system_state.lock()`, còn `ptck.py stale status` vẫn in **`Critical Lock: False`** ⇒
  công cụ kiểm tra nói dối, tệ hơn không có công cụ: người vận hành tưởng cửa đã mở.

[ROOT CAUSE]
  `stale_manager.status()` trả `"critical_lock": self.hard_shutdown` — tức gán nhãn "critical lock"
  cho một biến RUNTIME của riêng manager (`hard_shutdown`, tính từ các lớp stale trong phiên), không
  đọc cờ bền `system_state.is_locked()`. Hai khái niệm khác nhau bị trộn vào một khoá.

[INVARIANT]
  1. `status()["critical_lock"]` == `system_state.is_locked()` (nguồn sự thật duy nhất là file state).
  2. `hard_shutdown` giữ nguyên nghĩa riêng và vẫn được trả về độc lập.
  3. Khi state file thiếu/hỏng ⇒ fail-closed: `critical_lock` phải là **True**.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.portfolio import system_state
from src.portfolio.stale_manager import StalePositionManager


@pytest.fixture()
def state_file(tmp_path: Path, monkeypatch):
    p = tmp_path / "system_state.json"
    monkeypatch.setattr(system_state, "_STATE_PATH", p)
    return p


def _fake_manager(hard_shutdown: bool) -> SimpleNamespace:
    """Duck-typed 'self' đủ cho status(): tránh phải dựng DB/ledger thật."""
    return SimpleNamespace(
        _layers=[],
        stale_pcts=[],
        total_stale_pct=0.0,
        escrow_balance=0.0,
        ptd_frozen_surplus=0.0,
        ptd_releasable=0.0,
        hard_shutdown=hard_shutdown,
    )


# [WHY] Ca thật đã xảy ra 2026-10-19: ENTRY_LOCK = True nhưng CLI in False. Test chốt: lock BẬT mà
# hard_shutdown TẮT ⇒ critical_lock phải là True (hai biến độc lập, không được gán bằng nhau).
def test_critical_lock_doc_tu_state_file_khong_phai_hard_shutdown(state_file):
    system_state.lock(0.0)
    out = StalePositionManager.status(_fake_manager(hard_shutdown=False))
    assert out["critical_lock"] is True, "CLI báo lock đã mở trong khi ENTRY_LOCK = True"
    assert out["hard_shutdown"] is False, "hard_shutdown phải giữ nghĩa riêng"


# [WHY] Chiều ngược lại: hard_shutdown BẬT nhưng ENTRY_LOCK TẮT ⇒ critical_lock = False. Nếu gán
# critical_lock = hard_shutdown thì ca này "đúng" một cách tình cờ — test này buộc hai biến tách nhau.
def test_hard_shutdown_bat_khong_lam_critical_lock_bat(state_file):
    system_state.unlock()
    out = StalePositionManager.status(_fake_manager(hard_shutdown=True))
    assert out["critical_lock"] is False
    assert out["hard_shutdown"] is True


# [WHY] Luật fail-closed: state file thiếu/hỏng ⇒ KHÓA. Đây là bất biến an toàn vốn quan trọng nhất,
# không được để status() báo "mở" chỉ vì file không đọc được.
def test_state_file_thieu_thi_fail_closed(state_file):
    assert not state_file.exists()
    out = StalePositionManager.status(_fake_manager(hard_shutdown=False))
    assert out["critical_lock"] is True
