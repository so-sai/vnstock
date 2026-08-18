# GVZ Interval Gate (Bước 2) — PASS

## Bối cảnh
Sau v0.3B FAIL (coverage80 test 0.735, Q2 regime 0.581 — dynamic vol 20d lagged
không theo kịp regime vol shift), user chốt thứ tự:
**v0.3B PI FAIL → GVZ feature → Interval Gate → Forward Paper Gate → sizing**.
GVZ qua source-discovery (Bước 1, note trước), giờ kiểm định incremental power
(Bước 2). Không mở ETF, không mở M3 song song.

## Thiết kế (a priori, không chọn theo kết quả)
- 4 cấu hình σ_dyn: **sigma20** (PRIMARY baseline), **ewma20** (descriptive),
  **gvz** (CANDIDATE), **sigma20_gvz** = sqrt(σ20² + gvz²) (CANDIDATE combine).
- GVZ PIT-safe: `gvz_adapter` pub = obs + 1 trading day, align ffill vào panel.
- Scale a priori từ định nghĩa chỉ số: `sigma_gvz = (GVZ/100)*sqrt(20/252)` —
  GVZ ≈ annualized 30d implied vol % → đổi sang 20-day log-return vol. KHÔNG fit.
- Conformal: calibration OOS ≤ 2024-12-31 **FROZEN**, test 2025+ **pure OOS**.
- READ-ONLY; không sửa checkpoint v0.2/v0.3/v0.3B.

## Kết quả (panel v02 → 2026-02-25, test n=269)
| method | cov80 test | cov90 test | Q1 | Q2 | Q3 | robust80(1%/5%) |
|---|---|---|---|---|---|---|
| sigma20 (base) | 0.736 | 0.859 | 0.778 | 0.581 | 0.832 | 0.744/0.773 |
| ewma20 | 0.758 | 0.855 | 0.844 | 0.516 | 0.901 | — |
| **gvz** | **0.758** | **0.887** | 0.674 | **0.625** | 0.880 | 0.716/0.770 |
| sigma20_gvz | 0.754 | 0.852 | 0.674 | 0.625 | 0.872 | — |

Midpoint MAE/RMSE giữ nguyên (0.0399 / 0.0488 vs naive 0.0503 / 0.0628) — σ_dyn
chỉ đổi interval scale, không đổi midpoint (đúng thiết kế).

## Verdict
**GVZ INTERVAL GATE PASS** — candidate `gvz`:
- coverage-gap vs target cải thiện so với σ20: **80: 0.042 (base 0.064); 90: 0.013 (base 0.041)**;
- 6/6 checks PASS (coverage80 0.758 ∈ [0.75,0.85], coverage90 0.887 ∈ [0.86,0.94],
  midpoint MAE < naive, robust ∈ [0.65,0.95], regime80 ≥ 0.60, frozen ≤ 2024);
- **Q2 regime**: 0.581 → **0.625** (vượt ngưỡng 0.60) — đúng điểm yếu v0.3B.

`sigma20_gvz` FAIL (coverage90 0.852 < 0.86): GVZ + σ20 cộng variance làm interval
quá rộng tầm 90% — KHÔNG chọn; chỉ dùng `gvz` đơn lẻ.

## Lưu ý descriptive (2026 chỉ n=~30, không dùng để tune)
- year80 2026 vẫn thấp: gvz=0.412 (σ20 0.378). Số test-row 2026 ít + gold chuyển
  regime vol cao — mô tả, không phải lý do đóng/pass gate.

## Kết luận theo khung user
GVZ có **incremental info trên uncertainty** OOS. Bước tiếp theo (chờ user duyệt):
**Calibration lại với σ_dyn = gvz** → nếu ổn định → **freeze** → quay lại
**Forward Paper Gate**. Nếu forward không xác nhận → giữ kết luận
"dự báo được expected return/magnitude, chưa dự báo được uncertainty distribution".

## Artifacts
- `backend/src/research/gvz_adapter.py` + `tests/test_gvz_adapter.py` — PIT adapter.
- `backend/src/research/gold_gvz_interval_gate.py` + tests — gate.
- Cache: `backend/data/cache/gvz/gvzcls_fred.csv` (gitignored).
- Report: `backend/data/reports/gold_gvz_interval_gate.csv` (gitignored).
- Note này: `gvz_interval_gate_note.md`.