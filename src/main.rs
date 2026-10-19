use std::env;
use std::ffi::CString;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::os::raw::{c_char, c_int, c_void};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::ptr;
use std::thread;
use std::time::{Duration, Instant};

use rusqlite::{params, Connection, OpenFlags};

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

pub const TARGET_CONV_NAME: &str = "Trạm Số Liệu - Minh Chứng";

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
// 2. RUSQLITE ENGINE (bundled + FTS5 Full-Text Search)
// ============================================================================
pub struct Db {
    pub conn: Connection,
}

impl Db {
    pub fn open_readonly(path: &str) -> Result<Self, String> {
        Connection::open_with_flags(
            path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map(|conn| Db { conn })
        .map_err(|e| format!("Lỗi mở CSDL {}: {}", path, e))
    }

    pub fn open(path: &str) -> Result<Self, String> {
        Connection::open(path)
            .map(|conn| Db { conn })
            .map_err(|e| format!("Lỗi mở CSDL {}: {}", path, e))
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
        for _ in 0..5 {
            if let Ok(ws_path) = Self::find_zalo_ws_path(58721) {
                if let Ok(client) = Self::connect(58721, &ws_path) {
                    return Ok(client);
                }
            }
            thread::sleep(Duration::from_millis(200));
        }
        Self::ensure_zalo_cdp()?;
        for _ in 0..10 {
            if let Ok(ws_path) = Self::find_zalo_ws_path(58721) {
                if let Ok(client) = Self::connect(58721, &ws_path) {
                    return Ok(client);
                }
            }
            thread::sleep(Duration::from_millis(500));
        }
        Err("Không thể kết nối đến Zalo PC qua cổng CDP 58721".to_string())
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
        if let Ok(addr) = "127.0.0.1:58721".parse() {
            if TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok() {
                return Ok(());
            }
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
        let js = format!(
            r#"
        (() => {{
            const targetName = "{}";
            const targetLow = targetName.toLowerCase();
            const h = document.querySelector('.header-title, [data-id="header-title"], .chat-title, .title');
            const hText = (h ? h.innerText : '').replace(/\u00a0/g, ' ').toLowerCase();

            const isTarget = hText.includes(targetLow) || (hText.includes('số liệu') && hText.includes('minh chứng'));
            if (!isTarget) {{
                const items = document.querySelectorAll('.conv-item');
                let targetItem = null;
                for (const item of items) {{
                    const t = (item.innerText || '').replace(/\u00a0/g, ' ').toLowerCase();
                    if (t.includes(targetLow) || (t.includes('số liệu') && t.includes('minh chứng'))) {{
                        targetItem = item;
                        break;
                    }}
                }}
                if (!targetItem) return '';
                const hasUnread = !!targetItem.querySelector('[class*="unread"], [class*="badge"], [class*="count"]');
                if (hasUnread) {{
                    targetItem.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true, cancelable: true}}));
                    targetItem.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true, cancelable: true}}));
                    targetItem.click();
                }}
                return '';
            }}

            const chatView = document.querySelector('#messageView');
            if (!chatView) return '';
            const bubbles = chatView.querySelectorAll('.card--text, [data-id="div_SentMsg_Text"], [data-id="div_ReceivedMsg_Text"]');
            for (let i = bubbles.length - 1; i >= 0; i--) {{
                const t = (bubbles[i].innerText || '').trim();
                if (t.length > 0 && !t.startsWith(':-') && !t.startsWith('/-')) {{
                    return t;
                }}
            }}
            return '';
        }})()
        "#,
            TARGET_CONV_NAME
        );
        self.eval_js(&js)
    }

    pub fn send_message_to_my_documents(&mut self, text: &str) -> Result<String, String> {
        let check_js = format!(
            r#"
            (() => {{
                const targetLow = "{}".toLowerCase();
                const h = document.querySelector('.header-title, [data-id="header-title"], .chat-title, .title');
                const hText = (h ? h.innerText : '').replace(/\u00a0/g, ' ').toLowerCase();
                if (hText.includes(targetLow) || (hText.includes('số liệu') && hText.includes('minh chứng'))) return 'IS_TARGET';

                const items = document.querySelectorAll('.conv-item');
                for (const item of items) {{
                    const t = (item.innerText || '').replace(/\u00a0/g, ' ').toLowerCase();
                    if (t.includes(targetLow) || (t.includes('số liệu') && t.includes('minh chứng'))) {{
                        item.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true, cancelable: true}}));
                        item.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true, cancelable: true}}));
                        item.click();
                        return 'SWITCHED';
                    }}
                }}
                return 'NOT_FOUND';
            }})()
            "#,
            TARGET_CONV_NAME
        );
        let check = self.eval_js(&check_js).unwrap_or_default();
        if check != "IS_TARGET" && check != "SWITCHED" {
            return Err(format!("Chưa mở đúng nhóm '{}'. Đã hủy gửi để bảo đảm tuyệt đối không nhầm nhóm.", TARGET_CONV_NAME));
        }
        if check == "SWITCHED" {
            thread::sleep(Duration::from_millis(150));
        }

        let js_text = Self::json_escape(text);
        let js = format!(
            r#"
            (() => {{
                const input = document.querySelector('#richInput');
                if (!input) return 'ERR_NO_RICHINPUT';
                input.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, "{}");
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
    let raw_trimmed = raw_symbol.trim();
    if raw_trimmed.chars().any(|c| !c.is_ascii()) {
        return Ok(format!(
            "🤖 [Minh Chứng] Chưa nhận diện được lệnh hoặc mã cổ phiếu `{}`.\n\
             • Tra cứu cổ phiếu: `/mc <MÃ>` (VD: `/mc HPG`, `/mc TCB`, `/mc FPT`, `/mc PNJ`, `/mc SHB`)\n\
             • Điểm tin phiên: `/mc tổng kết` hoặc `/mc brief`",
            raw_trimmed
        ));
    }

    let sym = raw_trimmed
        .to_uppercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>();
    if sym.is_empty() {
        return Err("Mã cổ phiếu không hợp lệ.".to_string());
    }

    let screener_path = resolve_db_path("screener_cache.db");
    let facts_path = resolve_db_path("financial_facts.db");
    let db_scr = Db::open_readonly(&screener_path)?;
    let db_fin = Db::open_readonly(&facts_path)?;

    let mut stmt = db_scr
        .conn
        .prepare("SELECT date, close, volume FROM daily_ohlcv WHERE symbol = ?1 ORDER BY date DESC LIMIT 20;")
        .map_err(|e| e.to_string())?;

    let mut rows = stmt.query(params![sym]).map_err(|e| e.to_string())?;
    let mut dates = Vec::new();
    let mut closes = Vec::new();
    let mut vols = Vec::new();

    while let Some(row) = rows.next().map_err(|e| e.to_string())? {
        dates.push(row.get::<_, String>(0).unwrap_or_default());
        closes.push(row.get::<_, f64>(1).unwrap_or(0.0));
        vols.push(row.get::<_, f64>(2).unwrap_or(0.0));
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

    let mut latest_period = String::new();
    let mut entity_type = String::new();
    if let Ok(mut p_stmt) = db_fin.conn.prepare(
        "SELECT period, entity_type FROM financial_facts WHERE symbol = ?1 ORDER BY period DESC LIMIT 1;",
    ) {
        if let Ok(mut p_rows) = p_stmt.query(params![sym]) {
            if let Ok(Some(row)) = p_rows.next() {
                latest_period = row.get::<_, String>(0).unwrap_or_default();
                entity_type = row.get::<_, String>(1).unwrap_or_default();
            }
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
        if let Ok(mut f_stmt) = db_fin.conn.prepare(
            "SELECT metric, value FROM financial_facts WHERE symbol = ?1 AND period = ?2;",
        ) {
            if let Ok(mut f_rows) = f_stmt.query(params![sym, latest_period]) {
                while let Ok(Some(row)) = f_rows.next() {
                    let m: String = row.get(0).unwrap_or_default();
                    let v: f64 = row.get(1).unwrap_or(0.0);
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

        // ── GÓC NHÌN ĐỊNH LƯỢNG & RỦI RO (MINH CHỨNG) ──
        out.push_str("── GÓC NHÌN ĐỊNH LƯỢNG (MINH CHỨNG) ──\n");
        let tech_signal = if close >= ma20 * 1.02 {
            format!("Xu hướng Tăng ngắn hạn (+{:.1}% so với MA20)", ((close - ma20) / ma20) * 100.0)
        } else if close <= ma20 * 0.98 {
            format!("Xu hướng Điều chỉnh ngắn hạn ({:.1}% so với MA20)", ((close - ma20) / ma20) * 100.0)
        } else {
            "Vận động Tích lũy cân bằng quanh MA20".to_string()
        };

        let vol_signal = if vol_ratio >= 1.25 {
            format!("Dòng tiền vào chủ động ({:.2}x ADV20)", vol_ratio)
        } else if vol_ratio <= 0.75 {
            format!("Cung bán suy kiệt/Cạn thanh khoản ({:.2}x ADV20)", vol_ratio)
        } else {
            format!("Thanh khoản ở mức trung bình ({:.2}x ADV20)", vol_ratio)
        };
        out.push_str(&format!("• Kỹ thuật: {} | {}\n", tech_signal, vol_signal));

        let fund_signal = if pe > 0.0 && pe < 10.0 {
            format!("P/E {:.1}x (Định giá hấp dẫn)", pe)
        } else if pe >= 18.0 {
            format!("P/E {:.1}x (Định giá cao, kỳ vọng tăng trưởng lớn)", pe)
        } else if pe > 0.0 {
            format!("P/E {:.1}x (Định giá hợp lý)", pe)
        } else {
            "P/E: N/A".to_string()
        };

        let qual_signal = if entity_type == "BANK" {
            if npl > 0.0 && npl < 0.015 {
                format!("Chất lượng nợ tốt (NPL {:.2}%)", npl * 100.0)
            } else if npl >= 0.02 {
                format!("Cần giám sát nợ xấu (NPL {:.2}%)", npl * 100.0)
            } else {
                format!("NPL: {:.2}%", npl * 100.0)
            }
        } else {
            if roe >= 0.18 {
                format!("Hiệu quả sinh lời cao (ROE {:.1}%)", roe * 100.0)
            } else if roe > 0.0 && roe < 0.10 {
                format!("Biên sinh lời khiêm tốn (ROE {:.1}%)", roe * 100.0)
            } else {
                format!("ROE: {:.1}%", roe * 100.0)
            }
        };
        out.push_str(&format!("• Cơ bản: {} | {}\n", fund_signal, qual_signal));

        let advice = if close > ma20 && vol_ratio <= 0.85 {
            "Khuyến nghị: Vận động test cung lành mạnh; ưu tiên nắm giữ/theo dõi tích lũy."
        } else if close < ma20 && vol_ratio > 1.25 {
            "Cảnh báo rủi ro: Áp lực phân phối ngắn hạn; hạn chế mua đuổi, ưu tiên quản trị tỷ trọng."
        } else if close > ma20 {
            "Khuyến nghị: Xu hướng ổn định; duy trì vị thế theo ngưỡng hỗ trợ MA20."
        } else {
            "Khuyến nghị: Chờ tín hiệu dòng tiền xác nhận vượt lại MA20 trước khi giải ngân mới."
        };
        out.push_str(&format!("• {}\n", advice));
    }

    Ok(out.trim_end().to_string())
}

pub fn build_market_brief(mode_title: &str) -> Result<String, String> {
    let screener_path = resolve_db_path("screener_cache.db");
    let db_scr = Db::open(&screener_path)?;

    let mut out = String::new();
    out.push_str(&format!("📈 [Minh Chứng] {}\n", mode_title));

    // 1. VN-INDEX
    let mut vni_date = String::new();
    let mut vni_c0 = 0.0;
    let mut vni_c1 = 0.0;
    let mut vni_vol = 0.0;

    if let Ok(mut stmt) = db_scr.conn.prepare(
        "SELECT date, close, volume FROM daily_ohlcv WHERE symbol='VNINDEX' ORDER BY date DESC LIMIT 2;",
    ) {
        if let Ok(mut rows) = stmt.query([]) {
            if let Ok(Some(row)) = rows.next() {
                vni_date = row.get(0).unwrap_or_default();
                vni_c0 = row.get(1).unwrap_or(0.0);
                vni_vol = row.get(2).unwrap_or(0.0);
            }
            if let Ok(Some(row)) = rows.next() {
                vni_c1 = row.get(1).unwrap_or(0.0);
            }
        }
    }

    let vni_diff = vni_c0 - vni_c1;
    let vni_pct = if vni_c1 > 0.0 {
        (vni_diff / vni_c1) * 100.0
    } else {
        0.0
    };
    let sign = if vni_diff >= 0.0 { "+" } else { "" };

    // 2. Market Regime & Governor Decision
    let mut reg_status = String::from("CRISIS");
    let mut reg_score = 0.0;
    let mut breadth = 0.0;
    if let Ok(mut stmt) = db_scr.conn.prepare(
        "SELECT status, regime_score, breadth_pct FROM regime_history ORDER BY date DESC LIMIT 1;",
    ) {
        if let Ok(mut rows) = stmt.query([]) {
            if let Ok(Some(row)) = rows.next() {
                reg_status = row.get(0).unwrap_or_else(|_| "CRISIS".to_string());
                reg_score = row.get(1).unwrap_or(0.0);
                breadth = row.get(2).unwrap_or(0.0);
            }
        }
    }

    // Check governor_decision.json for entry_lock
    let mut entry_lock = reg_status == "CRISIS" || reg_score < 0.40;
    let govr_path = resolve_project_root().join("backend").join("data").join("governor_decision.json");
    if let Ok(content) = std::fs::read_to_string(&govr_path) {
        if content.contains("\"entry_lock\": true") || content.contains("\"entry_lock\":true") {
            entry_lock = true;
        }
    }

    let regime_risk_label = if reg_status == "CRISIS" || reg_score < 0.40 {
        "Rủi ro cao"
    } else if reg_score < 0.60 {
        "Phân hóa"
    } else {
        "Thuận lợi"
    };

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
        "• Market Regime: {} (Score: {:.2} | Độ rộng >MA20: {:.1}% - {})\n",
        reg_status, reg_score, breadth, regime_risk_label
    ));

    // 3. Vĩ mô với tính từ chỉ trạng thái rõ ràng
    let mut fed_rate = None;
    let mut us10y = None;
    let mut dxy = None;
    let mut interbank_on = None;
    let mut vgb10y = None;

    if let Ok(mut m_stmt) = db_scr.conn.prepare(
        "SELECT value FROM macro_history WHERE variable=?1 ORDER BY date DESC LIMIT 1;",
    ) {
        for &var in &["FED_TARGET_RATE", "US10Y", "DXY", "INTERBANK_ON", "VGB10Y"] {
            if let Ok(mut rows) = m_stmt.query(params![var]) {
                if let Ok(Some(row)) = rows.next() {
                    let val: f64 = row.get(0).unwrap_or(0.0);
                    match var {
                        "FED_TARGET_RATE" => fed_rate = Some(val),
                        "US10Y" => us10y = Some(val),
                        "DXY" => dxy = Some(val),
                        "INTERBANK_ON" => interbank_on = Some(val),
                        "VGB10Y" => vgb10y = Some(val),
                        _ => {}
                    }
                }
            }
        }
    }

    out.push_str("• Trạng thái Vĩ mô:\n");
    if let Some(on) = interbank_on {
        let on_status = if on < 4.0 {
            "Dồi dào, hệ thống ổn định"
        } else if on < 5.0 {
            "Bình thường, chưa căng thẳng"
        } else {
            "⚠️ Cảnh báo căng thẳng thanh khoản"
        };
        out.push_str(&format!("  - Liên NH O/N: {:.2}% ({})\n", on, on_status));
    }

    let mut row2 = Vec::new();
    if let Some(d) = dxy {
        let d_status = if d < 102.0 {
            "Áp lực tỷ giá hạ nhiệt"
        } else if d <= 104.0 {
            "Áp lực vừa phải"
        } else {
            "Áp lực tỷ giá cao"
        };
        row2.push(format!("DXY {:.2} ({})", d, d_status));
    }
    if let Some(u) = us10y {
        let u_status = if u < 4.2 {
            "Ổn định"
        } else if u <= 4.7 {
            "Neo cao"
        } else {
            "Gây áp lực định giá"
        };
        row2.push(format!("US10Y {:.2}% ({})", u, u_status));
    }
    if !row2.is_empty() {
        out.push_str(&format!("  - {}\n", row2.join(" | ")));
    }

    let mut row3 = Vec::new();
    if let Some(f) = fed_rate {
        row3.push(format!("Fed {:.2}%", f));
    }
    if let Some(v) = vgb10y {
        row3.push(format!("TPCP 10Y {:.2}%", v));
    }
    if !row3.is_empty() {
        out.push_str(&format!("  - {}\n", row3.join(" | ")));
    }

    // 4. Phân loại danh mục trọng điểm (Loại bỏ Data Dump, phân nhóm động)
    out.push_str("── DIỄN BIẾN DANH MỤC TRỌNG ĐIỂM ──\n");
    let mut ohlcv_stmt = db_scr
        .conn
        .prepare("SELECT close, volume FROM daily_ohlcv WHERE symbol=?1 ORDER BY date DESC LIMIT 2;")
        .map_err(|e| e.to_string())?;

    struct SymDetail {
        symbol: &'static str,
        price: f64,
        pct: f64,
        vol: f64,
        note: String,
    }

    let mut group_active: Vec<SymDetail> = Vec::new();
    let mut group_sideways: Vec<String> = Vec::new();

    for &sym in &["SHB", "FPT", "PNJ", "MBB", "TCB", "VCB", "ACB", "HPG"] {
        let mut c0 = 0.0;
        let mut c1 = 0.0;
        let mut v0 = 0.0;
        let mut v1 = 0.0;

        if let Ok(mut rows) = ohlcv_stmt.query(params![sym]) {
            if let Ok(Some(row)) = rows.next() {
                c0 = row.get(0).unwrap_or(0.0);
                v0 = row.get(1).unwrap_or(0.0);
            }
            if let Ok(Some(row)) = rows.next() {
                c1 = row.get(0).unwrap_or(0.0);
                v1 = row.get(1).unwrap_or(0.0);
            }
        }
        if c0 <= 0.0 {
            continue;
        }

        let pct: f64 = if c1 > 0.0 { ((c0 - c1) / c1) * 100.0 } else { 0.0 };
        let vol_ratio: f64 = if v1 > 0.0 { v0 / v1 } else { 1.0 };
        let sign_s = if pct >= 0.0 { "+" } else { "" };

        // Phân loại: Biến động mạnh (|pct| >= 1.5% HOẶC vol_ratio >= 1.4 HOẶC v0 >= 25M cp)
        let is_active = pct.abs() >= 1.5 || vol_ratio >= 1.4 || v0 >= 25_000_000.0;

        if is_active {
            let note = if pct >= 2.0 {
                "Dòng tiền bứt phá mạnh mẽ".to_string()
            } else if pct > 0.0 && vol_ratio >= 1.3 {
                format!("Cầu hấp thụ chủ động, giữ sắc xanh (+{:.0}% Vol)", (vol_ratio - 1.0) * 100.0)
            } else if v0 >= 25_000_000.0 {
                "Thanh khoản dẫn đầu toàn thị trường".to_string()
            } else if pct <= -2.0 {
                "Áp lực bán mạnh, gãy nhịp ngắn hạn".to_string()
            } else if pct <= -1.5 {
                "Áp lực cung gia tăng, điều chỉnh kỹ thuật".to_string()
            } else if vol_ratio >= 1.4 && pct < 0.0 {
                "Thanh khoản tăng trong nhịp giảm (áp lực bán lớn)".to_string()
            } else {
                "Dao động đáng chú ý trong phiên".to_string()
            };

            group_active.push(SymDetail {
                symbol: sym,
                price: c0,
                pct,
                vol: v0,
                note,
            });
        } else {
            group_sideways.push(format!(
                "{}: {} ({}{:.2}%)",
                sym,
                format_int_dots(c0.round() as i64),
                sign_s,
                pct
            ));
        }
    }

    if !group_active.is_empty() {
        out.push_str("• Biến động / Dòng tiền chú ý:\n");
        for item in group_active {
            let sign_s = if item.pct >= 0.0 { "+" } else { "" };
            out.push_str(&format!(
                "  - {}: {} ({}{:.2}% | {:.1}M cp) - {}\n",
                item.symbol,
                format_int_dots(item.price.round() as i64),
                sign_s,
                item.pct,
                item.vol / 1_000_000.0,
                item.note
            ));
        }
    }

    if !group_sideways.is_empty() {
        out.push_str(&format!(
            "• Đi ngang tích lũy / Hẹp: {} | Khối lượng duy trì mức trung bình.\n",
            group_sideways.join(", ")
        ));
    }

    // 5. Phán quyết hệ thống & Hành động dứt khoát
    out.push_str("── ⚡ PHÁN QUYẾT & HÀNH ĐỘNG HÔM NAY ──\n");
    if entry_lock || reg_status == "CRISIS" || reg_score < 0.40 {
        out.push_str("🔴 TRẠNG THÁI: KHÓA MUA MỚI (ENTRY_LOCK = ON | 100% TIỀN MẶT)\n");
        out.push_str("• Mua mới: 🔒 KHÓA TOÀN BỘ. Tuyệt đối không bắt đáy hay mở vị thế mới khi thị trường ở Regime CRISIS (Score < 0.40).\n");
        out.push_str("• Quản trị danh mục: Đưa tài khoản về trạng thái phòng thủ tối đa; hạ margin về 0%; ưu tiên bảo toàn vốn.\n");
        let support_level: f64 = ((vni_c0 * 0.99 / 10.0) as f64).floor() * 10.0;
        out.push_str(&format!(
            "• Ngưỡng rủi ro: VN-INDEX mốc hỗ trợ tâm lý {:.0}. Nếu thủng mốc này, tiếp tục đứng ngoài quan sát, không hành động vội vã.\n",
            support_level
        ));
    } else if reg_score < 0.60 || reg_status == "RANGING" {
        out.push_str("🟡 TRẠNG THÁI: PHÂN HÓA / THĂM DÒ THẬN TRỌNG (TỶ TRỌNG 30-50%)\n");
        out.push_str("• Mua mới: Chỉ giải ngân thăm dò từng phần (10-20%) tại hỗ trợ cứng với mã có dòng tiền đỡ; không mua đuổi giá xanh.\n");
        out.push_str("• Quản trị danh mục: Duy trì sức mua 50-70% tiền mặt; chủ động hiện thực hóa lợi nhuận khi chạm kháng cự trên.\n");
        out.push_str(&format!(
            "• Ngưỡng rủi ro: VN-INDEX hỗ trợ gần tại {:.1}; tuân thủ dừng lỗ nghiêm ngặt nếu vị thế lỗ quá 5-7%.\n",
            vni_c1
        ));
    } else {
        out.push_str("🟢 TRẠNG THÁI: THỊ TRƯỜNG THUẬN LỢI (TỶ TRỌNG 70-80%)\n");
        out.push_str("• Mua mới: Mở vị thế theo tín hiệu bứt phá (breakout) hoặc kiểm định (pullback) trên các cổ phiếu dẫn dắt.\n");
        out.push_str("• Quản trị danh mục: Nâng trailing stop để khóa lợi nhuận; nắm giữ nhóm cổ phiếu khỏe hơn thị trường chung.\n");
        out.push_str(&format!(
            "• Ngưỡng rủi ro: Hỗ trợ MA10/MA20 ({:.1}); vi phạm xu hướng ngắn hạn thì hạ tỷ trọng ngay.\n",
            vni_c1
        ));
    }

    Ok(out.trim_end().to_string())
}

pub fn search_industry_fts(query: &str) -> Result<String, String> {
    let clean = query.trim();
    if clean.is_empty() {
        return Ok("🔍 Vui lòng nhập tên ngành hoặc từ khóa cần tìm (VD: `/mc ngành ngân hàng`, `/mc ngành thép`, `/mc ngành bán lẻ`).".to_string());
    }

    let screener_path = resolve_db_path("screener_cache.db");
    let db = Db::open_readonly(&screener_path)?;

    // FTS5 exact phrase search
    let fts_query = format!("\"{}\"", clean.replace('"', "\"\""));
    let mut stmt = db
        .conn
        .prepare("SELECT DISTINCT symbol, icb_name2, icb_name3 FROM symbol_fts WHERE symbol_fts MATCH ?1 LIMIT 15;")
        .map_err(|e| e.to_string())?;

    let mut rows = stmt.query(params![fts_query]).map_err(|e| e.to_string())?;
    let mut items = Vec::new();
    while let Some(row) = rows.next().map_err(|e| e.to_string())? {
        let sym: String = row.get(0).unwrap_or_default();
        let name2: String = row.get(1).unwrap_or_default();
        let name3: String = row.get(2).unwrap_or_default();
        items.push(format!("• **{}**: {} ({})", sym, name2, name3));
    }

    if items.is_empty() {
        let like_param = format!("%{}%", clean);
        if let Ok(mut l_stmt) = db.conn.prepare(
            "SELECT DISTINCT symbol, icb_name2, icb_name3 FROM symbol_industry WHERE icb_name2 LIKE ?1 OR icb_name3 LIKE ?1 OR icb_name4 LIKE ?1 LIMIT 15;",
        ) {
            if let Ok(mut l_rows) = l_stmt.query(params![like_param]) {
                while let Ok(Some(row)) = l_rows.next() {
                    let sym: String = row.get(0).unwrap_or_default();
                    let name2: String = row.get(1).unwrap_or_default();
                    let name3: String = row.get(2).unwrap_or_default();
                    items.push(format!("• **{}**: {} ({})", sym, name2, name3));
                }
            }
        }
    }

    if items.is_empty() {
        Ok(format!(
            "🔍 [Minh Chứng FTS5] Không tìm thấy mã cổ phiếu nào thuộc ngành hoặc từ khóa `{}`.",
            clean
        ))
    } else {
        Ok(format!(
            "🔍 [Minh Chứng FTS5] Danh sách cổ phiếu khớp từ khóa `{}` ({} mã):\n{}",
            clean,
            items.len(),
            items.join("\n")
        ))
    }
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
        || trimmed.starts_with("📋")
        || trimmed.starts_with("🏗️")
        || trimmed.starts_with("🌅")
        || trimmed.starts_with("🌇")
    {
        return None;
    }

    let lower = trimmed.to_lowercase();
    if lower.starts_with("minh tâm") || lower.starts_with("minh tam") || lower.starts_with("/mt") {
        return None;
    }

    // 1. Tiền tố chuẩn ở đầu
    if lower.starts_with("minh chứng") {
        return Some(trimmed["minh chứng".len()..].trim().to_string());
    }
    if lower.starts_with("minh chung") {
        return Some(trimmed["minh chung".len()..].trim().to_string());
    }
    if lower.starts_with("/mc ") || lower == "/mc" {
        return Some(trimmed["/mc".len()..].trim().to_string());
    }

    // 2. Hậu tố người dùng hay gõ tự nhiên (VD: "/tổng kết ngày hôm nay mc", "báo cáo mc", "vĩ mô mc")
    if lower.ends_with(" mc") {
        let mut body = trimmed[..trimmed.len() - 3].trim();
        if body.starts_with('/') {
            body = &body[1..];
        }
        return Some(body.trim().to_string());
    }
    if lower.ends_with("/mc") {
        let mut body = trimmed[..trimmed.len() - 3].trim();
        if body.starts_with('/') {
            body = &body[1..];
        }
        return Some(body.trim().to_string());
    }

    // 3. Trong nhóm riêng "Trạm Số Liệu - Minh Chứng":
    // Người dùng gõ trực tiếp /hpg, /shb, /brief, /ngành thép, /tìm, /sync hoặc mã CK (HPG, SHB, SSI...)
    if lower.starts_with('/') {
        return Some(trimmed[1..].trim().to_string());
    }
    if (trimmed.len() >= 3 && trimmed.len() <= 4) && trimmed.chars().all(|c| c.is_ascii_alphabetic()) {
        return Some(trimmed.to_string());
    }

    None
}

fn handle_zalo_command(cmd_body: &str) -> String {
    let clean = cmd_body.trim();
    let lower = clean.to_lowercase();

    // 1. Nhận diện các ý định tổng kết thị trường / vĩ mô / ATC / hôm nay
    if clean.is_empty()
        || lower.contains("báo cáo")
        || lower.contains("bao cao")
        || lower.contains("tổng kết")
        || lower.contains("tong ket")
        || lower.contains("cuối ngày")
        || lower.contains("cuoi ngay")
        || lower.contains("hôm nay")
        || lower.contains("hom nay")
        || lower.contains("thị trường")
        || lower.contains("thi truong")
        || lower.contains("vĩ mô")
        || lower.contains("vi mo")
        || lower.contains("macro")
        || lower.contains("brief")
        || lower.contains("summary")
        || lower.contains("atc")
    {
        return build_market_brief("TỔNG KẾT ĐÓNG CỬA PHIÊN ATC (MARKET SUMMARY 15:15)")
            .unwrap_or_else(|e| format!("⚠️ [Minh Chứng] Lỗi truy vấn: {}", e));
    }

    if lower.contains("sync") || lower.contains("cập nhật") || lower.contains("cap nhat") {
        let sync_res = run_cli_sync();
        let brief = build_market_brief("BÁO CÁO SAU ĐỒNG BỘ CLI")
            .unwrap_or_else(|e| format!("⚠️ [Minh Chứng] Lỗi truy vấn: {}", e));
        return format!("{}\n\n{}", sync_res, brief);
    }

    if lower.starts_with("ngành ")
        || lower.starts_with("nganh ")
        || lower.starts_with("tìm ")
        || lower.starts_with("tim ")
        || lower.starts_with("search ")
        || lower.starts_with("s ")
    {
        let query = clean
            .split_once(' ')
            .map(|x| x.1.trim())
            .unwrap_or(clean);
        return search_industry_fts(query)
            .unwrap_or_else(|e| format!("⚠️ [Minh Chứng FTS5] Lỗi: {}", e));
    }

    if lower.contains("help") || lower.contains("giúp") || lower.contains("hướng dẫn") {
        return "🤖 [Minh Chứng - Trợ lý Định lượng & FTS5 (<30ms)]\n\
                • Gõ `/mc <MÃ>` (VD: `/mc HPG`, `/mc TCB`, `/mc FPT`): Tra cứu tức thì Định giá & BCTC.\n\
                • Gõ `/mc ngành <TÊN_NGÀNH>` (VD: `/mc ngành ngân hàng`, `/mc ngành thép`, `/mc ngành bán lẻ`): Tra cứu danh mục cổ phiếu bằng FTS5.\n\
                • Gõ `/mc tổng kết`, `/mc hôm nay`, `/mc brief`: Tổng kết phiên ATC, Market Regime, Vĩ mô & Watchlist.\n\
                • Gõ `/mc sync`: Cập nhật dữ liệu CLI mới nhất."
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
        "search" | "s" | "fts" => {
            let query = if args.len() > 2 {
                args[2..].join(" ")
            } else {
                "ngân hàng".to_string()
            };
            match search_industry_fts(&query) {
                Ok(res) => println!("{}\n⏱️ Thời gian tìm kiếm FTS5: {:.2?}", res, t0.elapsed()),
                Err(e) => eprintln!("❌ Lỗi FTS5: {}", e),
            }
        }
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
                                "📨 Đã gửi báo cáo {} vào Zalo '{}' (CDP: {:.2?} | Tổng: {:.2?}).",
                                sym.to_uppercase(),
                                TARGET_CONV_NAME,
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
                                        "📨 Đã đồng bộ Zalo '{}' ({}) trong {:.2?}",
                                        TARGET_CONV_NAME,
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
                        println!("📩 [Zalo {}]: {}\n⏱️ {:.2?}", TARGET_CONV_NAME, msg, t0.elapsed());
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
            let mut last_processed_msg = String::new();
            let mut first_run = true;

            loop {
                {
                    let _guard = ZaloMutexGuard::acquire(1000);
                    if let Ok(mut cdp) = CdpClient::connect_with_cached_path(&mut cached_ws_path) {
                        if let Ok(msg) = cdp.read_latest_message() {
                            let trimmed = msg.trim().to_string();
                            if !trimmed.is_empty() && trimmed != last_processed_msg {
                                if first_run {
                                    first_run = false;
                                    last_processed_msg = trimmed.clone();
                                    println!("📌 Tin nhắn hiện tại: {}", trimmed);
                                } else if let Some(cmd_body) = parse_wake_command(&trimmed) {
                                    last_processed_msg = trimmed.clone();
                                    let t_cmd = Instant::now();
                                    println!("⚡ Phát hiện lệnh Minh Chứng: '{}'", cmd_body);
                                    let reply = handle_zalo_command(&cmd_body);
                                    let _ = cdp.send_message_to_my_documents(&reply);
                                    println!(
                                        "✅ Đã phản hồi trên Zalo trong {:.2?}",
                                        t_cmd.elapsed()
                                    );
                                } else {
                                    last_processed_msg = trimmed.clone();
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
