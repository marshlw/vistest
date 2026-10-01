# VisTest - self-hosted visual regression testing.
# Copyright (C) 2026 Kirill Kulagin
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of VisTest. See LICENSE for the full terms and NOTICE for
# the trademark and commercial-licensing terms. Removing this header does not
# remove those obligations.

"""The stand's server: the pages, and answers that take as long as the seed says.

Real network, not timers in the page: a request for data, an image, the font,
the banner or the rows is held by the server for `stand.delay_ms(...)` before
it is answered, so the browser sees a request in flight the way it would on a
slow backend. Nothing is cached (`Cache-Control: no-store`). Listens on
127.0.0.1 only.

    with serve() as base:          # "http://127.0.0.1:<port>"
        ...
"""

from __future__ import annotations

import json
import struct
import threading
import time
import zlib
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import stand

STATIC = stand.HERE / "pages"

#: The table every data page shows; the signal variant changes one number.
ROWS = [("Harbor Street Bakery", "1,284.50"), ("Blue Lantern Books", "312.00"),
        ("Maple Garden Supply", "96.40"), ("Quiet Hill Coffee", "58.00"),
        ("Riverside Florist", "740.25"), ("Copper Kettle Diner", "1,020.00")]
SIGNAL_ROW = (2, "196.40")
ACCENT, SIGNAL_ACCENT = "#2563eb", "#dc2626"


def _png(width: int, height: int, rgb: tuple[int, int, int], stripe: tuple[int, int, int]
         ) -> bytes:
    """A small PNG, by hand: a colour with a stripe, so a late image is visible."""
    rows = []
    for y in range(height):
        colour = stripe if height // 3 <= y < height // 3 + 12 else rgb
        rows.append(b"\x00" + bytes(colour) * width)
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                                              8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


IMAGE_COLOURS = [(96, 165, 250), (52, 211, 153), (251, 191, 36), (244, 114, 182),
                 (167, 139, 250), (248, 113, 113), (45, 212, 191), (163, 230, 53)]


def answer(path: str, query: dict) -> tuple[int, str, bytes, int]:
    """(status, content type, body, delay in ms) for one request."""
    seed = int(query.get("seed", ["0"])[0])
    signal = query.get("signal", ["0"])[0] == "1"
    if path.startswith("/pages/") or path.startswith("/static/"):
        name = path.split("/")[-1]
        file = STATIC / name
        if not file.is_file() or ".." in name:
            return 404, "text/plain", b"not found", 0
        kind = {"html": "text/html; charset=utf-8", "js": "text/javascript",
                "css": "text/css"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
        return 200, kind, file.read_bytes(), 0
    if path == "/api/data":
        h = query.get("h", ["data"])[0]
        rows = [list(r) for r in ROWS]
        if signal:
            rows[SIGNAL_ROW[0]][1] = SIGNAL_ROW[1]
        body = {"rows": rows, "accent": SIGNAL_ACCENT if signal else ACCENT,
                "total": "3,511.15" if not signal else "3,611.15"}
        return 200, "application/json", json.dumps(body).encode(), stand.delay_ms(f"data:{h}",
                                                                                 seed)
    if path == "/api/banner":
        text = ("Maintenance tomorrow at 22:00 UTC" if signal
                else "Maintenance tonight at 22:00 UTC")
        return 200, "application/json", json.dumps({"text": text}).encode(), \
            stand.delay_ms("banner", seed)
    if path == "/api/rows":
        rows = [[f"Shipment {i + 1:02d}", f"{(i * 37) % 97 + 3} parcels"] for i in range(30)]
        if signal:
            rows[4][1] = "77 parcels"
        return 200, "application/json", json.dumps({"rows": rows}).encode(), \
            stand.delay_ms("rows", seed)
    if path == "/img":
        i = int(query.get("i", ["0"])[0])
        colour = IMAGE_COLOURS[i % len(IMAGE_COLOURS)]
        if signal and i == 1:
            colour = (30, 41, 59)
        return 200, "image/png", _png(160, 100, colour, (255, 255, 255)), \
            stand.delay_ms(f"img{i}", seed)
    if path == "/font":
        return 200, "font/ttf", stand.FONT.read_bytes(), stand.delay_ms("font", seed)
    return 404, "text/plain", b"not found", 0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the name http.server calls
        url = urlparse(self.path)
        status, kind, body, delay = answer(url.path, parse_qs(url.query))
        if delay:
            time.sleep(delay / 1000)
        try:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                      # the context was closed while we slept

    def log_message(self, *args):     # quiet
        pass


@contextmanager
def serve():
    """The stand on 127.0.0.1 and a free port, for as long as the block runs."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":       # python -m tests.capture_hazards.server
    with serve() as base:
        print(base, flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
