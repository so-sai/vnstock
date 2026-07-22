#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use std::path::PathBuf;
use std::sync::Mutex;
use tauri::api::process::Command;
use tauri::{Manager, RunEvent};

const ERL_LAST_SCAN: &str = "erl_last_scan.txt";
const ERL_DATA_DIR: &str = "backend/data";

struct BackendState {
    child_process: Mutex<Option<tauri::api::process::CommandChild>>,
}

/// Phiên đóng cửa gần nhất dựa trên giờ hiện tại.
///
/// - Trước 15:05  → ngày GD trước đó
/// - Sau 15:05    → hôm nay (đã đóng cửa)
fn latest_closed_session() -> chrono::NaiveDate {
    let now = chrono::Local::now();
    let today = now.date_naive();

    // Nếu đã qua 15:05 → hôm nay là phiên đã đóng
    if now.time().num_seconds_from_midnight() >= 15 * 3600 + 5 * 60 {
        return today;
    }

    // Chưa qua 15:05 → lùi về phiên trước (skip weekends)
    let mut cursor = today;
    loop {
        cursor = cursor.pred_opt().unwrap_or(cursor);
        let wd = cursor.format("%u").to_string().parse::<u32>().unwrap_or(0);
        if wd <= 5 {
            // Mon-Fri, bỏ qua holiday check đơn giản
            return cursor;
        }
    }
}

/// Kiểm tra xem ERL scan cho phiên đã đóng gần nhất đã chạy chưa.
fn needs_erl_catchup(app_dir: &PathBuf) -> bool {
    let scan_file = app_dir.join(ERL_DATA_DIR).join(ERL_LAST_SCAN);
    let expected = latest_closed_session();

    if !scan_file.exists() {
        return true;
    }
    match std::fs::read_to_string(&scan_file) {
        Ok(content) => content.trim() != expected.format("%Y-%m-%d").to_string(),
        Err(_) => true,
    }
}

/// Chạy ERL scan one-shot qua CLI sidecar.
fn spawn_erl_catchup(app_handle: &tauri::AppHandle) {
    let app_dir = app_handle
        .path_resolver()
        .app_resource_dir()
        .unwrap_or_else(|| PathBuf::from("."));

    if !needs_erl_catchup(&app_dir) {
        return;
    }

    eprintln!(
        "[ERL] Catch-up needed for session {} — spawning sidecar...",
        latest_closed_session().format("%Y-%m-%d"),
    );

    let sidecar = app_handle
        .path_resolver()
        .resolve_resource("binaries/uv_backend")
        .expect("Failed to resolve sidecar binary");

    let result = std::process::Command::new(&sidecar)
        .args(&["erl-scan", "--whitelist"])
        .current_dir(&app_dir)
        .output();

    match result {
        Ok(output) => {
            if output.status.success() {
                eprintln!(
                    "[ERL] Catch-up scan OK: {}",
                    String::from_utf8_lossy(&output.stdout)
                        .lines()
                        .last()
                        .unwrap_or(""),
                );
            } else {
                eprintln!(
                    "[ERL] Catch-up scan FAILED: {}",
                    String::from_utf8_lossy(&output.stderr),
                );
            }
        }
        Err(e) => {
            eprintln!("[ERL] Catch-up scan error: {}", e);
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState {
            child_process: Mutex::new(None),
        })
        .setup(|app| {
            // ── 1. Spawn main backend sidecar ──
            let backend_run = Command::new_sidecar("uv_backend")
                .expect("Failed to create sidecar configuration")
                .args(["serve"])
                .spawn();

            match backend_run {
                Ok((mut rx, child)) => {
                    let state = app.state::<BackendState>();
                    *state.child_process.lock().unwrap() = Some(child);

                    let app_handle = app.handle();
                    tauri::async_runtime::spawn(async move {
                        while let Some(event) = rx.recv().await {
                            match event {
                                tauri::api::process::CommandEvent::Stdout(line) => {
                                    let _ = app_handle.emit_all("backend-stdout", line);
                                }
                                tauri::api::process::CommandEvent::Stderr(line) => {
                                    let _ = app_handle.emit_all("backend-stderr", line);
                                }
                                _ => {}
                            }
                        }
                    });
                }
                Err(e) => {
                    eprintln!("Sidecar spawn failed: {}", e);
                }
            }

            // ── 2. ERL Catch-up Scan (app vừa mở lại) ──
            // Phân biệt:
            //   - 10:30 sáng → scan phiên hôm qua (hôm nay chưa đóng)
            //   - 16:00 chiều → scan phiên hôm nay (đã đóng cửa)
            //   - Sáng thứ Hai → scan phiên thứ Sáu tuần trước
            spawn_erl_catchup(app.handle());

            Ok(())
        })
        .on_window_event(|event| {
            if let tauri::WindowEvent::Destroyed = event.event() {
                #[cfg(target_os = "windows")]
                {
                    let _ = std::process::Command::new("taskkill")
                        .args(&["/F", "/IM", "uv_backend-x86_64-pc-windows-msvc.exe"])
                        .output();
                    let _ = std::process::Command::new("taskkill")
                        .args(&["/F", "/IM", "uv_backend.exe"])
                        .output();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                let state = app_handle.state::<BackendState>();
                let mut guard = state.child_process.lock().unwrap();
                if let Some(child) = guard.take() {
                    drop(guard);
                    let _ = child.kill();
                }
                #[cfg(target_os = "windows")]
                {
                    let _ = std::process::Command::new("taskkill")
                        .args(&["/F", "/IM", "uv_backend-x86_64-pc-windows-msvc.exe"])
                        .output();
                    let _ = std::process::Command::new("taskkill")
                        .args(&["/F", "/IM", "uv_backend.exe"])
                        .output();
                }
            }
        });
}
