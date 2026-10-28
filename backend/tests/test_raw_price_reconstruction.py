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
    return _adjust_like(dates, raw_closes, events, raw_closes)


def _adjust_like(dates, raw_closes, events, series):
    """Nhân cùng hệ số back-adjust lên BẤT KỲ series nào (OHLC) trong thang thô."""
    idx_of = {d: i for i, d in enumerate(dates)}
    ratios = []
    for ev in events:
        j = idx_of[ev.ex_date]
        assert j > 0, "ex-date must not be the first bar"
        ratios.append((j, raw_closes[j] / raw_closes[j - 1]))
    out = []
    for i, p in enumerate(series):
        f = 1.0
        for j, r in ratios:
            if j > i:
                f *= r
        out.append(p * f)
    return out


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


# ── Band mode (OHLC ex-session bound) — NQ-04 điều chỉnh DoD ngày 28/10/2026:
#    "Cho phép khoảng rơi giá đóng cửa giữa 2 phiên biên lệch khỏi cổ tức một
#     lượng Δ_market (biến động phiên GDKHQ), nhưng factor f giải ra trên từng
#     segment PHẢI đưa 100% bars về bội 100 VND."
#
#    [WHY] Δ_market không được là "cửa sau" để bỏ qua gap identity: nó bị chặn
#    bằng biên độ VẬT LÝ của chính phiên GDKHQ — giá tham chiếu P_pre − Cash
#    phải nằm trong [low_ex, high_ex]. Cổ tức khai sai ⇒ tham chiếu rơi ra
#    ngoài biên ⇒ KHÔNG certify. Đây là bằng chứng mạnh hơn dung sai tuyệt đối
#    vì nó co giãn theo biến động thật của phiên (UPCoM ±10-15%).
def _synthetic_with_market_move():
    """Như _synthetic_foc_like nhưng phiên GDKHQ 2021-04-12 đóng cửa +2,000đ
    so với giá tham chiếu (= Δ_market thật, không phải cổ tức)."""
    dates, raw, events = _synthetic_foc_like()
    shifted = list(raw)
    for i in range(7, len(shifted)):  # từ bar ex trở đi: thị trường hồi +2,000
        shifted[i] += 2_000.0
    lows = [c - 500.0 for c in shifted]
    highs = [c + 500.0 for c in shifted]
    # phiên ex 1: mở gần tham chiếu 84,600 rồi tăng trần → biên rộng, ref nằm trong
    lows[7], highs[7] = 84_000.0, 87_500.0
    # phiên ex 2 (2021-04-23): rơi đúng 10,000 = cổ tức, biên hẹp quanh close
    lows[18], highs[18] = 78_000.0, 79_500.0
    return dates, shifted, events, lows, highs


def test_band_mode_certifies_market_delta_within_ex_session_range():
    dates, raw, events, lows, highs = _synthetic_with_market_move()
    adj = _build_adjusted(dates, raw, events)
    # band phải cùng thang với chuỗi đầu vào (adjusted) — cùng convention
    adj_lows = _adjust_like(dates, raw, events, lows)
    adj_highs = _adjust_like(dates, raw, events, highs)

    report = reconstruct_raw_prices(
        "SYN_BAND", dates, adj, events=events, lows=adj_lows, highs=adj_highs
    )
    assert report.is_certified is True, report.anomalies
    assert report.anomalies == ()
    assert report.n_off_grid == 0
    # DoD 100% bars bội 100 VND — không một ngoại lệ
    assert all(abs(c / 100.0 - round(c / 100.0)) < 1e-9 for c in report.raw_closes)
    for got, want in zip(report.raw_closes, raw):
        assert abs(got - want) < 1.0
    # Δ_market phải được GHI LẠI làm bằng chứng kiểm toán (không im lặng)
    assert len(report.gap_deltas) == len(events)
    assert abs(report.gap_deltas[0] - (-2_000.0)) < 1.0   # drop 18k vs cash 20k
    assert abs(report.gap_deltas[1]) < 1.0                # drop 10k = cash 10k


def test_band_mode_rejects_cash_dividend_outside_ex_session_range():
    """[WHY] Cổ tức khai 8k trong khi thực trả 20k ⇒ tham chiếu 96,600 rơi
    NGOÀI biên phiên ex [84,000–87,500] ⇒ fail-closed, không được certify."""
    dates, raw, events, lows, highs = _synthetic_with_market_move()
    adj = _build_adjusted(dates, raw, events)
    wrong = (
        CashEvent(ex_date=date(2021, 4, 12), cash=8_000.0),
        CashEvent(ex_date=date(2021, 4, 23), cash=10_000.0),
    )
    report = reconstruct_raw_prices(
        "SYN_BAND_BAD", dates, adj, events=wrong,
        lows=_adjust_like(dates, raw, events, lows),
        highs=_adjust_like(dates, raw, events, highs),
    )
    assert report.is_certified is False
    assert any("2021-04-12" in a for a in report.anomalies)


def test_band_mode_is_opt_in_strict_gap_still_default():
    """[WHY] Không có OHLC ⇒ vẫn dùng gap identity tuyệt đối (GAP_TOL). Band
    mode KHÔNG được âm thầm nới lỏng mặc định cho mọi caller cũ."""
    dates, raw, events, _lows, _highs = _synthetic_with_market_move()
    adj = _build_adjusted(dates, raw, events)
    report = reconstruct_raw_prices("SYN_STRICT", dates, adj, events=events)
    assert report.is_certified is False
    assert any("2021-04-12" in a for a in report.anomalies)


def test_band_mode_requires_complete_ohlc_pair():
    """[WHY] Missing > Fake: cho low mà thiếu high (hoặc ngược lại) ⇒ không đủ
    biên để phán quyết ⇒ ValueError, tuyệt đối không tự suy ra biên còn lại."""
    dates, raw, events, lows, highs = _synthetic_with_market_move()
    adj = _build_adjusted(dates, raw, events)
    adj_lows = _adjust_like(dates, raw, events, lows)
    adj_highs = _adjust_like(dates, raw, events, highs)
    with pytest.raises(ValueError):
        reconstruct_raw_prices("SYN_HALF", dates, adj, events=events, lows=adj_lows)
    with pytest.raises(ValueError):
        reconstruct_raw_prices("SYN_HALF", dates, adj, events=events, highs=adj_highs)
    with pytest.raises(ValueError):
        reconstruct_raw_prices(
            "SYN_LEN", dates, adj, events=events, lows=adj_lows[:-1], highs=adj_highs
        )


def test_band_mode_rejects_inverted_ex_session_range():
    """[WHY] low > high ở phiên GDKHQ = bar hỏng; dùng nó làm biên sẽ luôn
    FAIL (hoặc luôn PASS nếu đảo) — phải chặn bằng ValueError."""
    dates, raw, events, lows, highs = _synthetic_with_market_move()
    adj = _build_adjusted(dates, raw, events)
    bad_lows = list(_adjust_like(dates, raw, events, lows))
    adj_highs = _adjust_like(dates, raw, events, highs)
    bad_lows[7], adj_highs[7] = adj_highs[7] + 1.0, adj_highs[7]
    with pytest.raises(ValueError):
        reconstruct_raw_prices(
            "SYN_INV", dates, adj, events=events, lows=bad_lows, highs=adj_highs
        )


# ── [WHY] HOSE mid-cap (10k-50k VND, e.g. SMB): lưới tick là 50 VND.
#    Engine PHẢI hỗ trợ tham số tick=50.0, phục nguyên chính xác 100% bars.
def test_reconstruct_hose_50_tick_grid():
    dates = [date(2022, 9, 1 + k) for k in range(20)]
    # raw closes trên lưới 50 VND (lẻ 50đ: 35050, 35150)
    raw = [
        35_050.0, 35_150.0, 35_250.0, 35_100.0, 35_200.0,  # seg 0 (10 bars)
        35_350.0, 35_400.0, 35_300.0, 35_250.0, 35_350.0,
        33_350.0, 33_450.0, 33_400.0, 33_550.0, 33_600.0,  # seg 1 (10 bars, drop 2000đ)
        33_500.0, 33_700.0, 33_650.0, 33_750.0, 33_800.0,
    ]
    events = (CashEvent(ex_date=date(2022, 9, 11), cash=2_000.0),)
    adj = _build_adjusted(dates, raw, events)

    report = reconstruct_raw_prices("SYN_HOSE", dates, adj, events=events, tick=50.0)
    assert report.n_bars == len(raw)
    assert report.n_off_grid == 0
    assert report.is_certified is True
    for got, want in zip(report.raw_closes, raw):
        assert abs(got - want) < 1.0
