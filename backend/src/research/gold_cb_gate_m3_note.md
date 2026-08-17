# Gate M3 — China Reserve-Allocation Shift (verdict note) — ĐÓNG: NOT TESTABLE

## Mục tiêu M3
- Feature đề xuất: `China_Treasury_Gold_Shift_t = ΔShare(Gold)_t − ΔShare(Treasury)_t`
  (reserve-allocation shift, hypothesis: China đổi vàng ↔ Treasury).
- Điều kiện PIT bắt buộc (contract user): denominator `TotalReserves_China,t` phải có
  cùng temporal/PIT contract — KHÔNG được lấy chuỗi hiện tại ghép ngược lịch sử.
- IS 2022-2024 (walk-forward OOS), 2025 chỉ descriptive. Gate: ΔAUC H20 > 0 + độc lập
  M2 → OPEN; ngược lại CLOSE/FAIL. Mọi kết quả KHÔNG deploy khi chưa có strict PIT.

## Work đã làm (đủ cho M3)
1. **B1 — IFS China gold Δ (tonnes)**: adapter riêng `IFS_CHINA_GOLD_RESERVE_CHANGE`
   entity `CHINA_MAINLAND`, 52 vintage files, pub = HTTP Last-Modified thật, 13,465 rows
   (2001-12 → 2026-06), 0 NEEDS_MANUAL, tests 10/10 PASS, contract validate PASS.
   → **Đủ PIT-clean cho numerator Gold.**
2. **B2 — TIC China Treasury holdings (USD B)**: adapter `TIC_CHINA_TREASURY_HOLDINGS`
   entity `CHINA_MAINLAND`, từ `mfhhis01.txt` current-vintage, 168 rows (2012-01 →
   2025-12), SLT-era 2012+ cross-check FRED `FORTREASPOS41408` maxdiff ≤ 0.05.
   → Numerator Treasury **chỉ LANE B** (publication synthetic day-15 month+2,
   `not_strict_pit=true`). Pre-2012 survey-era bị loại (lệch methodology).
   → **KHÔNG phải strict PIT.**

## Data gap — TotalReserves_China
Investigation (2026-08, toàn cache `backend/data/cache/`):
| Nguồn | Nội dung | Periodicity | PIT-clean monthly? |
|---|---|---|---|
| IFS `Changes_latest_as_of_*_IFS.xlsx` (52) | Chỉ gold Δ tonnes | Monthly | — (không có total) |
| TIC `mfhhis01.txt` | Treasury holdings | Monthly | Lane B (không strict) |
| WGC `fsapi_getPage.json` | `total_reserves`/`fx_reserves`/`gold_reserves` | **Annual YEAR-END only** (2019→2025) | ✗ current-vintage, không vintage ladder |
| WGC `fsapi_exportExcel.xlsx` | — | — | ✗ error stub (64B) |
| IMF SDMX / DataMapper API | — | — | Truy cập fail (HTTP 404) |

→ **KHÔNG có TotalReserves China monthly PIT-clean.**

## Quyết định (verdict user 2026-08)
- **Đóng M3: NOT TESTABLE.** Không build feature, không đổi tên denominator
  (`Gold+Treasury` chỉ là 2 tài sản quan sát được, không phải tổng dự trữ — ΔShare
  của nó suy biến thành 2ΔGoldWeight, không độc lập). Không mở diagnostic proxy
  annual (year-end current-vintage không tính ΔShare walk-forward hợp lệ).
- Giữ nguyên các adapter B1/B2 đã ingest (series riêng, có provenance) — phục vụ
  forward ladder và các hướng kiểm tra khác, KHÔNG dùng làm M3 feature.

## Lý do chặn (ghi rõ, tránh tái mở vội)
1. Denominator phải là tổng dự trữ ngoại hối thực (deposits, agency, EUR/JPY, SDR…),
   không phải `Gold + Treasury` quan sát được → dùng proxy tùy tiện làm hỏng thesis.
2. TotalReserves hiện chỉ có WGC annual year-end current-vintage — ghép vào lịch sử =
   look-ahead (vi phạm Zero-Hallucination / PIT boundary).
3. Kết quả M3 đẹp đến đâu cũng không trả lời đúng hypothesis ban đầu nếu denominator
   không đúng → giữ boundary.

## Mở lại khi nào
- Có nguồn **monthly PIT-clean TotalReserves_China** (vintage ladder thật, vd crawl
  SAFE/PBOC monthly releases hoặc IMF IFS monthly export có publication_date chứng
  minh được) → chạy lại Gate M3 walk-forward. Kèm điều kiện: vẫn phải ΔAUC H20 > 0 và
  độc lập M2 (redundancy/residual test vs `CB_IFS_z`) trước khi mở.

## Artifacts
- B1: `ifs_china_gold_reserve_adapter.py` + `test_ifs_china_gold_reserve_adapter.py`.
- B2: `tic_china_treasury_adapter.py` + `test_tic_china_treasury_adapter.py`.
- Cache: `data/cache/tic/mfhhis01.txt` (sha256 e4e4f7e7…), `data/cache/wgc_cb/ifs_changes/` (52 files), `ifs_last_modified.csv`.
- DB: `data/gold_h2.db` — thêm `IFS_CHINA_GOLD_RESERVE_CHANGE`/`CHINA_MAINLAND` (13,465) và `TIC_CHINA_TREASURY_HOLDINGS`/`CHINA_MAINLAND` (168); contract validate PASS.
- Sửa fixture B1: Jun file chỉ chứa obs ≤ data_to=M-2; B2: label row phải trên Country row, tháng trùng giữ cột đầu (khớp FRED).
