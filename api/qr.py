"""Vercel serverless function: payload bytes in, a printable QR image out
(single code, or an auto-sized grid for larger payloads).

Thin HTTP shell around balzar.webapi.handle_qr, same limit profile as
api/encode.py. Only needs the pure-Python `qrcode` package (+ Pillow,
already a dependency) — unlike *reading* a QR (pyzbar/libzbar0, native),
generation has no extra system dependency.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from balzar.webapi import VERCEL_LIMITS, handle_qr


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send_json(200, {
            "ok": True,
            "info": "POST a JSON body {payload_base64: <base64 .bzp payload>} to this endpoint",
        })

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > VERCEL_LIMITS.max_upload_bytes:
                self._send_json(400, {"ok": False,
                                      "error": "richiesta vuota o troppo grande"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            status, obj = handle_qr(body, VERCEL_LIMITS)
            self._send_json(status, obj)
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
