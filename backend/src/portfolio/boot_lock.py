"""Re-assert ENTRY_LOCK tại CỔNG KHỞI ĐỘNG (boot-time enforcement) — chống lỡ cron 07:00.

[WHY]
  2026-10-20 đo được: cron là **in-process của Hermes Desktop** ⇒ app đóng lúc 07:00 thì job bị bỏ qua
  và chỉ chạy bù lúc mở app (hôm đó: `ptck-assert-lock-0700` hẹn 07:00, chạy thật **11:43** — sau ATO
  09:00). Lớp phủ "re-assert mỗi sáng" vì thế chỉ đúng khi app mở từ 07:00. Không được dựa vào may mắn:
  bất kỳ lần chạm hệ thống nào (CLI, pipeline EOD) cũng phải siết lại khoá nếu regime vẫn CRISIS.

[ROOT CAUSE]
  Cơ chế khoá phụ thuộc vào MỘT sự kiện thời gian (cron). Thiếu một điểm chạm đồng bộ theo *sự kiện
  khởi động* (boot) nên khi sự kiện thời gian bị lỡ thì không có gì bù.

[INVARIANT]
  1. CHỈ KHOÁ, KHÔNG BAO GIỜ MỞ. Hàm này không gọi `unlock()` trong bất kỳ nhánh nào — regime không
     CRISIS thì chỉ no-op (việc mở khoá thuộc `stale_manager`, không thuộc cổng khởi động).
  2. Đã khoá ⇒ no-op, KHÔNG ghi đè `last_lock_reason` (giữ vết kiểm toán của lần khoá thật).
  3. Không đọc được regime (bảng/cột thiếu, rỗng, ngày hỏng) ⇒ **khoá** với lý do riêng
     (`BOOT_REGIME_UNKNOWN_FAIL_CLOSED`). "Không biết" không được coi là "lành".
  4. Regime đã cũ hơn `MAX_REGIME_AGE_DAYS` ⇒ khoá với lý do `BOOT_REGIME_STALE_FAIL_CLOSED`
     (dữ liệu quá hạn cũng là "không biết").
"""

from __future__ import annotations

from datetime import date

from . import system_state

BOOT_CRISIS_REASON = "BOOT_GOVERNOR_CRISIS_AUTO_ENFORCE"
BOOT_UNKNOWN_REASON = "BOOT_REGIME_UNKNOWN_FAIL_CLOSED"
BOOT_STALE_REASON = "BOOT_REGIME_STALE_FAIL_CLOSED"
CRISIS = "CRISIS"
MAX_REGIME_AGE_DAYS = 5

_ACT_LOCKED_CRISIS = "LOCKED_CRISIS"
_ACT_LOCKED_UNKNOWN = "LOCKED_UNKNOWN"
_ACT_LOCKED_STALE = "LOCKED_STALE"
_ACT_ALREADY = "NOOP_ALREADY_LOCKED"


def latest_regime(conn) -> tuple[str | None, str | None, str | None]:
    """→ (date, status, lỗi). `lỗi` khác None nghĩa là KHÔNG đo được (caller phải fail-closed)."""
    try:
        # [WHY 2026-10-22] Đọc TRÊN ĐƯỜNG TÍN HIỆU ⇒ phải loại phiên degraded (hợp đồng 2 tầng của
        # `breadth_consumers_audit.md`): một phiên degraded có breadth/score không đáng tin, dùng nó để
        # quyết định khoá vốn là ăn dữ liệu xấu. Nếu mọi dòng đều degraded ⇒ rỗng ⇒ fail-closed (khoá).
        row = conn.execute(
            "SELECT date, status FROM regime_history WHERE COALESCE(is_degraded, 0) = 0 ORDER BY date DESC LIMIT 1"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 - bảng/cột thiếu, DB hỏng
        return None, None, f"regime_history không đọc được ({type(exc).__name__})"
    if row is None or not row[0] or not row[1]:
        return None, None, "regime_history rỗng hoặc thiếu date/status"
    return str(row[0]), str(row[1]).upper(), None


def _ensure_locked(reason: str) -> bool:
    """Khoá nếu CHƯA khoá. Trả True nếu có ghi. Không bao giờ mở, không ghi đè lý do đã có."""
    if system_state.get_state().get("portfolio_critical_lock") is True:
        return False
    system_state.lock(reason=reason)
    return True


def enforce_boot_lock(conn, today: str | None = None) -> str:
    """Siết lại ENTRY_LOCK theo regime đã biết. Trả mã hành động để log/kiểm toán."""
    asof = today or date.today().isoformat()

    reg_date, status, err = latest_regime(conn)
    if err is not None or not reg_date:
        return _ACT_LOCKED_UNKNOWN if _ensure_locked(BOOT_UNKNOWN_REASON) else _ACT_ALREADY

    try:
        age_days = (date.fromisoformat(asof) - date.fromisoformat(reg_date)).days
    except ValueError:
        return _ACT_LOCKED_UNKNOWN if _ensure_locked(BOOT_UNKNOWN_REASON) else _ACT_ALREADY

    if status == CRISIS:
        return _ACT_LOCKED_CRISIS if _ensure_locked(BOOT_CRISIS_REASON) else _ACT_ALREADY
    if age_days > MAX_REGIME_AGE_DAYS:
        return _ACT_LOCKED_STALE if _ensure_locked(BOOT_STALE_REASON) else _ACT_ALREADY
    return f"NOOP_{status or 'UNKNOWN'}"
