"""Test cho tầng kiểm định CHÉO HAI VENDOR (KBS vintage vs DB VCI).

[WHY] Band OHLC chỉ dùng được khi phiên GDKHQ giao dịch hai phía tham chiếu; với
PAT có 4/9 event rơi vào phiên gap (cả dải một phía) nên tầng 2 (±15% UPCoM)
là bằng chứng yếu. Tầng 3 dưới đây độc lập với giá khớp: nó so TỶ SỐ hai chuỗi
back-adjusted, nên đo được "vendor kia có áp đúng khoản cổ tức này không".

[INVARIANT] Không bao giờ suy ra cash từ im lặng: mọi kết luận "khớp" phải kèm
số đo step/implied_cash; mâu thuẫn thật ⇒ ProvenanceConflictError (fail-closed).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from src.research.cross_vendor_agreement import (
        ProvenanceConflictError,
        assert_no_conflict,
        cross_vendor_steps,
    )
except ImportError:  # pragma: no cover - chỉ xảy ra khi import bằng `backend.`
    from backend.src.research.cross_vendor_agreement import (
        ProvenanceConflictError,
        assert_no_conflict,
        cross_vendor_steps,
    )


def _series(start: date, n: int, base: float, step: float = 100.0):
    """Chuỗi ngày giao dịch giả (bỏ cuối tuần) + giá tuyến tính."""
    out = {}
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out[d] = base + step * len(out)
        d += timedelta(days=1)
    return out


def _calendar(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def test_agree_when_both_vendors_apply_same_adjustment():
    """[WHY] Hai vendor điều chỉnh giống nhau ⇒ tỷ số phẳng ⇒ 'agree' (xác nhận
    độc lập cash + ex-date), kèm step ≈ 0 để người đọc thấy số đo."""
    days = _calendar(date(2024, 1, 1), 60)
    ex = days[30]
    db = _series(date(2024, 1, 1), 60, 50_000.0)
    # CẢ HAI vendor áp cùng khoản 500 đ ⇒ tỷ số phẳng 0.99 ở mọi bar.
    kbs = {d: v * 0.99 for d, v in db.items()}
    checks = cross_vendor_steps("TST", [(ex, 500.0)], kbs, db)
    assert len(checks) == 1
    assert checks[0].status == "agree"
    assert abs(checks[0].step_pct) < 0.05


def test_vendor_lag_when_only_one_vendor_applied_the_dividend():
    """[WHY] DB chưa cập nhật kỳ cổ tức mới nhất ⇒ tỷ số nhảy đúng một lượng
    bằng cash/giá. Đây KHÔNG phải mâu thuẫn — nó còn đo được cash của KBS
    (implied_cash ≈ cash khai báo) nên phải tách nhãn riêng, không được gộp
    vào 'agree' (che mất việc DB lỗi thời) cũng không được gộp vào 'conflict'."""
    db = _series(date(2024, 1, 1), 60, 50_000.0)
    ex = _calendar(date(2024, 1, 1), 60)[30]
    pre = 50_000.0 + 100.0 * 30
    # KBS áp đúng khoản 1.000 đ (f = (pre − cash)/pre), DB chưa áp (f = 1).
    f = (pre - 1_000.0) / pre
    kbs = {d: v * (f if d < ex else 1.0) for d, v in db.items()}
    checks = cross_vendor_steps("TST", [(ex, 1_000.0)], kbs, db)
    assert checks[0].status == "vendor-lag"
    assert checks[0].implied_cash == pytest.approx(1_000.0, rel=0.03)


def test_conflict_when_vendor_applied_a_different_amount():
    """[WHY] Cùng ex-date nhưng vendor kia điều chỉnh 5.000 trong khi ta khai
    1.000 ⇒ implied_cash lệch 5× ⇒ KHÔNG được certify. Đây là ca chống
    'vá quá tay': tầng này tồn tại để bắt chính lỗi nó có thể gây ra."""
    db = _series(date(2024, 1, 1), 60, 50_000.0)
    ex = _calendar(date(2024, 1, 1), 60)[30]
    pre = 50_000.0 + 100.0 * 30
    # Vendor kia điều chỉnh 5.000 đ trong khi ta khai 1.000 đ ⇒ implied lệch 5×.
    f = (pre - 5_000.0) / pre
    kbs = {d: v * (f if d < ex else 1.0) for d, v in db.items()}
    checks = cross_vendor_steps("TST", [(ex, 1_000.0)], kbs, db)
    assert checks[0].status == "conflict"
    with pytest.raises(ProvenanceConflictError):
        assert_no_conflict(checks)


def test_insufficient_data_is_not_silently_agree():
    """[WHY] Không đủ bar hai phía (mã mới, DB thiếu) ⇒ 'insufficient-data',
    TUYỆT ĐỐI không được mặc định là 'agree' (Missing > Fake)."""
    db = _series(date(2024, 1, 1), 20, 50_000.0)
    kbs = dict(db)
    checks = cross_vendor_steps("TST", [(date(2024, 2, 1), 500.0)], kbs, db)
    assert checks[0].status == "insufficient-data"
    assert checks[0].implied_cash is None


def _real_inputs(symbol: str, fname: str):
    vintage = BACKEND / "data" / fname
    db = BACKEND / "data" / "screener_cache.db"
    if not vintage.exists() or not db.exists():
        pytest.skip("thiếu vintage hoặc DB trong workspace")
    import json
    import sqlite3

    rows = json.loads(vintage.read_text(encoding="utf-8"))
    kbs = {}
    for r in rows:
        kbs[date.fromisoformat(r["t"][:10])] = float(r["c"])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    got = con.execute("SELECT date, close FROM daily_ohlcv WHERE symbol=? ORDER BY date", (symbol,)).fetchall()
    con.close()
    return kbs, {date.fromisoformat(d): float(c) for d, c in got}


def test_real_pat_has_no_conflict_and_one_vendor_lag():
    """[WHY] Đo thật trên PAT (KBS vs VCI): 8/8 event cũ khớp nhau (|step|<0,05%),
    đúng 1 event 2026-09-15 là 'vendor-lag' (VCI chưa điều chỉnh kỳ 2/2025);
    KHÔNG có conflict ⇒ tầng trần/sàn cho các phiên gap là có cơ sở độc lập."""
    kbs, db = _real_inputs("PAT", "kbs_pat_2022_2026.json")
    events = [
        (date(2022, 8, 4), 10_000.0),
        (date(2022, 12, 19), 10_000.0),
        (date(2023, 4, 7), 10_655.0),
        (date(2023, 12, 19), 9_000.0),
        (date(2024, 4, 12), 1_000.0),
        (date(2024, 11, 19), 7_000.0),
        (date(2025, 4, 24), 2_500.0),
        (date(2025, 12, 24), 10_000.0),
        (date(2026, 9, 15), 2_900.0),
    ]
    checks = cross_vendor_steps("PAT", events, kbs, db)
    assert_no_conflict(checks)
    by_date = {c.ex_date: c for c in checks}
    assert by_date[date(2026, 9, 15)].status == "vendor-lag"
    assert [d for d, c in by_date.items() if c.status == "vendor-lag"] == [date(2026, 9, 15)]
    assert sum(1 for c in checks if c.status == "agree") == 8


def test_real_dsn_has_no_conflict_and_all_events_agree_after_backfill():
    """[WHY] DSN: 10/10 event khớp sau khi vá lỗ hổng phủ 2026-04..07 (30/10/2026).

    [WHY] Trước backfill, event 2026-05-18 từng bị gắn `vendor-lag` +4,342% và
    được ghi chú là "DB-VCI chưa điều chỉnh kỳ này". Chẩn đoán đó SAI: 5 bar
    `kbs` trong DB giai đoạn 2026-04-08..05-15 là giá THÔ (bội lưới), còn phần
    VCI lịch sử là giá ĐÃ ĐIỀU CHỈNH ⇒ bước nhảy đúng bằng
    1/0,958395 − 1 = +4,34%, tức là chuyển thang giá BÊN TRONG DB, không phải
    một vendor chậm. Vá bằng `backfill-history` (VCI, cùng quy ước adjusted)
    ⇒ bước nhảy về +0,003% và event thành `agree`.

    [WHY] Test khoá cả hai chiều: (a) không có conflict, (b) MỌI event agree.
    Nếu ai nạp lại bar thô vào khoảng này, hoặc để lỗ hổng phủ tái xuất, bước
    nhảy sẽ hiện lại và test đỏ — đúng lúc cần biết. KHÔNG nới thành "cho phép
    1 vendor-lag": làm vậy sẽ xoá mất chốt chặn vừa vá.
    """
    kbs, db = _real_inputs("DSN", "kbs_dsn_2021_2026.json")
    events = [
        (date(2021, 2, 26), 2_500.0),
        (date(2021, 12, 28), 1_500.0),
        (date(2022, 11, 15), 1_500.0),
        (date(2023, 4, 18), 2_500.0),
        (date(2023, 12, 28), 2_400.0),
        (date(2024, 3, 14), 1_600.0),
        (date(2024, 12, 26), 2_400.0),
        (date(2025, 3, 20), 1_600.0),
        (date(2026, 1, 13), 2_400.0),
        (date(2026, 5, 18), 1_600.0),
    ]
    checks = cross_vendor_steps("DSN", events, kbs, db)
    assert_no_conflict(checks)
    assert {c.status for c in checks} == {"agree"}
    assert sum(1 for c in checks if c.status == "agree") == 10
    by_date = {c.ex_date: c for c in checks}
    assert abs(by_date[date(2026, 5, 18)].step_pct) < 0.3
