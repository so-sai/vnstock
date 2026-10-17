---
name: zalo-rust-bot-forge
description: Kỹ năng đúc và triển khai Bot Zalo Rust mới siêu tốc (<30ms) trên Windows 11 từ khuôn mẫu `src/template_main.rs`. Tự động phân chia tiền tố (Prefix), Named Mutex, kết nối `winsqlite3.dll` và Zalo PC CDP Loopback (127.0.0.1:58721). Dùng khi người dùng yêu cầu tạo thêm Bot mới (như Minh Kiểm LCA, Minh Kế Kế toán, Minh CAD) hoặc triển khai sang máy tính mới.
---

# Kỹ năng Đúc Bot Zalo Rust Siêu Tốc (`zalo-rust-bot-forge`)

Kỹ năng này cho phép AI Agent tự động đúc ra một Bot Zalo Rust độc lập mới trong **dưới 60 giây**, tuân thủ 100% nguyên tắc **Zero-Hardcode, Zero-Crates, Sub-30ms Latency** và **Zero-Collision** với các bot hiện hữu.

## 1. Quy Trình Đúc Bot 4 Bước Tự Động

Mỗi khi người dùng yêu cầu tạo bot mới (VD: *"Hãy tạo Bot Minh Kiểm chuyên kiểm toán chứng từ nhập khẩu và LCA"*):

### Bước 1: Tra Cứu Namespace Registry (Chống Xung Đột)
Kiểm tra bảng tiền tố độc quyền để tránh va chạm với các bot đã chạy:
- Bot 1: `Minh Tâm` $\rightarrow$ `/mt` (`Local\MinhTam_Daemon_Instance_Mutex`)
- Bot 2: `Minh Chứng` $\rightarrow$ `/mc` (`Local\MinhChung_Daemon_Instance_Mutex`)
- Bot 3: `Minh Kiểm` $\rightarrow$ `/mk`, `/lca` (`Local\MinhKiem_Daemon_Instance_Mutex`)
- Bot mới: Phải chọn một tiền tố chưa từng xuất hiện (VD: `/kt`, `/cad`, `/kho`).

### Bước 2: Sinh Mã Nguồn Từ `src/template_main.rs`
1. Đọc file khuôn mẫu [src/template_main.rs](file:///D:/04.%20Software/opensource_contrib/PTCK_VNSTOCK/src/template_main.rs).
2. Tạo file mã nguồn mới tại thư mục dự án tương ứng (VD: `src/<bot_name>_main.rs` hoặc thư mục dự án mới).
3. Điền đúng khối cấu hình:
   ```rust
   const BOT_NAME: &str = "<Tên_Bot>";
   const BOT_TITLE: &str = "<Chức_Danh_Chuyên_Môn>";
   const WAKE_PREFIXES: &[&str] = &["/<tiền_tố>", "<tên bot không dấu>", "<tên bot có dấu>"];
   const INSTANCE_MUTEX: &str = "Local\\<TênBot>_Daemon_Instance_Mutex";
   const DB_FILE_NAME: &str = "<bot_name>.db";
   const CDP_PORT: u16 = 58721;
   const POLL_INTERVAL_MS: u64 = 100;
   ```
4. Bổ sung các bảng SQLite và hàm logic nghiệp vụ chuyên sâu vào hàm `handle_agent_command()`.

### Bước 3: Biên Dịch 1 Dòng Duy Nhất (Native Rust)
Gọi `rustc` trực tiếp (không cần Cargo, không cần internet):
```powershell
rustc -C opt-level=3 src\<bot_name>_main.rs -o <bot_name>.exe
Remove-Item <bot_name>.pdb -Force -ErrorAction SilentlyContinue
```

### Bước 4: Khởi Chạy Tách Rời (WMI Detached SW_HIDE)
Kích hoạt tiến trình ngầm vĩnh viễn không phụ thuộc vào shell:
```powershell
$cmd = '"' + (Get-Location).Path + '\<bot_name>.exe" watch'
$startup = [wmiclass]"Win32_ProcessStartup"
$startup.Properties['ShowWindow'].Value = 0 # SW_HIDE
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = (Get-Location).Path } | Out-Null
```

---

## 2. Tiêu Chuẩn Nghiệp Vụ Của Bot Mới (Do & Don't)

- **DO:**
  - Luôn sử dụng văn phong **Trọng tâm - Điểm nghẽn - Tham mưu hành động**.
  - Luôn gắn kèm mã định danh tác vụ `[TSK-xxxx]` hoặc mã hồ sơ để lãnh đạo dễ kiểm soát.
  - Sử dụng trực tiếp `winsqlite3.dll` có sẵn trong Windows 11 để lưu trữ dữ liệu.
  - Đảm bảo thời gian giữ khóa `Local\ZaloPC_Bridge_Mutex` luôn **`< 20ms`** (nhờ Async UI Dispatch `setTimeout 0`).

- **DON'T:**
  - Tuyệt đối **KHÔNG** bắt ký tự đại diện chung chung như `starts_with('/')` (sẽ cướp lệnh của các bot khác).
  - Tuyệt đối **KHÔNG** tự ý gọi lệnh PowerShell làm chớp nháy cửa sổ màn hình người dùng.
  - Tuyệt đối **KHÔNG** dùng in-memory cache cho dữ liệu thường xuyên cập nhật từ bên ngoài (tránh stale data).
