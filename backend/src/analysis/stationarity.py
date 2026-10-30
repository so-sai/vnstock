"""Kiểm định tính dừng / hồi quy trung bình cho chuỗi giá (ADF, Hurst, VR, OU, EG).

[WHY] NQ-10/NQ-HĐKT (30/10/2026) yêu cầu TRƯỚC khi dựng bất kỳ tham số nào cho bạc
phải trả lời được: log giá bạc là Random Walk hay hồi quy trung bình? Vàng và bạc có
đồng tích hợp (cointegrated) không? Tỷ lệ GSR có half-life đo được không, và vùng
65–70 có phải điểm cân bằng THỐNG KÊ không? Không có câu trả lời đo được thì mọi
"ngưỡng rẻ/đắt" chỉ là cảm tính — đúng lỗi tự sự mà Hội đồng đã bắt lỗi (báo "đừng
bắt dao rơi" mà vẫn gợi ý "bạc đang rẻ").

[ROOT CAUSE] Không có `statsmodels` trong venv (đo 30/10/2026: chỉ numpy 2.4.4,
pandas 3.0.2, scipy 1.18.0). Trước đây mọi kết luận về mean-reversion của dự án hoặc
là (a) nhập khẩu từ tài liệu, hoặc (b) đọc bằng mắt trên đồ thị — không tái lập được.
Viết lại đúng phần tối thiểu cần dùng (OLS t-stat + R/S + VR + AR(1)) là cách duy nhất
để con số kiểm lại được, và cũng để khai báo rõ giả định nào đang được dùng.

[INVARIANT]
(1) Fail-closed: chuỗi có NaN/inf, hoặc quá ngắn để hồi quy có bậc tự do dương ⇒
    RAISE `DiagnosticsUnavailable`. KHÔNG trả 0.0 / NaN im lặng, KHÔNG nội suy, KHÔNG
    cắt bớt im lặng.
(2) "Không hồi quy" phải là `None` + cờ lý do (`NO_MEAN_REVERSION`), không phải
    half-life = 0 hay = ∞ giả.
(3) Mọi giá trị tới hạn đều khai báo nguồn + bản chất tiệm cận; không có bảng tra
    nào được coi là chính xác cho N hữu hạn.
(4) Module này KHÔNG sinh tín hiệu giao dịch và KHÔNG đọc DB — chỉ nhận mảng số.
    Nhãn `NON-DECISIONAL` do tầng gọi (CLI) in ra.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

# [WHY] 120 quan sát: dưới mức này, cả ADF lẫn R/S đều cho khoảng tin cậy rộng đến
# mức không phân biệt nổi H = 0,5 với H = 0,7 (đo trên mô phỏng ở
# test_hurst_random_walk_quanh_0_5). Chặn ở đây thay vì trả một con số trông đẹp.
MIN_OBS = 120

# [WHY] Bậc tự do dư tối thiểu của hồi quy ADF: với df < 10, sai số chuẩn của β
# bất định tới mức t-stat đổi dấu chỉ vì thêm/bớt 1 độ trễ. Đây là ngưỡng kỹ thuật,
# không phải ngưỡng thống kê của kiểm định.
MIN_RESIDUAL_DF = 10

# [WHY] Giá trị tới hạn tiệm cận của ADF (Dickey–Fuller 1979, bảng không xu hướng,
# có hằng số; N → ∞): 1% −3,43 · 5% −2,86 · 10% −2,57. Dùng bảng TIỆM CẬN cho N
# hữu hạn là thiên BẢO THỦ theo nghĩa dễ bác bỏ H0 hơn? KHÔNG — với N nhỏ, t-stat
# thực có phân bố lệch trái hơn, nên dùng CV tiệm cận sẽ KHÓ bác bỏ hơn thực tế
# (thiên về "không kết luận"), tức vẫn fail-closed. Phải khai báo để người đọc
# không coi p-value là chính xác.
ADF_CRITICAL_VALUES = {"1%": -3.43, "5%": -2.86, "10%": -2.57}

# [WHY] Giá trị tới hạn Engle–Granger cho 2 biến, có hằng số (MacKinnon 1991, N=2):
# 1% −3,90 · 5% −3,34 · 10% −3,04. KHÁC hẳn bảng ADF thường vì residual là biến
# được ƯỚC LƯỢNG (bậc tự do bị tiêu hao khi ước lượng β đồng tích hợp) ⇒ dùng bảng
# ADF thường sẽ bác bỏ H0 "không đồng tích hợp" quá dễ, tạo đồng tích hợp giả.
EG_CRITICAL_VALUES = {"1%": -3.90, "5%": -3.34, "10%": -3.04}


class DiagnosticsUnavailable(ValueError):
    """Chuỗi không đủ điều kiện để kiểm định ⇒ không được trả một con số thay thế."""


def _clean(series: Sequence[float], *, label: str = "series", min_obs: int = MIN_OBS) -> np.ndarray:
    """Ép về mảng float 1-D, kiểm NaN/inf/độ dài. Không nội suy, không cắt lặng lẽ."""
    arr = np.asarray(series, dtype=float).ravel()
    if arr.size == 0:
        raise DiagnosticsUnavailable(f"{label}: chuỗi rỗng.")
    bad = ~np.isfinite(arr)
    if bad.any():
        first = int(np.flatnonzero(bad)[0])
        raise DiagnosticsUnavailable(
            f"{label}: có {int(bad.sum())} điểm không hữu hạn (NaN/inf), ví dụ vị trí {first}. "
            "Không nội suy — phải xử lý ở tầng dữ liệu trước."
        )
    if arr.size < min_obs:
        raise DiagnosticsUnavailable(
            f"{label}: {arr.size} quan sát < ngưỡng {min_obs} — mẫu cụt, mọi tham số là ngụy tạo thống kê."
        )
    return arr


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """OLS thường: trả (beta, covariance, df_resid). Raise nếu thiếu bậc tự do.

    [WHY] Dùng `solve` trên XᵀX và kiểm điều kiện ma trận, thay vì `pinv`: `pinv` sẽ
    lặng lẽ "giải" một hệ suy biến (ví dụ cột hằng số trùng cột khác) và trả hệ số
    vô nghĩa. Sai số chuẩn tính từ (XᵀX)⁻¹ là thứ duy nhất cần, nên không cần QR.
    """
    n, p = X.shape
    df_resid = n - p
    if df_resid <= 0:
        raise DiagnosticsUnavailable(f"Hồi quy {n} quan sát / {p} tham số ⇒ bậc tự do {df_resid} ≤ 0.")
    xtx = X.T @ X
    if np.linalg.matrix_rank(xtx) < p:
        raise DiagnosticsUnavailable("Ma trận thiết kế suy biến (hạng < số tham số).")
    beta = np.linalg.solve(xtx, X.T @ y)
    resid = y - X @ beta
    sigma2 = float(resid @ resid) / df_resid
    cov = sigma2 * np.linalg.inv(xtx)
    return beta, cov, df_resid


def schwert_lags(n_obs: int) -> int:
    """Số độ trễ ADF theo quy tắc Schwert (1989): ⌊12·(N/100)^0,25⌋.

    [WHY] Chọn độ trễ bằng AIC trên từng chuỗi sẽ khiến hai lần chạy khác nhau cho
    hai bậc trễ khác nhau (kết quả không tái lập khi dữ liệu mới về). Quy tắc công
    thức là hàm của N nên cùng N ⇒ cùng bậc trễ, so sánh được giữa các chuỗi.
    """
    return int(np.floor(12.0 * (max(n_obs, 1) / 100.0) ** 0.25))


def adf(
    series: Sequence[float],
    *,
    lags: int | None = None,
    deterministic: str = "constant",
) -> dict[str, Any]:
    """Augmented Dickey–Fuller: H0 = có nghiệm đơn vị (Random Walk).

    Mô hình hồi quy (deterministic="constant"):
        Δy_t = α + β·y_{t−1} + Σ_{i=1..k} γ_i·Δy_{t−i} + ε_t
    Kiểm định là t-stat của β. β < 0 ⇒ có lực kéo về ⇒ bác bỏ Random Walk khi
    t-stat nhỏ hơn giá trị tới hạn (miền bác bỏ nằm ở ĐUÔI TRÁI).

    [WHY] Phải có `deterministic` tường minh vì residual đồng tích hợp (Engle–Granger)
    có trung bình 0 theo cấu trúc OLS — thêm hằng số vào đó sẽ làm mất bậc tự do và
    đổi phân bố t-stat, khiến so với bảng EG sai.
    """
    if deterministic not in ("constant", "none"):
        raise DiagnosticsUnavailable(f"deterministic='{deterministic}' không hỗ trợ.")
    y = _clean(series, label="ADF")
    n = y.size
    dy = np.diff(y)
    k = schwert_lags(n) if lags is None else int(lags)
    if k < 0:
        raise DiagnosticsUnavailable("lags âm.")
    if dy.size - k < 1:
        raise DiagnosticsUnavailable(f"lags={k} quá lớn cho {n} quan sát.")

    rows = dy.size - k
    n_cols = 1 + (1 if deterministic == "constant" else 0) + k
    X = np.empty((rows, n_cols), dtype=float)
    col = 0
    if deterministic == "constant":
        X[:, col] = 1.0
        col += 1
    beta_col = col
    X[:, col] = y[k : n - 1]
    for i in range(1, k + 1):
        X[:, col + i] = dy[k - i : dy.size - i]
    target = dy[k:]

    beta, cov, df_resid = _ols(target, X)
    if df_resid < MIN_RESIDUAL_DF:
        raise DiagnosticsUnavailable(f"df_resid={df_resid} < {MIN_RESIDUAL_DF}.")
    se = float(np.sqrt(cov[beta_col, beta_col]))
    t_stat = float(beta[beta_col]) / se
    return {
        "statistic": t_stat,
        "beta": float(beta[beta_col]),
        "lags": k,
        "n_obs": int(rows),
        "df_resid": int(df_resid),
        "deterministic": deterministic,
        "critical_values": dict(ADF_CRITICAL_VALUES),
        "reject_unit_root_5pct": bool(t_stat < ADF_CRITICAL_VALUES["5%"]),
    }


def hurst_rs(
    series: Sequence[float],
    *,
    min_size: int = 8,
    max_fraction: float = 0.5,
    n_sizes: int = 12,
) -> dict[str, Any]:
    """Hurst exponent bằng R/S (rescaled range), hồi quy log(R/S) ~ log(size).

    ĐẦU VÀO PHẢI LÀ CHUỖI DỪNG THEO SAI PHÂN (log-return), không phải mức giá.
    Đọc kết quả: H ≈ 0,5 ⇒ không nhớ dài (Random Walk ở mức giá); H > 0,5 ⇒
    persistence; H < 0,5 ⇒ phản xu hướng/hồi quy trung bình.

    [WHY] Đây là cái bẫy đã đo được, không phải lý thuyết suông: khi đưa MỨC giá
    (I(1)) vào R/S, thuật toán lấy cumsum của chính nó thành I(2) nên H ước lượng
    bị cộng ~1. Đo 30/10/2026 trên 20 mô phỏng random walk n=2.000: R/S trên MỨC
    cho H = 0,989 (luôn "persistent"!), trên LOG-RETURN cho H = 0,555 ± 0,028. Vì
    vậy tầng gọi dùng `hurst_from_log_prices`, và nếu ai đưa mức giá trực tiếp thì
    con số H > 0,85 chính là dấu hiệu sai đầu vào.
    [WHY] R/S trên log-return của random walk cho 0,555 chứ không phải đúng 0,500 —
    đây là thiên lệch hữu hạn mẫu đã biết của công cụ, nên ngưỡng đọc phải là dải
    ±0,1 quanh 0,5 (không phải ±0,02).
    [WHY] `max_fraction=0,5` chứ không dùng toàn chuỗi: khi block chiếm gần hết chuỗi
    chỉ còn 1–2 block ⇒ R/S là biến ngẫu nhiên đơn lẻ, hồi quy log–log gần như nối
    hai điểm và cho H bất kỳ. Nửa chuỗi là mức phổ biến (Peters) và vẫn giữ ≥ 2 block.
    [WHY] Không dùng DFA: DFA cần chọn bậc đa thức và bỏ qua xu hướng cục bộ, thêm
    giả định không kiểm chứng được trong phạm vi NQ-10.
    """
    x = _clean(series, label="Hurst")
    n = x.size
    max_size = int(n * max_fraction)
    if max_size < min_size * 2:
        raise DiagnosticsUnavailable(f"Hurst: max_size={max_size} < {min_size * 2} ⇒ không đủ 2 thang đo.")
    sizes = np.unique(np.round(np.geomspace(min_size, max_size, n_sizes)).astype(int))
    xs: list[float] = []
    ys: list[float] = []
    block_counts: dict[int, int] = {}
    for size in sizes:
        n_blocks = n // size
        if n_blocks < 2:
            continue
        ratios = []
        for b in range(n_blocks):
            block = x[b * size : (b + 1) * size]
            deviate = np.cumsum(block - block.mean())
            rng = float(deviate.max() - deviate.min())
            sd = float(block.std(ddof=0))
            # [WHY] Block phẳng (sd = 0, ví dụ chuỗi bị "tráng" hằng số) không có
            # R/S xác định; bỏ block đó thay vì gán R/S = 0 (0 sẽ kéo H xuống giả tạo).
            if sd == 0.0:
                continue
            ratios.append(rng / sd)
        if len(ratios) < 2:
            continue
        xs.append(float(size))
        ys.append(float(np.mean(ratios)))
        block_counts[int(size)] = len(ratios)

    if len(xs) < 3:
        raise DiagnosticsUnavailable(f"Hurst: chỉ {len(xs)} thang đo hợp lệ (< 3) ⇒ không hồi quy được.")
    lx = np.log(np.asarray(xs))
    ly = np.log(np.asarray(ys))
    design = np.column_stack([np.ones_like(lx), lx])
    beta, _cov, _df = _ols(ly, design)
    fitted = design @ beta
    ss_res = float(((ly - fitted) ** 2).sum())
    ss_tot = float(((ly - ly.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "hurst": float(beta[1]),
        "intercept": float(beta[0]),
        "r2": r2,
        "sizes": xs,
        "mean_rs": ys,
        "blocks": block_counts,
        "interpretation": (
            "random-walk" if abs(float(beta[1]) - 0.5) <= 0.1 else ("persistent" if beta[1] > 0.5 else "mean-reverting")
        ),
    }


def hurst_from_log_prices(log_prices: Sequence[float], **kwargs: Any) -> dict[str, Any]:
    """Hurst của quá trình giá: chuyển mức log → log-return rồi mới chạy R/S.

    [WHY] Bọc đúng một phép biến đổi ở MỘT chỗ để không ai vô tình đưa mức giá vào
    `hurst_rs` (đo được: H trên mức của random walk = 0,989 ⇒ luôn kết luận sai là
    "persistent"). Trả thêm `n_returns` để báo cáo biết mẫu thực sự dùng là bao
    nhiêu (N mức giá ⇒ N−1 return).
    """
    prices = _clean(log_prices, label="Hurst(log price)", min_obs=MIN_OBS + 1)
    returns = np.diff(prices)
    out = hurst_rs(returns, **kwargs)
    out["n_returns"] = int(returns.size)
    out["input"] = "log-return"
    return out


def variance_ratio(
    series: Sequence[float],
    *,
    qs: Sequence[int] = (2, 5, 10, 20, 40),
) -> dict[str, Any]:
    """Variance Ratio (Lo–MacKinlay 1988): VR(q) = σ²(q·ngày) / (q·σ²(1 ngày)).

    VR ≈ 1 ⇒ Random Walk; VR < 1 ⇒ hồi quy trung bình; VR > 1 ⇒ persistence.

    [WHY] Thống kê z đi kèm CHỈ đúng dưới giả định đồng phương sai có điều kiện
    (homoskedastic). Bạc có σ ngày ~2,2% và các cụm biến động lớn (2020-03, 2026-01)
    nên z ở đây là THAM KHẢO, không phải p-value. Ghi rõ để không ai trích dẫn
    "p < 0,01" từ một giả định đã bị vi phạm.
    """
    x = _clean(series, label="VarianceRatio")
    r = np.diff(np.log(x)) if (x > 0).all() else np.diff(x)
    m = r.size
    mu = float(r.mean())
    var1 = float(((r - mu) ** 2).sum()) / (m - 1)
    if var1 == 0.0:
        raise DiagnosticsUnavailable("VarianceRatio: phương sai bậc 1 bằng 0.")
    out: dict[int, dict[str, float]] = {}
    for q in qs:
        q = int(q)
        if q < 2:
            raise DiagnosticsUnavailable(f"VarianceRatio: q={q} < 2.")
        if m - q + 1 < 2:
            continue
        window = np.convolve(r, np.ones(q), mode="valid")  # tổng trượt q phần tử
        m_adj = q * (m - q + 1) * (1.0 - q / m)
        if m_adj <= 0:
            continue
        sigma_q = float(((window - q * mu) ** 2).sum()) / m_adj
        vr = sigma_q / var1
        theta = 2.0 * (2 * q - 1) * (q - 1) / (3.0 * q * m)
        z = (vr - 1.0) / float(np.sqrt(theta))
        out[q] = {"vr": vr, "z_iid": z, "n_window": int(window.size)}
    if not out:
        raise DiagnosticsUnavailable("VarianceRatio: không q nào đủ cửa sổ.")
    return {"horizon": out, "n_returns": int(m), "var_1": var1}


def ou_half_life(series: Sequence[float], *, lags: int = 0) -> dict[str, Any]:
    """Half-life của quy trình Ornstein–Uhlenbeck, ước lượng qua AR(1) trên mức log.

    Δy_t = α + β·y_{t−1} + ε_t với φ = 1 + β; half-life = −ln2 / ln φ.

    [WHY] Trả `half_life_days = None` + cờ lý do khi β ≥ 0 (không có lực kéo về):
    gán 0 hoặc ∞ ở đó sẽ khiến tầng trên chia/so sánh ra số vô nghĩa. "Không biết"
    phải khác "0 ngày".
    [WHY] β ≤ −1 (φ ≤ 0) là dấu hiệu chuỗi đã bị lấy sai phân/thang giá, không phải
    OU — báo `OVER_DIFFERENCED` để người đọc nghi dữ liệu trước khi nghi thị trường.
    """
    y = _clean(series, label="OU")
    dy = np.diff(y)
    if lags:
        raise DiagnosticsUnavailable("OU half-life chỉ hỗ trợ AR(1) thuần (lags=0).")
    X = np.column_stack([np.ones(dy.size), y[:-1]])
    beta, cov, df_resid = _ols(dy, X)
    alpha, b = float(beta[0]), float(beta[1])
    se = float(np.sqrt(cov[1, 1]))
    t_stat = b / se
    phi = 1.0 + b
    if b >= 0.0:
        half_life = None
        reason = "NO_MEAN_REVERSION"
    elif phi <= 0.0:
        half_life = None
        reason = "OVER_DIFFERENCED"
    else:
        half_life = float(-np.log(2.0) / np.log(phi))
        reason = None
    long_run_mean = float(-alpha / b) if b < 0 else None
    return {
        "phi": phi,
        "beta": b,
        "alpha": alpha,
        "t_stat": t_stat,
        "half_life_days": half_life,
        "reason": reason,
        "long_run_mean": long_run_mean,
        "n_obs": int(dy.size),
        "df_resid": int(df_resid),
    }


def engle_granger(
    y: Sequence[float],
    x: Sequence[float],
    *,
    lags: int | None = None,
) -> dict[str, Any]:
    """Engle–Granger 2 bước: OLS y = α + β·x, rồi ADF trên residual (không hằng số).

    H0: KHÔNG đồng tích hợp. Bác bỏ khi t-stat residual < bảng EG (5% = −3,34).

    [WHY] Hai chuỗi phải được ghép theo ĐÚNG cặp ngày trước khi gọi (hàm này không
    tự align): align sai (lệch 1 phiên) sẽ tạo residual giả và có thể "phát hiện"
    đồng tích hợp không tồn tại. Tầng gọi phải truyền cặp đã join theo ngày.
    """
    ya = np.asarray(y, dtype=float).ravel()
    xa = np.asarray(x, dtype=float).ravel()
    if ya.size != xa.size:
        raise DiagnosticsUnavailable(f"EngleGranger: độ dài lệch ({ya.size} vs {xa.size}) — phải align theo ngày trước.")
    if not np.isfinite(ya).all() or not np.isfinite(xa).all():
        raise DiagnosticsUnavailable("EngleGranger: có NaN/inf — không nội suy.")
    design = np.column_stack([np.ones(ya.size), xa])
    beta, _cov, _df = _ols(ya, design)
    resid = ya - design @ beta
    res = adf(resid, lags=lags, deterministic="none")
    return {
        "alpha": float(beta[0]),
        "beta": float(beta[1]),
        "residual_adf": res,
        "critical_values": dict(EG_CRITICAL_VALUES),
        "reject_no_cointegration_5pct": bool(res["statistic"] < EG_CRITICAL_VALUES["5%"]),
        "residual_sd": float(resid.std(ddof=0)),
    }


def summarize_series(series: Sequence[float], *, label: str) -> dict[str, Any]:
    """Thống kê mô tả tối thiểu đi kèm mọi kiểm định (để báo cáo không phải tự đếm).

    [WHY] Đặt cạnh ADF/Hurst để người đọc thấy ngay mẫu dài bao nhiêu, biến động bao
    nhiêu — cùng một H = 0,6 trên 150 quan sát và trên 2.700 quan sát là hai mức độ
    tin cậy hoàn toàn khác nhau.
    """
    arr = _clean(series, label=label)
    logret = np.diff(np.log(arr)) if (arr > 0).all() else np.diff(arr)
    return {
        "label": label,
        "n": int(arr.size),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "last": float(arr[-1]),
        "ann_vol_pct": float(logret.std(ddof=1) * np.sqrt(252.0) * 100.0),
    }
