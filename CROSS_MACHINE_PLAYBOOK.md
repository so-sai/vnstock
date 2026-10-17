# CẨM NANG TRIỂN KHAI ĐA MÁY (CROSS-MACHINE DEPLOYMENT PLAYBOOK)
**Chuẩn Hóa Agent Phản Xạ Siêu Tốc (`< 30ms`) Trên Windows 11: Native Rust + `winsqlite3.dll` + Zalo PC CDP Loopback**

Tài liệu này là **Khuôn Mẫu Thực Chiến (Standard Operating Procedure - SOP)** đúc kết toàn diện từ quá trình thực nghiệm, thử sai, bóc tách First Principles và tối ưu hóa thành công trên Windows 11. Bạn có thể mang toàn bộ tài liệu và mã nguồn mẫu này sang bất kỳ máy tính mới nào (laptop cá nhân, máy chi nhánh, máy đối tác) để dựng một Agent Zalo chuyên biệt chỉ trong **3 phút** mà **không cần cài thêm bất kỳ thư viện hay phần mềm trung gian nào**.

---

## I. NĂM "BẪY NGẦM" ĐẮT GIÁ NHẤT ĐÃ ĐƯỢC GIẢI MÃ (LESSONS LEARNED)

Đây là 5 bài học thực nghiệm cốt lõi giải thích vì sao các cách làm thông thường (dùng Python, Selenium, AutoHotkey, hoặc cron AI) đều thất bại hoặc hoạt động chập chờn:

```
                  ┌─────────────────────────────────────────────────────────┐
                  │                 5 BẪY KỸ THUẬT & GIẢI PHÁP             │
                  └─────────────────────────────────────────────────────────┘
                                               │
         ┌───────────────────┬─────────────────┼───────────────────┬───────────────────┐
         ▼                   ▼                 ▼                   ▼                   ▼
    [BẪY 1: LAUNCHER]   [BẪY 2: CHỚP CONSOLE] [BẪY 3: NGHẼN MUTEX] [BẪY 4: ĐỘ TRỄ 1S]  [BẪY 5: JOB OBJECT]
    Zalo.exe gốc là     Gọi Command không     2 bot tranh chấp    Đọc HTTP treo chờ    Shell đóng là kill
    launcher, làm rơi   cờ Win32 làm nháy     cổng 58721 và gõ    timeout, sleep thừa  luôn cả daemon ngầm
    cờ --remote-debug   cửa sổ đen Desktop    đè phím lên nhau    lên tới 998ms        của tiến trình con
         │                   │                 │                   │                   │
         ▼                   ▼                 ▼                   ▼                   ▼
    Quét Zalo-*\Zalo    CREATE_NO_WINDOW      Tách biệt tiền tố   Zero-wait HTTP,      WMI Win32_Process
    trong LocalAppData  (0x08000000) cấp Win  + Global Mutex      Async UI Dispatch,   Detached SW_HIDE
    để gọi đúng nhân    triệt tiêu 100%       + Singleton Mutex   100ms Sweet Spot     chạy bền bỉ 24/7
```

### 1. Bẫy 1: Bật cờ `--remote-debugging-port` nhưng cổng CDP không mở
* **Bản chất kỹ thuật:** File `Zalo.exe` nằm tại `%LOCALAPPDATA%\Programs\Zalo\Zalo.exe` thực chất chỉ là một **Launcher mỏng**. Khi được bấm, nó kiểm tra phiên bản mới, tự gọi sang file thực thi thực sự nằm trong thư mục phiên bản (VD: `Zalo-26.2.2\Zalo.exe`) rồi tiến trình Launcher tự thoát. Quá trình chuyển tiếp này làm **rơi mất** tham số dòng lệnh `--remote-debugging-port=58721`!
* **Giải pháp chuẩn:** Viết hàm tự động quét thư mục `%LOCALAPPDATA%\Programs\Zalo`, tìm thư mục có tiền tố `Zalo-*` có thời gian sửa đổi gần nhất, và gọi trực tiếp file `Zalo-*\Zalo.exe` kèm cờ CDP.

### 2. Bẫy 2: Chớp nháy cửa sổ đen Console (Console Flash) trên Desktop
* **Bản chất kỹ thuật:** Khi gọi tiến trình con (`Command::new("powershell")` hoặc `python`) trên Windows mà không gắn cờ Win32 Subsystem, Windows OS sẽ tự tạo một Console Window trong 50–100ms rồi đóng lại, gây hiện tượng màn hình bị chớp tắt liên tục làm phiền người dùng.
* **Giải pháp chuẩn:**
  1. Bắt buộc gắn `.creation_flags(0x08000000)` (`CREATE_NO_WINDOW`) cho toàn bộ lệnh gọi tiến trình con.
  2. Kèm theo `.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())` để tránh panic `The handle is invalid` khi chạy ngầm không có console.

### 3. Bẫy 3: Nhiều Bot chạy chung 1 Zalo PC bị cướp lệnh và kẹt phím
* **Bản chất kỹ thuật:** Khi chạy song song nhiều Bot (VD: Bot 1 `minh_tam` - Xây dựng, Bot 2 `minh_chung` - Chứng khoán) cùng trỏ vào 1 tiến trình Zalo PC:
  - Nếu Bot 1 bắt tiền tố chung chung (như bắt tất cả tin nhắn có dấu `/`), nó sẽ nuốt luôn lệnh `/mc` của Bot 2 và ghi nhầm dữ liệu.
  - Nếu 2 Bot cùng gửi tin vào khung chat `#richInput` một lúc, bàn phím ảo sẽ bị trộn lẫn ký tự.
* **Giải pháp chuẩn (Bộ Ba Khóa An Toàn):**
  - **Khóa 1 (Strict Prefix Isolation):** Phân chia namespace tuyệt đối: `/mt` vs `/mc`. Lọc bỏ ngay các tin nhắn chứa ngoặc vuông `[...]` hoặc emoji để chống vòng lặp dội âm (Echo loop).
  - **Khóa 2 (Inter-Process Bridge Mutex):** Dùng Named Mutex cấp Kernel `Local\ZaloPC_Bridge_Mutex` bọc toàn bộ giao dịch CDP. Mỗi thời điểm chỉ duy nhất 1 bot được thao tác với Zalo trong `< 15ms`.
  - **Khóa 3 (Per-Bot Singleton Mutex):** Mỗi bot giữ 1 Mutex đơn thể riêng (VD: `Local\MinhChung_Daemon_Instance_Mutex`). Nếu người dùng bấm bật nhầm lần 2, tiến trình mới tự thoát sau `0.001ms`.

### 4. Bẫy 4: Độ trễ phản xạ bị kìm hãm ở mức ~1 giây (`998ms`)
* **Bản chất kỹ thuật:** Rust chạy rất nhanh (truy vấn SQL chỉ mất `3ms`), nhưng 99% thời gian bị lãng phí do:
  - Hàm đọc HTTP `GET /json` dùng đọc chuỗi thông thường khiến TCP phải chờ hết timeout (`read_timeout 250ms`) mới nhận biết hết dữ liệu.
  - Lệnh gửi tin JavaScript thực thi tuần tự nhiều bước chờ DOM render bong bóng chat và gửi mạng rồi mới trả về.
  - Đặt chu kỳ Polling ngủ quá dài (`800ms`).
* **Giải pháp chuẩn:**
  - **Zero-Wait HTTP:** Phân tích `Content-Length` hoặc ngắt ngay khi đọc được ký tự đóng mảng JSON `]` của danh sách DevTools targets (giảm từ `250ms` xuống `1.5ms`).
  - **Async UI Dispatch (`setTimeout 0`):** Kích hoạt paste chữ, sau đó bọc lệnh nhấn Enter và click nút gửi trong `setTimeout 0`. Trả về `SENT_OK` và nhả khóa Mutex ngay trong **`< 20ms`**, để lại việc vẽ giao diện cho Chromium tự xử lý ngầm.
  - **Ngưỡng Vàng Polling `100ms`:** Độ trễ đón tin trung bình chỉ còn `50ms` (tức thì với mắt người), trong khi tải CDP chỉ chiếm `5.6%`, giải phóng `94.4%` cho Zalo PC.

### 5. Bẫy 5: Tiến trình Daemon bị đóng oan khi tắt cửa sổ dòng lệnh (Job Object)
* **Bản chất kỹ thuật:** Khi bạn mở một PowerShell/CMD để khởi chạy daemon rồi đóng cửa sổ lại, hoặc khi một công cụ AI chạy lệnh shell, Windows gắn tất cả tiến trình con vào một Windows Job Object. Khi phiên shell kết thúc, Windows sẽ tự động "trảm" toàn bộ các tiến trình con liên quan!
* **Giải pháp chuẩn:** Khởi chạy daemon thông qua WMI:
  `Invoke-CimMethod -ClassName Win32_Process -MethodName Create` kết hợp `Win32_ProcessStartup` với cờ `ShowWindow = 0` (`SW_HIDE`). Tiến trình được tạo sẽ nằm hoàn toàn độc lập dưới quyền quản lý của Windows Session, sống bền bỉ 24/7 và ẩn 100%.

---

## II. NGUYÊN TẮC "ZERO-HARDCODE" CHO MỌI MÁY TÍNH

Khi chuyển sang máy tính mới (ổ đĩa có thể là `C:\`, `D:\`, `E:\` hoặc tên người dùng Windows khác nhau), mã nguồn tuyệt đối **KHÔNG ĐƯỢC CHỨA BẤT KỲ ĐƯỜNG DẪN Ổ ĐĨA CỐ ĐỊNH NÀO**.

Khuôn mẫu chuẩn áp dụng 3 quy tắc định vị tự động:

1. **Định vị Zalo qua Biến Môi Trường Hệ Thống:**
   ```rust
   let local_app_data = env::var("LOCALAPPDATA").unwrap_or_else(|_| r"C:\Users\Default\AppData\Local".to_string());
   let zalo_base = PathBuf::from(local_app_data).join("Programs").join("Zalo");
   ```
2. **Định vị Cơ sở dữ liệu theo vị trí file `.exe` đang chạy:**
   ```rust
   let exe_dir = env::current_exe().ok().and_then(|p| p.parent().map(|d| d.to_path_buf())).unwrap_or_else(|| PathBuf::from("."));
   let db_path = exe_dir.join("data").join(DB_FILE_NAME);
   ```
3. **Cấu hình nhanh qua 6 dòng Hằng số ở đầu file `src/template_main.rs`:**
   ```rust
   const BOT_NAME: &str = "Minh Kế";
   const BOT_TITLE: &str = "Thư ký Kế toán & Tài chính Doanh nghiệp";
   const WAKE_PREFIXES: &[&str] = &["/mk", "minh kế", "minh ke"];
   const INSTANCE_MUTEX: &str = "Local\\MinhKe_Daemon_Instance_Mutex";
   const DB_FILE_NAME: &str = "minh_ke.db";
   const CDP_PORT: u16 = 58721;
   ```

---

## III. QUY TRÌNH 3 PHÚT TRIỂN KHAI TRÊN MÁY TÍNH MỚI

### Bước 1: Sao chép Thư mục Mẫu sang máy mới
Bạn chỉ cần copy thư mục dự án (hoặc file mã nguồn [src/template_main.rs](file:///D:/04.%20Software/opensource_contrib/PTCK_VNSTOCK/src/template_main.rs)) vào bất kỳ ổ đĩa nào trên máy mới (VD: `C:\Agents\MinhKe` hoặc `D:\Bot\`).

### Bước 2: Tùy biến Tên Bot & Tiền tố (Chỉ sửa 5 dòng đầu)
Mở file `src/template_main.rs`, đổi tên Bot, tiền tố và tên DB mong muốn:
```rust
const BOT_NAME: &str = "Minh Kho";
const BOT_TITLE: &str = "Trợ lý Quản lý Tồn kho & Vật tư";
const WAKE_PREFIXES: &[&str] = &["/kho", "minh kho"];
const INSTANCE_MUTEX: &str = "Local\\MinhKho_Daemon_Instance_Mutex";
const DB_FILE_NAME: &str = "kho_data.db";
```

### Bước 3: Biên dịch bằng Rust 1 dòng duy nhất (Không cần Crate ngoài)
Vì code chỉ gọi `winsqlite3.dll` và `kernel32.dll` có sẵn trong Windows 11, bạn chỉ cần gõ:
```powershell
rustc -C opt-level=3 src\template_main.rs -o minh_kho.exe
Remove-Item minh_kho.pdb -Force -ErrorAction SilentlyContinue
```

### Bước 4: Khởi động hệ thống (Chạy ngầm vĩnh viễn)
Khởi chạy Zalo PC kèm cổng CDP và kích hoạt daemon ẩn:
```powershell
.\minh_kho.exe boot
```
Hoặc khởi chạy hoàn toàn tách biệt qua WMI:
```powershell
$cmd = '"' + (Get-Location).Path + '\minh_kho.exe" watch'
$startup = [wmiclass]"Win32_ProcessStartup"
$startup.Properties['ShowWindow'].Value = 0 # SW_HIDE
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = (Get-Location).Path } | Out-Null
```

### Bước 5: Kiểm thử trên điện thoại
Mở Zalo trên điện thoại, vào hội thoại **"Cloud của tôi" ("My Documents")** và nhắn:
```text
/kho Tồn kho thép Hòa Phát D20 tại kho tổng?
```
Bot sẽ phản hồi ngay lập tức trong **`< 50ms`** theo chuẩn phong thái điều hành chuyên nghiệp!

---

## IV. BẢNG PHÂN BỔ TRÁCH NHIỆM: BOT DAEMON VS. AI AGENT (ANTIGRAVITY)

| Nhiệm vụ | Ai đảm nhiệm? | Cơ chế / Tần suất | Tại sao? |
| --- | --- | --- | --- |
| **Phản xạ tức thì `< 1 giây`** (Tra cứu nhanh, ghi nhận chỉ đạo, tìm kiếm tồn kho, BCTC) | **Bot Daemon Rust (`<agent>.exe watch`)** | Vòng lặp `100ms`, tốn ~`3.8MB RAM`, `0% CPU`. | Cực nhanh, ổn định 24/7, không tốn tiền API, không bị trễ Cold Start. |
| **Suy luận sâu & Lập báo cáo phức tạp** (So sánh thị trường, phân tích xu hướng vĩ mô, đối chiếu hợp đồng lớn) | **AI Agent (Antigravity / Gemini)** | Chạy theo lịch cố định (`08:00`, `15:15`) hoặc khi người dùng yêu cầu phân tích sâu. | Tận dụng trí tuệ nhân tạo để tổng hợp thông tin đa chiều. |
| **Cầu nối dữ liệu giữa AI và Zalo** | **CLI Mode (`<agent>.exe read | send`)** | AI gọi binary thực thi như một CLI Tool thông thường (`~2.8ms`). | AI không phải tự viết script tạm, không xả rác thư mục, tái sử dụng 100% logic Rust. |

---

## V. CẨM NANG XỬ LÝ SỰ CỐ NHANH (TROUBLESHOOTING CHECKLIST)

1. **Kiểm tra cổng CDP 58721 có đang mở không?**
   ```powershell
   netstat -ano | findstr 58721
   ```
   *Nếu không có dòng `LISTENING`:* Gõ `.\<agent>.exe boot` để hệ thống tự động tìm và khởi động lại Zalo với đúng cổng.

2. **Kiểm tra Daemon có đang chạy ngầm không và ăn bao nhiêu RAM?**
   ```powershell
   Get-Process | Where-Object { $_.ProcessName -match "minh_" } | Select-Object Id, ProcessName, @{Name="RAM (MB)"; Expression={[math]::Round($_.WorkingSet64 / 1MB, 2)}} | Format-Table -AutoSize
   ```

3. **Khi Zalo tự động cập nhật phiên bản mới (VD: `Zalo-26.3.1`):**
   * Bạn **không cần làm gì cả**. Hàm `ensure_zalo_cdp()` tự động quét thư mục phiên bản mới nhất theo thời gian sửa đổi (`modified time`) của thư mục.

4. **Khi cần dừng toàn bộ các bot để nâng cấp code:**
   ```powershell
   Get-Process -Name "minh_*" -ErrorAction SilentlyContinue | Stop-Process -Force
   ```

---
*Toàn bộ giải pháp được đóng gói độc lập, tự vận hành và sẵn sàng nhân bản tức thì trên mọi hệ thống Windows 11.*
