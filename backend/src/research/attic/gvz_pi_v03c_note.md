# GVZ Prediction Interval v0.3C — PASS (calibration frozen)

## Mục đích
Sau GVZ Interval Gate PASS (e766a1d), chuyển PI sang production-form và khóa mọi
tham số. Guardrails tuân thủ nghiêm ngặt:
1. **μ invariant**: giữ nguyên pred = walk-forward M1 + CB_IFS_z (v0.2/v0.3), KHÔNG retrain.
2. **PIT calibration window**: chỉ fit q trên rows ≤ 2024-12-31 rồi freeze.
3. **2025-2026 descriptive**: chỉ đánh giá, cấm can thiệp tham số hồi tố.
4. **STOP-RULE**: fail → không tune tiếp interval trên lịch sử → Forward Paper Ledger.

## Công thức (production-form)
```
μ_t        = M1 + CB_IFS_z walk-forward           (bất biến, không đổi)
σ_dyn,t    = (GVZ_t / 100) * sqrt(20/252)          (PIT: pub = obs + 1 trading day)
score s_t  = |y_t − μ_t| / σ_dyn,t                 (calibration ≤ 2024)
q_α        = empirical quantile(s) → FROZEN
PI_t       = μ_t ± q_α * σ_dyn,t
```

## Kết quả (panel 2021-08-09 → 2026-02-25, test 2025+ n=269)
| metric | value | ngưỡng gate | check |
|---|---|---|---|
| q_80 / q_90 | 1.073 / 1.507 | — | freeze |
| coverage80 cal / test | 0.799 / **0.758** | [0.75, 0.85] | PASS |
| coverage90 cal / test | 0.899 / **0.887** | [0.86, 0.94] | PASS |
| midpoint MAE | 0.0399 (naive 0.0503) | < naive | PASS |
| midpoint RMSE | 0.0488 (naive 0.0628) | — | — |
| robust80 loại 1% / 5% | 0.767 / 0.798 | [0.65, 0.95] | PASS |
| regime80 | Q1 0.674, **Q2 0.625**, Q3 0.880 | ≥ 0.60 | PASS |
| frozen | max cal 2024-12-31 | ≤ 2024-12-31 | PASS |

**KẾT LUẬN: GVZ PI v0.3C CALIBRATION PASS** — freeze q80=1.073, q90=1.507.
Q2 regime (điểm yếu v0.3B 0.581) nay 0.625 — GVZ bám regime vol tốt hơn σ20 lagged.

## Descriptive (2025-2026, KHÔNG dùng để tune)
- year80: 2025 = 0.747, 2026 = 0.412 (n~30, gold vol-regime cao; chỉ mô tả).
- 2026 coverage thấp vẫn là dấu hiệu uncertainty extreme chưa khớp hết — cần
  theo dõi forward (mature dần), KHÔNG được retune q trên dữ liệu này.

## Bước tiếp theo
Freeze PI GVZ → **Forward Paper Gate** (ledger đã có, sẽ gắn PI GVZ vào forecast
hàng ngày). Nếu forward xác nhận → bắt đầu đánh giá investment utility. Nếu
forward không xác nhận → giữ kết luận "dự báo được expected return/magnitude,
chưa dự báo được uncertainty distribution đủ tốt".

## Artifacts
- `backend/src/research/gold_gvz_pi_v03c.py` + `tests/test_gold_gvz_pi_v03c.py` (9 tests).
- Report: `backend/data/reports/gold_gvz_pi_v03c.csv` (gitignored).
- Note này: `gvz_pi_v03c_note.md`.
- Phụ thuộc: `gvz_adapter.py` (PIT GVZ), `gold_conformal_interval_v03b` (conformal core).