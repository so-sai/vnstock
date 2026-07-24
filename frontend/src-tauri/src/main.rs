#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use chrono::Timelike;
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::Emitter;
use tauri::Manager;
use tauri::RunEvent;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

const ERL_LAST_SCAN: &str = "erl_last_scan.txt";
const ERL_DATA_DIR: &str = "backend/data";

struct BackendState {
    child_pid: Mutex<Option<u32>>,
}

fn latest_closed_session() -> chrono::NaiveDate {
    let now = chrono::Local::now();
    let today = now.date_naive();

    if now.time().num_seconds_from_midnight() >= 15 * 3600 + 5 * 60 {
        return today;
    }

    let mut cursor = today;
    loop {
        cursor = cursor.pred_opt().unwrap_or(cursor);
        let wd = cursor.format("%u").to_string().parse::<u32>().unwrap_or(0);
        if wd <= 5 {
            return cursor;
        }
    }
}

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

fn spawn_erl_catchup(_app_handle: &tauri::AppHandle) {
    let current_exe = std::env::current_exe().expect("Failed to get current exe");
    let app_dir = current_exe.parent().expect("Failed to get app dir").to_path_buf();

    if !needs_erl_catchup(&app_dir) {
        return;
    }

    eprintln!(
        "[ERL] Catch-up needed for session {} — spawning sidecar...",
        latest_closed_session().format("%Y-%m-%d"),
    );

    let sidecar_exe = app_dir.join("uv_backend-x86_64-pc-windows-msvc.exe");
    let fallback_exe = app_dir.join("uv_backend.exe");
    let binaries_exe = app_dir.join("binaries").join("uv_backend-x86_64-pc-windows-msvc.exe");

    let sidecar_path = if sidecar_exe.exists() {
        sidecar_exe
    } else if fallback_exe.exists() {
        fallback_exe
    } else {
        binaries_exe
    };

    let result = std::process::Command::new(sidecar_path)
        .args(["erl-scan", "--whitelist"])
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
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(BackendState {
            child_pid: Mutex::new(None),
        })
        .setup(|app| {
            let sidecar_command = app
                .shell()
                .sidecar("uv_backend")
                .expect("Failed to create sidecar configuration")
                .args(["serve"]);

            let (mut rx, child) = sidecar_command
                .spawn()
                .expect("Failed to spawn sidecar");

            let state = app.state::<BackendState>();
            *state.child_pid.lock().unwrap() = Some(child.pid());

            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            let _ = app_handle.emit("backend-stdout", line);
                        }
                        CommandEvent::Stderr(line) => {
                            let _ = app_handle.emit("backend-stderr", line);
                        }
                        _ => {}
                    }
                }
            });

            spawn_erl_catchup(app.handle());

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                let state = app_handle.state::<BackendState>();
                let pid = {
                    let guard = state.child_pid.lock().unwrap();
                    *guard
                };
                if let Some(pid) = pid {
                    #[cfg(target_os = "windows")]
                    {
                        let _ = std::process::Command::new("taskkill")
                            .args(["/F", "/PID", &pid.to_string()])
                            .output();
                        let _ = std::process::Command::new("taskkill")
                            .args(["/F", "/IM", "uv_backend-x86_64-pc-windows-msvc.exe"])
                            .output();
                        let _ = std::process::Command::new("taskkill")
                            .args(["/F", "/IM", "uv_backend.exe"])
                            .output();
                    }
                }
            }
        });
}
