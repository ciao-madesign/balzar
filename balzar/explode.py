"""Automatic exploded view: one CAD/vector file -> a payload of N+1 frames
where every layer/group is pushed radially away from the drawing's overall
centroid, one step further per frame (frame 0 = fully assembled).

Grouping is by _Shape.layer (DXF group code 8 / SVG <g id>) — the natural
"this is one part" unit in a real CAD drawing (same key vectorio.py already
carries on every shape). A layer's own direction is the vector from the
drawing's centroid to that layer's centroid; a layer that happens to sit
exactly on the drawing centroid has a zero-length direction and correctly
does not move (nothing to explode a part away from itself).

This does NOT reuse sequence.py's text-line dedup: that delta only saves
bytes when geometry is purely additive (nothing already drawn ever moves).
Here every group moves every frame, and the interpreter's canvas is
cumulative (FRAME snapshots, never clears) — so skipping a "seen" line
would leave a ghost of the old position on screen. The correct model for
moving content is a full repaint per frame: FILL the whole canvas back to
background, then draw every shape at its current frame's position. That
costs more per frame than sequence.py's model, but it is the only one that
renders correctly.

Rotation (2D or 3D) is out of scope for this module by explicit choice —
this is a straight-line radial explosion only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dsl import canonical
from .interpreter import render as render_program
from .payload import encode_payload
from .vectorio import (_Shape, _PaletteBuilder, _emit_shapes, _fit_transform,
                       parse_vector_file, shapes_bounds)


class ExplodeError(ValueError):
    pass


@dataclass
class ExplodeResult:
    program_text: str
    payload: bytes
    width: int
    height: int
    frame_count: int
    group_count: int
    instruction_count: int
    skipped: list[str] = field(default_factory=list)
    source_format: str = ""


def explode_vector_file(path: str, steps: int = 6, spacing: float = 0.6,
                        max_dim: int = 800) -> ExplodeResult:
    """steps: frames of explosion AFTER the assembled starting frame (the
    payload has steps+1 frames total). spacing: fraction of a group's own
    distance-from-centroid added per step (0.6 => each step pushes a group
    60% of its own radial distance further out)."""
    if steps < 1:
        raise ExplodeError("servono almeno 1 step di esplosione (oltre al frame assemblato)")
    if spacing <= 0:
        raise ExplodeError("spacing deve essere positivo")

    shapes, _bounds, skipped, fmt = parse_vector_file(path)
    if not shapes:
        raise ExplodeError("nessuna geometria trovata nel file")

    groups: dict[str, list[_Shape]] = {}
    for s in shapes:
        groups.setdefault(s.layer, []).append(s)

    if len(groups) < 2:
        raise ExplodeError(
            "un solo layer/gruppo trovato: niente da esplodere — l'esploso "
            "automatico raggruppa per layer DXF / <g id> SVG, il disegno "
            "deve avere piu' di un gruppo")

    min_x, min_y, max_x, max_y = shapes_bounds(shapes)
    cx0, cy0 = (min_x + max_x) / 2, (min_y + max_y) / 2

    directions: dict[str, tuple[float, float]] = {}
    for layer, group_shapes in groups.items():
        gcx = sum(s.center()[0] for s in group_shapes) / len(group_shapes)
        gcy = sum(s.center()[1] for s in group_shapes) / len(group_shapes)
        directions[layer] = (gcx - cx0, gcy - cy0)

    # worst-case spread (last frame, every group at max offset) sizes the
    # shared transform once so no frame needs its own rescaling
    max_dx = max((abs(dx) * spacing * steps for dx, _dy in directions.values()), default=0.0)
    max_dy = max((abs(dy) * spacing * steps for _dx, dy in directions.values()), default=0.0)
    full_min_x, full_min_y = min_x - max_dx, min_y - max_dy
    full_max_x, full_max_y = max_x + max_dx, max_y + max_dy

    flip_y = (fmt == "dxf")
    transform, width, height, scale = _fit_transform(
        full_min_x, full_min_y, full_max_x, full_max_y, max_dim, flip_y)

    palette = _PaletteBuilder()
    palette.get((255, 255, 255))
    bg_idx = 0

    full_region = f"REGION name=__FULL__ x=0 y=0 w={width} h={height}"
    lines: list[str] = [full_region]
    n_instr = 1
    for step in range(steps + 1):
        lines.append(f"FILL region=__FULL__ color={bg_idx}")
        n_instr += 1
        frame_shapes = []
        for layer, group_shapes in groups.items():
            dx_dir, dy_dir = directions[layer]
            dx, dy = dx_dir * spacing * step, dy_dir * spacing * step
            frame_shapes.extend(s.translated(dx, dy) for s in group_shapes)
        new_lines = _emit_shapes(frame_shapes, transform, scale, palette)
        lines.extend(new_lines)
        n_instr += len(new_lines)
        lines.append("FRAME")
        n_instr += 1

    program_text = ("\n".join([f"CANVAS w={width} h={height} bg=0",
                              *palette.palette_lines(), *lines]) + "\n")
    rendered = render_program(program_text)
    if len(rendered.frames) != steps + 1:
        raise RuntimeError("explode encoder self-check failed: frame count mismatch")

    payload = encode_payload(program_text)
    return ExplodeResult(
        program_text=canonical(program_text),
        payload=payload,
        width=width,
        height=height,
        frame_count=steps + 1,
        group_count=len(groups),
        instruction_count=n_instr,
        skipped=skipped,
        source_format=fmt,
    )
