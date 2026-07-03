"""Transport-agnostic web API logic, shared by the Vercel function and the
offline local server (`python -m balzar serve`).

All platform constraints live in a Limits value so the two deployments
differ only in numbers: Vercel has hard request/response caps (~4.5MB
each), the local server has essentially none.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_upload_bytes: int
    max_analysis_dim: int
    max_preview_dim: int
    max_program_chars: int
    max_payload_b64_bytes: int


# Vercel rejects request AND response bodies over ~4.5MB, so oversized
# pieces must be dropped from the response and flagged instead of letting
# the platform fail with an opaque error.
VERCEL_LIMITS = Limits(
    max_upload_bytes=6 * 1024 * 1024,
    max_analysis_dim=800,
    max_preview_dim=400,
    max_program_chars=300_000,
    max_payload_b64_bytes=3_000_000,
)

# offline: no platform caps — only a sanity ceiling on the analysis size,
# because the pure-Python encoder is quadratic-ish in pixel count
LOCAL_LIMITS = Limits(
    max_upload_bytes=1 << 30,
    max_analysis_dim=2000,
    max_preview_dim=1200,
    max_program_chars=20_000_000,
    max_payload_b64_bytes=1 << 30,
)


def limits_info(limits: Limits) -> dict:
    """Payload for GET /api/encode: lets the frontend adapt its UI."""
    return {
        "ok": True,
        "info": "POST a JSON body {data: <base64 image>, max_dim: <int>} to this endpoint",
        "limits": {
            "max_upload_bytes": limits.max_upload_bytes,
            "max_analysis_dim": limits.max_analysis_dim,
        },
    }


def handle_encode(body: dict, limits: Limits) -> tuple[int, dict]:
    """Process one encode request; returns (http_status, json_dict)."""
    data_b64 = body.get("data")
    if not data_b64:
        return 400, {"ok": False, "error": "campo 'data' mancante"}
    max_dim = max(16, min(int(body.get("max_dim", 300)), limits.max_analysis_dim))
    image_bytes = base64.b64decode(data_b64)

    from .encoder import encode_image
    from .imageio import load_rgb
    from .interpreter import render as render_program
    from .payload import fits_in_qr, to_base64

    w, h, rgb = load_rgb(image_bytes, max_dim=max_dim)
    result = encode_image(w, h, rgb)

    # the preview really is the program's output, re-rendered by the
    # interpreter; it is only downscaled afterwards for the response
    rendered = render_program(result.program_text)
    import io as _io

    from PIL import Image
    img = Image.frombytes("RGB", (rendered.width, rendered.height),
                          rendered.frame_rgb(0))
    preview_scaled = max(img.size) > limits.max_preview_dim
    if preview_scaled:
        img.thumbnail((limits.max_preview_dim, limits.max_preview_dim),
                      Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    preview_png = buf.getvalue()

    program_text = result.program_text
    program_truncated = len(program_text) > limits.max_program_chars
    if program_truncated:
        head = "\n".join(
            program_text[:limits.max_program_chars].splitlines()[:2000])
        program_text = (head + "\n# ... troncato per il limite di risposta; "
                        "il programma completo si ricava dal payload con "
                        "'python -m balzar decode'\n")

    payload_b64 = to_base64(result.payload)
    payload_omitted = len(payload_b64) > limits.max_payload_b64_bytes
    if payload_omitted:
        payload_b64 = ""

    raw_rgb_bytes = w * h * 3
    return 200, {
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
    }
