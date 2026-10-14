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
) -> None:
    """Cổng cho phép toán TOÀN THỊ TRƯỜNG: mỗi phiên phải đủ số mã (và/hoặc đủ tỷ lệ giá trị khớp).

    min_value_ratio: nếu đặt, kiểm thêm tỷ lệ giá trị khớp so với phiên chuẩn (value_by_date).
    """
    bad = {d: n for d, n in count_by_date.items() if n < min_symbols}
    if bad:
        d0 = sorted(bad)[0]
        raise PanelCoverageError(
            f"[{label}] {len(bad)}/{len(count_by_date)} phiên có < {min_symbols} mã "
            f"(ví dụ {d0}={bad[d0]}). Phép toán toàn thị trường không hợp lệ trên các phiên này."
        )
    if min_value_ratio is not None and value_by_date:
        ref = max(value_by_date.values()) if value_by_date else 0.0
        if ref > 0:
            bad_v = {d: v / ref for d, v in value_by_date.items() if v / ref < min_value_ratio}
            if bad_v:
                d0 = sorted(bad_v)[0]
                raise PanelCoverageError(
                    f"[{label}] {len(bad_v)} phiên có tỷ lệ giá trị khớp < {min_value_ratio:.0%} "
                    f"(ví dụ {d0}={bad_v[d0]:.1%}) so với phiên đầy nhất."
                )


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
