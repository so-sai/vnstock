# Known Source Anomaly — IMF IFS "Changes in World Official Gold Reserves"

Checkpoint data-forensics: phát hiện trong quá trình ingest + PIT audit
vintage ladder (52 file `Changes_latest_as_of_{MMM}{YYYY}_IFS.xlsx`, 2020-01 → 2026-08).

## Anomaly chính

```
Publication:   2024-10-03  (file Changes_latest_as_of_Oct2024_IFS.xlsx)
Entity:        Russian Federation
Observation:   2024-07
Value:         -2335.85 t
```

### Forensic result

- Giá trị `-2335.85t` **chỉ tồn tại trong vintage này** (`spike_vintages=['2024-10-03']`).
- Tất cả các file khác (Sep2024 trước, Nov2024..Aug2026 sau): `''` (không report) hoặc `0.0`.
- Parser đã verify độc lập: đọc lại raw xlsx, cột date row index 4, header động —
  kết quả khớp, **không phải lỗi parse**.
- Phân loại: **SOURCE_ANOMALY** (giá trị cực lớn, spike 1-vintage).

### Cảnh báo diễn giải

**KHÔNG gọi đây là "Russia bán vàng".** Ta chỉ biết nguồn IFS ghi một thay đổi
âm cực lớn ở entity Russian Federation cho obs 2024-07. Chưa xác định được là
giao dịch thực, reclassification, hay data error. Zero-hallucination: không suy diễn.

## Các obs phụ bị ảnh hưởng cùng file Oct2024 (|value| < 500t)

Cùng spike file nguồn nhưng magnitude dưới ngưỡng SOURCE_ANOMALY → audit flag
`REVISION_LARGE`:

| obs | max_delta | do |
|---|---|---|
| 2024-05-31 | 170.59t | file Oct2024 (~-156t) |
| 2024-06-30 | 499.27t | file Oct2024 (~-472t) |
| 2024-08-31 | 67.83t | file Oct2024 |
| 2024-10-31 | 63.62t | file Feb2025 tail column (-2.88t) |

## Policy (đã áp dụng trong adapter + audit)

1. **Data layer:** giữ nguyên giá trị raw trong `gold_h2.db` — faithful to source.
   PIT contract trả lời "ngày 10/2024 analyst thực sự thấy gì?" = `-2335.85t`.
2. **Không tự sửa thành `0`/`NULL`** — đó là hindsight correction / look-ahead.
3. **Flag:** audit gắn `anomaly_type=SOURCE_ANOMALY` + `spike_vintages`; obs phụ
   mang `REVISION_LARGE`. CSV: `backend/data/reports/ifs_gold_reserve_audit.csv`.
4. **Không phân loại dữ liệu lớn nhất quán** (vd 2015-06 ~638t mọi vintage) là
   anomaly — đó là dữ liệu thật, chỉ `REVISION_LARGE` khi revision > 20t.

## Robustness branch (evaluation layer, KHÔNG đụng raw)

Khi đưa vào model:

```
IFS_raw                          — PIT resolver, nguyên giá trị
IFS_excluding_flagged_anomaly    — loại vintage SOURCE_ANOMALY/REVISION_LARGE
```

Chỉ tồn tại ở feature/evaluation layer. Raw/PIT truth không đổi.

## Nguồn gốc giá trị

- 52 file IFS vintage ladder: `backend/data/cache/wgc_cb/ifs_changes/`.
- `publication_date` = HTTP `Last-Modified` (`backend/data/cache/wgc_cb/ifs_changes/ifs_last_modified.csv`).
- Ingest: 13.475 dòng series `IFS_GOLD_RESERVE_CHANGE`, entity `GLOBAL`,
  source `IMF_IFS`, entity rule loại `Euro Area`/`Turkey*`/`Netherlands Antilles`.
- Audit: `backend/src/research/ifs_gold_reserve_audit.py`.