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
    time.sleep(2.5)
    url = "http://127.0.0.1:%d/#desk" % config.PORT
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(exe):
            subprocess.Popen([exe, "--app=" + url, "--window-size=1360,860"])
            return
    webbrowser.open(url)


def main():
    if config.FROZEN:  # a windowed program has no console: keep a log file next to the data instead
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(config.DATA_DIR / "server.log", "a", buffering=1, encoding="utf-8")
    print("%s Library: http://%s:%d   documents: %s" % (config.APP_NAME, lan_address(), config.PORT, config.LIBRARY_DIR))
    if config.FROZEN and "--no-window" not in sys.argv:
        threading.Thread(target=open_desk, daemon=True).start()
    from .app import app
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_config=None if config.FROZEN else uvicorn.config.LOGGING_CONFIG)


if __name__ == "__main__":
    main()
