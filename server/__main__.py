"""Starts the DexMentor Library server. Packaged as "DexMentor Library" for the office server PC."""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from . import config


def lan_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def open_desk():
    """Opens the Library desk in its own window, using the Edge or Chrome already on the PC."""
    url = "http://127.0.0.1:%d/#desk" % config.PORT
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(exe):
            try:  # the packaged Library runs under a CPU cap (desktop/winlimits.py); its window must not
                from winlimits import popen_outside
            except ImportError:
                popen_outside = subprocess.Popen
            popen_outside([exe, "--app=" + url, "--window-size=1360,860"])
            return
    webbrowser.open(url)


def already_running():
    try:
        with socket.create_connection(("127.0.0.1", config.PORT), timeout=1):
            return True
    except OSError:
        return False


def main():
    if already_running():  # started a second time (for example from the Start menu): just show the desk
        if "--no-window" not in sys.argv:
            open_desk()
        return
    if config.FROZEN:  # a windowed program has no console: keep a log file next to the data instead
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(config.DATA_DIR / "server.log", "a", buffering=1, encoding="utf-8")
    if os.name == "nt":  # a sleeping server PC answers nobody: keep Windows awake while the Library runs
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        except Exception:
            pass
    print("%s Library: http://%s:%d   documents: %s" % (config.APP_NAME, lan_address(), config.PORT, config.LIBRARY_DIR))
    if config.FROZEN and "--no-window" not in sys.argv:
        threading.Thread(target=lambda: (time.sleep(2.5), open_desk()), daemon=True).start()
    from .app import app
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_config=None if config.FROZEN else uvicorn.config.LOGGING_CONFIG)


if __name__ == "__main__":
    main()
