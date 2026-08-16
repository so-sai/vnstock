# Gate O1 — WGC ↔ IFS Overlap Diagnostic (verdict)

## Câu hỏi gate

> Khi cả hai measurement systems đã available tại cùng thời điểm PIT, chúng có
> chứa thông tin nhất quán về cùng underlying phenomenon hay không?

## Kết quả (backend/data/gold_h2.db, PIT-align theo publication_date)

### Coverage
- Overlap: **22 months** (2023-12 .. 2026-06)
- WGC-only: 0 — WGC blog chỉ publish các obs có IFS đồng kỳ
- IFS-only: 273 (IFS 2001-2026; WGC blog chỉ bắt đầu 2023-12)

### View 1 — Final observed (measurement agreement, as_of 2026-08-31)

| metric | IFS_raw | IFS_excl_source_anomaly |
|---|---|---|
| n | 22 | 21 |
| Pearson | 0.914 | 0.913 |
| Spearman | 0.891 | 0.882 |
| sign_agreement | 0.909 (20/22) | 0.905 |
| MAE | 9.15t | 9.30t |
| confusion | TP19 FP1 FN1 TN1 | TP18 FP1 FN1 TN1 |

### View 2 — First available (real-time signal)
- n=22, Pearson=0.921, Spearman=0.894, sign_agreement=0.955 (21/22), MAE=6.88t

### Anomaly sensitivity
- delta correlation raw→excl = **0.001 → stable** (anomaly -2335.85t KHÔNG làm
  thay đổi kết luận ở final view — chỉ 1 trong 22 obs)

### Revision stability (IFS, 294 obs ≥2 vintages)
- mean|first−final| = 28.86t, median = 14.59t, max = 623.3t, sign_flips = 34

### Revision-transient (rolling grid monthly)
- 1 mốc duy nhất: **2024-10-05** (file Oct2024) → Pearson=-0.702, sign_agree=0.2.
  File Oct2024 nhiễm 5 obs đồng thời (2023-12/2024-03/05/06/07), không chỉ spike
  >500t. Phục hồi ngay từ 2024-11-05 (r=0.794).

## Verdict: WGC ≈ IFS — YES (chặt chẽ, không phải two different phenomena)

1. **Hai view đều r≈0.91** — dù dùng final hay first-available, cả hai measurement
   systems chứa thông tin nhất quán về cùng underlying phenomenon
   (net central-bank gold demand).
2. **Measurement agreement > real-time signal đều cao.** Real-time (View 2)
   thậm chí sign_agree 0.955 — signal không bị "nhiễu" bởi revision.
3. **Overlap relationship KHÔNG anomaly-sensitive** ở final view (delta 0.001).
4. **Có 1 revision-transient** (2024-10-05, file Oct2024): tại thời điểm đó
   analyst thấy correlation đảo dấu (-0.70). Đây là data-integrity transient,
   KHÔNG phải bằng chứng thay đổi mối quan hệ — file Oct2024 là source anomaly
   (xem ifs_gold_reserve_anomaly_note.md). Phục hồi tức thì.
5. **MAE 9.15t (final) / 6.88t (first)** — hai hệ đo lệch magnitude ~7-9t/tháng
   nhưng hướng và xếp hạng khớp chặt.

## Khuyến nghị (cho Gate tiếp theo)

- WGC và IFS **có thể coi là complementary sources của cùng biến**, nhưng KHÔNG
  merge thành một series — mỗi source giữ vintage/coverage riêng.
- Khi đưa vào Gold Engine: dùng **View 2 (first-available)** để bảo toàn real-time
  signal; dùng IFS làm primary (coverage 2001-2026, có vintage ladder PIT),
  WGC blog làm secondary/validation (22 obs, lag thấp hơn).
- **Robustness branch IFS_excluding_flagged_anomaly** chỉ cần thiết ở rolling
  thời gian thực (2024-10 window), không ảnh hưởng final view.

## Phạm vi Gate O1 (đã giới hạn)

KHÔNG fit conversion, KHÔNG regression, KHÔNG calibrate, KHÔNG tạo composite
CB signal, KHÔNG đánh giá Gold return. Đây là data-integrity/representation gate.

## File & lệnh chạy

- Module: `backend/src/research/wgc_ifs_overlap.py`
- Tests: `backend/tests/test_wgc_ifs_overlap.py` (13 tests, no network)
- Chạy: `python -X utf8 src/research/wgc_ifs_overlap.py --db data/gold_h2.db`