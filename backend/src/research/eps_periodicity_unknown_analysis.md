# ĐO NGUYÊN NHÂN `EPS_PERIODICITY_UNKNOWN` — kết quả và đề xuất

> Chủ sở hữu ghi chú: **Agent 1** (kiểm toán & định lượng). Dữ liệu thô:
> `netincome_coverage_request.json` (scratch run dir) · đo 2026-10-16 sau khi Hạng mục B chạy.
> Ghi chú ingestion của luồng TCBS vẫn thuộc `tcbs_ingestion_report.md` / `agent2_*.md`.

## 1. Sửa lại một con số tôi đã báo sai

Con số **27.779** trong báo cáo DoD là **số DÒNG mang thuộc tính `eps_flow_kind='UNKNOWN'`**, tức
thuộc tính cấp MÃ ghi lặp trên mọi ratio — **không phải** số dòng bị loại khỏi xếp hạng.

Số dòng **thực sự bị chặn** do không chứng minh được chu kỳ EPS (cờ `EPS_PERIODICITY_UNKNOWN`,
phạm vi PE+ROE) = **10.503 dòng**, thuộc **344 mã** (trên 791).

## 2. Phân loại 344 mã theo chính `best_dev` của bộ hiệu chuẩn

| Nhóm | Số mã | Số dòng bị chặn |
|---|---|---|
| Lệch **2–20%** (`LECH_NHE`) | **250** | **7.851** |
| Lệch **> 20%** (`LECH_BAN_CHAT`) | **94** | **2.652** |
| Không đủ dữ liệu để so (cửa sổ khuyết) | **0** | 0 |
| Thiếu `NET_INCOME` hoàn toàn | **0** | 0 |

⇒ **Không mã nào** bị cờ vì thiếu `NET_INCOME` hay vì cửa sổ 4 quý khuyết **hoàn toàn**.

## 3. Chữ ký sai lệch: LỆCH MỨC hệ thống, không phải sai chu kỳ

Đo `EPS_t / (NET_INCOME_t / shares_t)` theo từng kỳ (hằng số ⇒ lệch mức; dao động ⇒ sai chu kỳ):

| Mã | n | trung vị tỷ lệ | biến thiên (CV) | Kết luận |
|---|---|---|---|---|
| **VNM** | 30 | **0,891** | **1,2%** | **hằng số** ⇒ lệch MỨC ~−10,9% |
| MCM | 26 | 0,900 | 10,8% | hằng số ⇒ lệch MỨC −10% |
| PRE | 24 | 0,965 | 6,2% | hằng số ⇒ lệch MỨC −3,5% |
| LCG | 30 | 1,005 | 8,5% | ≈1 ⇒ nhiễu quanh 1 |
| AAH | 10 | **1,001** | 4,7% | khớp (đúng ca vci đo trước đây) |
| FPT | 75 | **4,535** | 20,1% | ≈×4,5 ⇒ EPS không cùng gốc với NI kỳ (lẫn lộn TTM/quý giữa các nguồn) |
| CII | 28 | 0,251 | **398%** | NI đổi dấu/gần 0 ⇒ dao động thật |

**Đọc kết quả:** phần lớn sai lệch là một **hệ số MỨC không đổi trong cùng một mã** (VNM CV =
1,2%!) — dấu hiệu của *khác biệt định nghĩa/mẫu số* (lợi nhuận mẹ vs toàn phần, hoặc số cổ phiếu
mà nhà cung cấp dùng vs `CHARTER_CAPITAL/10.000`), **không phải** sai chu kỳ.

## 4. Hệ quả cho quyết định "phương án (b): mở rộng phủ NET_INCOME"

**Mở rộng `NET_INCOME` sẽ KHÔNG giải phóng các dòng này.** Bằng chứng: 0/344 mã thiếu `NET_INCOME`;
cửa sổ khuyết chỉ là khuyết *rời rạc* và không phải nguyên nhân (nhiều mã có 30/34 kỳ có NI vẫn
bị cờ — vì sai lệch MỨC, không vì kỳ thiếu).

Việc mở rộng phủ dữ liệu cơ bản vẫn đáng làm cho các mục tiêu khác (ROE/loss check, mã mới,
`NET_PROFIT_PARENT`), nhưng **không phải đòn bẩy cho độ phủ xếp hạng định giá**.

### Đề xuất thay thế (cần GO vì là thay đổi PHƯƠNG PHÁP, không phải nới ngưỡng)

Tách bộ hiệu chuẩn thành 2 bước, **giữ nguyên dung sai 2%** (không hạ chuẩn):

1. **Chu kỳ (shape)** — chấp nhận một quy ước nếu *độ ổn định* của tỷ lệ là cao:
   `CV(EPS/ứng_viên) ≤ 2%`, thay vì đòi `|trung vị tỷ lệ − 1| ≤ 2%` (điều kiện sau trộn lẫn
   "sai chu kỳ" với "lệch mức", chính là điều đang xảy ra ở VNM/MCM/PRE).
2. **Mức (level)** — ghi cờ **thông tin** `EPS_LEVEL_MISMATCH` khi `|trung vị tỷ lệ − 1| > 2%`
   (VNM sẽ thành công với `EPS_LEVEL_MISMATCH`; PE vẫn dùng EPS như nhà cung cấp công bố nên
   không bị lệch theo).

**Fail-closed vẫn giữ:** nếu **hai quy ước cùng ổn định** (hiếm: NI gần như phẳng) ⇒
`EPS_PERIODICITY_AMBIGUOUS` + `is_clean_metric = 0` — vì chọn sai ở ca này làm PE lệch 4×.

**Ước tính tác động:** nhóm `LECH_NHE` (7.851 dòng) phần lớn sẽ được giải phóng; nhóm dao động
thật (CII, FPT do trộn nguồn) **vẫn bị chặn** — đúng như mong muốn.

**Việc đã làm ngay theo chỉ đạo:** khóa cứng ngưỡng bằng test
(`test_nguong_hieu_chuan_bi_khoa_khong_duoc_noi`) — nới `CALIB_TOL` sẽ làm test FAIL.

---

## 5. KẾT QUẢ ĐO SAU KHI THỰC THI (Bước 5, 2026-10-16) — sửa lại ước lượng ở §4

> **DẤU VẾT KẾT LUẬN CŨ (đã bị số đo bác bỏ):** §4 dự báo *"nhóm `LECH_NHE` (7.851 dòng) phần lớn
> sẽ được giải phóng"*. **SAI.** Thực tế **+1.093 dòng sạch (+531 PE)**. Giữ lại đây để không lặp
> lại kiểu ngoại suy từ một thống kê (median) sang một thống kê khác (độ tán xạ).

| Vòng | Tiêu chí độ ổn định | Dòng sạch | PE sạch | UNKNOWN | Phán quyết |
|---|---|---|---|---|---|
| trước B5 | `median\|r−1\| ≤ 2%` | 34.707 | 3.929 | 27.779 | — |
| **5a** | `CV = σ/μ ≤ 2%` | 29.313 ↓ | 1.359 ↓ | 42.096 ↑ | **BÁC BỎ** |
| **5b** | `1,4826·MAD(r)/median(r) ≤ 2%` | **35.800** | **4.460** | 24.641 | **GIỮ** |

`σ/μ` hỏng vì bị **vài kỳ dị biệt** chi phối ⇒ veto cả mã dù phần lớn kỳ khớp tuyệt đối. `MAD`
(điểm gãy 50%) xử lý đúng nhóm "đa số khớp + vài kỳ lệch" (MCM `r ≡ 0,900 ± 0,001`; PRE `0,965`
với 2 kỳ `1,03`) mà **không nới ngưỡng 2%**.

### Ba giả thuyết của tôi về nguyên nhân UNKNOWN — cả ba đều BỊ BÁC BỎ

| Giả thuyết | Phép đo | Kết quả |
|---|---|---|
| Thiếu độ phủ `NET_INCOME` | 344 mã mang cờ | **0 mã** thiếu, **0** mã khuyết cửa sổ hoàn toàn |
| Trộn nguồn trong cùng ô `(mã,kỳ,metric)` | `COUNT(DISTINCT source)` | **0/622.992 ô** có >1 nguồn |
| EPS và NI cùng kỳ khác nguồn | so `source` cặp EPS↔NI | chỉ **2 cặp** (BCM 2025Q3, VCB 2025Q2) |

### Kết luận đúng về 24.641 dòng còn lại

Đó là các mã **không có đa số kỳ khớp cùng một quy ước** ⇒ không thể chứng minh chu kỳ EPS từ dữ
liệu hiện có. **Không sửa được bằng phương pháp** — hai đòn bẩy thật sự đều là dữ liệu cơ bản chưa
tồn tại trong kho (**0/66 metric**): `NET_PROFIT_PARENT` (lợi nhuận cổ đông mẹ) và **số cổ phiếu
bình quân gia quyền** mà nhà cung cấp dùng để tính EPS. Giữ chúng ngoài bảng xếp hạng là **hành vi
đúng của fail-closed**, không phải nợ kỹ thuật cần "sửa cho đủ".
