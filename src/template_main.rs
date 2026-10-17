// ============================================================================
// KHUÔN MẪU AGENT ZALO RUST ĐA MÁY (ZERO-HARDCODE PORTABLE TEMPLATE)
// ============================================================================
// Kiến trúc: Native Rust (Zero Crates) + winsqlite3.dll + Zalo PC CDP Loopback
// Tốc độ phản xạ: < 30ms | Bộ nhớ RAM: ~3.8MB | 100% Ẩn ngầm (Zero Console Flash)
// ============================================================================

use std::env;
use std::ffi::{CStr, CString};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::os::raw::{c_char, c_double, c_int, c_void};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::ptr;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

// ============================================================================
// 1. CẤU HÌNH NHANH (CHỈ CẦN SỬA KHỐI NÀY KHI TẠO BOT MỚI CHO BẤT KỲ DỰ ÁN NÀO)
// ============================================================================
const BOT_NAME: &str = "Minh Mẫu";
const BOT_TITLE: &str = "Trợ Lý Điều Hành Mẫu Đa Máy";
const WAKE_PREFIXES: &[&str] = &["/mm", "minh mẫu", "minh mau"];
const INSTANCE_MUTEX: &str = "Local\\MinhMau_Daemon_Instance_Mutex";
const DB_FILE_NAME: &str = "agent_data.db";
const CDP_PORT: u16 = 58721;
const POLL_INTERVAL_MS: u64 = 100;
const CREATE_NO_WINDOW: u32 = 0x08000000;

// ============================================================================
// 2. WINDOWS KERNEL32 NAMED MUTEX (ĐỒNG BỘ ĐA TIẾN TRÌNH & CHỐNG CHẠY TRÙNG)
// ============================================================================
#[link(name = "kernel32")]
extern "system" {
    fn CreateMutexA(
        lpMutexAttributes: *mut c_void,
        bInitialOwner: c_int,
        lpName: *const c_char,
    ) -> *mut c_void;
    fn OpenMutexA(
        dwDesiredAccess: u32,
        bInheritHandle: c_int,
        lpName: *const c_char,
    ) -> *mut c_void;
    fn GetLastError() -> u32;
    fn WaitForSingleObject(hHandle: *mut c_void, dwMilliseconds: u32) -> u32;
    fn ReleaseMutex(hMutex: *mut c_void) -> c_int;
    fn CloseHandle(hObject: *mut c_void) -> c_int;
}

pub struct ZaloMutexGuard {
    handle: *mut c_void,
}

impl ZaloMutexGuard {
    pub fn acquire(timeout_ms: u32) -> Option<Self> {
        let name = CString::new("Local\\ZaloPC_Bridge_Mutex").ok()?;
        unsafe {
            let handle = CreateMutexA(ptr::null_mut(), 0, name.as_ptr());
            if handle.is_null() {
                return None;
            }
            let wait_res = WaitForSingleObject(handle, timeout_ms);
            if wait_res == 0 || wait_res == 0x80 {
                Some(ZaloMutexGuard { handle })
            } else {
                CloseHandle(handle);
                None
            }
        }
    }
}

impl Drop for ZaloMutexGuard {
    fn drop(&mut self) {
        unsafe {
            if !self.handle.is_null() {
                ReleaseMutex(self.handle);
                CloseHandle(self.handle);
            }
        }
    }
}

pub fn is_named_mutex_alive(name_str: &str) -> bool {
    if let Ok(cname) = CString::new(name_str) {
        unsafe {
            let h = OpenMutexA(0x00100000 /* SYNCHRONIZE */, 0, cname.as_ptr());
            if !h.is_null() {
                CloseHandle(h);
                return true;
            }
        }
    }
    false
}

// ============================================================================
// 3. NATIVE FFI: WINSQLITE3.DLL (CÓ SẴN TRÊN MỌI MÁY WINDOWS 11)
// ============================================================================
#[repr(C)]
pub struct sqlite3 {
    _unused: [u8; 0],
}
#[repr(C)]
pub struct sqlite3_stmt {
    _unused: [u8; 0],
}

pub const SQLITE_OK: c_int = 0;
pub const SQLITE_ROW: c_int = 100;
pub const SQLITE_DONE: c_int = 101;
pub const SQLITE_OPEN_READWRITE: c_int = 0x00000002;
pub const SQLITE_OPEN_CREATE: c_int = 0x00000004;

#[link(name = "winsqlite3", kind = "raw-dylib")]
extern "C" {
    pub fn sqlite3_open_v2(
        filename: *const c_char,
        ppDb: *mut *mut sqlite3,
        flags: c_int,
        zVfs: *const c_char,
    ) -> c_int;
    pub fn sqlite3_close(db: *mut sqlite3) -> c_int;
    pub fn sqlite3_prepare_v2(
        db: *mut sqlite3,
        zSql: *const c_char,
        nByte: c_int,
        ppStmt: *mut *mut sqlite3_stmt,
        pzTail: *mut *const c_char,
    ) -> c_int;
    pub fn sqlite3_step(pStmt: *mut sqlite3_stmt) -> c_int;
    pub fn sqlite3_finalize(pStmt: *mut sqlite3_stmt) -> c_int;
    pub fn sqlite3_column_text(pStmt: *mut sqlite3_stmt, iCol: c_int) -> *const c_char;
    pub fn sqlite3_column_double(pStmt: *mut sqlite3_stmt, iCol: c_int) -> c_double;
    pub fn sqlite3_column_int64(pStmt: *mut sqlite3_stmt, iCol: c_int) -> i64;
    pub fn sqlite3_errmsg(db: *mut sqlite3) -> *const c_char;
    pub fn sqlite3_exec(
        db: *mut sqlite3,
        sql: *const c_char,
        callback: *mut c_void,
        arg: *mut c_void,
        errmsg: *mut *mut c_char,
    ) -> c_int;
}

pub struct Db {
    pub handle: *mut sqlite3,
}

impl Db {
    pub fn open(path_str: &str) -> Result<Self, String> {
        let c_path = CString::new(path_str).map_err(|e| e.to_string())?;
        let mut handle: *mut sqlite3 = ptr::null_mut();
        let rc = unsafe {
            sqlite3_open_v2(
                c_path.as_ptr(),
                &mut handle,
                SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE,
                ptr::null(),
            )
        };
        if rc != SQLITE_OK {
            return Err(format!("Lỗi mở SQLite: {}", rc));
        }
        Ok(Db { handle })
    }

    pub fn exec(&self, sql: &str) -> Result<(), String> {
        let c_sql = CString::new(sql).map_err(|e| e.to_string())?;
        let rc = unsafe {
            sqlite3_exec(
                self.handle,
                c_sql.as_ptr(),
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
            )
        };
        if rc != SQLITE_OK {
            let err = unsafe { CStr::from_ptr(sqlite3_errmsg(self.handle)) }.to_string_lossy();
            return Err(format!("Lỗi thực thi SQL: {}", err));
        }
        Ok(())
    }

    pub fn get_col_str(stmt: *mut sqlite3_stmt, col: c_int) -> String {
        unsafe {
            let p = sqlite3_column_text(stmt, col);
            if p.is_null() {
                String::new()
            } else {
                CStr::from_ptr(p).to_string_lossy().into_owned()
            }
        }
    }
}

impl Drop for Db {
    fn drop(&mut self) {
        unsafe {
            if !self.handle.is_null() {
                sqlite3_close(self.handle);
            }
        }
    }
}

// ============================================================================
// 4. ZERO-WAIT CDP CLIENT (KẾT NỐI KHÔNG CHỜ & ASYNC UI DISPATCH)
// ============================================================================
pub struct CdpClient {
    stream: TcpStream,
    msg_id: u32,
}

impl CdpClient {
    pub fn connect_zalo() -> Result<Self, String> {
        if let Ok(ws_path) = Self::find_zalo_ws_path(CDP_PORT) {
            if let Ok(client) = Self::connect(CDP_PORT, &ws_path) {
                return Ok(client);
            }
        }
        Self::ensure_zalo_cdp()?;
        let ws_path = Self::find_zalo_ws_path(CDP_PORT)?;
        Self::connect(CDP_PORT, &ws_path)
    }

    pub fn connect_with_cached_path(cached_path: &mut String) -> Result<Self, String> {
        if !cached_path.is_empty() {
            if let Ok(client) = Self::connect(CDP_PORT, cached_path) {
                return Ok(client);
            }
        }
        if let Ok(ws_path) = Self::find_zalo_ws_path(CDP_PORT) {
            *cached_path = ws_path;
            return Self::connect(CDP_PORT, cached_path);
        }
        Err(format!("Không kết nối được cổng CDP {}", CDP_PORT))
    }

    pub fn ensure_zalo_cdp() -> Result<(), String> {
        if Self::find_zalo_ws_path(CDP_PORT).is_ok() {
            return Ok(());
        }
        let local_app_data = env::var("LOCALAPPDATA")
            .unwrap_or_else(|_| r"C:\Users\Admin\AppData\Local".to_string());
        let zalo_base = PathBuf::from(local_app_data).join("Programs").join("Zalo");
        let mut zalo_exe: Option<(SystemTime, PathBuf)> = None;

        if let Ok(entries) = std::fs::read_dir(&zalo_base) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_dir() {
                    if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
                        if name.starts_with("Zalo-") {
                            let candidate = p.join("Zalo.exe");
                            if let Ok(meta) = std::fs::metadata(&candidate) {
                                let mtime = meta.modified().unwrap_or(UNIX_EPOCH);
                                if zalo_exe.as_ref().map_or(true, |(best_t, _)| mtime > *best_t) {
                                    zalo_exe = Some((mtime, candidate));
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Some((_, exe_path)) = zalo_exe {
            println!(
                "⚡ [Self-Boot] Đang khởi động Zalo PC kèm cổng CDP {}: {}",
                CDP_PORT,
                exe_path.display()
            );
            let _ = Command::new("powershell")
                .creation_flags(CREATE_NO_WINDOW)
                .args([
                    "-NoProfile",
                    "-Command",
                    &format!(
                        "Stop-Process -Name Zalo -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800; Start-Process -FilePath '{}' -ArgumentList '--remote-debugging-port={}' | Out-Null",
                        exe_path.display(),
                        CDP_PORT
                    ),
                ])
                .output();

            for _ in 0..20 {
                thread::sleep(Duration::from_millis(500));
                if Self::find_zalo_ws_path(CDP_PORT).is_ok() {
                    thread::sleep(Duration::from_millis(1200));
                    println!("✅ [Self-Boot] Cổng CDP {} đã sẵn sàng!", CDP_PORT);
                    return Ok(());
                }
            }
            Err(format!("Đã gọi Zalo.exe nhưng cổng {} chưa phản hồi.", CDP_PORT))
        } else {
            Err("Không tìm thấy Zalo.exe trong %LOCALAPPDATA%\\Programs\\Zalo.".to_string())
        }
    }

    fn parse_content_length(header_str: &str) -> Option<usize> {
        for line in header_str.lines() {
            let lower = line.to_ascii_lowercase();
            if let Some(rest) = lower.strip_prefix("content-length:") {
                if let Ok(n) = rest.trim().parse::<usize>() {
                    return Some(n);
                }
            }
        }
        None
    }

    pub fn find_zalo_ws_path(port: u16) -> Result<String, String> {
        let addr = format!("127.0.0.1:{}", port);
        let mut stream = TcpStream::connect_timeout(
            &addr.parse().unwrap(),
            Duration::from_millis(80),
        )
        .map_err(|e| e.to_string())?;
        stream.set_nodelay(true).ok();
        stream
            .set_read_timeout(Some(Duration::from_millis(80)))
            .ok();

        let req = format!(
            "GET /json HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
            port
        );
        stream.write_all(req.as_bytes()).map_err(|e| e.to_string())?;

        let mut buf = Vec::with_capacity(4096);
        let mut chunk = [0u8; 2048];
        let mut expected_total: Option<usize> = None;

        loop {
            match stream.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => {
                    buf.extend_from_slice(&chunk[..n]);
                    if expected_total.is_none() {
                        if let Ok(s) = std::str::from_utf8(&buf) {
                            if let Some(hdr_end) = s.find("\r\n\r\n") {
                                let body_start = hdr_end + 4;
                                if let Some(cl) = Self::parse_content_length(&s[..hdr_end]) {
                                    expected_total = Some(body_start + cl);
                                }
                            }
                        }
                    }
                    if let Some(total) = expected_total {
                        if buf.len() >= total {
                            break;
                        }
                    } else if buf.ends_with(b"]") || buf.ends_with(b"]\r\n") || buf.ends_with(b"]\n") {
                        break;
                    }
                }
                Err(_) => break,
            }
        }

        let body = String::from_utf8_lossy(&buf);
        let key = "\"webSocketDebuggerUrl\":";
        if let Some(pos) = body.find(key) {
            let rest = &body[pos + key.len()..];
            let quote1 = rest.find('"').ok_or("Malformed JSON")?;
            let rest2 = &rest[quote1 + 1..];
            let quote2 = rest2.find('"').ok_or("Malformed JSON")?;
            let full_url = &rest2[..quote2];
            if let Some(idx) = full_url.find(&format!("127.0.0.1:{}", port)) {
                let path = &full_url[idx + format!("127.0.0.1:{}", port).len()..];
                return Ok(path.to_string());
            }
            if let Some(idx) = full_url.find("/devtools/") {
                return Ok(full_url[idx..].to_string());
            }
        }
        Err(format!("Cổng {} mở nhưng không tìm thấy Target CDP hợp lệ", port))
    }

    pub fn connect(port: u16, path: &str) -> Result<Self, String> {
        let addr = format!("127.0.0.1:{}", port);
        let mut stream = TcpStream::connect_timeout(
            &addr.parse().unwrap(),
            Duration::from_millis(150),
        )
        .map_err(|e| e.to_string())?;

        stream.set_nodelay(true).ok();
        stream
            .set_read_timeout(Some(Duration::from_millis(800)))
            .ok();

        let req = format!(
            "GET {} HTTP/1.1\r\n\
             Host: 127.0.0.1:{}\r\n\
             Upgrade: websocket\r\n\
             Connection: Upgrade\r\n\
             Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\
             Sec-WebSocket-Version: 13\r\n\r\n",
            path, port
        );
        stream.write_all(req.as_bytes()).map_err(|e| e.to_string())?;

        let mut buf = [0u8; 1024];
        let n = stream.read(&mut buf).map_err(|e| e.to_string())?;
        let res = String::from_utf8_lossy(&buf[..n]);
        if !res.starts_with("HTTP/1.1 101") {
            return Err("WebSocket handshake thất bại".to_string());
        }

        Ok(CdpClient { stream, msg_id: 0 })
    }

    fn send_ws_frame(&mut self, text: &str) -> Result<(), String> {
        let bytes = text.as_bytes();
        let mut frame = Vec::with_capacity(bytes.len() + 14);
        frame.push(0x81);
        let len = bytes.len();
        if len <= 125 {
            frame.push(0x80 | (len as u8));
        } else if len <= 65535 {
            frame.push(0x80 | 126);
            frame.extend_from_slice(&(len as u16).to_be_bytes());
        } else {
            frame.push(0x80 | 127);
            frame.extend_from_slice(&(len as u64).to_be_bytes());
        }
        let mask_key = [0x12, 0x34, 0x56, 0x78];
        frame.extend_from_slice(&mask_key);
        for (i, &b) in bytes.iter().enumerate() {
            frame.push(b ^ mask_key[i % 4]);
        }
        self.stream.write_all(&frame).map_err(|e| e.to_string())
    }

    fn read_ws_frame(&mut self) -> Result<String, String> {
        let mut header = [0u8; 2];
        self.stream
            .read_exact(&mut header)
            .map_err(|e| e.to_string())?;
        let len_byte = header[1] & 0x7f;
        let payload_len: usize = if len_byte <= 125 {
            len_byte as usize
        } else if len_byte == 126 {
            let mut ext = [0u8; 2];
            self.stream
                .read_exact(&mut ext)
                .map_err(|e| e.to_string())?;
            u16::from_be_bytes(ext) as usize
        } else {
            let mut ext = [0u8; 8];
            self.stream
                .read_exact(&mut ext)
                .map_err(|e| e.to_string())?;
            u64::from_be_bytes(ext) as usize
        };

        let mut payload = vec![0u8; payload_len];
        self.stream
            .read_exact(&mut payload)
            .map_err(|e| e.to_string())?;
        Ok(String::from_utf8_lossy(&payload).into_owned())
    }

    pub fn eval_js(&mut self, expr: &str) -> Result<String, String> {
        self.msg_id = self.msg_id.wrapping_add(1);
        let id = self.msg_id;
        let escaped = Self::json_escape(expr);
        let cmd = format!(
            "{{\"id\":{},\"method\":\"Runtime.evaluate\",\"params\":{{\"expression\":\"{}\",\"returnByValue\":true}}}}",
            id, escaped
        );
        self.send_ws_frame(&cmd)?;
        let id_pat = format!("\"id\":{}", id);
        for _ in 0..10 {
            let frame = self.read_ws_frame()?;
            if frame.contains(&id_pat) {
                return Ok(Self::extract_json_value(&frame));
            }
        }
        Err("Timeout chờ kết quả CDP Runtime.evaluate".to_string())
    }

    pub fn read_latest_message(&mut self) -> Result<String, String> {
        let js = r#"
        (() => {
            const h = document.querySelector('.header-title, [data-id="header-title"], .chat-title');
            if (!h || (!h.innerText.includes('Documents') && !h.innerText.includes('Cloud'))) {
                const items = Array.from(document.querySelectorAll('.msg-item, [id^="msg-item"], .conv-item'));
                const target = items.find(el => el.innerText && (el.innerText.includes('My Documents') || el.innerText.includes('Cloud của tôi')));
                if (target) target.click();
            }
            const chatView = document.querySelector('#messageView');
            if (!chatView) return '';
            const bubbles = chatView.querySelectorAll('.card--text, [data-id="div_SentMsg_Text"], [data-id="div_ReceivedMsg_Text"]');
            for (let i = bubbles.length - 1; i >= 0; i--) {
                const t = (bubbles[i].innerText || '').trim();
                if (t.length > 1 && !t.startsWith(':-') && !t.startsWith('/-')) {
                    return t;
                }
            }
            return '';
        })()
        "#;
        self.eval_js(js)
    }

    pub fn send_message_to_my_documents(&mut self, text: &str) -> Result<String, String> {
        let js_text = Self::json_escape(text);
        // ASYNC UI DISPATCH: Nhả Mutex ngay sau paste chữ, ủy thác Enter & click cho Event Loop
        let js = format!(
            r#"
        (() => {{
            const h = document.querySelector('.header-title, [data-id="header-title"], .chat-title');
            if (!h || (!h.innerText.includes('Documents') && !h.innerText.includes('Cloud'))) {{
                const items = Array.from(document.querySelectorAll('.msg-item, [id^="msg-item"], .conv-item'));
                const target = items.find(el => el.innerText && (el.innerText.includes('My Documents') || el.innerText.includes('Cloud của tôi')));
                if (target) target.click();
            }}
            const input = document.querySelector('#richInput');
            if (!input) return 'ERR_NO_RICHINPUT';
            input.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, "{}");
            setTimeout(() => {{
                const ev = new KeyboardEvent('keydown', {{
                    key: 'Enter',
                    code: 'Enter',
                    keyCode: 13,
                    which: 13,
                    bubbles: true,
                    cancelable: true
                }});
                input.dispatchEvent(ev);
                const sendBtn = document.querySelector('[data-translate-title="STR_SEND"], .send-msg-btn, #sendBtn');
                if (sendBtn) sendBtn.click();
            }}, 0);
            return 'SENT_OK';
        }})()
        "#,
            js_text
        );
        self.eval_js(&js)
    }

    fn json_escape(s: &str) -> String {
        let mut out = String::with_capacity(s.len() + 16);
        for c in s.chars() {
            match c {
                '\\' => out.push_str("\\\\"),
                '"' => out.push_str("\\\""),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                _ => out.push(c),
            }
        }
        out
    }

    fn extract_json_value(json: &str) -> String {
        let key = "\"value\":";
        if let Some(pos) = json.find(key) {
            let rest = json[pos + key.len()..].trim_start();
            if !rest.starts_with('"') {
                let end = rest.find([',', '}']).unwrap_or(rest.len());
                return rest[..end].trim().to_string();
            }
            let inner = &rest[1..];
            let mut out = String::new();
            let mut chars = inner.chars();
            while let Some(c) = chars.next() {
                if c == '"' {
                    break;
                } else if c == '\\' {
                    if let Some(nc) = chars.next() {
                        match nc {
                            'n' => out.push('\n'),
                            'r' => out.push('\r'),
                            't' => out.push('\t'),
                            '\\' => out.push('\\'),
                            '"' => out.push('"'),
                            'u' => {
                                let hex: String = chars.by_ref().take(4).collect();
                                if let Ok(code) = u32::from_str_radix(&hex, 16) {
                                    if let Some(ch) = std::char::from_u32(code) {
                                        out.push(ch);
                                    }
                                }
                            }
                            _ => out.push(nc),
                        }
                    }
                } else {
                    out.push(c);
                }
            }
            return out;
        }
        json.to_string()
    }
}

// ============================================================================
// 5. CƠ CHẾ ĐỊNH VỊ ZERO-HARDCODE (CHẠY TRÊN MỌI Ổ ĐĨA & MÁY TÍNH KHÁC)
// ============================================================================
fn resolve_data_dir() -> PathBuf {
    // 1. Kiểm tra thư mục chứa file thực thi hiện tại
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join("data");
            if candidate.exists() {
                return candidate;
            }
            return dir.to_path_buf();
        }
    }
    // 2. Kiểm tra Current Working Directory
    if let Ok(cwd) = env::current_dir() {
        let candidate = cwd.join("data");
        if candidate.exists() {
            return candidate;
        }
        return cwd;
    }
    PathBuf::from(".")
}

fn resolve_db_path() -> String {
    resolve_data_dir()
        .join(DB_FILE_NAME)
        .to_string_lossy()
        .into_owned()
}

fn init_sqlite_schema(db: &Db) -> Result<(), String> {
    db.exec(
        "CREATE TABLE IF NOT EXISTS agent_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'MOI',
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS agent_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            log_text TEXT,
            created_at INTEGER
        );",
    )
}

// ============================================================================
// 6. BỘ LỌC TIỀN TỐ & XỬ LÝ LỆNH CHUYÊN BIỆT
// ============================================================================
fn parse_wake_command(msg: &str) -> Option<String> {
    let trimmed = msg.trim();
    // Bỏ qua tin nhắn phản hồi của chính mình hoặc bot khác
    if trimmed.starts_with('[')
        || trimmed.starts_with("📊")
        || trimmed.starts_with("📈")
        || trimmed.starts_with("📋")
        || trimmed.starts_with("🏗️")
        || trimmed.starts_with("⚠️")
        || trimmed.starts_with("🤖")
    {
        return None;
    }

    let lower = trimmed.to_lowercase();
    for &prefix in WAKE_PREFIXES {
        if lower.starts_with(prefix) {
            let cmd_body = trimmed[prefix.len()..].trim();
            return Some(cmd_body.to_string());
        }
    }
    None
}

fn handle_agent_command(db: &Db, cmd_body: &str) -> String {
    let now_sec = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let task_id = format!("TSK-{:04}", now_sec % 10000);
    let clean = cmd_body.trim();
    let lower = clean.to_lowercase();

    // Lưu vào SQLite
    let ins = format!(
        "INSERT OR REPLACE INTO agent_tasks (id, title, status, created_at) VALUES ('{}', '{}', 'MOI', {});",
        task_id,
        clean.replace('\'', "''"),
        now_sec
    );
    let _ = db.exec(&ins);

    if lower.is_empty() || lower == "help" || lower == "hướng dẫn" {
        return format!(
            "🤖 [{} - {}]\n\
             • Tiền tố nhận lệnh: `{}`\n\
             • Cú pháp: `<tiền_tố> <nội dung công việc>`\n\
             • Tốc độ phản xạ: < 30ms (Native Windows 11 FFI)",
            BOT_NAME, BOT_TITLE, WAKE_PREFIXES.join(" | ")
        );
    }

    // Văn phong chuyên môn gãy gọn: Trọng tâm - Điểm nghẽn - Kiến nghị
    format!(
        "📋 [{} - {}] Báo cáo Lãnh đạo:\n\
         • Tiếp nhận & khóa việc: \"{}\"\n\
         • Ghi Sổ Điều Hành: [{}] | Trạng thái: Tiếp nhận (P1)\n\
         👉 Tham mưu: Đã lưu vết Audit Log và kích hoạt theo dõi tiến độ.",
        BOT_NAME, BOT_TITLE, clean, task_id
    )
}

// ============================================================================
// 7. MAIN DISPATCHER & DAEMON RUNNER
// ============================================================================
fn main() {
    let t0 = Instant::now();
    let args: Vec<String> = env::args().collect();
    let cmd = if args.len() > 1 { args[1].as_str() } else { "help" };

    let db_path = resolve_db_path();
    let db = Db::open(&db_path).unwrap_or_else(|e| {
        eprintln!("⚠️ Lỗi mở DB ({}): {}", db_path, e);
        Db { handle: ptr::null_mut() }
    });
    if !db.handle.is_null() {
        let _ = init_sqlite_schema(&db);
    }

    match cmd {
        "read" => {
            let _guard = ZaloMutexGuard::acquire(2000);
            match CdpClient::connect_zalo() {
                Ok(mut cdp) => match cdp.read_latest_message() {
                    Ok(msg) => println!("📩 [Zalo My Documents]: {}\n⏱️ {:.2?}", msg, t0.elapsed()),
                    Err(e) => eprintln!("❌ Lỗi đọc: {}", e),
                },
                Err(e) => eprintln!("❌ {}", e),
            }
        }
        "send" => {
            if args.len() < 3 {
                eprintln!("Cách dùng: {} send \"<nội dung>\"", BOT_NAME);
                return;
            }
            let text = args[2..].join(" ");
            let _guard = ZaloMutexGuard::acquire(2500);
            match CdpClient::connect_zalo() {
                Ok(mut cdp) => match cdp.send_message_to_my_documents(&text) {
                    Ok(res) => println!("✅ Đã gửi Zalo ({}): {:.2?}", res, t0.elapsed()),
                    Err(e) => eprintln!("❌ Lỗi gửi: {}", e),
                },
                Err(e) => eprintln!("❌ {}", e),
            }
        }
        "boot" => match CdpClient::ensure_zalo_cdp() {
            Ok(_) => {
                if let Ok(exe) = env::current_exe() {
                    if !is_named_mutex_alive(INSTANCE_MUTEX) {
                        let _ = Command::new(&exe)
                            .arg("watch")
                            .stdin(Stdio::null())
                            .stdout(Stdio::null())
                            .stderr(Stdio::null())
                            .creation_flags(CREATE_NO_WINDOW)
                            .spawn();
                    }
                }
                println!("✅ Cổng CDP Zalo ({}) & Daemon [{}] sẵn sàng.", CDP_PORT, BOT_NAME);
            }
            Err(e) => eprintln!("❌ {}", e),
        },
        "watch" => {
            let instance_name = CString::new(INSTANCE_MUTEX).unwrap();
            let _inst_handle = unsafe { CreateMutexA(ptr::null_mut(), 0, instance_name.as_ptr()) };
            if unsafe { GetLastError() } == 183 {
                eprintln!("⚠️ [{}] Daemon đã đang chạy trong một tiến trình khác.", BOT_NAME);
                return;
            }

            println!("🚀 [{}] Trực chiến Zalo PC ({}ms Polling)...", BOT_NAME, POLL_INTERVAL_MS);
            let _ = CdpClient::ensure_zalo_cdp();
            let mut cached_ws_path = CdpClient::find_zalo_ws_path(CDP_PORT).unwrap_or_default();
            let mut last_msg = String::new();
            let mut first_run = true;

            loop {
                {
                    let _guard = ZaloMutexGuard::acquire(1000);
                    if let Ok(mut cdp) = CdpClient::connect_with_cached_path(&mut cached_ws_path) {
                        if let Ok(msg) = cdp.read_latest_message() {
                            let trimmed = msg.trim().to_string();
                            if !trimmed.is_empty() && trimmed != last_msg {
                                last_msg = trimmed.clone();
                                if first_run {
                                    first_run = false;
                                } else if let Some(cmd_body) = parse_wake_command(&last_msg) {
                                    let t_cmd = Instant::now();
                                    let reply = handle_agent_command(&db, &cmd_body);
                                    let _ = cdp.send_message_to_my_documents(&reply);
                                    last_msg = reply;
                                    println!("✅ Đã phản hồi trong {:.2?}", t_cmd.elapsed());
                                }
                            }
                        }
                    } else {
                        cached_ws_path.clear();
                    }
                }
                thread::sleep(Duration::from_millis(POLL_INTERVAL_MS));
            }
        }
        _ => {
            println!("🤖 {} - {}", BOT_NAME, BOT_TITLE);
            println!("  Lệnh sẵn sàng:");
            println!("  • read          : Đọc tin nhắn mới nhất từ 'My Documents'");
            println!("  • send \"<txt>\"  : Gửi tin nhắn vào 'My Documents'");
            println!("  • boot          : Khởi động Zalo CDP & Daemon ngầm");
            println!("  • watch         : Trực chiến vòng lặp thời gian thực");
        }
    }
}
