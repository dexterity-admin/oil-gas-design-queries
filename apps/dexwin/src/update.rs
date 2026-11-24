//! In-app updates from the GitHub releases of this repository (the same scheme as Sleuth).
//!
//! Every release carries `latest.json`: the version, the notes, and for each program (`ask`, `library`) the
//! installer's address and SHA-256, plus the release before it (for going back). The app checks a minute and a
//! half after it starts and then every hour, downloads a newer installer in the background (resuming after a
//! break), checks it against the SHA-256 and only then offers "Restart and update". Installing stops the app's
//! background processes (the model runner, the Library engine), starts the installer in update mode (a progress
//! window only) and closes the app; the installer replaces the files and starts the app again.
//!
//! Edge cases: no network or GitHub down (tried again next hour, nothing shown), a damaged or tampered download
//! (deleted, downloaded again), a download cut off (resumed), a full disk (error shown), two checks at once (one
//! runs), a local build ("dev": never updated), an update that did not finish (noticed at the next start and
//! offered again), installers of older versions (deleted). Settings, models, documents and saved chats live in
//! %LOCALAPPDATA% and are never touched by the installer.
use std::fs;
use std::io::{Read, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

pub const FEED: &str = "https://github.com/dexterity-admin/oil-gas-design-queries/releases/latest/download/latest.json";

pub struct Updater {
    /// "ask" or "library": which installer in latest.json is ours
    app: &'static str,
    current: &'static str,
    dir: PathBuf,
    status: Mutex<Value>,
    busy: Mutex<()>,
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// "1.0.57" -> [1, 0, 57]. Anything that is not a number counts as 0.
fn parts(v: &str) -> Vec<u64> {
    v.trim_start_matches('v').split('.').map(|p| p.trim().parse().unwrap_or(0)).collect()
}

pub fn newer(a: &str, b: &str) -> bool {
    parts(a) > parts(b)
}

fn sha256_of(path: &Path) -> Option<String> {
    use sha2::{Digest, Sha256};
    let mut f = fs::File::open(path).ok()?;
    let mut h = Sha256::new();
    std::io::copy(&mut f, &mut h).ok()?;
    Some(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

fn client() -> reqwest::blocking::Client {
    reqwest::blocking::Client::builder()
        .user_agent("DexMentor")
        .timeout(Duration::from_secs(60))
        .connect_timeout(Duration::from_secs(20))
        .build()
        .expect("http client")
}

impl Updater {
    /// `dir` is where downloaded installers are kept (in the user's own AppData, not the shared temp folder).
    pub fn new(app: &'static str, current: &'static str, dir: PathBuf) -> Updater {
        let u = Updater { app, current, dir, status: Mutex::new(json!({"state": "idle", "current": current})), busy: Mutex::new(()) };
        u.after_start();
        u
    }

    pub fn status(&self) -> Value {
        self.status.lock().unwrap().clone()
    }

    fn set(&self, pairs: &[(&str, Value)]) {
        let mut s = self.status.lock().unwrap();
        for (k, v) in pairs {
            s[*k] = v.clone();
        }
    }

    fn enabled(&self) -> bool {
        self.current != "dev" || std::env::var_os("DEXMENTOR_UPDATE_FEED").is_some()
    }

    fn feed(&self) -> Result<Value, String> {
        let src = std::env::var("DEXMENTOR_UPDATE_FEED").unwrap_or_else(|_| FEED.into());
        let body = if src.starts_with("http") {
            client().get(&src).send().and_then(|r| r.error_for_status()).and_then(|r| r.text()).map_err(|e| format!("Could not check for updates ({e})."))?
        } else {
            fs::read_to_string(&src).map_err(|e| format!("Could not read {src} ({e})."))?
        };
        serde_json::from_str(&body).map_err(|e| format!("The update information is not valid ({e})."))
    }

    /// Ours from a release entry: (version, url, sha256).
    fn pick(&self, rel: &Value) -> Option<(String, String, String)> {
        let v = rel["version"].as_str()?;
        let a = &rel[self.app];
        Some((v.into(), a["url"].as_str()?.into(), a["sha256"].as_str()?.trim().to_lowercase()))
    }

    fn marker(&self) -> PathBuf {
        self.dir.join("update-attempt.json")
    }

    /// After an update: was it installed, or did it not finish?
    fn after_start(&self) {
        let Ok(b) = fs::read(self.marker()) else { return };
        let _ = fs::remove_file(self.marker());
        let Ok(m) = serde_json::from_slice::<Value>(&b) else { return };
        let to = m["to"].as_str().unwrap_or("");
        if to == self.current {
            self.set(&[("updated", json!(to))]);
        } else if m["from"].as_str() == Some(self.current) {
            self.set(&[("failed", json!(to))]);
        }
    }

    /// Checks the feed and, when there is a newer release, downloads and verifies it. Returns the status.
    pub fn check(&self) -> Value {
        if !self.enabled() {
            self.set(&[("state", json!("current")), ("note", json!("A local build is not updated."))]);
            return self.status();
        }
        let Ok(_one) = self.busy.try_lock() else { return self.status() };
        if self.status()["state"] == "ready" {
            return self.status();
        }
        self.set(&[("state", json!("checking")), ("error", json!(""))]);
        let res = (|| -> Result<Value, String> {
            let feed = self.feed()?;
            let prev = feed.get("previous").and_then(|p| self.pick(p)).filter(|p| newer(self.current, &p.0));
            self.set(&[("previous", json!(prev.as_ref().map(|p| &p.0)))]);
            let (version, url, sha) = self.pick(&feed).ok_or("The update information has no installer for this program.")?;
            if !newer(&version, self.current) {
                self.set(&[("state", json!("current"))]);
                return Ok(self.status());
            }
            self.set(&[("notes", feed["notes"].clone())]);
            self.download(&version, &url, &sha)?;
            self.set(&[("state", json!("ready")), ("version", json!(version))]);
            Ok(self.status())
        })();
        self.set(&[("checked", json!(now()))]);
        res.unwrap_or_else(|e| {
            self.set(&[("state", json!("error")), ("error", json!(e))]);
            self.status()
        })
    }

    fn installer(&self, version: &str) -> PathBuf {
        self.dir.join(format!("DexMentor-{}-{version}-Setup.exe", self.app))
    }

    fn download(&self, version: &str, url: &str, sha: &str) -> Result<PathBuf, String> {
        fs::create_dir_all(&self.dir).map_err(|e| e.to_string())?;
        let file = self.installer(version);
        let part = file.with_extension("part");
        for e in fs::read_dir(&self.dir).into_iter().flatten().flatten() {
            let p = e.path(); // installers of other versions are no longer needed
            if p != file && p != part && p.extension().is_some_and(|x| x == "exe" || x == "part") {
                let _ = fs::remove_file(p);
            }
        }
        if file.is_file() && sha256_of(&file).as_deref() == Some(sha) {
            return Ok(file); // downloaded earlier
        }
        let _ = fs::remove_file(&file);
        self.set(&[("state", json!("downloading")), ("version", json!(version)), ("done", json!(0)), ("total", json!(0))]);
        let http = client();
        for attempt in 0..5u64 {
            let last;
            let have = fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
            let mut req = http.get(url);
            if have > 0 {
                req = req.header("Range", format!("bytes={have}-"));
            }
            let r = req.send().map_err(|e| e.to_string()).and_then(|r| r.error_for_status().map_err(|e| e.to_string()));
            match r {
                Ok(mut r) => {
                    let resumed = have > 0 && r.status().as_u16() == 206;
                    let start = if resumed { have } else { 0 };
                    let total = start + r.content_length().unwrap_or(0);
                    let mut f = if resumed { fs::OpenOptions::new().append(true).open(&part) } else { fs::File::create(&part) }
                        .map_err(|e| format!("Could not save the update ({e})."))?;
                    let mut buf = vec![0u8; 1 << 18];
                    let mut done = start;
                    let res = loop {
                        match r.read(&mut buf) {
                            Ok(0) => break Ok(()),
                            Ok(n) => {
                                f.write_all(&buf[..n]).map_err(|e| format!("Could not save the update ({e}). Is the disk full?"))?;
                                done += n as u64;
                                self.set(&[("done", json!(done)), ("total", json!(total))]);
                            }
                            Err(e) => break Err(e.to_string()),
                        }
                    };
                    drop(f);
                    if res.is_ok() {
                        break;
                    }
                    last = res.unwrap_err();
                }
                Err(e) => last = e,
            }
            if attempt == 4 {
                return Err(format!("The update download kept stopping ({last}). It is tried again later."));
            }
            std::thread::sleep(Duration::from_secs(if cfg!(test) { 0 } else { 5 + attempt * 10 }));
        }
        if sha256_of(&part).as_deref() != Some(sha) {
            let _ = fs::remove_file(&part);
            return Err("The downloaded update did not match its fingerprint and was deleted. It is downloaded again later.".into());
        }
        fs::rename(&part, &file).map_err(|e| e.to_string())?;
        Ok(file)
    }

    /// Starts the verified installer in update mode. The caller has stopped its background processes and must
    /// close the app straight after; the installer waits for it, replaces the files and starts it again.
    /// `back`: install the release before this one instead (downloaded and checked the same way).
    /// `quiet_start`: after installing, start the app without a window (the Library's night update).
    pub fn install(&self, back: bool, quiet_start: bool) -> Result<(), String> {
        let (version, url, sha) = if back {
            let feed = self.feed()?;
            feed.get("previous").and_then(|p| self.pick(p)).filter(|p| newer(self.current, &p.0)).ok_or("There is no earlier version to go back to.")?
        } else {
            let st = self.status();
            if st["state"] != "ready" {
                return Err("No update is ready yet.".into());
            }
            let feed = self.feed().ok();
            match feed.as_ref().and_then(|f| self.pick(f)) {
                Some(p) => p,
                None => (st["version"].as_str().unwrap_or("").into(), String::new(), String::new()),
            }
        };
        let file = if url.is_empty() {
            // offline now, but the installer was downloaded and checked before
            let f = self.installer(&version);
            if !f.is_file() {
                return Err("The update is no longer on this PC. It is downloaded again at the next check.".into());
            }
            f
        } else {
            let _one = self.busy.lock().unwrap();
            self.download(&version, &url, &sha)?
        };
        let _ = fs::write(self.marker(), serde_json::to_vec(&json!({"from": self.current, "to": version, "at": now()})).unwrap_or_default());
        self.set(&[("state", json!("installing")), ("version", json!(version))]);
        std::process::Command::new(&file)
            .args(["/SILENT", "/NORESTART", "/UPDATE=1"])
            .args(quiet_start.then_some("/QUIETSTART=1"))
            .creation_flags(0x0000_0008 | 0x0000_0200) // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: outlives the app
            .spawn()
            .map(|_| ())
            .map_err(|e| {
                let _ = fs::remove_file(self.marker());
                self.set(&[("state", json!("error")), ("error", json!(format!("The installer could not start ({e}).")))]);
                format!("The installer could not start ({e}).")
            })
    }

    /// The background check: a minute and a half after the start, then every hour.
    pub fn run_checks(&'static self, on_ready: impl Fn() + Send + 'static) {
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(90));
            loop {
                if self.check()["state"] == "ready" {
                    on_ready();
                }
                std::thread::sleep(Duration::from_secs(3600));
            }
        });
    }
}

/// The local hour (0-23), for installing a ready Library update at night.
pub fn local_hour() -> u16 {
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;
    unsafe {
        let mut t = std::mem::zeroed();
        GetLocalTime(&mut t);
        t.wHour
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions() {
        assert!(newer("1.0.58", "1.0.57"));
        assert!(newer("1.0.100", "1.0.99"));
        assert!(newer("v2.0.0", "1.9.9"));
        assert!(!newer("1.0.57", "1.0.57"));
        assert!(!newer("1.0.9", "1.0.10"));
    }

    #[test]
    fn checks_downloads_and_verifies() {
        let d = std::env::temp_dir().join(format!("dexwin-update-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        let payload = d.join("payload.exe");
        fs::write(&payload, b"installer bytes").unwrap();
        let sha = sha256_of(&payload).unwrap();
        let url = format!("file:///{}", payload.display()); // not http: exercised through download() only below
        let feed = d.join("latest.json");
        fs::write(&feed, serde_json::to_vec(&json!({"version": "1.0.9", "notes": "n",
            "ask": {"url": url, "sha256": sha}, "previous": {"version": "1.0.4", "ask": {"url": "x", "sha256": "y"}}})).unwrap()).unwrap();
        std::env::set_var("DEXMENTOR_UPDATE_FEED", &feed);
        let u = Updater::new("ask", "1.0.5", d.join("updates"));
        let f = u.feed().unwrap();
        assert_eq!(u.pick(&f).unwrap().0, "1.0.9");
        assert_eq!(u.pick(&f["previous"]).unwrap().0, "1.0.4");
        // a verified copy already on disk is reused; a wrong one is refused
        fs::create_dir_all(d.join("updates")).unwrap();
        fs::copy(&payload, u.installer("1.0.9")).unwrap();
        assert!(u.download("1.0.9", "http://127.0.0.1:9/never", &sha).is_ok());
        fs::write(u.installer("1.0.9"), b"tampered").unwrap();
        assert!(u.download("1.0.9", "http://127.0.0.1:9/never", &sha).is_err());
        // an update that did not finish is noticed at the next start
        fs::write(u.marker(), br#"{"from": "1.0.5", "to": "1.0.9"}"#).unwrap();
        let again = Updater::new("ask", "1.0.5", d.join("updates"));
        assert_eq!(again.status()["failed"], "1.0.9");
        fs::write(u.marker(), br#"{"from": "1.0.5", "to": "1.0.9"}"#).unwrap();
        let done = Updater::new("ask", "1.0.9", d.join("updates"));
        assert_eq!(done.status()["updated"], "1.0.9");
        std::env::remove_var("DEXMENTOR_UPDATE_FEED");
        let _ = fs::remove_dir_all(d);
    }
}
