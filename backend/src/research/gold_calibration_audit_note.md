# Gold H20 Calibration Audit (v0.3A) — Interval Integrity, READ-ONLY

## Phạm vi
Kiểm tra prediction interval của magnitude forecast (v0.3, checkpoint `af2c8a2`)
TRƯỚC khi mở v0.3 probabilistic price forecast. KHÔNG sửa model — μ̂ giữ nguyên.
Mọi adjustment chỉ ước lượng trên TRAIN, freeze cho OOS. KHÔNG tuning theo 2025.

Module: `gold_calibration_audit_v03a.py` (13 tests — logic Gaussian coverage,
gap detection, by-year/by-regime, std resid stats, rolling vol, extreme coverage,
width-vs-vol, empirical-z frozen PIT).

## Kết quả (walk-forward OOS, n=873)

### 1. Empirical coverage (Gaussian z)
| Nominal | z | Coverage | Gap |
|---|---|---|---|
| 80% | 1.2816 | **0.740** | −0.060 |
| 90% | 1.6449 | **0.859** | −0.041 |

→ **under-cover** như đã nghi ngờ (0.739 ở gate; confirm 0.740).

### 2. Coverage 80% by year
| Năm | Coverage |
|---|---|
| 2022 | 0.757 |
| 2023 | 0.768 |
| 2024 | 0.798 (gần chuẩn) |
| 2025* | **0.651** |
| 2026* | **0.688** |

→ IS (2022-2024) coverage khá hợp lý; under-coverage chủ yếu từ **2025-2026**
(regime vol cao, descriptive-only).

### 3. Coverage 80% by regime (trailing vol tercile)
| Regime | Coverage |
|---|---|
| Q1 (vol thấp) | 0.681 |
| Q2 | 0.717 |
| Q3 (vol cao) | 0.813 |

→ under-coverage **tập trung ở regime vol THẤP** (interval hẹp khi vol thấp → lệch
nhiều so với thực tế). Có dấu hiệu interval width không bám theo realized vol.

### 4. Standardized residual z=(y−pred)/resid_std
- mean=**0.220** (bias dương rõ — pred thường thấp hơn realized)
- std=1.125, skew=0.057, kurtosis=0.141
→ KHÔNG phải heavy-tail chính (kurtosis gần 0); vấn đề chính là **bias + spread
không theo kịp** chứ không phải tail Gaussian.

### 5. Raw residual
- n=873, skew=0.111, kurtosis=0.117, min=−0.1209, max=+0.1640.

### 6. Rolling residual vol (window=20) vs model resid_std
- ratio_mean=**0.662**, ratio_std=0.314 (rolling resid vol thấp hơn model std
  trung bình NHƯNG biến động lớn)
- model_std_std=0.0014 → **resid_std gần như hằng số** (homoskedastic, không phản
  ứng regime vol)
- corr(|resid|, resid_std)=**−0.084** → model std không dự báo nơi resid lớn.

### 7. Extreme-move coverage (|y| > q90 = 7.83% / 20 ngày, n=88)
| Interval | Coverage |
|---|---|
| 80% | **0.045** |
| 90% | **0.216** |

→ interval gần như KHÔNG phủ các extreme moves. Đây là điểm nguy hiểm nhất nếu
dùng interval để sizing.

### 8. Interval width vs realized |y|
- spearman = **−0.127** → width KHÔNG tăng khi realized |y| lớn; interval có xu
  hướng ngược (hẹp đúng lúc cần rộng).

### 9. Frozen adjustment (empirical |z| quantile từ TRAIN per block, áp OOS)
| Nominal | Coverage empirical | Coverage Gaussian | z_emp_mean |
|---|---|---|---|
| 80% | 0.755 | 0.740 | 1.330 |
| 90% | 0.849 | 0.859 | 1.612 |

→ z_emp_train (1.330/1.612) gần với Gaussian (1.2816/1.6449) vì **train residual
gần chuẩn**. Adjustment tĩnh từ train KHÔNG sửa được under-coverage (0.755 vs 0.80;
90% còn tệ hơn Gaussian). Nguyên nhân: vấn đề không phải tail trong train mà là
**spread OOS 2025-2026 tăng** + resid_std hằng số.

## Chẩn đoán
1. **Interval chưa calibrated** — không được ship P̂(50/80/90) như production.
2. Root cause KHÔNG phải heavy-tail train → **empirical quantile/Student-t tĩnh
   sẽ không đủ**. Vấn đề là **heteroskedasticity**: resid_std per block ~ hằng số
   (std 0.0014), không phản ứng regime vol; realized resid vol biến động gấp ~2×.
3. Có **bias dương** (mean z=0.220) — pred hệ thống thấp hơn realized ở OOS
   (chủ yếu 2025-2026).
4. Extreme-move coverage ~0.045 → interval vô nghĩa cho stress sizing.

## Verdict
**v0.3A interval audit: FAIL calibration.** Magnitude signal (MAE/RMSE −11%/−13%)
vẫn hợp lệ — KHÔNG phủ nhận. Nhưng prediction interval hiện tại:
- under-cover 6pp (80%);
- không phủ extreme moves;
- width không bám vol;
- adjustment tĩnh từ train không cứu được.

→ **Chưa mở v0.3 probabilistic price forecast production.** Cần Phase A-fix trước:
interval scale theo **dynamic vol** (GARCH-like / rolling realized vol trên train,
fit ≤2024, freeze) hoặc **conformal interval** với calibration set pre-2025 —
rồi re-audit. Chọn phương án sẽ dựa trên kết quả này (không phải con số 2025).

## Boundary
- KHÔNG thêm ETF/TIC data lúc này (M2 + magnitude đủ để kiểm calibration).
- KHÔNG tuning theo 2025; 2025-2026 chỉ descriptive.
- Không sửa model v0.2/v0.3; module audit READ-ONLY.

## Artifacts
- Module: `gold_calibration_audit_v03a.py`.
- Tests: `test_gold_calibration_audit_v03a.py` (13 tests).
- CSV: `data/reports/gold_calibration_audit.csv` (gitignored qua `data/`).
- Kế thừa: v0.3 `walk_forward_empirical_z` (đồng bộ logic Ridge block); v0.2/v0.1 constants.