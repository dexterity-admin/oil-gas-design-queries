//! DexMentor Library: the server PC's program.
//!
//! The Rust shell gives the Library its own window (the Library desk) and a tray icon, and runs the Python
//! engine (server/: reading, OCR, search) behind it as a hidden child process. One copy only: starting it again
//! shows the desk. Closing the desk keeps the Library serving the office from the tray; "Stop DexMentor Library"
//! in the tray menu (or on the desk) ends it. The engine is in a job object, so it can never outlive the shell.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{LazyLock, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use dexwin::job::Job;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const APP: &str = "DexMentor Library";
/// Addresses the old desk window (Edge/Chrome) used, whose saved chats are imported once.
const OLD_ORIGINS: [&str; 2] = ["http://127.0.0.1:8700", "http://localhost:8700"];

static HOME: LazyLock<PathBuf> = LazyLock::new(|| {
    PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_else(|| ".".into())).join("DexMentor Library")
});
static KB: LazyLock<PathBuf> = LazyLock::new(|| match std::env::var_os("DATA_DIR") {
    Some(p) => PathBuf::from(p),
    None => HOME.join("kb"),
});
static PORT: OnceLock<u16> = OnceLock::new();
static ENGINE: Mutex<Option<Child>> = Mutex::new(None);
static JOB: LazyLock<Option<Job>> = LazyLock::new(|| Job::new(None)); // the engine dies with the shell
static QUITTING: AtomicBool = AtomicBool::new(false);
static VERSION: &str = match option_env!("APP_VERSION") {
    Some(v) => v,
    None => "dev",
};
static UPDATER: LazyLock<dexwin::update::Updater> = LazyLock::new(|| dexwin::update::Updater::new("library", VERSION, HOME.join("updates")));

// ---------- in-app updates, called by the desk page (the update bar) ----------
#[tauri::command]
fn update_status() -> serde_json::Value {
    UPDATER.status()
}

#[tauri::command]
async fn update_check() -> serde_json::Value {
    tauri::async_runtime::spawn_blocking(|| UPDATER.check()).await.unwrap_or_default()
}

#[tauri::command]
async fn update_install(app: AppHandle, back: bool) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || install_update(&app, back, false)).await.map_err(|e| e.to_string())?
}

/// Stops the engine (and its scan readers), starts the installer, closes. The installer replaces the files and
/// starts the Library again in the tray; the engineers' apps reconnect by themselves.
fn install_update(app: &AppHandle, back: bool, quiet_start: bool) -> Result<(), String> {
    QUITTING.store(true, Ordering::SeqCst);
    if let Some(mut c) = ENGINE.lock().unwrap().take() {
        let _ = c.kill();
        let _ = c.wait();
    }
    if let Err(e) = UPDATER.install(back, quiet_start) {
        QUITTING.store(false, Ordering::SeqCst); // the installer did not start: keep serving
        let handle = app.clone();
        std::thread::spawn(move || supervise(handle));
        return Err(e);
    }
    log(&format!("closing for the update to {}", UPDATER.status()["version"]));
    std::thread::sleep(Duration::from_millis(400));
    app.exit(0);
    Ok(())
}

/// The server PC usually runs unattended: a ready update installs itself between 01:00 and 05:00 when the desk
/// is not open, so nobody is asking questions and nobody is managing the library.
fn night_installer(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(600));
        let desk_open = app.get_webview_window("main").and_then(|w| w.is_visible().ok()).unwrap_or(false);
        if UPDATER.status()["state"] == "ready" && !desk_open && (1..5).contains(&dexwin::update::local_hour()) {
            log("installing the ready update at night");
            if let Err(e) = install_update(&app, false, true) {
                log(&format!("night update did not start: {e}"));
            }
        }
    });
}

fn log(line: &str) {
    let _ = std::fs::create_dir_all(&*KB);
    let t = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(KB.join("shell.log")) {
        let _ = writeln!(f, "{t} {line}");
    }
}

/// The usual port, or the next free one (Windows sometimes reserves a block of ports after a restart). The
/// engineers' apps find the Library again by asking the network, which answers with the port in use.
fn pick_port() -> u16 {
    let first: u16 = std::env::var("PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8700);
    for p in std::iter::once(first).chain(8702..8712) {
        if TcpListener::bind(("0.0.0.0", p)).is_ok() {
            if p != first {
                log(&format!("port {first} was not free; using {p}"));
            }
            return p;
        }
    }
    first
}

fn engine_command(port: u16) -> Command {
    // DEXMENTOR_ENGINE runs the engine from source, e.g. "python -m server" (from the repository folder).
    let mut cmd = match std::env::var("DEXMENTOR_ENGINE") {
        Ok(s) => {
            let mut parts = s.split_whitespace();
            let mut c = Command::new(parts.next().unwrap_or("python"));
            c.args(parts);
            c
        }
        Err(_) => {
            let dir = std::env::current_exe().ok().and_then(|p| p.parent().map(|p| p.to_path_buf())).unwrap_or_default();
            Command::new(dir.join("engine").join("DexMentorLibraryEngine.exe"))
        }
    };
    cmd.arg("--no-window")
        .env("PORT", port.to_string())
        .env("APP_NAME", APP)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(0x0800_0000); // no console window; the engine lowers its own priority and caps its CPU
    cmd
}

fn ready(port: u16) -> bool {
    let Ok(mut s) = TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_secs(1)) else { return false };
    let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
    let req = format!("GET /api/status HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n");
    let mut head = [0u8; 16];
    s.write_all(req.as_bytes()).is_ok() && s.read(&mut head).is_ok_and(|n| head[..n].windows(3).any(|w| w == b"200"))
}

/// Runs the engine, and starts it again if it stops by itself. Stopped on purpose (the desk's "Stop the
/// Library", which exits with code 0), the whole program ends.
fn supervise(app: AppHandle) {
    let port = *PORT.get().unwrap();
    let mut crashes: Vec<Instant> = vec![];
    loop {
        match engine_command(port).spawn() {
            Ok(child) => {
                if let Some(j) = JOB.as_ref() {
                    j.add(&child);
                }
                log(&format!("engine started on port {port}"));
                *ENGINE.lock().unwrap() = Some(child);
            }
            Err(e) => {
                log(&format!("engine could not start: {e}"));
                app.exit(1);
                return;
            }
        }
        let code = loop {
            std::thread::sleep(Duration::from_millis(500));
            let mut g = ENGINE.lock().unwrap();
            match g.as_mut().map(|c| c.try_wait()) {
                Some(Ok(Some(st))) => break st.code(),
                Some(Ok(None)) => continue,
                _ => break None, // taken away by quit()
            }
        };
        if QUITTING.load(Ordering::SeqCst) {
            return;
        }
        if code == Some(0) {
            log("engine stopped from the desk");
            app.exit(0);
            return;
        }
        log(&format!("engine stopped unexpectedly (exit code {code:?}); starting it again"));
        crashes.retain(|t| t.elapsed() < Duration::from_secs(600));
        crashes.push(Instant::now());
        if crashes.len() > 5 {
            log("engine stopped 6 times in 10 minutes; giving up");
            app.exit(1);
            return;
        }
        std::thread::sleep(Duration::from_secs(3));
    }
}

fn quit(app: &AppHandle) {
    QUITTING.store(true, Ordering::SeqCst);
    if let Some(mut c) = ENGINE.lock().unwrap().take() {
        let _ = c.kill();
        let _ = c.wait();
    }
    log("stopped");
    app.exit(0);
}

fn open_outside(u: &tauri::Url) {
    if matches!(u.scheme(), "http" | "https") {
        let _ = Command::new("rundll32").args(["url.dll,FileProtocolHandler", u.as_str()]).creation_flags(0x0800_0000).spawn();
    }
}

fn show(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        return;
    }
    let port = *PORT.get().unwrap();
    let built = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into())) // the splash first
        .title(APP)
        .inner_size(1360.0, 860.0)
        .min_inner_size(480.0, 480.0)
        .background_color(tauri::window::Color(7, 26, 29, 255))
        .on_navigation(move |u| {
            let ok = matches!(u.scheme(), "tauri" | "about" | "data")
                || u.host_str() == Some("tauri.localhost")
                || (u.host_str() == Some("127.0.0.1") && u.port() == Some(port));
            if !ok {
                open_outside(u);
            }
            ok
        })
        .build();
    let w = match built {
        Ok(w) => w,
        Err(e) => {
            log(&format!("window could not open: {e}"));
            return;
        }
    };
    let win = w.clone();
    w.on_window_event(move |e| {
        if let WindowEvent::CloseRequested { api, .. } = e {
            api.prevent_close(); // the Library keeps serving the office from the tray
            let _ = win.hide();
        }
    });
    std::thread::spawn(move || {
        let start = Instant::now();
        while !ready(port) {
            if start.elapsed() > Duration::from_secs(300) {
                let msg = format!("The Library engine did not start. Its log is in {}", KB.join("server.log").display());
                let _ = w.eval(format!("document.getElementById('msg').textContent = {:?}", msg));
                return;
            }
            std::thread::sleep(Duration::from_millis(400));
        }
        std::thread::sleep(Duration::from_millis(300));
        if let Ok(u) = tauri::Url::parse(&format!("http://127.0.0.1:{port}/#desk")) {
            let _ = w.navigate(u);
        }
    });
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
            log(&format!("started {APP} {VERSION}"));
            UPDATER.run_checks(|| log(&format!("update {} downloaded and ready", UPDATER.status()["version"])));
            night_installer(app.handle().clone());
            let n = dexwin::chats::import_once(&KB.join("chats.json"), &OLD_ORIGINS);
            if n > 0 {
                log(&format!("imported {n} saved chats from the old desk window's browser storage"));
            }
            let _ = PORT.set(pick_port());
            let handle = app.handle().clone();
            std::thread::spawn(move || supervise(handle));

            let open = MenuItem::with_id(app, "open", "Open the Library desk", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "quit", "Stop DexMentor Library", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &stop])?;
            let mut tray = TrayIconBuilder::with_id("main")
                .tooltip(APP)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, e| match e.id.as_ref() {
                    "open" => show(app),
                    "quit" => quit(app),
                    _ => {}
                })
                .on_tray_icon_event(|tray, e| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = e {
                        show(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            if let Err(e) = tray.build(app) {
                log(&format!("tray icon not available: {e}")); // e.g. no desktop session; the Library still serves
            }
            if !headless {
                show(app.handle());
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("DexMentor Library could not start");
    app.run(|app, event| match event {
        // The desk was closed: keep running in the tray. Only quit() ends the program.
        RunEvent::ExitRequested { code: None, api, .. } => api.prevent_exit(),
        RunEvent::Exit => {
            QUITTING.store(true, Ordering::SeqCst);
            if let Some(mut c) = ENGINE.lock().unwrap().take() {
                let _ = c.kill();
            }
            let _ = app;
        }
        _ => {}
    });
}
