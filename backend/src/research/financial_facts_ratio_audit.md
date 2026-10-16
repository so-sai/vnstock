# HẠNG MỤC E — Audit tầng ratio (`valuation_scores`) + đề xuất quy chuẩn lọc

Ngày: **2026-10-15** · chế độ read-only · kho `backend/data/financial_facts.db`.
Nguyên tắc chỉ đạo: **gắn cờ / mặt nạ loại trừ, TUYỆT ĐỐI KHÔNG xoá cứng** (Missing > Fake).

---

## 1. Phân phối 4 chỉ số cốt lõi (n = 7.318 dòng `valuation_scores`)

| Ratio | n | Min | P1 | P5 | Median | P95 | P99 | Max | <0 | =0 |
|---|---|---|---|---|---|---|---|---|---|---|
| **PE** | 1.089 | 0,019 | 1,29 | 2,32 | **11,87** | 94,34 | 358,02 | **12.500** | 0 | 0 |
| **PB** | 1.770 | 0,353 | 0,50 | 0,67 | 2,03 | 6,51 | 14,94 | **79,66** | 0 | 0 |
| **PS** | 1.680 | 0,206 | 0,49 | 0,83 | 7,41 | 33,04 | 60,59 | 444,42 | 0 | 0 |
| **EV/EBITDA** | 1.126 | 0,135 | 3,58 | 8,47 | 35,52 | 156,28 | 575,64 | **3.386,93** | 0 | 0 |
| (tham chiếu) ROE | 1.653 | 0,000 | 0,00 | 0,02 | 0,15 | 0,36 | 0,56 | 0,88 | 0 | — |

Không có giá trị âm ở **bất kỳ** ratio nào ⇒ các nhánh "âm" mà kế hoạch dự phòng
(`NEGATIVE_EARNINGS`, `NEGATIVE_EQUITY`) hiện **chưa xuất hiện** — vì writer đã chặn sẵn
(`if eps > 0`, `if bvps > 0`) và bỏ qua trường hợp còn lại, tức **chúng bị ẩn đi** chứ không
được gắn nhãn. Đó là lý do cần cờ tường minh (mục 4).

## 2. ROOT CAUSE 40 ca PE < 2 — **một nguyên nhân hệ thống, không phải 40 ca cá biệt**

**Bằng chứng 1 — dấu vân tay số học:** với **39/40** ca, `PE_lưu / (price ÷ EPS) = 0,25` **chính xác**
(1/4). Ví dụ SHB 2024Q4: price 7.760, EPS 2.306 → đúng phải là 3,37; lưu là **0,841**.

**Bằng chứng 2 — code writer** (`src/financial/valuation_engine.py:266–274`):

```python
quarters = 4
if eps and eps > 0:
    ttm_eps = eps * (quarters / 1)   # ⚠ nhân 4 CHO MỌI KỲ
    result["PE"] = price / ttm_eps
```

⇒ Dòng `×4` là **vô điều kiện**, trong khi chuỗi EPS trong kho là **hỗn hợp chu kỳ**: phần lớn là
EPS *một quý*, nhưng các kỳ **Q2 (bán niên luỹ kế)** và **Q4 (cả năm luỹ kế)** là số **luỹ kế YTD**.
Nhân 4 lên số luỹ kế ⇒ PE bị chia ~4.

**Bằng chứng 3 — phân bố kỳ của 40 ca:** Q2 **16** · Q4 **10** · Q1 8 · Q3 5 ⇒ **Q2+Q4 = 65%**,
đúng chỗ EPS luỹ kế.

**Đã loại trừ (đo được):**
- ✗ Lợi nhuận đột biến/âm: **0/40** ca có `EPS ≤ 0` hoặc `NET_INCOME ≤ 0`.
- ✗ Lỗi đơn vị ×1000: **0/40** ca lệch theo bậc 1000 (tỷ lệ luôn đúng 0,25).
- ✗ "Cả cột PE sai 4×": nếu vậy median phải là **47,5** (bất khả thi cho TTCK VN) — nên **bác bỏ**;
  khiếm khuyết là **có điều kiện** (chỉ ở kỳ EPS luỹ kế).

⇒ **Kết luận:** không phải "40 ca bẩn" mà là **lỗi kỳ hạn (periodicity mismatch) trong công thức PE**,
triệu chứng lộ ra ở 40 dòng. Sửa ở **Hạng mục B** (tính lại từ `NET_INCOME` cấp báo cáo, không
annualize một chuỗi EPS mơ hồ), **E chỉ gắn cờ**.

## 3. Hai nhóm cực trị còn lại — cơ chế đã xác định

**PB > 20 (5 dòng): HVN 2021Q1/Q3/Q4 (79,66 · 43,67 · 29,57), FRT 2018Q1/Q2 (22,98 · 20,96).**
Cả 5 dòng **`BOOK_VALUE_PS = None`** ⇒ PB được tính bằng mẫu số khác. Kiểm `TOTAL_EQUITY`:
HVN 2021Q4 = **507 tỷ** trên quy mô tài sản hàng trăm nghìn tỷ ⇒ **vốn chủ sụp gần 0 sau COVID**
⇒ PB vọt lên 79 là **hệ quả mẫu số tí hon**, KHÔNG phải "đắt".
→ Đây đúng loại bạn dự đoán: **`DISTRESSED_EQUITY`, không phải tín hiệu định giá**.

**EV/EBITDA > 1.000 (6 dòng):** BSI 2022Q2 (2.109), KDH 2022Q1 (3.387), FRT 2020Q4 (1.655),
SSI 2020Q1 (1.358), NKG 2019Q1 (1.171)…
→ Nhưng **`EBITDA` KHÔNG phải metric nào trong kho** (0 dòng khớp `%EBITDA%`) ⇒ mẫu số
**không truy vết được** ⇒ không chỉ "EBITDA ≈ 0" mà còn là **lỗ hổng provenance**.
Cần: hoặc nạp `EBITDA` thành metric, hoặc gắn cờ `UNTRACEABLE_DENOMINATOR` và loại khỏi mọi bộ lọc.

## 4. Phát hiện thứ hai — **TỰ SỬA: KHÔNG phải look-ahead, mà là FIX BASIS có chủ đích**

> **ĐÍNH CHÍNH 2026-10-16.** Kết luận ban đầu của tôi (`SHARES_NOT_PIT` = look-ahead) **SAI**.
> Đọc kỹ writer: `valuation_engine.py:199-212` — `_get_latest_shares()` có docstring ghi rõ
> **BASIS-FIX 2026-08-19**: *"price từ screener_cache là split-adjusted (quy về số cổ phiếu hiện tại).
> BVPS fallback phải dùng shares HIỆN TẠI, nếu không P/B lịch sử bị lệch basis."*
> Dùng shares hiện tại cho BVPS là **ghép đúng basis**, không phải rò rỉ tương lai.

**Tái lập được con số của tác giả (HPG 2018Q1) — khớp tuyệt đối:**

| Cách tính | Shares dùng | BVPS | PB @giá 10.220 |
|---|---|---|---|
| Theo shares **PIT** (CC 2018Q1 = 15.171 tỷ) | 1.517,1 tr | 22.696 | **0,450** ← đúng con số *"sai 0,45"* trong docstring |
| Theo shares **HIỆN TẠI** (CC 2025Q2 = 63.963 tỷ) | 6.396,3 tr | 5.383 | **1,899** ← đúng con số *"đúng 1,90"*, và **trùng giá trị đã lưu (1,899)** |

**Bằng chứng giá ĐÃ được điều chỉnh:** HPG tỷ lệ shares NOW/PIT = **4,22×**; giá store 2018Q1 = **10.220**
≈ giá thô ~43.000 ÷ 4,22 ⇒ chuỗi giá đã back-adjust về số cổ phiếu hiện tại.
⚠️ Nhưng **`adj_close == close` trên toàn bộ 1.578.932 dòng** (0 dòng lệch >5%) ⇒ **hệ thống KHÔNG
ghi lại basis điều chỉnh ở đâu cả**; nó phụ thuộc hoàn toàn vào việc *nhà cung cấp* trả giá đã điều chỉnh.
Đó mới là rủi ro thật: đổi nguồn ⇒ lệch basis âm thầm, và không có cột nào để phát hiện.

**Hai câu hỏi provenance CHƯA giải được (chặn B):**
1. **Giá mà writer dùng ≠ `daily_ohlcv.close` cùng kỳ.** HPG 2018Q1: giá lưu 10.220 vs close 8.070;
   PNJ 2018Q1: giá lưu 23.050 vs close 36.480. ⇒ Phải xác định **nguồn giá của `valuation_engine`**
   (bảng nào, ngày nào, basis nào) trước khi tính lại (B).
2. Nếu một mã có sự kiện quyền mà nguồn giá **không** back-adjust (hoặc adjust một phần), PB của mã đó
   sẽ lệch một cách im lặng; cần **canary test** kiểu HPG để phát hiện.

## 4b. Thiết kế Hạng mục C — `shares_history` (PIT) + khai báo basis

**Tin tốt: không cần crawl mới.** Kho **đã có `CHARTER_CAPITAL` theo từng kỳ** (VND, mệnh giá 10.000đ),
độ phủ **687 → 744 mã/kỳ từ 2018Q1 đến 2025Q2** — trùng đúng cửa sổ dữ liệu dày của `financial_facts`
(rơi xuống 88/78/70/69 mã từ 2025Q3 ⇒ cùng vùng tối đã biết).

| Thành phần | Thiết kế đề xuất |
|---|---|
| Bảng mới `shares_history` | `(symbol, period, effective_date, shares_outstanding, source_metric, basis, revision)` |
| Nguồn | `CHARTER_CAPITAL / 10.000` (đúng công thức writer đang dùng) |
| PIT | `effective_date` **kế thừa Hạng mục A** (+30/+45/+90 ngày) ⇒ không bịa lịch mới |
| `basis` | `'CHARTER_PIT'` (số cp tại kỳ) — khai báo tường minh để không trộn với basis giá |
| Cờ | nếu `basis_price = ADJUSTED_TODAY` thì cặp BVPS **phải** dùng shares-now; nếu `RAW` thì dùng shares-PIT |
| Canary | tái lập test HPG 2018Q1 (PB 0,450 vs 1,899) sau mỗi lần đổi nguồn giá/dữ liệu |
| Giới hạn | 2025Q3+ chỉ 70-88 mã ⇒ C **không** cứu được vùng tối; phải chờ D (ingestion) |

**Kết luận điều chỉnh cho roadmap:** C **vẫn cần** (để có mẫu số lịch sử + khai báo basis + canary),
nhưng **không còn là "điều kiện tiên quyết để sửa look-ahead"** — vì look-ahead đó không tồn tại.
Việc chặn B thật sự là **câu hỏi provenance #1 ở trên (nguồn giá của writer)**.

> **DẤU VẾT — kết luận SAI ban đầu (giữ lại, KHÔNG dùng):** *"`shares_now` dùng cho mọi kỳ là
> look-ahead; cờ `SHARES_NOT_PIT`; Hạng mục C là điều kiện tiên quyết để sửa lỗi này."* —
> Sai vì bỏ qua việc chuỗi giá **đã back-adjust**; xem đính chính + phép tái lập HPG ở §4.

## 5. Đề xuất quy chuẩn gắn cờ (Bounding Spec) — additive, không xoá

Thêm vào tầng kết quả (nơi ratio sống), **không** sửa/xoá `ratio_value`:

| Cột mới | Kiểu | Ý nghĩa |
|---|---|---|
| `is_clean_metric` | INTEGER 0/1 | 1 = được đưa vào xếp hạng/mô hình |
| `anomaly_flags` | TEXT | danh sách mã lỗi, phân tách bằng `;` |
| `effective_date` | TEXT | kế thừa từ Hạng mục A (PIT) |

**Mã cờ đề xuất:**

| Mã cờ | Điều kiện | Xử lý |
|---|---|---|
| `EXTREME_LOW_PE` | `PE < 2.0` | loại khỏi xếp hạng (mẫu số EPS nghi vấn) |
| `PERIODICITY_MISMATCH_EPS` | `PE < 2.0` ∧ kỳ ∈ {Q2,Q4} | loại + đánh dấu cần tính lại (B) |
| `NEGATIVE_EARNINGS` | `EPS ≤ 0` ∨ `NET_INCOME ≤ 0` | loại (hiện đang bị **bỏ lặng**, không lưu) |
| `DISTRESSED_EQUITY` | `TOTAL_EQUITY ≤ 0` ∨ (BVPS/PB suy ra < 500đ) | loại khỏi "đắt/rẻ"; chuyển sang nhóm rủi ro |
| `NEAR_ZERO_DENOMINATOR` | `PB > 20` ∨ `EV/EBITDA > 1000` ∨ `PS > 100` | loại |
| `UNTRACEABLE_DENOMINATOR` | mẫu số không có metric tương ứng trong kho (vd EBITDA) | loại + ghi vào backlog ingestion |
| `SHARES_NOT_PIT` | BVPS/EPS dùng `shares_now` | **ĐÍNH CHÍNH 16/10: không phải lỗi** khi giá đã back-adjust — giữ cờ nhưng đổi nghĩa thành *"đang ở basis SHARES_NOW"*, hợp lệ |
| `BASIS_MISMATCH` | giá ở basis `ADJUSTED_TODAY` nhưng BVPS dùng shares PIT (hoặc ngược lại) | **loại** — đây mới là lỗi thật cần chặn |
| `OUTLIER_WINSORED` | vượt ngưỡng nhưng đã kẹp biên | dùng với giá trị kẹp, ghi rõ |

**Ngưỡng biên đề xuất (áp SAU khi hết lỗi ×4 ở B):**
`PE ∈ [2, 100]` · `PB ∈ [0,2, 20]` · `PS ∈ [0,2, 100]` · `EV/EBITDA ∈ [1, 200]`;
ngoài biên ⇒ `is_clean_metric = 0` **trước**, cân nhắc winsorize **sau**.

**Tái dùng hạ tầng đã có (không phát minh trùng):** kho đã có `health_ratios`
(**319.676 dòng · 790 mã · 26 ratio_name**, có cột `category` = liquidity/efficiency/leverage/
profitability) và `forensic_scores` (792 mã, `m_score`, `hard_violation`).
⇒ Đặt trường cờ theo cùng quy ước, và ưu tiên `DEBT_TO_EQUITY`/`CURRENT_RATIO` sẵn có cho nhóm
`DISTRESSED_*` thay vì tự định nghĩa lại.

## 6. Đầu vào cho Hạng mục B (tính lại `valuation_scores` theo PIT)

1. **Sửa gốc**: tính EPS từ `NET_INCOME` cấp báo cáo (TTM = tổng 4 quý đơn) — **không** nhân 4
   một chuỗi EPS chưa xác định chu kỳ; nếu buộc dùng EPS, phải **kiểm tra kỳ đó là quý hay luỹ kế**.
2. **PIT**: gắn `effective_date` (A) vào từng dòng ratio; loại **90 dòng kỳ 2026Q3**
   (`effective_date = 2026-10-30`) khỏi mọi truy vấn hôm nay.
3. **Số cổ phiếu**: nếu chưa có chuỗi theo thời gian (C) thì **bắt buộc** gắn `SHARES_NOT_PIT`
   và cấm dùng cho backtest — hiện đang dùng `shares_now` cho mọi kỳ.
4. **Không xoá cứng**: chỉ thêm cờ + (nếu cần) giá trị kẹp biên ở cột riêng.
5. **Kiểm chứng sau khi chạy B:** PE mới phải cho phân phối hợp lý (kỳ vọng median ~10–15, P99 ≤ vài
   trăm) và **số ca `PE < 2` phải về 0** — đây là tiêu chí DONE của B.

---

## 8. BƯỚC P — Truy vết nguồn giá của writer (chỉ đọc) · **phát hiện look-ahead THẬT**

**Trả lời 3 câu hỏi:**

| Câu hỏi | Trả lời (có dẫn chứng) |
|---|---|
| Bảng/DB nào? | `backend/data/screener_cache.db` → bảng `daily_ohlcv` (`valuation_engine.py:36` `SCREENER_DB_PATH`, `:103`, `:154-180`) |
| Ngày nào? | **ngày cuối quý** (`period_end`), tìm **ngược tối đa 30 ngày** để bắt phiên gần nhất (`:163-172`) |
| Cột nào / nguồn nào? | cột **`close`** (trùng `adj_close`) — chuỗi **đã back-adjust** theo số cổ phiếu hiện tại ⇒ basis `ADJUSTED_TODAY`, khớp với việc BVPS dùng shares-now (BASIS-FIX là nhất quán) |

**Kiểm chứng cơ chế:** HPG 2018Q1 → giá lưu **10.220** = close đúng ngày **2018-03-30**;
HPG 2018Q2 → **9.090** = close **2018-06-29** ✓ khớp tuyệt đối.

### 8.1. LỖI THẬT: fallback "lấy giá MỚI NHẤT" khi chuỗi quá ngắn

`valuation_engine.py:173-180`:

```python
# Fallback: latest available
cur.execute("SELECT date, close FROM daily_ohlcv WHERE symbol=? ORDER BY date DESC LIMIT 1", ...)
return row[1] if row else None
```

Nếu không có bar nào trong 30 ngày trước `period_end`, writer **lấy giá MỚI NHẤT trong kho**
— tức **giá TƯƠNG LAI** — rồi tính PE/PB cho kỳ quá khứ. Chính comment của tác giả cũng ghi
*"Fallback tới giá mới nhất khi chuỗi quá ngắn."*

### 8.2. Quy mô nhiễm (đo trên 7.318 dòng có `price` / 1.789 cặp `(mã,kỳ)`)

| Phân loại | Số cặp | Tỷ lệ |
|---|---|---|
| Giá khớp bar quanh `period_end` (±10 ngày) — hợp lệ | **1.141** | 63,8% |
| **Giá KHÔNG khớp quanh `period_end` nhưng có trong store ở ngày khác ⇒ nghi dùng fallback** | **636** | **35,6%** |
| Giá không tồn tại trong `daily_ohlcv` | 12 | 0,7% |

**Ca điển hình — ACB:** `2018Q1 → 2019Q4` (8 kỳ liên tiếp) đều bị gán **price = 21.100**, mà 21.100
chỉ tồn tại ở các ngày **2025-02-04 / 2025-05-30 / 2025-06-02** ⇒ PE/PB của ACB giai đoạn 2018–2019
được tính bằng **giá của năm 2025** (look-ahead **7 năm**).

⇒ **Hệ quả:** ~**1/3** giá trị lịch sử trong `valuation_scores` bị nhiễm look-ahead gián tiếp qua giá.
Mọi backtest dùng PE/PB của tầng này trước nay đều "ăn gian" — **và đây mới là lý do đúng**, không
phải chuyện shares (mục §4).

### 8.3. Sửa gì ở B (fail-closed, không xoá)

1. **Bỏ hẳn fallback "latest available"**: không có bar trong cửa sổ 30 ngày ⇒ `price = NULL` +
   cờ **`PRICE_FALLBACK_FUTURE`** (nếu phát hiện giá được lấy từ ngày > `period_end`) hoặc
   **`PRICE_MISSING`** (không có giá) ⇒ `is_clean_metric = 0`.
2. **Gắn cờ hồi tố** cho 636 cặp `(mã,kỳ)` đã nhiễm (giữ dữ liệu, chỉ đánh dấu) — danh sách sinh
   được bằng chính truy vấn ở §7.2.
3. **Canary mới:** HPG 2018Q1 = 10.220 (khớp `period_end`) và ACB 2018Q1 **phải KHÔNG còn** 21.100.
4. Điều kiện DONE của B bổ sung: **số cặp dùng fallback = 0**.

---

## 9. Chuỗi C/B — nhật ký thực thi & 3 phép đo lật ngược giả định (2026-10-16)

**Bước 1 (A re-run) — XONG.** Chạy lại `migrate_financial_facts_effective_date.py --apply`:
`617.699/617.699` dòng có `effective_date`, `0` ngày sai định dạng, basis 100% `STATUTORY_LAG`.
Bù đúng **2.715 dòng SHB (2008Q4→2026Q2, 40 metric)** mà luồng ingestion thêm sau Hạng mục A.

**Đo 1 — quy ước EPS KHÁC NHAU theo nguồn (giả định "một hệ số chung" là SAI):**

| Nguồn | Phép đo | Kết luận |
|---|---|---|
| `vci` (legacy) | `EPS == NET_INCOME_kỳ / shares` khớp **×1,00** ở mọi kỳ (AAH/AAS/AAT) | EPS = **RIÊNG KỲ** ⇒ annualize ×4 |
| `tcbs` | `EPS == TTM4 / shares` khớp **×1,00** ở **6/6** kỳ (2.273 vs 2.272 · 2.281 vs 2.281 · 2.238 vs 2.238 · 2.215 vs 2.215 · 2.053 vs 2.053 · 302 vs 301) | EPS = **TTM** ⇒ annualize ×1 |
| `vci` EPS(Q4)/EPS(Q1) | median **1,33**, chỉ 20,9% ca ≈1 (P10 0,34 · P90 9,55) | xác nhận **không** phải luỹ kế YTD (YTD ⇒ ≈4) |

⇒ **40 ca `PE < 2` có nguồn gốc là các dòng EPS đã-TTM bị nhân ×4** (SHB 2024Q4: 0,84 → đúng 3,37).
⇒ Hệ số annualize **phải tự hiệu chuẩn theo từng (mã, kỳ)**, không có hằng số chung: so `EPS` với
`NI_kỳ/shares`, `TTM4/shares`, `YTD/shares`; khớp cái nào ⇒ dùng hệ số đó; không khớp ⇒
`EPS_PERIODICITY_UNKNOWN` + `is_clean_metric = 0` (cấm đoán).

**Đo 2 — hai nguồn số cổ phiếu nằm ở HAI BASIS, cấm nối chuỗi (SHB):**

| kỳ | `SHARES_OUT` (tcbs) | `CHARTER_CAPITAL/10.000` (vci) | lệch |
|---|---|---|---|
| 2008Q4 | 200.000.000 | 200.000.000 | **+0,0%** |
| 2010Q1–Q3 | 2.668.817.204 → 2.669.412.976 | 200.000.000 | **+1.234%** |
| 2015Q4 | 2.667.061.611 | 948.600.000 | +181,2% |
| 2020Q4 | 4.593.785.311 | 1.755.800.000 | +161,6% |
| 2024Q4 | 5.343.748.241 | 3.662.900.000 | +45,9% |
| 2026Q2 | 5.343.748.241 | 4.594.200.000 | +16,3% |

⇒ `SHARES_OUT` **không phải** số cp tại kỳ (biên độ lệch giảm dần về hiện tại = dấu hiệu chuỗi đã
điều chỉnh hồi tố về một mốc nào đó, nhưng mốc đó không khớp lần điều chỉnh giá). **Hệ quả trực
tiếp lên headline SHB:** BVPS = **14.093** (tcbs) ⇒ P/B(16/10, 10.650) = **0,756** · BVPS =
**16.393** (charter) ⇒ P/B = **0,650**. Hai con số đều "đo được", khác nhau 16% ⇒ **phải chốt
basis trước khi đưa vào xếp hạng**.

**Đo 3 — bảng C lưu CẢ HAI basis** (`shares_history`, PK `(symbol, period, basis)` + cột
`divergence_pct`): dry-run **ĐẠT 0 lỗi**, 6/6 canary OK — `CHARTER_PIT`: **22.009 dòng / 71 kỳ /
777 mã**; `TCBS_REPORTED`: **71 dòng / 1 mã (SHB)**, 66/71 cặp lệch >1% (đều là SHB).

**Sự cố fail-closed (đáng ghi lại):** lần chạy `--apply` **bị chặn** với 144 lỗi "thiếu
effective_date" — vì **một tiến trình khác đang nạp DB** (PNJ xuất hiện giữa 2 lần chạy; tổng
dòng biến động 22.009→21.979). Không có dòng nào bị ghi ⇒ hợp đồng "không đủ điều kiện thì không
ghi" hoạt động đúng. **Bài học vận hành:** mọi bước ghi phải (a) chạy lại A nếu luồng ingestion
vừa thêm dòng, (b) kiểm "writer có im lặng không" trước khi mở transaction.

---

## 10. Triển khai 3 phán quyết (2026-10-16) — code + test, chưa ghi DB

| Phán quyết | Cài đặt | Bằng chứng |
|---|---|---|
| 1. Chờ writer im lặng ≥60s | `scripts/recompute_valuation_scores_pit.py::wait_for_idle` (đo số dòng mỗi 5s) | lần `--apply` trước đã bị chặn thật (144 lỗi) |
| 2. Basis xếp hạng ưu tiên `TCBS_REPORTED` | `valuation_engine.py::select_shares_now` + guard `SHRES_DIV_TOL_PCT = 25%` | SHB 2026Q2 (+16,3%) ⇒ dùng tcbs, cờ `SHARES_DILUTION_FLAGGED`; SHB 2010Q1 (+1.234%) ⇒ charter, cờ `SHARES_BASIS_UNEXPLAINED` |
| 3. Tự hiệu chuẩn chu kỳ EPS | `valuation_engine.py::calibrate_eps_periodicity` + `_af_for` | QUARTER ×4 / TTM ×1 / YTD ×4-q / `UNKNOWN` ⇒ cờ + `is_clean_metric=0` |

**Phát hiện phụ khi cài đặt — `get_facts` chỉ trả 8 KỲ:** tầng writer từ trước tới nay chỉ nhìn
thấy 8 kỳ gần nhất (`get_facts(symbol)` không truyền `periods`) ⇒ các hàng lịch sử trong
`valuation_scores` là tàn dư của những lần chạy khác nhau, không phải một chuỗi PIT thống nhất.
Đã sửa: `MAX_PERIODS = 200` truyền tường minh cho chuỗi PIT (SHB có 71 kỳ).

**Bẫy do test bắt được (đáng ghi):** với mã chỉ có ~1 năm dữ liệu, cửa sổ **TTM (4 quý) TRÙNG**
cửa sổ **YTD** ở mọi kỳ của năm đầu ⇒ hai ứng viên có cùng độ lệch ⇒ việc chọn bị quyết định bởi
thứ tự dict (tuỳ tiện). Sửa: bỏ 4 kỳ đầu khi hiệu chuẩn, chỉ so từ kỳ thứ 5 (lúc đó TTM ≠ YTD
thật). Test `test_calibrate_nhan_dien_ytd` là ca canary cho bẫy này.

**Trạng thái:** 20/20 test PASS (gồm 3 canary chạy trên store thật: HPG 2018Q1 = 10.220 ·
ACB 2018Q1 = `None` + `PRICE_FALLBACK_FUTURE` · SHB hiệu chuẩn ra **TTM**). Chuỗi A→C→B đang
chạy nền với chốt chặn writer-idle; báo cáo DoD sẽ bổ sung vào đây khi xong.

---

## 12. BƯỚC 5 — tách CHU KỲ (shape) khỏi MỨC (level): 2 vòng, 4 cờ mới, tự sửa 1 lần

Phán quyết 2026-10-16 (GO). Mục tiêu: chấp nhận một quy ước theo **độ ổn định** của tỷ lệ
`r_t = EPS_t / (NI_t/S_t)` (bất biến với mức), ghi lệch mức thành cờ **thông tin**. Dung sai
**vẫn 2% — không nới** (khóa bằng `test_nguong_hieu_chuan_bi_khoa_khong_duoc_noi`).

| Vòng | Tiêu chí độ ổn định | Dòng sạch | PE sạch | UNKNOWN | Phán quyết |
|---|---|---|---|---|---|
| (trước B5) | `median\|r−1\| ≤ 2%` | 34.707 | 3.929 | 27.779 | — |
| **5a** | `CV = σ/μ ≤ 2%` | 29.313 ↓ | 1.359 ↓ | 42.096 ↑ | **BỊ BÁC BỎ bằng số đo** |
| **5b** | `1,4826 × MAD(r)/median(r) ≤ 2%` | **35.800** | **4.460** | 24.641 | **GIỮ** |

**Vì sao 5a hỏng:** `σ/μ` bị chi phối bởi **vài kỳ dị biệt** ⇒ veto cả mã dù phần lớn kỳ khớp
tuyệt đối. Bản cũ (trung vị) không bị vì nó vốn vững. `MAD` giữ tính vững đó mà vẫn đo độ tán xạ
⇒ 5b vượt cả bản cũ: **+1.093 dòng sạch, +531 dòng PE sạch**, PE<2 hợp lệ vẫn **0**.

**Cờ mới:** `EPS_LEVEL_MISMATCH` (**thông tin**) — 1.487 dòng đứng riêng + 5.949/835 dòng kèm cờ
khác · `EPS_PERIODICITY_AMBIGUOUS` (**chặn**) — 260 dòng (ca NI phẳng: chọn sai ⇒ PE lệch 4×).
Quy ước sau 5b: QUARTER 29.087 · UNKNOWN 24.641 · YTD 1.075 · TTM 497 · AMBIGUOUS 260.

**Canary trên store thật:** VNM ⇒ `QUARTER` (tán xạ **1,1%**) + lệch mức **11,2%** ✓ ·
SHB ⇒ `TTM` (0,1%) ✓ · CII ⇒ `UNKNOWN` (148%) ✓ · ca tổng hợp NI phẳng ⇒ `AMBIGUOUS` ✓.

### Tự đính chính: ước lượng 7.851 dòng là SAI (thực tế +1.093)

Tôi dự báo Bước 5 giải phóng ~7.851 dòng. Sai, và đã truy nguyên (hai giả thuyết của tôi đều bị
số đo bác bỏ):

| Giả thuyết | Phép đo | Kết quả |
|---|---|---|
| "Thiếu độ phủ `NET_INCOME`" | 344 mã bị cờ | **0 mã** thiếu `NET_INCOME`, **0** mã khuyết cửa sổ hoàn toàn ⇒ bác bỏ |
| "Trộn nguồn trong cùng ô (mã,kỳ,metric)" | `COUNT(DISTINCT source)` | **0/622.992 ô** có >1 nguồn ⇒ bác bỏ |
| "EPS và NET_INCOME của cùng kỳ khác nguồn" | so `source` cặp EPS↔NI | chỉ **2 cặp** (BCM 2025Q3, VCB 2025Q2) ⇒ bác bỏ |
| **Thực tế:** tỷ lệ `r` không có "đa số áp đảo" nào | MCM: `r ≡ 0,900 ± 0,001`; PRE: `0,965` + 2 kỳ `1,03` | MAD nhận được cả hai ✓ — nên lợi ích THẬT nhỏ và nằm ở nhóm "đa số khớp + vài kỳ lệch", không phải 7.851 dòng |

**⇒ Nguyên nhân 24.641 dòng UNKNOWN còn lại:** mã có **không** đa số kỳ khớp cùng một quy ước
(ví dụ tỷ lệ dao động liên tục) ⇒ không thể chứng minh chu kỳ từ những gì kho có. Đây **không phải**
vấn đề độ phủ lẫn trộn nguồn — và **không sửa được bằng phương pháp**; levers thật sự đều nằm ở
dữ liệu cơ bản chi tiết hơn (`NET_PROFIT_PARENT`, số cổ phiếu bình quân gia quyền mà nhà cung cấp
dùng — cả hai **chưa có** trong kho: 0/66 metric). Vì vậy 24.641 dòng đó **ở ngoài xếp hạng là
đúng**, không phải nợ kỹ thuật cần "sửa cho đủ".

## 11. KẾT QUẢ HẠNG MỤC B (đã chạy thật, 4 vòng lặp sửa lỗi)

**Chuỗi A→C→B:** guard writer-idle OK (622.992 dòng không đổi 30s) → A `exit=0` (bù 7.287 dòng)
→ C `exit=0` → B **791 mã / 40s / 0 lỗi**; backup `financial_facts.db.bak_recompute_scores_*`.

| Chỉ số nghiệm thu | Trước | Sau (đo lại độc lập) |
|---|---|---|
| Cặp bị LOẠI vì không có bar trong cửa sổ PIT | — | **8.988** (197 trước 2018 = chuỗi giá chưa tồn tại · **8.791 từ 2018**) |
| … trong đó bản cũ **SẼ** lấy giá tương lai | 636 cặp (đo trên 58 mã có sẵn hàng điểm) | **8.601 cặp** (8.412 từ 2018) — quy mô thật lớn hơn 13,5× |
| Dòng `PE < 2` được coi là hợp lệ | 40 | **0** (còn 201 dòng nhưng đều mang cờ: `EXTREME_LOW_PE` 185 · phần còn lại cờ khác) |
| Median PE (dòng sạch) | 11,87 (méo bởi ×4) | **9,48** tổng · **9,99** (ADV20 ≥ 50k, n=1.284) · 9,23 (nhỏ, n=2.645) |
| Dòng đủ tư cách xếp hạng | — | **34.707 / 58.279** (59,6%) |

Median PE dòng sạch theo năm: 2020 **7,04** → 2021 8,42 → 2022 8,41 → 2023 9,11 → 2024 10,71 →
2025 10,89 → 2026 9,66 — chuỗi trơn, hợp lý về kinh tế (khác hẳn dạng méo ×4 trước đây).

**Canary SHB (kiểm độc lập):** 2026Q2 PB = **0,961** (giá 13.550 = close 30/06) với
`shares_basis = TCBS_REPORTED` + `eps_flow_kind = TTM` + `clean = 1` (cờ `SHARES_DILUTION_FLAGGED`
chỉ là thông tin). Tại giá 16/10 (10.650) ⇒ **P/B = 0,756** — đúng con số đã tranh luận, nay do
hệ thống tự sinh và có khai báo basis.

### Ba lỗi của CHÍNH TÔI mà vòng nghiệm thu phơi ra (đã sửa + có test)

1. **Cờ gán theo Kỳ cho mọi ratio** ⇒ 11.375 hàng PE bị loại oan vì EV/EBITDA của cùng kỳ dùng
   EBITDA proxy. Sửa: `FLAG_SCOPE` + `flags_for_ratio()` — cờ chỉ dán lên ratio mà nó nói về.
   Dòng sạch: 5.551 → **34.707**.
2. **Cờ pha loãng bị cho CHẶN** ⇒ SHB/MBB/TCB bị loại khỏi xếp hạng dù số liệu đúng. Sửa:
   `INFO_FLAGS` + `is_blocking()` — canary pha loãng là thông tin, chỉ `SHARES_BASIS_UNEXPLAINED`
   (>25%) mới chặn.
3. **`ni_at()` coi quý thiếu = 0** (định làm sai luật "Không biết ≠ 0") ⇒ nay trả `None` và
   **bỏ ứng viên** thay vì đoán; `window()` đòi cửa sổ đủ kỳ.

### Phát hiện dữ liệu cần chuyển cho luồng ingestion

`NII` (SHB, nguồn tcbs): 2025Q4 = **187 tỷ** trong khi 2026Q1 = 5.497 tỷ và 2026Q2 = 6.761 tỷ
⇒ nhảy ~30×. Nghi lỗi đơn vị/thiếu số 0 ở một kỳ. Cờ `NEAR_ZERO_DENOMINATOR` đã bắt được
(PS 2025Q4 = 460 lần). **Cần Agent 2 đối chiếu lại chuỗi NII.**

---

## 7. ĐỀ XUẤT WINSORIZE — áp Ở ĐÂU, bằng công thức nào (bổ sung 2026-10-15)

### 7.1 Chuỗi hỏng đã đóng: winsorize một mình là KHÔNG ĐỦ, và hướng hỏng là fail-OPEN trên rủi ro

| Bước | Vị trí (đã đọc mã, không suy đoán) | Hệ quả |
|---|---|---|
| 1 | `valuation_engine.py:273` — `ttm_eps = eps * (quarters / 1)` **vô điều kiện** | PE kỳ Q2/Q4 (EPS luỹ kế) bị chia ~4; 39/40 ca đúng tỉ lệ `0,25` (mục 2) |
| 2 | z-score sinh từ chuỗi có PE sai | z của chính mã đó âm rất sâu ("cực rẻ") |
| 3 | `grid_search_v2.py:189` — `mos = 1.0 - (max(zs) / 3.0 + 0.5)` | **bão hoà ở `\|z\| ≥ 1,5`**: `z ≤ −1,5` ⇒ MoS = **100%**; `z ≥ +1,5` ⇒ MoS = 0% |
| 4 | `governor/emergency_exit_engine.py:233–240` — xếp hạng xả theo `mos < 20 / 30 / 50` | MoS 100% ⇒ **cả 3 ngưỡng đều false** ⇒ `priority = LOW` ("bluechip, hold if possible") |
| 5 | `decision_guard.py:364` — nhánh DEFENSIVE (`LRI < 0,30`) gọi `EmergencyExitEngine.evaluate()` | vị thế có PE hỏng được xếp **ưu tiên xả THẤP NHẤT** đúng lúc quy trình thanh lý cần chạy |

⇒ **Đây là lỗi kỳ hạn ở tầng ratio làm IM LẶNG quy trình thanh lý theo rủi ro** — cùng họ với Ca 1
(`RANGING`) và Ca 6 (guard tắt tiếng khi thiếu dữ liệu), nhưng hậu quả nặng hơn: nó không chỉ làm
sai một nhãn hiển thị mà **đổi thứ tự ưu tiên xả hàng trong chế độ DEFENSIVE**.
⇒ Winsorize **không sửa được gốc** (đúng như mục 2: B sửa, E gắn cờ). Winsorize ở đây chỉ có một
mục tiêu hợp lệ: **cứu THANG ĐO của z** cho những mã còn lại — vì một `std` bị đuôi thổi phồng làm
mọi z co về 0, MoS về ~50% (vùng "không nói gì"), và bộ lọc mất khả năng phân biệt trong im lặng.

### 7.2 Kẹp `ratio_value` toàn cục là LOOK-AHEAD — cấm

Một ngưỡng percentile/`[min,max]` tính trên **toàn bộ** chuỗi (mọi kỳ, kể cả kỳ tương lai) rồi kẹp
từng dòng ⇒ dòng 2018 bị kẹp bằng ngưỡng chỉ biết được từ 2026. Trong một bảng PIT đó là **look-ahead
mới**, đúng loại lỗi mà A/B đang dọn ⇒ tự tay tái nhiễm.
⇒ Winsorize chỉ hợp lệ khi biên là **trailing/expanding**: tại kỳ `t`, biên chỉ tính từ các kỳ `< t`.

### 7.3 Đề xuất 3 tầng, đúng thứ tự (không đảo)

**Tầng 1 — CỜ (đúng/sai), additive, không xoá cứng:** như mục 5 (`is_clean_metric`, `anomaly_flags`,
`effective_date`). Các mã `EXTREME_LOW_PE` · `PERIODICITY_MISMATCH_EPS` · `DISTRESSED_EQUITY` ·
`NEAR_ZERO_DENOMINATOR` · `UNTRACEABLE_DENOMINATOR` **loại dòng khỏi xếp hạng**.

**Tầng 2 — WINSOR (ổn định thang đo), đặt trong `backfill_valuation_pit.py` (expanding-window),
KHÔNG đặt trong `valuation_engine.py`** (file đó đang dùng `mean/std` toàn chuỗi — look-ahead sẵn có,
theo chính docstring của `backfill_valuation_pit.py`), và **KHÔNG ghi đè cột `ratio_value`**:
- Ước lượng bền vững thay cho `std`: `z_robust = (x − median_w) / (1,4826 · MAD_w)` với `w` = cửa sổ
  **trailing**; lý do: `std` bị chính các cực trị thổi phồng nên nó là thứ bị hỏng, không phải thứ để tin.
- Kẹp đầu vào ở mức **trailing percentile** `[p_lo, p_hi]` theo `(symbol, ratio_name)` **trước** khi
  tính `mean/std`; `p_lo/p_hi` là **số phải ĐO** (xem 7.4) — không bịa trước. Mọi lần kẹp ghi lại giá
  trị kẹp ở cột riêng + cờ `OUTLIER_WINSORED` (đã định nghĩa ở mục 5).
- Fail-closed: nếu `MAD_w = 0` hoặc `count_w < n_min` ⇒ `z = None`, **không** bịa 0 (luật A của `AGENTS.md`).

**Tầng 3 — Chặn bão hoà ở consumer:** vì `mos` bão hoà ở `|z| ≥ 1,5`, một z rác đủ để lật MoS từ 0% sang
100%:
- consumer (`grid_search_v2._get_mos_pct`) **bắt buộc** lọc `is_clean_metric = 1`;
- khi z không sạch/không đo được ⇒ MoS = `None`, và `emergency_exit_engine` phải coi `None` là
  **không kết luận được**, KHÔNG được rơi về nhánh "rẻ ⇒ LOW priority". (Hiện `_get_mos_pct` trả `0.0`
  khi thiếu z — hướng đó an toàn vì `mos < 20` ⇒ CRITICAL; điểm hỏng nằm ở nhánh MoS = 100% sinh từ z rác.)

### 7.4 SỐ ĐO THỰC TẾ (2026-10-15, read-only, `financial_facts.db`) + thông số đề xuất

> ⚠️ **ĐÍNH CHÍNH TRẠNG THÁI (2026-10-16 19:5x).** Các bảng dưới đây đo trên trạng thái **7.427 dòng**
> — trạng thái đó **không còn là bảng sống**: phiên song song đã chạy lại B/C và **dựng lại
> `valuation_scores` lúc 2026-10-16 19:05–19:24** (`bak_recompute_scores_20261016_19xxxx`,
> `bak_shares_hist_20261016_19xxxx`). Trạng thái cũ nay nằm ở **`valuation_scores_backup` = 7.301 dòng**.
> Bảng sống hiện tại: **58.279 dòng · 743 mã · 35 kỳ (2018Q1–2026Q3)**, đã có thêm cột
> `anomaly_flags`, `is_clean_metric`, `price_basis`, `shares_basis`, `eps_flow_kind`.
> ⇒ **`[p2, p98]` và `n_min = 5` CHƯA được chốt**: phép thử bác bỏ ở Bảng B phải chạy LẠI trên trạng thái
> mới trước khi thi hành (đuôi của trạng thái mới **xấu hơn**, xem 7.4bis). Số dưới đây giữ nguyên
> làm **lịch sử quyết định**, không xoá.

**Bảng A — phân phối `ratio_value`** (tổng 7.427 dòng: `PE` 1.159 · `PB` 1.776 · `PS` 1.700 · `ROE` 1.666 · `EV_EBITDA` 1.126).
**0 giá trị âm · 0 giá trị = 0 · 0 non-finite ở MỌI ratio** ⇒ hai nhánh `NEGATIVE_*` chưa từng xuất hiện (writer ẩn đi, đúng như mục 1).

| ratio | min | P1 | P5 | med | P95 | P99 | max |
|---|---|---|---|---|---|---|---|
| PE | 0,019 | 1,257 | 2,191 | **11,1** | 88,1 | 351,9 | **12.500** |
| PB | 0,353 | 0,496 | 0,708 | 2,06 | 6,54 | 14,9 | **79,66** |
| PS | 0,206 | 0,484 | 0,805 | 7,49 | 33,0 | 61,0 | **460,4** |
| EV_EBITDA | 0,135 | 3,583 | 8,401 | 36,5 | 155,5 | 557,0 | **3.386,9** |
| ROE | ~0 | 0,0039 | 0,021 | 0,143 | 0,358 | 0,552 | 0,885 |

**Bảng B — độ nhạy thang đo (phép thử BÁC BỎ ở 7.4 cũ), `std_full ÷ std_cắt`:**

| ratio | ÷ sau [1,99] | ÷ sau [2,98] | ÷ sau [5,95] | kết luận |
|---|---|---|---|---|
| PE | **11,2×** (373,7→33,5) | 15,3× (→24,5) | 24,6× (→15,2) | đuôi nuốt std: **ĐÚNG** |
| EV_EBITDA | 2,9× (158,1→54,1) | 3,7× (→42,3) | 5,0× (→31,7) | **ĐÚNG** |
| PS | 2,0× (19,4→9,5) | 2,3× (→8,5) | 2,8× (→7,0) | **ĐÚNG** |
| PB | 1,6× (3,26→2,03) | 1,9× (→1,74) | 2,3× (→1,40) | **ĐÚNG** |
| ROE | **1,16×** (0,1131→0,0975) | 1,24× | 1,43× | **SAI/không đáng kể** ⇒ KHÔNG winsorize |

⇒ Giả thuyết "std bị đuôi thổi phồng" **đứng vững ở PE/EV_EBITDA/PS/PB** và **bị bác bỏ ở ROE**
(1,16× < ngưỡng 1,2 đặt trước khi đo — ngưỡng chốt trước, không chỉnh sau khi thấy số).
Cắt **[p2, p98]** đã lấy 47–93% mức lạm phát với chỉ 4% mẫu bị kẹp; [p5, p95] thêm rất ít mà mất 10% mẫu ⇒ **chọn [p2, p98]**.

**Bảng C — cỡ mẫu (n_min):** `count` (số kỳ trong cửa sổ): min 1 · P10 3 · P50 14 · P90 27; `< 3` = 570 dòng · `< 5` = **1.127** · `< 10` = 2.493. `count_5y`: `< 5` = 1.403.
⇒ **`n_min = 5`** (1.127 dòng = 15,2% sẽ có `z = None` + cờ `INSUFFICIENT_HISTORY`; **không** điền 0). `n_min = 3` giữ 92,3% nhưng z dựng trên 3 điểm là nhiễu.
**`peer_count` · `peer_group` · `z_score_peer` = 100% NULL** ⇒ tầng "xếp hạng phân vị NGÀNH" **chưa tồn tại trong bảng**, nên **không thể chốt `n_min` cho nó từ dữ liệu hiện có** — phải định nghĩa khi dựng tầng peer (ghi backlog, không bịa ngưỡng).

**Bảng D — `z_score` và tác động lên quyết định:** `z_score` P50 0,764 · P95 2,12 · P99 3,01 · max 4,47; **`|z| ≥ 1,5` (ngưỡng bão hoà MoS) = 1.068 dòng (14,4%)**. `z_score_ts` max **211,88**, `|z| > 10` = 22 dòng. Sức khoẻ mẫu số: **`std ≤ 0`/NULL = 286 dòng**, `count < 3` = 570.
Mô phỏng đúng công thức consumer (`grid_search_v2.py:189`, `max(z)` trên PE/PB mỗi `(symbol, kỳ)`): 1.786 cặp ⇒ **MoS = 100% ("cực rẻ") 125 cặp · MoS = 0% 134 cặp · còn lại 1.527** ⇒ ~7% cặp đang bị đẩy vào nhánh "rẻ ⇒ ưu tiên xả THẤP NHẤT".

**Thông số đề xuất (chờ GO):** winsor **`[p2, p98]` trailing** cho PE/PB/PS/EV_EBITDA · **không** winsor ROE · **`n_min = 5`** · z theo `median/MAD` trailing · consumer lọc `is_clean_metric = 1` và coi z không sạch là "không kết luận được".

**Đo tiếp (1 lệnh, chưa chạy):** 286 dòng `std ≤ 0` có mang `z_score` khác NULL không (nếu có ⇒ z chia cho 0, đúng lớp lỗi Ca 1b), và 1.068 dòng `|z| ≥ 1,5` phân bố thế nào theo `ratio_name` × kỳ Q2/Q4 (để biết bao nhiêu là hệ quả trực tiếp của lỗi `×4` ⇒ nối vào tiêu chí DONE của B).

### 7.4bis SỐ ĐO TRÊN TRẠNG THÁI MỚI (2026-10-16, sau khi B/C chạy lại, 58.279 dòng)

*Snapshot nhất quán đã lấy bằng SQLite backup API: `ff_snapshot_20261016.db` · rows = 58.279 ·
`(n, Σratio_value, max id) = (58279, 14836848.423891, 314676)` · fingerprint `88138018f38b3f3f`.*

**Bảng A′ — phân phối mới (đuôi XẤU HƠN trạng thái cũ):**

| ratio | n | min | P2 | P50 | P98 | max | so với cũ (max) |
|---|---|---|---|---|---|---|---|
| PE | 10.016 | 0,00029 | 1,996 | 23,66 | 1.183 | **57.030** | 12.500 → ×4,6 |
| PB | 15.187 | 0,00034 | 0,2435 | 0,998 | 5,608 | **226,2** | 79,66 → ×2,8 |
| PS | 12.899 | 0,00021 | 0,0524 | 1,757 | 60,49 | **25.584** | 460,4 → ×55 |
| ROE | 11.447 | ~0 | 0,00115 | 0,0482 | 0,4181 | 9,143 | 0,885 → ×10 |
| EV_EBITDA | 8.730 | 0,0217 | 3,571 | 33,85 | 582,5 | **1,2033e7** | 3.386,9 → ×3.553 |

**Bảng A″ — `PE < 2`: 201 dòng** (không còn 40), phân bố quý `{Q4: 74 · Q2: 62 · Q3: 36 · Q1: 29}`
⇒ **Q2+Q4 = 136/201 = 67,7%** — đúng dấu vân tay của lỗi annualization theo kỳ (nhưng tổng số đã tăng
5×, tức lỗi vẫn tồn tại ở một phần dữ liệu sau B — **cần đối chiếu với `eps_flow_kind`** trước khi kết luận).

**Bảng B′ — mẫu số suy biến (phát hiện MỚI, nặng hơn con số cũ 286):**
`std = 0` ở **3.702 dòng** (`std IS NULL` = 0), `count` của nhóm này ∈ **[1, 4]**; trong đó
**`z_score` = 0,0 ở 3.690 dòng** và **12 dòng mang z hữu hạn khác 0 dù `std = 0`** (nghi chia-cho-0 đã
bị kẹp) ⇒ **z được BỊA 0** khi không đo được — đúng lớp lỗi luật A của `AGENTS.md`.
Cùng nhóm đó `z_score_ts` = **NULL toàn bộ** ⇒ tầng ts đã theo luật, tầng `z_score` thì chưa.

**Bảng D′ — `|z_score| ≥ 1,5` = 5.820 dòng (chiếm ~10–11% mỗi ratio)**, chia **4.674 dòng z ≥ +1,5
("đắt") vs 1.146 dòng z ≤ −1,5 ("rẻ")** ⇒ bất đối xứng ~4:1.
**Quý: Q2+Q4 = 49,2% so với nền toàn bảng 51,1% ⇒ KHÔNG có làm giàu theo quý** ⇒ phần lớn 5.820 dòng
này **KHÔNG** do lỗi `×4` — nó là đuôi thật của tỉ số (mẫu số nhỏ / giá trị lớn).
Mô phỏng consumer (`grid_search_v2:189`): **238 cặp `(symbol, kỳ)` chảy vào MoS = 100%** (cũ: 125) ·
quý của dòng gây bão hoà: Q4 76 · Q2 75 · Q3 48 · Q1 39 ⇒ Q2+Q4 = 151/238 = 63,4%.

⇒ **Hệ quả cho thông số:** `[p2, p98]` và `n_min = 5` **phải được đo lại** trên snapshot này
(đuôi đã nở rộng ở mọi ratio, `std = 0` tăng 13×, còn `n_min` chưa đo lại phân bố `count`).
**Không thi hành winsorize trước khi có phép thử bác bỏ chạy trên trạng thái mới.**

### 7.4ter CHẨN ĐOÁN ROOT-CAUSE ĐUÔI + ĐO LẠI THÔNG SỐ (2026-10-16, snapshot 58.279 dòng)

**Nguồn:** `ff_snapshot_20261016.db` (backup API), fingerprint `88138018f38b3f3f`.

#### (a) Giả thuyết "lệch basis giá/cổ phiếu sinh ra đuôi" — **BỊ BÁC BỎ làm nguyên nhân chính**

| Kiểm chứng | Kết quả |
|---|---|
| `(price_basis × shares_basis)` của TOP 1% mỗi ratio | **~gần như đồng nhất**: `ADJUSTED_TODAY/CHARTER_PIT` chiếm 101/101 (PE) · 127/129 (PS) · 85/88 (EV) · 111/115 (ROE) · 91/152 (PB) ⇒ đuôi **không** tập trung ở nhánh basis nào |
| Giá writer vs `daily_ohlcv.close` cùng kỳ (top 6 PE cực trị) | tỉ lệ **0,988 – 1,025** (KDC 57.030 vs close 57.720; NBB 1,000; GLT 1,000) ⇒ **giá KHỚP**, không phải lệch nguồn giá |

#### (b) Nguyên nhân THẬT — 3 cơ chế tách bạch được

1. **MẪU SỐ ≈ 0** (chính, đã có cờ): PE cực trị có `ratio_value ≈ price` (KDC: PE 57.030 = giá 57.030 ⇒ **EPS ≈ 1 đồng**);
   EV/EBITDA max `1,2×10⁷` với `NEAR_ZERO_DENOMINATOR,UNTRACEABLE_DENOMINATOR` (kho **không có metric EBITDA**);
   PS max 25.584 với `NEAR_ZERO_DENOMINATOR` (127/129 top-1%).
2. **GIÁ LỆCH THANG Ở MỘT SỐ DÒNG** (mới, chưa có cờ): `DDG 2026Q2` PB 226 với **price = 800.000**; `DDG 2026Q1` **900.000**;
   `BII 2024Q2` PS 25.584 với **price = 900.000** — trong khi DDG/BII giao dịch ở mức vài nghìn đồng ⇒
   **nghi lệch thang/đơn vị ở `valuation_scores.price`**, không phải near-zero denominator.
   ⇒ Việc cần làm: cổng sanity giá (`price` vs `daily_ohlcv.close` cùng kỳ) cho **mọi** dòng, không chỉ outlier.
3. **`LEGACY_PRE_PIT`**: 60/152 dòng top-1% của PB có `price_basis = NULL` và cờ `LEGACY_PRE_PIT` ⇒
   tồn dư của đường cũ, chưa qua B ⇒ nhóm này phải **loại** chứ không winsorize.

#### (c) LỖ HỔNG BOUNDING SPEC: **ROE chưa có biên nào**

`ROE = 9,14` (914%, ATG 2025Q2) mang **`anomaly_flags = NULL` và `is_clean_metric = 1`**;
top-1% ROE có **107/115 dòng `is_clean_metric = 1`** ⇒ mọi cực trị ROE đều đang **lọt lưới sạch sẽ**.
⇒ Bổ sung biên cho ROE (và rà lại danh sách ratio được bound) là việc phải làm trước mọi xếp hạng.

#### (d) PHÉP THỬ BÁC BỎ chạy lại trên trạng thái mới — **kết luận ĐẢO so với trạng thái cũ**

| ratio | `std_full ÷ std[1,99]` | `÷ [2,98]` | `÷ [5,95]` | so với cũ |
|---|---|---|---|---|
| PE | 5,68× | **9,01×** | 18,90× | cũ 11,2× |
| PB | 4,49× | **5,10×** | 6,49× | cũ 1,6× |
| PS | 22,68× | **32,57×** | 50,28× | cũ 2,0× |
| EV_EBITDA | **1.230×** | 1.742× | 2.775× | cũ 2,9× |
| ROE | 2,02× | **2,21×** | 2,71× | cũ **1,16× ⇒ ĐẢO: nay VƯỢT ngưỡng 1,2** |

⇒ **Cả 5 ratio nay đều vượt ngưỡng bác bỏ** (ROE đảo kết luận — chứng minh việc đo lại là bắt buộc, không
phải thủ tục). Nhưng **EV/EBITDA không được winsorize**: 1.230× phản ánh mẫu số **không truy vết được**;
kẹp nó lại là hợp thức hoá một con số không có nguồn ⇒ phải **loại bằng cờ** (`UNTRACEABLE_DENOMINATOR`),
không kẹp biên.

#### (e) `n_min` trên trạng thái mới

`count`: min 1 · P10 **2** · P50 9 · P90 18 · max 34; `< 3` = 7.329 (12,6%) · **`< 5` = 14.334 (24,6%)** · `< 10` = 30.637 (52,6%).
⇒ `n_min = 5` làm **24,6% dòng** mất z (cũ: 15,2%) — phải chấp nhận như một **mất mát có khai báo**
(`INSUFFICIENT_HISTORY`), hoặc hạ về 3 (12,6%) nếu ưu tiên độ phủ. Đây là **đánh đổi phải chốt bằng quyết định**,
không có "đúng" tự thân.


### 7.4quater SANITY GIÁ (GO 2026-10-16) — **giả thuyết "price lệch thang ở `valuation_scores`" BỊ BÁC BỎ**, nhưng lộ ra ổ lệch đơn vị trong `daily_ohlcv` (LIVE)

#### (a) Cổng sanity như đặc tả (`price ÷ close ≈ 1.000`, hoặc `price > 500.000`) — **0 ca để bắt**

Đối chiếu **58.279/58.279 dòng** có `price` với `daily_ohlcv.close` cùng kỳ (đọc panel: chỉ `VALID_ROW_SQL`):

| Bucket `price ÷ close` | Số dòng | % |
|---|---|---|
| 0,8 – 1,25 | **55.553** | **99,99%** |
| 1,25 – 2 | 3 | 0,01% |
| ≥ 2 · 5–20 · 20–100 · 100–1.000 · > 1.000 | **0 · 0 · 0 · 0 · 0** | 0% |
| Không đối chiếu được (không có bar hợp lệ ≤ cuối quý) | 2.723 | — |

Lệch lớn nhất toàn bảng = **1,313×** (`BHI 2025Q4`: 8.800 vs 6.700 — lệch *ngày* chốt quý, không phải lệch thang).
⇒ Cổng `PRICE_SCALE_CORRUPTED` như đặc tả **sẽ không bao giờ nổ** ⇒ nếu thêm nó như một bước *làm sạch dữ liệu* thì đó là **cổng trang trí** (đúng luật 1 của skill thiết kế cổng). Nó chỉ còn giá trị như **tripwire bất biến** ("không dòng nào được lệch > 2×") để chặn hồi quy về sau.
⇒ Kết luận phụ: 2.723 dòng không đối chiếu được là vấn đề **độ phủ** (thiếu bar trước cuối quý), KHÔNG phải thang giá.

#### (b) Ổ BỆNH THẬT: **một mã mang HAI ĐƠN VỊ trong cùng chuỗi `daily_ohlcv`, và các dòng đó đều `is_stale = 0`**

Phát hiện bằng nhật ký nhảy giá trong ngày trên **dòng hợp lệ vị từ** (1.538.753 cặp ngày liên tiếp):

| Bucket nhảy | Số cặp | Số mã | Trong đó `is_stale = 0` |
|---|---|---|---|
| 3–10× | 0 | 0 | — |
| 10–100× | 0 | 0 | — |
| **100–1.000×** | **193** | **43** | **193 (100%)** |
| **> 1.000×** | **42** | **32** | **41** |
| **Tổng ≥ 100×** | **452 cặp** | **92 mã** | ~100% |

Top mã: `NOS` 45 · `FID` 42 · `BII` 29 · `VKC` 28 · `LMH` 20 · `FTM` 20 · `ATG` 18 · `PTX` 16…
**Đặc trưng số học:** các cặp nhảy có hệ số **đúng bằng 900** và nhảy *qua lại* trong cùng một mã —
`DDG`: `2026-03-27` 1.000 → **900.000**; `2026-07-03` **800.000** → 1.100; 14 cặp như vậy.
`BII`: từ `2019-10-21` trở đi 29 cặp `1.000 ⇄ 900.000`. Cả hai đều `source = 'VCI'`, `is_stale = 0`.
⇒ Đây là dấu vân tay **trộn đơn vị VND ↔ "nghìn đồng"** (900.000 = 900 × 1.000) trong CÙNG một mã.
**Không thể quyết định bên nào là đơn vị đúng CHỈ từ store** ⇒ phải đối chiếu nguồn ngoài, KHÔNG rescale mù.

#### (c) Hệ quả — hai bất biến hiện có KHÔNG bắt được lớp này

`is_stale = 0` ✓ và `VALID_ROW_SQL` ✓ (giá "hợp lệ" về hình dạng: `low ≤ close ≤ high`, `volume ≥ 0`) —
một dòng 900.000 trong khi hàng xóm 1.000 **qua được cả hai lưới**. Đây là **cơ chế thứ ba**, khác hẳn
ba lớp đã biết (nguồn không có bar · dòng bị cách ly · lệch thang do *nguồn khác* ghi):
**cùng một nguồn (VCI) trả hai đơn vị ở hai thời điểm** ⇒ hệ quả lan ra MỌI consumer của `daily_ohlcv`
(không riêng định giá): RS/momentum, ADV, breadth, breakout, valuation.
Ví dụ đúng chuỗi nhân quả đã thấy: `BII 2024Q2` price 900.000 ⇒ `PS = 25.584`; `DDG 2026Q2` price 800.000 ⇒ `PB = 226`.

#### (d) Đề xuất (chờ GO)
1. **Cổng phát hiện theo lớp:** `|close_t / close_{t-1} − 1| ≥ 10×` **trong cùng một mã** ⇒ gắn
   `UNIT_MIX_SUSPECT` + cách ly khỏi đường quyết định (cùng họ với `detect_duplicate_sessions`),
   KHÔNG xoá, KHÔNG rescale tự động. Ngưỡng 10× là **thô hơn hẳn** mức nhảy thật (≥100×) nên không
   chặn oan biến động thường ngày (bucket 3–10× = **0 cặp** trên toàn store ⇒ dư địa an toàn lớn).
2. **Sanity giá ở `valuation_scores`** giữ lại dưới dạng **tripwire**: `price ÷ close ∈ [0,5; 2]`,
   kỳ vọng **không bao giờ nổ** (hiện 55.553/55.556 đạt; 3 dòng 1,25–2× là lệch ngày chốt ⇒ cần
   cửa sổ ±5 ngày trước khi biến nó thành cổng cứng).
3. **Truy đơn vị đúng cho 92 mã** bằng một nguồn thứ hai (TCBS/cafef) trước khi sửa — mỗi mã chỉ cần
   một điểm neo "giá thật ngày X" là phân xử được bên nào lệch.


#### (e) PHÁN QUYẾT GHI NHẬN (GO 2026-10-16) — và một điều phải sửa lại

| # | Nội dung GO | Trạng thái sau khi đo |
|---|---|---|
| 1 | Cổng sanity giá `PRICE_SCALE_CORRUPTED` (P0) | **PHẢI RE-SCOPE**: đo ra **0 ca** trong `valuation_scores` ⇒ hạ xuống **tripwire** (`price ÷ close ∈ [0,5; 2]`, cửa sổ ±5 ngày) + **chuyển trọng tâm sang cổng mới ở `daily_ohlcv`** cho 92 mã trộn đơn vị (mục (b)) |
| 2 | Bổ sung biên ROE vào bounding spec: `EXTREME_ROE_ANOMALY` khi `\|ROE\| > 1,5`; winsor ROE ở `[p2, p98]` | **XÁC NHẬN** — số đo ủng hộ (ROE vượt ngưỡng bác bỏ 2,21×; top-1% ROE có 107/115 dòng đang `is_clean_metric = 1`) |
| 3 | Winsor `[p2, p98]` trailing cho PE/PB/PS/ROE · **KHÔNG** winsor EV/EBITDA (loại bằng cờ) · `n_min = 5` | **XÁC NHẬN** — `n_min = 5` làm 24,6% dòng mang `INSUFFICIENT_HISTORY` + `z = NULL` (đánh đổi đã biết, không điền 0) |

⚠️ Chưa thi hành mục nào trong bảng trên: đây là **ghi nhận quyết định**, phần code sẽ làm ở bước riêng
(bounding spec nằm trong `valuation_engine.py`/writer — vùng vừa được phiên song song sửa, phải đọc lại trước khi vá).

### 7.4quinquies CÁCH LY PHANTOM THEO NEO NGOÀI — và TÌM RA DẤU VẾT GỐC (`rescaled_vci_x1000`)

#### (a) Đã thi hành: cách ly 3 mã có neo ngoài
Công cụ: `backend/scripts/quarantine_unit_mix.py` (dry-run mặc định, `--apply` mới ghi, backup API trước khi ghi,
idempotent, **additive**: `is_stale = 1` + gắn nhãn `quarantine:unit_mix_phantom_1000x`, KHÔNG xoá, KHÔNG sửa giá).

| mã | dòng bị cách ly | khoảng giá bị cách ly | dòng giữ lại (`is_stale = 0`) |
|---|---|---|---|
| `DDG` | 26 | 600.000 – 900.000 | 1.352 dòng, 800 – 43.300 |
| `BII` | 469 | 300.000 – 900.000 | 837 dòng, 1.000 – 31.000 |
| `NOS` | 742 | 200.000 – 900.000 | 704 dòng, 600 – 5.900 |
| **tổng** | **1.237** | | rò rỉ giá ≥110.000 ở `is_stale = 0`: **0** |

Ngưỡng cách ly = `100 × cận trên của neo` (DDG ≥110.000; BII/NOS ≥50.000/60.000) — chừa dư địa cho biến
động HỢP LỆ nhiều năm (DDG 2020 ~17.160đ = 17× neo vẫn giữ), vẫn bắt trọn nhánh 200.000–900.000.
**Bản dry-run đã bị loại 2 lần trước khi dùng được** — cả hai lần đều gắn cờ nhánh ĐÚNG:
lần 1 (mốc = "đoạn trội theo số dòng") gắn cờ median 1.000–1.100 của DDG; lần 2 (đoạn "cô lập giữa hai
đoạn liền kề cùng thang") gắn cờ **chuỗi hợp lệ 6 năm** của DDG (median 17.160, 1.304 dòng) và chuỗi
4.900 kéo 3 năm của BII. ⇒ **Kết luận phương pháp: cấu trúc chuỗi một mình KHÔNG đủ để phân xử bên nào
sai**; chỉ neo ngoài mới phân xử được. 92 mã không có neo ⇒ **chỉ khai báo, không đụng.**
Hạn chế đã khai báo: `is_stale = 1` chặn đường **PIT/live** nhưng **panel lịch sử chỉ lọc `VALID_ROW_SQL`**
nên 1.237 dòng này VẪN lọt vào panel/backtest ⇒ cần vị từ theo nhãn cho đường đọc panel (việc riêng).

#### (b) GỐC RỄ LỘ DIỆN: nhãn `rescaled_vci_x1000` có trong DỮ LIỆU nhưng KHÔNG có trong mã nguồn
`grep` toàn `backend/`: **0 kết quả** cho `rescaled_vci`. Trong kho: **4.633 dòng / 171 mã**
(2019-02-22 → 2026-09-23), trong đó **3.891 dòng vẫn `is_stale = 0`** (742 dòng `NOS` đã bị cách ly ở (a)).
⇒ Một script ad-hoc (đã biến mất khỏi repo) đã nhân `×1000` lên một số dòng `vci` và **ghi dấu vết vào `source`**.

#### (c) Nhưng nhãn đó **KHÔNG đồng nghĩa sai** — đo tỉ lệ thang (median dòng có nhãn ÷ median dòng không nhãn):

| Tỉ lệ | Số mã | Đọc |
|---|---|---|
| 0,9 – 1,1 | **47** | **phep ×1000 ĐÚNG** (giá vci vốn ở đơn vị "nghìn đồng" ⇒ ×1000 khôi phục đồng) |
| 0,1 – 0,9 / 1,1 – 10 | 59 / 49 | lệch thang nhẹ hoặc biến động giá hợp lệ |
| 10 – 300 | 8 | nghi vấn |
| **300 – 3.000** | **8** | **phep ×1000 SAI** ⇒ phantom thật (PPS 1.114×, VIN 1.074×, MGG 954×, VTJ 900×, MIE 733×, PXA/HHG 375×, NOS 307×) |

⇒ Đảo ngược `×1000` cho toàn bộ 171 mã sẽ **phá 47+ mã đang đúng** ⇒ lần nữa khẳng định kỷ luật neo ngoài.
Ví dụ ca "cả chuỗi bị nhân": `NOS` có **1.439 dòng mang nhãn / chỉ 10 dòng không nhãn** (median 200.000 vs 650)
⇒ nhánh đúng gần như bị xoá sạch khỏi chuỗi, và **cổng phát hiện theo bước nhảy KHÔNG THỂ thấy**
(không có bước nhảy nào để thấy — cả chuỗi cùng một thang sai) ⇒ cần thêm lưới theo **nhãn**, không chỉ theo nhảy.

#### (d) Hệ quả cho mục (c) — winsorize/n_min: **CHƯA THỂ THI HÀNH**
`valuation_scores` hiện vẫn được tính từ chuỗi còn phantom (BII/DDG/NOS vừa cách ly nhưng **chưa tính lại**);
kẹp biên `[p2, p98]` trên dữ liệu còn trộn thang đơn vị là **hợp thức hoá sai số** — đúng bài học đã chốt.
Thứ tự bắt buộc: **(1)** đủ neo cho 92 (hoặc 171) mã ⇒ cách ly hết phantom → **(2)** tính lại `valuation_scores`
→ **(3)** chạy lại phép thử bác bỏ trên bảng ĐÃ SẠCH → **(4)** mới áp `[p2, p98]` + `n_min = 5`.


### 7.4sexies NEO NGOÀI 8 MÃ — **đo trước khi thêm: 7/8 KHÔNG hề có phantom**

Chủ hệ thống công bố neo cho 8 mã (`PPS` 9.000–10.000 · `MGG` 23.500–24.500 · `VTJ` 3.400–3.600 ·
`MIE` 5.500 · `HHG` 700–1.000 · `PXA` 600–800 · `VIN` 30.000–40.000 · `NOS` 500). Đã nạp cả 8 vào
`EXTERNAL_ANCHOR`. **Kết quả lệnh cách ly: 0 dòng** — và đây là lý do, đo trên `daily_ohlcv` (dòng hợp lệ vị từ):

| mã | các DẢI giá thật trong store (median, số dòng) | đối chiếu neo | phán quyết |
|---|---|---|---|
| `PPS` | **9** (1.135 dòng) · **10.000** (17 dòng, 2026-08→10) | dải nhỏ = **9.000đ ở đơn vị "nghìn"** | THIẾU ~1000×, không phantom |
| `MGG` | **25** (894) · **24.400** (15) | 25 nghìn = 25.000đ ≈ neo | THIẾU ~1000× |
| `VTJ` | **4** (1.457) · **3.700** (13) | 4 nghìn = 4.000đ ≈ neo | THIẾU ~1000× |
| `MIE` | **7** (138) · **6.150** (8) | 7 nghìn = 7.000đ ≈ neo | THIẾU ~1000× |
| `HHG` | **2** (1.307) · **800** (117) | 2 nghìn = 2.000đ | THIẾU ~1000× |
| `PXA` | **1–2** (210) · **800** (72) | 2 nghìn = 2.000đ | THIẾU ~1000× |
| `VIN` | **15** (731) · **16.000** (9) | 15 nghìn = 15.000đ | THIẾU ~1000× |
| `NOS` | **400.000** (742) · 1.600 (696) · 700 (11) | 400.000đ = **800× neo** | **PHANTOM — đã cách ly ở §7.4quinquies** |

⇒ 7 mã kia **không có một dòng nào ≥100× cận trên neo** ⇒ không có gì để cách ly. Cột "tỉ lệ 300–3.000"
trong bảng trước là **tỉ số median(có nhãn)/median(không nhãn)**, KHÔNG phải mức giá — đọc nó thành
"bị nhân lên N lần" là **nhầm đơn vị đọc**.
**Defect thật của 7 mã này là ĐỐI XỨNG**: dải lịch sử bị **THIẾU ~1000×** (đơn vị "nghìn đồng"), dải gần
đây (2026-08→10) đã đúng đơn vị đồng. **Chứng minh nội tại, không cần neo:** bước nhảy ≥10× giữa hai dải
CHÍNH LÀ chỗ đổi đơn vị — nhân dải lịch sử ×1000 thì chuỗi liền mạch trở lại (PPS: 9→9.000 rồi 9.200 ✓).
Đây là **quyết định ĐƠN VỊ cho cả lịch sử** (đổi ~1.000–1.500 dòng/mã, ảnh hưởng mọi consumer), KHÔNG
phải việc cách ly ⇒ công cụ **chỉ khai báo 13 đoạn** (`THIẾU thang (~1000×)`) và **cấm tự rescale**.

**Lưới theo nhãn (mục ii) — đã làm, nhưng ở dạng KHAI BÁO, không tự cách ly:** 4.633 dòng / 171 mã,
742 đã cách ly ⇒ **3.891 dòng còn LIVE**. Quy tắc "nhãn + tỉ lệ ∈ [300, 3.000] ⇒ phantom" **bị bác bỏ**:
7 dòng mang nhãn rescaled của `PPS/VTJ/MGG/MIE/HHG/PXA/VIN` **KHỚP neo ngoài** (10.400 / 3.600 / 24.250 /
5.500 / 750 / 600 / 16.000đ) ⇒ nhãn đó là **bản VÁ ĐÚNG**, tự động cách ly sẽ **xoá dữ liệu đúng**.
Chỉ `NOS` (200.000đ = 800× neo) là phantom, đã xử lý. 697 dòng `NOS` còn lại (1.000–5.900đ = 2–12× neo)
⇒ khai báo mơ hồ, chưa đụng.

**Trạng thái công cụ:** `ruff All checks passed` · `5 passed` · chạy lại trên kho thật = **0 dòng cần ghi**
(idempotent) · 98 mục khai báo (85 mã không neo + 13 đoạn thiếu thang).

### 7.5 Tiêu chí DONE (đo được, không cảm tính)


- Sau B: số ca `PE < 2` = **0**; với mỗi `(symbol, kỳ)`, `z` tính lại từ chuỗi sạch khớp ±5% với bản backup.
- Mọi dòng `valuation_scores` có `is_clean_metric ∈ {0,1}`; mọi dòng `0` có `anomaly_flags` **không rỗng**.
- Với thang trailing-robust: không còn dòng nào `|z| > 1,5` mà không kèm cờ lý do.
- Test hồi quy (TDD, RED trước): dựng chuỗi 20 điểm ~12 có **1 điểm 12.500** ⇒ chứng minh `z_robust` của
  các điểm sạch **không bị co về 0** (bản cũ co mạnh ⇒ MoS kẹt ~50%), và **cột `ratio_value` không đổi**.

### 7.6 Chưa xác minh (ghi rõ để không hoá thành giả định)

- Nguồn `open_positions` (⇒ `mos_pct`) của **đường live**: `decision_guard.py:378` nhận qua tham số, chưa
  truy được ai dựng nó trong lượt này. Đường backtest (`grid_search_v2.build_open_positions`) thì đã
  xác minh từng dòng ở 7.1.
