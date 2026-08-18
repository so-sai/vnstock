# GVZ Source Audit — Bước 1 (discovery, chưa viết adapter/model)

## Mục đích
Theo quyết định user: mở lại **Ưu tiên 2 (GVZ)** chỉ ở mức **source discovery/audit**
trước khi viết adapter/test. Nếu GVZ có PIT semantics sạch + coverage đủ → ứng viên
hợp lý cho **Interval Gate** (thay/ổ sung σ20/EWMA20 lagged). KHÔNG mở M3 song song.

## Nguồn & phương pháp
- Series FRED **`GVZCLS`** (CBOE Gold Volatility Index, GVZ — implied vol 30d từ
  option COMEX).
- Fetch: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=GVZCLS` (endpoint CSV
  mở, cùng pattern TIC/FRED). Cached: `backend/data/cache/gvz/gvzcls_fred.csv`.
- Đối chiếu: `screener_cache.db` macro_history `GOLD_XAU` (đã cập nhật 2026-08-18).

## Kết quả audit

### 1. Tồn tại & lịch sử
- **Range: 2008-06-03 → 2026-08-14** (4,749 rows).
- value non-NA: **4,581** | NA: **168 (3.54%)**.
- min/max/mean: 8.88 / 64.53 / 18.95 — hợp lý cho implied vol chỉ số.

### 2. Observation date & missingness
- Observation date = ngày giao dịch CBOE (daily).
- **Top NA blocks nhỏ**: dài nhất chỉ **2 ngày** (2012-10-29→30, Hurricane Sandy);
  phần lớn NA = holiday Mỹ (2008-07-04, 2008-09-01, 2008-11-27, 2008-12-25,
  2009-01-01, ...) — là **ngày CBOE đóng cửa, KHÔNG phải data thiếu**.
- Per-year 2021→2026: 250-262 rows, non-NA ~250-254/năm → coverage đều, không có
  khoảng trống kéo dài.

### 3. Coverage vs panel hiện tại
- GOLD_XAU tới **2026-08-18**; GVZ non-NA tới **2026-08-14** (trễ ~2-4 ngày giao
  dịch — GVZ/FRED cập nhật chậm hơn giá close; không phải lỗi).
- Overlap (ngày có cả GOLD + GVZ): **1,348 ngày**.
- GOLD days 2025+: 409 | GVZ missing trong đó: **chỉ 4 ngày**
  (2025-01-09, 2025-07-04, 2026-08-17, 2026-08-18) — 3/4 là holiday/CBOE đóng,
  còn lại chỉ là FRED chưa kịp cập nhật 2 ngày gần nhất.

### 4. Semantics PIT (điểm mấu chốt)
- GVZ là **implied volatility hôm trước close** → giá trị ngày t phản ánh kỳ vọng
  thị trường TẠI close t, KHÔNG cần chờ future.
- Để PIT-clean: **publication_date = observation_date + 1 ngày giao dịch** (giá trị
  ngày t chỉ dùng được từ t+1 trở đi) — đúng chuẩn FRED daily release (giá trị
  ngày t được publish sau close t / trước mở t+1).
- Không cần vintage ladder (GVZ là daily index, không revision, khác IFS/TIC).
- Kiểm chứng phụ: value == prev liên tiếp chỉ 27 lần (ít, không phải series tĩnh).

## Verdict
**GVZCLS đạt source-discovery gate:**
1. ✅ Tồn tại, lịch sử 2008→nay, coverage tới sát hiện tại (trễ vài ngày do FRED).
2. ✅ Missingness thấp (3.54%), NA = CBOE holidays (không phải data gap).
3. ✅ Overlap 1,348 ngày với panel; 2025+ chỉ thiếu 4 ngày (holiday + 2 ngày chưa
   cập nhật).
4. ✅ PIT-clean được với `pub = obs + 1 trading day` (daily index, không revision).
5. ⚠️ CẢNH BÁO (chưa phải verdict): GVZ là **implied vol** (risk-premium, có thể
   tương quan mạnh với M1 hiện tại). Audit này KHÔNG chứng minh nó cải thiện
   interval OOS — đó là việc của Bước 2 (GVZ Feature Gate).

## Điều kiện cần cho bước tiếp theo (Bước 2)
Adapter `gvz_adapter.py` chỉ khi:
- lưu `publication_date = obs + 1 trading day` (không mặc định +7 — đó là M3/SAFE);
- lưu provenance (fred / GVZCLS / fetch date);
- dùng được PIT trong panel (ffill, min_periods) như macro series khác.

GVZ Feature Gate so sánh (model frozen, walk-forward ≤2024 → frozen, 2025-2026
descriptive): σ20 vs EWMA20 vs GVZ vs σ20+GVZ — metrics coverage 80/90, MAE/RMSE,
per-regime. Nếu không cải thiện OOS → đóng FAIL (như v0.3B).

## Artifacts
- Cache: `backend/data/cache/gvz/gvzcls_fred.csv` (4,749 rows, gitignored qua data/).
- Note này: `backend/src/research/gvz_source_audit_note.md`.
- KHÔNG viết adapter/model ở bước này. KHÔNG mở M3 (SAFE/FRED TRESEGCNM052N) đồng thời.