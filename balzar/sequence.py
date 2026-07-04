"""Multi-file sequences: several separate files combined into one
multi-FRAME payload, instead of one payload per file.

Two independent paths, chosen by input type:

  vector sequence (SVG *or* DXF, one format per call — see below): every
  file is parsed with vectorio.parse_vector_file into raw shapes, all
  sharing ONE canvas/palette/coordinate transform computed from the union
  of every file's bounding box. This is what keeps a part introduced in
  step 5 from shifting the whole drawing's scale. The delta between steps
  is a plain text-line dedup (a shape already emitted in an earlier frame
  costs nothing in a later one) — exact, but only correct for content that
  is purely additive step over step (geometry appears, never moves or
  disappears). That is the real shape of a montage/assembly sequence
  (examples/sequenza_montaggio.bzr); it is NOT what explode.py needs,
  which is why explode.py does its own thing instead of reusing this dedup.

  raster sequence (PNG/JPEG/...): each file decoded independently via
  imageio, forced onto ONE shared canvas size (the first file's computed
  size after --max-dim scaling), then handed whole to video.encode_video,
  which already does true pixel-delta encoding between frames.

Mixed formats in one call are rejected, not guessed at.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .dsl import canonical
from .interpreter import render as render_program
from .payload import encode_payload
from .vectorio import (_PaletteBuilder, _emit_shapes, _fit_transform,
                       parse_vector_file, shapes_bounds)


class SequenceError(ValueError):
    pass


@dataclass
class SequenceEncodeResult:
    program_text: str
    payload: bytes
    width: int
    height: int
    frame_count: int
    instruction_count: int
    skipped: list[str] = field(default_factory=list)
    source_format: str = ""


def encode_vector_sequence(paths: list[str], max_dim: int = 800) -> SequenceEncodeResult:
    if len(paths) < 2:
        raise SequenceError("una sequenza richiede almeno 2 file")

    exts = {os.path.splitext(p)[1].lower() for p in paths}
    if exts == {".svg"}:
        fmt = "svg"
    elif exts == {".dxf"}:
        fmt = "dxf"
    else:
        raise SequenceError(
            "sequenza vettoriale: tutti i file devono essere dello stesso "
            f"formato (solo .svg o solo .dxf), trovati: {sorted(exts)}")

    per_file_shapes = []
    skipped: list[str] = []
    for path in paths:
        shapes, _bounds, file_skipped, _fmt = parse_vector_file(path)
        per_file_shapes.append(shapes)
        name = os.path.basename(path)
        skipped.extend(f"{name}: {reason}" for reason in file_skipped)

    all_shapes = [s for shapes in per_file_shapes for s in shapes]
    min_x, min_y, max_x, max_y = shapes_bounds(all_shapes)
    transform, width, height, scale = _fit_transform(
        min_x, min_y, max_x, max_y, max_dim, flip_y=(fmt == "dxf"))

    palette = _PaletteBuilder()
    palette.get((255, 255, 255))

    seen: set[str] = set()
    lines: list[str] = []
    n_instr = 0
    for shapes in per_file_shapes:
        new_lines = _emit_shapes(shapes, transform, scale, palette, seen=seen)
        lines.extend(new_lines)
        n_instr += len(new_lines)
        lines.append("FRAME")
        n_instr += 1

    program_text = ("\n".join([f"CANVAS w={width} h={height} bg=0",
                              *palette.palette_lines(), *lines]) + "\n")
    rendered = render_program(program_text)
    if len(rendered.frames) != len(paths):
        raise RuntimeError("sequence encoder self-check failed: frame count mismatch")

    payload = encode_payload(program_text)
    return SequenceEncodeResult(
        program_text=canonical(program_text),
        payload=payload,
        width=width,
        height=height,
        frame_count=len(paths),
        instruction_count=n_instr,
        skipped=skipped,
        source_format=fmt,
    )


def encode_raster_sequence(paths: list[str], max_dim: int = 400):
    """Each file is one still frame; returns video.VideoEncodeResult since
    the semantics (shared palette, true pixel delta between frames) are
    identical to encode_video — a sequence of stills IS a video here."""
    if len(paths) < 2:
        raise SequenceError("una sequenza richiede almeno 2 file")

    from .imageio import load_rgb, load_rgb_fixed
    from .video import encode_video

    width = height = None
    frames: list[bytes] = []
    for path in paths:
        with open(path, "rb") as fh:
            data = fh.read()
        if width is None:
            width, height, rgb = load_rgb(data, max_dim=max_dim)
        else:
            rgb = load_rgb_fixed(data, width, height)
        frames.append(rgb)

    return encode_video(width, height, frames)
