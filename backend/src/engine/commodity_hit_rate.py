"""commodity_hit_rate.py — Bộ đo hit-rate KHÔNG CHỒNG LẤN (Tiên quyết B).

Spec: docs/SPEC_DUAL_TRACK_DECISION_2026-10.md — Addendum B.

VÌ SAO KHÔNG DÙNG CỬA SỔ TRƯỢT
-----------------------------
Cửa sổ trượt tạo mẫu TỰ TƯƠNG QUAN: với lag k, 120 mẫu trượt chỉ chứa ~120/(k+1)
quan sát ĐỘC LẬP. Đếm n theo kiểu trượt là "đếm ảo" → hit-rate trông chắc chắn hơn
thực tế. Bộ đo này lấy mẫu bước nhảy `step = k+1`.

SỬA LỖI NỘI TẠI CỦA SPEC (phát hiện khi triển khai)
---------------------------------------------------
Spec ghi W=120 phiên VÀ n≥30 mẫu độc lập — hai con số này MÂU THUẪN:
    k=4  ⇒ step 5  ⇒ 120/5 = 24 mẫu độc lập  (< 30)
    k=12 ⇒ step 13 ⇒ 120/13 ≈ 9 mẫu độc lập (< 30)
⇒ Không k với giá trị nào trong registry đạt 30 mẫu với W=120 (trừ k≤2).
Cách xử lý: `window` là NGƯỠNG TỐI THIỂU, hàm tự mở rộng cửa sổ tới khi đủ 30 mẫu
độc lập (nếu dữ liệu cho phép) và **báo cáo cửa sổ hiệu dụng** ra output.

NGUỒN DỮ LIỆU
-------------
- Driver: `macro_history_v2` (ưu tiên — có unit/asset_class) → fallback `macro_history`.
  ⚠️ Dữ liệu có NGÀY TRÙNG với giá trị lệch nhau (vd 2026-10-05 có 101.6 và 100.7)
  ⇒ dedupe theo trung vị và ĐẾM số ngày trùng để báo cáo, không im lặng.
- Giá mã: `daily_ohlcv` (is_stale=0), dùng cột `close` (giá thô — nhất quán với
  quy ước "store giữ giá thô"; RS ranker dùng adj_close cho mục đích khác).
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "backend" / "data" / "screener_cache.db"

MIN_INDEPENDENT_SAMPLES = 30
# Trần lookback: nhiều dữ liệu hơn = nhiều mẫu độc lập hơn = ước lượng chắc hơn.
# 1500 phiên ≈ 6 năm, phủ trọn lịch sử rổ dầu khí (2021→nay ~1435 phiên).
MAX_SESSIONS_LOOKBACK = 1500
STATUS_OK = "OK"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"
STATUS_NO_DRIVER = "NO_DRIVER_DATA"

VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_NOT_CONFIRMED = "NOT_CONFIRMED"
VERDICT_REJECT = "REJECT_TICKER"
VERDICT_INSUFFICIENT = "INSUFFICIENT_DATA"


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Khoảng tin cậy Wilson cho tỷ lệ nhị thức (bền ở n nhỏ).

    VÌ SAO CẦN: luật cũ của spec ("hit_rate < 0.50 ⇒ REJECT") dùng ĐIỂM ƯỚC LƯỢNG,
    nên với n=50 và hr=0.46 nó tuyên bố "dấu ngược" trong khi 0.46 không phân biệt
    được với tung đồng xu (SE ≈ 0.07 ⇒ 0.46 nằm trong ~0.6σ của 0.5). Nói quá bằng
    chứng cũng là một dạng bịa số.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class HitRateResult:
    symbol: str
    driver: str
    lag_k: int
    exposure_sign: int
    threshold: float
    status: str
    verdict: str
    hit_rate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    n_sliding: int = 0
    n_independent: int = 0
    requested_window: int = 0
    effective_window: int = 0
    measured_sign: int = 0
    driver_last_date: str | None = None
    driver_duplicate_dates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return self.verdict == VERDICT_CONFIRMED


def independent_n(n_sliding: int, k: int) -> int:
    """Số mẫu ĐỘC LẬP thật khi lấy bước nhảy (k+1) trên n_sliding quan sát."""
    if k < 0:
        raise ValueError("k phải >= 0")
    return n_sliding // (k + 1)


def _required_window(k: int, min_window: int) -> int:
    """Số phiên tối thiểu để có MIN_INDEPENDENT_SAMPLES mẫu độc lập.

    Mẫu tại i = k, k+(k+1), k+2(k+1), ... ; mẫu cuối cần i+k ≤ n−1
    ⇒ n ≥ (M−1)(k+1) + 2k + 1
    """
    return max(min_window, (MIN_INDEPENDENT_SAMPLES - 1) * (k + 1) + 2 * k + 1)


# ─────────────────────────────────────────────────────────────────────
# Nạp dữ liệu
# ─────────────────────────────────────────────────────────────────────
def load_driver_series(conn: sqlite3.Connection, driver: str) -> tuple[dict[str, float], list[str]]:
    """Trả về (map date→value, danh sách ngày bị TRÙNG giá trị khác nhau)."""
    rows: list[tuple[str, float]] = []
    for table in ("macro_history_v2", "macro_history"):
        try:
            rows = conn.execute(
                f"SELECT date, value FROM {table} WHERE variable = ? AND value IS NOT NULL ORDER BY date",
                (driver,),
            ).fetchall()
        except sqlite3.Error:
            rows = []
        if rows:
            break
    if not rows:
        return {}, []

    by_date: dict[str, list[float]] = {}
    for d, v in rows:
        by_date.setdefault(str(d), []).append(float(v))

    series: dict[str, float] = {}
    dupes: list[str] = []
    for d, vals in by_date.items():
        uniq = {round(x, 6) for x in vals}
        if len(uniq) > 1:
            dupes.append(d)
            series[d] = float(statistics.median(vals))  # trung vị — bền với outlier
        else:
            series[d] = vals[0]
    return series, sorted(dupes)


def load_symbol_sessions(conn: sqlite3.Connection, symbol: str, limit: int) -> list[tuple[str, float]]:
    rows = conn.execute(
        "SELECT date, close FROM daily_ohlcv WHERE symbol = ? AND COALESCE(is_stale, 0) = 0 ORDER BY date DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    return [(str(d), float(c)) for d, c in reversed(rows)]


def _asof_driver(series: dict[str, float], sorted_dates: list[str], target: str) -> float | None:
    """Giá driver 'như được biết tại' ngày target (PIT): bản ghi gần nhất <= target."""
    import bisect

    idx = bisect.bisect_right(sorted_dates, target) - 1
    if idx < 0:
        return None
    return series[sorted_dates[idx]]


def hit_rate_pure(
    closes: list[float],
    drivers: list[float | None],
    k: int,
    exposure_sign: int,
) -> tuple[int, int]:
    """Lõi toán THUẦN (không DB) — theo ĐÚNG định nghĩa lead-lag của spec.

    Mẫu tại chỉ số ``i`` với ``i >= k`` và ``i+k <= len-1``, bước nhảy ``k+1``:

        Δx = driver[i]   − driver[i−k]     (k phiên KẾT THÚC tại i — quá khứ)
        Δy = close[i+k]  − close[i]        (k phiên BẮT ĐẦU từ i — tương lai)
        agree ⟺ sign(Δy) == exposure_sign × sign(Δx)

    ⚠️ Sửa lỗi: bản đầu lấy Δx trên CÙNG cửa sổ với Δy ([i, i+k]) ⇒ đó là đo
    đồng biến ĐỒNG THỜI, không phải driver DẪN trước — lệch hẳn giả thuyết.

    Trả về ``(n_agree, n_total)``.
    """
    if k < 1:
        raise ValueError("k phải >= 1")
    n = len(closes)
    agree = total = 0
    for i in range(k, n - k, k + 1):
        x_now, x_prev = drivers[i], drivers[i - k]
        c_now, c_next = closes[i], closes[i + k]
        if x_now is None or x_prev is None or x_now == x_prev or c_next == c_now:
            continue
        total += 1
        if (c_next > c_now) == (exposure_sign * (x_now - x_prev) > 0):
            agree += 1
    return agree, total


# ─────────────────────────────────────────────────────────────────────
# Bộ đo
# ─────────────────────────────────────────────────────────────────────
def calculate_non_overlapping_hit_rate(
    symbol: str,
    driver: str,
    lag_k: int,
    window: int = 120,
    exposure_sign: int = 1,
    threshold: float = 0.60,
    db_path: Path | str = DB_PATH,
) -> HitRateResult:
    """Tỷ lệ đồng thuận dấu giữa Δdriver và Δclose tương lai, mẫu KHÔNG chồng lấn.

    agree_t = 1 nếu sign(close_{t+k} − close_t) == exposure_sign * sign(driver_t − driver_{t−k})
    """
    eff_window = _required_window(lag_k, window)
    res = HitRateResult(
        symbol=symbol,
        driver=driver,
        lag_k=lag_k,
        exposure_sign=exposure_sign,
        threshold=threshold,
        status=STATUS_INSUFFICIENT,
        verdict=VERDICT_INSUFFICIENT,
        requested_window=window,
        effective_window=eff_window,
    )

    conn = sqlite3.connect(str(db_path))
    try:
        series, dupes = load_driver_series(conn, driver)
        res.driver_duplicate_dates = dupes
        if not series:
            res.status = STATUS_NO_DRIVER
            res.notes.append(f"không có dữ liệu cho driver '{driver}'")
            return res
        sorted_dates = sorted(series)
        res.driver_last_date = sorted_dates[-1]

        sessions = load_symbol_sessions(conn, symbol, MAX_SESSIONS_LOOKBACK)
    finally:
        conn.close()

    # Cửa sổ hiệu dụng = số phiên THỰC SỰ dùng. Dùng trọn lịch sử (có trần) thay vì
    # cửa sổ vừa khít: cửa sổ khít làm vài mẫu bị lọc (driver đứng giá, phiên cuối
    # đồng giá) ⇒ tụt xuống 29/30 và bị fail-closed cắt oan dù dữ liệu còn đầy.
    res.effective_window = len(sessions)

    if len(sessions) < lag_k + 2:
        res.notes.append(f"chỉ có {len(sessions)} phiên — không đủ cho lag {lag_k}")
        return res

    # Mẫu KHÔNG chồng lấn: bước nhảy (k+1)
    # Driver căn theo TỪNG phiên (giá 'như được biết tại' phiên đó — PIT).
    drivers: list[float | None] = [_asof_driver(series, sorted_dates, d) for d, _ in sessions]
    closes = [c for _, c in sessions]

    n_agree, n_sliding = hit_rate_pure(closes, drivers, lag_k, exposure_sign)
    agrees_n = n_agree

    res.n_sliding = n_sliding
    res.n_independent = n_sliding

    if res.n_independent < MIN_INDEPENDENT_SAMPLES:
        res.status = STATUS_INSUFFICIENT
        res.verdict = VERDICT_INSUFFICIENT
        res.notes.append(
            f"chỉ {res.n_independent} mẫu độc lập (< {MIN_INDEPENDENT_SAMPLES}) "
            f"với cửa sổ hiệu dụng {eff_window} phiên, k={lag_k}"
        )
        return res

    res.hit_rate = agrees_n / n_sliding
    res.status = STATUS_OK
    lo, hi = wilson_interval(agrees_n, n_sliding)
    res.ci_low, res.ci_high = lo, hi

    # Quyết định dựa trên KHOẢNG TIN CẬY, không dựa trên điểm ước lượng:
    #   - chỉ tuyên bố "ngược dấu" khi CI nằm TRỌN dưới 0.50
    #   - chỉ xác nhận khi CI nằm TRỌN trên 0.50 VÀ đạt ngưỡng tiên nghiệm
    if hi < 0.50:
        res.verdict = VERDICT_REJECT
        res.measured_sign = -exposure_sign
        res.notes.append(
            f"CI 95% [{lo:.3f}, {hi:.3f}] nằm trọn dưới 0.50 ⇒ dấu đo được NGƯỢC tiên nghiệm ⇒ loại mã (KHÔNG lật dấu)"
        )
    elif lo > 0.50 and res.hit_rate >= threshold:
        res.verdict = VERDICT_CONFIRMED
        res.measured_sign = exposure_sign
    else:
        res.verdict = VERDICT_NOT_CONFIRMED
        res.notes.append(
            f"CI 95% [{lo:.3f}, {hi:.3f}] — chưa đủ bằng chứng để xác nhận (cần CI trọn trên 0.50 và hit-rate ≥ {threshold})"
        )
    return res


def measure_universe(threshold_fn=None, window: int = 120, db_path: Path | str = DB_PATH) -> list[HitRateResult]:
    """Chạy bộ đo cho toàn bộ universe đủ điều kiện (Tiên quyết D — đo, chưa phải OOS)."""
    from src.calibration.commodity_exposure import active_universe, hit_rate_threshold

    out: list[HitRateResult] = []
    for e in active_universe():
        th = threshold_fn(e) if threshold_fn else hit_rate_threshold(e)
        out.append(
            calculate_non_overlapping_hit_rate(
                symbol=e.symbol,
                driver=e.driver,
                lag_k=e.lag_mid_sessions,
                window=window,
                exposure_sign=e.prior_sign,
                threshold=th,
                db_path=db_path,
            )
        )
    return out
