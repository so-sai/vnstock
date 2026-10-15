# AUDIT: kho dữ liệu cơ bản `backend/data/financial_facts.db` (209 MB)

Ngày audit: **2026-10-15** · chế độ **read-only** (`file:...?mode=ro`) · mọi con số dưới đây đo bằng SQL.
Quy mô: **615.528 dòng `financial_facts` · 791 mã · 55 kỳ**; kèm `financial_facts_annual` (28.730 dòng),
`valuation_scores` (7.318), `health_ratios`, `forensic_scores`, `entity_registry` (1.967), `ingestion_log` (34.913).

---

## 1. Ma trận coverage & VÙNG TỐI

| Kỳ | mã | trong đó BS/IS | dòng |
|---|---|---|---|
| 2018Q1 → 2024Q4 | 696 – 746 | 696 – 745 | 18.661 – 20.124 |
| 2025Q1 | 741 | 740 | 19.933 |
| 2025Q2 | 741 | 740 | 19.977 |
| **2025Q3** | 593 | **198** | **4.495** |
| 2025Q4 | 733 | 730 | 8.772 |
| 2026Q1 | 731 | 726 | 8.609 |
| 2026Q2 | 729 | 724 | 8.410 |
| 2026Q3 | 11 | 11 | 90 |

- Từ vựng metric **ổn định 47 metric / 3 statement_type ở MỌI kỳ** ⇒ không phải mất chỉ tiêu, mà là
  **mật độ phủ theo mã rơi**.
- **VÙNG TỐI = từ 2025Q3 tới nay**: dòng/kỳ còn 22–44% mức chuẩn; riêng 2025Q3 chỉ 198 mã có BS/IS.
- 2026Q3 gần trống là **bình thường** (quý kết thúc 30/09, hạn nộp ~30/10).
- ⇒ Dữ liệu dùng được cho chuỗi dài PIT: **2018Q1 – 2025Q2** (30 quý liên tục, đủ dày).

## 2. Truy vết nguồn gốc `valuation_scores`

- **Writer:** `src/financial/valuation_engine.py:430` (`INSERT OR REPLACE INTO valuation_scores`).
- Dữ liệu 2026Q1/Q2 vào kho chủ yếu từ **vci** (2026Q1: 717 mã/8.425 dòng; 2026Q2: 710/8.215);
  `cafef` chỉ 8 mã (2026Q1) nhưng **có nhật ký** trong `ingestion_log`
  (`cafef_20260916_090003`, `cafef_20260908_090001` — batch 09:00, ghi 4–13 fact/mã).
  ⇒ **KHÔNG có API thứ ba không nhật ký.**
- **Chuỗi provenance của SHB đã đóng:** `financial_facts` có `BOOK_VALUE_PS = 15.677,66 VND`
  (SHB, 2026Q2, nguồn vci); `valuation_scores` 2026Q2 PB = **0,8643** với `price = 13.550`
  ⇒ 13.550 / 0,8643 = **15.677** — khớp tuyệt đối với BVPS đã lưu.
- **HỆ QUẢ ĐO ĐƯỢC:** tại giá 15/10 = 10.400 và BVPS 15.677,66 ⇒ **P/B hiện tại = 0,663**
  (khớp vùng 0,66–0,74 mà app hiển thị; app dùng BVPS 14.093 ⇒ 0,74 — **hai nguồn BVPS lệch ~10%**).
- **LỖ HỔNG PROVENANCE:** kho **không có metric số cổ phiếu lưu hành** (0 dòng khớp
  `%lưu hành%` / `%share%`) ⇒ `BOOK_VALUE_PS` là số **nhà cung cấp đưa sẵn**, không kiểm chứng lại
  được bằng (vốn chủ sở hữu ÷ số cổ phiếu). Muốn tự tính BVPS phải bổ sung chuỗi cổ phiếu.

## 3. `integrity_flags`, đơn vị, và dị thường

**Phân bố cờ (615.528 dòng):** `OK` 556.661 (90,4%) · `OK;SUSPECTED_PARENT_STATEMENT` 41.799 ·
**`AUTO_SCALED_1000000000x` 13.068** · `SUSPECTED_PARENT_STATEMENT` 3.566 ·
`AUTO_SCALED_1e9;SUSPECTED_PARENT` 358 · rỗng 64 · `AUTO_SCALED_1000000x` 7 · `VERIFIED_AUDIT;PATCHED_BY_PTCR_2025` 4.

- `unit` = **VND đồng nhất** (BS 394.466 / IS 131.900 / CF 89.162) ⇒ bẫy nhãn *"(triệu)"* quan sát được
  ở **API thô** (vci trả `'Số CP lưu hành (triệu)' = 1.203.119.924`) **không lọt vào kho** — kho đã chuẩn hoá.
- `is_synthetic` = **0**; định dạng `period` hợp lệ **615.528/615.528**; VCSH âm = **0**.
- **Dị thường ở TẦNG PHÁI SINH (`valuation_scores`)**: `PE` có **40 dòng < 2** (min **0,019**, max **12.500**);
  `PB` max **79,66**; `EV_EBITDA` max **3.386**; `PS` max **444**.
  ⇒ Không dùng trực tiếp các ratio này làm bộ lọc; phải winsorize/audit. Ca `PE = 0,8413` (SHB 2024Q4)
  nằm trong nhóm hỏng này.

## 4. Kế hoạch vá `effective_date`

| Số đo | Giá trị |
|---|---|
| Dòng có `period` phân tích được (`YYYYQn`) | **615.528 / 615.528 = 100%** |
| Dòng có `reported_at` dùng được | **4** (và là chuỗi kỳ `"2025Q3"`, **không phải ngày**) |
| Nguồn có ngày công bố | vci 600.295 (0) · vnstock 14.607 (0) · vietstock 497 (0) · cafef 115 (0) · api 9 (0) |

⇒ **`STATUTORY_LAG` là cơ chế CHÍNH, không phải fallback** (nguồn không cung cấp `issueDate`).

**Công thức đề xuất:** `effective_date = period_end + 30 ngày` (quý) · `+ 90 ngày` (năm/kiểm toán);
`effective_date_basis = 'STATUTORY_LAG'`; 4 dòng nguồn tài liệu → đối chiếu tay → `'FILING'`.
Bulk-update theo `period` (≤ 55 nhóm) rồi gán hàng loạt — một lượt, rẻ, không cần đọc lại dòng nào.

**Hai rủi ro phải ghi rõ:**
1. Doanh nghiệp **nộp muộn/gia hạn** ⇒ lag luật định vẫn có thể sớm hơn thực tế. Nếu muốn bảo thủ hơn:
   dùng **+45 ngày** cho quý (vẫn rẻ hơn nhiều so với bỏ qua PIT hoàn toàn).
2. Sau khi vá `effective_date`, **`valuation_scores` phải tính lại theo PIT** — hiện nó có thể đã dùng
   số mới nhất bất kể ngày công bố (đúng loại look-ahead mà audit này sinh ra để chặn).

**Cột `revision`:** hiện **0 nhóm trùng** theo `(symbol, period, statement_type, metric)` ⇒ chưa cần gấp,
nhưng **phải thêm** vì nguồn đã chứng minh tồn tại bản trùng kỳ (kbs trả cột `2025-Q4_1`).

## 5. Kết luận & hệ quả cho chu kỳ này

1. **"SHB rẻ" — KIỂM CHỨNG ĐƯỢC**: P/B **0,663** (giá 10.400 / BVPS 15.677,66 kỳ 2026Q2, nguồn vci),
   đúng vùng app hiển thị 0,66–0,74. Luận điểm thoát khỏi diện "tự sự không kiểm chứng".
2. **"LLR 80% · NPL 2,2% · NIM 2,2% · LDR 1,1" — KHÔNG KIỂM ĐƯỢC**: từ vựng 47 metric của kho là
   **chỉ tiêu doanh nghiệp phi tài chính** (`CURRENT_ASSETS`, `INVENTORY`, `RECEIVABLES`, `COGS`,
   `GROSS_PROFIT`…) và **không có metric ngân hàng** (NPL/LLR/NIM/cho vay/tiền gửi). Muốn xác nhận
   luận điểm chất lượng tài sản phải bổ sung nguồn riêng — không thể "suy" từ kho hiện có.
3. Phủ không đều theo chỉ tiêu ở 2026Q2: `BOOK_VALUE_PS` 656 mã · `TOTAL_EQUITY` 642 · `TOTAL_ASSETS` 628 ·
   `REVENUE` 629 · `EPS` 517 · **`NET_INCOME` chỉ 118 mã**.
4. `backend/data/financial_data.db` = **0 byte** ⇒ xác nhận file rác (đã được chủ dự án xác nhận),
   loại khỏi luồng dữ liệu, không xoá để tránh gãy path.

## 6. Việc kế tiếp (đề xuất, chờ GO)

| # | Việc | Vì sao |
|---|---|---|
| A | Bulk-update `effective_date` + `effective_date_basis` (mục 4) | mở khoá PIT cho 615k dòng — điều kiện tiên quyết của mọi thứ khác |
| B | Tính lại `valuation_scores` theo PIT sau khi có `effective_date` | hiện có thể chứa look-ahead |
| C | Bổ sung chuỗi **số cổ phiếu lưu hành** (`shares_history`) | để tự kiểm chứng BVPS/EPS thay vì tin số nhà cung cấp |
| D | Bổ sung **metric ngân hàng** (NPL/LLR/NIM/cho vay/tiền gửi) | hiện không có ⇒ không kiểm được luận điểm chất lượng tài sản |
| E | Winsorize/audit tầng ratio (PE<2, PB>79, EV/EBITDA>3.386) | chặn dị thường lọt vào bộ lọc |
