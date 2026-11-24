"""Starts the DexMentor Library engine. On the office server PC the DexMentor Library program (apps/library, Rust)
runs it hidden behind its own window and tray icon; for development run it directly:  python -m server"""
import os
import socket
import sys

import uvicorn

from . import config


def lan_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def already_running():
    try:
        with socket.create_connection(("127.0.0.1", config.PORT), timeout=1):
            return True
    except OSError:
        return False


def main():
    if already_running():  # one engine per PC; the program's window belongs to the Rust shell
        print("%s is already running on port %d" % (config.APP_NAME, config.PORT))
        sys.exit(3)
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
    from .app import app
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_config=None if config.FROZEN else uvicorn.config.LOGGING_CONFIG)


if __name__ == "__main__":
    main()
