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

# Vercel rejects request bodies over ~4.5MB before we ever see them, so
# this cap only guards the local/dev path; the real limit is the platform's
MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MAX_ANALYSIS_DIM = 800

# Vercel also caps the RESPONSE at ~4.5MB. On no-gain inputs (noise/photos
# at high resolution) payload+program+preview can exceed that, so oversized
# pieces are dropped from the response and flagged, instead of letting the
# platform fail with an opaque error.
MAX_PREVIEW_DIM = 400
MAX_PROGRAM_CHARS = 300_000
MAX_PAYLOAD_B64_BYTES = 3_000_000


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
            max_dim = max(16, min(int(body.get("max_dim", 300)), MAX_ANALYSIS_DIM))
            image_bytes = base64.b64decode(data_b64)

            from balzar.encoder import encode_image
            from balzar.imageio import load_rgb
            from balzar.interpreter import render as render_program
            from balzar.payload import fits_in_qr, to_base64

            w, h, rgb = load_rgb(image_bytes, max_dim=max_dim)
            result = encode_image(w, h, rgb)

            # the preview really is the program's output, re-rendered by the
            # interpreter; it is only downscaled afterwards for the response
            rendered = render_program(result.program_text)
            import io as _io

            from PIL import Image
            img = Image.frombytes("RGB", (rendered.width, rendered.height),
                                  rendered.frame_rgb(0))
            preview_scaled = max(img.size) > MAX_PREVIEW_DIM
            if preview_scaled:
                img.thumbnail((MAX_PREVIEW_DIM, MAX_PREVIEW_DIM), Image.NEAREST)
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            preview_png = buf.getvalue()

            program_text = result.program_text
            program_truncated = len(program_text) > MAX_PROGRAM_CHARS
            if program_truncated:
                head = "\n".join(program_text[:MAX_PROGRAM_CHARS].splitlines()[:2000])
                program_text = (head + "\n# ... troncato per il limite di risposta; "
                                "il programma completo si ricava dal payload con "
                                "'python -m balzar decode'\n")

            payload_b64 = to_base64(result.payload)
            payload_omitted = len(payload_b64) > MAX_PAYLOAD_B64_BYTES
            if payload_omitted:
                payload_b64 = ""

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
                "payload_base64": payload_b64,
                "payload_omitted": payload_omitted,
                "program_text": program_text,
                "program_truncated": program_truncated,
                "preview_scaled": preview_scaled,
                "preview_png_base64": base64.b64encode(preview_png).decode("ascii"),
            })
        except Exception as exc:  # last-resort JSON error surface for the UI
            self._send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
