# BẢN TỔNG HỢP KIẾN TRÚC MẪU (MASTER BLUEPRINT)
**Kiến trúc Agent Phản Xạ Tức Thì (`< 30ms`) trên Windows 11: Native Rust + `winsqlite3.dll` + Zalo PC CDP Loopback**

Tài liệu này là **Standard Operating Procedure (SOP)** đúc kết từ thực nghiệm bóc tách, thử sai và tối ưu hóa thành công, dùng để nhân bản cho mọi dự án Agent chuyên biệt trên Windows 11:
- **Minh Tâm (`minh_tam.exe`)** – Thư ký Dự án Xây dựng (`G:\My Drive\05. AI - Data\GEM\04. THƯ KÝ DỰ ÁN XD`)
- **Minh Chứng (`minh_chung.exe`)** – Chuyên gia Phân tích Chứng khoán & Vĩ mô (`PTCK_VNSTOCK`)
- **Trợ lý Nhập khẩu LCA (`minh_lca.exe`)** – Quản lý Hồ sơ & Chuỗi cung ứng
- **Điều phối AutoCAD / BIM (`minh_cad.exe`)** – Tự động hóa Kỹ thuật qua COM Interop

---

## I. TƯ DUY NGUYÊN BẢN (FIRST PRINCIPLES): 4 QUY LUẬT VÀNG

Qua thực nghiệm, 4 nguyên lý dưới đây phân định rõ giữa một hệ thống **chậm chạp, hay treo** và một hệ thống **phản xạ `< 1 giây`, ổn định 24/7**:

1. **Tuyệt đối không giả lập API mạng của Zalo (Zero-Protocol Spoofing):**
   - Không tự giải mã `zpw_sek`/`zpw_enk`, không đưa phiên QR lên VPS lạ (như OpenClaw/GoClaw).
   - **Nguyên lý:** Ký sinh trực tiếp vào tiến trình `Zalo.exe` chính chủ đang chạy trên máy tính và mạng văn phòng quen thuộc. Zalo Server nhìn vào chỉ thấy 100% là thao tác hợp lệ của người dùng thật.

2. **Không dùng AI Agent để chạy vòng lặp canh tin nhắn (Zero-Cron Polling):**
   - Dùng tính năng `Automations (Cron 5-15 phút)` của Antigravity để canh tin nhắn sẽ gây khởi tạo phiên AI liên tục (Cold Start) và dẫn đến lỗi đẻ chồng tiến trình (**Task Pile-up**).
   - **Nguyên lý:** Tách bạch **Tầng Phản Xạ (`< 1 giây`)** giao cho 1 binary **Rust (`watch`)** siêu nhẹ (`~4MB RAM`), còn **Tầng Lịch Trình (`Automations`)** chỉ dùng cho các mốc cố định trong ngày (08:00/08:45 sáng và 15:05/16:05 chiều).

3. **Không để AI tự đẻ script Python/PowerShell lúc chạy (Zero Runtime Scripting):**
   - Mỗi lần AI tự viết file `.py`/`.ps1` rồi gọi trình thông dịch sẽ mất 5–15 giây và xả rác đầy thư mục dự án.
   - **Nguyên lý:** Đóng gói mọi thao tác I/O (Đọc Zalo, Gửi Zalo, Truy vấn SQLite) vào **1 file thực thi Rust duy nhất (`<agent_name>.exe`, ~220KB)** liên kết thẳng với `winsqlite3.dll` sẵn có của Windows 11. Thời gian thực thi giảm xuống còn **`~28ms`**.

4. **"Cloud của tôi" (`My Documents`) là Bus Điều Khiển Trung Tâm + Bộ lọc Tiền tố:**
   - Biến hội thoại **"My Documents"** ("Cloud của tôi") thành Command Line từ xa trên điện thoại.
   - Luôn khóa bằng **Tiền tố kích hoạt (Wake-word)** như `Minh Tâm ...` (`/mt`), `Minh Chứng ...` (`/mc`), hoặc dấu `/` để không xung đột khi lưu file cá nhân hoặc khi chạy nhiều Agent đồng thời trên cùng 1 máy.

---

## II. BẢNG PHÂN LOẠI CÁC CƠ CHẾ KẾT NỐI ZALO PC & PHẦN MỀM WINDOWS 11

Khi áp dụng sang dự án khác, tùy thuộc vào phần mềm đích mà chọn đúng cơ chế kết nối tương ứng trên Windows 11 (hoàn toàn không cần dựng MCP Server rườm rà):

| Cơ chế kết nối | Cách thức kích hoạt & Giao tiếp | Tốc độ / Độ ổn định | Khi nào nên áp dụng? |
| --- | --- | --- | --- |
| **1. Electron CDP Loopback (Chuẩn tối ưu cho Zalo PC)** | Khởi chạy file lõi `Zalo-*\Zalo.exe` qua WMI (`Win32_Process.Create`) hoặc Process Spawn kèm cờ `--remote-debugging-port=<Cổng_Hiếm>` (VD: `58721`). Binary Rust kết nối thẳng TCP/WebSocket tới `127.0.0.1:58721`. | **~28ms / Ổn định tuyệt đối 100%.** Chạy ngầm hoàn toàn khi Zalo thu nhỏ dưới Taskbar, không chiếm chuột phím. | **Mặc định dùng cho mọi dự án kết nối Zalo PC** (và các app Electron khác như Slack, Teams, VSCode, Notion, Obsidian). |
| **2. Windows UI Automation (UIA Targeted)** | Dùng `UIAutomationClient` của Windows 11. **Lưu ý sống còn:** Chỉ dùng `FindFirst` nhắm đích danh `AutomationId="richInput"`, tuyệt đối cấm dùng `FindAll(TreeScope.Descendants)` gây treo đơ toàn bộ cây Chromium. | **~300ms / Trung bình.** Đòi hỏi cửa sổ phải `SetForegroundWindow` (nổi lên màn hình) khi dán Clipboard. | Dùng làm phương án dự phòng khi phần mềm đích là WinForms / WPF / Win32 truyền thống không có nhân Chromium. |
| **3. Windows COM Interop (`ole32.dll`)** | Gọi thẳng ActiveObject của Windows (`GetActiveObject`). Ví dụ: `AutoCAD.Application`, `Excel.Application`, `Word.Application`, `Outlook.Application`. | **< 50ms / Cực kỳ mạnh mẽ.** Bắn lệnh trực tiếp vào Command Line hoặc Object Model của phần mềm kỹ thuật. | Dùng khi mở rộng Agent sang tự động hóa vẽ **AutoCAD**, xuất báo cáo **Excel**, tạo văn bản **Word** từ lệnh nhắn trên Zalo. |
| **4. Native System DLL (`winsqlite3.dll`)** | Liên kết FFI trực tiếp (`#[link(name = "winsqlite3", kind = "raw-dylib")]`) với thư viện C có sẵn trong `C:\Windows\System32\winsqlite3.dll`. | **< 5ms / Tuyệt đối.** Không cần cài đặt SQLite ngoài, không cần tải crate phụ thuộc. | Dùng cho mọi truy vấn Cơ sở dữ liệu nội bộ (Task, Tài chính, Kho bãi, Lịch sử giá). |

---

## III. SƠ ĐỒ KIẾN TRÚC 3 TẦNG (3-TIER HYBRID ARCHITECTURE)

```
📱 Điện thoại (Zalo "My Documents")
       │
       ▼ (Đồng bộ tức thì qua hạ tầng chính chủ Zalo)
💻 Zalo PC (Zalo.exe --remote-debugging-port=58721)
       │
       ├──► [TẦNG 1: PHẢN XẠ TỨC THÌ (< 1 GIÂY)]
       │    Binary Rust: `<agent>.exe watch` (Chạy ngầm 24/7, tốn ~4MB RAM)
       │    • Tự động kiểm tra & bật Zalo CDP nếu chưa mở (Self-Boot)
       │    • Bắt tin nhắn có tiền tố ("Minh Tâm ...", "Minh Chứng ...", "/mc ...")
       │    • Truy vấn trực tiếp SQLite (`winsqlite3.dll`) & trả lời ngay lập tức
       │
       └──► [TẦNG 2 & 3: SUY LUẬN SÂU & LỊCH TRÌNH ĐỊNH KỲ]
            AI Agent (Antigravity / Gemini)
            • Khi cần đọc/gửi Zalo hoặc tra cứu nhanh: Gọi `<agent>.exe read|send|query` (28ms)
            • Chỉ chạy 2 lịch cố định/ngày (Sáng & Chiều), không chạy vòng lặp canh tin nhắn
```

---

## IV. KHUNG MÃ NGUỒN RUST CHUẨN (TEMPLATE 1 FILE DUY NHẤT – ZERO DEPENDENCY)

Chỉ cần **1 file `main.rs` duy nhất** (không cần `Cargo.toml`, không cần tải thư viện ngoài), biên dịch bằng lệnh:

```powershell
rustc -C opt-level=3 main.rs -o <ten_agent>.exe
```

là tạo ra ngay file `.exe` **`~220KB`** tích hợp sẵn:
1. **Kết nối `winsqlite3.dll`:** Đọc/ghi cơ sở dữ liệu SQLite nội bộ trong `< 5ms`.
2. **Giao thức WebSocket/HTTP CDP (`127.0.0.1:58721`):** Tự động tìm tab `index.html` của Zalo PC, nhảy vào `"My Documents"`, đọc tin nhắn mới nhất và bơm tin nhắn trả lời qua `document.execCommand('insertText')` + sự kiện `Enter` chuẩn Chromium.
3. **Cơ chế Self-Boot (Tự khởi chạy Zalo CDP):** Nếu phát hiện cổng `58721` chưa mở, tự động tìm `C:\Users\...\AppData\Local\Programs\Zalo\Zalo-*\Zalo.exe` và khởi chạy kèm cờ `--remote-debugging-port=58721`.

---

## V. QUY TRÌNH 3 BƯỚC NHÂN BẢN SANG DỰ ÁN MỚI (VD: `PTCK_VNSTOCK` - MINH CHỨNG)

Khi áp dụng kiến trúc này sang dự án phân tích chứng khoán **`PTCK_VNSTOCK`** (hoặc bất kỳ dự án nào khác), quy trình triển khai gói gọn trong 3 bước:

### Bước 1: Định nghĩa Tiền tố (Namespace) để chạy đa Agent trên cùng 1 Zalo PC
Vì cả `minh_tam.exe` và `minh_chung.exe` đều có thể cùng lắng nghe cổng `58721` trên hội thoại `"My Documents"`, chỉ cần phân chia tiền tố:
- Gõ `Minh Tâm báo cáo` hoặc `/mt ...` $\rightarrow$ **`minh_tam.exe`** bắt lệnh và trả lời việc Xây dựng.
- Gõ `Minh Chứng SHB` hoặc `/mc VNI` $\rightarrow$ **`minh_chung.exe`** bắt lệnh, truy vấn dữ liệu định lượng/BCTC trong `screener_cache.db` + `financial_facts.db` và trả lời phân tích cổ phiếu ngay trên Zalo.

### Bước 2: Thay câu lệnh SQL trong hàm `handle_command()`
Giữ nguyên 100% phần lõi kết nối `CdpClient` và `winsqlite3` của Rust, chỉ thay đổi câu lệnh SQL trỏ vào database của dự án mới:
- Với **Thư ký Xây dựng (`minh_tam.exe`)**: `SELECT ... FROM tasks WHERE status != 'DONE'` (`secretary.db`).
- Với **Chứng khoán (`minh_chung.exe`)**:
  - `SELECT date, close, volume FROM daily_ohlcv WHERE symbol = ? ORDER BY date DESC LIMIT 2` (`backend/data/screener_cache.db`).
  - `SELECT metric_code, value, period_key FROM financial_facts WHERE symbol = ?` (`backend/data/financial_facts.db`).
  - `SELECT regime_code, regime_label, vnindex_close FROM regime_history ORDER BY date DESC LIMIT 1` (`backend/data/screener_cache.db`).

### Bước 3: Khóa kỷ luật AI bằng 1 file `SKILL.md` ngắn gọn
Tạo file `.agents/skills/<ten-skill>/SKILL.md` với chỉ thị:
> *"Mọi thao tác đọc/gửi Zalo và truy vấn nhanh phải gọi trực tiếp `<ten_agent>.exe` (`28ms`). Tuyệt đối cấm viết script `.py`/`.ps1` tạm thời."*
