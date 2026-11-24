//! Saved chats, kept on disk in the Windows user's own AppData folder.
//!
//! The file is written safely: the new copy goes to a temporary file that is flushed to the disk, then it
//! replaces the old file in one step, and the previous copy is kept as `.bak`. A power cut or a forced
//! restart in the middle of a save leaves either the old or the new history, never half a file.
use std::fs;
use std::io::{self, Write};
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

fn wide(p: &Path) -> Vec<u16> {
    p.as_os_str().encode_wide().chain(Some(0)).collect()
}

fn sibling(path: &Path, suffix: &str) -> PathBuf {
    let mut s = path.as_os_str().to_owned();
    s.push(suffix);
    PathBuf::from(s)
}

/// Writes `data` to `path` so that a crash at any moment leaves the old or the new content.
pub fn write_atomic(path: &Path, data: &[u8]) -> io::Result<()> {
    use windows_sys::Win32::Storage::FileSystem::{MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH};
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    let tmp = sibling(path, ".tmp");
    {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(data)?;
        f.sync_all()?; // on the disk, not only in the Windows cache
    }
    if path.exists() {
        let _ = fs::copy(path, sibling(path, ".bak")); // one step back, in case the newest copy is ever unreadable
    }
    let ok = unsafe { MoveFileExW(wide(&tmp).as_ptr(), wide(path).as_ptr(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) };
    if ok == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn empty() -> Value {
    json!({"chats": [], "current": null})
}

fn valid(v: &Value) -> bool {
    v.get("chats").is_some_and(Value::is_array)
}

/// The saved history: the file, else its previous copy, else nothing.
pub fn load(path: &Path) -> Value {
    for p in [path.to_path_buf(), sibling(path, ".bak")] {
        if let Ok(v) = fs::read(&p).map_err(|_| ()).and_then(|b| serde_json::from_slice::<Value>(&b).map_err(|_| ())) {
            if valid(&v) {
                return v;
            }
        }
    }
    empty()
}

/// Saves the history the window sent. Refuses anything that is not a history.
pub fn save(path: &Path, v: &Value) -> io::Result<()> {
    if !valid(v) {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "not a chat history"));
    }
    write_atomic(path, serde_json::to_string(v)?.as_bytes())
}

/// Adds chats to a history: a chat already there is kept unless the other copy was updated later.
pub fn merge(into: &mut Value, more: &[Value]) -> usize {
    let list = into["chats"].as_array_mut().expect("valid history");
    let mut added = 0;
    for c in more {
        let Some(id) = c.get("id").and_then(Value::as_str) else { continue };
        let upd = |x: &Value| x.get("updated").and_then(Value::as_f64).unwrap_or(0.0);
        match list.iter_mut().find(|x| x.get("id").and_then(Value::as_str) == Some(id)) {
            Some(have) if upd(have) >= upd(c) => {}
            Some(have) => *have = c.clone(),
            None => {
                list.push(c.clone());
                added += 1;
            }
        }
    }
    list.sort_by(|a, b| {
        let u = |x: &Value| x.get("updated").and_then(Value::as_f64).unwrap_or(0.0);
        u(b).partial_cmp(&u(a)).unwrap_or(std::cmp::Ordering::Equal)
    });
    added
}

// ---------- one-time import from the browser the old app windows ran in ----------

/// Chromium stores a string as 0x01 + Latin-1 bytes, or 0x00 + UTF-16LE.
fn chromium_str(b: &[u8]) -> Option<String> {
    match b.split_first()? {
        (1, rest) => Some(rest.iter().map(|&c| c as char).collect()),
        (0, rest) => {
            let u: Vec<u16> = rest.chunks_exact(2).map(|p| u16::from_le_bytes([p[0], p[1]])).collect();
            String::from_utf16(&u).ok()
        }
        _ => None,
    }
}

/// Reads one browser profile's local storage (a copy of it, so the browser's own files are never touched)
/// and returns the `chats` and `current` values saved by any of `origins`.
fn read_profile(leveldb: &Path, origins: &[&str], scratch: &Path) -> Vec<(String, String)> {
    use rusty_leveldb::{LdbIterator, Options, DB};
    let _ = fs::remove_dir_all(scratch);
    if fs::create_dir_all(scratch).is_err() {
        return vec![];
    }
    if let Ok(rd) = fs::read_dir(leveldb) {
        for e in rd.flatten() {
            if e.file_name() != "LOCK" && e.path().is_file() {
                let _ = fs::copy(e.path(), scratch.join(e.file_name()));
            }
        }
    }
    let mut out = vec![];
    let found = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let mut opt = Options::default();
        opt.create_if_missing = false;
        let mut db = DB::open(scratch, opt).ok()?;
        let mut it = db.new_iter().ok()?;
        let mut got = vec![];
        while let Some((k, v)) = it.next() {
            for o in origins {
                let head = format!("_{o}\0");
                if let Some(rest) = k.strip_prefix(head.as_bytes()) {
                    if let (Some(key), Some(val)) = (chromium_str(rest), chromium_str(&v)) {
                        if key == "chats" || key == "current" {
                            got.push((key, val));
                        }
                    }
                }
            }
        }
        Some(got)
    }));
    if let Ok(Some(got)) = found {
        out = got;
    }
    let _ = fs::remove_dir_all(scratch);
    out
}

/// Chats the old DexMentor windows (Edge or Chrome "app" windows at one of `origins`) left in the browser's
/// storage, from every profile. Returns the chats and the one that was open last.
pub fn import_from_browsers(origins: &[&str], scratch: &Path) -> (Vec<Value>, Option<String>) {
    let Some(local) = std::env::var_os("LOCALAPPDATA").map(PathBuf::from) else { return (vec![], None) };
    let mut chats: Vec<Value> = vec![];
    let mut current = None;
    for base in [local.join("Microsoft/Edge/User Data"), local.join("Google/Chrome/User Data")] {
        let Ok(profiles) = fs::read_dir(&base) else { continue };
        for p in profiles.flatten() {
            let ldb = p.path().join("Local Storage/leveldb");
            if !ldb.is_dir() {
                continue;
            }
            for (k, v) in read_profile(&ldb, origins, scratch) {
                if k == "current" {
                    current = serde_json::from_str::<Value>(&v).ok().and_then(|c| c.as_str().map(String::from));
                } else if let Ok(Value::Array(list)) = serde_json::from_str::<Value>(&v) {
                    let mut tmp = json!({"chats": chats});
                    merge(&mut tmp, &list);
                    chats = tmp["chats"].as_array().cloned().unwrap_or_default();
                }
            }
        }
    }
    (chats, current)
}

/// Once per user: copies the old browser-kept chats into the history file, keeping what is already there.
/// Returns how many chats were added.
pub fn import_once(path: &Path, origins: &[&str]) -> usize {
    let marker = sibling(path, ".imported");
    if marker.exists() {
        return 0;
    }
    let _ = fs::create_dir_all(path.parent().unwrap_or(Path::new(".")));
    let _ = fs::write(&marker, b"browser chats were looked for once"); // first: a bad browser file can never block every start
    let scratch = std::env::temp_dir().join(format!("dexmentor-import-{}", std::process::id()));
    let (old, current) = import_from_browsers(origins, &scratch);
    if old.is_empty() {
        return 0;
    }
    let mut have = load(path);
    let added = merge(&mut have, &old);
    if have["current"].is_null() {
        if let Some(c) = current {
            have["current"] = Value::String(c);
        }
    }
    if added > 0 && save(path, &have).is_err() {
        let _ = fs::remove_file(&marker); // try again next time
        return 0;
    }
    added
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("dexwin-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn saves_and_loads_with_backup() {
        let d = tmp("save");
        let f = d.join("chats.json");
        assert_eq!(load(&f), empty());
        let one = json!({"chats": [{"id": "c1", "updated": 1, "messages": []}], "current": "c1"});
        save(&f, &one).unwrap();
        assert_eq!(load(&f), one);
        let two = json!({"chats": [{"id": "c2", "updated": 2, "messages": []}], "current": "c2"});
        save(&f, &two).unwrap();
        assert_eq!(load(&f), two);
        fs::write(&f, b"{half a fi").unwrap(); // a damaged file falls back to the previous copy
        assert_eq!(load(&f), one);
        assert!(save(&f, &json!({"nope": 1})).is_err());
        assert!(!sibling(&f, ".tmp").exists());
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn merge_keeps_newest() {
        let mut h = json!({"chats": [{"id": "a", "updated": 5, "title": "old"}], "current": null});
        let n = merge(&mut h, &[json!({"id": "a", "updated": 9, "title": "new"}), json!({"id": "b", "updated": 7})]);
        assert_eq!(n, 1);
        assert_eq!(h["chats"][0]["title"], "new");
        assert_eq!(h["chats"][1]["id"], "b");
        merge(&mut h, &[json!({"id": "a", "updated": 1, "title": "older"})]);
        assert_eq!(h["chats"][0]["title"], "new");
    }

    #[test]
    fn reads_chromium_local_storage() {
        use rusty_leveldb::{Options, DB};
        let d = tmp("ldb");
        let src = d.join("leveldb");
        {
            let mut db = DB::open(&src, Options::default()).unwrap();
            let mut key = b"_http://127.0.0.1:8720\0\x01".to_vec();
            key.extend_from_slice(b"chats");
            let text = r#"[{"id":"c9","updated":3,"title":"Flange ₹ test"}]"#;
            let mut val = vec![0u8];
            for u in text.encode_utf16() {
                val.extend_from_slice(&u.to_le_bytes());
            }
            db.put(&key, &val).unwrap();
            db.put(b"_http://other:1\0\x01chats", b"\x01[]").unwrap();
            db.flush().unwrap();
        }
        let got = read_profile(&src, &["http://127.0.0.1:8720"], &d.join("scratch"));
        assert_eq!(got.len(), 1);
        assert!(got[0].1.contains("Flange ₹ test"));
        fs::remove_dir_all(d).unwrap();
    }
}
