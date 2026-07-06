"""Vercel serverless function: 3DXML CAD assembly in, BZM1 payload +
GLB (for <model-viewer>) + bill of materials out.

Thin HTTP shell around balzar.webapi.handle_encode_3d, same limit
profile as the other tabs. See balzar/scene3d.py + balzar/gltf.py for
why 3DXML (not STEP) and why the preview is a GLB, not a rasterized
image.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from balzar.webapi import VERCEL_LIMITS, handle_encode_3d


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
            "info": "POST a JSON body {data: <base64 .3dxml>} to this endpoint",
        })

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > VERCEL_LIMITS.max_upload_bytes:
                self._send_json(400, {"ok": False,
                                      "error": "richiesta vuota o troppo grande"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            status, obj = handle_encode_3d(body, VERCEL_LIMITS)
            self._send_json(status, obj)
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
