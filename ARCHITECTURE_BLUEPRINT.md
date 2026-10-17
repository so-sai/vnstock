# BẢN TỔNG HỢP KIẾN TRÚC MẪU (MASTER BLUEPRINT)
**Kiến trúc Agent Phản Xạ Tức Thì (`< 30ms`) trên Windows 11: Native Rust + `winsqlite3.dll` + Zalo PC CDP Loopback**

Tài liệu này là **Standard Operating Procedure (SOP)** đúc kết từ thực nghiệm bóc tách, thử sai và tối ưu hóa thành công, dùng để nhân bản cho mọi dự án Agent chuyên biệt trên Windows 11:
- **Minh Tâm (`minh_tam.exe`)** – Thư ký Dự án Xây dựng (`G:\My Drive\05. AI - Data\GEM\04. THƯ KÝ DỰ ÁN XD`)
- **Minh Chứng (`minh_chung.exe`)** – Chuyên gia Phân tích Chứng khoán & Vĩ mô (`PTCK_VNSTOCK`)
- **Trợ lý Nhập khẩu LCA (`minh_lca.exe`)** – Quản lý Hồ sơ & Chuỗi cung ứng
- **Điều phối AutoCAD / BIM (`minh_cad.exe`)** – Tự động hóa Kỹ thuật qua COM Interop

---

## I. TƯ DUY NGUYÊN BẢN (FIRST PRINCIPLES): 5 QUY LUẬT VÀNG

Qua thực nghiệm, 5 nguyên lý dưới đây phân định rõ giữa một hệ thống **chậm chạp, hay treo** và một hệ thống **phản xạ `< 1 giây`, ổn định 24/7**:

1. **Tuyệt đối không giả lập API mạng của Zalo (Zero-Protocol Spoofing):**
   - Không tự giải mã `zpw_sek`/`zpw_enk`, không đưa phiên QR lên VPS lạ (như OpenClaw/GoClaw).
   - **Nguyên lý:** Ký sinh trực tiếp vào tiến trình `Zalo.exe` chính chủ đang chạy trên máy tính và mạng văn phòng quen thuộc. Zalo Server nhìn vào chỉ thấy 100% là thao tác hợp lệ của người dùng thật.

2. **Không dùng AI Agent để chạy vòng lặp canh tin nhắn (Zero-Cron Polling):**
   - Dùng tính năng `Automations (Cron 5-15 phút)` của Antigravity để canh tin nhắn sẽ gây khởi tạo phiên AI liên tục (Cold Start) và dẫn đến lỗi đẻ chồng tiến trình (**Task Pile-up**).
   - **Nguyên lý:** Tách bạch **Tầng Phản Xạ (`< 1 giây`)** giao cho 1 binary **Rust (`watch`)** siêu nhẹ (`~4MB RAM`), còn **Tầng Lịch Trình (`Automations`)** chỉ dùng cho các mốc cố định trong ngày (08:00/08:45 sáng và 15:05/16:05 chiều).

3. **Không để AI tự đẻ script Python/PowerShell lúc chạy (Zero Runtime Scripting):**
   - Mỗi lần AI tự viết file `.py`/`.ps1` rồi gọi trình thông dịch sẽ mất 5–15 giây và xả rác đầy thư mục dự án.
   - **Nguyên lý:** Đóng gói mọi thao tác I/O (Đọc Zalo, Gửi Zalo, Truy vấn SQLite) vào **1 file thực thi Rust duy nhất (`<agent_name>.exe`, ~220–390KB)** liên kết thẳng với `winsqlite3.dll` sẵn có của Windows 11. Thời gian thực thi giảm xuống còn **`~3–28ms`**.

4. **"Cloud của tôi" (`My Documents`) là Bus Điều Khiển Trung Tâm + Khóa Cứng Tiền Tố (Strict Prefix Isolation):**
   - Biến hội thoại **"My Documents"** ("Cloud của tôi") thành Command Line từ xa trên điện thoại.
   - **Quy tắc cô lập tiền tố:** Tuyệt đối **CẤM** dùng ký tự đại diện chung chung (như `starts_with('/')`). Mỗi Bot phải có namespace riêng biệt:
     - Bot 1 (Minh Tâm): `Minh Tâm ...`, `minh tam ...`, `/mt ...`, `/task ...` (Bỏ qua hoàn toàn `/mc` và các bot khác).
     - Bot 2 (Minh Chứng): `Minh Chứng ...`, `minh chung ...`, `/mc ...` (Bỏ qua hoàn toàn `/mt` và các bot khác).
     - Mọi tin nhắn bắt đầu bằng dấu ngoặc vuông `[...]`, emoji báo cáo (`📊`, `📈`, `⚠️`, `🤖`) hoặc tên bot phản hồi phải được lọc bỏ ngay để tránh vòng lặp phản xạ vô tận (Infinite Echo Loop).

5. **Đồng bộ Liên Tiến trình qua Windows Named Mutex (Inter-Process Safety):**
   - Khi nhiều Bot cùng kết nối tới 1 tiến trình Zalo PC trên cổng `58721`, để tránh xung đột cổng WebSocket và không bị ghi đè bộ đệm bàn phím `#richInput`, toàn bộ lệnh đọc/gửi CDP phải được bao bọc bởi Windows Named Mutex: `Local\ZaloPC_Bridge_Mutex`.
   - Mutex cấp Kernel Windows (`kernel32.dll`: `CreateMutexA`, `WaitForSingleObject`, `ReleaseMutex`) đảm bảo tại một mili-giây chỉ có đúng 1 bot thao tác với Zalo, bot kia tự động đợi theo cơ chế hàng đợi mà không cần dựng HTTP Proxy phức tạp.

---

## II. BẢNG PHÂN LOẠI CÁC CƠ CHẾ KẾT NỐI ZALO PC & PHẦN MỀM WINDOWS 11

Khi áp dụng sang dự án khác, tùy thuộc vào phần mềm đích mà chọn đúng cơ chế kết nối tương ứng trên Windows 11 (hoàn toàn không cần dựng MCP Server rườm rà):

| Cơ chế kết nối | Cách thức kích hoạt & Giao tiếp | Tốc độ / Độ ổn định | Khi nào nên áp dụng? |
| --- | --- | --- | --- |
| **1. Electron CDP Loopback (Chuẩn tối ưu cho Zalo PC)** | Khởi chạy file lõi `Zalo-*\Zalo.exe` qua WMI (`Win32_Process.Create`) hoặc Process Spawn kèm cờ `--remote-debugging-port=<Cổng_Hiếm>` (VD: `58721`). Binary Rust kết nối thẳng TCP/WebSocket tới `127.0.0.1:58721`. | **~3–28ms / Ổn định tuyệt đối 100%.** Chạy ngầm hoàn toàn khi Zalo thu nhỏ dưới Taskbar, không chiếm chuột phím. | **Mặc định dùng cho mọi dự án kết nối Zalo PC** (và các app Electron khác như Slack, Teams, VSCode, Notion, Obsidian). |
| **2. Windows Named Mutex (`kernel32.dll`)** | Đồng bộ tiến trình qua `CreateMutexA(..., "Local\\ZaloPC_Bridge_Mutex")`. | **< 0.1ms overhead.** Bảo vệ an toàn 100% khi chạy song song 2 hay nhiều Bot độc lập trên cùng 1 máy. | **Bắt buộc áp dụng khi có từ 2 Bot trở lên cùng giao tiếp với 1 Zalo PC.** |
| **3. Native System DLL (`winsqlite3.dll`)** | Liên kết FFI trực tiếp (`#[link(name = "winsqlite3", kind = "raw-dylib")]`) với thư viện C có sẵn trong `C:\Windows\System32\winsqlite3.dll`. | **< 5ms / Tuyệt đối.** Không cần cài đặt SQLite ngoài, không cần tải crate phụ thuộc. | Dùng cho mọi truy vấn Cơ sở dữ liệu nội bộ (Task, Tài chính, Kho bãi, Lịch sử giá). |
| **4. Windows COM Interop (`ole32.dll`)** | Gọi thẳng ActiveObject của Windows (`GetActiveObject`). Ví dụ: `AutoCAD.Application`, `Excel.Application`, `Word.Application`, `Outlook.Application`. | **< 50ms / Cực kỳ mạnh mẽ.** Bắn lệnh trực tiếp vào Command Line hoặc Object Model của phần mềm kỹ thuật. | Dùng khi mở rộng Agent sang tự động hóa vẽ **AutoCAD**, xuất báo cáo **Excel**, tạo văn bản **Word** từ lệnh nhắn trên Zalo. |

---

## III. SƠ ĐỒ KIẾN TRÚC 3 TẦNG & CƠ CHẾ ĐỒNG BỘ MULTI-AGENT

```
📱 Điện thoại (Zalo "My Documents")
       │
       ▼ (Đồng bộ tức thì qua hạ tầng chính chủ Zalo)
💻 Zalo PC (Zalo.exe --remote-debugging-port=58721)
       │
       ├──► 🔒 [Windows Named Mutex: Local\ZaloPC_Bridge_Mutex]
       │         ▲                                  ▲
       │         │ (Khóa giao dịch 10-50ms)        │ (Khóa giao dịch 10-50ms)
       │         │                                  │
       ├──► [BOT 1: MINH TÂM (XÂY DỰNG)]    [BOT 2: MINH CHỨNG (CHỨNG KHOÁN)]
       │    Tiền tố: `/mt ...`, `Minh Tâm`       Tiền tố: `/mc ...`, `Minh Chứng`
       │    Database: `minh_tam.db` (25 dự án)    Database: `screener_cache.db` + `financial_facts.db`
       │    Binary: `minh_tam.exe watch`          Binary: `minh_chung.exe watch`
       │    Source: `04. THƯ KÝ...\src\main.rs`   Source: `PTCK_VNSTOCK\src\main.rs`
       │
       └──► [TẦNG 2 & 3: SUY LUẬN SÂU & LỊCH TRÌNH ĐỊNH KỲ]
            AI Agent (Antigravity / Gemini)
            • Khi cần đọc/gửi Zalo hoặc tra cứu nhanh: Gọi `<agent>.exe read|send|query` (3ms)
            • Chỉ chạy các mốc lịch cố định trong ngày, không chạy vòng lặp canh tin nhắn
```

---

## IV. QUY TRÌNH QUẢN LÝ MÃ NGUỒN (SOURCE CODE PERSISTENCE IN GIT)

- **Nguyên tắc bảo trì:** Mã nguồn Rust của mỗi binary **PHẢI** được lưu trữ trực tiếp tại `src/main.rs` trong Git repo của dự án tương ứng.
- **Biên dịch tại chỗ (In-Place Compilation):**
  ```powershell
  rustc -C opt-level=3 src\main.rs -o minh_chung.exe
  ```
- **Không xả rác:** Sau khi biên dịch, xóa file debug `.pdb`, chỉ commit `src/main.rs`, file thực thi `.exe` và `SKILL.md` vào Git.
