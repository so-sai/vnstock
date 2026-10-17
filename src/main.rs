use std::env;
use std::ffi::{CStr, CString};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::os::raw::{c_char, c_double, c_int, c_void};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::ptr;
use std::thread;
use std::time::{Duration, Instant};

const CREATE_NO_WINDOW: u32 = 0x08000000;

// ============================================================================
// 1. WINDOWS NAMED MUTEX (Wraps Complete CDP Transaction <10ms)
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

// ============================================================================
// 2. NATIVE WINDOWS 11 SQLITE FFI (winsqlite3.dll - Zero External Crates)
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

#[link(name = "winsqlite3", kind = "raw-dylib")]
extern "C" {
    pub fn sqlite3_open(filename: *const c_char, ppDb: *mut *mut sqlite3) -> c_int;
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
    pub fn sqlite3_column_text(pStmt: *mut sqlite3_stmt, iCol: c_int) -> *const u8;
    pub fn sqlite3_column_int(pStmt: *mut sqlite3_stmt, iCol: c_int) -> c_int;
    pub fn sqlite3_column_double(pStmt: *mut sqlite3_stmt, iCol: c_int) -> c_double;
    pub fn sqlite3_errmsg(db: *mut sqlite3) -> *const c_char;
}

pub struct Db {
    handle: *mut sqlite3,
}

impl Db {
    pub fn open(path: &str) -> Result<Self, String> {
        let c_path = CString::new(path).map_err(|e| e.to_string())?;
        let mut handle: *mut sqlite3 = ptr::null_mut();
        unsafe {
            let rc = sqlite3_open(c_path.as_ptr(), &mut handle);
            if rc != SQLITE_OK {
                let err = if !handle.is_null() {
                    CStr::from_ptr(sqlite3_errmsg(handle))
                        .to_string_lossy()
                        .into_owned()
                } else {
                    format!("SQLite open error code {}", rc)
                };
                if !handle.is_null() {
                    sqlite3_close(handle);
                }
                return Err(err);
            }
        }
        Ok(Db { handle })
    }

    pub fn get_col_str(stmt: *mut sqlite3_stmt, col: c_int) -> String {
        unsafe {
            let ptr = sqlite3_column_text(stmt, col);
            if ptr.is_null() {
                String::new()
            } else {
                CStr::from_ptr(ptr as *const c_char)
                    .to_string_lossy()
                    .into_owned()
            }
        }
    }

    pub fn get_col_f64(stmt: *mut sqlite3_stmt, col: c_int) -> f64 {
        unsafe { sqlite3_column_double(stmt, col) }
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
// 3. ZERO-WAIT ZALO PC CDP CLIENT (Cached ws_path + Content-Length + TCP_NODELAY)
// ============================================================================
pub struct CdpClient {
    stream: TcpStream,
    msg_id: u32,
}

impl CdpClient {
    pub fn connect_zalo() -> Result<Self, String> {
        if let Ok(ws_path) = Self::find_zalo_ws_path(58721) {
            if let Ok(client) = Self::connect(58721, &ws_path) {
                return Ok(client);
            }
        }
        Self::ensure_zalo_cdp()?;
        let ws_path = Self::find_zalo_ws_path(58721)?;
        Self::connect(58721, &ws_path)
    }

    pub fn connect_with_cached_path(cached_path: &mut String) -> Result<Self, String> {
        if !cached_path.is_empty() {
            if let Ok(client) = Self::connect(58721, cached_path) {
                return Ok(client);
            }
        }
        if let Ok(ws_path) = Self::find_zalo_ws_path(58721) {
            *cached_path = ws_path;
            return Self::connect(58721, cached_path);
        }
        Err("Không kết nối được cổng 58721".to_string())
    }

    pub fn ensure_zalo_cdp() -> Result<(), String> {
        if Self::find_zalo_ws_path(58721).is_ok() {
            return Ok(());
        }
        let local_app_data = env::var("LOCALAPPDATA")
            .unwrap_or_else(|_| r"C:\Users\Admin\AppData\Local".to_string());
        let zalo_base = PathBuf::from(local_app_data).join("Programs").join("Zalo");
        let mut zalo_exe: Option<(std::time::SystemTime, PathBuf)> = None;
        if let Ok(entries) = std::fs::read_dir(&zalo_base) {
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_dir() {
                    if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
                        if name.starts_with("Zalo-") {
                            let candidate = p.join("Zalo.exe");
                            if let Ok(meta) = std::fs::metadata(&candidate) {
                                let mtime = meta.modified().unwrap_or(std::time::UNIX_EPOCH);
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
                "⚡ [Self-Boot] Đang khởi động lại Zalo PC kèm cổng CDP 58721: {}",
                exe_path.display()
            );
            let _ = Command::new("powershell")
                .creation_flags(CREATE_NO_WINDOW)
                .args([
                    "-NoProfile",
                    "-Command",
                    &format!(
                        "Stop-Process -Name Zalo -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800; Start-Process -FilePath '{}' -ArgumentList '--remote-debugging-port=58721' | Out-Null",
                        exe_path.display()
                    ),
                ])
                .output();
            for _ in 0..20 {
                thread::sleep(Duration::from_millis(500));
                if Self::find_zalo_ws_path(58721).is_ok() {
                    thread::sleep(Duration::from_millis(1200));
                    println!("✅ [Self-Boot] Cổng CDP 58721 đã sẵn sàng!");
                    return Ok(());
                }
            }
            Err("Đã gọi Zalo.exe nhưng cổng 58721 chưa phản hồi.".to_string())
        } else {
            Err("Không tìm thấy Zalo.exe trong AppData\\Local\\Programs\\Zalo.".to_string())
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
                    } else if let Ok(s) = std::str::from_utf8(&buf) {
                        if s.contains("\r\n\r\n") && s.trim_end().ends_with(']') {
                            break;
                        }
                    }
                }
                Err(_) => break,
            }
        }
        let resp = String::from_utf8_lossy(&buf).into_owned();

        let body = resp.split("\r\n\r\n").nth(1).unwrap_or(&resp);
        for block in body.split('{') {
            if (block.contains("index.html") || block.contains("\"title\": \"Zalo\""))
                && block.contains("\"type\": \"page\"")
            {
                if let Some(pos) = block.find("\"webSocketDebuggerUrl\":") {
                    let sub = &block[pos..];
                    if let Some(q1) = sub.find("ws://") {
                        let sub2 = &sub[q1..];
                        if let Some(q2) = sub2.find('"') {
                            let full_ws = &sub2[..q2];
                            if let Some(slash_pos) = full_ws[5..].find('/') {
                                return Ok(full_ws[5 + slash_pos..].to_string());
                            }
                        }
                    }
                }
            }
        }
        Err("Không tìm thấy tab Zalo page".to_string())
    }

    fn connect(port: u16, path: &str) -> Result<Self, String> {
        let addr = format!("127.0.0.1:{}", port);
        let mut stream = TcpStream::connect_timeout(
            &addr.parse().unwrap(),
            Duration::from_millis(80),
        )
        .map_err(|e| e.to_string())?;
        stream.set_nodelay(true).ok();
        stream
            .set_read_timeout(Some(Duration::from_millis(500)))
            .ok();

        let handshake = format!(
            "GET {} HTTP/1.1\r\n\
             Host: 127.0.0.1:{}\r\n\
             Upgrade: websocket\r\n\
             Connection: Upgrade\r\n\
             Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\
             Sec-WebSocket-Version: 13\r\n\r\n",
            path, port
        );
        stream
            .write_all(handshake.as_bytes())
            .map_err(|e| e.to_string())?;

        let mut buf = [0u8; 512];
        let n = stream.read(&mut buf).map_err(|e| e.to_string())?;
        let resp = String::from_utf8_lossy(&buf[..n]);
        if !resp.contains(" 101 ") {
            return Err(format!("WebSocket handshake thất bại: {}", resp));
        }

        Ok(CdpClient { stream, msg_id: 0 })
    }

    fn send_ws_frame(&mut self, payload: &str) -> Result<(), String> {
        let bytes = payload.as_bytes();
        let len = bytes.len();
        let mut frame = Vec::with_capacity(len + 14);
        frame.push(0x81);

        let mask_key: [u8; 4] = [0x37, 0xfa, 0x21, 0x3d];
        if len <= 125 {
            frame.push(0x80 | (len as u8));
        } else if len <= 65535 {
            frame.push(0x80 | 126);
            frame.push(((len >> 8) & 0xff) as u8);
            frame.push((len & 0xff) as u8);
        } else {
            frame.push(0x80 | 127);
            for i in (0..8).rev() {
                frame.push(((len >> (i * 8)) & 0xff) as u8);
            }
        }
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
        let marker = "\"value\":\"";
        if let Some(pos) = json.find(marker) {
            let rest = &json[pos + marker.len()..];
            let mut out = String::new();
            let mut chars = rest.chars();
            while let Some(c) = chars.next() {
                if c == '"' {
                    break;
                } else if c == '\\' {
                    if let Some(nc) = chars.next() {
                        match nc {
                            'n' => out.push('\n'),
                            'r' => out.push('\r'),
                            't' => out.push('\t'),
                            '"' => out.push('"'),
                            '\\' => out.push('\\'),
                            'u' => {
                                let mut hex = String::with_capacity(4);
                                for _ in 0..4 {
                                    if let Some(hc) = chars.next() {
                                        hex.push(hc);
                                    }
                                }
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
// 4. DATABASE RESOLVER, CLI PIPELINE BRIDGE & QUANT ENGINE (MINH CHỨNG)
// ============================================================================
fn resolve_project_root() -> PathBuf {
    // 1. Kiểm tra thư mục chứa file thực thi hiện tại (Portable Zero-Hardcode)
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            if dir.join("backend").join("data").exists() {
                return dir.to_path_buf();
            }
            if let Some(parent) = dir.parent() {
                if parent.join("backend").join("data").exists() {
                    return parent.to_path_buf();
                }
            }
        }
    }
    // 2. Kiểm tra biến môi trường tùy chỉnh PTCK_ROOT
    if let Ok(custom) = env::var("PTCK_ROOT") {
        let p = PathBuf::from(custom);
        if p.join("backend").join("data").exists() {
            return p;
        }
    }
    // 3. Kiểm tra Current Working Directory
    if let Ok(cwd) = env::current_dir() {
        if cwd.join("backend").join("data").exists() {
            return cwd;
        }
    }
    // 4. Fallback danh sách đường dẫn khả dĩ trên các ổ đĩa
    for candidate in &[
        r"D:\04. Software\opensource_contrib\PTCK_VNSTOCK",
        r"E:\DEV\opensource_contrib\PTCK_VNSTOCK",
        r"C:\PTCK_VNSTOCK",
    ] {
        let p = PathBuf::from(candidate);
        if p.exists() {
            return p;
        }
    }
    PathBuf::from(".")
}

fn resolve_db_path(db_filename: &str) -> String {
    let root = resolve_project_root();
    root.join("backend")
        .join("data")
        .join(db_filename)
        .to_string_lossy()
        .into_owned()
}

pub fn ensure_watch_daemons_running() {
    let root = resolve_project_root();
    let mc_exe = root.join("minh_chung.exe");
    if !is_named_mutex_alive("Local\\MinhChung_Daemon_Instance_Mutex") && mc_exe.exists() {
        let _ = Command::new(&mc_exe)
            .arg("watch")
            .current_dir(&root)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn();
    }

    // Tự động tìm thư mục Minh Tâm qua biến môi trường hoặc thư mục anh em
    let mt_dir = env::var("MINH_TAM_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(r"G:\My Drive\05. AI - Data\GEM\04. THƯ KÝ DỰ ÁN XD"));
    let mt_exe = mt_dir.join("minh_tam.exe");
    if !is_named_mutex_alive("Local\\MinhTam_Daemon_Instance_Mutex") && mt_exe.exists() {
        let _ = Command::new(&mt_exe)
            .arg("watch")
            .current_dir(&mt_dir)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn();
    }
}

pub fn run_cli_sync() -> String {
    let t0 = Instant::now();
    let root = resolve_project_root();
    println!("🔄 [CLI Pipeline] Đang đồng bộ dữ liệu Vĩ mô & Lãi suất qua `ptck.py`...");
    let sbv_ok = Command::new("python")
        .creation_flags(CREATE_NO_WINDOW)
        .args(["ptck.py", "sbv-update"])
        .current_dir(&root)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    let macro_ok = Command::new("python")
        .creation_flags(CREATE_NO_WINDOW)
        .args(["ptck.py", "fetch-macro"])
        .current_dir(&root)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    format!(
        "🔄 [Minh Chứng - Đồng bộ CLI `ptck.py` hoàn tất trong {:.2?}]\n• Lãi suất Liên NH (`sbv-update`): {}\n• Vĩ mô & Tỷ giá (`fetch-macro`): {}",
        t0.elapsed(),
        if sbv_ok { "✅ OK" } else { "⚠️ Dùng bản cache" },
        if macro_ok { "✅ OK" } else { "⚠️ Dùng bản cache" }
    )
}

fn format_vnd_ty(val: f64) -> String {
    let ty = val / 1_000_000_000.0;
    if ty.abs() >= 1000.0 {
        let nghin = (ty / 1000.0).floor() as i64;
        let rem = (ty.abs() % 1000.0).round() as i64;
        format!("{}.{:03} tỷ", nghin, rem)
    } else {
        format!("{:.1} tỷ", ty)
    }
}

fn format_int_dots(n: i64) -> String {
    let s = n.abs().to_string();
    let mut out = String::new();
    let len = s.len();
    for (i, ch) in s.chars().enumerate() {
        if i > 0 && (len - i) % 3 == 0 {
            out.push('.');
        }
        out.push(ch);
    }
    if n < 0 {
        format!("-{}", out)
    } else {
        out
    }
}

pub fn build_stock_report(raw_symbol: &str) -> Result<String, String> {
    let sym = raw_symbol
        .trim()
        .to_uppercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>();
    if sym.is_empty() {
        return Err("Mã cổ phiếu không hợp lệ.".to_string());
    }

    let screener_path = resolve_db_path("screener_cache.db");
    let facts_path = resolve_db_path("financial_facts.db");
    let db_scr = Db::open(&screener_path)?;
    let db_fin = Db::open(&facts_path)?;

    let sql_ohlcv = format!(
        "SELECT date, close, volume, high, low FROM daily_ohlcv WHERE symbol = '{}' ORDER BY date DESC LIMIT 20;",
        sym
    );
    let c_sql = CString::new(sql_ohlcv).unwrap();
    let mut stmt: *mut sqlite3_stmt = ptr::null_mut();
    let mut dates = Vec::new();
    let mut closes = Vec::new();
    let mut vols = Vec::new();

    unsafe {
        if sqlite3_prepare_v2(db_scr.handle, c_sql.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
            while sqlite3_step(stmt) == SQLITE_ROW {
                dates.push(Db::get_col_str(stmt, 0));
                closes.push(Db::get_col_f64(stmt, 1));
                vols.push(Db::get_col_f64(stmt, 2));
            }
            sqlite3_finalize(stmt);
        }
    }

    if closes.is_empty() {
        return Ok(format!(
            "⚠️ [Minh Chứng] Không tìm thấy dữ liệu giá cho mã `{}` trong kho `screener_cache.db`.",
            sym
        ));
    }

    let latest_date = &dates[0];
    let close = closes[0];
    let prev_close = if closes.len() > 1 { closes[1] } else { close };
    let diff = close - prev_close;
    let pct = if prev_close > 0.0 {
        (diff / prev_close) * 100.0
    } else {
        0.0
    };
    let vol = vols[0];
    let adv20: f64 = vols.iter().sum::<f64>() / (vols.len() as f64);
    let ma20: f64 = closes.iter().sum::<f64>() / (closes.len() as f64);
    let vol_ratio = if adv20 > 0.0 { vol / adv20 } else { 1.0 };

    let sql_period = format!(
        "SELECT period, entity_type FROM financial_facts WHERE symbol = '{}' ORDER BY period DESC LIMIT 1;",
        sym
    );
    let c_sql_p = CString::new(sql_period).unwrap();
    let mut latest_period = String::new();
    let mut entity_type = String::new();
    unsafe {
        if sqlite3_prepare_v2(db_fin.handle, c_sql_p.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
            if sqlite3_step(stmt) == SQLITE_ROW {
                latest_period = Db::get_col_str(stmt, 0);
                entity_type = Db::get_col_str(stmt, 1);
            }
            sqlite3_finalize(stmt);
        }
    }

    let mut shares_out = 0.0;
    let mut eps = 0.0;
    let mut bvps = 0.0;
    let mut roe = 0.0;
    let mut roa = 0.0;
    let mut total_assets = 0.0;
    let mut total_equity = 0.0;
    let mut npl = 0.0;
    let mut llr = 0.0;
    let mut nim = 0.0;
    let mut ldr = 0.0;
    let mut credit_growth = 0.0;
    let mut customer_loans = 0.0;
    let mut customer_deposits = 0.0;
    let mut cir = 0.0;
    let mut revenue = 0.0;
    let mut net_income = 0.0;
    let mut gross_profit = 0.0;

    if !latest_period.is_empty() {
        let sql_facts = format!(
            "SELECT metric, value FROM financial_facts WHERE symbol = '{}' AND period = '{}';",
            sym, latest_period
        );
        let c_sql_f = CString::new(sql_facts).unwrap();
        unsafe {
            if sqlite3_prepare_v2(db_fin.handle, c_sql_f.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
                while sqlite3_step(stmt) == SQLITE_ROW {
                    let m = Db::get_col_str(stmt, 0);
                    let v = Db::get_col_f64(stmt, 1);
                    match m.as_str() {
                        "SHARES_OUT" => shares_out = v,
                        "EPS" => eps = v,
                        "BOOK_VALUE_PS" => bvps = v,
                        "ROE" => roe = v,
                        "ROA" => roa = v,
                        "TOTAL_ASSETS" => total_assets = v,
                        "TOTAL_EQUITY" => total_equity = v,
                        "NPL_RATIO" | "BANK_NPL_RATIO" => npl = v,
                        "LLR" | "BANK_LLR" => llr = v,
                        "NIM" | "BANK_NIM" => nim = v,
                        "LDR" | "BANK_LDR" => ldr = v,
                        "CREDIT_GROWTH" | "BANK_CREDIT_GROWTH" => credit_growth = v,
                        "CUSTOMER_LOANS" => customer_loans = v,
                        "CUSTOMER_DEPOSITS" => customer_deposits = v,
                        "COST_TO_INCOME" => cir = v,
                        "REVENUE" | "TOI" => revenue = v,
                        "NET_INCOME" | "NET_PROFIT" => net_income = v,
                        "GROSS_PROFIT" => gross_profit = v,
                        _ => {}
                    }
                }
                sqlite3_finalize(stmt);
            }
        }
    }

    let mut out = String::new();
    let sign = if diff >= 0.0 { "+" } else { "" };
    let price_str = if sym == "VNINDEX" || close < 2500.0 {
        format!("{:.2}", close)
    } else {
        format_int_dots(close.round() as i64)
    };
    let ma20_str = if sym == "VNINDEX" || ma20 < 2500.0 {
        format!("{:.2}", ma20)
    } else {
        format_int_dots(ma20.round() as i64)
    };

    out.push_str(&format!(
        "📊 [Minh Chứng] HỒ SƠ ĐỊNH LƯỢNG & BCTC: {} (Phiên {})\n",
        sym, latest_date
    ));
    out.push_str(&format!(
        "• Giá đóng cửa: {} ({}{:.2}% | {}{:.0}) | MA20: {}\n",
        price_str, sign, pct, sign, diff, ma20_str
    ));
    out.push_str(&format!(
        "• Thanh khoản: {:.2}M cp ({:.2}x ADV20 = {:.2}M)\n",
        vol / 1_000_000.0,
        vol_ratio,
        adv20 / 1_000_000.0
    ));

    if !latest_period.is_empty() {
        let pe = if eps > 0.0 { close / eps } else { 0.0 };
        let pb = if bvps > 0.0 { close / bvps } else { 0.0 };
        let mcap_ty = if shares_out > 0.0 {
            (close * shares_out) / 1_000_000_000.0
        } else {
            0.0
        };

        out.push_str(&format!(
            "── ĐỊNH GIÁ & QUY MÔ (Kỳ {}) ──\n",
            latest_period
        ));
        if shares_out > 0.0 {
            out.push_str(&format!(
                "• CP lưu hành: {} cp | Vốn hóa: {:.0} tỷ\n",
                format_int_dots(shares_out.round() as i64),
                mcap_ty
            ));
        }
        out.push_str(&format!(
            "• P/E: {:.2}x (EPS: {}đ) | P/B: {:.2}x (BVPS: {}đ)\n",
            pe,
            format_int_dots(eps.round() as i64),
            pb,
            format_int_dots(bvps.round() as i64)
        ));
        out.push_str(&format!(
            "• Hiệu suất: ROE {:.1}% | ROA {:.2}%\n",
            roe * 100.0,
            roa * 100.0
        ));
        if total_assets > 0.0 || total_equity > 0.0 {
            out.push_str(&format!(
                "• Tổng Tài sản: {} | VCSH: {}\n",
                format_vnd_ty(total_assets),
                format_vnd_ty(total_equity)
            ));
        }

        if entity_type == "BANK" || npl > 0.0 || customer_loans > 0.0 {
            out.push_str("── CHỈ SỐ CHUYÊN BIỆT NGÂN HÀNG ──\n");
            out.push_str(&format!(
                "• Nợ xấu (NPL): {:.2}% | Bao phủ nợ xấu (LLR): {:.1}%\n",
                npl * 100.0,
                llr * 100.0
            ));
            out.push_str(&format!(
                "• NIM: {:.2}% | LDR: {:.1}% | Tăng trưởng TD: {:.1}% | CIR: {:.1}%\n",
                nim * 100.0,
                ldr * 100.0,
                credit_growth * 100.0,
                cir * 100.0
            ));
            if customer_loans > 0.0 {
                out.push_str(&format!(
                    "• Dư nợ cho vay: {} | Huy động KH: {}\n",
                    format_vnd_ty(customer_loans),
                    format_vnd_ty(customer_deposits)
                ));
            }
        } else if revenue > 0.0 || net_income != 0.0 {
            out.push_str("── KẾT QUẢ KINH DOANH QUÝ GẦN NHẤT ──\n");
            let gm = if revenue > 0.0 && gross_profit > 0.0 {
                (gross_profit / revenue) * 100.0
            } else {
                0.0
            };
            out.push_str(&format!(
                "• Doanh thu: {} | LNST: {} | Biên gộp: {:.1}%\n",
                format_vnd_ty(revenue),
                format_vnd_ty(net_income),
                gm
            ));
        }
    }

    Ok(out.trim_end().to_string())
}

pub fn build_market_brief(mode_title: &str) -> Result<String, String> {
    let screener_path = resolve_db_path("screener_cache.db");
    let facts_path = resolve_db_path("financial_facts.db");
    let db_scr = Db::open(&screener_path)?;
    let db_fin = Db::open(&facts_path)?;

    let mut out = String::new();
    out.push_str(&format!("📈 [Minh Chứng] {}\n", mode_title));

    let mut stmt: *mut sqlite3_stmt = ptr::null_mut();
    let sql_vni = CString::new(
        "SELECT date, close, volume FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date DESC LIMIT 2;",
    )
    .unwrap();
    let mut vni_date = String::new();
    let mut vni_c0 = 0.0;
    let mut vni_c1 = 0.0;
    let mut vni_vol = 0.0;
    unsafe {
        if sqlite3_prepare_v2(db_scr.handle, sql_vni.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
            if sqlite3_step(stmt) == SQLITE_ROW {
                vni_date = Db::get_col_str(stmt, 0);
                vni_c0 = Db::get_col_f64(stmt, 1);
                vni_vol = Db::get_col_f64(stmt, 2);
            }
            if sqlite3_step(stmt) == SQLITE_ROW {
                vni_c1 = Db::get_col_f64(stmt, 1);
            }
            sqlite3_finalize(stmt);
        }
    }
    let vni_diff = vni_c0 - vni_c1;
    let vni_pct = if vni_c1 > 0.0 {
        (vni_diff / vni_c1) * 100.0
    } else {
        0.0
    };
    let sign = if vni_diff >= 0.0 { "+" } else { "" };

    let sql_reg = CString::new(
        "SELECT status, regime_score, breadth_pct FROM regime_history ORDER BY date DESC LIMIT 1;",
    )
    .unwrap();
    let mut reg_status = String::new();
    let mut reg_score = 0.0;
    let mut breadth = 0.0;
    unsafe {
        if sqlite3_prepare_v2(db_scr.handle, sql_reg.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
            if sqlite3_step(stmt) == SQLITE_ROW {
                reg_status = Db::get_col_str(stmt, 0);
                reg_score = Db::get_col_f64(stmt, 1);
                breadth = Db::get_col_f64(stmt, 2);
            }
            sqlite3_finalize(stmt);
        }
    }

    out.push_str(&format!(
        "• VN-INDEX ({}): {:.2} ({}{:.2} điểm | {}{:.2}%) | KL: {:.1}M cp\n",
        vni_date,
        vni_c0,
        sign,
        vni_diff,
        sign,
        vni_pct,
        vni_vol / 1_000_000.0
    ));
    out.push_str(&format!(
        "• Market Regime: {} (Score: {:.2} | Độ rộng >MA20: {:.1}%)\n",
        reg_status, reg_score, breadth
    ));

    let mut macro_items = Vec::new();
    for &var in &["FED_TARGET_RATE", "US10Y", "DXY", "INTERBANK_ON", "VGB10Y"] {
        let sql_m = CString::new(format!(
            "SELECT value FROM macro_history WHERE variable='{}' ORDER BY date DESC LIMIT 1;",
            var
        ))
        .unwrap();
        unsafe {
            if sqlite3_prepare_v2(db_scr.handle, sql_m.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
                if sqlite3_step(stmt) == SQLITE_ROW {
                    let val = Db::get_col_f64(stmt, 0);
                    let label = match var {
                        "FED_TARGET_RATE" => format!("Fed {:.2}%", val),
                        "US10Y" => format!("US10Y {:.2}%", val),
                        "DXY" => format!("DXY {:.2}", val),
                        "INTERBANK_ON" => format!("Liên NH O/N {:.2}%", val),
                        "VGB10Y" => format!("TPCP 10Y {:.2}%", val),
                        _ => format!("{} {:.2}", var, val),
                    };
                    macro_items.push(label);
                }
                sqlite3_finalize(stmt);
            }
        }
    }
    if !macro_items.is_empty() {
        out.push_str(&format!("• Vĩ mô: {}\n", macro_items.join(" | ")));
    }

    out.push_str("── DANH MỤC THEO DÕI TRỌNG ĐIỂM ──\n");
    for &sym in &["SHB", "FPT", "PNJ", "MBB", "TCB", "VCB", "ACB", "HPG"] {
        let sql_s = CString::new(format!(
            "SELECT close, volume FROM daily_ohlcv WHERE symbol='{}' ORDER BY date DESC LIMIT 2;",
            sym
        ))
        .unwrap();
        let mut c0 = 0.0;
        let mut c1 = 0.0;
        let mut v0 = 0.0;
        unsafe {
            if sqlite3_prepare_v2(db_scr.handle, sql_s.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
                if sqlite3_step(stmt) == SQLITE_ROW {
                    c0 = Db::get_col_f64(stmt, 0);
                    v0 = Db::get_col_f64(stmt, 1);
                }
                if sqlite3_step(stmt) == SQLITE_ROW {
                    c1 = Db::get_col_f64(stmt, 0);
                }
                sqlite3_finalize(stmt);
            }
        }
        if c0 <= 0.0 {
            continue;
        }
        let pct_s = if c1 > 0.0 { ((c0 - c1) / c1) * 100.0 } else { 0.0 };
        let sign_s = if pct_s >= 0.0 { "+" } else { "" };

        let sql_f = CString::new(format!(
            "SELECT metric, value FROM financial_facts WHERE symbol='{}' AND period=(SELECT period FROM financial_facts WHERE symbol='{}' ORDER BY period DESC LIMIT 1) AND metric IN ('EPS','BOOK_VALUE_PS','ROE');",
            sym, sym
        ))
        .unwrap();
        let mut eps = 0.0;
        let mut bvps = 0.0;
        let mut roe = 0.0;
        unsafe {
            if sqlite3_prepare_v2(db_fin.handle, sql_f.as_ptr(), -1, &mut stmt, ptr::null_mut()) == SQLITE_OK {
                while sqlite3_step(stmt) == SQLITE_ROW {
                    let m = Db::get_col_str(stmt, 0);
                    let v = Db::get_col_f64(stmt, 1);
                    match m.as_str() {
                        "EPS" => eps = v,
                        "BOOK_VALUE_PS" => bvps = v,
                        "ROE" => roe = v,
                        _ => {}
                    }
                }
                sqlite3_finalize(stmt);
            }
        }
        let pe = if eps > 0.0 { c0 / eps } else { 0.0 };
        let pb = if bvps > 0.0 { c0 / bvps } else { 0.0 };
        out.push_str(&format!(
            "• {}: {} ({}{:.2}% | {:.1}M cp) | P/E {:.1}x | P/B {:.2}x | ROE {:.1}%\n",
            sym,
            format_int_dots(c0.round() as i64),
            sign_s,
            pct_s,
            v0 / 1_000_000.0,
            pe,
            pb,
            roe * 100.0
        ));
    }

    Ok(out.trim_end().to_string())
}

// ============================================================================
// 5. NAMESPACE COMMAND ROUTER FOR ZALO WATCH DAEMON ("Minh Chứng ..." / "/mc ...")
// ============================================================================
fn parse_wake_command(msg: &str) -> Option<String> {
    let trimmed = msg.trim();
    if trimmed.starts_with('[')
        || trimmed.starts_with("📊")
        || trimmed.starts_with("📈")
        || trimmed.starts_with("⚠️")
        || trimmed.starts_with("🤖")
        || trimmed.starts_with("🔄")
    {
        return None;
    }

    let lower = trimmed.to_lowercase();
    if lower.starts_with("minh tâm") || lower.starts_with("minh tam") || lower.starts_with("/mt") {
        return None;
    }

    if lower.starts_with("minh chứng") {
        return Some(trimmed["minh chứng".len()..].trim().to_string());
    }
    if lower.starts_with("minh chung") {
        return Some(trimmed["minh chung".len()..].trim().to_string());
    }
    if lower.starts_with("/mc ") || lower == "/mc" {
        return Some(trimmed["/mc".len()..].trim().to_string());
    }

    None
}

fn handle_zalo_command(cmd_body: &str) -> String {
    let clean = cmd_body.trim();
    let lower = clean.to_lowercase();

    if clean.is_empty()
        || lower == "báo cáo"
        || lower == "bao cao"
        || lower == "vĩ mô"
        || lower == "vi mo"
        || lower == "macro"
        || lower == "brief"
        || lower == "thị trường"
    {
        return build_market_brief("BÁO CÁO NHANH VĨ MÔ & DANH MỤC")
            .unwrap_or_else(|e| format!("⚠️ [Minh Chứng] Lỗi truy vấn: {}", e));
    }

    if lower == "sync" || lower == "cập nhật" || lower == "cap nhat" {
        let sync_res = run_cli_sync();
        let brief = build_market_brief("BÁO CÁO SAU ĐỒNG BỘ CLI")
            .unwrap_or_else(|e| format!("⚠️ [Minh Chứng] Lỗi truy vấn: {}", e));
        return format!("{}\n\n{}", sync_res, brief);
    }

    if lower == "help" || lower == "giúp" || lower == "hướng dẫn" {
        return "🤖 [Minh Chứng - Trợ lý Phân tích Định lượng & BCTC (<30ms)]\n\
                • Gõ `Minh Chứng <MÃ_CP>` hoặc `/mc <MÃ_CP>` (VD: `/mc SHB`, `/mc FPT`, `/mc PNJ`, `/mc MBB`, `/mc TCB`): Tra cứu tức thì Giá, Thanh khoản, P/E, P/B, ROE, NPL, NIM, CASA/LDR.\n\
                • Gõ `Minh Chứng báo cáo` hoặc `/mc brief`: Điểm tin VN-Index, Market Regime, Vĩ mô (Fed, DXY, US10Y, Liên NH) & Watchlist.\n\
                • Gõ `/mc sync`: Kích hoạt CLI `ptck.py` cập nhật lãi suất Liên NH & Vĩ mô mới nhất."
            .to_string();
    }

    let first_tok = clean.split_whitespace().next().unwrap_or("");
    build_stock_report(first_tok)
        .unwrap_or_else(|e| format!("⚠️ [Minh Chứng] Lỗi truy vấn mã {}: {}", first_tok, e))
}

fn main() {
    let t0 = Instant::now();
    let args: Vec<String> = env::args().collect();
    let cmd = if args.len() > 1 {
        args[1].as_str()
    } else {
        "brief"
    };

    match cmd {
        "query" | "q" => {
            let sym = if args.len() > 2 { &args[2] } else { "SHB" };
            let send_flag = args.iter().any(|a| a == "--send");
            match build_stock_report(sym) {
                Ok(report) => {
                    let t_sql = t0.elapsed();
                    println!("{}\n⏱️ Thời gian truy vấn SQL: {:.2?}", report, t_sql);
                    if send_flag {
                        let t_send = Instant::now();
                        let _guard = ZaloMutexGuard::acquire(2500);
                        if let Ok(mut cdp) = CdpClient::connect_zalo() {
                            let _ = cdp.send_message_to_my_documents(&report);
                            println!(
                                "📨 Đã gửi báo cáo {} vào Zalo 'My Documents' (CDP: {:.2?} | Tổng: {:.2?}).",
                                sym.to_uppercase(),
                                t_send.elapsed(),
                                t0.elapsed()
                            );
                        }
                    }
                }
                Err(e) => eprintln!("❌ Lỗi: {}", e),
            }
        }
        "brief" | "summary" => {
            let do_sync = args.iter().any(|a| a == "--sync");
            if do_sync {
                println!("{}", run_cli_sync());
            }
            let title = if cmd == "brief" {
                "ĐIỂM TIN TRƯỚC GIỜ MỞ CỬA (PRE-MARKET BRIEF 08:45)"
            } else {
                "TỔNG KẾT ĐÓNG CỬA PHIÊN ATC (MARKET SUMMARY 15:15)"
            };
            let no_send = args.iter().any(|a| a == "--no-send");
            match build_market_brief(title) {
                Ok(report) => {
                    println!("{}\n⏱️ Thời gian tổng hợp: {:.2?}", report, t0.elapsed());
                    if !no_send {
                        let t_send = Instant::now();
                        {
                            let _guard = ZaloMutexGuard::acquire(2500);
                            if let Ok(mut cdp) = CdpClient::connect_zalo() {
                                if let Ok(res) = cdp.send_message_to_my_documents(&report) {
                                    println!(
                                        "📨 Đã đồng bộ Zalo 'My Documents' ({}) trong {:.2?}",
                                        res,
                                        t_send.elapsed()
                                    );
                                }
                            }
                        }
                    }
                }
                Err(e) => eprintln!("❌ Lỗi: {}", e),
            }
        }
        "sync" => {
            let msg = run_cli_sync();
            println!("{}", msg);
            if args.iter().any(|a| a == "--send") {
                let _guard = ZaloMutexGuard::acquire(2500);
                if let Ok(mut cdp) = CdpClient::connect_zalo() {
                    let _ = cdp.send_message_to_my_documents(&msg);
                }
            }
        }
        "read" => {
            let _guard = ZaloMutexGuard::acquire(2000);
            match CdpClient::connect_zalo() {
                Ok(mut cdp) => match cdp.read_latest_message() {
                    Ok(msg) => {
                        println!("📩 [Zalo My Documents]: {}\n⏱️ {:.2?}", msg, t0.elapsed());
                    }
                    Err(e) => eprintln!("❌ Lỗi đọc Zalo: {}", e),
                },
                Err(e) => eprintln!("❌ {}", e),
            }
        }
        "send" => {
            if args.len() < 3 {
                eprintln!("Cách dùng: minh_chung.exe send \"<nội dung>\"");
                return;
            }
            let text = args[2..].join(" ");
            let _guard = ZaloMutexGuard::acquire(2500);
            match CdpClient::connect_zalo() {
                Ok(mut cdp) => match cdp.send_message_to_my_documents(&text) {
                    Ok(res) => println!("✅ Đã gửi Zalo ({}): {:.2?}", res, t0.elapsed()),
                    Err(e) => eprintln!("❌ Lỗi gửi Zalo: {}", e),
                },
                Err(e) => eprintln!("❌ {}", e),
            }
        }
        "boot" => match CdpClient::ensure_zalo_cdp() {
            Ok(_) => {
                ensure_watch_daemons_running();
                println!("✅ Zalo CDP (58721) & Daemons (minh_chung + minh_tam) sẵn sàng.");
            }
            Err(e) => eprintln!("❌ {}", e),
        },
        "watch" => {
            let instance_name = CString::new("Local\\MinhChung_Daemon_Instance_Mutex").unwrap();
            let _inst_handle = unsafe { CreateMutexA(ptr::null_mut(), 0, instance_name.as_ptr()) };
            if unsafe { GetLastError() } == 183 {
                eprintln!("⚠️ [Minh Chứng] Daemon đã đang chạy trong một tiến trình khác.");
                return;
            }

            println!("🚀 [Minh Chứng Daemon] Cached WS Path + 100ms Loop trên Zalo PC...");
            println!("💡 Tiền tố nhận diện: `Minh Chứng <MÃ_CP>` hoặc `/mc <MÃ_CP>`");
            println!("🔒 Đồng bộ Windows Named Mutex (<5ms): 'Local\\ZaloPC_Bridge_Mutex'");
            let _ = CdpClient::ensure_zalo_cdp();
            let mut cached_ws_path = CdpClient::find_zalo_ws_path(58721).unwrap_or_default();
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
                                    println!("📌 Tin nhắn hiện tại: {}", last_msg);
                                } else if let Some(cmd_body) = parse_wake_command(&last_msg) {
                                    let t_cmd = Instant::now();
                                    println!("⚡ Phát hiện lệnh Minh Chứng: '{}'", cmd_body);
                                    let reply = handle_zalo_command(&cmd_body);
                                    let _ = cdp.send_message_to_my_documents(&reply);
                                    last_msg = reply;
                                    println!(
                                        "✅ Đã phản hồi trên Zalo trong {:.2?}",
                                        t_cmd.elapsed()
                                    );
                                }
                            }
                        }
                    } else {
                        cached_ws_path.clear();
                    }
                    // CdpClient and ZaloMutexGuard drop here (~2.5ms total hold time!)
                }
                thread::sleep(Duration::from_millis(100));
            }
        }
        other => match build_stock_report(other) {
            Ok(report) => println!("{}\n⏱️ Thời gian truy vấn: {:.2?}", report, t0.elapsed()),
            Err(e) => eprintln!("❌ Lỗi: {}", e),
        },
    }
}
