"""
RAW PRICE RECONSTRUCTION — UNIT TESTS (TDD RED/GREEN)
PTCK_VNSTOCK Quantitative Research Suite

[WHY] Nghị quyết 04/NQ-HĐKT (Pha 2b) ban hành bất biến
`RAW_PRICE_TICK_GRID_PROOF_REQUIRED`: mọi chuỗi giá đi vào kiểm toán cổ tức
PHẢI chứng minh được là giá thô (bội số 100 VND) và khoảng rơi ngày GDKHQ
khớp cổ tức. KBS/VCI trả chuỗi back-adjusted; biên bản hiệu chuẩn P2
(`raw_price_reconstruction_findings.md`, E6–E7) chứng minh factor segments
giải được từ grid constraint — nhưng chưa có module chính thức, chưa có
test, và gap identity với danh sách event Phase-1 còn mâu thuẫn (E8).

[ROOT CAUSE] Sự cố Pha 1 (commit 15e09c8 → thu hồi tại 8c4e846): engine
chạy trên adjusted closes rồi CỘNG thêm dòng tiền cổ tức = double-counting.
Gốc rễ là chuỗi giá không được phục nguyên về thang tick 100 VND TRƯỚC khi
vào engine. Module này đảo ngược back-adjustment: giải hệ số từng segment
`f` sao cho `P_adj/f` bội 100 VND (grid constraint), suy ra giá thô, rồi
kiểm gap identity `P_ex = P_pre − Cash_Div` với event khai báo.

[INVARIANT]
(1) Fail-closed: segment không giải được factor duy nhất ⇒
    FactorSolveError (Missing > Fake — không chọn bừa một reciprocal).
(2) Raw prices trả về PHẢI bội 100 VND (tick grid), kiểm lại sau khi divide.
(3) `is_certified=True` CHỈ khi mọi cash event khai báo khớp gap identity
    trong tolerance. Thiếu/mâu thuẫn ⇒ certified=False + anomaly (report
    mode), KHÔNG bao giờ im lặng cho qua.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from src.research.raw_price_reconstruction import (
        CashEvent,
        FactorSolveError,
        ReconstructionReport,
        reconstruct_raw_prices,
        solve_factors,
    )
except ImportError:
    from backend.src.research.raw_price_reconstruction import (
        CashEvent,
        FactorSolveError,
        ReconstructionReport,
        reconstruct_raw_prices,
        solve_factors,
    )


# ── Synthetic builder: dựng adjusted closes từ raw grid + cash events theo
#    backward-ratio convention (r_k = P_raw(ex_k)/P_raw(pre_k); mọi bar
#    TRƯỚC ex_k bị nhân tích luỹ product các r_k).
def _build_adjusted(dates, raw_closes, events):
    idx_of = {d: i for i, d in enumerate(dates)}
    ratios = []
    for ev in events:
        j = idx_of[ev.ex_date]
        assert j > 0, "ex-date must not be the first bar"
        ratios.append((j, raw_closes[j] / raw_closes[j - 1]))
    adj = []
    for i, p in enumerate(raw_closes):
        f = 1.0
        for j, r in ratios:
            if j > i:
                f *= r
        adj.append(p * f)
    return adj


def _synthetic_foc_like():
    """Raw grid FOC-like 26 bars, 2 cash events (20k @04-12, 10k @04-23).

    Segment lengths 7/11/8 — đủ bằng chứng solve factor (SEG_MIN_BARS=5).
    Mọi raw % 500 != 0 (raw/100 not divisible by 5) — nếu không, family
    f×5/n (n=6,7,8) cũng preserve grid (raw×n/5 bội 100), gây artificial
    ambiguity không phản ánh dữ liệu KBS thật.
    """
    dates = [date(2021, 4, 5 + k) for k in range(26)]
    # ex index 7 (04-12) chứa drop 20k từ bar trước; ex index 18 (04-23)
    # chứa drop 10k — theo convention P_ex = P_pre − Cash_Div.
    raw = [
        100_100.0, 101_300.0, 103_100.0, 102_600.0,          # seg 0 (7 bars)
        104_100.0, 105_600.0, 104_600.0,
        84_600.0, 85_100.0, 84_100.0, 85_100.0,              # seg 1 (11 bars)
        84_600.0, 85_600.0, 86_100.0, 85_100.0,
        86_600.0, 87_100.0, 86_600.0,
        76_600.0, 78_100.0, 79_100.0, 78_600.0,              # seg 2 (8 bars)
        80_100.0, 79_100.0, 80_600.0, 81_100.0,
    ]
    events = (
        CashEvent(ex_date=date(2021, 4, 12), cash=20_000.0),
        CashEvent(ex_date=date(2021, 4, 23), cash=10_000.0),
    )
    return dates, raw, events


# ── [WHY] Heart of Pha 2b: adjusted chuỗi (synthetic, convention đúng)
#    PHẢI phục nguyên đúng 100% raw closes, certify gap identity.
def test_reconstruct_recovers_raw_grid_and_certifies():
    dates, raw, events = _synthetic_foc_like()
    adj = _build_adjusted(dates, raw, events)
    # sanity: adjusted off-grid trước khi phục nguyên (proof of need)
    assert any(abs(c / 100.0 - round(c / 100.0)) > 1e-6 for c in adj)

    report = reconstruct_raw_prices("SYN", dates, adj, events=events)
    assert isinstance(report, ReconstructionReport)
    assert report.n_bars == len(raw)
    assert report.n_off_grid == 0
    assert report.is_certified is True
    for got, want in zip(report.raw_closes, raw):
        assert abs(got - want) < 1.0  # noise tolerance < 1 VND
    assert report.anomalies == ()


# ── [WHY] Quy tắc phân tầng: KHÔNG được chọn bừa một reciprocal factor.
#    Factor segment cuối luôn 1.0 (raw hiện tại); các segment cũ là dãy
#    giảm(strictly) — backward induction bắt buộc, sai ⇒ FactorSolveError.
def test_solve_factors_backward_induction_consistent():
    dates, raw, events = _synthetic_foc_like()
    adj = _build_adjusted(dates, raw, events)
    factors = solve_factors(dates, adj, event_dates=[e.ex_date for e in events])
    assert len(factors) == len(events) + 1
    assert abs(factors[-1] - 1.0) < 1e-9
    # monotone increasing toward present (f_prev = f_next * r < f_next)
    for older, newer in zip(factors, factors[1:]):
        assert older < newer


# ── [WHY] Fail-closed cốt lõi (E8 findings): danh sách event SAI/MẤT
#    (div khai 5k trong khi data drop 10k) ⇒ KHÔNG được certify. Report
#    phải giữ nguyên giá phục nguyên nhưng gắn cờ anomaly + certified=False.
def test_mismatched_event_fails_certification():
    dates, raw, events = _synthetic_foc_like()
    adj = _build_adjusted(dates, raw, events)
    wrong_events = (
        CashEvent(ex_date=date(2021, 4, 12), cash=5_000.0),   # thiếu 15k
        CashEvent(ex_date=date(2021, 4, 23), cash=10_000.0),
    )
    report = reconstruct_raw_prices("SYN", dates, adj, events=wrong_events)
    assert report.is_certified is False
    assert any("2021-04-12" in a for a in report.anomalies)


# ── [WHY] E8: event khai báo KHÔNG có breakpoint tương ứng trong data
#    (ex-date trúng segment không đổi factor) ⇒ missing evidence, fail-closed.
def test_declared_event_without_factor_breakpoint_fails_certification():
    dates, raw, events = _synthetic_foc_like()
    adj = _build_adjusted(dates, raw, events)
    ghost = events + (CashEvent(ex_date=date(2021, 4, 18), cash=1_000.0),)
    report = reconstruct_raw_prices("SYN", dates, adj, events=ghost)
    assert report.is_certified is False
    assert any("2021-04-18" in a for a in report.anomalies)


# ── [WHY] Segment không có factor grid-consistent nào (dữ liệu méo/mất
#    bar) ⇒ raise FactorSolveError — cấm chọn approximate factor (Missing > Fake).
def test_unsolvable_segment_raises_fail_closed():
    dates, raw, events = _synthetic_foc_like()
    adj = _build_adjusted(dates, raw, events)
    corrupted = list(adj)
    corrupted[2] = 12_345.678  # phá grid hoàn toàn trong segment 0
    with pytest.raises(FactorSolveError):
        reconstruct_raw_prices("SYN_BAD", dates, corrupted, events=events)


# ── [WHY] Phép thử đầu vào: rỗng/không xác định ⇒ ValueError, không im lặng
#    sản xuất report rỗng看起来 hợp lệ.
def test_invalid_inputs_fail_closed():
    with pytest.raises(ValueError):
        reconstruct_raw_prices("EMPTY", [], [], events=())
    with pytest.raises(ValueError):
        reconstruct_raw_prices("NONE", None, None, events=())
    with pytest.raises(ValueError):
        reconstruct_raw_prices(
            "MISMATCH", [date(2021, 1, 4), date(2021, 1, 5)], [70_000.0],
            events=())


# ── [WHY] Mode report (không events): GIẢ ĐOÁN single-segment. KHÔNG bao
#    giờ certify (không có gap evidence); data chứa breakpoints mà không
#    khai báo ⇒ FactorSolveError fail-closed (Missing > Fake — không đoán
#    bừa breakpoints từ sliding window vì reciprocal/neighbor-k families
#    không distinguish được, xem biên bản P2 E6–E9).
def test_report_mode_without_events_never_certifies():
    dates, raw, _ = _synthetic_foc_like()
    report = reconstruct_raw_prices("SYN_BLIND", dates, raw, events=None)
    assert report.is_certified is False
    assert report.n_off_grid == 0
    assert len(report.raw_closes) == len(raw)
    assert report.anomalies  # report-mode note


def test_report_mode_with_undisclosed_breakpoints_fails_closed():
    dates, raw, events = _synthetic_foc_like()
    adj = _build_adjusted(dates, raw, events)
    with pytest.raises(FactorSolveError):
        reconstruct_raw_prices("SYN_BLIND_BAD", dates, adj, events=None)
