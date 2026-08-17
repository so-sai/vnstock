# Gold v0.2 Model Comparison — M1 vs M1+M2 (verdict note)

## Phạm vi
So sánh **Gold v0.1 (M1 monetary)** vs **Gold v0.2 (M1 + CB/IFS PIT)** bằng walk-forward
time-series logistic, KHÔNG random split. M2 = `CB_IFS_*` (central-bank reserve-demand)
từ IFS GLOBAL PIT-step (`_pit_latest_step`, latest vintage per obs, `publication_date <= t`).

Chỉ bổ sung duy nhất feature đã qua audit: **M2** (PROMISING H20). Loại khỏi v0.2:
- **E1 ETF Flows** → `NO STABLE SIGNAL` (không vintage ladder).
- **M3 China Reserve Shift** → `NOT TESTABLE` (thiếu Total Reserves monthly PIT-clean).

## Dữ liệu
- Panel usable: 2021-08-09 → 2026-02-24 (1143 ngày), CB_IFS_z phủ 1103 ngày.
- IS 2022-2024 (walk-forward block OOS); **2025-2026 chỉ DESCRIPTIVE** (đã nhiễm — cấm
  dùng chọn tham số). H20 primary; H60/H120 diagnostic.

## Kết quả walk-forward (OOS, intersection M1 & M1+M2)

### H20 (primary)
| Metric | M1 | M1+CB_IFS_z |
|---|---|---|
| AUC | 0.514 | **0.574** (Δ +0.060) |
| Accuracy | 71.1% | (base 70.2%) |
| Log-Loss | 0.6289 | **0.6225** |
| Brier | 0.2176 | **0.2096** |
| Spearman(prob) | — | **0.565** (không ≈1 → không redundant) |
| Hit-rate vùng tự tin cao | 0.696 (n=621) | **0.737** (n=735) |

Per-year ΔAUC (M2−M1): **2022:+0.101, 2023:+0.215, 2024:+0.090, 2025:+0.065** → dương
ổn định qua 4 năm (2025 chỉ descriptive, không phải bằng chứng chọn tham số).

### H60 (diagnostic)
| Metric | M1 | M1+CB_IFS_z |
|---|---|---|
| AUC | 0.505 | 0.629 (Δ +0.124) |
| Brier | 0.1610 | 0.1367 |

Per-year ΔAUC: 2022:+0.277, 2023:+0.258, 2024:+0.369, **2025:−0.294** → pooled dương
nhưng **2025 đảo dấu** → signal không ổn định dài hạn (chẩn đoán, không reject vì H20 thắng).

### H120 (diagnostic)
| Metric | M1 | M1+CB_IFS_z |
|---|---|---|
| AUC | 0.475 | 0.525 (Δ +0.051) |
| Log-Loss | 0.3480 | 0.4432 (xấu hơn) |
| Brier | 0.1006 | 0.1297 (xấu hơn) |

Chỉ có 1 năm hợp lệ (+0.151, 2023). Calibration H120 xấu hơn M1 → không dùng H120.

## Verdict
```
H20 (primary): ΔAUC +0.060, dương ổn định 2022-2025, logloss/brier cải thiện,
               spearman 0.565 (không redundant), hit-rate cao hơn → ✅ PROMISING
H60 (diagnostic): ΔAUC +0.124 nhưng 2025 đảo dấu −0.294 → ⚠ không ổn định
H120 (diagnostic): ΔAUC +0.051, logloss/brier xấu hơn → ✗ không dùng
```

**Kết luận:** Gold v0.2 = **M1 + CB_IFS_z tại H20** là representation khả dĩ nhất
cho Reserve-demand track (đồng thuận M2 audit). Không deploy production, không gọi là
"de-dollarization alpha". CB vẫn là feature nghiên cứu cho tới khi có thêm bằng chứng
forward-ladder.

## Hạn chế / boundary
1. **2025-2026 nhiễm** (đã dùng ≥6 cấu hình thí nghiệm khác) → H20 per-year 2025 chỉ
   descriptive; không dùng làm cơ sở chọn threshold.
2. H60/H120 không ổn định → v0.2 chỉ khả dụng tại chân sóng ngắn H20.
3. AUC 0.574 là modest — không đủ cho standalone; nên dùng như feature trong panel
   rộng hơn, không đơn lẻ.

## Khuyến nghị tiếp theo
1. Forward crawl IFS/TIC (chi phí thấp) → khi có vintage mới, chạy lại M2/v0.2.
2. Nếu mở Gold v0.3: xét interaction M2×regime (giống hướng M4 cũ) CHỈ khi có bằng
   chứng regime-conditional, không dùng để cứu H60/H120.
3. CB_IFS_z H20 có thể đưa vào feature universe cho các mô hình tổng hợp, kèm flag
   research-only (PIT hợp lệ).

## Artifacts
- Module: `gold_forecast_engine_v02.py`.
- Tests: `test_gold_forecast_engine_v02.py` (11 tests — PIT purity, deterministic,
  intersection-OOS, per-year AUC, calibration, spearman, hit-rate).
- CSV: `data/reports/gold_v02_model_comparison.csv` (gitignored qua `data/`).
- Kế thừa: `gold_forecast_engine_v01.py` (pipeline), `gold_cb_feature_audit.py`
  (`build_cb_features` + `_pit_latest_step`), `gold_h2_series.py` (contract layer).