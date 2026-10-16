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

### 7.4 Việc PHẢI ĐO trước khi chốt ngưỡng — **CHƯA ĐO ĐƯỢC trong lượt này**

Truy vấn read-only đã được phát lệnh nhưng cổng phê duyệt hết hạn ⇒ **các số dưới đây CHƯA có**, và
không được bịa để lấp chỗ trống:

1. Phân bố `z_score` thật: p50/p99/max `|z|`, và **số dòng `|z| > 1,5`** (đang bão hoà MoS).
2. `std` / `count` per `(symbol, ratio_name)`: bao nhiêu dòng `std ≤ 0` hoặc `count < 3` (nguồn sinh z vô cực).
3. **Độ nhạy thang đo**: `std` có/không 1% đuôi. Nếu tỉ lệ < 1,2 ⇒ lập luận "std bị đuôi nuốt" **SAI** và
   phải bỏ tầng 2 (đây là phép thử có thể bác bỏ chính đề xuất này).
4. `p_lo/p_hi` trailing: chọn bằng tiêu chí **z của các dòng SẠCH ổn định qua các cửa sổ**, không chọn
   theo "số trông đẹp".
5. Đếm số vị thế **thực tế** bị hạ ưu tiên xả do z rác (quy mô tác động lên quyết định, không chỉ số dòng).

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
