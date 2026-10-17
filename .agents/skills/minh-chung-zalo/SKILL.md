---
name: minh-chung-zalo
description: Kỹ năng thực thi siêu tốc (<30ms) cho Minh Chứng (PTCK_VNSTOCK) qua binary Rust bản địa `minh_chung.exe` kết nối trực tiếp `winsqlite3.dll`, CLI `ptck.py` và Zalo PC CDP Loopback (127.0.0.1:58721). Dùng khi đọc/gửi báo cáo Zalo "My Documents", tra cứu nhanh cổ phiếu/BCTC, đồng bộ CLI hoặc điểm tin vĩ mô.
---

# Kỹ năng Minh Chứng (`minh_chung.exe`) – Thực Thi Bản Địa `< 30ms` & Cầu Nối CLI `ptck.py`

## 1. Kỷ Luật Tối Thượng (Zero Runtime Scripting)
- **TUYỆT ĐỐI CẤM** tự viết hoặc sinh ra các file `.py`, `.ps1`, `.js` tạm thời để đọc/gửi tin nhắn Zalo hoặc truy vấn nhanh cổ phiếu/BCTC.
- Mọi thao tác I/O với Zalo PC (`127.0.0.1:58721`), đồng bộ CLI định kỳ và truy vấn nhanh SQLite (`screener_cache.db`, `financial_facts.db`) **PHẢI** gọi trực tiếp file thực thi Rust bản địa `.\minh_chung.exe` tại thư mục gốc dự án.

## 2. Bảng Lệnh Chuẩn (`minh_chung.exe`)

| Tác vụ | Lệnh thực thi chuẩn | Tốc độ / Cơ chế |
|---|---|---|
| **Tra cứu nhanh Cổ phiếu + BCTC** | `.\minh_chung.exe query <MÃ_CP>` *(VD: `.\minh_chung.exe query SHB`)* | **`~3.5ms`** (`winsqlite3.dll`) |
| **Tra cứu Cổ phiếu & Gửi thẳng Zalo** | `.\minh_chung.exe query <MÃ_CP> --send` | **`~15–30ms`** (Kèm khóa `ZaloPC_Bridge_Mutex`) |
| **Điểm tin Sáng (Pre-Market 08:45)** | `.\minh_chung.exe brief --sync` *(hoặc `.\minh_chung.exe brief`)* | Chạy CLI `ptck.py` + Xuất bản tin Sáng (`~26ms`) + Tự hồi phục Daemon |
| **Tổng kết Chiều (ATC Summary 15:15)** | `.\minh_chung.exe summary --sync` *(hoặc `.\minh_chung.exe summary`)* | Chạy CLI `ptck.py` + Xuất bản tin ATC (`~26ms`) + Tự hồi phục Daemon |
| **Đồng bộ CLI Vĩ mô & Lãi suất** | `.\minh_chung.exe sync` *(hoặc `--send`)* | Gọi `python ptck.py sbv-update` & `python ptck.py fetch-macro` |
| **Đọc tin nhắn mới nhất trên Zalo** | `.\minh_chung.exe read` | **`~3.5ms`** (Tự động Self-Boot Zalo CDP nếu chưa bật) |
| **Gửi tin nhắn/báo cáo vào Zalo** | `.\minh_chung.exe send "<Nội dung báo cáo>"` | **`~25ms`** |
| **Tự động bật Zalo CDP & 2 Daemons** | `.\minh_chung.exe boot` | Tự dò `Zalo.exe` mới nhất + Bật `minh_chung.exe watch` & `minh_tam.exe watch` |
| **Chạy Daemon Phản xạ `<1s` 24/7** | `.\minh_chung.exe watch` | Canh `"My Documents"` chu kỳ `100ms` (`~3.7MB RAM`) |

## 3. Phân Định Tính Cách & Văn Phong Chuyên Môn (Agent Persona & Executive Tone)

Hai Bot được thiết kế với văn phong sắc bén, ngắn gọn, giàu thông tin và tuyệt đối không rườm rà:

### 🎯 Bot 1: Minh Tâm (`minh_tam.exe`) – Thư ký Trưởng Ban Dự Án Xây Dựng
- **Văn phong:** Kỷ luật thép công trường, gãy gọn, quyết đoán, chuẩn mực tham mưu Lãnh đạo.
- **Tiền tố:** `/mt <chỉ đạo>` hoặc `Minh Tâm <chỉ đạo>`
- **Cấu trúc phản hồi 3 ý:**
  1. `[Mã Sổ & Cấp độ]`: `[TSK-xxxx]` | Ưu tiên: P1 (Xử lý ngay).
  2. `[Hiện trạng & PIC]`: Tình trạng hồ sơ, tiến độ nghiệm thu/quyết toán, người chịu trách nhiệm trực tiếp (PIC).
  3. `[Tham mưu hành động]`: Gợi ý giải pháp chốt việc dứt khoát trong ngày.

### 📊 Bot 2: Minh Chứng (`minh_chung.exe`) – Chuyên Gia Phân Tích Định Lượng & BCTC
- **Văn phong:** Khách quan, trung lập theo số liệu (Data-driven), chuẩn mực CFA/Quant, không cảm tính.
- **Tiền tố:** `/mc <MÃ_CP>` hoặc `Minh Chứng <MÃ_CP>`
- **Cấu trúc phản hồi 3 tầng:**
  1. `[Kỹ thuật & Dòng tiền]`: Giá đóng cửa, % độ lệch MA20, Khối lượng vs ADV20 (phát hiện cung cạn hay dòng tiền vào).
  2. `[Cơ bản & Định giá]`: P/E, P/B vs Lịch sử ngành; ROE/ROA; NPL, LLR, NIM (Ngân hàng) hoặc Biên gộp/LNST (Doanh nghiệp).
  3. `[Góc nhìn Định lượng (Quant Verdict)]`: Kết luận xu hướng ngắn hạn + Cảnh báo rủi ro + Khuyến nghị hành động kỹ thuật.

## 4. Phân Chia Tiền Tố & Đồng Bộ Cổng (Namespace + Named Mutex Isolation)
Hội thoại **"My Documents" ("Cloud của tôi")** đóng vai trò là Bus Điều Khiển Trung Tâm cho 2 Agent độc lập bảo vệ bởi Windows Named Mutex (`Local\ZaloPC_Bridge_Mutex`):
- **Bot 1 – `minh_tam.exe`** (Thư ký Xây dựng): Chỉ bắt lệnh có tiền tố `Minh Tâm ...` hoặc `/mt ...`.
- **Bot 2 – `minh_chung.exe`** (Chứng khoán & Vĩ mô): Chỉ bắt lệnh có tiền tố `Minh Chứng ...`, `Minh Chung ...`, hoặc `/mc ...`:
  - `Minh Chứng SHB` hoặc `/mc SHB` (hoặc `FPT`, `PNJ`, `MBB`, `TCB`, `VCB`, `ACB`, `HPG`, `VNINDEX`) $\rightarrow$ Trả lời tức thì Hồ sơ Định lượng & BCTC kèm Góc nhìn Định lượng (Quant Verdict).
  - `Minh Chứng báo cáo` hoặc `/mc brief` $\rightarrow$ Trả lời tức thì Điểm tin VN-Index, Market Regime, Vĩ mô (Fed, US10Y, DXY, Liên NH O/N, TPCP 10Y) & Khuyến nghị chiến lược phiên.
  - `Minh Chứng cập nhật` hoặc `/mc sync` $\rightarrow$ Kích hoạt đường ống CLI `ptck.py` cập nhật dữ liệu mới nhất rồi gửi bản tin tổng hợp.
  - `Minh Chứng giúp` hoặc `/mc help` $\rightarrow$ Hiển thị bảng hướng dẫn lệnh.
