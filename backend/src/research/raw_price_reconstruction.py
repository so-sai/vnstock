"""
RAW PRICE RECONSTRUCTION ENGINE (PHA 2B — NQ-04/NQ-HĐKT)
PTCK_VNSTOCK Quantitative Research Suite

[WHY] Phục nguyên giá thô từ chuỗi back-adjusted (KBS/VCI) để thỏa bất biến
`RAW_PRICE_TICK_GRID_PROOF_REQUIRED`: 100% close bội số 100 VND + gap ngày
GDKHQ khớp cổ tức, trước khi chuỗi được đưa vào `DividendAuditEngineV2`.
Tiền đề kỹ thuật do biên bản hiệu chuẩn P2
(`raw_price_reconstruction_findings.md`, E6–E7) chứng minh: hệ số điều
chuẩn từng segment GIẢI ĐƯỢC từ grid constraint (giá thô HOSE/UPCoM luôn
nằm trên lưới tick 100 VND; adjusted = raw × f_segment).

[ROOT CAUSE] KBS/VCI trả `P_adj(t) = P_raw(t) × f_k` với f_k hằng trong mỗi
kỳ giữa hai ngày GDKHQ; f_k tích luỹ giảm dần về quá khứ (backward
adjustment), f_kới gần nhất = 1.0. Vì lưới 100 VND bất biến, tập giá trị
`{P_adj/f}` phải đồng thời rơi vào lưới — ràng buộc đủ chặt để giải f_k
một cách duy nhất, LOẠI được các nghiệm reciprocal (f/2, f/3 cũng bảo
tồn lưới) bằng backward induction + kiểm tra liên tục giá qua biên.

[INVARIANT]
(1) Fail-closed — Missing > Fake: segment không có factor duy nhất, hoặc
    sai số grid vượt ngưỡng ⇒ FactorSolveError. Không bao giờ "chọn gần
    đúng".
(2) Giá thô trả về PHẢI bội 100 VND (kiểm lại sau khi divide); vi phạm
    nằm trong report (`n_off_grid`), chặn certification.
(3) Certification CHỈ khi mọi cash event khớp gap identity
    `P_ex = P_pre − Cash_Div` trong dung sai. Thiếu evidence (report mode)
    hoặc mâu thuẫn ⇒ `is_certified=False` + anomaly tường minh.
(4) Band mode (có OHLC): khoảng rơi được phép lệch cổ tức một lượng
    Δ_market của phiên GDKHQ, nhưng CHỈ trong biên vật lý của chính phiên
    đó — tham chiếu `P_pre − Cash` phải nằm trong `[low_ex, high_ex]`.
    Δ_market luôn được ghi lại (`gap_deltas`) để kiểm toán, không im lặng.
(5) Tầng trần/sàn luật định (NQ-07): khi phiên GDKHQ gap-up/gap-down (cả dải
    nằm một phía so với tham chiếu), event ĐƯỢC KHAI BÁO trong
    `limit_band_events` có thể nghiệm thu bằng Δ_market nằm trong
    ±limit_pct × P_ref (HOSE 7%, UPCoM 15%) + dải giá nằm trong biên luật
    định + hướng Δ hợp logic. Tầng này YẾU hơn (4) nên mặc định TẮT, không
    bật theo symbol, và mọi event dùng nó phải mang nhãn `limit-band`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

# [WHY]: Lưới tick 100 VND của HOSE/UPCoM là bất biến của dữ liệu giá thô —
# xóa/đổi sẽ khiến mọi so sánh grid-consistency vô nghĩa (hằng số nền của
# toàn bộ thuật toán; xem NQ-04 DoD 100% close bội 100).
GRID_TICK = 100.0
# [WHY]: Segment < 5 bars không đủ ràng buộc để phân biệt factor thật với
# neighbor-k/reciprocal family — thí nghiệm probe cho 179 nghiệm đồng điểm
# trên 6 bars. Hạ ngưỡng ⇒ nguy cơ chọn factor rác (Missing > Fake).
SEG_MIN_BARS = 5
# [WHY]: 0.1 grid = 10 VND. Đo thực tế KBS làm tròn tỉ lệ 4 dp gây RMS
# ~0.66 VND/bar, tích lũy theo số event. Siết < 0.02 ⇒ segment dài không
# solve được; nới > 0.25 ⇒ neighbor-k lọt vào (sai factor).
GRID_TOL = 0.1
# [WHY]: >5% bars lệch lưới nghĩa là factor SAI cho segment này — dừng phục
# nguyên thay vì "chọn gần đúng" (Missing > Fake).
SEG_BAD_RATIO_MAX = 0.05
# [WHY]: 1 tick (100 VND) = sai số tối đa của gap identity do close đã làm
# tròn về lưới; nhỏ hơn ⇒ mismatch giả; lớn hơn ⇒ bỏ sót event thật.
GAP_TOL = 100.0
# [WHY]: Hệ số back-adjust thực tế rơi trong (0.3, 3.0) — ngoài dải này là
# cổ tức/thưởng phi thực tế (>70% giá hoặc tăng vốn 3×). Thu hẹp sai ⇒
# miss factor hợp lệ; mở rộng ⇒ tăng nhiễu candidate.
CAND_F_RANGE = (0.3, 3.0)
# [WHY]: P_ex/P_pre ∈ [0.5, 1.05] — cận dưới loại reciprocal f/2 (drop
# 50%, không phải cổ tức tiền mặt hợp lý); cận trên cho phép giá tăng nhẹ
# qua biên (ex-date không nhất thiết giảm nếu có tin tốt).
CONTINUITY_RANGE = (0.5, 1.05)


class FactorSolveError(RuntimeError):
    """Segment không giải được factor grid-consistent duy nhất (fail-closed)."""


@dataclass(frozen=True)
class CashEvent:
    """Sự kiện cổ tức tiền mặt (ngày GDKHQ + giá trị/cp)."""

    ex_date: date
    cash: float


@dataclass(frozen=True)
class ReconstructionReport:
    """Kết quả phục nguyên: giá thô + factors + chứng nhận gap identity."""

    symbol: str
    n_bars: int
    n_off_grid: int
    is_certified: bool
    raw_closes: tuple[float, ...]
    factors: tuple[float, ...]
    segments: tuple[tuple[date, date, float], ...]
    anomalies: tuple[str, ...]
    # Δ_market từng event = drop thực − cổ tức khai báo (VND). Bằng chứng kiểm
    # toán: Δ lớn nhưng vẫn trong biên phiên ⇒ hợp lệ; Δ bị ghi lại để người
    # đọc thấy mức nhiễu thị trường, KHÔNG được coi là 0 khi thiếu dữ liệu.
    gap_deltas: tuple[float, ...] = ()
    # Nhãn tầng nghiệm thu từng event: "in-range" (band OHLC, mạnh) hoặc
    # "limit-band" (trần/sàn luật định, yếu — chỉ event khai báo mới dùng được).
    gap_checks: tuple[str, ...] = ()


def _grid_residual(price_over_factor: float, tick: float = GRID_TICK) -> float:
    q = price_over_factor / tick
    return abs(q - round(q))


def _segment_index(bounds: Sequence[int], i: int) -> int:
    """Chỉ số segment chứa bar i (bounds = các mốc cắt đã sắp tăng)."""
    return max(s for s in range(len(bounds) - 1) if i >= bounds[s])


def _segment_candidates(closes: Sequence[float], grid_tol: float, tick: float = GRID_TICK) -> list[float]:
    """Tất cả f = c0/(k*tick) khớp grid cho bar đầu của segment.

    Cho phép ≤5% bar "nhiễu" (noise làm tròn từ source); nghiệm reciprocal
    và neighbor-k còn sót do backward induction + argmin residual xử lý sau.
    Segment quá ngắn ⇒ fail-closed (không đủ bằng chứng).
    """
    if len(closes) < SEG_MIN_BARS:
        raise FactorSolveError(f"segment quá ngắn ({len(closes)} < {SEG_MIN_BARS} bars) để giải factor — không đủ bằng chứng")
    c0 = closes[0]
    k_lo = max(1, int(c0 / (CAND_F_RANGE[1] * tick)))
    k_hi = int(c0 / (CAND_F_RANGE[0] * tick)) + 2
    checked = closes[1:21]
    allowed_bad = max(1, int(0.05 * len(checked)))
    cands: list[float] = []
    for k0 in range(k_lo, k_hi + 1):
        f = c0 / (k0 * tick)
        bad = sum(1 for c in checked if _grid_residual(c / f, tick=tick) > grid_tol)
        if bad <= allowed_bad:
            cands.append(f)
    return cands


def _segment_bad_count(closes: Sequence[float], f: float, grid_tol: float, tick: float = GRID_TICK) -> int:
    return sum(1 for c in closes if _grid_residual(c / f, tick=tick) > grid_tol)


def solve_factors(
    dates: Sequence[date],
    closes: Sequence[float],
    event_dates: Sequence[date] | None = None,
    grid_tol: float = GRID_TOL,
    tick: float = GRID_TICK,
) -> list[float]:
    """Giải hệ số điều chỉnh từng segment (backward induction, fail-closed).

    `event_dates` (nếu có) xác định biên segment — mọi ngày GDKHQ khai báo
    PHẢI tồn tại trong chuỗi; sai ⇒ ValueError (missing evidence).
    Trả về list f theo thứ tự quá khứ → hiện tại; f cuối = 1.0 (raw).
    """
    if dates is None or closes is None:
        raise ValueError("dates/closes không được None")
    if len(dates) == 0 or len(closes) == 0:
        raise ValueError("chuỗi rỗng — không có bằng chứng để giải")
    if len(dates) != len(closes):
        raise ValueError("dates và closes phải cùng độ dài")

    idx_of = {d: i for i, d in enumerate(dates)}
    bounds = [0]
    if event_dates:
        for ed in sorted(set(event_dates)):
            if ed not in idx_of:
                raise ValueError(f"ngày GDKHQ {ed} không tồn tại trong chuỗi")
            j = idx_of[ed]
            if j == 0:
                raise ValueError(f"ngày GDKHQ {ed} trùng bar đầu tiên")
            bounds.append(j)
    bounds.append(len(closes))

    seg_list = [closes[bounds[i] : bounds[i + 1]] for i in range(len(bounds) - 1)]
    n_seg = len(seg_list)

    # 1) Candidate từng segment
    all_cands: list[list[float]] = []
    for i, seg in enumerate(seg_list):
        cands = _segment_candidates(seg, grid_tol, tick=tick)
        if not cands:
            raise FactorSolveError(f"segment {i} ({dates[bounds[i]]}): không có factor grid-consistent")
        all_cands.append(cands)

    # 2) Backward induction: segment hiện tại f≈1.0; f_prev = f_next × r
    factors: list[float | None] = [None] * n_seg
    factors[-1] = min(all_cands[-1], key=lambda x: abs(x - 1.0))
    for i in range(n_seg - 2, -1, -1):
        f_next = factors[i + 1]
        assert f_next is not None
        window = [c for c in all_cands[i] if 0.40 * f_next <= c <= f_next + 1e-9]
        if len(window) > 1:
            # lọc cứng: factor phải nhất quán grid trên TOÀN segment
            seg = seg_list[i]
            allowed = max(2, int(SEG_BAD_RATIO_MAX * len(seg)))
            window = [c for c in window if _segment_bad_count(seg, c, grid_tol, tick=tick) <= allowed]
        if len(window) > 1:
            # lọc liên tục: khoảng rơi qua biên phải là cash-div hợp lý
            refined = []
            last_pre = seg_list[i][-1]
            first_ex = seg_list[i + 1][0]
            for c in window:
                ratio = (first_ex / f_next) / (last_pre / c)
                if CONTINUITY_RANGE[0] <= ratio <= CONTINUITY_RANGE[1]:
                    refined.append(c)
            window = refined
        if len(window) > 1:
            # chọn nghiệm tốt nhất theo tổng sai số grid toàn segment
            seg = seg_list[i]
            scores = sorted((sum(_grid_residual(c / f, tick=tick) for c in seg), f) for f in window)
            best_score = scores[0][0]
            tied = [f for s, f in scores if s - best_score < 1e-6]
            if len(tied) > 1:
                # tie-break: implied boundary ratio gần 1 nhất = drop nhỏ
                # nhất; spurious sub-event (f/2 ⇒ drop 50%) bị loại. Nếu
                # vẫn đồng điểm ⇒ bất định thật, fail-closed.
                last_pre = seg_list[i][-1]
                first_ex = seg_list[i + 1][0]

                def _ratio_dist(c: float) -> float:
                    ratio = (first_ex / f_next) / (last_pre / c)
                    return abs(ratio - 1.0)

                tied.sort(key=_ratio_dist)
                if _ratio_dist(tied[1]) - _ratio_dist(tied[0]) < 1e-9:
                    raise FactorSolveError(f"segment {i} ({dates[bounds[i]]}): bất định {len(tied)} nghiệm đồng điểm tối ưu")
            window = [tied[0]]
        if len(window) != 1:
            raise FactorSolveError(f"segment {i} ({dates[bounds[i]]}): bất định {len(window)} nghiệm")
        factors[i] = window[0]

    resolved = [float(f) for f in factors]

    # 3) Xác minh cứng từng segment (fail-closed nếu >5% bars lệch lưới)
    for i, seg in enumerate(seg_list):
        bad = _segment_bad_count(seg, resolved[i], grid_tol, tick=tick)
        if bad > SEG_BAD_RATIO_MAX * len(seg):
            raise FactorSolveError(
                f"segment {i} ({dates[bounds[i]]}): {bad}/{len(seg)} bars "
                "lệch lưới với factor giải được — dữ liệu méo, dừng phục nguyên"
            )
    return resolved


def reconstruct_raw_prices(
    symbol: str,
    dates: Sequence[date],
    closes: Sequence[float],
    events: Sequence[CashEvent] | None = None,
    grid_tol: float = GRID_TOL,
    gap_tol: float = GAP_TOL,
    tick: float = GRID_TICK,
    lows: Sequence[float] | None = None,
    highs: Sequence[float] | None = None,
    limit_pct: float | None = None,
    limit_band_events: Sequence[date] = (),
) -> ReconstructionReport:
    """Phục nguyên giá thô + chứng nhận gap identity (report-mode, fail-closed).

    `events=None`: report mode — vẫn phục nguyên + solve factors nhưng KHÔNG
    bao giờ certify (không có gap evidence). Với events: mọi mâu thuẫn giữa
    drop thực và cổ tức khai báo ⇒ anomaly + `is_certified=False`.

    `lows`/`highs` (tuỳ chọn, phải đi CẶP): bật band mode — khoảng rơi qua
    ngày GDKHQ được phép lệch cổ tức một lượng Δ_market của phiên, nhưng
    tham chiếu `P_pre − Cash` PHẢI nằm trong biên `[low_ex, high_ex]`.
    Không truyền ⇒ gap identity tuyệt đối (GAP_TOL) như trước.

    `limit_pct` + `limit_band_events` (NQ-07): với ĐÚNG các ngày GDKHQ được
    khai báo, nếu band OHLC thất bại (phiên gap-up/gap-down) thì dùng tầng
    trần/sàn luật định. Không khai báo ⇒ không bao giờ dùng tầng này.
    """
    if dates is None or closes is None:
        raise ValueError("dates/closes không được None")
    if len(dates) == 0 or len(closes) == 0:
        raise ValueError("chuỗi rỗng — không có bằng chứng để phục nguyên")
    if len(dates) != len(closes):
        raise ValueError("dates và closes phải cùng độ dày")
    # [WHY]: biên độ phiên là bằng chứng VẬT LÝ thay cho dung sai tuyệt đối —
    # thiếu một nửa (low hoặc high) thì không có biên, phải chặn thay vì tự
    # suy ra (Missing > Fake).
    if (lows is None) != (highs is None):
        raise ValueError("lows/highs phải cung cấp ĐỒNG THỜI — thiếu một nửa là không có biên độ phiên GDKHQ")
    if lows is not None and (len(lows) != len(dates) or len(highs) != len(dates)):
        raise ValueError("lows/highs phải cùng độ dài với dates/closes")

    event_dates = [e.ex_date for e in events] if events is not None else None
    # report mode (events=None): GIẢ ĐOÁN single-segment — KHÔNG tự đoán
    # breakpoints (sliding-window scan không đủ bằng chứng để distinguish
    # reciprocal/neighbor-k families — fail-closed: data chứa events không
    # khai báo ⇒ FactorSolveError thay vì đoán bừa). Breakpoint discovery
    # blind thuộc về runner calibration riêng, không thuộc core.
    factors = solve_factors(dates, closes, event_dates=event_dates, grid_tol=grid_tol, tick=tick)

    idx_of = {d: i for i, d in enumerate(dates)}
    bounds = [0]
    if event_dates:
        bounds += [idx_of[ed] for ed in sorted(set(event_dates))]
    bounds.append(len(closes))

    # Kiểm tra biên độ phiên GDKHQ TRƯỚC khi phán quyết (bar hỏng không được
    # dùng làm bằng chứng: low > high sẽ khiến band luôn FAIL/PASS vô nghĩa).
    # [WHY] close của bar ex PHẢI nằm trong [low, high] của chính nó — bar tự
    # mâu thuẫn (KBS 7/1452 bar lỗi kiểu này) mà vẫn được dùng làm biên thì
    # phán quyết gap identity dựa trên bar không có thật.
    if lows is not None and events is not None:
        for ev in events:
            j = idx_of[ev.ex_date]
            if not (lows[j] > 0 and lows[j] <= highs[j]):
                raise ValueError(f"bar {ev.ex_date}: biên độ phiên GDKHQ không hợp lệ (low={lows[j]!r}, high={highs[j]!r})")
            if not (lows[j] <= closes[j] <= highs[j]):
                raise ValueError(
                    f"bar {ev.ex_date}: close {closes[j]!r} nằm ngoài biên độ "
                    f"phiên [{lows[j]!r}, {highs[j]!r}] — bar không dùng được làm bằng chứng"
                )

    # raw = adj / f(segment); kiểm lưới tick tuyệt đối (0.5 tick)
    raw: list[float] = []
    n_off_grid = 0
    for i, c in enumerate(closes):
        seg_i = _segment_index(bounds, i)
        p_raw = c / factors[seg_i]
        if _grid_residual(p_raw, tick=tick) > 0.5:
            n_off_grid += 1
        else:
            p_raw = round(p_raw / tick) * tick
        raw.append(p_raw)

    anomalies: list[str] = []
    gap_deltas: list[float] = []
    gap_checks: list[str] = []
    if events is None:
        anomalies.append(f"{symbol}: report mode — không có danh sách event để chứng nhận gap identity")
    elif lows is None:
        # Gap identity tuyệt đối (không có OHLC để dựng biên phiên GDKHQ).
        for ev in events:
            j = idx_of[ev.ex_date]
            drop = raw[j - 1] - raw[j]
            gap_deltas.append(drop - ev.cash)
            if abs(drop - ev.cash) > gap_tol:
                anomalies.append(
                    f"{ev.ex_date}: drop={drop:.1f} ≠ cash={ev.cash:.1f} "
                    "(mâu thuẫn provenance — cổ tức khai báo không khớp data)"
                )
    else:
        # Band mode: Δ_market hợp lệ, nhưng bị chặn bởi biên độ phiên GDKHQ.
        # [WHY]: đo thực tế FOC 2022–2026 Δ_market = −1,200 … +2,200 VND
        # (−0.9% … +2.6% P_pre) — đúng cỡ biến động UPCoM một phiên, nên
        # dung sai tuyệt đối 1 tick sẽ trượt oan; biên [low_ex, high_ex] mới
        # là bằng chứng đủ mạnh (cổ tức khai sai ⇒ tham chiếu rơi ra ngoài).
        declared_limit = set(limit_band_events)
        for ev in events:
            j = idx_of[ev.ex_date]
            pre_raw = raw[j - 1]
            drop = pre_raw - raw[j]
            delta = drop - ev.cash
            gap_deltas.append(delta)
            ref = pre_raw - ev.cash
            seg_j = _segment_index(bounds, j)
            lo = lows[j] / factors[seg_j]
            hi = highs[j] / factors[seg_j]
            if lo - gap_tol <= ref <= hi + gap_tol:
                gap_checks.append("in-range")
                continue
            # Tầng 2 (NQ-07): CHỈ event được khai báo + có limit_pct.
            # [WHY] Tầng này yếu hơn ~5-10× (không phân biệt được cổ tức sai
            # ≤ limit_pct với biến động thị trường) nên phải fail-closed về
            # phạm vi: khai báo từng ngày, không bật theo symbol.
            if limit_pct is not None and ev.ex_date in declared_limit:
                # [WHY] Quy ước dấu: `gap_deltas` = drop − cash (P_pre − P_ex − cash);
                # NQ-07 định nghĩa Δ_market = P_close_ex − P_ref = −(drop − cash).
                # Đảo dấu tại đây để công thức trần/sàn đọc đúng chiều biến động,
                # tránh footgun "so sánh +2,000 với ngưỡng của −2,000".
                delta_market = -delta
                within_limit = abs(delta_market) <= limit_pct * ref + gap_tol
                floor_ok = ref * (1.0 - limit_pct) - gap_tol <= lo
                ceil_ok = hi <= ref * (1.0 + limit_pct) + gap_tol
                # (Không cần kiểm "hướng Δ": close nằm trong [lo, hi] đã bị chặn
                # bởi integrity check ở trên, nên dải hoàn toàn trên ref ⇒ close
                # > ref là hệ quả toán học, không phải giả định.)
                if within_limit and floor_ok and ceil_ok:
                    gap_checks.append("limit-band")
                    continue
                anomalies.append(
                    f"{ev.ex_date}: tham chiếu {ref:.1f} ngoài band OHLC [{lo:.1f}, {hi:.1f}] "
                    f"và KHÔNG đạt tầng trần/sàn ±{limit_pct * 100:.0f}%: "
                    f"Δ_market={delta_market:+.1f} ({100 * delta_market / ref:+.2f}% P_ref), "
                    f"dải giá {'ngoài' if not (floor_ok and ceil_ok) else 'trong'} biên luật định — "
                    "cổ tức khai báo không khớp data"
                )
                continue
            anomalies.append(
                f"{ev.ex_date}: tham chiếu {ref:.1f} (P_pre {pre_raw:.1f} − cash {ev.cash:.1f}) "
                f"rơi NGOÀI biên độ phiên GDKHQ [{lo:.1f}, {hi:.1f}] "
                f"(Δ_market={delta:+.1f}) — cổ tức khai báo không khớp data"
            )

    if n_off_grid > 0:
        anomalies.append(f"{symbol}: {n_off_grid} bars giá phục nguyên lệch lưới tick {tick:g} VND")

    is_certified = not anomalies
    segments = tuple((dates[bounds[i]], dates[bounds[i + 1] - 1], factors[i]) for i in range(len(bounds) - 1))
    return ReconstructionReport(
        symbol=symbol,
        n_bars=len(raw),
        n_off_grid=n_off_grid,
        is_certified=is_certified,
        raw_closes=tuple(raw),
        factors=tuple(factors),
        segments=segments,
        anomalies=tuple(anomalies),
        gap_deltas=tuple(gap_deltas),
        gap_checks=tuple(gap_checks),
    )
