# Gold Forward Paper Ledger — Gate khởi tạo (checkpoint d2aa5eb)

## Phạm vi
Forward Paper Gate: chứng minh forecast "ghi TRƯỚC khi biết outcome" có giá trị
ngoài mẫu hay không, model FROZEN (v0.2 direction logistic + v0.3 magnitude
Ridge, walk-forward PIT-safe, KHÔNG retrain/tune theo ledger).

Ledger: SQLite `backend/data/gold_paper_ledger.db` → `gold_paper_forecasts`,
append-only. Mỗi forecast_date lưu μ̂, P(up), P̂(t+20)=P_t·e^{μ̂}, interval 80%
descriptive (KHÔNG sizing), mature_date (t+20 phiên); khi mature → điền realized.
`is_backfill=1` = outcome đã biết trước khi tạo ledger; `is_backfill=0` = forecast
mới sau khi panel có ngày mới (outcome chưa xảy ra lúc ghi).

Module: `gold_paper_ledger.py` (`--init/--update/--report`), 8 tests.

## Trạng thái ledger (2026-08-18, panel 2021-04-05 → 2026-08-18)
- forecast generated: 1,102 ngày
- ledger: total=1,102 | matured=1,082 (backfill=1,082) | pending=20
- **fwd_matured = 0** — chưa có forecast THỰC SỰ forward nào matured (mọi ngày
  có outcome đã nằm trong backfill; 20 ngày cuối đang chờ mature).

→ Gate forward thực sự CHƯA bắt đầu có dữ liệu. Cần chạy `daily-update` + `ledger
--update` mỗi ngày để đẻ forecast mới (is_backfill=0); forecast đầu tiên sẽ mature
sau ~20 phiên.

## Gate metrics (n=1,082 matured, backfill OOS)

### Direction (so v0.2 reference: auc=0.574)
- acc = **70.1%**, auc = **0.578**, brier = 0.2310, logloss = 0.6622
- auc ≈ v0.2 (0.578 vs 0.574) — không suy giảm ✅

### Magnitude
- mae = **0.0380** vs naive 0.0400 vs expmean 0.0389 → thắng cả 2 baseline ✅
- sign_acc = **69.5%**, bias = **−0.0035** (rất nhỏ), spearman = **0.208**
- rolling20 decay: first 0.0382 → last 0.0367 (d = −0.0016, không decay) ✅

### Calibration P(up) vs realized frequency
| Bin | n | P_mean | freq |
|---|---|---|---|
| [0.0, 0.35) | 130 | 0.300 | **0.462** |
| [0.35, 0.5) | 89 | 0.405 | **0.067** |
| [0.5, 0.65) | 349 | 0.589 | **0.751** |
| [0.65, 1.0) | 514 | 0.721 | 0.667 |

→ **Calibration chưa tốt**: mô hình quá thận trọng ở vùng P thấp (0.30 → freq 0.46),
quá lạc quan ở vùng 0.35-0.65 (P 0.405 → freq 0.067; P 0.589 → freq 0.751). Brier
0.231 phản ánh điểm này. KHÔNG dùng P(up) tuyệt đối để sizing.

## Chẩn đoán
1. Backfill OOS (2022-2026): direction ổn định ngang v0.2, magnitude thắng naive/
   expmean, bias nhỏ, không decay — nhưng đây là lịch sử, KHÔNG phải bằng chứng
   forward mới.
2. Calibration direction kém ở vùng xác suất trung/thấp — cảnh báo dùng P(up)
   như xác suất thật.
3. Forward thực sự (fwd_matured=0) chưa có dữ liệu → verdict cuối CHƯA thể kết
   luận về khả năng dự báo giá tương lai.

## Verdict
**Chưa kết luận được.** Gate forward cần thời gian:
- Hiện tại: backfill OOS cho thấy forecast PIT-safe ổn định (direction ngang v0.2,
  magnitude thắng baseline, bias ~0, không decay) — xác nhận historical OOS skill.
- Forward thật (is_backfill=0): **0 matured** — cần chạy hàng ngày; ~20 phiên nữa
  mới có forecast đầu tiên matured.

Quy tắc vận hành (bắt buộc):
- Chạy `ptck.py daily-update` (hoặc `update_macro_data`) rồi `gold_paper_ledger.py
  --update` mỗi phiên để append forecast mới TRƯỚC outcome.
- KHÔNG retrain/tune theo kết quả ledger; đổi model = version mới, không sửa ledger cũ.
- Interval 80% chỉ descriptive — KHÔNG sizing/risk budgeting.
- 2025-2026 vẫn descriptive (không chọn tham số theo chúng).

## Artifacts
- Module: `gold_paper_ledger.py`.
- Tests: `test_gold_paper_ledger.py` (8 tests — schema, generate columns, calendar,
  sync backfill/pending + idempotent, mature_date=20d, expmean PIT, report no-crash).
- Ledger DB: `backend/data/gold_paper_ledger.db` (gitignored qua `data/`).
- Không sửa checkpoint v0.2/v0.3/v0.3A/v0.3B. `update_macro_data()` đã cập nhật
  panel tới 2026-08-18 trước khi tạo ledger.