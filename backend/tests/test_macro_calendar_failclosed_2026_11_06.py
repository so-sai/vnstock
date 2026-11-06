# -*- coding: utf-8 -*-
"""Mục 29 #6 (NQ-18 §II.2): loader lịch của MACRO phải fail-CLOSED, và dùng CHUNG một nguồn.

[WHY]
    Hai loader cùng đọc `weekend_holidays.json` nhưng khác chính sách lỗi là đủ để tái diễn sự
    cố 31/08/2026 ở dạng khó thấy hơn: file hỏng ⇒ `daily_updater` dừng, nhưng `VNSessionCalendar`
    (dùng bởi freshness sentinel, session resolver, stale-prev guard) vẫn "sống" với lịch
    **rỗng** ⇒ mọi ngày lễ thành phiên, nến macro ghép sang ngày sàn đóng.

[ROOT CAUSE]
    `time_series_aligner._load_vn_holidays` bọc `try/except` rộng rồi `return holidays` với
    `holidays = set()`. Không phân biệt "không có ngày lễ" với "không đọc được lịch".

[INVARIANT]
    (a) mọi nhánh lỗi ⇒ `TradingCalendarError`, KHÔNG BAO GIỜ trả set rỗng;
    (b) hai loader phải cho CÙNG một tập ngày (một nguồn, một chính sách);
    (c) `VNSessionCalendar` phải từ chối ngày sàn đóng cửa thật (`2026-09-01`) và chấp nhận
        phiên thật (`2026-11-05`);
    (d) chống vá quá tay: file schema_version < 2 bị TỪ CHỐI (bản cũ chưa đối chiếu dữ liệu),
        vì "đọc được" ≠ "đúng".
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _write(tmp_path: Path, payload) -> Path:
    p = tmp_path / "calendar.json"
    p.write_text(
        json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload,
        encoding="utf-8",
    )
    return p


def test_macro_and_daily_updater_load_the_same_calendar():
    """[WHY] "Một nguồn sự thật" chỉ có nghĩa nếu hai đường đọc ra CÙNG tập ngày. Test này
    đỏ ngay nếu ai đó thêm một fallback/lọc riêng ở một phía.
    """
    from src.daily_updater import VN_HOLIDAYS
    from src.services.macro.time_series_aligner import _load_vn_holidays

    macro = {d.isoformat() for d in _load_vn_holidays()}
    assert macro == set(VN_HOLIDAYS)


def test_missing_calendar_raises_instead_of_returning_empty_set(monkeypatch, tmp_path):
    """[WHY] Đây là bất biến trung tâm: file thiếu ⇒ DỪNG. Trả `set()` là khẳng định sai sự
    thật ("không có ngày lễ nào") và làm mọi ngày lễ thành phiên.
    """
    import src.database.trading_calendar as tc
    from src.services.macro.time_series_aligner import _load_vn_holidays

    monkeypatch.setattr(tc, "DEFAULT_CALENDAR_PATHS", (tmp_path / "khong_ton_tai.json",))
    with pytest.raises(tc.TradingCalendarError, match="Không tìm thấy"):
        _load_vn_holidays()


@pytest.mark.parametrize(
    "payload,match",
    [
        ("{ khong phai json", "hỏng"),
        ({"holidays": []}, "rỗng"),
        ({"holidays": ["2026-13-45"]}, "sai định dạng"),
        ({"holidays": ["2026-09-01", 123]}, "không phải chuỗi"),
    ],
)
def test_broken_calendar_raises_instead_of_returning_empty_set(monkeypatch, tmp_path, payload, match):
    """[WHY] Bốn kiểu hỏng khác nhau phải cùng một kết cục (DỪNG). Nếu chỉ một kiểu được chặn,
    thì "chính sách fail-closed" chỉ đúng trên giấy.
    """
    import src.database.trading_calendar as tc
    from src.services.macro.time_series_aligner import _load_vn_holidays

    monkeypatch.setattr(tc, "DEFAULT_CALENDAR_PATHS", (_write(tmp_path, payload),))
    with pytest.raises(tc.TradingCalendarError, match=match):
        _load_vn_holidays()


def test_schema_v1_calendar_is_rejected(monkeypatch, tmp_path):
    """[WHY] Chống vá quá tay theo hướng nguy hiểm: ai đó "khôi phục" file lịch cũ (schema v1,
    36 ngày, thiếu 14 ngày nghỉ, thừa 2 phiên Tết 2024) thì loader phải TỪ CHỐI, không được
    im lặng đọc như thể hợp lệ — nếu không, toàn bộ 16 ngày đã sửa bị đảo ngược mà test lịch
    vẫn có thể xanh vì chúng đọc file mới.
    """
    import src.database.trading_calendar as tc
    from src.services.macro.time_series_aligner import _load_vn_holidays

    old = {"schema_version": 1, "holidays": ["2026-09-01"]}
    monkeypatch.setattr(tc, "DEFAULT_CALENDAR_PATHS", (_write(tmp_path, old),))
    with pytest.raises(tc.TradingCalendarError, match="schema_version"):
        _load_vn_holidays()


def test_vn_session_calendar_rejects_real_market_holiday():
    """[WHY] Kiểm chứng đầu-cuối trên lớp mà các consumer thật dùng (`VNSessionCalendar`):
    01/09/2026 là Quốc khánh (0 dòng ở cả 3 bảng) nên phải KHÔNG phải phiên; 05/11/2026 là
    phiên thật. Nếu đảo, `prev_trading_day` sẽ nhảy vào ngày sàn đóng.
    """
    from src.services.macro.time_series_aligner import VNSessionCalendar

    cal = VNSessionCalendar()
    assert cal.is_trading_day(date(2026, 9, 1)) is False
    assert cal.is_trading_day(date(2026, 8, 31)) is False
    assert cal.is_trading_day(date(2026, 11, 5)) is True
    assert cal.prev_trading_day(date(2026, 9, 3)) == date(2026, 8, 28)
