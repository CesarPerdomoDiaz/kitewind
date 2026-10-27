"""Tiny HTTP server: the dashboard page and its JSON."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .scheduler import Scheduler

WEB = Path(__file__).parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".webmanifest": "application/manifest+json"}
log = logging.getLogger("kitewind")


def make_handler(sched: Scheduler):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/forecast":
                with sched.snapshot_lock:
                    snap = sched.snapshot
                self._send(200, json.dumps(snap).encode(), "application/json")
                return
            if path == "/":
                path = "/index.html"
            f = (WEB / path.lstrip("/")).resolve()
            if WEB.resolve() not in f.parents or not f.is_file():
                self._send(404, b"not found", "text/plain")
                return
            self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

        def log_message(self, fmt, *args):
            log.debug("http: " + fmt, *args)

    return Handler


def serve(sched: Scheduler, host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(sched))
    log.info("dashboard on http://%s:%d", host, port)
    httpd.serve_forever()
