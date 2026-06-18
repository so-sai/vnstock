#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use tauri::api::process::Command;
use tauri::{Manager, RunEvent};
use std::sync::Mutex;

struct BackendState {
    child_process: Mutex<Option<tauri::api::process::CommandChild>>,
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState {
            child_process: Mutex::new(None),
        })
        .setup(|app| {
            let backend_run = Command::new_sidecar("uv_backend")
                .expect("Failed to create sidecar configuration")
                .spawn();

            match backend_run {
                Ok((mut rx, child)) => {
                    let state = app.state::<BackendState>();
                    *state.child_process.lock().unwrap() = Some(child);

                    // Stream stdout and stderr logs to frontend via Tauri events
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
