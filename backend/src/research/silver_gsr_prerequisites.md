# Bạc & GSR — Pháp y dữ liệu + Kiểm định tiên quyết (NON-DECISIONAL)

**Loại:** Research note — **NON-DECISIONAL**. Không sinh tín hiệu, không đặt ngưỡng giao dịch.
**Ngày đo:** 2026-10-30. **Phạm vi:** NQ-10/NQ-HĐKT nhiệm vụ 1 (dữ liệu) và nhiệm vụ 2 (kiểm định).
**Nguồn số:** `ptck.py backfill-macro` (Yahoo `SI=F`, `GC=F`) + `ptck.py silver stationarity`
+ `backend/data/screener_cache.db` (`macro_history`, `macro_history_v2`, `*_quarantine`).

---

## 0. Kết luận ngắn (đọc trước khi trích dẫn bất kỳ con số nào)

1. **Chuỗi bạc cũ (2025-06-10 → 2026-10-29) là chuỗi NHIỄM** và không tái lập được từ một
   lần tải duy nhất. Đã **cách ly toàn bộ** rồi nạp lại một nguồn liên tục 2016-01-04 →
   2026-10-29 (2.721 phiên). Toàn bộ số liệu báo cáo ngày 30/10 (bản trước) **phải thay**
   — xem §2.
2. **Không có bằng chứng** log giá bạc hay log GSR khác random walk ở mức 5% trên mẫu hiện
   có (ADF t = −0,63 và −2,39 trên toàn mẫu). Vì vậy **không được** tuyên bố "GSR có điểm
   cân bằng 65–70", và **không được** coi half-life 51–73 phiên là tham số.
3. **Chuỗi vàng `GOLD_XAU` vẫn là chuỗi cũ chưa thay** (1.293 dòng trùng ở v1, lệch tới
   1,59% so với `GC=F` tải lại). Kết quả Engle–Granger dưới đây **chỉ là chỉ báo sơ bộ**.

---

## 1. Pháp y dữ liệu bạc (`XAGUSD`)

### 1.1 Bằng chứng chuỗi cũ bị nhiễm (đo trên `macro_history`, trước 2026-10-30 09:45)

| Chỉ tiêu | Số đo |
|---|---|
| Số dòng / số ngày phân biệt | **361 dòng / 319 ngày** ⇒ 42 dòng trùng ngày |
| Bar rơi vào cuối tuần | **2** — 2026-10-04 (Chủ nhật), 2026-10-17 (Thứ Bảy) |
| Lệch cực đại so với `SI=F` tải lại | **5,046%** tại 2026-09-10 (n chồng lấn = 316 ngày) |
| Ngày lệch đầu tiên | 2026-07-23 |
| Nguồn trong bảng | `NULL` và `'yahoo'` trộn lẫn |

Hai bar cuối tuần là bằng chứng **định tính** mạnh nhất: hợp đồng tương lai COMEX không có
phiên Thứ Bảy/Chủ Nhật, nên hai dòng đó **không thể** đến từ `SI=F`. Nghĩa là chuỗi là hỗn
hợp nhiều nguồn/quy ước (snapshot trong phiên vs giá settle vs nguồn khác).

**Đã loại trừ giả thuyết "range-dependence của yfinance":** ba khung truy vấn khác nhau
(`period=5d`, `period=2y`, `start=2016-01-01`) trong cùng ngày cho **cùng một giá trị cuối**
(51,470). Vậy khác biệt không nằm ở cách hỏi vendor hôm nay, mà ở **giá trị đã lưu trước đó**
— đúng loại lỗi E19 (scale-switch/bar trộn quy ước), chỉ khác là ở chuỗi macro thay vì OHLCV.

### 1.2 Hành động (fail-closed, không "giữ phần tốt")

Không chọn ranh giới "từ ngày X trở đi thì đúng" — chọn ranh giới nào cũng là suy đoán.
Thay vào đó **cách ly toàn bộ** rồi nạp lại từ **một** nguồn, có audit trail:

```
ptck.py backfill-macro --variables XAGUSD --start 2016-01-01 --min-rows 1000 \
        --replace-series --reason "NQ-10 ..."
```

| Bảng | Dòng bị cách ly | Ghi chú |
|---|---|---|
| `macro_history_quarantine` | **3.082** | 361 (lần 1) + 2.721 (lần 2, sau khi nâng trần registry) |
| `macro_history_v2_quarantine` | **3.030** | 314 (lần 1) + 2.716 (lần 2) |

Lần chạy thứ hai là **bắt buộc về mặt kỹ thuật**, không phải "làm lại cho chắc": xem §1.3.

### 1.3 Defect thứ hai — trần `canonical/registry.py` từ chối dữ liệu THẬT

| Biến | Trần cũ | Dòng thật bị từ chối | Hệ quả |
|---|---|---|---|
| `XAGUSD` | 100 | **5 dòng** (2026-01-23 → 2026-01-29, max 115,080) | v2 (2.716) thiếu đúng tuần blow-off top so với v1 (2.721) |
| `GOLD_XAU` | 5.000 | **52 dòng** (2026-01-26 → 2026-03-17, max 5.318,40) | v2 (1.407) thiếu **25** ngày so với v1 |

Đây là loại lỗi **không nổ ra như lỗi**: dữ liệu vẫn nằm ở v1, chỉ lặng lẽ vắng ở v2 — nơi
`uncertainty_layer.py`, `gold_h2_series.py`, `commodity_hit_rate.py` đọc. Mẫu số bị hụt đúng
đoạn biến động mạnh nhất ⇒ mọi thống kê GSR/beta giai đoạn đó lệch mà không có cảnh báo.

Đã nâng trần: `XAGUSD` → **500**, `GOLD_XAU` → **20.000** (vẫn chặn được lỗi thang ×10:
bạc ~1.150, vàng ~53.184). Test: `backend/tests/test_canonical_commodity_bounds.py`.

### 1.4 Chuỗi mới — kiểm chứng sau khi nạp

| Chỉ tiêu | Số đo |
|---|---|
| Số dòng / ngày phân biệt (v1) | 2.721 / 2.721 |
| v2 | 2.721 dòng, **date-set khớp v1 tuyệt đối**, 0 giá trị lệch |
| Khoảng | 2016-01-04 → 2026-10-29 |
| Bar cuối tuần | **0** |
| Giá trị 0 / âm | **0 / 0** |
| Min – max | 11,735 – 115,080 USD/oz |
| σ năm hoá (log-return) | **34,7%** |
| CAGR 2016-01 → 2026-10 | **+13,0%/năm** (tổng +272%) |
| Biến động ngày lớn nhất | **−37,61%** (2026-01-30); kế tiếp +13,12% (2026-01-26) |

**Cú rơi 2026-01-26 → 2026-01-30 là THẬT, không phải splice:** `SI=F` OHLC ngày
2026-01-29 high 121,30 / close 114,04; 2026-01-30 open 116,86 / low 76,03 / close 78,29.
`GC=F` cùng ngày 5.318,4 → 4.745,1 (−10,8%) ⇒ kim loại quý có blow-off top tháng 01/2026.
Chỉ có 1 log-return vượt 10σ trong 2.720 quan sát — phù hợp một cú vỡ bong bóng thật, không
phải hàng loạt bar lỗi.

---

## 2. SỐ LIỆU ĐÃ SỬA — thay bản báo cáo 30/10/2026 (trước khi làm sạch)

Bản báo cáo trước dùng giá bạc **52,94** (một dòng trong chuỗi nhiễm) thay vì **51,46**.

| Đại lượng | Bản trước (chuỗi nhiễm) | **Bản đã sửa (chuỗi sạch)** |
|---|---|---|
| Bạc thế giới 28/10 → 29/10 | −2,11% | **−3,74%** (53,457 → 51,460) |
| GSR (vàng/bạc) | 76,31 | **78,51** (lệch +2,80%) |
| Nội địa 47,5M → 44,853M | −5,57% | −5,57% (không đổi — đo từ bảng nội địa) |
| Dư do nén premium | −3,54% | **−1,91%** |
| Premium ask 29/10 | — | **+4,87%** (bid +1,69%) |
| Premium ask 28/10 (47,5M) | +5,67% | **+6,91%** |

Phân rã vẫn đúng về hình thức, nhưng **trọng số đảo**: trong bản cũ, phần lớn cú giảm được
quy cho "nén premium" (−3,54% trên −5,57%); trên chuỗi sạch, **hai phần gần tương đương**
(−3,74% thế giới và −1,91% premium), tức **thị trường thế giới giảm là nguyên nhân chính** —
ngược với kết luận "dealer nội địa nén biên" của bản trước. Đây là ví dụ cụ thể cho luật:
*số liệu sai làm đảo cả kết luận nhân quả*, không chỉ lệch vài phần trăm.

---

## 3. Kiểm định tiên quyết — kết quả đo (NON-DECISIONAL)

Lệnh: `ptck.py silver stationarity` (module toán `backend/src/analysis/stationarity.py`;
venv **không có** `statsmodels` ⇒ ADF/Hurst/VR/OU/EG tự viết và được kiểm chứng bằng mô phỏng,
xem `backend/tests/test_stationarity_diagnostics.py`).

### 3.1 Bạc riêng (không phụ thuộc chân vàng)

| Cửa sổ | n | ADF (log XAGUSD) | Hurst (R/S trên log-return) |
|---|---|---|---|
| 2016-01-04 → 2026-10-29 (**chứa OOS**) | 2.721 | t = **−0,63** ⇒ không bác bỏ RW | H = **0,574** (random-walk) |
| 2016-01-04 → 2024-12-31 (**in-sample**) | 2.261 | t = **−1,39** ⇒ không bác bỏ RW | H = **0,557** (random-walk) |

### 3.2 Vàng–bạc & GSR (cặp giao ngày chung, vàng chỉ có từ 2021-04-05)

| Chỉ tiêu | Toàn mẫu (1.403 phiên, chứa OOS) | In-sample ≤ 2024-12-31 (943 phiên) |
|---|---|---|
| GSR cuối kỳ | 78,43 (phân vị 34,3%) | 91,26 (phân vị 96,6%) |
| GSR min–max / trung vị | 44,14–105,41 / 82,69 | 65,97–97,39 / 83,30 |
| Tỷ lệ phiên GSR ≤ 65 / ≤ 70 | 8,4% / 19,3% | 0,0% / 7,6% |
| ADF log GSR | t = **−2,39** ⇒ không bác bỏ RW | t = **−2,64** ⇒ không bác bỏ RW |
| Variance ratio (bạc) | VR(2)=0,95 · VR(5)=0,91 · VR(10)=0,83 · VR(20)=0,76 · VR(40)=0,81 | — |
| OU half-life (log GSR) | **73,1 phiên** (φ=0,9906, t(β)=−2,63) | **50,8 phiên** (φ=0,9864, t(β)=−2,64) |
| Trung bình dài hạn suy ra từ AR(1) | GSR ≈ **80,49** | GSR ≈ **83,38** |
| Engle–Granger (log vàng ~ log bạc) | β = **0,799**, residual ADF t = **−2,63** (CV EG 5% = −3,34) | β = **0,802**, t = **−1,97** |

### 3.3 Cách đọc — và giới hạn

* **ADF:** không bác bỏ H0 ở cả bạc lẫn GSR ⇒ *không đủ bằng chứng* nói chuỗi hồi quy trung
  bình. "Không bác bỏ" **không** đồng nghĩa "đã chứng minh random walk" — chỉ là mẫu hiện có
  chưa đủ sức bác bỏ. Giá trị tới hạn dùng là **tiệm cận** (N → ∞), không hiệu chỉnh hữu hạn.
* **OU half-life:** điểm ước lượng 51–73 phiên **nhưng** t(β) = −2,63 không vượt −2,86 ở 5%
  ⇒ bản thân β chưa chắc khác 0 ⇒ half-life chưa đủ tin cậy để làm tham số. Ghi nhận là
  *hypothesis-generating*, không phải *parameter*.
* **Engle–Granger:** t = −2,63 > −3,34 ⇒ **không bác bỏ** "không đồng tích hợp". Cộng thêm
  rủi ro chuỗi vàng còn nhiễm (§0.3) ⇒ **chưa thể** dùng cặp vàng–bạc như một hệ đồng tích hợp.
* **Vùng 65–70:** trong in-sample, GSR **chưa từng** xuống ≤ 65 (0,0% số phiên) và chỉ 7,6%
  số phiên ≤ 70. Vì vậy "65–70 là điểm cân bằng" **không có cơ sở thống kê** từ dữ liệu này;
  nó là một *quan sát lịch sử ngắn*, không phải hằng số cân bằng.
* **Variance ratio:** VR < 1 ở mọi q cho dấu hiệu mean-reversion nhẹ, nhưng z đi kèm chỉ đúng
  dưới giả định homoskedastic — bạc có cụm biến động lớn (2020-03, 2026-01) nên z **không**
  được trích dẫn như p-value.

**Cấm:** dùng 51 hay 73 phiên làm "chu kỳ hồi quy"; đặt ngưỡng vào/ra quanh 65–70; suy ra
"bạc rẻ" từ bất kỳ ô nào của bảng trên. Muốn chuyển sang DECISIONAL: phải **đăng ký giả
thuyết trước**, rồi kiểm chứng XUÔI bằng forward paper-trading (AGENTS bất biến #3).

---

## 4. Việc còn treo (đã ghi vào `tech_debt_backlog.md`)

1. `GOLD_XAU`: 1.293 dòng trùng ở v1, chuỗi hỗn hợp quy ước (lệch max 1,59% tại 2026-10-27),
   v2 vẫn thiếu 25 ngày > 5.000 cho tới khi chạy lại `--replace-series`. Blast radius khi thay:
   `uncertainty_layer.py`, `gold_h2_series.py`, `commodity_hit_rate.py`.
2. `macro_history_v2` không có ràng buộc "mọi ngày của v1 phải có mặt" ⇒ lệch date-set là
   **dấu hiệu duy nhất** phát hiện validator từ chối dữ liệu. Cần một cổng kiểm tự động.
