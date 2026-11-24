//! The small language model on this PC: a one-time download, and the llama.cpp runner started only while it
//! is used. It unloads after a few idle minutes so the RAM goes back to the design software.
//!
//! The runner lives in a job object (dexwin::job): it dies with the app, may use at most a hard share of the
//! CPU (raised only while an answer is written) and at most a fixed amount of committed memory.
//!
//! The graphics card is never tried on its own. An old card's driver can hang under a full load, and Windows
//! may then restart the PC; so the test runs only when the engineer presses "Test the graphics card", and a
//! marker file left behind by a test or a graphics-card run that never finished switches DexMentor Ask back to
//! the processor for good.
use std::fs;
use std::io::{Read, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{LazyLock, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use dexwin::job::{self, Boost, Job};
use serde_json::{json, Map, Value};

pub const APP: &str = "DexMentor Ask";
pub const VERSION: &str = match option_env!("APP_VERSION") {
    Some(v) => v,
    None => "dev",
};

fn env_u16(name: &str, default: u16) -> u16 {
    std::env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

pub static PORT: LazyLock<u16> = LazyLock::new(|| env_u16("DEXMENTOR_PORT", 8720));
pub static LLM_PORT: LazyLock<u16> = LazyLock::new(|| env_u16("DEXMENTOR_LLM_PORT", 8719));
/// Everything this app keeps: settings, the model, the runner, the saved chats. Same folder as the old
/// DexMentor launcher used, so an update keeps the downloaded model and the settings.
pub static DATA: LazyLock<PathBuf> = LazyLock::new(|| match std::env::var_os("DEXMENTOR_HOME") {
    Some(p) => PathBuf::from(p),
    None => PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap_or_else(|| ".".into())).join("DexMentor"),
});
pub static MANIFEST: LazyLock<Value> = LazyLock::new(|| {
    std::env::var("DEXMENTOR_MANIFEST")
        .ok()
        .and_then(|p| fs::read(p).ok())
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_else(|| serde_json::from_str(include_str!("../../../desktop/models.json")).expect("models.json"))
});
// Hard ceilings for the model runner in percent of the whole PC (0 = no cap), set by the owner: under 10 %
// at all times, under 25 % while an answer is written.
static CPU_LIMIT: LazyLock<f64> = LazyLock::new(|| job::limit_from_env("DEXMENTOR_CPU_LIMIT", 6.0));
static ANSWER_LIMIT: LazyLock<f64> = LazyLock::new(|| job::limit_from_env("DEXMENTOR_ANSWER_CPU_LIMIT", 20.0));
/// Committed memory the runner may use (the weights are read from the mapped model file and do not count).
const RUNNER_MEMORY: usize = 4 << 30;
static JOB: LazyLock<Option<Job>> = LazyLock::new(|| Job::new(Some(RUNNER_MEMORY)));
pub static BOOST: LazyLock<Boost> = LazyLock::new(|| Boost::new(JOB.as_ref(), *CPU_LIMIT, *ANSWER_LIMIT));

static SETTINGS: LazyLock<Mutex<Map<String, Value>>> = LazyLock::new(|| Mutex::new(Map::new()));
static STATE: LazyLock<Mutex<Map<String, Value>>> = LazyLock::new(|| {
    let m = &MANIFEST["model"];
    Mutex::new(
        json!({"state": "missing", "done": 0, "total": 0, "step": "", "error": "", "model": m["name"],
               "size_mb": m["size_mb"], "system_suffix": m.get("system_suffix").cloned().unwrap_or(json!("")), "version": VERSION})
        .as_object()
        .unwrap()
        .clone(),
    )
});
static RUNNER: Mutex<Option<Child>> = Mutex::new(None);
static LAST_USED: AtomicU64 = AtomicU64::new(0);

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn touch() {
    LAST_USED.store(now(), Ordering::Relaxed);
}

fn set(pairs: &[(&str, Value)]) {
    let mut s = STATE.lock().unwrap();
    for (k, v) in pairs {
        s.insert((*k).into(), v.clone());
    }
}

fn state() -> String {
    STATE.lock().unwrap()["state"].as_str().unwrap_or("").to_string()
}

// ---------- log: one line per event, so a restart or a crash can be traced afterwards ----------
pub fn log(line: &str) {
    let path = DATA.join("ask.log");
    if fs::metadata(&path).map(|m| m.len() > 1 << 20).unwrap_or(false) {
        let _ = fs::rename(&path, DATA.join("ask.old.log"));
    }
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{} {line}", now());
    }
}

// ---------- settings ----------
fn settings_file() -> PathBuf {
    DATA.join("settings.json")
}

pub fn server_url() -> String {
    SETTINGS.lock().unwrap().get("server_url").and_then(Value::as_str).unwrap_or("").to_string()
}

fn save_settings() {
    let body = serde_json::to_vec(&*SETTINGS.lock().unwrap()).unwrap_or_default();
    if let Err(e) = dexwin::chats::write_atomic(&settings_file(), &body) {
        log(&format!("settings not saved: {e}"));
    }
}

pub fn set_server(url: &str) {
    SETTINGS.lock().unwrap().insert("server_url".into(), json!(url));
    save_settings();
}

pub fn accel() -> Value {
    SETTINGS.lock().unwrap().get("accel").cloned().unwrap_or(Value::Null)
}

fn set_accel(v: Option<Value>) {
    let mut s = SETTINGS.lock().unwrap();
    match v {
        Some(v) => s.insert("accel".into(), v),
        None => s.remove("accel"),
    };
    drop(s);
    save_settings();
}

/// Marker present while the graphics card is being tested or used. Found at start-up, it means that work
/// never finished: the PC restarted, the driver stopped, or the app was killed.
fn gpu_marker() -> PathBuf {
    DATA.join("gpu.pending")
}

pub fn init() {
    let _ = fs::create_dir_all(&*DATA);
    let mut s = SETTINGS.lock().unwrap();
    s.insert("server_url".into(), json!(std::env::var("DEXMENTOR_SERVER").unwrap_or_else(|_| "http://127.0.0.1:8700".into())));
    if let Ok(Value::Object(saved)) = fs::read(settings_file()).map_err(|_| ()).and_then(|b| serde_json::from_slice(&b).map_err(|_| ())) {
        s.extend(saved); // a damaged or missing file: start from the defaults
    }
    drop(s);
    if gpu_marker().exists() {
        let gpu = accel().get("gpu").cloned().unwrap_or(json!(""));
        set_accel(Some(json!({"mode": "cpu", "gpu": gpu, "gpu_failed": true,
            "note": "The graphics card test or run did not finish last time (the PC restarted, or its driver stopped), so DexMentor Ask uses the processor."})));
        let _ = fs::remove_file(gpu_marker());
        log("graphics card work did not finish last time: switched to the processor");
    }
    refresh_state();
    std::thread::spawn(|| {
        auto_connect();
    });
    std::thread::spawn(idle_watch);
}

// ---------- the office Library ----------
static HTTP: LazyLock<reqwest::blocking::Client> = LazyLock::new(|| {
    reqwest::blocking::Client::builder()
        .no_proxy() // office-network calls never go through a web proxy
        .user_agent("DexMentor")
        .timeout(Duration::from_secs(45))
        .connect_timeout(Duration::from_secs(20))
        .build()
        .expect("http client")
});

pub fn server_ok(url: &str) -> bool {
    let url = format!("{}/api/status", url.trim_end_matches('/'));
    HTTP.get(url)
        .timeout(Duration::from_secs(3))
        .send()
        .ok()
        .filter(|r| r.status().is_success())
        .and_then(|r| r.json::<Value>().ok())
        .is_some_and(|v| v.get("version").is_some())
}

fn private(url: &str) -> bool {
    let host = url.split("://").nth(1).unwrap_or("").rsplit_once(':').map(|h| h.0).unwrap_or("");
    match host.trim_matches(|c| c == '[' || c == ']').parse::<std::net::IpAddr>() {
        Ok(std::net::IpAddr::V4(ip)) => ip.is_private() || ip.is_loopback(),
        Ok(std::net::IpAddr::V6(ip)) => ip.is_loopback(),
        Err(_) => false,
    }
}

/// Asks the office network "is a DexMentor Library running?" and returns the addresses that answer.
pub fn discover(wait: Duration) -> Vec<String> {
    use std::net::UdpSocket;
    let mut found = vec![];
    let Ok(s) = UdpSocket::bind("0.0.0.0:0") else { return found };
    let _ = s.set_broadcast(true);
    let _ = s.set_read_timeout(Some(Duration::from_millis(400)));
    let mut targets = vec!["255.255.255.255".to_string(), "127.0.0.1".to_string()];
    if let Ok(probe) = UdpSocket::bind("0.0.0.0:0") {
        // also this PC's own network broadcast address, which some routers need
        if probe.connect("10.255.255.255:1").is_ok() {
            if let Ok(std::net::SocketAddr::V4(a)) = probe.local_addr() {
                let o = a.ip().octets();
                targets.push(format!("{}.{}.{}.255", o[0], o[1], o[2]));
            }
        }
    }
    for t in &targets {
        let _ = s.send_to(b"DEXMENTOR_DISCOVER_V1", (t.as_str(), 8701));
    }
    let end = Instant::now() + wait;
    let mut buf = [0u8; 600];
    while Instant::now() < end {
        let Ok((n, sender)) = s.recv_from(&mut buf) else { continue };
        let Ok(v) = serde_json::from_slice::<Value>(&buf[..n]) else { continue };
        let url = v["url"].as_str().unwrap_or("").to_string();
        let port = url.rsplit(':').next().unwrap_or("").trim_matches('/').to_string();
        // The Library says where it thinks it is; the address the answer came from is tried too, which is
        // what works when the Library PC has several network adapters.
        for u in [format!("http://{}:{}", sender.ip(), port), url] {
            if u.starts_with("http://") && private(&u) && !found.contains(&u) {
                found.push(u);
            }
        }
    }
    found
}

/// Keeps the saved Library if it answers; otherwise finds one on the network and remembers it.
pub fn auto_connect() -> bool {
    if server_ok(&server_url()) {
        return true;
    }
    for url in discover(Duration::from_secs(2)) {
        if server_ok(&url) {
            set_server(&url);
            log(&format!("connected to the Library at {url}"));
            return true;
        }
    }
    false
}

// ---------- files ----------
fn model_path() -> PathBuf {
    DATA.join("models").join(MANIFEST["model"]["file"].as_str().unwrap_or("model.gguf"))
}

fn find(dir: &Path, name: &str) -> Option<PathBuf> {
    let mut found: Vec<PathBuf> = vec![];
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in fs::read_dir(d).into_iter().flatten().flatten() {
            let p = e.path();
            if p.is_dir() {
                stack.push(p);
            } else if p.file_name().is_some_and(|n| n.eq_ignore_ascii_case(name)) {
                found.push(p);
            }
        }
    }
    found.sort();
    found.into_iter().next()
}

/// The command that starts the llama.cpp server, or None if it is not installed yet.
fn runtime_cmd(kind: &str) -> Option<Vec<String>> {
    if let Ok(o) = std::env::var("DEXMENTOR_LLAMA_SERVER") {
        return Some(o.split_whitespace().map(String::from).collect());
    }
    let dir = DATA.join(if kind == "gpu" { "runtime-gpu" } else { "runtime" });
    find(&dir, "llama-server.exe").map(|p| vec![p.to_string_lossy().into_owned()])
}

fn refresh_state() {
    if ["downloading", "tuning", "starting", "running"].contains(&state().as_str()) {
        return;
    }
    let ready = model_path().exists() && runtime_cmd("cpu").is_some();
    set(&[("state", json!(if ready { "ready" } else { "missing" }))]);
}

pub fn status() -> Value {
    refresh_state();
    let mut s = STATE.lock().unwrap().clone();
    s.insert("server_url".into(), json!(server_url()));
    s.insert("accel".into(), accel());
    s.insert("app".into(), json!(APP));
    Value::Object(s)
}

/// Name of a real graphics card worth offering (an old GeForce counts), or "".
fn detect_gpu() -> String {
    if std::env::var_os("DEXMENTOR_FORCE_GPU_TEST").is_some() {
        return "forced test".into();
    }
    let Ok(mut child) = Command::new("powershell")
        .args(["-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(0x0800_0000)
        .spawn()
    else {
        return String::new();
    };
    let start = Instant::now();
    while child.try_wait().ok().flatten().is_none() {
        if start.elapsed() > Duration::from_secs(20) {
            let _ = child.kill();
            return String::new();
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    let mut out = String::new();
    let _ = child.stdout.take().map(|mut o| o.read_to_string(&mut out));
    const MAKERS: [&str; 6] = ["nvidia", "geforce", "quadro", "radeon", "amd", "arc"];
    out.lines()
        .find(|l| l.to_lowercase().split(|c: char| !c.is_alphanumeric()).any(|w| MAKERS.contains(&w)))
        .map(|l| l.trim().to_string())
        .unwrap_or_default()
}

// ---------- one-time download ----------
/// Download with resume and retries, so a dropped or stalled connection does not restart 1 GB.
fn fetch(url: &str, dest: &Path, step: &str, sha256: &str) -> Result<(), String> {
    use sha2::{Digest, Sha256};
    let _ = fs::create_dir_all(dest.parent().unwrap());
    let part = PathBuf::from(format!("{}.part", dest.display()));
    let mut last = String::new();
    let mut complete = false;
    for attempt in 0..8u64 {
        let have = fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
        let mut req = HTTP.get(url);
        if have > 0 {
            req = req.header("Range", format!("bytes={have}-"));
        }
        match req.send() {
            Ok(r) if r.status().as_u16() == 416 => {
                complete = true; // the server says there is nothing left to send: the file is complete
                break;
            }
            Ok(mut r) if r.status().is_success() => {
                let resumed = have > 0 && r.status().as_u16() == 206;
                let start = if resumed { have } else { 0 };
                let total = start + r.content_length().unwrap_or(0);
                set(&[("step", json!(step)), ("done", json!(start)), ("total", json!(total))]);
                let file = if resumed { fs::OpenOptions::new().append(true).open(&part) } else { fs::File::create(&part) };
                let mut f = match file {
                    Ok(f) => f,
                    Err(e) => return Err(format!("Could not write {} ({e}).", part.display())),
                };
                let mut buf = vec![0u8; 1 << 18];
                let mut done = start;
                let res: Result<(), String> = loop {
                    match r.read(&mut buf) {
                        Ok(0) => break Ok(()),
                        Ok(n) => {
                            if let Err(e) = f.write_all(&buf[..n]) {
                                break Err(e.to_string());
                            }
                            done += n as u64;
                            set(&[("done", json!(done))]);
                        }
                        Err(e) => break Err(e.to_string()),
                    }
                };
                let _ = f.sync_all();
                match res {
                    Ok(()) if total == start || done >= total => {
                        complete = true;
                        break;
                    }
                    Ok(()) => last = "the connection closed early".into(),
                    Err(e) => last = e,
                }
            }
            Ok(r) => last = format!("HTTP {}", r.status()),
            Err(e) => last = e.to_string(),
        }
        std::thread::sleep(Duration::from_secs((2 + attempt * 3).min(20)));
    }
    if !complete {
        return Err(format!("The download kept stopping ({last}). Press download again to resume."));
    }
    if !sha256.is_empty() {
        let mut h = Sha256::new();
        let mut f = fs::File::open(&part).map_err(|e| e.to_string())?;
        std::io::copy(&mut f, &mut h).map_err(|e| e.to_string())?;
        let got: String = h.finalize().iter().map(|b| format!("{b:02x}")).collect();
        if got != sha256.to_lowercase() {
            let _ = fs::remove_file(&part);
            return Err("The downloaded file did not match its expected fingerprint and was deleted.".into());
        }
    }
    fs::rename(&part, dest).map_err(|e| e.to_string())
}

fn unzip(url: &str, folder: &str, step: &str, sha256: &str) -> Result<(), String> {
    let dir = DATA.join(folder);
    let zip_path = dir.join("runner.zip");
    fetch(url, &zip_path, step, sha256)?;
    let f = fs::File::open(&zip_path).map_err(|e| e.to_string())?;
    zip::ZipArchive::new(f).and_then(|mut z| z.extract(&dir)).map_err(|e| format!("Could not unpack the runner ({e})."))?;
    let _ = fs::remove_file(zip_path);
    Ok(())
}

static DOWNLOADING: Mutex<()> = Mutex::new(());

pub fn download() {
    let Ok(_one) = DOWNLOADING.try_lock() else { return };
    set(&[("state", json!("downloading")), ("error", json!("")), ("done", json!(0)), ("total", json!(0))]);
    let res = (|| -> Result<(), String> {
        let rt = &MANIFEST["runtime"];
        let s = |v: &Value| v.as_str().unwrap_or("").to_string();
        if runtime_cmd("cpu").is_none() {
            // Only the pinned build DexMentor was tested with is ever downloaded and run.
            unzip(&s(&rt["url"]), "runtime", "Downloading the model runner", &s(&rt["sha256"]))?;
        }
        if !model_path().exists() {
            let m = &MANIFEST["model"];
            fetch(&s(&m["url"]), &model_path(), "Downloading the language model", &s(&m["sha256"]))?;
        }
        if accel().is_null() {
            // Note the graphics card, but do not test it: that is the engineer's choice (see the top of this file).
            set(&[("state", json!("tuning")), ("step", json!("Looking at this PC"))]);
            set_accel(Some(json!({"mode": "cpu", "gpu": detect_gpu()})));
        }
        Ok(())
    })();
    match res {
        Ok(()) => set(&[("state", json!("ready"))]),
        Err(e) => {
            log(&format!("download failed: {e}"));
            set(&[("state", json!("missing")), ("error", json!(e))]);
        }
    }
}

// ---------- the runner ----------
fn healthy() -> bool {
    HTTP.get(format!("http://127.0.0.1:{}/health", *LLM_PORT))
        .timeout(Duration::from_secs(2))
        .send()
        .is_ok_and(|r| r.status().is_success())
}

fn start(kind: &str) -> Result<Child, String> {
    let n = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4) as f64;
    let threads = if *CPU_LIMIT > 0.0 {
        // under the hard cap extra threads only queue for the same small share: use what the share is worth
        ((n * CPU_LIMIT.max(*ANSWER_LIMIT) / 100.0).round() as usize).max(1)
    } else if n <= 4.0 {
        n as usize
    } else {
        n as usize / 2
    };
    let mut cmd = runtime_cmd(kind).ok_or("The model runner is not installed yet.")?;
    let ctx = MANIFEST["model"]["context"].as_u64().unwrap_or(8192).to_string();
    let batch = std::env::var("DEXMENTOR_BATCH").unwrap_or_else(|_| "256".into());
    cmd.extend(
        [
            "-m", &model_path().to_string_lossy(), "-c", &ctx, "--host", "127.0.0.1", "--port", &LLM_PORT.to_string(),
            "-t", &threads.to_string(), "-b", &batch, "-ub", &batch, // smaller batches: smaller work buffers in RAM
            // Read the weights from the mapped model file instead of a private repacked copy (measured:
            // 983 -> 383 MB private), one answer at a time, no prompt cache in RAM.
            "--no-repack", "-np", "1", "-cram", "0",
        ]
        .map(String::from),
    );
    if kind == "gpu" {
        cmd.extend(["-ngl".into(), "99".into()]);
    }
    let child = Command::new(&cmd[0])
        .args(&cmd[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(job::QUIET)
        .spawn()
        .map_err(|e| format!("The model runner could not start ({e})."))?;
    if let Some(j) = JOB.as_ref() {
        j.add(&child);
    }
    Ok(child)
}

/// Seconds this PC needs for one typical question with the processor or the graphics card; None if it fails.
fn bench(kind: &str) -> Option<f64> {
    let mut p = start(kind).ok()?;
    let res = (|| {
        for _ in 0..180 {
            if p.try_wait().ok().flatten().is_some() {
                return None;
            }
            if healthy() {
                let body = json!({"max_tokens": 48, "temperature": 0, "messages": [{"role": "user", "content":
                    format!("SOURCES:\n{}\nQUESTION: Summarise the rule in one sentence.",
                            "Carbon steel piping shall be supported at the spans given in the table. ".repeat(45))}]});
                let t = Instant::now();
                let ok = HTTP.post(format!("http://127.0.0.1:{}/v1/chat/completions", *LLM_PORT))
                    .timeout(Duration::from_secs(300))
                    .json(&body)
                    .send()
                    .is_ok_and(|r| r.status().is_success());
                return ok.then(|| (t.elapsed().as_secs_f64() * 10.0).round() / 10.0);
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        None
    })();
    let _ = p.kill();
    let _ = p.wait();
    res
}

/// Pressed by the engineer: times the processor and the graphics card and keeps the faster one.
pub fn test_gpu() {
    let _g = BOOST.hold(); // the timing must see the same ceiling answers get
    stop_model();
    let gpu = detect_gpu();
    let mut acc = json!({"mode": "cpu", "gpu": gpu});
    let gpu_url = MANIFEST["runtime"]["gpu_url"].as_str().unwrap_or("").to_string();
    if !gpu.is_empty() && !gpu_url.is_empty() && std::env::var_os("DEXMENTOR_LLAMA_SERVER").is_none() {
        let res = (|| -> Result<(), String> {
            set(&[("state", json!("tuning")), ("step", json!("Fetching the graphics-card runner"))]);
            if runtime_cmd("gpu").is_none() {
                let sha = MANIFEST["runtime"]["gpu_sha256"].as_str().unwrap_or("");
                unzip(&gpu_url, "runtime-gpu", "Downloading the graphics-card runner", sha)?;
            }
            set(&[("state", json!("tuning")), ("step", json!("Timing the processor"))]);
            acc["cpu_s"] = json!(bench("cpu"));
            set(&[("step", json!("Timing the graphics card"))]);
            let _ = fs::write(gpu_marker(), b"graphics card test running");
            log("graphics card test started");
            let g = bench("gpu");
            let _ = fs::remove_file(gpu_marker());
            log(&format!("graphics card test finished: {g:?} s"));
            acc["gpu_s"] = json!(g);
            if let (Some(g), c) = (g, acc["cpu_s"].as_f64()) {
                if c.is_none_or(|c| g < c * 0.9) {
                    acc["mode"] = json!("gpu");
                }
            }
            Ok(())
        })();
        if let Err(e) = res {
            acc["note"] = json!(e.chars().take(200).collect::<String>());
        }
    }
    set_accel(Some(acc));
    set(&[("state", json!("ready"))]);
    refresh_state();
}

/// Starts the runner if it is not answering yet. Called for every request to the model.
pub fn ensure_model() -> Result<(), String> {
    let mut runner = RUNNER.lock().unwrap();
    touch();
    if state() == "running" && runner.as_mut().is_some_and(|p| p.try_wait().ok().flatten().is_none()) {
        return Ok(());
    }
    if runtime_cmd("cpu").is_none() || !model_path().exists() {
        return Err("The language model is not downloaded yet.".into());
    }
    if state() == "tuning" {
        return Err("DexMentor Ask is still measuring this PC. Try again in a minute.".into());
    }
    if healthy() {
        // a runner left over from an earlier session is already answering: use it
        set(&[("state", json!("running")), ("error", json!(""))]);
        return Ok(());
    }
    // Loading the model with too little free memory makes the whole PC page to disk: refuse instead.
    let need = (MANIFEST["model"]["size_mb"].as_u64().unwrap_or(1000) + 512) << 20;
    let free = job::free_memory();
    if free > 0 && free < need {
        let msg = format!(
            "Only {} MB of memory is free and the model needs about {} MB. Close a program and ask again.",
            free >> 20,
            need >> 20
        );
        set(&[("state", json!("ready")), ("error", json!(msg))]);
        return Err(msg);
    }
    set(&[("state", json!("starting"))]);
    let mut mode = accel().get("mode").and_then(Value::as_str).unwrap_or("cpu").to_string();
    if mode == "gpu" {
        let _ = fs::write(gpu_marker(), b"graphics card in use");
    }
    let mut p = match start(&mode) {
        Ok(p) => p,
        Err(e) => {
            set(&[("state", json!("ready")), ("error", json!(e))]);
            return Err(e);
        }
    };
    log(&format!("model starting on the {mode}"));
    for _ in 0..240 {
        if p.try_wait().ok().flatten().is_some() {
            if mode == "gpu" {
                // the graphics driver refused: use the processor from now on
                let mut a = accel();
                a["mode"] = json!("cpu");
                a["note"] = json!("The graphics card could not run the model, so the processor is used.");
                set_accel(Some(a));
                let _ = fs::remove_file(gpu_marker());
                mode = "cpu".into();
                p = start(&mode)?;
                continue;
            }
            let msg = "The model runner stopped while starting (not enough free RAM?).".to_string();
            set(&[("state", json!("ready")), ("error", json!(msg))]);
            log(&msg);
            return Err(msg);
        }
        if healthy() {
            set(&[("state", json!("running")), ("error", json!(""))]);
            touch();
            *runner = Some(p);
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    let _ = p.kill();
    let _ = fs::remove_file(gpu_marker());
    set(&[("state", json!("ready"))]);
    Err("The model took too long to load.".into())
}

pub fn stop_model() {
    let mut runner = RUNNER.lock().unwrap();
    if let Some(mut p) = runner.take() {
        let _ = p.kill();
        let _ = p.wait();
        log("model stopped");
    }
    let _ = fs::remove_file(gpu_marker());
    if ["running", "starting"].contains(&state().as_str()) {
        set(&[("state", json!("ready"))]);
    }
}

fn idle_watch() {
    let idle = MANIFEST.get("idle_minutes").and_then(Value::as_u64).unwrap_or(10) * 60;
    loop {
        std::thread::sleep(Duration::from_secs(30));
        if state() == "running" && now().saturating_sub(LAST_USED.load(Ordering::Relaxed)) > idle {
            stop_model();
        }
    }
}
