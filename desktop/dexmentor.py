"""DexMentor desktop launcher (standard library only, so the packaged .exe stays tiny).

What it does on the engineer's PC:
  1. Opens the DexMentor window (the UI itself is served by the office server).
  2. On request, downloads the small language model and the llama.cpp CPU runner once.
  3. Runs the model only while it is being used, and unloads it after a few idle minutes
     so the RAM goes back to the design software.

Everything is reached through one local address, so the UI never needs to know where
the office server or the model live:
  /local/*  -> this launcher (status, download, settings)
  /llm/*    -> the model on this PC
  the rest  -> the office server
"""
import http.client
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA = Path(os.getenv("DEXMENTOR_HOME") or Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local/share") / "DexMentor")
PORT = int(os.getenv("DEXMENTOR_PORT", "8720"))
LLM_PORT = int(os.getenv("DEXMENTOR_LLM_PORT", "8719"))
MANIFEST = json.loads(Path(os.getenv("DEXMENTOR_MANIFEST") or HERE / "models.json").read_text())
SETTINGS_FILE = DATA / "settings.json"

settings = {"server_url": os.getenv("DEXMENTOR_SERVER", "http://127.0.0.1:8700")}
state = {"state": "missing", "done": 0, "total": 0, "step": "", "error": "",
         "model": MANIFEST["model"]["name"], "size_mb": MANIFEST["model"]["size_mb"],
         "system_suffix": MANIFEST["model"].get("system_suffix", "")}
proc = None
last_used = 0.0
lock = threading.Lock()


def load_settings():
    DATA.mkdir(parents=True, exist_ok=True)
    if SETTINGS_FILE.exists():
        settings.update(json.loads(SETTINGS_FILE.read_text()))


def model_path():
    return DATA / "models" / MANIFEST["model"]["file"]


def runtime_cmd():
    """Command that starts the llama.cpp server, or None if it is not installed yet."""
    override = os.getenv("DEXMENTOR_LLAMA_SERVER")
    if override:
        return shlex.split(override)
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    found = sorted((DATA / "runtime").rglob(name))
    return [str(found[0])] if found else None


def refresh_state():
    if state["state"] in ("downloading", "starting", "running"):
        return
    state["state"] = "ready" if model_path().exists() and runtime_cmd() else "missing"


# ---------- one-time download ----------
def _fetch(url, dest, step):
    """Download with resume, so a dropped office connection does not restart 1 GB."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "DexMentor", **({"Range": "bytes=%d-" % have} if have else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        state.update(step=step, done=have, total=total)
        with open(part, "ab" if have else "wb") as f:
            while True:
                buf = r.read(1 << 18)
                if not buf:
                    break
                f.write(buf)
                state["done"] += len(buf)
    if total and part.stat().st_size < total:
        raise IOError("The download stopped early. Press download again to resume.")
    part.replace(dest)


def _latest_runtime_url():
    rt = MANIFEST["runtime"]
    api = "https://api.github.com/repos/%s/releases?per_page=15" % rt["github_repo"]
    with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "DexMentor"}), timeout=30) as r:
        releases = json.load(r)
    match = [a for rel in releases for a in rel.get("assets", []) if re.search(rt["asset_pattern"], a["name"])]
    if not match:
        raise IOError("No CPU build of the model runner was found in the recent llama.cpp releases.")
    return match[0]["browser_download_url"]


def _install_runtime():
    # The pinned build is the one DexMentor was tested with and needs no API lookup (GitHub
    # rate-limits lookups per office IP). The lookup is only a fallback if that file is gone.
    zip_path = DATA / "runtime" / "runner.zip"
    try:
        _fetch(MANIFEST["runtime"]["url"], zip_path, "Downloading the model runner")
    except (OSError, KeyError):
        _fetch(_latest_runtime_url(), zip_path, "Downloading the model runner")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(DATA / "runtime")
    zip_path.unlink()


def download():
    with lock:
        if state["state"] == "downloading":
            return
        state.update(state="downloading", error="", done=0, total=0)
    try:
        if not runtime_cmd():
            _install_runtime()
        if not model_path().exists():
            _fetch(MANIFEST["model"]["url"], model_path(), "Downloading the language model")
        state["state"] = "ready"
    except Exception as e:
        state.update(state="missing", error=str(e))


# ---------- run the model only while it is needed ----------
def _healthy():
    try:
        c = http.client.HTTPConnection("127.0.0.1", LLM_PORT, timeout=2)
        c.request("GET", "/health")
        return c.getresponse().status == 200
    except OSError:
        return False


def ensure_model():
    global proc, last_used
    with lock:
        last_used = time.time()
        if proc and proc.poll() is None and state["state"] == "running":
            return
        cmd = runtime_cmd()
        if not cmd or not model_path().exists():
            raise RuntimeError("The language model is not downloaded yet.")
        state["state"] = "starting"
        threads = max(1, (os.cpu_count() or 4) // 2)  # physical cores; leaves room for other software
        flags = 0x08000000 if os.name == "nt" else 0  # no console window on Windows
        proc = subprocess.Popen(cmd + ["-m", str(model_path()), "-c", str(MANIFEST["model"]["context"]),
                                       "--host", "127.0.0.1", "--port", str(LLM_PORT), "-t", str(threads)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(240):
            if proc.poll() is not None:
                state.update(state="ready", error="The model runner stopped while starting (not enough free RAM?).")
                raise RuntimeError(state["error"])
            if _healthy():
                state.update(state="running", error="")
                last_used = time.time()
                return
            time.sleep(0.5)
        proc.kill()
        state["state"] = "ready"
        raise RuntimeError("The model took too long to load.")


def stop_model():
    global proc
    with lock:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        proc = None
        if state["state"] in ("running", "starting"):
            state["state"] = "ready"


def idle_watch():
    while True:
        time.sleep(30)
        if state["state"] == "running" and time.time() - last_used > MANIFEST.get("idle_minutes", 10) * 60:
            stop_model()


# ---------- local web endpoint ----------
HOP = {"host", "connection", "content-length", "transfer-encoding", "accept-encoding", "keep-alive"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _proxy(self, base, path, body):
        global last_used
        u = urlsplit(base)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.netloc, timeout=600)
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            conn.request(self.command, u.path.rstrip("/") + path, body=body or None, headers=headers)
            r = conn.getresponse()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                buf = r.read1(65536)
                if not buf:
                    break
                self.wfile.write(buf)
                self.wfile.flush()
                last_used = time.time()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as e:
            try:
                self._json({"error": "Could not reach %s (%s)" % (base, e)}, 502)
            except OSError:
                pass
        finally:
            conn.close()

    def handle_any(self):
        body = self._body()
        if self.path == "/local/status":
            refresh_state()
            return self._json({**state, "server_url": settings["server_url"]})
        if self.path == "/local/download":
            threading.Thread(target=download, daemon=True).start()
            return self._json({"ok": True})
        if self.path == "/local/unload":
            stop_model()
            return self._json({"ok": True})
        if self.path == "/local/settings":
            url = str(json.loads(body or b"{}").get("server_url", "")).strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                return self._json({"error": "The address must start with http:// or https://"}, 400)
            settings["server_url"] = url
            SETTINGS_FILE.write_text(json.dumps(settings))
            return self._json({"ok": True})
        if self.path.startswith("/llm/"):
            try:
                ensure_model()
            except RuntimeError as e:
                return self._json({"error": str(e)}, 503)
            return self._proxy("http://127.0.0.1:%d" % LLM_PORT, self.path[4:], body)
        return self._proxy(settings["server_url"], self.path, body)

    do_GET = do_POST = handle_any


def open_window(url):
    """A plain app window using the Edge or Chrome already on the PC: nothing extra to install."""
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(exe):
            subprocess.Popen([exe, "--app=" + url, "--window-size=1280,820"])
            return
    webbrowser.open(url)


def main():
    load_settings()
    refresh_state()
    threading.Thread(target=idle_watch, daemon=True).start()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already running: just show the window again
        open_window("http://127.0.0.1:%d/" % PORT)
        return
    if "--no-window" not in sys.argv:
        open_window("http://127.0.0.1:%d/" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_model()


if __name__ == "__main__":
    main()
