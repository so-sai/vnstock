"""Tính liên tục phiên giao dịch — chốt chặn "tính biến động qua ngày khuyết".

[WHY]
    Ngày 04/11/2026 hệ thống **không chạy EOD** (cron miss), `daily_ohlcv` không có bar
    04/11. Sáng 05/11, `eod-run --date 2026-11-05` lúc 15:42 vẫn hạch toán và ghi
    `paper_equity_curve` 05/11 trong khi phiên liền trước (04/11) chưa hề tồn tại; phải
    tới 17:33 catch-up mới bù 04/11 (`CATCHUP_FULL`) và **ghi lại** equity 05/11. Bất kỳ
    phép so sánh "hôm nay vs hôm qua" nào trong khoảng 15:42–17:33 đều đang so 05/11 với
    03/11 — tức **gộp 2 phiên thành 1**, đúng cái bẫy sinh ra con số "+41 điểm ảo".

[ROOT CAUSE]
    Không có hàm nào ở tầng Python trả lời câu hỏi "phiên liền trước của ngày X là ngày
    nào, và nó có thật trong `daily_ohlcv` không". Code Rust (`src/main.rs`) đã có
    `is_vn_trading_day` / `missing_trading_days_between` / `expected_prev_trading_day`,
    nhưng phía Python thì mỗi chỗ tự suy diễn bằng `date - 1 ngày` hoặc `shift(1)` —
    hoàn toàn mù với cuối tuần, ngày lễ và phantom holiday (2026-09-01).

[INVARIANT]
    (a) "Phiên liền trước" = ngày làm việc gần nhất TRƯỚC ngày X, KHÔNG phải `X - 1 ngày`;
    (b) ngày lễ (`VN_HOLIDAYS`) và phantom holiday (`NON_TRADING_SESSIONS`) không phải
        phiên ⇒ không bao giờ bị báo là "khuyết";
    (c) khuyết phiên liền trước ⇒ `SessionGapError` (fail-closed), cấm im lặng bỏ qua;
    (d) hàm thuần: không đọc/ghi CSDL, chỉ nhận tập ngày đã có ⇒ test được không cần DB.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from src.database.panel_quality import is_non_trading_session


class SessionGapError(RuntimeError):
    """[WHY] Khuyết phiên là lỗi DỮ LIỆU, không phải "ngày trống": nếu nuốt lỗi này,
    mọi phép so sánh liền kề (biến động 1 phiên, breadth velocity, ATR ratio, PnL)
    sẽ âm thầm gộp nhiều phiên thành một. — bắt buộc phải nổi lên tới caller.
    """


@lru_cache(maxsize=1)
def _holidays() -> frozenset[str]:
    """Ngày lễ VN — dùng CHUNG nguồn với `daily_updater.VN_HOLIDAYS` (không chép lại).

    [WHY] Chép tay danh sách lễ sang đây sẽ tạo hai nguồn sự thật: sửa một nơi, nơi kia
    thành sai, và triệu chứng là "khuyết phiên" giả vào đúng ngày lễ. — import lazy để
    không kéo cả pipeline cào dữ liệu vào module thuần.
    """
    from src.daily_updater import VN_HOLIDAYS

    return frozenset(VN_HOLIDAYS)


def is_session_day(iso: str) -> bool:
    """True nếu `iso` là một phiên giao dịch (thứ 2–6, không lễ, không phantom)."""
    d = date.fromisoformat(iso)
    if d.weekday() >= 5:
        return False
    if iso in _holidays():
        return False
    if is_non_trading_session(iso):
        return False
    return True


def previous_session(iso: str, max_lookback: int = 30) -> str | None:
    """Phiên giao dịch liền trước `iso` (KHÔNG phải `iso - 1 ngày`).

    Trả `None` nếu lùi quá `max_lookback` ngày lịch mà không tìm thấy phiên nào —
    trường hợp đó là bất thường lịch, không được đoán bừa.

    [WHY] `max_lookback=30`: kỳ nghỉ Tết dài nhất trong dữ liệu là 5 phiên; 30 ngày là
    biên an toàn rộng gấp 6 lần nhưng vẫn chặn được vòng lặp vô hạn khi lịch hỏng.
    """
    d = date.fromisoformat(iso)
    for i in range(1, max_lookback + 1):
        cand = (d - timedelta(days=i)).isoformat()
        if is_session_day(cand):
            return cand
    return None


def sessions_between(start_iso: str, end_iso: str) -> list[str]:
    """Danh sách phiên giao dịch trong khoảng `(start_iso, end_iso)` — hai đầu KHÔNG tính."""
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    if end < start:
        raise ValueError(f"khoảng ngày ngược: {start_iso} > {end_iso}")
    out: list[str] = []
    cur = start + timedelta(days=1)
    while cur < end:
        iso = cur.isoformat()
        if is_session_day(iso):
            out.append(iso)
        cur += timedelta(days=1)
    return out


def find_missing_sessions(present: set[str] | list[str], start_iso: str, end_iso: str) -> list[str]:
    """Các phiên giao dịch bị khuyết trong `(start_iso, end_iso)` so với tập `present`."""
    present_set = set(present)
    return [d for d in sessions_between(start_iso, end_iso) if d not in present_set]


def assert_previous_session_present(as_of_iso: str, present: set[str] | list[str]) -> str:
    """Bảo đảm phiên liền trước `as_of_iso` CÓ mặt; trả về chính ngày đó.

    Ném `SessionGapError` nếu khuyết — kèm danh sách phiên khuyết gần nhất để người đọc
    log biết phải backfill ngày nào (không chỉ báo "thiếu").
    """
    prev = previous_session(as_of_iso)
    if prev is None:
        raise SessionGapError(f"Không xác định được phiên liền trước của {as_of_iso} trong 30 ngày — lịch phiên bất thường.")
    present_set = set(present)
    if prev in present_set:
        return prev
    # [WHY]: Liệt kê TOÀN BỘ phiên khuyết trong 10 phiên gần nhất thay vì chỉ 1 ngày —
    # sự cố thật (04/11) đi kèm việc cron miss nhiều ngày; chỉ báo 1 ngày sẽ khiến người
    # trực backfill từng ngày một rồi chạy lại nhiều lần. — cấm rút gọn thành 1 dòng.
    recent = [d for d in sessions_between(_shift(prev, -10), prev)]
    missing = [d for d in recent if d not in present_set] + [prev]
    missing = sorted(set(missing))
    raise SessionGapError(
        f"KHUYẾT PHIÊN LIỀN TRƯỚC: {as_of_iso} cần phiên {prev} nhưng `daily_ohlcv` không có bar. "
        f"Phiên khuyết gần nhất: {missing}. "
        f"KHÔNG hạch toán tiếp — hãy backfill {missing} trước (mọi so sánh liền kề sẽ gộp "
        f"nhiều phiên thành một và tạo biến động giả)."
    )


def _shift(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


# =====================================================================
# CHỐT #2 (Mục 27, NQ-17 §IV — PHƯƠNG ÁN B): CHỈ BÁO ĐỘNG, KHÔNG TỰ BÙ
# =====================================================================

# [WHY] Ngưỡng "phiên có thật": đo trên `screener_cache.db` (06/11/2026) có 1.666 phiên
# ≥ 100 mã hợp lệ trên tổng ~890 mã đang giao dịch mỗi phiên. Một phiên chỉ còn < 100 bar
# KHÔNG phải "phiên thưa" mà là ingest chết giữa đường — coi nó là "đã có" chính là Fake
# (AGENTS §1: missing > fake). — hạ ngưỡng này sẽ làm cổng mù với đúng ca 04/11.
MIN_VALID_BARS_PER_SESSION = 100


def detect_session_gaps(
    conn,
    as_of_iso: str,
    lookback_sessions: int = 60,
) -> dict:
    """Quét lỗ hổng phiên trong `lookback_sessions` phiên gần nhất. **KHÔNG backfill.**

    [WHY] NQ-17 §IV chọn phương án (B): `daily-update` chỉ được BÁO ĐỘNG rồi để
    `eod-run` fail-closed (exit 4). Tự động gọi backfill mạng trong `daily-update` sẽ
    (i) làm lệch hạn bản tin ATC 15:30 vì kéo nến ~900 mã, (ii) che giấu lỗi suy thoái
    vận hành (mất một phiên là SỰ CỐ, không phải chuyện thường để máy tự vá), (iii) đụng
    giới hạn NQ-16 về corporate actions. — cấm thêm bất kỳ lời gọi backfill vào hàm này.

    [INVARIANT] Phân loại RẠCH RÒI bốn trạng thái (AGENTS §2), không gộp thành một con số
    "mã thiếu" mờ nghĩa:
      * `missing`      — phiên KHÔNG có một bar hợp lệ nào (dữ liệu chưa về);
      * `partial`      — có bar nhưng < `MIN_VALID_BARS_PER_SESSION` (ingest chết giữa đường);
      * `stale_only`   — có dòng nhưng TẤT CẢ `is_stale = 1` (nguồn trả dữ liệu cũ);
      * `ok`           — đủ phủ.
    Ngày nghỉ (cuối tuần / `VN_HOLIDAYS` / phantom holiday) KHÔNG nằm trong cửa sổ nên
    không bao giờ bị báo "khuyết" — đó là phân biệt "không giao dịch" ≠ "thiếu dữ liệu".

    [INVARIANT] Hàm này KHÔNG ghi gì vào CSDL và KHÔNG gọi mạng. Nếu bản thân nó lỗi,
    caller phải ghi `None` + lỗi (không được trả 0 giả — AGENTS §4A).

    Returns:
        {"as_of", "window_start", "checked_sessions", "missing", "partial",
         "stale_only", "has_gap"}
    """
    as_of = as_of_iso
    if not is_session_day(as_of):
        prev = previous_session(as_of)
        if prev is None:
            raise ValueError(f"không xác định được phiên gần nhất ≤ {as_of_iso}")
        as_of = prev

    window: list[str] = [as_of]
    cur = as_of
    while len(window) < lookback_sessions:
        prev = previous_session(cur)
        if prev is None:
            break
        window.append(prev)
        cur = prev
    window.reverse()
    window_start = window[0]

    rows = conn.execute(
        "SELECT date, COUNT(*), SUM(CASE WHEN COALESCE(is_stale, 0) = 0 THEN 1 ELSE 0 END) "
        "FROM daily_ohlcv WHERE date >= ? AND date <= ? GROUP BY date",
        (window_start, as_of),
    )
    counts: dict[str, tuple[int, int]] = {}
    for d, total, fresh in rows:
        counts[str(d)] = (int(total or 0), int(fresh or 0))

    missing: list[str] = []
    partial: list[str] = []
    stale_only: list[str] = []
    for d in window:
        total, fresh = counts.get(d, (0, 0))
        if fresh >= MIN_VALID_BARS_PER_SESSION:
            continue
        if fresh > 0:
            partial.append(d)
        elif total > 0:
            stale_only.append(d)
        else:
            missing.append(d)

    return {
        "as_of": as_of,
        "window_start": window_start,
        "checked_sessions": len(window),
        "missing": missing,
        "partial": partial,
        "stale_only": stale_only,
        "has_gap": bool(missing or partial or stale_only),
    }
