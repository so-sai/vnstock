"""SSOT hợp nhất cổ tức cho các runner kiểm toán thực nghiệm (Pha 1–5).

[WHY] Cùng một bộ event cổ tức từng tồn tại ở BA nơi: `cash_event_registry`
(dùng cho cổng provenance), runner audit (khai báo lại `CashEvent`), và danh
sách `CashDividendEvent` cho engine (kèm pay_date + action_id). Đo 29/10/2026:
`run_vea_empirical_audit.py` có 7 event inline, `run_smb_empirical_audit.py` có
13, cả hai đều 0 lần đọc registry. Hai nguồn event là đúng cái bẫy trôi lệch đã
bị Hội đồng loại bỏ ở cổng — nếu ai đó sửa cash trong registry, audit vẫn chạy
trên con số cũ mà báo cáo thì ghi "nguồn: registry".

ROOT CAUSE: registry chỉ chứa dữ liệu *giá* (ex_date, cash); engine cần thêm
*thanh toán* (pay_date) và *danh tính* (action_id để đếm nguồn độc lập). Vì
thiếu chỗ chứa nên mỗi runner tự dựng lại cả cục, kéo theo cả cash/ex_date.

INVARIANT: cash + ex_date chỉ được đọc từ `CASH_EVENTS` (SSOT giá). pay_date và
action_id nằm trong bảng metadata dưới đây, khoá theo `ex_date`. Thiếu metadata
hoặc metadata mồ côi ⇒ `MissingDividendMetadataError` (fail-closed), cấm tự bịa
giá trị mặc định.
"""

from __future__ import annotations

from datetime import date

try:
    from src.research.cash_event_registry import CASH_EVENTS
    from src.research.dividend_audit_engine import CashDividendEvent
except ImportError:  # pragma: no cover - chỉ xảy ra khi import bằng `backend.`
    from backend.src.research.cash_event_registry import CASH_EVENTS
    from backend.src.research.dividend_audit_engine import CashDividendEvent


class MissingDividendMetadataError(RuntimeError):
    """Thiếu/mồ côi metadata thanh toán cho một event trong registry."""


# [WHY] Mốc bắt đầu kỳ kiểm toán của cả 5 mã: 2021-01-04 (phiên HOSE/UPCoM đầu
# năm). Event trước mốc (vd SMB 2020-12-22) chỉ dùng phục nguyên giá thô + làm
# warm-up ADV20, KHÔNG được tính vào dòng cổ tức của báo cáo — tính vào sẽ thổi
# phồng tổng cổ tức so với số công bố của doanh nghiệp.
AUDIT_WINDOW_START = date(2021, 1, 4)

# [WHY] pay_date: ngày thanh toán thực tế theo VSDC, dùng để engine dựng dòng
# tiền (T+2 + lịch trả) — sai ngày trả ⇒ lãi tiền gửi của khoản cổ tức bị lệch.
# action_id: mã sự kiện dùng làm khoá đếm "nguồn độc lập" (independent_action_ids);
# phải khớp mẫu `VSDC/<SYM>/<YYYYMMDD>/CASH_<...>` để truy vết ngược về hồ sơ.
# Sửa một dòng ở đây ⇒ provenance và dòng tiền cùng sai, và test
# `test_action_ids_are_unique_and_namespaced` sẽ chỉ ra ngay.
_DIVIDEND_METADATA: dict[str, dict[date, tuple[date, str]]] = {
    "FOC": {
        date(2021, 4, 13): (date(2021, 4, 29), "VSDC/FOC/20210413/CASH_20K"),
        date(2022, 6, 10): (date(2022, 6, 30), "VSDC/FOC/20220610/CASH_8K"),
        date(2023, 6, 8): (date(2023, 6, 19), "VSDC/FOC/20230608/CASH_5K"),
        date(2024, 7, 18): (date(2024, 7, 31), "VSDC/FOC/20240718/CASH_2K"),
        date(2025, 5, 23): (date(2025, 6, 5), "VSDC/FOC/20250523/CASH_10K"),
        date(2026, 8, 10): (date(2026, 8, 25), "VSDC/FOC/20260810/CASH_10K"),
    },
    "VEA": {
        date(2021, 1, 4): (date(2021, 2, 5), "VSDC/VEA/20210104/CASH_5253"),
        date(2021, 7, 15): (date(2021, 8, 16), "VSDC/VEA/20210715/CASH_4990"),
        date(2021, 10, 12): (date(2021, 11, 10), "VSDC/VEA/20211012/CASH_463"),
        date(2022, 10, 28): (date(2022, 11, 30), "VSDC/VEA/20221028/CASH_4494"),
        date(2023, 11, 17): (date(2023, 12, 20), "VSDC/VEA/20231117/CASH_4187"),
        date(2024, 11, 19): (date(2024, 12, 20), "VSDC/VEA/20241119/CASH_5035"),
        date(2025, 11, 18): (date(2025, 12, 19), "VSDC/VEA/20251118/CASH_4658"),
    },
    "SMB": {
        date(2021, 10, 22): (date(2021, 11, 15), "VSDC/SMB/20211022/CASH_2500"),
        date(2022, 2, 18): (date(2022, 3, 15), "VSDC/SMB/20220218/CASH_1000"),
        date(2022, 9, 20): (date(2022, 10, 14), "VSDC/SMB/20220920/CASH_2500"),
        date(2023, 2, 20): (date(2023, 3, 15), "VSDC/SMB/20230220/CASH_1000"),
        date(2023, 7, 17): (date(2023, 8, 15), "VSDC/SMB/20230717/CASH_2500"),
        date(2023, 10, 24): (date(2023, 11, 15), "VSDC/SMB/20231024/CASH_1000"),
        date(2024, 5, 22): (date(2024, 6, 14), "VSDC/SMB/20240522/CASH_1500"),
        date(2024, 9, 16): (date(2024, 10, 15), "VSDC/SMB/20240916/CASH_2000"),
        date(2025, 1, 7): (date(2025, 1, 24), "VSDC/SMB/20250107/CASH_1500"),
        date(2025, 8, 13): (date(2025, 9, 15), "VSDC/SMB/20250813/CASH_2000"),
        date(2025, 11, 12): (date(2025, 12, 15), "VSDC/SMB/20251112/CASH_2000"),
        date(2026, 5, 25): (date(2026, 6, 15), "VSDC/SMB/20260525/CASH_1000"),
    },
}


def build_vsdc_dividends(symbol: str, window_start: date) -> list[CashDividendEvent]:
    """Dựng danh sách `CashDividendEvent` cho kỳ audit TỪ registry (SSOT).

    Chỉ `cash` và `ex_date` lấy từ registry; `pay_date`/`action_id` lấy từ
    metadata. Mọi lệch phạm vi (thiếu metadata, metadata mồ côi) đều raise.
    """
    if symbol not in CASH_EVENTS:
        raise MissingDividendMetadataError(f"{symbol}: không có trong cash_event_registry")

    events = [ev for ev in CASH_EVENTS[symbol] if ev.ex_date >= window_start]
    metadata = _DIVIDEND_METADATA.get(symbol, {})

    # [WHY] Kiểm cả hai chiều: event thiếu metadata (không có bằng chứng thanh
    # toán) và metadata mồ côi (sự kiện không còn trong SSOT). Chỉ kiểm một chiều
    # sẽ để lọt đúng nửa số ca trôi lệch.
    missing = [ev.ex_date for ev in events if ev.ex_date not in metadata]
    if missing:
        raise MissingDividendMetadataError(
            f"{symbol}: event trong registry thiếu pay_date/action_id: {[str(d) for d in missing]}"
        )
    registry_dates = {ev.ex_date for ev in CASH_EVENTS[symbol]}
    orphans = sorted(set(metadata) - registry_dates)
    if orphans:
        raise MissingDividendMetadataError(
            f"{symbol}: metadata mồ côi (event không còn trong registry): {[str(d) for d in orphans]}"
        )

    return [
        CashDividendEvent(
            ex_date=ev.ex_date,
            pay_date=metadata[ev.ex_date][0],
            cash_per_share=ev.cash,
            action_id=metadata[ev.ex_date][1],
        )
        for ev in events
    ]


def dividend_cash_total(divs: list[CashDividendEvent]) -> float:
    """Tổng cổ tức tiền mặt/cp trong kỳ — in ra báo cáo từ DỮ LIỆU, không hardcode."""
    return sum(d.cash_per_share for d in divs)
