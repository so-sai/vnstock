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

---

## V. BẢN KIỂM TOÁN VẬN HÀNH ĐA MÁY (CROSS-MACHINE PORTABLE DEPLOYMENT CHECKLIST)

Đúc kết từ tư duy **Skeptical Engineering** (Hoài nghi kỹ thuật & Kiểm chứng thực tế), dưới đây là 5 bẫy kỹ thuật đã được bóc tách và quy chuẩn vận hành để đưa bộ đôi Agent chạy mượt mà trên bất kỳ máy tính Windows 11 nào khác:

### 1. Năm Bẫy Kỹ Thuật Đã Hóa Giải (Resolved Architectural Traps)

| Bẫy kỹ thuật tiềm ẩn | Hiện tượng / Rủi ro nếu mắc phải | Giải pháp First Principles đã chuẩn hóa |
| --- | --- | --- |
| **Bẫy 1: Tối ưu hóa ảo bằng RAM Cache (Stale Data)** | Cố tình tạo `HashMap` RAM để giảm truy vấn từ `3.45ms` về `50ns`. Khi pipeline bên ngoài (CLI/cron) cập nhật DB, RAM giữ nguyên dữ liệu cũ, bot trả lời sai lệch nghiêm trọng. | **Giữ nguyên đọc trực tiếp SQLite:** Nhờ Windows OS Page Cache, SQLite đã đạt `~3ms`. Đây là nguồn dữ liệu duy nhất và luôn đúng thực tế (Single Source of Truth). |
| **Bẫy 2: Polling quá dày làm nghẽn Mutex** | Hạ chu kỳ ngủ xuống `20–50ms` khiến 2 bot tranh chấp cổng CDP 40–50 lần/giây, làm nghẽn luồng UI của Zalo PC. | **Khóa chu kỳ Polling ở ngưỡng vàng `100ms`:** Mỗi lần đọc chỉ mất `2.8ms` (tổng tải CDP chỉ `~5.6%`), độ trễ đón tin trung bình `50ms` — mắt người cảm nhận tức thì mà 2 bot không bao giờ kẹt nhau. |
| **Bẫy 3: Gửi tin tuần tự gây kẹt Mutex (`~150ms`)** | Chờ toàn bộ chuỗi DOM render bong bóng chat và gửi tin mạng mới nhả Mutex, khiến bot thứ hai bị chặn đứng. | **Async UI Dispatch (`setTimeout 0`):** Kích hoạt gõ chữ, sau đó bọc Enter và click Send trong `setTimeout 0`. Trả về `SENT_OK` và nhả Mutex ngay trong **`< 20ms`**. |
| **Bẫy 4: Process bị đóng khi tắt cửa sổ (Job Object)** | Spawn daemon bằng shell thông thường sẽ bị Windows Job Object gom lại và hủy khi phiên console kết thúc. | **Khởi chạy qua WMI Detached (`SW_HIDE`):** Dùng `Win32_Process.Create` kết hợp `Win32_ProcessStartup` với `ShowWindow = 0` để tiến trình sống độc lập 24/7 và ẩn 100%. |
| **Bẫy 5: Cửa sổ console đen chớp nháy (Console Flash)** | Gọi tiến trình con (`powershell`, `python`) mà không có cờ ẩn sẽ làm Windows tạo console chớp tắt liên tục. | **Gắn cứng `CREATE_NO_WINDOW = 0x08000000` + `Stdio::null()`:** Triệt tiêu hoàn toàn mọi cửa sổ console bật lên màn hình Desktop. |

### 2. Quy Tắc Zero-Hardcode Paths (Di Động Tuyệt Đối)

Mọi đường dẫn tuyệt đối (như `D:\04. Software\...` hay `G:\My Drive\...`) đều bị cấm trong logic cốt lõi. Cả 2 bot tuân thủ nghiêm ngặt 3 cấp độ tự định vị:

1. **Cấp 1 (Ưu tiên số 1):** Tự định vị qua vị trí file nhị phân đang chạy (`std::env::current_exe()`).
   - `minh_chung.exe` tự tìm thư mục `backend/data/*.db` ngay tại thư mục cha hoặc ông của file `.exe`.
   - `minh_tam.exe` tự tìm `minh_tam.db` nằm ngay cạnh file `minh_tam.exe`.
2. **Cấp 2 (Tùy biến qua Môi trường):** Hỗ trợ override bằng biến môi trường khi cần đặt dữ liệu ở ổ đĩa khác:
   - `PTCK_ROOT`: Ghi đè thư mục gốc dự án Chứng khoán.
   - `MINH_TAM_DB`: Ghi đè đường dẫn file cơ sở dữ liệu `minh_tam.db`.
   - `MINH_TAM_DIR`: Ghi đè thư mục chứa binary `minh_tam.exe`.
3. **Cấp 3 (Fallback An Toàn):** Kiểm tra Current Working Directory (`env::current_dir()`), tuyệt đối không bao giờ panic nếu không tìm thấy thư mục phụ.

👉 **Kết luận:** Người dùng có thể copy nguyên thư mục dự án sang ổ `C:`, `D:`, hoặc máy tính của đồng nghiệp, gõ `.\minh_chung.exe boot` là toàn bộ hệ thống tự động khởi chạy trơn tru ngay lập tức!

---

## VI. KIẾN TRÚC TỔNG QUÁT HÓA CẤP ĐỘ CUỐI (THE UNIVERSAL SOVEREIGN AGENT ARCHITECTURE)

Để mở rộng hệ thống từ 2 Bot hiện tại lên $N$ Bot chuyên ngành (Kiểm toán LCA, Kế toán Thuế, Tự động hóa AutoCAD/BIM...) mà không bao giờ bị nghẽn Mutex hay phải sửa code khi Zalo cập nhật giao diện, hệ thống được quy chuẩn hóa theo mô hình **Tam Tầng Phân Lập (3-Tier Decoupled Architecture)**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                TẦNG 1: ADAPTER KÊNH GIAO TIẾP (TRANSPORT)               │
│   Zalo PC (CDP 58721)  |  Telegram/Web  |  AutoCAD COM  |  CLI Terminal │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ (Đọc 1 lần - Ngoại vi hóa Selector)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│             TẦNG 2: BỘ ĐỊNH TUYẾN TIỀN TỐ (SINGLE MULTIPLEXER)          │
│   1 Daemon Gác Cổng (3.8MB RAM, 100ms) bắt mọi tin nhắn từ My Documents │
│     Đọc "/mt ..."  ──► Gọi: minh_tam.exe <lệnh>                         │
│     Đọc "/mc ..."  ──► Gọi: minh_chung.exe <mã_cp>                      │
│     Đọc "/lca ..." ──► Gọi: minh_kiem.exe <chứng_từ>                    │
│     Đọc "/cad ..." ──► Gọi: minh_cad.exe <lệnh_vẽ>                      │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ (Chuẩn IPC siêu tốc: stdin/args ──► stdout)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│           TẦNG 3: CÁC MODULE NGHIỆP VỤ THUẦN KHIẾT (PURE ENGINES)       │
│  [minh_tam.exe]        [minh_chung.exe]        [minh_kiem.exe]          │
│  (Chỉ biết SQLite XD)  (Chỉ biết SQLite CK)    (Chỉ biết Luật LCA)      │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1. Ba Quy Luật Tổng Quát Hóa Cốt Lõi
1. **Tách Cổng Giao Tiếp Khỏi Não Nghiệp Vụ (Decouple Transport from Domain):**
   - Các module nghiệp vụ (`minh_chung.exe`, `minh_tam.exe`, `minh_kiem.exe`) là **Pure CLI Engines** (`3ms`): Nhận lệnh qua `args/stdin` $\rightarrow$ Đọc SQLite $\rightarrow$ Xuất báo cáo ra `stdout`.
   - Các engine này hoàn toàn không biết và không phụ thuộc vào Zalo PC hay giao diện người dùng. Có thể gọi chúng từ Zalo, Antigravity, PowerShell, hay bất kỳ phần mềm nào.
2. **Ngoại Vi Hóa Selector Giao Diện (`zalo_selectors.json`):**
   - Không nhúng chết các class HTML (`#richInput`, `.card--text`) vào bên trong các file nhị phân nghiệp vụ.
   - Khi Zalo cập nhật giao diện, chỉ cần cập nhật cấu hình của **1 Dispatcher duy nhất**, toàn bộ $N$ chuyên viên số phía sau vẫn hoạt động bình thường mà không cần mở mã nguồn biên dịch lại.
3. **Mô Hình `1 Dispatcher + N CLI Workers` (Mở Rộng Không Giới Hạn):**
   - Thay vì chạy $N$ vòng lặp `watch` tranh chấp 1 cổng CDP, hệ thống chuẩn chỉ duy trì **1 tiến trình Dispatcher gác cổng duy nhất** (`~3.8MB RAM`).
   - Dù mở rộng lên 10 hay 50 Bot chuyên môn, tải hệ thống vẫn chỉ là **1 tiến trình 3.8MB RAM** và **0 va chạm Mutex**.

### 2. Tiêu Chuẩn Nâng Cấp CSDL: `rusqlite (bundled + FTS5)`
Khi chuyển đổi từ binary đơn lẻ sang dự án quản lý bằng `Cargo.toml`:
- Chuẩn hóa sang crate **`rusqlite` (bản `bundled`)**: Đóng gói mã nguồn C SQLite cố định, hiện đại bên trong file thực thi, loại bỏ rủi ro sai lệch phiên bản `winsqlite3.dll` trên các máy Windows khác nhau.
- **Kích hoạt FTS5 (Full-Text Search 5):** Phục vụ tra cứu siêu tốc toàn văn hợp đồng, điều khoản hồ sơ thầu, ghi chú và văn bản pháp lý.
- **Cấu trúc mở cho SQLCipher:** Sẵn sàng bật tính năng mã hóa CSDL cấp doanh nghiệp khi lưu trữ dữ liệu tài chính/cá nhân nhạy cảm mà không làm thay đổi kiến trúc code.

---

## VII. NĂM TIÊN ĐỀ TƯ DUY CHO HỆ THỐNG TRI THỨC & TỰ ĐỘNG HÓA TƯƠNG LAI

Đây là 5 tiên đề cô đọng nhất đúc kết từ toàn bộ hành trình thử sai và tối ưu hóa thành công:

1. **Tiên đề Ký sinh Nội bộ (Local Parasitic Integration):** Muốn tự động hóa phần mềm đóng (Zalo, AutoCAD, ERP) an toàn và nhanh nhất, hãy ký sinh vào tầng Render/IPC nội bộ trên máy khách (`CDP Loopback` cho Electron/Chromium, `COM/OLE` cho phần mềm kỹ thuật, `winsqlite3.dll` cho dữ liệu) thay vì giả lập giao thức mạng lên đám mây.
2. **Tiên đề Phân tầng Tốc độ (Latency Stratification):** Tách rời **Phản xạ Định sẵn (Deterministic Reflex < 30ms bằng Native Binary Rust)** khỏi **Suy luận Ngữ cảnh (Probabilistic Reasoning 3–10s bằng LLM Agent)**. Không bao giờ dùng LLM Agent để làm việc của một Event Loop.
3. **Tiên đề Đơn thể Không phụ thuộc (Zero-Dependency Static Binary):** Trên Windows 11, một file thực thi bền vững nhất là file liên kết trực tiếp với System DLLs hoặc tự đóng gói (`bundled`), loại bỏ hoàn toàn rủi ro lệch phiên bản runtime (Python venv, Node modules) khi mang sang máy khác.
4. **Tiên đề Độc lập Giao diện (UI-Agnostic Domain Core):** Logic nghiệp vụ (Xây dựng, Chứng khoán, Kiểm toán LCA) phải tồn tại dưới dạng **Pure Input/Output CLI**. Zalo PC chỉ là một "màn hình hiển thị từ xa" (`My Documents` Bus), không được trộn lẫn code giao diện Zalo vào trong lõi tính toán nghiệp vụ.
5. **Tiên đề Khởi động Theo Nhịp Sinh Học (Bio-Rhythm Lifecycle):** Hệ thống tự động hóa cá nhân phải tôn trọng trạng thái vật lý của máy: Tự ngủ/tắt sạch sau giờ làm (`16:30`), và tự động đề máy/hồi phục (`Morning Ignition + Self-Healing`) vào ca sáng đầu tiên (`08:00`) mà không cần can thiệp tay.
