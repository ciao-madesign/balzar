"""Vercel serverless function: image bytes in, balzar payload + stats out.

Reuses the real balzar package as-is (encoder + interpreter + payload),
so the demo is guaranteed bit-identical to what `balzar encode-image`
produces on the CLI. Request/response are plain JSON with base64 image
data, which sidesteps multipart parsing entirely.
"""

import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

MAX_UPLOAD_BYTES = 6 * 1024 * 1024  # generous cap, well under Vercel's body limit


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
            "info": "POST a JSON body {data: <base64 image>, max_dim: <int>} to this endpoint",
        })

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self._send_json(400, {"ok": False,
                                      "error": "richiesta vuota o troppo grande (max 6MB)"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))

            data_b64 = body.get("data")
            if not data_b64:
                self._send_json(400, {"ok": False, "error": "campo 'data' mancante"})
                return
            max_dim = max(16, min(int(body.get("max_dim", 300)), 400))
            image_bytes = base64.b64decode(data_b64)

            from balzar.encoder import encode_image
            from balzar.imageio import load_rgb
            from balzar.interpreter import render as render_program
            from balzar.payload import fits_in_qr, to_base64
            from balzar.png import png_bytes

            w, h, rgb = load_rgb(image_bytes, max_dim=max_dim)
            result = encode_image(w, h, rgb)

            rendered = render_program(result.program_text)
            preview_png = png_bytes(rendered.width, rendered.height, rendered.frame_rgb(0))

            raw_rgb_bytes = w * h * 3
            self._send_json(200, {
                "ok": True,
                "width": w,
                "height": h,
                "palette_size": result.palette_size,
                "lossless": result.lossless,
                "tile": list(result.tile) if result.tile else None,
                "instruction_count": result.instruction_count,
                "raw_rgb_bytes": raw_rgb_bytes,
                "payload_bytes": len(result.payload),
                "upload_bytes": len(image_bytes),
                "fits_qr": fits_in_qr(result.payload),
                "expansion_vs_raw": raw_rgb_bytes / len(result.payload),
                "payload_base64": to_base64(result.payload),
                "program_text": result.program_text,
                "preview_png_base64": base64.b64encode(preview_png).decode("ascii"),
            })
        except Exception as exc:  # last-resort JSON error surface for the UI
            self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
