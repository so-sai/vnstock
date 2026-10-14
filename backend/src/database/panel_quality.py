"""panel_quality.py — Cổng FAIL-FAST cho tính toàn vẹn panel (coverage theo phiên).

WHY: store này có 2 dạng lỗ hổng đo được (2026-10):
  1. `daily_ohlcv` rụng mã theo giai đoạn: 1.516 mã/phiên (2026-09) → ~825–900 mã/phiên
     (2026-10). Chỉ số `VN30` thiếu bar 55/64 phiên trong cửa sổ 2026-06→10.
  2. Vì vậy mọi tỷ trọng cross-sectional (ví dụ KL nhóm mid/penny) sẽ NHẢY LOẠN vì *mã
     khuyết trong DB*, không vì dòng tiền đầu cơ thay đổi; và lợi suất cộng dồn của một
     index bị tính trên chuỗi thưa nếu ta lỡ `fillna(0)`.

Nguyên tắc: **thiếu bar ⇒ DỪNG (raise), không suy diễn**. Không có giá trị mặc định nào
thay được một bar đang thiếu; `fillna(0)` lên chuỗi lợi suất là tự tạo ra dữ kiện giả.

Dùng:
    from src.database.panel_quality import assert_series_coverage, assert_market_breadth
    assert_series_coverage(closes, expected_sessions=cal, min_bar_ratio=0.95, label="VN30 index")
    assert_market_breadth(count_by_date, min_symbols=1000, label="turnover toàn thị trường")
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

DB_PATH_FALLBACK = Path(__file__).resolve().parents[2] / "data" / "screener_cache.db"

DEFAULT_MIN_BAR_RATIO = 0.95
DEFAULT_MIN_BREADTH = 1000


class PanelCoverageError(RuntimeError):
    """Panel không đủ phủ để tính toán — dừng chạy thay vì suy diễn."""


def coverage_ratio(present: Iterable[str], expected: Iterable[str]) -> float:
    """Tỷ lệ phiên có bar / số phiên kỳ vọng."""
    exp = set(expected)
    if not exp:
        return 0.0
    return len(set(present) & exp) / len(exp)


def assert_panel_coverage(
    present: Iterable[str],
    expected: Iterable[str],
    min_bar_ratio: float = DEFAULT_MIN_BAR_RATIO,
    label: str = "panel",
) -> float:
    """Cổng tổng quát: tỷ lệ phủ < min_bar_ratio ⇒ PanelCoverageError."""
    exp = list(dict.fromkeys(expected))
    ratio = coverage_ratio(present, exp)
    if ratio < min_bar_ratio:
        missing = sorted(set(exp) - set(present))
        head = ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else "")
        raise PanelCoverageError(
            f"[{label}] coverage {ratio:.1%} < {min_bar_ratio:.1%} (thiếu {len(missing)}/{len(exp)} phiên: {head})"
        )
    return ratio


def assert_series_coverage(
    series: Mapping[str, float],
    expected_sessions: Iterable[str],
    min_bar_ratio: float = DEFAULT_MIN_BAR_RATIO,
    label: str = "series",
) -> float:
    """Cổng cho chuỗi ĐƠN (index/target): mọi phiên kỳ vọng phải có bar liền mạch.

    WHY tách riêng: chuỗi index với bar khuyết KHÔNG được phép coi return = 0 — nó không
    phải "không đổi", nó là "không có dữ liệu". Đã xảy ra thật với VN30.
    """
    return assert_panel_coverage(series.keys(), expected_sessions, min_bar_ratio, label)


def assert_market_breadth(
    count_by_date: Mapping[str, int],
    min_symbols: int = DEFAULT_MIN_BREADTH,
    label: str = "market-wide",
    min_value_ratio: float | None = None,
    value_by_date: Mapping[str, float] | None = None,
    baseline: str = "rolling_median",
    window: int = 20,
) -> None:
    """Cổng cho phép toán TOÀN THỊ TRƯỜNG: mỗi phiên phải đủ số mã (và/hoặc đủ tỷ lệ giá trị khớp).

    `baseline` quyết định mẫu số của tỷ lệ giá trị:
      - "rolling_median" (MẶC ĐỊNH, PIT-safe): so với median `window` phiên TRƯỚC đó. Đây là
        cơ sở đúng để phát hiện LỖ HỔNG dữ liệu, vì nó bù cho biến động thanh khoản bình thường.
      - "max": so với phiên đầy nhất — ĐÃ ĐO LÀ SAI: phạt mọi phiên thanh khoản thấp bình thường
        (với min_value_ratio=0.85 thì chặn 72/74 phiên, 97%). Giữ lại chỉ để tương thích.
    Đo 2026-06→10 với rolling median: ratio median 0,99 (p5 0,61). Ngưỡng 0,85 chặn 20/76 phiên
    (26% — quá tay), 0,70 chặn 11/76, 0,50 chỉ chặn 1/76 (phiên 72 dòng thật sự hỏng).
    """
    bad = {d: n for d, n in count_by_date.items() if n < min_symbols}
    if bad:
        d0 = sorted(bad)[0]
        raise PanelCoverageError(
            f"[{label}] {len(bad)}/{len(count_by_date)} phiên có < {min_symbols} mã "
            f"(ví dụ {d0}={bad[d0]}). Phép toán toàn thị trường không hợp lệ trên các phiên này."
        )
    if min_value_ratio is None or not value_by_date:
        return
    dates = sorted(value_by_date)
    for i, d in enumerate(dates):
        if baseline == "max":
            ref = max(value_by_date.values())
        else:
            prior = [value_by_date[x] for x in dates[max(0, i - window) : i]]
            ref = float(sorted(prior)[len(prior) // 2]) if prior else 0.0
        if ref > 0 and value_by_date[d] / ref < min_value_ratio:
            raise PanelCoverageError(
                f"[{label}] {d}: tỷ lệ giá trị khớp {value_by_date[d] / ref:.1%} < {min_value_ratio:.0%} "
                f"(cơ sở {baseline}={ref:.2f} nghìn tỷ) — nghi thiếu bar, không phải ngày ít giao dịch."
            )


def balanced_panel_from_db(
    db_path: str | Path | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_presence: float = 0.98,
    validity_sql: str | None = None,
    exclude: tuple[str, ...] = ("VNINDEX", "VN30", "HNXINDEX", "UPINDEX"),
) -> list[str]:
    """Bảng cân bằng lấy TRỰC TIẾP từ store — điểm neo cho mọi script alpha/factor đa mã.

    `validity_sql` mặc định là vị từ hợp lệ chuẩn của hệ thống (data_validity.VALID_ROW_SQL),
    KHÔNG phải `is_stale` — xem AGENTS.md: median 619/1514 dòng/phiên bị gắn is_stale=1 nên
    lọc bằng is_stale làm rụng ~40% mẫu (đo thật: balanced panel từ 237 mã còn 8 mã).
    """
    import sqlite3

    if validity_sql is None:
        from src.database.data_validity import VALID_ROW_SQL

        validity_sql = VALID_ROW_SQL
    path = Path(db_path) if db_path else DB_PATH_FALLBACK
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        sql = f"SELECT symbol, date FROM daily_ohlcv WHERE {validity_sql}"
        params: list[str] = []
        if date_from:
            sql += " AND date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND date <= ?"
            params.append(date_to)
        if exclude:
            sql += f" AND symbol NOT IN ({','.join('?' * len(exclude))})"
            params.extend(exclude)
        presence: dict[str, set[str]] = {}
        sessions: set[str] = set()
        for s, d in con.execute(sql, params):
            presence.setdefault(str(s), set()).add(str(d))
            sessions.add(str(d))
    finally:
        con.close()
    return balanced_panel(presence, sessions, min_presence=min_presence)


def balanced_panel(
    presence_by_symbol: Mapping[str, set[str]],
    expected_sessions: Iterable[str],
    min_presence: float = 0.98,
) -> list[str]:
    """Danh sách mã có mặt >= min_presence số phiên kỳ vọng — nền cho mọi chỉ báo tỷ trọng.

    WHY: nếu tính tỷ trọng nhóm trên "các dòng hiện có", chỉ báo sẽ phản ánh lỗ hổng DB.
    Bảng cân bằng (fixed) loại hiệu ứng đó ra khỏi thiết kế.
    """
    exp = set(expected_sessions)
    if not exp:
        return []
    need = min_presence * len(exp)
    return sorted(s for s, days in presence_by_symbol.items() if len(set(days) & exp) >= need)
