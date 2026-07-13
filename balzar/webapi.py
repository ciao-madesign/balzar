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


def _b64decode(data) -> bytes:
    """Decode client-supplied base64, honestly: malformed input (bad
    padding/characters, wrong type) becomes a plain ValueError the
    caller turns into a 400 — never an unhandled 500. base64.b64decode
    raises binascii.Error (a ValueError subclass) on bad padding and
    TypeError on a non-string/bytes input; both are real client-input
    mistakes, not internal bugs."""
    try:
        return base64.b64decode(data, validate=False)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"base64 non valido: {exc}") from None


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
    try:
        image_bytes = _b64decode(data_b64)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

    from .encoder import encode_image
    from .imageio import load_rgb
    from .interpreter import render as render_program
    from .payload import fits_in_qr, to_base64

    try:
        w, h, rgb = load_rgb(image_bytes, max_dim=max_dim)
    except OSError as exc:
        return 400, {"ok": False, "error": f"file non riconosciuto come immagine: {exc}"}
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
        "mean_color_error": result.mean_color_error,
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
    try:
        raw = _b64decode(data_b64)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
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


def handle_encode_3d(body: dict, limits: Limits) -> tuple[int, dict]:
    """3DXML CAD assembly -> BZM1 payload (balzar/scene3d.py) + a GLB for
    <model-viewer> + the bill of materials. Unlike the 2D tabs there is no
    PNG/pixel preview to render — the "preview" here IS the GLB, built by
    balzar/gltf.py and shown client-side by the same model-viewer web
    component the desktop app opens in a browser (balzar/viewer3d.py).

    Optional `alarm_csv` field (base64 of a component info CSV -- any
    columns, e.g. component name/alarm code/spare part/maintenance
    notes, in any order, with a header row): when present, the 3D
    payload and the CSV are packed together into one BZX1 bundle
    (balzar/bundle.py) instead of a bare BZM1 -- `payload_base64`/
    `fits_qr` then describe the *bundle*, and the same "genera QR"
    button already wired to this tab keeps working with zero changes,
    since chunk_payload/payload_to_qr_frames treat any payload as opaque
    bytes. `info_table` ({headers, rows}) is returned either way so the
    frontend can wire the 3D viewer's search bar immediately, without a
    separate client-side CSV upload step."""
    data_b64 = body.get("data")
    if not data_b64:
        return 400, {"ok": False, "error": "campo 'data' mancante"}
    try:
        raw = _b64decode(data_b64)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

    alarm_csv_b64 = body.get("alarm_csv")
    alarm_csv_text = None
    info_table = None
    if alarm_csv_b64:
        from .viewer3d import parse_component_table_text
        try:
            alarm_csv_text = _b64decode(alarm_csv_b64).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            return 400, {"ok": False, "error": f"tabella componenti non valida: {exc}"}
        try:
            info_table = parse_component_table_text(alarm_csv_text)
        except ValueError as exc:
            return 400, {"ok": False, "error": str(exc)}
    # any cell value across the whole table is offered as a candidate to
    # collapse its own BOM/GLB entry into a single row/highlight group
    # instead of expanding to every individual leaf part underneath --
    # see scene3d.generate_bom's collapse_names for why (a table often
    # names a whole sub-assembly, e.g. "HEATER1", not one physical part).
    # A candidate that doesn't match a real group name is simply
    # ignored, so there's no need to know which column is "the
    # component" here -- that's decided client-side, once this call's
    # own BOM exists to test candidates against.
    collapse_names = info_table.all_values() if info_table else None

    # extra consultable documents to bundle alongside the model: each
    # {label, data (base64)}. Carried as raw KIND_DOC bytes, no parsing.
    raw_docs = body.get("documents") or []
    doc_items = []
    for d in raw_docs:
        label = d.get("label") or "documento"
        try:
            doc_bytes = _b64decode(d.get("data", ""))
        except ValueError as exc:
            return 400, {"ok": False, "error": f"documento '{label}' non valido: {exc}"}
        doc_items.append((label, doc_bytes))

    from .scene3d import Scene3DError, encode_3dxml_file

    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "assembly.3dxml")
        with open(path, "wb") as fh:
            fh.write(raw)
        try:
            result = encode_3dxml_file(path)
        except Scene3DError as exc:
            return 400, {"ok": False, "error": str(exc)}

    from .gltf import scene3d_to_glb
    from .scene3d import decode_payload, generate_bom
    scene = decode_payload(result.payload)
    # result.bom (from encode_3dxml_file) already has valid material_names
    # (added to generate_bom unconditionally) but was computed with no
    # alarm context -- recompute only when there's a collapse to apply,
    # otherwise reuse it as-is (identical result, no need to redo the walk).
    bom_for_response = result.bom if collapse_names is None else generate_bom(scene, collapse_names)
    glb = scene3d_to_glb(scene, collapse_names=collapse_names)
    glb_b64 = base64.b64encode(glb).decode("ascii")
    glb_omitted = len(glb_b64) > limits.max_payload_b64_bytes

    response = {
        "ok": True,
        "shape_count": result.shape_count,
        "reference_count": result.reference_count,
        "instance_count": result.instance_count,
        "vertex_count": result.vertex_count,
        "mean_vertex_error": result.mean_vertex_error,
        "bom": [{"name": e.name, "count": e.count, "material_names": e.material_names}
               for e in sorted(bom_for_response, key=lambda e: -e.count)],
        "glb_omitted": glb_omitted,
        "glb_base64": "" if glb_omitted else glb_b64,
    }

    if alarm_csv_text is not None or doc_items:
        import os as _os

        from .bundle import (KIND_3D, KIND_2D, KIND_ALARM, KIND_DOC, BundleItem,
                             encode_bundle)
        from .payload import MAGIC as BZR1_MAGIC
        from .payload import encode_payload as encode_2d
        from .viewer3d import _render_2d_item

        bundle_items = [BundleItem(KIND_3D, "assembly.b3d", result.payload)]
        response_docs = []
        if alarm_csv_text is not None:
            bundle_items.append(BundleItem(KIND_ALARM, "alarms.csv",
                                          alarm_csv_text.encode("utf-8")))
            response_docs.append({"role": "allarmi", "label": "alarms.csv",
                                 "b64": base64.b64encode(alarm_csv_text.encode("utf-8")).decode("ascii")})
        for label, doc_bytes in doc_items:
            ext = _os.path.splitext(label)[1].lower()
            # .bzr/.bzp are rendered fresh into PNG/GIF/SVG entries (same
            # "describe, don't store pixels" rule as everywhere else in
            # balzar), exactly like encode_bundle_files does for the CLI/
            # desktop path -- everything else stays a raw KIND_DOC.
            if ext == ".bzr":
                try:
                    payload_2d = encode_2d(doc_bytes.decode("utf-8"))
                except UnicodeDecodeError as exc:
                    return 400, {"ok": False, "error": f"documento '{label}' non e' UTF-8 valido: {exc}"}
                except (SyntaxError, ValueError) as exc:
                    return 400, {"ok": False, "error": f"documento '{label}' non valido: {exc}"}
            elif ext == ".bzp":
                if doc_bytes[:4] != BZR1_MAGIC:
                    return 400, {"ok": False,
                                 "error": f"documento '{label}' non e' un payload BZR1 valido"}
                payload_2d = doc_bytes
            else:
                payload_2d = None

            if payload_2d is not None:
                item = BundleItem(KIND_2D, label, payload_2d)
                try:
                    response_docs.extend(_render_2d_item(item))
                except (SyntaxError, ValueError, RuntimeError) as exc:
                    return 400, {"ok": False, "error": f"documento '{label}' non valido: {exc}"}
                bundle_items.append(item)
            else:
                bundle_items.append(BundleItem(KIND_DOC, label, doc_bytes))
                response_docs.append({"role": "doc", "label": label,
                                     "b64": base64.b64encode(doc_bytes).decode("ascii")})

        bundle_bytes = encode_bundle(bundle_items)
        response.update(_payload_response_fields(bundle_bytes, limits))
        response["bundled"] = True
        response["info_table"] = info_table.to_json_dict() if info_table else {"headers": [], "rows": []}
        response["documents"] = response_docs
    else:
        response.update(_payload_response_fields(result.payload, limits))
        response["bundled"] = False
        response["info_table"] = {"headers": [], "rows": []}
        response["documents"] = []

    return 200, response


def handle_encode_video(body: dict, limits: Limits) -> tuple[int, dict]:
    """Animated GIF -> true multi-frame delta encoding (balzar/video.py),
    unlike handle_encode which only ever looks at the first frame."""
    data_b64 = body.get("data")
    if not data_b64:
        return 400, {"ok": False, "error": "campo 'data' mancante"}
    max_dim = max(16, min(int(body.get("max_dim", 300)), limits.max_analysis_dim))
    try:
        image_bytes = _b64decode(data_b64)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

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
        "mean_color_error": result.mean_color_error,
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
    same dispatch rule as `balzar encode-sequence` in the CLI. If
    body["mode"] == "independent", dispatches to handle_encode_independent
    instead: one payload per file, no shared canvas, no format
    restriction — see that function's docstring."""
    files = body.get("files")
    if not isinstance(files, list):
        return 400, {"ok": False, "error": "campo 'files' mancante"}

    max_dim = max(16, min(int(body.get("max_dim", 800)), limits.max_analysis_dim))

    if body.get("mode") == "independent":
        return handle_encode_independent(files, max_dim, limits)

    if len(files) < 2:
        return 400, {"ok": False, "error": "servono almeno 2 file, in ordine"}

    names = [f.get("filename", "") for f in files]
    exts = {n.lower().rsplit(".", 1)[-1] if "." in n else "" for n in names}

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
            try:
                raw = _b64decode(data)
            except ValueError as exc:
                return 400, {"ok": False, "error": f"file #{i + 1} ('{name}'): {exc}"}
            path = os.path.join(td, f"{i:03d}_{os.path.basename(name)}")
            with open(path, "wb") as fh:
                fh.write(raw)
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
        "mean_color_error": result.mean_color_error,
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


def handle_encode_independent(files: list, max_dim: int, limits: Limits) -> tuple[int, dict]:
    """Batch mode: each uploaded file becomes its own payload — no shared
    canvas, no delta, no format restriction (a batch can freely mix
    .svg/.dxf/raster). A broken file is reported as its own failed entry,
    not a 400 for the whole request: that fault isolation is the point of
    this mode versus the sequence/video paths above — it must hold even
    when the file's own upload data (base64) is corrupt, not only when
    its content fails to parse, so a bad base64 blob never reaches
    encode_independent at all: it becomes its own failed item directly."""
    if not files:
        return 400, {"ok": False, "error": "campo 'files' vuoto"}

    import os
    import tempfile

    from .sequence import encode_independent

    with tempfile.TemporaryDirectory() as td:
        names = [f.get("filename") or f"file{i}" for i, f in enumerate(files)]
        path_by_index: dict[int, str] = {}
        early_errors: dict[int, str] = {}
        for i, f in enumerate(files):
            data = f.get("data")
            if not data:
                return 400, {"ok": False, "error": f"file #{i + 1} ('{names[i]}') senza contenuto"}
            try:
                raw = _b64decode(data)
            except ValueError as exc:
                early_errors[i] = str(exc)
                continue
            path = os.path.join(td, f"{i:03d}_{os.path.basename(names[i])}")
            with open(path, "wb") as fh:
                fh.write(raw)
            path_by_index[i] = path

        ordered_indices = sorted(path_by_index)
        results = (encode_independent([path_by_index[i] for i in ordered_indices], max_dim=max_dim)
                  if ordered_indices else [])
        result_by_index = dict(zip(ordered_indices, results))

        items = []
        for i, name in enumerate(names):
            if i in early_errors:
                items.append({"ok": False, "filename": name, "error": early_errors[i]})
                continue
            result = result_by_index[i]
            if not result.ok:
                items.append({"ok": False, "filename": name, "error": result.error})
                continue

            from .interpreter import render as render_program
            rendered = render_program(result.program_text)
            preview_png = _png_frame(rendered, 0, limits.max_preview_dim)
            program_text, program_truncated = _truncate_program(result.program_text, limits)

            item = {
                "ok": True,
                "filename": name,
                "source_format": result.source_format,
                "width": result.width,
                "height": result.height,
                "instruction_count": result.instruction_count,
                "skipped": result.skipped,
                "program_text": program_text,
                "program_truncated": program_truncated,
                "preview_png_base64": base64.b64encode(preview_png).decode("ascii"),
            }
            if result.element_count is not None:
                item["element_count"] = result.element_count
            item.update(_payload_response_fields(result.payload, limits))
            items.append(item)

    n_ok = sum(1 for it in items if it["ok"])
    return 200, {"ok": True, "mode": "independent", "file_count": len(items),
                "success_count": n_ok, "items": items}


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
    """Payload bytes -> a printable QR image, or a real multi-frame
    sequence — reuses balzar/qr.py exactly as the CLI does. Generation
    only needs the pure-Python `qrcode` package (+ Pillow, already a
    dependency); unlike *reading* a QR (pyzbar/libzbar0, native, not
    wired into the web demo) this has no extra system dependency, so it
    is safe to expose here.

    `mode` (default "single") picks the output shape:
    - "single": one image, single QR or one auto-sized grid with every
      chunk crammed in (payload_to_qr_image) -- the original behaviour,
      still the default. For a large payload this can mean a grid with
      no cap on codes-per-image (e.g. 14x14 for a real 3D assembly
      payload) that's fine as a *file* but not something a person can
      usefully scan or print at a fixed physical size.
    - "gif": payload_to_qr_frames (capped at grid_dim**2 codes/frame) +
      frames_to_gif -- one auto-playing GIF, for a screen that cycles
      frames on its own.
    - "pages": same frame split, returned as a list of individual PNGs
      (base64 each) -- for printing one page per frame, where
      "auto-play" has no meaning.
    grid_dim (default 4, clamped to [1, 8]) is a property of the
    physical output medium, not the payload -- see CLAUDE.md §2.4b for
    why 4 is the recommended default and 8 is available but not
    recommended. grid_dim=1 (one QR code per frame, no grid at all) is
    a real, deliberate case, not just a permitted edge value: it is the
    ONLY grid_dim a live camera can reliably decode continuously (a
    matrix needs ~3800-4700px of live camera frame width to keep every
    one of its codes individually readable, unrealistic at a normal
    working distance -- CLAUDE.md §2.4g), so the "GIF per acquisizione
    continua" delivery mode always requests grid_dim=1 here.

    "gif"/"pages" responses also include estimated_scan_seconds_low/high
    -- an honest ballpark for how long reading the sequence back is
    likely to take, calibrated from a real decode benchmark (CLAUDE.md
    §9.24), not a promise: it can't know the operator's actual camera,
    lighting, or handling."""
    payload_b64 = body.get("payload_base64")
    if not payload_b64:
        return 400, {"ok": False, "error": "campo 'payload_base64' mancante"}

    mode = body.get("mode", "single")
    if mode not in ("single", "gif", "pages"):
        return 400, {"ok": False, "error": f"mode sconosciuto: {mode!r} (atteso single/gif/pages)"}

    try:
        grid_dim = int(body.get("grid_dim", 4))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "grid_dim deve essere un intero"}
    grid_dim = max(1, min(8, grid_dim))

    try:
        import qrcode  # noqa: F401  (import check only; qr.py does the real import)
    except ImportError:
        return 500, {"ok": False,
                     "error": "generazione QR non disponibile su questo deployment "
                             "(pacchetto 'qrcode' mancante)"}

    from .payload import fits_in_qr
    from .qr import (estimate_scan_seconds, frames_to_gif, payload_to_qr_frames,
                     payload_to_qr_image)

    try:
        payload = _b64decode(payload_b64)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    if len(base64.b64encode(payload)) > limits.max_payload_b64_bytes:
        return 400, {"ok": False, "error": "payload troppo grande per generare un QR qui"}

    import io as _io

    if mode == "single":
        img = payload_to_qr_image(payload)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return 200, {
            "ok": True,
            "mode": "single",
            "single_qr": fits_in_qr(payload),
            "width": img.size[0],
            "height": img.size[1],
            "qr_png_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
        }

    frames = payload_to_qr_frames(payload, grid_dim=grid_dim)
    scan_low, scan_high = estimate_scan_seconds(len(frames))

    if mode == "gif":
        gif_b64 = base64.b64encode(frames_to_gif(frames)).decode("ascii")
        # a GIF bundle inflates well past the source payload (measured:
        # 500KB payload -> 9MB GIF for 7 frames, CLAUDE.md §9.10), so the
        # payload-size check above does not bound this -- re-check the
        # actual output, same _omitted pattern as png/glb elsewhere in
        # this module, rather than letting a huge response blow past
        # Vercel's ~4.5MB reply cap.
        gif_omitted = len(gif_b64) > limits.max_payload_b64_bytes
        return 200, {
            "ok": True,
            "mode": "gif",
            "n_frames": len(frames),
            "grid_dim": grid_dim,
            "width": frames[0].size[0],
            "height": frames[0].size[1],
            "qr_gif_base64": "" if gif_omitted else gif_b64,
            "gif_omitted": gif_omitted,
            # honest ballpark, not a promise -- see estimate_scan_seconds
            "estimated_scan_seconds_low": scan_low,
            "estimated_scan_seconds_high": scan_high,
        }

    # mode == "pages"
    pages = []
    total_b64_len = 0
    for frame in frames:
        buf = _io.BytesIO()
        frame.save(buf, format="PNG")
        page_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        total_b64_len += len(page_b64)
        pages.append({"width": frame.size[0], "height": frame.size[1], "png_base64": page_b64})
    pages_omitted = total_b64_len > limits.max_payload_b64_bytes
    return 200, {
        "ok": True,
        "mode": "pages",
        "n_frames": len(frames),
        "grid_dim": grid_dim,
        "pages": [] if pages_omitted else pages,
        "pages_omitted": pages_omitted,
        "estimated_scan_seconds_low": scan_low,
        "estimated_scan_seconds_high": scan_high,
    }


def handle_render(body: dict, limits: Limits) -> tuple[int, dict]:
    """Open an existing balzar artifact -- this is the "Balzar Live"
    generic opener, not just a .bzr/.bzp reader anymore: the magic bytes
    of the decoded upload decide the path (BZR1 -> 2D program, BZM1 ->
    3D scene, BZX1 -> multi-document bundle), not the file extension --
    a browser upload doesn't reliably preserve one, and the three
    formats are trivially distinguishable by their own header anyway
    (same principle chunk_payload/qr.py already use: treat the bytes as
    self-describing, never guess from a filename)."""
    data_b64 = body.get("data")
    if not data_b64:
        return 400, {"ok": False, "error": "campo 'data' mancante"}
    try:
        raw = _b64decode(data_b64)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

    from .bundle import MAGIC as BZX1_MAGIC
    from .scene3d import MAGIC as BZM1_MAGIC

    if raw[:4] == BZX1_MAGIC:
        return _handle_render_bundle(raw, limits)
    if raw[:4] == BZM1_MAGIC:
        return _handle_render_3d(raw, limits)
    return _handle_render_2d(raw, limits)


def _scene3d_stats(scene, collapse_names: set[str] | None = None) -> dict:
    """Shape/reference/instance/vertex counts + BOM straight from an
    already-decoded Scene3D -- no `mean_vertex_error` here (unlike
    Scene3DEncodeResult): that field compares against the original,
    unquantized CAD source, which a re-opened BZM1/BZX1 payload no
    longer carries -- only encode_3dxml_file can report it honestly.

    `collapse_names`: same alarm-table component names passed to
    scene3d_to_glb by the caller, so the BOM this returns and the GLB
    built alongside it stay consistent (see generate_bom)."""
    from .scene3d import generate_bom
    bom = generate_bom(scene, collapse_names)
    return {
        "shape_count": len(scene.shapes),
        "reference_count": len(scene.references),
        "instance_count": sum(len(r.children) for r in scene.references),
        "vertex_count": sum(len(s.vertices) for s in scene.shapes),
        "bom": [{"name": e.name, "count": e.count, "material_names": e.material_names}
               for e in sorted(bom, key=lambda e: -e.count)],
    }


def _handle_render_3d(raw: bytes, limits: Limits) -> tuple[int, dict]:
    """A bare BZM1 payload (e.g. a .b3d saved from the CLI/desktop app,
    outside a bundle): decode straight to a GLB + BOM, same shape as
    handle_encode_3d's response so the frontend can reuse its 3D-tab
    rendering code for the "open" tab too."""
    from .scene3d import Scene3DError, decode_payload

    try:
        scene = decode_payload(raw)
    except Scene3DError as exc:
        return 400, {"ok": False, "error": f"payload 3D non valido: {exc}"}

    from .gltf import scene3d_to_glb
    glb = scene3d_to_glb(scene)
    glb_b64 = base64.b64encode(glb).decode("ascii")
    glb_omitted = len(glb_b64) > limits.max_payload_b64_bytes

    response = {"ok": True, "kind": "3d", "bundled": False,
               "info_table": {"headers": [], "rows": []}, "documents": []}
    response.update(_scene3d_stats(scene))
    response["glb_omitted"] = glb_omitted
    response["glb_base64"] = "" if glb_omitted else glb_b64
    response.update(_payload_response_fields(raw, limits))
    return 200, response


def _handle_render_bundle(raw: bytes, limits: Limits) -> tuple[int, dict]:
    """A BZX1 bundle: same dispatch open_bundle_in_browser already does
    for the desktop viewer (balzar/viewer3d.py), reused here to build a
    JSON response instead of an HTML page -- at most one 3D item is
    shown (the first, same rule as the desktop viewer), every alarm item
    feeds the search bar, every 2d/alarm/doc item lands in the document
    index via the same _documents_from_items already used by the
    "Assemblee 3D" tab's own bundle path."""
    from .bundle import BundleError, KIND_3D, decode_bundle, is_alarm_kind

    try:
        items = decode_bundle(raw)
    except BundleError as exc:
        return 400, {"ok": False, "error": f"bundle non valido: {exc}"}

    from .viewer3d import _documents_from_items, parse_component_table_text

    three_d_items = [it for it in items if it.kind == KIND_3D]
    response = {"ok": True, "kind": "bundle", "bundled": True, "has_3d": bool(three_d_items)}

    # parsed before the 3D block below so its cell values can collapse
    # the BOM/GLB the same way handle_encode_3d does -- see generate_bom.
    # Only the first alarm-marked item becomes the info table (same
    # "first one wins" rule as viewer3d.open_bundle_in_browser): several
    # could have entirely different columns, so concatenating rows
    # across mismatched schemas doesn't generalize the way it safely
    # could for the old fixed two-column format.
    info_table = None
    try:
        for it in items:
            if is_alarm_kind(it.kind):
                info_table = parse_component_table_text(it.data.decode("utf-8"))
                break
    except UnicodeDecodeError as exc:
        return 400, {"ok": False, "error": f"tabella componenti nel bundle non e' UTF-8 valida: {exc}"}
    except ValueError as exc:
        return 400, {"ok": False, "error": f"tabella componenti nel bundle non valida: {exc}"}
    response["info_table"] = info_table.to_json_dict() if info_table else {"headers": [], "rows": []}
    collapse_names = info_table.all_values() if info_table else None

    if three_d_items:
        from .scene3d import Scene3DError, decode_payload
        try:
            scene = decode_payload(three_d_items[0].data)
        except Scene3DError as exc:
            return 400, {"ok": False, "error": f"assieme 3D nel bundle non valido: {exc}"}
        from .gltf import scene3d_to_glb
        glb = scene3d_to_glb(scene, collapse_names=collapse_names)
        glb_b64 = base64.b64encode(glb).decode("ascii")
        glb_omitted = len(glb_b64) > limits.max_payload_b64_bytes
        response.update(_scene3d_stats(scene, collapse_names))
        response["glb_omitted"] = glb_omitted
        response["glb_base64"] = "" if glb_omitted else glb_b64

    try:
        response["documents"] = _documents_from_items(items)
    except (SyntaxError, ValueError, RuntimeError) as exc:
        return 400, {"ok": False, "error": f"documento nel bundle non valido: {exc}"}

    response.update(_payload_response_fields(raw, limits))
    return 200, response


def _handle_render_2d(raw: bytes, limits: Limits) -> tuple[int, dict]:
    """A bare BZR1 payload or plain DSL source text (no recognized
    binary magic): the original "Apri programma" path, unchanged except
    for the added `kind` discriminator field."""
    from .payload import MAGIC, PayloadError, decode_payload

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
        "kind": "2d",
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
