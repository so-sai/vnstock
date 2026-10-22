"""Test cổng khoá tại boot (`enforce_boot_lock`) — chống lỡ cron 07:00.

[WHY]
  Cron của Hermes Desktop là in-process: app đóng lúc 07:00 ⇒ job chạy bù lúc mở app (đo 2026-10-20:
  hẹn 07:00, chạy 11:43 — sau ATO 09:00). Cổng boot là lớp bù cuối: mọi lần chạm hệ thống phải siết lại
  khoá nếu regime vẫn CRISIS, và TUYỆT ĐỐI không được mở khoá.

[INVARIANT được canh ở đây]
  - CRISIS ⇒ khoá (reason BOOT_GOVERNOR_CRISIS_AUTO_ENFORCE).
  - Đã khoá ⇒ no-op, KHÔNG ghi đè lý do cũ (giữ vết kiểm toán).
  - Không CRISIS ⇒ không khoá, KHÔNG mở (kể cả khi đang mở).
  - Không đo được regime / regime quá cũ ⇒ khoá (fail-closed), mỗi trường hợp một lý do riêng.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from src.portfolio import boot_lock as bl
from src.portfolio import system_state


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch):
    """State riêng + **bắt đầu từ cửa MỞ** (file ghi False).

    [WHY] `system_state._read()` mặc định `portfolio_critical_lock = True` khi file thiếu (fail-closed),
    nên nếu để file vắng thì mọi ca đều rơi vào NOOP_ALREADY_LOCKED và không ca nào chạm được nhánh khoá.
    Trạng thái nguy hiểm thật cần canh là: file TỒN TẠI và ghi False (ai đó đã unlock) — đó là lúc cổng
    boot phải siết lại.
    """
    monkeypatch.setattr(system_state, "_STATE_PATH", tmp_path / "system_state.json")
    system_state.unlock()


def make_conn(rows=(), drop_table=False) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    if not drop_table:
        c.execute("CREATE TABLE regime_history (date TEXT, regime_score REAL, status TEXT, is_degraded INTEGER)")
        for d, s in rows:
            c.execute("INSERT INTO regime_history (date, regime_score, status, is_degraded) VALUES (?, 0.2, ?, 0)", (d, s))
        c.commit()
    return c


def state() -> dict:
    return system_state.get_state()


# [WHY] Ca chính: regime CRISIS ⇒ khoá ngay tại boot, có lý do riêng để phân biệt với khoá dữ liệu.
def test_crisis_thi_khoa_ngay():
    c = make_conn([("2026-10-19", "CRISIS")])
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "LOCKED_CRISIS"
    st = state()
    assert st["portfolio_critical_lock"] is True
    assert st["last_lock_reason"] == bl.BOOT_CRISIS_REASON


# [WHY] Đã khoá sẵn (vd từ cron 07:00 hoặc hôm qua) ⇒ no-op và GIỮ NGUYÊN lý do cũ: cổng boot không
# được ghi đè vết kiểm toán của lần khoá thật (nếu không, audit mất dấu cơ chế nào đã khoá).
def test_da_khoa_thi_khong_ghi_de_ly_do():
    system_state.lock(reason="DAILY_GOVERNOR_CRISIS_OVERLAY")
    before = state()["last_updated"]
    c = make_conn([("2026-10-19", "CRISIS")])
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "NOOP_ALREADY_LOCKED"
    st = state()
    assert st["last_lock_reason"] == "DAILY_GOVERNOR_CRISIS_OVERLAY"
    assert st["last_updated"] == before


# [WHY] Bất biến an toàn nhất: cổng boot KHÔNG BAO GIỜ mở khoá. Regime lành mà khoá đang mở thì phải
# giữ nguyên mở (việc mở khoá thuộc stale_manager, không thuộc boot) — nếu test này đỏ, boot thành cửa hậu.
def test_regime_lanh_khong_tu_mo_khoa():
    system_state.unlock()
    c = make_conn([("2026-10-19", "TRENDING")])
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "NOOP_TRENDING"
    assert state()["portfolio_critical_lock"] is False


# [WHY] Regime lành nhưng khoá đang ĐÓNG (do kẹt dữ liệu) ⇒ giữ nguyên đóng; không nhánh nào được mở.
def test_regime_lanh_giu_nguyen_khoa_dang_co():
    system_state.lock(reason="STALE_DATA_LOCK")
    c = make_conn([("2026-10-19", "RANGING")])
    assert bl.enforce_boot_lock(c, today="2026-10-20").startswith("NOOP")
    assert state()["portfolio_critical_lock"] is True


# [WHY] "Không biết ≠ lành" (Luật A): bảng rỗng ⇒ KHOÁ với lý do UNKNOWN, không được coi là ổn.
def test_bang_rong_thi_khoa_fail_closed():
    c = make_conn()
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "LOCKED_UNKNOWN"
    assert state()["last_lock_reason"] == bl.BOOT_UNKNOWN_REASON


# [WHY] Bảng/cột thiếu (DB chưa migrate hoặc mở sai file) ⇒ cũng là "không biết" ⇒ khoá.
def test_bang_khong_ton_tai_thi_khoa_fail_closed():
    c = make_conn(drop_table=True)
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "LOCKED_UNKNOWN"
    assert state()["portfolio_critical_lock"] is True


# [WHY] Regime quá cũ (đo được nhưng hết hạn) ⇒ khoá: dữ liệu cũ cũng là "không biết". Chọn ngưỡng 5
# ngày vì regime_history đang tụt lại cuối tuần (nợ §9) — quá 5 ngày thì KHÔNG được tin.
def test_regime_qua_cu_thi_khoa_fail_closed():
    c = make_conn([("2026-10-01", "RANGING")])
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "LOCKED_STALE"
    assert state()["last_lock_reason"] == bl.BOOT_STALE_REASON


# [WHY] Ngày hỏng (chuỗi rác trong date) không được làm nổ cổng boot; phải quy về UNKNOWN ⇒ khoá.
def test_ngay_regime_hong_thi_khoa_fail_closed():
    c = make_conn([("not-a-date", "CRISIS")])
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "LOCKED_UNKNOWN"


# [WHY] File state KHÔNG tồn tại (máy mới, file bị xoá): `is_locked()` fail-closed = True ⇒ cổng boot phải
# nhận ra "đã khoá" và không ghi bừa; đồng thời không được nổ. Đây là ca dễ gây hiểu nhầm nhất.
def test_thieu_file_state_thi_coi_nhu_da_khoa(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(system_state, "_STATE_PATH", tmp_path / "khong_ton_tai.json")
    c = make_conn([("2026-10-19", "CRISIS")])
    assert bl.enforce_boot_lock(c, today="2026-10-20") == "NOOP_ALREADY_LOCKED"
    assert system_state.is_locked() is True
    assert not (tmp_path / "khong_ton_tai.json").exists()  # không ghi file chỉ vì đã fail-closed


# [WHY] Phiên degraded KHÔNG được dùng để quyết định khoá vốn: nếu dòng mới nhất là degraded (breadth
# không đáng tin) thì cổng phải lùi về dòng sạch gần nhất, không lấy bừa dòng mới nhất.
def test_bo_qua_phien_degraded():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE regime_history (date TEXT, regime_score REAL, status TEXT, is_degraded INTEGER)")
    c.execute("INSERT INTO regime_history VALUES ('2026-10-20', 0.3, 'CRISIS', 0)")
    c.execute("INSERT INTO regime_history VALUES ('2026-10-21', 0.6, 'RANGING', 1)")  # degraded, mới hơn
    c.commit()
    assert bl.latest_regime(c)[:2] == ("2026-10-20", "CRISIS")


# [WHY] Toàn bộ dòng đều degraded ⇒ KHÔNG có mốc tin được ⇒ fail-closed (khoá), không đọc dòng bẩn.
def test_toan_bo_degraded_thi_fail_closed():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE regime_history (date TEXT, regime_score REAL, status TEXT, is_degraded INTEGER)")
    c.execute("INSERT INTO regime_history VALUES ('2026-10-21', 0.6, 'RANGING', 1)")
    c.commit()
    assert bl.enforce_boot_lock(c, today="2026-10-22") == "LOCKED_UNKNOWN"
    assert state()["last_lock_reason"] == bl.BOOT_UNKNOWN_REASON


# [WHY] Biên đúng ngưỡng: 5 ngày vẫn tin, 6 ngày thì không — chốt lại ngưỡng để không ai nới lặng lẽ.
def test_bien_nguong_5_ngay():
    assert bl.enforce_boot_lock(make_conn([("2026-10-15", "RANGING")]), today="2026-10-20").startswith("NOOP")
    system_state.unlock()
    assert bl.enforce_boot_lock(make_conn([("2026-10-14", "RANGING")]), today="2026-10-20") == "LOCKED_STALE"
