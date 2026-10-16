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

## 4. Phát hiện thứ hai (nghiêm trọng hơn, cùng họ với PIT): **số cổ phiếu không theo thời điểm**

`valuation_engine.py:351` — `shares_now = self._get_latest_shares(facts)` rồi dùng
**cùng một số cổ phiếu hiện tại cho MỌI kỳ** khi tính BVPS/EPS-per-share. Hệ quả:
- BVPS của kỳ 2018 được tính bằng số cổ phiếu của **2026** ⇒ mọi thay đổi vốn (chia thưởng,
  phát hành thêm) từ 2018→2026 **làm lệch BVPS quá khứ** — một dạng look-ahead **cùng loại**
  với lỗi PIT vừa vá ở Hạng mục A, nhưng nằm trong tầng ratio.
- Đây cũng giải thích vì sao nhiều dòng `PB` không có `BOOK_VALUE_PS` đi kèm.
→ Cờ đề xuất: **`SHARES_NOT_PIT`**; và Hạng mục C (chuỗi `shares_outstanding` theo thời gian)
trở thành **điều kiện tiên quyết** để B cho ra số đúng, không phải việc "nice to have".

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
| `SHARES_NOT_PIT` | BVPS/EPS dùng `shares_now` | giữ để xem, **cấm** dùng cho backtest |
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
