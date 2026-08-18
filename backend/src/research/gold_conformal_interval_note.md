# Gold H20 Conformal PI (v0.3B) — Phase A-Fix Audit Note

## Phạm vi
Sửa interval under-coverage (v0.3A: coverage80 0.740, heteroskedasticity, extreme-move 0.045)
bằng kiến trúc 2 tầng theo user:

    Forecast → Dynamic Volatility → Normalized Residual → Frozen Conformal
    Quantile → Prediction Interval

A1 Dynamic scale (PIT-safe, chỉ logR20 ĐÃ REALIZED j <= t−20):
  - `sigma20`: rolling std logR20 (window=20, lag=20) — **PRIMARY (a priori)**;
  - `ewma20`:  sqrt(EWMA span=20 của logR20²) — descriptive robustness.
  KHÔNG chọn method theo kết quả (cấm model-selection contamination).

A2 Locally-Adaptive Conformal:
  - calibration: OOS rows ≤ 2024-12-31 (n=605);
  - score s = |y − μ̂| / σ_dyn; q_α = empirical quantile → **FROZEN**;
  - test: 2025+ (n=268) — pure OOS, KHÔNG dùng để chọn q.

## Kết quả

### sigma20 (PRIMARY)
| | q | cal coverage | test coverage |
|---|---|---|---|
| 80% | 2.547 | 0.800 | **0.735** |
| 90% | 3.573 | 0.899 | **0.858** |

- midpoint MAE test 0.0399 < naive 0.0504 ✅
- robustness80 (loại 1%: 0.743, 5%: 0.772) — trong [0.65,0.95] ✅
- regime80: Q1 0.778, **Q2 0.581 (sụp)**, Q3 0.831 ❌
- year80: 2025 **0.730**, 2026 **0.361** ❌

### ewma20 (descriptive)
- coverage80: cal 0.800, test **0.757**; coverage90: cal 0.899, test **0.854**.
- mid MAE 0.0399 < naive 0.0504 ✅
- regime80: Q1 0.844, **Q2 0.516**, Q3 0.900 ❌
- year80: 2025 0.742, 2026 0.444 ❌

Cả 2 method cho kết luận GIỐNG NHAU → không phải artifact của riêng một σ_dyn.

## Gate verdict (primary sigma20)
| Check | Kết quả |
|---|---|
| coverage80 test ∈ [0.75, 0.85] | ❌ FAIL (0.735) |
| coverage90 test ∈ [0.86, 0.94] | ❌ FAIL (0.858) |
| midpoint MAE < naive | ✅ PASS (0.0399 < 0.0504) |
| robustness loại extreme | ✅ PASS |
| regime80 không sụp (≥0.60) | ❌ FAIL (Q2 0.581) |
| calibration frozen ≤ 2024 | ✅ PASS (max 2024-12-31) |

→ **CONFORMAL PI FAIL** (4/6 PASS).

## Chẩn đoán trung thực
1. **Conformal calibration trong-sample là chuẩn** (cal 0.800/0.899 đúng nominal) —
   cơ chế quantile hoạt động đúng.
2. **Nhưng test 2025-2026 vẫn under-cover**, dù đã dynamic-vol normalize. Coverage
   sụp mạnh ở 2026 (16 rows, |y| mean 0.0708 trong khi σ20 chỉ 0.0225 — gap ~3.1×).
3. **2025 (n=252) chỉ đạt 0.730** — dù đã chia σ_dyn. Như vậy dynamic vol 20d
   lagged KHÔNG bắt kịp regime vol của giai đoạn test: realized vol tăng trong khi
   σ_dyn (mean 0.0261) thấp hơn |y| mean 0.0491.
4. bias dương OOS (từ v0.3A) vẫn còn — interval tập trung quanh μ̂ bị lệch.

## Kết luận
**Phase A-Fix với cấu hình hiện tại: KHÔNG đạt chuẩn calibrated cho giai đoạn
tương lai.** Conformal + dynamic vol giúp ổn định calibration trong mẫu nhưng
chưa chứng minh được coverage trên dữ liệu thực sự ngoài mẫu.

KHÔNG phải là dấu hiệu để thêm feature/data. Đây là giới hạn chứng minh
**prediction interval reliability** cho giai đoạn 2025-2026 (contaminated, chỉ dùng
descriptive).

## Lựa chọn tiếp theo (chờ user quyết định)
1. **Chấp nhận hiện trạng**: Gold engine = direction (PROMISING) + magnitude
   (PASS) nhưng interval chưa calibrated → dùng làm research signal, KHÔNG làm
   probabilistic price forecast production. Kết luận v0.3 dừng ở đây.
2. **Điều chỉnh A1**: thử GARCH/EWMA khác, hoặc σ_dyn ước lượng từ daily returns
   thay vì logR20 — NHƯNG phải khai báo a priori trước khi nhìn test (tránh
   selection contamination); gate vẫn cứng như cũ.
3. **Chuyển hướng Forward Paper Gate** trước, dùng interval hiện tại như descriptive
   (không dùng sizing) — chứng minh điểm forecast + direction/magnitude trên thời
   gian thực trước khi ép interval.

Đề xuất của tôi: **phương án 3** — interval chưa sẵn sàng production, nhưng
forward paper validation của điểm forecast (P̂(t+20), P(up), E[logR20]) vẫn có thể
chạy ngay và là bước duy nhất tạo dữ liệu mới hoàn toàn không contamination.

## Artifacts
- Module: `gold_conformal_interval_v03b.py` (11 tests — PIT σ20/ewma20, conformal
  score, frozen quantile Gaussian coverage ~0.80, interval/by-year/by-regime/
  robustness).
- Tests: `test_gold_conformal_interval_v03b.py` (11 tests).
- CSV: `data/reports/gold_conformal_interval_v03b.csv` (gitignored qua `data/`).
- Không sửa checkpoint v0.2/v0.3/v0.3A. Không dùng 2025 để chọn q.