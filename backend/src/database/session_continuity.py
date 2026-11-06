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


# =====================================================================
# CHỐT #3 (Mục 27, NQ-17 §II.1): BẤT BIẾN TẬP NGÀY — PHẠM VI HỢP ĐỒNG
# =====================================================================

# [WHY] Mốc paper trading ra đời (đo 06/11/2026: `paper_equity_curve` có dòng đầu tiên đúng
# 2026-06-15). Trước mốc này chưa có khái niệm "sổ cái tài sản" nên đòi equity/ledger khớp
# lịch là vô nghĩa — NQ-17 §II.1 chốt dùng epoch này thay vì "toàn lịch sử".
CONTRACT_EPOCH = "2026-06-15"

# [WHY] Cửa sổ thị trường 60 phiên: đủ dài để bắt lỗ hổng cron miss hiếm (04/11/2026 nằm
# trong 60 phiên gần nhất) mà không vướng khác biệt lịch sử có lý do: đo 06/11/2026 cho thấy
# `breadth \ regime` = 229 ngày 2020-01-02..2020-11-30 vì regime engine ra đời 12/2020 —
# fail-closed trên toàn lịch sử sẽ đỏ vĩnh viễn rồi bị xfail (mất tác dụng).
MARKET_WINDOW_SESSIONS = 60

# [WHY] Cửa sổ kế toán 20 phiên: vùng KHÔNG khoan nhượng. Sự cố 04/11/2026 làm CẢ equity và
# ledger cùng khuyết một phiên — nếu chỉ so equity với ledger (không so với lịch) thì
# "cùng khuyết" lại trông như "khớp". 20 phiên ~ 1 tháng, đủ để một sự cố bị phát hiện trong
# vòng một tháng, ngắn đủ để không kéo nợ kế thừa 2020 vào (đo 06/11/2026: lỗ hổng cuối cùng
# của ledger là 2026-10-02, tức ngoài 20 phiên gần nhất).
ACCOUNTING_WINDOW_SESSIONS = 20

MARKET_TABLES: tuple[str, ...] = ("daily_ohlcv", "market_wide_breadth_daily", "regime_history")

# Bảng kế toán → cột ngày (ledger dùng `as_of_date`, equity dùng `date`).
ACCOUNTING_TABLES: dict[str, str] = {
    "paper_equity_curve": "date",
    "eod_run_ledger": "as_of_date",
}


class SessionSetMismatchError(RuntimeError):
    """[WHY] Lệch tập ngày là lỗi DỮ LIỆU, không phải "khác nhau chút thì kệ": một phiên
    thiếu ở breadth/regime làm mọi consumer đọc "phiên gần nhất" lấy nhầm phiên cũ, và một
    phiên thiếu ở equity/ledger làm PnL/CAGR tính qua khoảng trống. — phải nổi lên caller.
    """


def session_window_end(as_of_iso: str, sessions: int) -> list[str]:
    """`sessions` phiên giao dịch gần nhất kết thúc tại `as_of_iso` (tăng dần)."""
    if sessions <= 0:
        raise ValueError(f"sessions phải > 0, nhận {sessions}")
    as_of = as_of_iso
    if not is_session_day(as_of):
        prev = previous_session(as_of)
        if prev is None:
            raise ValueError(f"không xác định được phiên gần nhất ≤ {as_of_iso}")
        as_of = prev
    out = [as_of]
    cur = as_of
    while len(out) < sessions:
        prev = previous_session(cur)
        if prev is None:
            raise ValueError(f"lịch phiên không đủ {sessions} phiên trước {as_of_iso}")
        out.append(prev)
        cur = prev
    out.reverse()
    return out


def _present_dates(conn, table: str, column: str, min_bars: int | None = None, since: str | None = None):
    """Tập ngày CÓ THẬT trong `table`.

    [WHY] `daily_ohlcv` phải lọc `is_stale = 0` và đếm bar: một phiên còn 40/900 bar là
    ingest chết giữa đường, không được tính là "có" (AGENTS §1 missing > fake). Các bảng
    dẫn xuất (`breadth`, `regime`, `equity`, `ledger`) mỗi ngày chỉ có một dòng nên chỉ cần
    `DISTINCT`.
    """
    where = []
    params: list = []
    if since:
        where.append(f"{column} >= ?")
        params.append(since)
    if min_bars is None:
        sql = f"SELECT DISTINCT {column} FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
    else:
        where.append("COALESCE(is_stale, 0) = 0")
        sql = f"SELECT {column} FROM {table} WHERE " + " AND ".join(where) + " GROUP BY " + column + " HAVING COUNT(*) >= ?"
        params.append(min_bars)
    return {str(r[0]) for r in conn.execute(sql, tuple(params))}


def collect_session_alignment(
    conn,
    as_of_iso: str | None = None,
    market_window: int = MARKET_WINDOW_SESSIONS,
    accounting_window: int | None = ACCOUNTING_WINDOW_SESSIONS,
    contract_epoch: str = CONTRACT_EPOCH,
) -> dict:
    """Thu thập (KHÔNG ném lỗi) độ lệch tập ngày theo phạm vi hợp đồng.

    [WHY] Tách "thu thập" khỏi "assert" để báo cáo nghiệm thu và backlog đọc được cùng một
    nguồn số với cổng fail-closed — nếu báo cáo tự đếm riêng thì hai nơi sẽ lệch nhau.

    Phạm vi (NQ-17 §II.1):
      * thị trường: `market_window` phiên gần nhất, so tập ngày của
        `daily_ohlcv` (≥ MIN_VALID_BARS_PER_SESSION bar hợp lệ), `market_wide_breadth_daily`,
        `regime_history`;
      * kế toán: `accounting_window` phiên gần nhất (mặc định 20), hoặc TOÀN BỘ phiên từ
        `contract_epoch` khi `accounting_window=None`, so `paper_equity_curve` với
        `eod_run_ledger`.

    Returns:
        {"as_of", "market_window": [đầu, cuối, n], "accounting_window": [...],
         "market_missing": {table: [ngày]}, "accounting_missing": {table: [ngày]},
         "ok": bool}
    """
    if as_of_iso is None:
        row = conn.execute("SELECT MAX(date) FROM daily_ohlcv WHERE COALESCE(is_stale, 0) = 0").fetchone()
        if not row or not row[0]:
            raise ValueError("daily_ohlcv rỗng — không xác định được phiên mới nhất")
        as_of_iso = str(row[0])
    as_of = as_of_iso if is_session_day(as_of_iso) else previous_session(as_of_iso)
    if as_of is None:
        raise ValueError(f"không xác định được phiên gần nhất ≤ {as_of_iso}")

    market = session_window_end(as_of, market_window)
    market_set = set(market)
    market_missing: dict[str, list[str]] = {}
    market_spurious: dict[str, list[str]] = {}
    for table in MARKET_TABLES:
        min_bars = MIN_VALID_BARS_PER_SESSION if table == "daily_ohlcv" else None
        present = _present_dates(conn, table, "date", min_bars=min_bars, since=market[0])
        market_missing[table] = [d for d in market if d not in present]
        # [WHY] CHIỀU NGƯỢC (NQ-18 §II.2): dòng tồn tại trên NGÀY KHÔNG GIAO DỊCH. Đo 06/11/2026
        # `2026-09-01` (Quốc khánh) có dòng ở breadth/regime/equity mà mọi cổng cũ báo xanh —
        # vì bất biến chỉ được phát biểu "đủ phiên" chứ không "khớp đúng tập phiên". Một dòng
        # ma làm consumer đọc "phiên liền trước" quanh đó sai và tạo điểm equity không có phiên.
        market_spurious[table] = sorted(d for d in present if market[0] <= d <= market[-1] and d not in market_set)

    if accounting_window is None:
        expect = [d for d in _calendar_sessions(contract_epoch, as_of) if d >= contract_epoch]
    else:
        expect = session_window_end(as_of, accounting_window)

    expect_set = set(expect)
    accounting_missing: dict[str, list[str]] = {}
    accounting_spurious: dict[str, list[str]] = {}
    for table, column in ACCOUNTING_TABLES.items():
        present = _present_dates(conn, table, column, since=expect[0])
        accounting_missing[table] = [d for d in expect if d not in present]
        accounting_spurious[table] = sorted(d for d in present if expect[0] <= d <= expect[-1] and d not in expect_set)

    ok = (
        not any(market_missing.values())
        and not any(market_spurious.values())
        and not any(accounting_missing.values())
        and not any(accounting_spurious.values())
    )
    return {
        "as_of": as_of,
        "market_window": [market[0], market[-1], len(market)],
        "accounting_window": [expect[0], expect[-1], len(expect)],
        "market_missing": market_missing,
        "market_spurious": market_spurious,
        "accounting_missing": accounting_missing,
        "accounting_spurious": accounting_spurious,
        "ok": ok,
    }


def _calendar_sessions(start_iso: str, end_iso: str) -> list[str]:
    """Mọi phiên giao dịch trong `[start_iso, end_iso]` (tính cả hai đầu)."""
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    if end < start:
        raise ValueError(f"khoảng ngày ngược: {start_iso} > {end_iso}")
    out: list[str] = []
    cur = start
    while cur <= end:
        iso = cur.isoformat()
        if is_session_day(iso):
            out.append(iso)
        cur += timedelta(days=1)
    return out


def assert_session_set_alignment(conn, as_of_iso: str | None = None, **kwargs) -> dict:
    """FAIL-CLOSED: lệch tập ngày trong phạm vi hợp đồng ⇒ `SessionSetMismatchError`.

    [WHY] Mọi báo cáo đã công bố đều dựa trên giả định "phiên X có đủ dữ liệu ở cả ba bảng".
    Nếu giả định đó sai mà không ai biết, hệ thống vẫn "xanh" trong khi con số đã sai — đúng
    kiểu lỗi 04/11/2026 (EOD vẫn SUCCESS trên nền khuyết).

    [INVARIANT] KHÔNG miễn trừ nội bộ: hàm này không có tham số "bỏ qua ngày X". Nợ kế thừa
    (nếu có) phải được nêu TÊN ở caller/test — để không ai vô tình thêm một ngày vào danh
    sách miễn trừ trong im lặng.

    [INVARIANT] HAI CHIỀU (NQ-18 §II.2): thiếu phiên ⇒ lỗi; và dòng tồn tại trên NGÀY KHÔNG
    GIAO DỊCH cũng là lỗi. Đo 06/11/2026: `2026-09-01` có dòng ở breadth/regime/equity nhưng
    mọi cổng một chiều đều xanh — đúng lỗ hổng mà hàm này phải bịt.
    """
    report = collect_session_alignment(conn, as_of_iso, **kwargs)
    if report["ok"]:
        return report
    lines = []
    for table, days in report["market_missing"].items():
        if days:
            lines.append(f"  [market] {table}: thiếu {len(days)} phiên {days}")
    for table, days in report["accounting_missing"].items():
        if days:
            lines.append(f"  [accounting] {table}: thiếu {len(days)} phiên {days}")
    for table, days in report["market_spurious"].items():
        if days:
            lines.append(f"  [NON-SESSION] {table}: {len(days)} dòng trên ngày KHÔNG giao dịch {days}")
    for table, days in report["accounting_spurious"].items():
        if days:
            lines.append(f"  [NON-SESSION] {table}: {len(days)} dòng trên ngày KHÔNG giao dịch {days}")
    raise SessionSetMismatchError(
        f"LỆCH TẬP NGÀY tại {report['as_of']} — thị trường {report['market_window']}, "
        f"kế toán {report['accounting_window']}:\n" + "\n".join(lines)
    )
