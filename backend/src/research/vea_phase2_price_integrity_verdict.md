# BIÊN BẢN PHÁN QUYẾT — PHA 2 (VEA): PHÁT HIỆN LỖI TÍNH TOÀN NỀN Ở TẦNG DỮ LIỆU GIÁ

- **Ngày phán quyết:** 2026-10-27
- **Cơ quan thực hiện:** Tổ công tác kiểm toán định lượng (thực thi Quyết nghị GO Pha 2 — mã VEA)
- **Trạng thái:** 🔴 **RED — PHA 2 BLOCKED (fail-closed)** · Pha 1 (FOC) phải thu hồi và chạy lại
- **Bất biến bị vi phạm:** Invariant #1 `FAIL_CLOSED_ON_ADJUSTED_PRICE` (engine cổ tức V2.3)
- **Cổng kiểm tra mới triển khai:** `backend/src/research/price_provenance_gate.py` (7/7 test PASS)

---

## 1. TÓM TẮT ĐIỀU HÀNH (cho Hội đồng)

Quyết nghị GO Pha 2 đặt giả định ngầm rằng dữ liệu giá trong `screener_cache.db` là **giá thô
(raw close)** — đúng như báo cáo Pha 1 trên FOC đã tuyên bố (`is_unadjusted_price=True`).
Kiểm tra trước khi chạy Pha 2 chứng minh **giả định này sai**:

> **Toàn bộ chuỗi giá lịch sử 2021–2025 của cả VEA lẫn FOC trong CSDL (nguồn `kbs`) là chuỗi
> ĐÃ ĐIỀU CHỈNH (back-adjusted server-side bởi KB Securities), KHÔNG phải giá giao dịch thô.**

Hệ quả trực tiếp:

1. **Pha 2 (VEA) KHÔNG THỂ THỰC THI** trên dữ liệu hiện có. Chạy tiếp sẽ tái diễn đúng lỗi đã
   bị kết tội ở Pha 1: giá đã loại cổ tức + engine lại cộng cổ tức tiền mặt ⇒ **cổ tức bị đếm
   hai lần**, giá vào lệnh sai ~2–3 lần, lát cắt ADV20 sai (volume cũng bị scalẻ).
2. **Toàn bộ số liệu báo cáo Pha 1 trên FOC (1,596 tỷ · CAGR 8,41% · B3 10,01% · 8 phiên slicing ·
   MDD −46,91%) trở thành NON-DECISIONAL** cho tới khi chạy lại trên giá thô. Trạng thái "đạt
   1/5 mã kiểm định" của Quyết nghị không còn cơ sở.
3. Một câu hỏi của Pha 2 vẫn trả lời được hợp lệ (Câu 1 — xem §5) vì dùng **đoạn 2026** —
   đoạn duy nhất còn nguyên giá thô.

Tất cả bằng chứng dưới đây **tái lập được** bằng lệnh:
`python -m backend.src.research.price_provenance_gate --symbols VEA,FOC` (exit code 1 = FAIL).

---

## 2. BẰNG CHỨNG (5 mảnh, độc lập chéo nhau)

### E1 — Kiểm tra lưới tick 100 VND (bằng chứng vật lý, không thể giả mạo)

Giá đóng cửa HOSE/UPCoM **bắt buộc** là bội số của 100 VND. Chuỗi điều chỉnh nhân
riêng (multiplicative) sẽ đẩy giá ra khỏi lưới tick. Kết quả quét `VALID_ROW_SQL`:

| Năm | VEA lệch lưới | FOC lệch lưới | Nhận định |
|---|---|---|---|
| 2021 | 223/250 (89,2%) | 231/249 (92,8%) | ADJUSTED |
| 2022 | 229/249 (92,0%) | 230/249 (92,4%) | ADJUSTED |
| 2023 | 226/249 (90,8%) | 234/249 (94,0%) | ADJUSTED |
| 2024 | 232/250 (92,8%) | 217/250 (86,8%) | ADJUSTED |
| 2025 | 203/249 (81,5%) | 85/249 (34,1%) | ADJUSTED |
| 2026 | **0/200 (0,0%)** | **0/161 (0,0%)** | RAW |

Mẫu hình "lịch sử lẻ → hiện tại tròn tuyệt đối" là chữ ký của back-adjust neo về hôm nay:
các bar **sau ngày GDKHQ cuối cùng** giữ nguyên giá thô, các bar trước đó bị nhân hệ số.

### E2 — Gọi trực tiếp API KB Securities (bằng chứng server-side, ngày 2026-10-27)

```
GET kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/VEA/data_day?...
  2021-01-04: o=27985.484 h=29093.82 l=27431.316 c=27542.1496 v=299320
  2026-10-26: o=38900     h=39000    l=38600    c=38800     v=430000
GET .../stocks/FOC/data_day?...
  2021-04-09: c=93526 (KHÔNG bội số 100)
```

- Giá 2021 **có phân số** (27542.1496) — bar thô không bao giờ có dạng này.
- Volume 2021 scalẻ lẻ (299.320 cp) — adjustment giữ nguyên giá trị giao dịch
  (turnover-preserving): giá ÷ F, volume × (1/F).
- Thư viện `vnstock` (vendored) chỉ là pass-through (`/1000`, không điều chỉnh) ⇒
  **adjustment nằm ở server KB Securities**, ảnh hưởng MỌI consumer `kbs`.

### E3 — Đối chiếu khoảng rơi ngày GDKHQ FOC (bằng chứng kinh tế)

Cổ tức FOC đợt 12/04/2021: **20.000 đ/cp** (provenance VSDC trong runner Pha 1).
Trên giá thô, giá phải rơi ~20.000đ ngay ngày GDKHQ. Thực tế trong CSDL:

| Ngày | Close trong DB | Chú thích |
|---|---|---|
| 2021-04-09 (cuối quyền) | 108.030 | giá điều chỉnh vintage cũ |
| 2021-04-12 (GDKHQ) | 103.130 | chỉ **−4,5%** |

Yêu cầu của cổ tức 20.000đ: **−18,5%**. observed ≪ expected ⇒ khoảng rơi đã bị "ăn" bởi
hệ số điều chỉnh. (Cổng `exdate_gap_check` tái lập phép đối chiếu này bằng test.)

### E4 — Nguồn độc lập VCI (Vietcap) cùng trả giá điều chỉnh

`vnstock` nguồn VCI, FOC 09/04/2021 = **93,53 nghìn** (khớp vintage KBS hôm nay 93.526),
VEA 04/01/2021 = 27,57 nghìn. Không có nguồn API thương mại nào đang dùng trong hệ thống
trả giá thô 2021.

### E5 — DB đang trộn NHIỀU VINTAGE điều chỉnh khác nhau

Cùng một ngày, DB và API hôm nay cho hai mức giá với **hệ số cố định 1,155×**:

| Ngày | DB (kbs, vintage cũ) | API KBS (hôm nay) | Hệ số |
|---|---|---|---|
| FOC 2021-04-05 | 112.730 | 97.593 | 1,1551 |
| FOC 2021-04-09 | 108.030 | 93.526 | 1,1551 |
| FOC 2021-04-20 | 87.660 | 75.900 | 1,1549 |

Lịch sử FOC 2021–2025 trong DB **không được pull lại** kể từ trước các đợt cổ tức gần đây
⇒ chuỗi trong DB không tự nhất quán: hai vintage điều chỉnh khác nhau dính cạnh nhau.
Đây cũng là lý do không thể "phục nguyên giá" bằng cách đơn giản.

---

## 3. PHÁN QUYẾT KỸ THUẬT

1. **`run_foc_empirical_audit.py` (Pha 1) bị treo ở trạng thái NON-DECISIONAL.**
   Số liệu 1,596 tỷ / CAGR 8,41% / B3 10,01% / slicing 8 phiên / MDD −46,91% **không được
   trích dẫn, không được đưa vào chỉ số so sánh "Dividend Sweeper"** (điều 2 Quyết nghị) cho
   tới khi chạy lại trên giá thô. Phương pháp luận (engine V2.3, provenance 2 chiều, T+2,
   Decree 126) **vẫn đứng vững** — lỗi nằm ở tầng dữ liệu đầu vào, không ở engine.
2. **Pha 2 (VEA) chuyển sang trạng thái BLOCKED.** Không được chạy `DividendAuditEngineV2`
   trên bars VEA 2021–2025 cho tới khi có giá thô. Mọi kết quả DRIP-vs-B3 trên dữ liệu
   điều chỉnh là **giả mạo vô tình**: cả hai kịch bản đều bị cộng cổ tức hai lần nhưng
   với cơ chế tái đầu tư khác nhau ⇒ so sánh A/B cũng vô nghĩa.
3. **Trạng thái "1/5 mã kiểm định" của `SYSTEM_MANIFEST.yaml` phải rút về 0/5** chờ đợt
   chạy lại (biên bản này ghi nhận; quyết định cuối thuộc Hội đồng).

---

## 4. ĐÃ TRIỂN KHAI NGAY (không chờ GO — phòng ngừa tái phát)

| Hạng mục | Chi tiết |
|---|---|
| Cổng phát hiện | `backend/src/research/price_provenance_gate.py` — kiểm tra lưới tick theo năm + đối chiếu khoảng rơi GDKHQ. CLI: `python -m backend.src.research.price_provenance_gate --symbols VEA,FOC` (exit 1 khi FAIL). |
| Test TDD | `backend/tests/test_price_provenance_gate.py` — 7 test, RED (exit 2, `ModuleNotFoundError`) → GREEN (13/13 PASS kèm test engine cũ). Có ca chống vá: chuỗi thô tổng hợp không bị báo oan; ca FOC thật (108.030→103.130) phải bị phát hiện. |
| Biên bản này | `backend/src/research/vea_phase2_price_integrity_verdict.md` |
| Manifest | Đăng ký cổng + cờ cảnh báo trên entry `foc_dividend_audit` (xem §6). |

**KHÔNG** sửa/xóa dữ liệu trong `daily_ohlcv` (freeze nguyên trạng — dữ liệu điều chỉnh vẫn
hữu ích cho biểu đồ, chỉ CẤM dùng cho audit cổ tức).

---

## 5. KẾT QUẢ TRUNG GIAN HỢP LỆ DUY NHẤT CỦA PHA 2 — CÂU 1 (CÔNG BỘ)

Đoàn 2026 của VEA là **giá thô đã được chứng minh** (0/200 bar lệch lưới). Trên đúng đoạn đó:

| Chỉ số | Giá trị (nguồn: bar 2026 thô, `VALID_ROW_SQL`) |
|---|---|
| ADV20 (20 phiên gần nhất, đến 26/10/2026) | **618.670 cp/phiên** |
| ADV20 bình quân 2026 (200 phiên) | 520.497 cp/phiên |
| Giá đóng cửa cuối | 38.800 đ (26/10/2026) |
| 1 tỷ đồng mua được (slip 50bps + phí 0,15%) | **25.600 cp** @ 39.052 đ/cp |
| Trần 20% ADV20 phiên đó | 123.700 cp |
| Tỷ trọng lệnh / ADV20 | **4,14%** |
| Số phiên giải ngân | **1 phiên duy nhất** (trần 20% không giữ vai) |
| ADV20 tối thiểu để 1 tỷ hấp thụ 1 phiên | 128.000 cp/phiên |

**Công nhận có điều kiện:** giả định thanh khoản dồi dào của Hội đồng ĐÚNG với regime 2026.
Điều kiện hiệu lực: **chỉ suy luận cho cửa sổ 2026 (raw)**; không được kéo ngược về 2021–2025
vì volume các năm đó đã bị scalẻ (không còn là khối lượng khớp thật).

---

## 6. LỘ TRÌNH MỞ KHÓA (đề xuất Hội đồng phê duyệt)

1. **Bước 1 — Thu giá thô 2021–2026 cho FOC & VEA.** Khả năng khả thi theo thứ tự ưu tiên:
   (a) **Dựng lại từ corporate actions có provenance** (giá thô = giá điều chỉnh / hệ số,
   hệ số tính tuần tự từ danh sách cổ tức đã đối soát VSDC) — tự kiểm bằng **lưới tick**:
   giá phục nguyên 100% phải nằm trên bội số 100; fail-closed nếu không đạt.
   (b) Nguồn thô thương mại thay thế (CafeF xuất Excel — endpoint
   `/du-lieu/Ajax/PageNew/DataHistory/PriceHistoryExport.ashx` hiện trả 400, cần theo dõi;
   hoặc feed có cột "Giá đóng cửa" tách rời "Điều chỉnh").
2. **Bước 2 — Đối soát VSDC 2 chiều** cho VEA 2021–2026 (Câu 3 của Quyết nghị): danh sách
   ngày GDKHQ + số tiền mặt/cp, khớp với (i) công bố công ty, (ii) chuỗi giá phục nguyên.
3. **Bước 3 — Chạy lại Pha 1 (FOC)** trên giá thô + đăng ký lại kết quả (kể cả kiểm chứng
   "Dividend Sweeper" B3 theo Điều 2 Quyết nghị — trên dữ liệu HỢP LỆ lần này).
4. **Bước 4 — Chạy Pha 2 (VEA)** trọn vẹn 3 câu hỏi, và mở rộng cổng
   `price_provenance_gate` vào **mọi** runner audit cổ tức trong tương lai.

---

## 7. BẢNG CÂN ĐỐI QUYẾT NGHỊ ↔ THỰC TẾ

| Điều của Quyết nghị GO Pha 2 | Thực tế kiểm toán | Phán quyết |
|---|---|---|
| "Thanh khoản VEA vượt trội, ADV20 500k–1,5M" | ADV20 2026 raw = 618.670 (khoảng tin cậy Hội đồng nêu ĐÚNG cho 2026) | ✅ Công nhận (cửa sổ 2026) |
| "1 tỷ có hấp thụ trong 1 phiên?" | 25.600 cp = 4,14% ADV20 ≤ 20% ⇒ 1 phiên | ✅ Trả lời được (2026) |
| "Kiểm chứng Dividend Sweeper DRIP vs B3" | Cần giá thô + provenance cổ tức | 🔴 BLOCKED |
| "Đối soát VSDC 2021–2026" | Chưa có nguồn độc lập trong phiên này (websearch/CafeF/Fireant đều chết/404) | 🔴 BLOCKED (Bước 2) |
| "Ghi nhận commit 15e09c8, bảo lưu 1/5 mã" | Commit 15e09c8 dùng dữ liệu điều chỉnh | ⚠️ 1/5 → 0/5 đề nghị rút |

---
*Tổ công tác báo cáo Hội đồng. Vi phạm bất biến là DỪNG — không thương lượng.*
