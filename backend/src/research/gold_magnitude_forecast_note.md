# Gold H20 Magnitude Forecast Gate — verdict note

## Phạm vi
READ-ONLY gate: với M1+CB_IFS_z (v0.2, H20 direction AUC 0.574), model có dự báo
được ĐỘ LỚN forward log-return H20 không?

    Direction:  P(R20>0)  → đã có (v0.2, PROMISING)
    Magnitude:  E[logR20] → GATE NÀY

KHÔNG sửa checkpoint v0.2 (b96a03d). KHÔNG ghi Governor/replay/screener. KHÔNG săn
thêm data (ETF/TIC không quay lại — M2 là signal đủ để kiểm magnitude).

## Thiết kế
- Target: `logR20 = log(GOLD_{t+20}/GOLD_t)` (forward log-return, 20 ngày giao dịch).
- 3 baselines bắt buộc:
  1. **Naive**: E[logR20]=0.
  2. **Historical expanding mean** (PIT-safe): tại t chỉ dùng logR20 đã realized
     (j <= t−20) → `shift(h).expanding().mean()`.
  3. **M1 + CB_IFS_z Ridge** walk-forward expanding (MIN_TRAIN=250, block=20).
- Evaluation: walk-forward OOS. IS 2022-2024, 2025-2026 descriptive. H20 primary,
  H60/H120 diagnostic.
- Prediction interval: ±1.28·resid_std (80%) với resid_std từ train per block.

## Kết quả (walk-forward OOS)

### OOS full-sample
| Model | n | MAE | RMSE | Spearman | sign_acc | cov80 |
|---|---|---|---|---|---|---|
| naive | 1123 | 0.0357 | 0.0457 | — | — | — |
| expmean | 1084 | 0.0343 | 0.0434 | 0.174 | 0.625 | — |
| model | 873 | **0.0334** | **0.0415** | **0.232** | 0.729 | 0.739 |

### COMMON-OOS (n=873 — chỉ rows cả 3 model có giá trị, so sánh công bằng)
| Model | MAE | RMSE | Spearman | sign_acc |
|---|---|---|---|---|
| naive | 0.0376 | 0.0481 | — | — |
| expmean | 0.0348 | 0.0441 | 0.229 | 0.674 |
| **model** | **0.0334** | **0.0415** | **0.232** | **0.729** |

→ model tốt hơn naive **MAE −11.2%, RMSE −13.7%** trên cùng tập OOS.

### Sign consistency vs direction model (v0.2)
- direction model: acc = **73.9%** (auc 0.574).
- magnitude model sign_acc = **72.9%** (không suy giảm đáng kể; chênh −1.0pp).
- expmean sign_acc = 62.5% (thấp hơn hẳn).

### Per-year MAE (log return)
| Năm | naive | expmean | model |
|---|---|---|---|
| 2022 | 0.0363 | 0.0380 | **0.0303** |
| 2023 | **0.0269** | 0.0272 | 0.0316 (thua) |
| 2024 | 0.0333 | 0.0308 | **0.0294** |
| 2025* | 0.0491 | 0.0423 | **0.0401** |
| 2026* | 0.0708 | 0.0545 | **0.0375** |

IS: model thắng naive **2/3 năm (2022, 2024)**, thua **2023** (+0.0047). 2025/2026
descriptive (nhiễm) — model vẫn thấp hơn.

### Robustness (loại 1% cực trị |logR20|)
| Model | n | MAE | RMSE |
|---|---|---|---|
| naive | 1111 | 0.0345 | 0.0434 |
| expmean | 1073 | 0.0333 | 0.0415 |
| model | 864 | **0.0324** | **0.0397** |

→ thắng lợi không phụ thuộc vài extreme moves.

### Diagnostic H60/H120 (model regression only)
- H60: mae 0.0580, spearman 0.333, sign_acc 0.873.
- H120: mae 0.0908, spearman 0.663, sign_acc 0.838.
- Đánh giá: spearman tăng theo horizon nhưng đây là diagnostic; H60/H120 2025 vẫn
  đảo dấu như v0.2 → KHÔNG dùng cho production, không reject vì H20 thắng.

## Gate verdict
| Check | Kết quả |
|---|---|
| OOS MAE/RMSE < naive (common-OOS) | ✅ PASS |
| sign_acc >= direction − 0.02 | ✅ PASS (72.9% vs 73.9%) |
| thắng naive >=2/3 năm IS (2022-2024) | ✅ PASS (2/3) |
| robustness loại extreme vẫn thắng | ✅ PASS |
| coverage80 hợp lý (0.70-0.88) | ✅ PASS (0.739) |

## Kết luận
**MAGNITUDE PASS (H20)** — nhưng với 2 cảnh báo trung thực:

1. **2023 thua naive** (MAE 0.0316 vs 0.0269) → improvement không đều mọi năm;
   chỉ 2/3 năm IS thắng. Đủ cho gate pass (>=2/3) nhưng KHÔNG đủ gọi là ổn định
   tuyệt đối.
2. **sign_acc 72.9% ≈ direction 73.9%** → magnitude model giữ nguyên hướng của
   direction model, không suy giảm. Tốt, nhưng độ lớn dự báo là khiêm tốn
   (MAE 0.0334 = ~3.3% log-return/20 ngày).

→ Đủ điều kiện để mở **Gold Forecast Engine v0.3 — probabilistic price forecast**
dưới dạng research artifact: `P̂(t+20) = GOLD_t · exp(E[logR20] ± 1.28·resid_std)`.
KHÔNG production deploy, KHÔNG gọi là "gold price target".

## Hạn chế / boundary
1. Ridge trên 5 features — chưa thử nonlinear/quantile; không tuning theo 2025.
2. resid_std dùng train residual (homoskedastic giả định) → coverage 0.739 hơi
   thấp hơn nominal 0.80 (interval hơi hẹp). Có thể cần fat-tail điều chỉnh.
3. 2025-2026 nhiễm (descriptive) → mọi con số 2025 không dùng để chọn tham số.
4. Direction vẫn là tầng chính; magnitude là lớp bổ sung, không thay thế.

## Artifacts
- Module: `gold_magnitude_forecast_v03.py`.
- Tests: `test_gold_magnitude_forecast_v03.py` (9 tests — logR formula, expmean PIT,
  walk-forward deterministic + OOS>=MIN_TRAIN, metrics, robustness, interval coverage).
- CSV: `data/reports/gold_magnitude_forecast_gate.csv` (gitignored qua `data/`).
- Kế thừa: `gold_forecast_engine_v01.py` (pipeline), `gold_forecast_engine_v02.py`
  (`load_v02_panel`, `CB_PRIMARY`), `gold_cb_feature_audit.py` (CB features).