//! DexMentor Ask: the engineers' desktop app.
//!
//! One window, one copy: starting it again (Start menu, desktop, taskbar) brings the open window to the front
//! instead of opening another. Closing the window ends the app and frees the model's RAM.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod model;
mod web;

use std::sync::{LazyLock, OnceLock};
use std::time::Duration;

use dexwin::update::Updater;
use serde_json::Value;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

static URL: OnceLock<String> = OnceLock::new();
static UPDATER: LazyLock<Updater> = LazyLock::new(|| Updater::new("ask", model::VERSION, model::DATA.join("updates")));

// ---------- in-app updates, called by the page (the update bar) ----------
#[tauri::command]
fn update_status() -> Value {
    UPDATER.status()
}

#[tauri::command]
async fn update_check() -> Value {
    tauri::async_runtime::spawn_blocking(|| UPDATER.check()).await.unwrap_or_default()
}

/// "Restart and update" (or, with `back`, go back to the version before): stop the model runner, start the
/// installer, close. The installer replaces the files and opens DexMentor Ask again.
#[tauri::command]
async fn update_install(app: AppHandle, back: bool) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        model::stop_model();
        UPDATER.install(back, false)?;
        model::log(&format!("closing for the update to {}", UPDATER.status()["version"]));
        std::thread::sleep(Duration::from_millis(400));
        app.exit(0);
        Ok(())
    })
    .await
    .map_err(|e| e.to_string())?
}
/// Addresses the old app windows (Edge/Chrome) used, whose saved chats are imported once.
const OLD_ORIGINS: [&str; 2] = ["http://127.0.0.1:8720", "http://localhost:8720"];

fn ours(u: &tauri::Url) -> bool {
    let page = URL.get().and_then(|s| tauri::Url::parse(s).ok());
    matches!(u.scheme(), "tauri" | "about" | "data")
        || u.host_str() == Some("tauri.localhost")
        || page.is_some_and(|p| p.host_str() == u.host_str() && p.port() == u.port())
}

/// Links that lead out of the app (the update page) open in the PC's own browser.
fn open_outside(u: &tauri::Url) {
    use std::os::windows::process::CommandExt;
    if matches!(u.scheme(), "http" | "https") {
        let _ = std::process::Command::new("rundll32").args(["url.dll,FileProtocolHandler", u.as_str()]).creation_flags(0x0800_0000).spawn();
    }
}

fn show(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        return;
    }
    let built = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into())) // the splash first
        .title(model::APP)
        .inner_size(1280.0, 820.0)
        .min_inner_size(420.0, 480.0)
        .background_color(tauri::window::Color(7, 26, 29, 255))
        .on_navigation(|u| {
            let ok = ours(u);
            if !ok {
                open_outside(u);
            }
            ok
        })
        .build();
    match built {
        Ok(w) => {
            let url = URL.get().cloned().unwrap_or_default();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(900)); // long enough to see the splash, short enough not to wait on it
                if let Ok(u) = tauri::Url::parse(&url) {
                    let _ = w.navigate(u);
                }
            });
        }
        Err(e) => model::log(&format!("window could not open: {e}")),
    }
}

fn main() {
    let headless = std::env::args().any(|a| a == "--no-window");
    let app = tauri::Builder::default()
        // Must come first: a second copy hands over to the running one here and ends.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if !args.iter().any(|a| a == "--no-window") {
                show(app);
            }
        }))
        .invoke_handler(tauri::generate_handler![update_status, update_check, update_install])
        .setup(move |app| {
            model::init();
            UPDATER.run_checks(|| model::log(&format!("update {} downloaded and ready", UPDATER.status()["version"])));
            // A run that ended without "closed" in the log ended by a crash, a forced restart or a power cut.
            let log = std::fs::read_to_string(model::DATA.join("ask.log")).unwrap_or_default();
            if log.lines().rev().find(|l| l.contains(" started ") || l.ends_with(" closed")).is_some_and(|l| l.contains(" started ")) {
                model::log("the previous run did not close normally (PC restart, power cut or crash)");
            }
            model::log(&format!("started {} {}", model::APP, model::VERSION));
            // Before the page can load the history, so the page never saves over the imported chats.
            let n = dexwin::chats::import_once(&web::chats_file(), &OLD_ORIGINS);
            if n > 0 {
                model::log(&format!("imported {n} saved chats from the old app window's browser storage"));
            }
            let listener = web::bind()?;
            let _ = URL.set(format!("http://127.0.0.1:{}/", listener.local_addr()?.port()));
            tauri::async_runtime::spawn(web::serve(listener));
            if !headless {
                show(app.handle());
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("DexMentor Ask could not start");
    app.run(|_app, event| {
        if let RunEvent::Exit = event {
            model::stop_model();
            model::log("closed");
        }
    });
}
