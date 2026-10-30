"""Test — kiểm định tính dừng (NQ-10 nhiệm vụ 2).

[WHY] Module `src/analysis/stationarity.py` tự viết lại ADF/Hurst/VR/OU/Engle–Granger
vì venv không có `statsmodels`. Code tự viết KHÔNG được tin chỉ vì "chạy không lỗi":
một ADF sai dấu, sai bậc tự do, hay một R/S đọc mức giá thay vì log-return vẫn trả
ra một con số trông rất hợp lý. Vì vậy mọi kiểm định ở đây được đối chiếu với quá
trình ĐÃ BIẾT TRƯỚC (random walk vs Ornstein–Uhlenbeck ↔ φ đã chọn ↔ half-life tính
tay), chứ không đối chiếu với kết quả thực nghiệm trên thị trường (thứ không có
"đáp án" để test).

[ROOT CAUSE] Trước 30/10/2026 dự án không có đường nào để nói "GSR có hồi quy trung
bình hay không" bằng số. Khi buộc phải có (NQ-10), cách nhanh nhất là tin vào một
con số duy nhất do ai đó đưa ra — đúng kiểu ngụy tạo thống kê mà nghị quyết cấm.

[INVARIANT] Test phải chứng minh được ba điều, không chỉ "hàm chạy được":
(1) Cổng ADF có KÍCH THƯỚC đúng (không bác bỏ Random Walk quá thường xuyên) và có
    LỰC (bác bỏ OU rõ ràng).
(2) Half-life trùng công thức giải tích của AR(1) đã dùng để sinh dữ liệu.
(3) Fail-closed: NaN, mẫu cụt, lệch độ dài ⇒ RAISE, không trả số thay thế.

Ghi chú: mọi ngưỡng dưới đây là ngưỡng của QUÁ TRÌNH MÔ PHỎNG (seed cố định), không
phải ngưỡng thị trường. Kết quả trên dữ liệu thật là `NON-DECISIONAL`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = None
for _par in [Path(__file__).resolve().parent.parent.parent] + list(
    Path(__file__).resolve().parent.parent.parent.parents
):
    if (_par / "AGENTS.md").exists() and (_par / "backend").is_dir():
        PROJECT_ROOT = _par
        break
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from src.analysis.stationarity import (  # noqa: E402
    ADF_CRITICAL_VALUES,
    EG_CRITICAL_VALUES,
    MIN_OBS,
    DiagnosticsUnavailable,
    adf,
    engle_granger,
    hurst_from_log_prices,
    hurst_rs,
    ou_half_life,
    schwert_lags,
    summarize_series,
    variance_ratio,
)

# [WHY] n = 2.000 xấp xỉ số phiên thật của chuỗi bạc sau backfill (2.721) — test trên
# cùng bậc độ lớn với dữ liệu sẽ dùng, để sai số hữu hạn mẫu không bị đánh giá thấp.
N = 2000
SIGMA = 0.01


def _random_walk(seed: int, n: int = N, sigma: float = SIGMA) -> np.ndarray:
    """Random walk mũ: log giá = cumsum nhiễu trắng (đúng H0 của ADF)."""
    rng = np.random.default_rng(seed)
    return np.cumsum(rng.normal(0.0, sigma, n))


def _ou(seed: int, phi: float = 0.9, n: int = N, sigma: float = SIGMA, mu: float = 4.5) -> np.ndarray:
    """Ornstein–Uhlenbeck rời rạc trên mức log, φ đã chọn ⇒ half-life tính tay được."""
    rng = np.random.default_rng(seed)
    y = np.empty(n)
    y[0] = mu
    for t in range(1, n):
        y[t] = mu + phi * (y[t - 1] - mu) + rng.normal(0.0, sigma)
    return y


# ───────────────────────── ADF ─────────────────────────


def test_adf_khong_bac_bo_random_walk():
    """[WHY] Đây là ca chống "vá quá tay" quan trọng nhất của ADF: nếu cổng bác bỏ
    Random Walk một cách hào phóng thì mọi chuỗi giá đều thành "mean-reversion" và
    chúng ta sẽ dựng chiến lược trên một tính chất không tồn tại.
    [WHY] Khẳng định ở mức 1% (−3,43) chứ KHÔNG phải 5%: một kiểm định 5% ĐÚNG vẫn
    bác bỏ ~5% mẫu Random Walk, nên khoá test theo 3 seed ở mức 5% là khoá theo may
    mắn (đo thật: seed 7 cho t = −2,87, lọt đuôi 5%). Kích thước thực tế ở mức 5%
    được kiểm riêng bằng `test_adf_kich_thuoc_monte_carlo_gan_5_phan_tram`."""
    for seed in (7, 21, 123):
        res = adf(_random_walk(seed))
        assert res["critical_values"] == ADF_CRITICAL_VALUES
        assert res["statistic"] > ADF_CRITICAL_VALUES["1%"], (
            f"seed={seed}: ADF t={res['statistic']:.2f} vượt cả mức 1% — sai số loại I"
        )
        assert res["n_obs"] > N - 40
        assert res["lags"] == schwert_lags(N)


def test_adf_bac_bo_ou_ro_rang():
    """[WHY] Ngược lại phải có LỰC: chuỗi OU φ = 0,9 (half-life 6,58 phiên) hồi quy
    về trung bình rất mạnh, ADF phải bác bỏ ở 1%. Nếu không, cổng vô dụng."""
    for seed in (7, 21, 123):
        res = adf(_ou(seed))
        assert res["statistic"] < ADF_CRITICAL_VALUES["1%"], (
            f"seed={seed}: ADF t={res['statistic']:.2f} không bác bỏ được OU φ=0,9"
        )
        assert res["beta"] < 0


def test_adf_kich_thuoc_monte_carlo_gan_5_phan_tram():
    """[WHY] Kiểm tra KÍCH THƯỚC của cổng (calibration), không chỉ 3 seed may mắn:
    trên 60 mô phỏng Random Walk, tỷ lệ bác bỏ sai ở mức 5% phải nằm trong khoảng
    hợp lý cho 60 lần thử (kỳ vọng 3; 0–9 vẫn chấp nhận được). Nếu vượt 9 thì
    công thức t-stat/bậc tự do đã sai (đo thực tế 30/10/2026: 11/200 = 5,5%)."""
    rejects = 0
    for i in range(60):
        if adf(_random_walk(10_000 + i, n=800))["reject_unit_root_5pct"]:
            rejects += 1
    assert rejects <= 9, f"ADF bác bỏ sai {rejects}/60 lần — cổng quá hào phóng"


def test_adf_deterministic_none_bo_hang_so():
    """[WHY] Residual Engle–Granger phải chạy ADF KHÔNG hằng số (trung bình 0 theo
    cấu trúc OLS). Test khoá lại: chế độ "none" phải cho df_resid lớn hơn đúng 1
    và t-stat khác đi — nếu ai đó lặng lẽ luôn thêm hằng số thì bảng EG bị so sai."""
    y = _ou(7)
    with_c = adf(y, deterministic="constant")
    without_c = adf(y, deterministic="none")
    assert without_c["df_resid"] == with_c["df_resid"] + 1
    assert without_c["statistic"] != with_c["statistic"]


# ───────────────────────── Hurst ─────────────────────────


def test_hurst_log_return_random_walk_quanh_0_5():
    """[WHY] R/S trên log-return của Random Walk cho ~0,555 (thiên lệch hữu hạn mẫu
    đã đo), nên dải chấp nhận là 0,45–0,70 chứ không phải "đúng 0,5". Nếu ai đó
    "sửa" công thức để ra đúng 0,500 thì đó là hiệu chỉnh sai, không phải cải tiến."""
    hs = [hurst_from_log_prices(_random_walk(500 + i))["hurst"] for i in range(5)]
    assert 0.45 <= min(hs) and max(hs) <= 0.70, hs


def test_hurst_ou_thap_hon_random_walk():
    """[WHY] So sánh TƯƠNG ĐỐI ổn định hơn ngưỡng tuyệt đối: OU φ=0,9 có log-return
    phản tương quan nên H phải thấp hơn hẳn RW trên cùng seed/n. Đo thực tế:
    RW 0,555 vs OU 0,345."""
    for seed in (7, 21):
        h_rw = hurst_from_log_prices(_random_walk(seed))["hurst"]
        h_ou = hurst_from_log_prices(_ou(seed))["hurst"]
        assert h_ou < h_rw - 0.1, (h_rw, h_ou)
        assert h_ou < 0.45


def test_hurst_tren_muc_gia_bi_cong_gan_1():
    """[WHY] Bằng chứng cho cảnh báo trong docstring: R/S trên MỨC giá (I(1)) cho
    H ≈ 1 — nếu không ghi lại thì một ngày nào đó sẽ có người "phát hiện" bạc có
    H = 0,99 và kết luận "siêu xu hướng" từ đúng cái lỗi này."""
    h_level = hurst_rs(_random_walk(42))["hurst"]
    assert h_level > 0.85


# ───────────────────────── Variance Ratio ─────────────────────────


def test_variance_ratio_random_walk_quanh_1():
    """[WHY] VR là kiểm tra độc lập với ADF cho cùng giả thuyết; nếu hai cổng lệch
    nhau hệ thống thì một trong hai bị sai. Dải ở đây rộng vì VR(q) với q lớn có
    phương sai chọn mẫu lớn (đo: 20 mô phỏng RW cho VR20 trung bình 0,95 nhưng
    seed 7 cho 1,23) — dải hẹp sẽ biến test thành "khoá seed", không phải khoá
    hành vi."""
    res = variance_ratio(np.exp(_random_walk(7)), qs=(5, 20))
    assert 0.85 <= res["horizon"][5]["vr"] <= 1.15
    assert 0.70 <= res["horizon"][20]["vr"] <= 1.40


def test_variance_ratio_ou_nho_hon_1():
    """[WHY] VR < 1 là chữ ký của mean-reversion. Nếu VR của OU lại ≥ 1 thì phép
    tính tổng trượt/normalize đã sai (đo: OU φ=0,9 cho VR5 ≈ 0,81, VR20 ≈ 0,43)."""
    res = variance_ratio(np.exp(_ou(7)), qs=(5, 20))
    assert res["horizon"][5]["vr"] < 0.95
    assert res["horizon"][20]["vr"] < 0.70


# ───────────────────────── OU half-life ─────────────────────────


def test_ou_half_life_trung_cong_thuc_giai_tich():
    """[WHY] Dữ liệu sinh ra với φ = 0,9 ⇒ half-life lý thuyết = ln2 / −ln(0,9) =
    6,579 phiên. Ước lượng phải nằm trong ±20%. Đây là phép kiểm DUY NHẤT xác nhận
    công thức half-life đúng dấu và đúng cơ số log (sai cơ số vẫn ra "số đẹp")."""
    expected = float(-np.log(2.0) / np.log(0.9))
    for seed in (7, 21):
        res = ou_half_life(_ou(seed))
        assert res["reason"] is None
        assert res["half_life_days"] == pytest.approx(expected, rel=0.2), res["half_life_days"]
        assert res["long_run_mean"] == pytest.approx(4.5, abs=0.5)


def test_ou_half_life_random_walk_tra_ve_none_kem_ly_do():
    """[WHY] Bất biến A: "không hồi quy" phải là None + lý do, KHÔNG phải 0 hay ∞.
    Nếu ai đó đổi thành `half_life or 0`, tầng trên sẽ chia cho 0 hoặc coi như
    "hồi quy tức thời" — đúng loại default "lành" mà AGENTS cấm."""
    res = ou_half_life(_random_walk(21))
    assert res["half_life_days"] is None
    assert res["reason"] == "NO_MEAN_REVERSION"
    assert res["beta"] >= 0


# ────────────────────── Engle–Granger ──────────────────────


def test_engle_granger_phat_hien_dong_tich_hop():
    """[WHY] Hai chuỗi dựng sẵn quan hệ tuyến tính + nhiễu dừng ⇒ phải bác bỏ H0
    "không đồng tích hợp". Dùng đúng bảng EG (5% = −3,34), không dùng bảng ADF
    (−2,86) — nếu lẫn bảng thì test này vẫn xanh nhưng kết luận trên dữ liệu thật
    sẽ "phát hiện" đồng tích hợp giả ở mức 5%."""
    x = _random_walk(11)
    rng = np.random.default_rng(11)
    y = 0.4 + 2.5 * x + rng.normal(0.0, 0.02, x.size)
    res = engle_granger(y, x)
    assert res["beta"] == pytest.approx(2.5, rel=0.02)
    assert res["critical_values"] == EG_CRITICAL_VALUES
    assert res["reject_no_cointegration_5pct"] is True


def test_engle_granger_hai_random_walk_doc_lap_khong_dong_tich_hop():
    """[WHY] Chống dương tính giả: hai random walk độc lập KHÔNG đồng tích hợp nhưng
    hồi quy OLS vẫn cho β "đẹp" (spurious regression). Ở đây t-stat residual phải
    không vượt bảng EG."""
    res = engle_granger(_random_walk(31), _random_walk(32))
    assert res["reject_no_cointegration_5pct"] is False


def test_engle_granger_lech_do_dai_thi_fail():
    """[WHY] Align theo ngày là bước dễ sai nhất (lệch 1 phiên do nghỉ lễ). Hàm không
    tự align; lệch độ dài phải RAISE thay vì cắt bớt im lặng — cắt bớt sẽ ghép sai
    cặp ngày và tạo residual giả."""
    with pytest.raises(DiagnosticsUnavailable, match="align"):
        engle_granger(_random_walk(41), _random_walk(41)[:-5])


# ────────────────────── Fail-closed ──────────────────────


def test_nan_khong_duoc_noi_suy():
    """[WHY] Một NaN lọt vào nghĩa là chuỗi có lỗ hổng; nội suy nó là biến "không
    biết" thành "biết". Phải raise, và thông điệp phải nêu vị trí để truy ngược."""
    y = _random_walk(51)
    y[500] = np.nan
    with pytest.raises(DiagnosticsUnavailable, match="500"):
        adf(y)


def test_mau_cut_thi_fail_kem_nguong():
    """[WHY] NQ-10 nêu rõ N > 1.000 mới được kiểm định; module chặn ở MIN_OBS=120
    (mức kỹ thuật tối thiểu). Thông điệp phải nêu cả số quan sát thực và ngưỡng để
    người vận hành biết còn thiếu bao nhiêu."""
    short = _random_walk(61, n=MIN_OBS - 1) + 100.0
    with pytest.raises(DiagnosticsUnavailable) as exc:
        adf(short)
    assert str(MIN_OBS - 1) in str(exc.value) and str(MIN_OBS) in str(exc.value)


def test_summarize_series_tra_so_do_kem_mau():
    """[WHY] Báo cáo phải nêu cỡ mẫu và biến động cạnh mọi tham số, để H = 0,6 trên
    150 quan sát không bị đọc như H = 0,6 trên 2.700 quan sát."""
    stats = summarize_series(np.exp(_random_walk(71)), label="XAGUSD")
    assert stats["n"] == N
    assert stats["label"] == "XAGUSD"
    # σ ngày = 1% ⇒ σ năm hoá ≈ 15,9%; dải rộng để không phụ thuộc seed
    assert 13.0 <= stats["ann_vol_pct"] <= 19.0
