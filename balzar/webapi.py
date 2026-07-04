"""Transport-agnostic logic behind the Vercel demo's serverless functions
(api/encode.py, api/encode_vector.py, api/encode_video.py, api/render.py) —
the desktop app (balzar/gui.py) does not use this module, it calls the
engine directly with no platform caps.

LOCAL_LIMITS exists for a possible future non-Vercel deployment; only
VERCEL_LIMITS is wired to a real endpoint today.
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
    max_video_frames: int


# Vercel rejects request AND response bodies over ~4.5MB, so oversized
# pieces must be dropped from the response and flagged instead of letting
# the platform fail with an opaque error.
VERCEL_LIMITS = Limits(
    max_upload_bytes=6 * 1024 * 1024,
    max_analysis_dim=800,
    max_preview_dim=400,
    max_program_chars=300_000,
    max_payload_b64_bytes=3_000_000,
    max_video_frames=40,
)

# offline: no platform caps — only a sanity ceiling on the analysis size,
# because the pure-Python encoder is quadratic-ish in pixel count
LOCAL_LIMITS = Limits(
    max_upload_bytes=1 << 30,
    max_analysis_dim=2000,
    max_preview_dim=1200,
    max_program_chars=20_000_000,
    max_payload_b64_bytes=1 << 30,
    max_video_frames=120,
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
        "color_step": result.color_step,
        "fidelity_label": result.fidelity_label(),
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


def _truncate_program(program_text: str, limits: Limits) -> tuple[str, bool]:
    if len(program_text) <= limits.max_program_chars:
        return program_text, False
    head = "\n".join(program_text[:limits.max_program_chars].splitlines()[:2000])
    return (head + "\n# ... troncato per il limite di risposta; il programma "
           "completo si ricava dal payload con 'python -m balzar decode'\n"), True


def _payload_response_fields(payload: bytes, limits: Limits) -> dict:
    from .payload import fits_in_qr, to_base64
    payload_b64 = to_base64(payload)
    omitted = len(payload_b64) > limits.max_payload_b64_bytes
    return {
        "payload_bytes": len(payload),
        "fits_qr": fits_in_qr(payload),
        "payload_base64": "" if omitted else payload_b64,
        "payload_omitted": omitted,
    }


def handle_encode_vector(body: dict, limits: Limits) -> tuple[int, dict]:
    """SVG/DXF text -> vectorio ingestion -> payload. No raster in
    between (vectorio.py), unlike handle_encode which quantizes pixels."""
    data_b64 = body.get("data")
    filename = body.get("filename", "")
    if not data_b64:
        return 400, {"ok": False, "error": "campo 'data' mancante"}
    lower = filename.lower()
    if lower.endswith(".svg"):
        fmt = "svg"
    elif lower.endswith(".dxf"):
        fmt = "dxf"
    else:
        return 400, {"ok": False, "error": "estensione non riconosciuta: atteso .svg o .dxf"}

    max_dim = max(16, min(int(body.get("max_dim", 800)), limits.max_analysis_dim))
    raw = base64.b64decode(data_b64)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return 400, {"ok": False, "error": f"file non decodificabile come testo UTF-8: {exc}"}

    from .vectorio import VectorIngestError, ingest_dxf, ingest_svg

    try:
        result = (ingest_svg(text, max_dim=max_dim) if fmt == "svg"
                 else ingest_dxf(text, max_dim=max_dim))
    except VectorIngestError as exc:
        return 400, {"ok": False, "error": str(exc)}

    return 200, _vector_result_response(result, limits)


def _vector_result_response(result, limits: Limits) -> dict:
    """Shared by handle_encode_vector and handle_encode_sequence: render
    every frame for a navigable preview, disclose skipped elements, offer
    an SVG re-export of frame 0 (which always succeeds here — vectorio
    only ever emits the vector-safe op subset, unlike an arbitrary
    hand-written .bzr program). A single-file result has exactly one
    frame; the frontend's single-image tab just shows frames[0]."""
    import io as _io

    from PIL import Image

    from .interpreter import render as render_program
    from .svg import UnsupportedForSVG, render_svg

    rendered = render_program(result.program_text)
    preview_scaled = max(rendered.width, rendered.height) > limits.max_preview_dim

    def _preview_png(i: int) -> bytes:
        img = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.frame_rgb(i))
        if preview_scaled:
            img.thumbnail((limits.max_preview_dim, limits.max_preview_dim), Image.NEAREST)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    preview_frames = [base64.b64encode(_preview_png(i)).decode("ascii")
                      for i in range(len(rendered.frames))]

    program_text, program_truncated = _truncate_program(result.program_text, limits)

    try:
        svg_available, svg_text, svg_reason = True, render_svg(result.program_text), ""
    except UnsupportedForSVG as exc:
        svg_available, svg_text, svg_reason = False, "", str(exc)

    raw_rgb_bytes = result.width * result.height * 3 * len(rendered.frames)
    response = {
        "ok": True,
        "source_format": result.source_format,
        "width": result.width,
        "height": result.height,
        "frame_count": len(rendered.frames),
        "instruction_count": result.instruction_count,
        "skipped": result.skipped,
        "raw_rgb_bytes": raw_rgb_bytes,
        "expansion_vs_raw": raw_rgb_bytes / len(result.payload),
        "program_text": program_text,
        "program_truncated": program_truncated,
        "preview_scaled": preview_scaled,
        "preview_png_base64": preview_frames[0],
        "preview_frames_png_base64": preview_frames,
        "svg_available": svg_available,
        "svg_text": svg_text,
        "svg_reason": svg_reason,
    }
    response.update(_payload_response_fields(result.payload, limits))
    if hasattr(result, "element_count"):
        response["element_count"] = result.element_count
    return response


def handle_encode_video(body: dict, limits: Limits) -> tuple[int, dict]:
    """Animated GIF -> true multi-frame delta encoding (balzar/video.py),
    unlike handle_encode which only ever looks at the first frame."""
    data_b64 = body.get("data")
    if not data_b64:
        return 400, {"ok": False, "error": "campo 'data' mancante"}
    max_dim = max(16, min(int(body.get("max_dim", 300)), limits.max_analysis_dim))
    image_bytes = base64.b64decode(data_b64)

    from .imageio import load_frames

    try:
        w, h, frames = load_frames(image_bytes, max_dim=max_dim, max_frames=limits.max_video_frames)
    except OSError as exc:
        return 400, {"ok": False, "error": f"file non riconosciuto come immagine: {exc}"}
    if len(frames) < 2:
        return 400, {"ok": False,
                     "error": "il file ha un solo frame: usa 'Comprimi immagine' per un file statico"}

    from .video import encode_video
    result = encode_video(w, h, frames)

    preview_gif_b64, preview_scaled = _gif_preview_base64(w, h, frames, limits)
    program_text, program_truncated = _truncate_program(result.program_text, limits)

    raw_rgb_bytes = w * h * 3 * result.frame_count
    response = {
        "ok": True,
        "width": w,
        "height": h,
        "frame_count": result.frame_count,
        "palette_size": result.palette_size,
        "lossless": result.lossless,
        "delta_pixels_total": result.delta_pixels_total,
        "instruction_count": result.instruction_count,
        "raw_rgb_bytes": raw_rgb_bytes,
        "upload_bytes": len(image_bytes),
        "expansion_vs_raw": raw_rgb_bytes / len(result.payload),
        "program_text": program_text,
        "program_truncated": program_truncated,
        "preview_scaled": preview_scaled,
        "preview_gif_base64": preview_gif_b64,
    }
    response.update(_payload_response_fields(result.payload, limits))
    return 200, response


def _gif_preview_base64(w: int, h: int, frames: list[bytes],
                        limits: Limits) -> tuple[str, bool]:
    """Downscale (NEAREST) if needed so the preview GIF stays under the
    response size cap; returns (base64 gif, was_scaled)."""
    import os as _os
    import tempfile

    from PIL import Image

    from .imageio import save_gif

    scaled = max(w, h) > limits.max_preview_dim
    pw, ph, pframes = w, h, frames
    if scaled:
        scale = limits.max_preview_dim / max(w, h)
        pw, ph = max(1, round(w * scale)), max(1, round(h * scale))
        pframes = []
        for f in frames:
            img = Image.frombytes("RGB", (w, h), f).resize((pw, ph), Image.NEAREST)
            pframes.append(img.tobytes())
    with tempfile.TemporaryDirectory() as td:
        path = _os.path.join(td, "preview.gif")
        save_gif(path, pw, ph, pframes)
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii"), scaled


def handle_encode_sequence(body: dict, limits: Limits) -> tuple[int, dict]:
    """Several files, in the order given by the caller (the frontend lets
    the user reorder them before submitting) -> one multi-frame payload.
    Vector files (.svg/.dxf, homogeneous) go through sequence.py's
    text-dedup delta; raster images go through video.py's pixel delta —
    same dispatch rule as `balzar encode-sequence` in the CLI."""
    files = body.get("files")
    if not isinstance(files, list) or len(files) < 2:
        return 400, {"ok": False, "error": "servono almeno 2 file, in ordine"}

    names = [f.get("filename", "") for f in files]
    exts = {n.lower().rsplit(".", 1)[-1] if "." in n else "" for n in names}
    max_dim = max(16, min(int(body.get("max_dim", 800)), limits.max_analysis_dim))

    import os
    import tempfile

    from .sequence import SequenceError, encode_raster_sequence, encode_vector_sequence
    from .vectorio import VectorIngestError

    with tempfile.TemporaryDirectory() as td:
        paths = []
        for i, f in enumerate(files):
            name = f.get("filename") or f"file{i}"
            data = f.get("data")
            if not data:
                return 400, {"ok": False, "error": f"file #{i + 1} ('{name}') senza contenuto"}
            path = os.path.join(td, f"{i:03d}_{os.path.basename(name)}")
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(data))
            paths.append(path)

        try:
            if exts <= {"svg", "dxf"}:
                result = encode_vector_sequence(paths, max_dim=max_dim)
                return 200, _vector_result_response(result, limits)
            else:
                result = encode_raster_sequence(paths, max_dim=max_dim)
        except (SequenceError, VectorIngestError) as exc:
            return 400, {"ok": False, "error": str(exc)}
        except OSError as exc:
            # e.g. a non-image file reaching the raster path (PIL's
            # UnidentifiedImageError is an OSError subclass)
            return 400, {"ok": False,
                         "error": f"file non riconosciuto come immagine: {exc}"}

    # raster path: same response shape as the vector path, built by hand
    # here since result is a video.VideoEncodeResult, not a VectorIngestResult
    program_text, program_truncated = _truncate_program(result.program_text, limits)
    raw_rgb_bytes = result.width * result.height * 3 * result.frame_count
    response = {
        "ok": True,
        "source_format": "raster",
        "width": result.width,
        "height": result.height,
        "frame_count": result.frame_count,
        "palette_size": result.palette_size,
        "lossless": result.lossless,
        "instruction_count": result.instruction_count,
        "raw_rgb_bytes": raw_rgb_bytes,
        "expansion_vs_raw": raw_rgb_bytes / len(result.payload),
        "program_text": program_text,
        "program_truncated": program_truncated,
    }
    response.update(_payload_response_fields(result.payload, limits))

    from .interpreter import render as render_program
    rendered = render_program(result.program_text)
    preview_scaled = max(rendered.width, rendered.height) > limits.max_preview_dim
    response["preview_scaled"] = preview_scaled
    response["preview_frames_png_base64"] = [
        base64.b64encode(_png_frame(rendered, i, limits.max_preview_dim if preview_scaled else None)).decode("ascii")
        for i in range(len(rendered.frames))
    ]
    return 200, response


def _png_frame(rendered, index: int, max_dim: int | None = None) -> bytes:
    import io as _io

    from PIL import Image
    img = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.frame_rgb(index))
    if max_dim:
        img.thumbnail((max_dim, max_dim), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def handle_qr(body: dict, limits: Limits) -> tuple[int, dict]:
    """Payload bytes -> a printable QR image (single code, or an
    auto-sized grid if the payload doesn't fit one) — reuses
    balzar/qr.py exactly as the CLI/GUI do. Generation only needs the
    pure-Python `qrcode` package (+ Pillow, already a dependency); unlike
    *reading* a QR (pyzbar/libzbar0, native, not wired into the web demo)
    this has no extra system dependency, so it is safe to expose here."""
    payload_b64 = body.get("payload_base64")
    if not payload_b64:
        return 400, {"ok": False, "error": "campo 'payload_base64' mancante"}

    try:
        import qrcode  # noqa: F401  (import check only; qr.py does the real import)
    except ImportError:
        return 500, {"ok": False,
                     "error": "generazione QR non disponibile su questo deployment "
                             "(pacchetto 'qrcode' mancante)"}

    from .payload import fits_in_qr
    from .qr import payload_to_qr_image

    payload = base64.b64decode(payload_b64)
    if len(base64.b64encode(payload)) > limits.max_payload_b64_bytes:
        return 400, {"ok": False, "error": "payload troppo grande per generare un QR qui"}

    img = payload_to_qr_image(payload)

    import io as _io
    buf = _io.BytesIO()
    img.save(buf, format="PNG")

    return 200, {
        "ok": True,
        "single_qr": fits_in_qr(payload),
        "width": img.size[0],
        "height": img.size[1],
        "qr_png_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }


def handle_render(body: dict, limits: Limits) -> tuple[int, dict]:
    """Open an existing .bzr/.bzp (no terminal needed): decode + render,
    return a PNG/GIF to look at and download, plus SVG if the program
    only uses the vector-safe op subset (balzar/svg.py)."""
    data_b64 = body.get("data")
    if not data_b64:
        return 400, {"ok": False, "error": "campo 'data' mancante"}

    from .payload import MAGIC, PayloadError, decode_payload

    raw = base64.b64decode(data_b64)
    try:
        program_text = decode_payload(raw) if raw[:4] == MAGIC else raw.decode("utf-8")
    except (PayloadError, UnicodeDecodeError) as exc:
        return 400, {"ok": False, "error": f"file non riconosciuto: {exc}"}

    from .interpreter import render as render_program

    try:
        result = render_program(program_text)
    except (ValueError, SyntaxError) as exc:
        return 400, {"ok": False, "error": f"programma non valido: {exc}"}

    import io as _io

    from PIL import Image

    from .png import png_bytes

    img = Image.frombytes("RGB", (result.width, result.height), result.frame_rgb(0))
    preview_scaled = max(img.size) > limits.max_preview_dim
    if preview_scaled:
        img.thumbnail((limits.max_preview_dim, limits.max_preview_dim), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")

    from .payload import encode_payload

    response = {
        "ok": True,
        "width": result.width,
        "height": result.height,
        "frame_count": len(result.frames),
        "raw_rgb_bytes": result.raw_rgb_size,
        "program_text": program_text,
        "preview_scaled": preview_scaled,
        "preview_png_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }
    # re-encode canonically so "genera QR" works the same whether the
    # upload was a .bzr (source text) or a .bzp (already a payload)
    response.update(_payload_response_fields(encode_payload(program_text), limits))

    full_png_b64 = base64.b64encode(
        png_bytes(result.width, result.height, result.frame_rgb(0))).decode("ascii")
    response["png_omitted"] = len(full_png_b64) > limits.max_payload_b64_bytes
    response["png_base64"] = "" if response["png_omitted"] else full_png_b64

    if len(result.frames) > 1:
        import os as _os
        import tempfile

        from .imageio import save_gif
        with tempfile.TemporaryDirectory() as td:
            path = _os.path.join(td, "out.gif")
            frames = [result.frame_rgb(i) for i in range(len(result.frames))]
            save_gif(path, result.width, result.height, frames)
            with open(path, "rb") as fh:
                gif_b64 = base64.b64encode(fh.read()).decode("ascii")
        response["gif_omitted"] = len(gif_b64) > limits.max_payload_b64_bytes
        response["gif_base64"] = "" if response["gif_omitted"] else gif_b64

    from .svg import UnsupportedForSVG, render_svg
    try:
        response["svg_available"] = True
        response["svg_text"] = render_svg(program_text)
    except UnsupportedForSVG as exc:
        response["svg_available"] = False
        response["svg_reason"] = str(exc)

    return 200, response
