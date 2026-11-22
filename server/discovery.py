"""Lets engineers' PCs find the Library on the office network without typing an address.

The engineer app shouts a short question on the LAN (UDP broadcast); every running Library answers
with its name and address. Nothing else is exchanged.
"""
import json
import socket
import threading

PORT = 8701
QUESTION = b"DEXMENTOR_DISCOVER_V1"


def serve(name, url_for):
    """url_for() returns this Library's address at the time of asking."""
    def loop():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", PORT))
        except OSError:
            return  # another Library on this PC already answers
        while True:
            try:
                data, addr = s.recvfrom(256)
                if data.strip() == QUESTION:
                    s.sendto(json.dumps({"dexmentor": name, "url": url_for()}).encode(), addr)
            except OSError:
                pass
    threading.Thread(target=loop, daemon=True).start()
