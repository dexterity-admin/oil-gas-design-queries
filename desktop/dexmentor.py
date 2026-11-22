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
import urllib.error
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
         "system_suffix": MANIFEST["model"].get("system_suffix", ""), "version": MANIFEST.get("version", "dev")}
proc = None
last_used = 0.0
lock = threading.Lock()


def load_settings():
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        pass  # first run, or a damaged file: start from the defaults


def save_settings():
    tmp = SETTINGS_FILE.with_suffix(".tmp")  # write beside, then swap: a crash never leaves half a file
    tmp.write_text(json.dumps(settings))
    tmp.replace(SETTINGS_FILE)


DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # office-network calls never go through a web proxy


def server_ok(url=None):
    try:
        with DIRECT.open((url or settings["server_url"]).rstrip("/") + "/api/status", timeout=3) as r:
            return r.status == 200 and "version" in json.load(r)
    except Exception:
        return False


def _private(url):
    """Only addresses inside the office network are accepted from discovery."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(url.split("://", 1)[1].rsplit(":", 1)[0].strip("[]"))
        return ip.is_private or ip.is_loopback
    except (ValueError, IndexError):
        return False


def discover(wait=2.0):
    """Asks the office network "is a DexMentor Library running?" and returns the addresses that answer."""
    import socket
    found = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.4)
        targets = ["255.255.255.255", "127.0.0.1"]
        try:  # also the broadcast address of this PC's own network, which some routers need
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("10.255.255.255", 1))
            targets.append(probe.getsockname()[0].rsplit(".", 1)[0] + ".255")
            probe.close()
        except OSError:
            pass
        for t in targets:
            try:
                s.sendto(b"DEXMENTOR_DISCOVER_V1", (t, 8701))
            except OSError:
                pass
        end = time.time() + wait
        while time.time() < end:
            try:
                data, sender = s.recvfrom(600)
                url = json.loads(data).get("url", "")
                # The Library says where it thinks it is; the address the answer came from is tried too,
                # which is what works when the Library PC has several network adapters.
                for u in ("http://%s:%s" % (sender[0], url.rsplit(":", 1)[-1].strip("/")), url):
                    if u.startswith("http://") and _private(u) and u not in found:
                        found.append(u)
            except (OSError, ValueError):
                pass
    finally:
        s.close()
    return found


def auto_connect():
    """Keeps the saved Library if it answers; otherwise finds one on the network and remembers it."""
    if server_ok():
        return True
    for url in discover():
        if server_ok(url):
            settings["server_url"] = url
            save_settings()
            return True
    return False


def model_path():
    return DATA / "models" / MANIFEST["model"]["file"]


def runtime_cmd(kind="cpu"):
    """Command that starts the llama.cpp server, or None if it is not installed yet."""
    override = os.getenv("DEXMENTOR_LLAMA_SERVER")
    if override:
        return shlex.split(override)
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    found = sorted((DATA / ("runtime-gpu" if kind == "gpu" else "runtime")).rglob(name))
    return [str(found[0])] if found else None


def detect_gpu():
    """Name of a real graphics card worth testing (an old GeForce counts), or ''."""
    if os.getenv("DEXMENTOR_FORCE_GPU_TEST"):
        return "forced test"
    if os.name != "nt":
        return ""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_VideoController).Name"],
                             capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout
    except Exception:
        return ""
    for line in out.splitlines():
        if re.search(r"nvidia|geforce|quadro|radeon|\bamd\b|\barc\b", line, re.I):
            return line.strip()
    return ""


def refresh_state():
    if state["state"] in ("downloading", "tuning", "starting", "running"):
        return
    state["state"] = "ready" if model_path().exists() and runtime_cmd() else "missing"


# ---------- one-time download ----------
def _fetch(url, dest, step, sha256=""):
    """Download with resume and retries, so a dropped or stalled connection does not restart 1 GB."""
    import hashlib
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    last = None
    for attempt in range(8):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "DexMentor", **({"Range": "bytes=%d-" % have} if have else {})})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
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
            if not total or part.stat().st_size >= total:
                break
        except urllib.error.HTTPError as e:
            if e.code == 416:  # the server says there is nothing left to send: the file is complete
                break
            last = e
        except OSError as e:
            last = e
        time.sleep(min(20, 2 + attempt * 3))
    else:
        raise IOError("The download kept stopping (%s). Press download again to resume." % last)
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != sha256.lower():
            part.unlink()
            raise IOError("The downloaded file did not match its expected fingerprint and was deleted.")
    part.replace(dest)


def _unzip(url, folder, step, sha256=""):
    zip_path = DATA / folder / "runner.zip"
    _fetch(url, zip_path, step, sha256)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(DATA / folder)
    zip_path.unlink()


def _start(kind):
    n = os.cpu_count() or 4
    threads = n if n <= 4 else n // 2  # small CPUs have no spare threads to leave idle; big ones keep half free
    flags = 0x08000000 if os.name == "nt" else 0  # no console window on Windows
    cmd = runtime_cmd(kind) + ["-m", str(model_path()), "-c", str(MANIFEST["model"]["context"]),
                               "--host", "127.0.0.1", "--port", str(LLM_PORT), "-t", str(threads)]
    if kind == "gpu":
        cmd += ["-ngl", "99"]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


def _bench(kind):
    """Seconds this PC needs for one typical question with the CPU or the graphics card; None if it fails."""
    p = _start(kind)
    try:
        for _ in range(180):
            if p.poll() is not None:
                return None
            if _healthy():
                break
            time.sleep(0.5)
        else:
            return None
        body = json.dumps({"max_tokens": 48, "temperature": 0, "messages": [{"role": "user", "content":
               "SOURCES:\n" + "Carbon steel piping shall be supported at the spans given in the table. " * 45 +
               "\nQUESTION: Summarise the rule in one sentence."}]}).encode()
        t = time.time()
        c = http.client.HTTPConnection("127.0.0.1", LLM_PORT, timeout=300)
        c.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
        ok = c.getresponse().status == 200
        return round(time.time() - t, 1) if ok else None
    except OSError:
        return None
    finally:
        p.kill()
        p.wait()


def tune():
    """Old graphics cards are sometimes slower than the processor, so measure instead of guessing."""
    gpu = detect_gpu()
    accel = {"mode": "cpu", "gpu": gpu}
    if gpu and MANIFEST["runtime"].get("gpu_url") and not os.getenv("DEXMENTOR_LLAMA_SERVER"):
        try:
            state.update(state="tuning", step="Fetching the graphics-card runner")
            if not runtime_cmd("gpu"):
                _unzip(MANIFEST["runtime"]["gpu_url"], "runtime-gpu", "Downloading the graphics-card runner", MANIFEST["runtime"].get("gpu_sha256", ""))
            state.update(state="tuning", step="Timing the processor")
            accel["cpu_s"] = _bench("cpu")
            state.update(state="tuning", step="Timing the graphics card")
            accel["gpu_s"] = _bench("gpu")
            if accel["gpu_s"] and (not accel["cpu_s"] or accel["gpu_s"] < accel["cpu_s"] * 0.9):
                accel["mode"] = "gpu"
        except Exception as e:
            accel["note"] = str(e)[:200]
    settings["accel"] = accel
    save_settings()


def _install_runtime():
    # Only the pinned build DexMentor was tested with is ever downloaded and run.
    rt = MANIFEST["runtime"]
    _unzip(rt["url"], "runtime", "Downloading the model runner", rt.get("sha256", ""))


def download():
    with lock:
        if state["state"] == "downloading":
            return
        state.update(state="downloading", error="", done=0, total=0)
    try:
        if not runtime_cmd():
            _install_runtime()
        if not model_path().exists():
            _fetch(MANIFEST["model"]["url"], model_path(), "Downloading the language model", MANIFEST["model"].get("sha256", ""))
        if "accel" not in settings:
            tune()
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
        if state["state"] == "tuning":
            raise RuntimeError("DexMentor is still measuring this PC. Try again in a minute.")
        if _healthy():  # a runner left over from an earlier session is already answering: use it
            state.update(state="running", error="")
            return
        state["state"] = "starting"
        mode = (settings.get("accel") or {}).get("mode", "cpu")
        proc = _start(mode)
        for _ in range(240):
            if proc.poll() is not None and mode == "gpu":  # graphics driver refused: use the processor from now on
                settings["accel"]["mode"] = mode = "cpu"
                save_settings()
                proc = _start(mode)
                continue
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
HOP = {"host", "connection", "content-length", "transfer-encoding", "accept-encoding", "keep-alive", "origin", "referer"}
MINE = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)


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
        except http.client.HTTPException:  # the far end died part-way through its reply
            try:
                self.wfile.write(b'\ndata: {"error": "The answer was cut off."}\n\n')
            except OSError:
                pass
        except OSError as e:
            try:
                self._json({"error": "Could not reach %s (%s)" % (base, e)}, 502)
            except OSError:
                pass
        finally:
            conn.close()

    def handle_any(self):
        # Only this PC's own DexMentor window may talk to the launcher: a web page open in a browser must not be
        # able to repoint it, start downloads or read answers.
        origin = self.headers.get("Origin")
        if self.headers.get("Host") not in MINE or (origin and origin.split("://", 1)[-1] not in MINE):
            return self._json({"error": "Not allowed."}, 403)
        if self.path.startswith("/local/") and self.path != "/local/status" and self.command != "POST":
            return self._json({"error": "Not allowed."}, 405)
        body = self._body()
        if self.path == "/local/status":
            refresh_state()
            return self._json({**state, "server_url": settings["server_url"], "accel": settings.get("accel")})
        if self.path == "/local/discover":
            return self._json({"connected": auto_connect(), "server_url": settings["server_url"]})
        if self.path == "/local/download":
            threading.Thread(target=download, daemon=True).start()
            return self._json({"ok": True})
        if self.path == "/local/retune":
            stop_model()
            settings.pop("accel", None)
            threading.Thread(target=lambda: (tune(), state.update(state="ready")), daemon=True).start()
            return self._json({"ok": True})
        if self.path == "/local/unload":
            stop_model()
            return self._json({"ok": True})
        if self.path == "/local/settings":
            url = str(json.loads(body or b"{}").get("server_url", "")).strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                return self._json({"error": "The address must start with http:// or https://"}, 400)
            settings["server_url"] = url
            save_settings()
            return self._json({"ok": True})
        if self.path.startswith("/llm/"):
            try:
                ensure_model()
            except RuntimeError as e:
                return self._json({"error": str(e)}, 503)
            return self._proxy("http://127.0.0.1:%d" % LLM_PORT, self.path[4:], body)
        return self._proxy(settings["server_url"], self.path, body)

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


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
    threading.Thread(target=auto_connect, daemon=True).start()
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
