---
name: minh-chung-zalo
description: Kỹ năng thực thi siêu tốc (<30ms) cho Minh Chứng (PTCK_VNSTOCK) qua binary Rust bản địa `minh_chung.exe` kết nối trực tiếp `winsqlite3.dll` và Zalo PC CDP Loopback (127.0.0.1:58721). Dùng khi đọc/gửi báo cáo Zalo "My Documents", tra cứu nhanh cổ phiếu/BCTC hoặc điểm tin vĩ mô.
---

# Kỹ năng Minh Chứng (`minh_chung.exe`) – Thực Thi Bản Địa `< 30ms`

## 1. Kỷ Luật Tối Thượng (Zero Runtime Scripting)
- **TUYỆT ĐỐI CẤM** tự viết hoặc sinh ra các file `.py`, `.ps1`, `.js` tạm thời để đọc/gửi tin nhắn Zalo hoặc truy vấn nhanh cổ phiếu/BCTC.
- Mọi thao tác I/O với Zalo PC (`127.0.0.1:58721`) và truy vấn nhanh SQLite (`screener_cache.db`, `financial_facts.db`) **PHẢI** gọi trực tiếp file thực thi Rust bản địa `.\minh_chung.exe` tại thư mục gốc dự án.

## 2. Bảng Lệnh Chuẩn (`minh_chung.exe`)

| Tác vụ | Lệnh thực thi chuẩn | Tốc độ thực tế |
|---|---|---|
| **Tra cứu nhanh Cổ phiếu + BCTC** | `.\minh_chung.exe query <MÃ_CP>` *(VD: `.\minh_chung.exe query SHB`)* | **`~3.5ms`** |
| **Tra cứu Cổ phiếu & Gửi thẳng Zalo** | `.\minh_chung.exe query <MÃ_CP> --send` | **`~15–30ms`** |
| **Điểm tin Sáng (Pre-Market 08:45)** | `.\minh_chung.exe brief` *(hoặc `--no-send` nếu chỉ in console)* | **`~26ms`** |
| **Tổng kết Chiều (ATC Summary 15:05)** | `.\minh_chung.exe summary` | **`~26ms`** |
| **Đọc tin nhắn mới nhất trên Zalo** | `.\minh_chung.exe read` | **`~3.5ms`** |
| **Gửi tin nhắn/báo cáo vào Zalo** | `.\minh_chung.exe send "<Nội dung báo cáo>"` | **`~25ms`** |
| **Tự động bật Zalo PC kèm cổng CDP** | `.\minh_chung.exe boot` | Tự dò `Zalo.exe --remote-debugging-port=58721` |
| **Chạy Daemon Phản xạ `<1s` 24/7** | `.\minh_chung.exe watch` | Canh `"My Documents"` chu kỳ `800ms` (`~4MB RAM`) |

## 3. Phân Chia Tiền Tố (Namespace Coexistence trên cùng 1 Zalo PC)
Hội thoại **"My Documents" ("Cloud của tôi")** đóng vai trò là Bus Điều Khiển Trung Tâm cho nhiều Agent chạy song song trên cổng `58721`:
- **`minh_tam.exe`** (Thư ký Xây dựng): Chỉ bắt lệnh có tiền tố `Minh Tâm ...` hoặc `/mt ...`.
- **`minh_chung.exe`** (Chứng khoán & Vĩ mô): Chỉ bắt lệnh có tiền tố `Minh Chứng ...`, `Minh Chung ...`, hoặc `/mc ...`:
  - `Minh Chứng SHB` hoặc `/mc SHB` (hoặc `FPT`, `PNJ`, `MBB`, `TCB`, `VCB`, `ACB`, `HPG`, `VNINDEX`) $\rightarrow$ Trả lời tức thì Hồ sơ Định lượng & BCTC (Giá, KL, ADV20, MA20, Vốn hóa, P/E, P/B, ROE, ROA, NPL, LLR, NIM, LDR, Tăng trưởng tín dụng).
  - `Minh Chứng báo cáo` hoặc `/mc brief` $\rightarrow$ Trả lời tức thì Điểm tin VN-Index, Market Regime, Vĩ mô (Fed, US10Y, DXY, Liên NH O/N, TPCP 10Y) & Danh mục trọng điểm.
  - `Minh Chứng giúp` hoặc `/mc help` $\rightarrow$ Hiển thị bảng hướng dẫn lệnh.
