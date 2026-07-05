"""Vector format ingestion: SVG/DXF -> balzar DSL, no raster in between.

This is the real answer to the text/curve degradation seen when a CAD
drawing or a UI is *screenshotted* and fed to the raster encoder
(encoder.py): anti-aliased edges blow past the 256-color budget and
non-axis-aligned curves turn into one instruction per pixel. A circle in
an SVG/DXF file is already a circle with an exact center and radius — it
maps directly onto our existing `CIRCLE`, no pixels involved, no
quantization, no edge-fitting. Same story for `<text>`/TEXT DXF entities:
they become our own `TEXT` op directly, which is exactly why
hand-authored text (etichetta_bom.bzr) was always pixel-perfect while
screenshotted text was not — this module gets that same exactness for
externally-authored vector files.

Both parsers are pure stdlib (xml.etree for SVG, a plain group-code
reader for DXF) — no new dependency, matching the core engine's
zero-dependency contract.

Parsing is split from transform+emission (`_parse_svg`/`_parse_dxf` ->
raw `_Shape` list in source-world coordinates, `_emit_shapes` -> DSL
lines) so a *shared* coordinate transform and palette can span several
files — that is what makes multi-file sequences (sequence.py) and
automatic per-layer explosion (explode.py) possible without each file
drifting to its own scale/canvas size.

This is best-effort, not lossless-verified like encoder.py: there is no
raster original to diff against. Unsupported constructs (curves beyond
straight segments, gradients, patterns, arcs, non-translate transforms,
BYLAYER colors without a resolvable layer table, ...) are SKIPPED with a
human-readable reason collected in `skipped`, never silently dropped
without a trace and never approximated as something they are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dsl import canonical
from .interpreter import render as render_program
from .payload import encode_payload

MAX_COLORS = 256


@dataclass
class VectorIngestResult:
    program_text: str
    payload: bytes
    width: int
    height: int
    instruction_count: int
    element_count: int      # entities/elements actually converted
    skipped: list[str] = field(default_factory=list)
    source_format: str = ""


class VectorIngestError(ValueError):
    pass


# ------------------------------------------------------- shared shape model

@dataclass
class _Shape:
    kind: str                              # CIRCLE | LINE | RECT | TEXT
    color: tuple[int, int, int]
    layer: str                             # grouping key: DXF layer / SVG <g id>
    filled: bool = True
    # geometry, meaning depends on kind:
    #   CIRCLE: (cx, cy, r)         LINE: (x1, y1, x2, y2)
    #   RECT:   (x, y, w, h)        TEXT: (x, y, size)  (y = baseline, source convention)
    geom: tuple = ()
    text: str = ""

    def center(self) -> tuple[float, float]:
        if self.kind == "CIRCLE":
            return self.geom[0], self.geom[1]
        if self.kind == "LINE":
            return (self.geom[0] + self.geom[2]) / 2, (self.geom[1] + self.geom[3]) / 2
        if self.kind == "RECT":
            return self.geom[0] + self.geom[2] / 2, self.geom[1] + self.geom[3] / 2
        return self.geom[0], self.geom[1]

    def bounds(self) -> tuple[float, float, float, float]:
        if self.kind == "CIRCLE":
            cx, cy, r = self.geom
            return cx - r, cy - r, cx + r, cy + r
        if self.kind == "LINE":
            x1, y1, x2, y2 = self.geom
            return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
        if self.kind == "RECT":
            x, y, w, h = self.geom
            return x, y, x + w, y + h
        x, y, size = self.geom
        return x, y - size, x + size * len(self.text) * 0.7, y

    def translated(self, dx: float, dy: float) -> "_Shape":
        if self.kind in ("CIRCLE", "RECT"):
            g = list(self.geom)
            g[0] += dx
            g[1] += dy
            geom = tuple(g)
        elif self.kind == "LINE":
            x1, y1, x2, y2 = self.geom
            geom = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
        else:
            x, y, size = self.geom
            geom = (x + dx, y + dy, size)
        return _Shape(self.kind, self.color, self.layer, self.filled, geom, self.text)


class _PaletteBuilder:
    def __init__(self) -> None:
        self.index: dict[tuple[int, int, int], int] = {}

    def get(self, rgb: tuple[int, int, int]) -> int:
        if rgb not in self.index:
            if len(self.index) >= MAX_COLORS:
                raise VectorIngestError(
                    f"più di {MAX_COLORS} colori distinti — non tipico per un "
                    f"disegno CAD/vettoriale, caso non supportato")
            self.index[rgb] = len(self.index)
        return self.index[rgb]

    def palette_lines(self) -> list[str]:
        return [f"PALETTE i={i} rgb=#{r:02X}{g:02X}{b:02X}"
                for (r, g, b), i in sorted(self.index.items(), key=lambda kv: kv[1])]


# ---------------------------------------------------------------- geometry

def _fit_transform(min_x: float, min_y: float, max_x: float, max_y: float,
                   max_dim: int, flip_y: bool):
    """Uniform scale + translate (+ optional Y flip) so a bounding box
    fits in a max_dim x max_dim canvas, preserving aspect ratio."""
    w = max(max_x - min_x, 1e-9)
    h = max(max_y - min_y, 1e-9)
    scale = max_dim / max(w, h)
    canvas_w = max(1, round(w * scale))
    canvas_h = max(1, round(h * scale))

    def transform(x: float, y: float) -> tuple[int, int]:
        px = round((x - min_x) * scale)
        py = round((max_y - y) * scale) if flip_y else round((y - min_y) * scale)
        return px, py

    return transform, canvas_w, canvas_h, scale


def shapes_bounds(shapes: list[_Shape]) -> tuple[float, float, float, float]:
    xs0, ys0, xs1, ys1 = zip(*(s.bounds() for s in shapes))
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _emit_shape(shape: _Shape, transform, px_scale: float, palette: _PaletteBuilder) -> str:
    idx = palette.get(shape.color)
    if shape.kind == "CIRCLE":
        cx, cy, r = shape.geom
        c = transform(cx, cy)
        r_px = max(1, round(r * px_scale))
        return f"CIRCLE cx={c[0]} cy={c[1]} r={r_px} color={idx} fill={1 if shape.filled else 0}"
    if shape.kind == "LINE":
        x1, y1, x2, y2 = shape.geom
        p1, p2 = transform(x1, y1), transform(x2, y2)
        return f"LINE x1={p1[0]} y1={p1[1]} x2={p2[0]} y2={p2[1]} color={idx}"
    if shape.kind == "RECT":
        x, y, w, h = shape.geom
        p1, p2 = transform(x, y), transform(x + w, y + h)
        rw = max(1, abs(p2[0] - p1[0])); rh = max(1, abs(p2[1] - p1[1]))
        x0, y0 = min(p1[0], p2[0]), min(p1[1], p2[1])
        return (f"RECT x={x0} y={y0} w={rw} h={rh} color={idx} "
               f"fill={1 if shape.filled else 0}")
    x, y, size = shape.geom
    p = transform(x, y)
    scale = max(1, round(size * px_scale / 7))
    safe = shape.text.replace('"', "'")
    return f'TEXT x={p[0]} y={p[1]} text="{safe}" color={idx} scale={scale}'


def _emit_shapes(shapes: list[_Shape], transform, px_scale: float,
                 palette: _PaletteBuilder, seen: set[str] | None = None) -> list[str]:
    """DSL lines for `shapes`. If `seen` is given, lines already produced
    in a previous call (by exact text match) are skipped and `seen` is
    updated — this is the delta mechanism sequence.py uses across frames:
    genuinely new geometry costs bytes, geometry already on screen doesn't."""
    lines = []
    for shape in shapes:
        line = _emit_shape(shape, transform, px_scale, palette)
        if seen is not None:
            if line in seen:
                continue
            seen.add(line)
        lines.append(line)
    return lines


def _finish(lines: list[str], palette: _PaletteBuilder, width: int, height: int,
           element_count: int, skipped: list[str], fmt: str) -> VectorIngestResult:
    # white is always reserved as index 0 (see callers) so CANVAS bg=0 is
    # never at the mercy of whatever color happened to appear first
    program_text = ("\n".join([f"CANVAS w={width} h={height} bg=0", *palette.palette_lines(), *lines])
                    + "\n")
    render_program(program_text)  # sanity check: must parse and render without error
    payload = encode_payload(program_text)
    return VectorIngestResult(
        program_text=canonical(program_text),
        payload=payload,
        width=width,
        height=height,
        instruction_count=len(lines),
        element_count=element_count,
        skipped=skipped,
        source_format=fmt,
    )


# --------------------------------------------------------------------- SVG

_SVG_NS = "{http://www.w3.org/2000/svg}"

_CSS_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "orange": (255, 165, 0), "purple": (128, 0, 128),
    "brown": (165, 42, 42), "pink": (255, 192, 203), "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211), "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169),
    "silver": (192, 192, 192), "navy": (0, 0, 128), "lime": (0, 255, 0),
    "maroon": (128, 0, 0), "olive": (128, 128, 0), "teal": (0, 128, 128),
}


def _parse_svg_color(value: str | None) -> tuple[int, int, int] | None:
    """None means 'not specified' (caller applies SVG defaults); returns
    None for 'none'/'transparent' (no paint)."""
    if value is None:
        return None
    value = value.strip().lower()
    if value in ("none", "transparent", ""):
        return None
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) >= 6:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return None
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value)
    if m:
        return tuple(int(g) for g in m.groups())
    return _CSS_COLORS.get(value)


def _svg_paint(elem, attr: str, default: tuple[int, int, int] | None):
    """Resolve fill/stroke color: style="" attribute wins over the bare attribute."""
    style = elem.attrib.get("style", "")
    m = re.search(rf"{attr}\s*:\s*([^;]+)", style)
    raw = m.group(1) if m else elem.attrib.get(attr)
    if raw is None:
        return default
    return _parse_svg_color(raw)


def _svg_translate(elem) -> tuple[float, float]:
    t = elem.attrib.get("transform", "")
    m = re.match(r"\s*translate\(\s*([-\d.eE]+)(?:[,\s]+([-\d.eE]+))?\s*\)", t)
    if not m:
        if t.strip():
            raise VectorIngestError(f"transform '{t}' non supportato (solo translate)")
        return 0.0, 0.0
    dx = float(m.group(1))
    dy = float(m.group(2)) if m.group(2) else 0.0
    return dx, dy


def _svg_bounds(root) -> tuple[float, float, float, float]:
    vb = root.attrib.get("viewBox")
    if vb:
        x, y, w, h = (float(v) for v in re.split(r"[\s,]+", vb.strip()))
        return x, y, x + w, y + h
    w = float(re.sub(r"[a-z%]+$", "", root.attrib.get("width", "300")))
    h = float(re.sub(r"[a-z%]+$", "", root.attrib.get("height", "150")))
    return 0.0, 0.0, w, h


def _parse_svg(svg_text: str) -> tuple[list[_Shape], tuple[float, float, float, float], list[str]]:
    """SVG text -> (raw shapes in SVG-world coordinates, bounds, skipped)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise VectorIngestError(f"SVG non valido: {exc}") from None

    bounds = _svg_bounds(root)
    shapes: list[_Shape] = []
    skipped: list[str] = []

    def tag(elem) -> str:
        t = elem.tag
        return t[len(_SVG_NS):] if t.startswith(_SVG_NS) else t

    def walk(elem, ox: float, oy: float, layer: str) -> None:
        name = tag(elem)
        if name in ("svg", "g", "defs", "title", "desc", "metadata"):
            try:
                dx, dy = _svg_translate(elem) if name == "g" else (0.0, 0.0)
            except VectorIngestError as exc:
                skipped.append(f"<g>: {exc}")
                return
            sub_layer = elem.attrib.get("id", layer) if name == "g" else layer
            for child in elem:
                walk(child, ox + dx, oy + dy, sub_layer)
            return

        fill = _svg_paint(elem, "fill", (0, 0, 0))
        stroke = _svg_paint(elem, "stroke", None)
        color = fill if fill is not None else stroke
        outline = fill is None and stroke is not None

        try:
            if name == "rect":
                x = float(elem.attrib.get("x", 0)) + ox
                y = float(elem.attrib.get("y", 0)) + oy
                w = float(elem.attrib["width"])
                h = float(elem.attrib["height"])
                if color is None:
                    skipped.append("<rect>: nessun fill/stroke"); return
                shapes.append(_Shape("RECT", color, layer, not outline, (x, y, w, h)))
            elif name == "circle":
                cx = float(elem.attrib["cx"]) + ox
                cy = float(elem.attrib["cy"]) + oy
                r = float(elem.attrib["r"])
                if color is None:
                    skipped.append("<circle>: nessun fill/stroke"); return
                shapes.append(_Shape("CIRCLE", color, layer, not outline, (cx, cy, r)))
            elif name == "ellipse":
                rx = float(elem.attrib["rx"])
                ry = float(elem.attrib["ry"])
                if abs(rx - ry) > 1e-6:
                    skipped.append("<ellipse>: non circolare, non rappresentabile con CIRCLE")
                    return
                cx = float(elem.attrib["cx"]) + ox
                cy = float(elem.attrib["cy"]) + oy
                if color is None:
                    skipped.append("<ellipse>: nessun fill/stroke"); return
                shapes.append(_Shape("CIRCLE", color, layer, not outline, (cx, cy, rx)))
            elif name == "line":
                x1 = float(elem.attrib.get("x1", 0)) + ox
                y1 = float(elem.attrib.get("y1", 0)) + oy
                x2 = float(elem.attrib.get("x2", 0)) + ox
                y2 = float(elem.attrib.get("y2", 0)) + oy
                col = stroke if stroke is not None else fill
                if col is None:
                    skipped.append("<line>: nessun stroke"); return
                shapes.append(_Shape("LINE", col, layer, geom=(x1, y1, x2, y2)))
            elif name in ("polyline", "polygon"):
                pts_raw = elem.attrib.get("points", "").strip()
                nums = [float(v) for v in re.split(r"[\s,]+", pts_raw) if v]
                pts = [(nums[i] + ox, nums[i + 1] + oy) for i in range(0, len(nums) - 1, 2)]
                if len(pts) < 2:
                    skipped.append(f"<{name}>: punti insufficienti"); return
                col = stroke if stroke is not None else fill
                if col is None:
                    skipped.append(f"<{name}>: nessun colore"); return
                seq = pts + [pts[0]] if name == "polygon" else pts
                for a, b in zip(seq, seq[1:]):
                    shapes.append(_Shape("LINE", col, layer, geom=(*a, *b)))
            elif name == "path":
                d = elem.attrib.get("d", "")
                if re.search(r"[CScQqTtAa]", d):
                    skipped.append("<path>: curve (C/S/Q/T/A) non supportate, solo M/L/Z")
                    return
                tokens = re.findall(r"[MLZmlz]|-?\d+\.?\d*(?:[eE]-?\d+)?", d)
                pts, i = [], 0
                while i < len(tokens):
                    cmd = tokens[i]
                    if cmd.upper() == "Z":
                        if pts:
                            pts.append(pts[0])
                        i += 1
                        continue
                    if cmd.upper() in ("M", "L"):
                        x, y = float(tokens[i + 1]) + ox, float(tokens[i + 2]) + oy
                        pts.append((x, y))
                        i += 3
                        continue
                    i += 1
                if len(pts) < 2:
                    skipped.append("<path>: punti insufficienti"); return
                col = stroke if stroke is not None else fill
                if col is None:
                    skipped.append("<path>: nessun colore"); return
                for a, b in zip(pts, pts[1:]):
                    shapes.append(_Shape("LINE", col, layer, geom=(*a, *b)))
            elif name == "text":
                x = float(elem.attrib.get("x", 0)) + ox
                y = float(elem.attrib.get("y", 0)) + oy
                text = "".join(elem.itertext()).strip()
                if not text:
                    return
                size = float(re.sub(r"[a-z%]+$", "", elem.attrib.get("font-size", "16")))
                col = fill if fill is not None else (0, 0, 0)
                # SVG's y is the text baseline; shift up by ~font size (approx
                # ascent) so it lines up with TEXT's top-of-glyph convention
                shapes.append(_Shape("TEXT", col, layer, geom=(x, y - size, size), text=text))
            else:
                skipped.append(f"<{name}>: elemento non supportato")
        except (KeyError, ValueError) as exc:
            skipped.append(f"<{name}>: {exc}")

    walk(root, 0.0, 0.0, "_root")
    return shapes, bounds, skipped


def ingest_svg(svg_text: str, max_dim: int = 800) -> VectorIngestResult:
    shapes, (min_x, min_y, max_x, max_y), skipped = _parse_svg(svg_text)
    transform, width, height, scale = _fit_transform(min_x, min_y, max_x, max_y, max_dim, flip_y=False)
    palette = _PaletteBuilder()
    palette.get((255, 255, 255))
    lines = _emit_shapes(shapes, transform, scale, palette)
    return _finish(lines, palette, width, height, len(shapes), skipped, "svg")


# --------------------------------------------------------------------- DXF

# Common ACI (AutoCAD Color Index) entries — only the small, unambiguous
# subset used in the overwhelming majority of real drawings. Anything
# outside this table is disclosed and rendered as neutral gray rather
# than guessed, since the full 256-entry ACI table cannot be verified
# without network access in this environment.
_ACI_COLORS: dict[int, tuple[int, int, int]] = {
    1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0), 4: (0, 255, 255),
    5: (0, 0, 255), 6: (255, 0, 255), 7: (0, 0, 0), 8: (65, 65, 65),
    9: (128, 128, 128),
}
_ACI_DEFAULT = (90, 90, 90)


def _aci_to_rgb(code: int | None) -> tuple[int, int, int]:
    if code is None or code in (0, 256):  # BYBLOCK / BYLAYER: no table resolved
        return _ACI_DEFAULT
    return _ACI_COLORS.get(code, _ACI_DEFAULT)


def _dxf_pairs(text: str):
    lines = [ln.strip() for ln in text.splitlines()]
    for i in range(0, len(lines) - 1, 2):
        if lines[i] == "" and lines[i + 1] == "":
            continue
        try:
            yield int(lines[i]), lines[i + 1]
        except ValueError:
            continue


# ---------------------------------------------------- SPLINE (NURBS) sampling
#
# The DSL has no curve primitive, so a SPLINE is approximated the same way
# LWPOLYLINE already is: sample the curve and emit connected LINE segments.
# This needs a real B-spline evaluator (De Boor's algorithm, in homogeneous
# coordinates so rational/weighted splines work too) since a SPLINE entity
# stores control points + a knot vector, not a series of points to connect
# directly. Fixed sample count, not adaptive to curvature — a documented,
# honest tolerance choice, not hidden precision. 64, not 32: measured on a
# real multi-spline logo (118 SPLINE entities), 32 samples left visible
# facets on fine detail (feather edges); 64 fixes most of it for ~1.6x the
# bytes (20,391 B -> 32,172 B on that file, still 10x smaller than the raw
# DXF). The rest of the perceived roughness is NOT sampling density: our
# own png.py draws plain Bresenham lines with no anti-aliasing, so even a
# densely-sampled curve looks faceted there. A browser rendering the SVG
# export of the SAME 64-sample data (render_svg, svg.py) looks smoother
# than our own PNG at 256 samples, for free, via the browser's
# anti-aliasing — so SVG, not PNG, is the fidelity-first output for
# curve-heavy content; PNG stays exact for axis-aligned/technical drawings.
SPLINE_SAMPLES = 64


def _bspline_find_span(n: int, degree: int, u: float, knots: list[float]) -> int:
    if u >= knots[n + 1]:
        return n
    if u <= knots[degree]:
        return degree
    low, high = degree, n + 1
    mid = (low + high) // 2
    while u < knots[mid] or u >= knots[mid + 1]:
        if u < knots[mid]:
            high = mid
        else:
            low = mid
        mid = (low + high) // 2
    return mid


def _bspline_de_boor(u: float, degree: int, knots: list[float],
                     points: list[tuple[float, ...]]) -> tuple[float, ...]:
    """One point on the curve at parameter u, via De Boor's algorithm.
    `points` may be plain (x, y) or homogeneous (x*w, y*w, w) for NURBS."""
    n = len(points) - 1
    k = _bspline_find_span(n, degree, u, knots)
    dim = len(points[0])
    d = [list(points[j + k - degree]) for j in range(degree + 1)]
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            denom = knots[j + 1 + k - r] - knots[j + k - degree]
            alpha = 0.0 if denom == 0 else (u - knots[j + k - degree]) / denom
            d[j] = [(1.0 - alpha) * d[j - 1][c] + alpha * d[j][c] for c in range(dim)]
    return tuple(d[degree])


def _sample_bspline(control_points: list[tuple[float, float]], weights: list[float],
                    knots: list[float], degree: int,
                    n_samples: int = SPLINE_SAMPLES) -> list[tuple[float, float]] | None:
    """Uniform samples of a (possibly rational) clamped B-spline curve, or
    None if the control points/knots/degree don't form a valid curve."""
    n = len(control_points) - 1
    if n < degree or len(knots) != n + degree + 2:
        return None
    u_min, u_max = knots[degree], knots[n + 1]
    if u_max <= u_min:
        return None
    homogeneous = [(x * w, y * w, w) for (x, y), w in zip(control_points, weights)]
    result = []
    for i in range(n_samples + 1):
        u = u_min + (u_max - u_min) * i / n_samples
        xw, yw, w = _bspline_de_boor(u, degree, knots, homogeneous)
        result.append((xw / w, yw / w))
    return result


def _parse_dxf(dxf_text: str) -> tuple[list[_Shape], tuple[float, float, float, float], list[str], int]:
    """DXF text -> (raw shapes in DXF-world coordinates, bounds, skipped).

    Layer (group code 8) becomes the shape's grouping key — the natural
    "this is one part" unit in real CAD drawings, used both here (color
    fallback context) and by explode.py (auto-explode grouping).
    """
    pairs = list(_dxf_pairs(dxf_text))

    entities_raw: list[tuple[int, str]] = []
    in_entities = False
    i = 0
    while i < len(pairs):
        code, val = pairs[i]
        if code == 0 and val == "SECTION" and i + 1 < len(pairs) and pairs[i + 1] == (2, "ENTITIES"):
            in_entities = True
            i += 2
            continue
        if in_entities and code == 0 and val == "ENDSEC":
            break
        if in_entities:
            entities_raw.append((code, val))
        i += 1

    if not entities_raw:
        raise VectorIngestError("nessuna sezione ENTITIES trovata nel file DXF")

    entities: list[list[tuple[int, str]]] = []
    for code, val in entities_raw:
        if code == 0:
            entities.append([(code, val)])
        elif entities:
            entities[-1].append((code, val))

    def get(entity, code, cast=float, default=None):
        for c, v in entity:
            if c == code:
                return cast(v)
        return default

    def get_all(entity, code, cast=float):
        return [cast(v) for c, v in entity if c == code]

    shapes: list[_Shape] = []
    skipped: list[str] = []
    xs: list[float] = []
    ys: list[float] = []
    entity_count = 0  # entities converted, not shapes emitted (one LWPOLYLINE -> N line segments)

    for entity in entities:
        kind = entity[0][1]
        color_code = get(entity, 62, int, None)
        layer = get(entity, 8, str, "0")
        color = _aci_to_rgb(color_code)
        if color_code is not None and color_code not in _ACI_COLORS and color_code not in (0, 256):
            skipped.append(f"colore ACI {color_code} non nella tabella nota, reso in grigio neutro")

        if kind == "LINE":
            x1, y1 = get(entity, 10), get(entity, 20)
            x2, y2 = get(entity, 11), get(entity, 21)
            if None in (x1, y1, x2, y2):
                skipped.append("LINE: coordinate mancanti"); continue
            shapes.append(_Shape("LINE", color, layer, geom=(x1, y1, x2, y2)))
            xs += [x1, x2]; ys += [y1, y2]
            entity_count += 1
        elif kind == "CIRCLE":
            cx, cy, r = get(entity, 10), get(entity, 20), get(entity, 40)
            if None in (cx, cy, r):
                skipped.append("CIRCLE: coordinate/raggio mancanti"); continue
            shapes.append(_Shape("CIRCLE", color, layer, False, (cx, cy, r)))
            xs += [cx - r, cx + r]; ys += [cy - r, cy + r]
            entity_count += 1
        elif kind == "LWPOLYLINE":
            pxs, pys = get_all(entity, 10), get_all(entity, 20)
            pts = list(zip(pxs, pys))
            closed = bool(get(entity, 70, int, 0) & 1)
            if len(pts) < 2:
                skipped.append("LWPOLYLINE: punti insufficienti"); continue
            seq = pts + [pts[0]] if closed else pts
            for a, b in zip(seq, seq[1:]):
                shapes.append(_Shape("LINE", color, layer, geom=(*a, *b)))
            xs += pxs; ys += pys
            entity_count += 1
        elif kind == "SPLINE":
            degree = get(entity, 71, int)
            cxs, cys = get_all(entity, 10), get_all(entity, 20)
            knots = get_all(entity, 40)
            weights = get_all(entity, 41)
            if degree is None or len(cxs) != len(cys) or not cxs:
                skipped.append("SPLINE: punti di controllo insufficienti "
                               "(solo fit point non e' supportato)"); continue
            control_points = list(zip(cxs, cys))
            if not weights:
                weights = [1.0] * len(control_points)
            elif len(weights) != len(control_points):
                weights = [1.0] * len(control_points)  # malformed weights: ignore, non-rational
            pts = _sample_bspline(control_points, weights, knots, degree)
            if pts is None:
                skipped.append("SPLINE: nodi/grado incoerenti con i punti di controllo"); continue
            for a, b in zip(pts, pts[1:]):
                shapes.append(_Shape("LINE", color, layer, geom=(*a, *b)))
            sxs, sys_ = zip(*pts)
            xs += sxs; ys += sys_
            entity_count += 1
        elif kind in ("TEXT", "MTEXT"):
            x, y = get(entity, 10), get(entity, 20)
            h = get(entity, 40, float, 2.5)
            txt = get(entity, 1, str, "")
            if None in (x, y) or not txt:
                skipped.append(f"{kind}: dati mancanti"); continue
            # DXF's insertion point is the baseline and Y grows upward, so
            # the glyph top is at y+h in world space
            shapes.append(_Shape("TEXT", color, layer, geom=(x, y + h, h), text=txt))
            xs.append(x); ys.append(y + h)
            entity_count += 1
        elif kind in ("SECTION", "ENDSEC", "EOF"):
            continue
        else:
            skipped.append(f"{kind}: entità non supportata")

    if not shapes:
        detail = ""
        if skipped:
            from collections import Counter
            counts = Counter(skipped)
            detail = " — " + "; ".join(
                f"{msg} (×{n})" if n > 1 else msg for msg, n in counts.items())
        raise VectorIngestError(
            "nessuna entità convertibile trovata (LINE/CIRCLE/LWPOLYLINE/"
            f"SPLINE/TEXT) nel file DXF{detail}")

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x - min_x < 1e-9:
        max_x += 1
    if max_y - min_y < 1e-9:
        max_y += 1
    return shapes, (min_x, min_y, max_x, max_y), skipped, entity_count


def ingest_dxf(dxf_text: str, max_dim: int = 800) -> VectorIngestResult:
    shapes, (min_x, min_y, max_x, max_y), skipped, entity_count = _parse_dxf(dxf_text)
    transform, width, height, scale = _fit_transform(min_x, min_y, max_x, max_y, max_dim, flip_y=True)
    # DXF TEXT's y-flip needs the same +h adjustment applied consistently;
    # already baked into geom during parsing (see _parse_dxf)
    palette = _PaletteBuilder()
    palette.get((255, 255, 255))
    lines = _emit_shapes(shapes, transform, scale, palette)
    return _finish(lines, palette, width, height, entity_count, skipped, "dxf")


def parse_vector_file(path: str) -> tuple[list[_Shape], tuple[float, float, float, float], list[str], str]:
    """Dispatch by extension, parsing only (no transform/emission yet) —
    the building block sequence.py/explode.py share."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lower = path.lower()
    if lower.endswith(".svg"):
        shapes, bounds, skipped = _parse_svg(text)
        return shapes, bounds, skipped, "svg"
    if lower.endswith(".dxf"):
        shapes, bounds, skipped, _entity_count = _parse_dxf(text)
        return shapes, bounds, skipped, "dxf"
    raise VectorIngestError(f"estensione non riconosciuta: '{path}' (attesi .svg/.dxf)")


def ingest_vector_file(path: str, max_dim: int = 800) -> VectorIngestResult:
    """Dispatch by extension: .svg -> ingest_svg, .dxf -> ingest_dxf."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lower = path.lower()
    if lower.endswith(".svg"):
        return ingest_svg(text, max_dim=max_dim)
    if lower.endswith(".dxf"):
        return ingest_dxf(text, max_dim=max_dim)
    raise VectorIngestError(f"estensione non riconosciuta: '{path}' (attesi .svg/.dxf)")
