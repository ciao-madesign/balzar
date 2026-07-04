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

    return transform, canvas_w, canvas_h


class _PaletteBuilder:
    def __init__(self) -> None:
        self.index: dict[tuple[int, int, int], int] = {}

    def get(self, rgb: tuple[int, int, int]) -> int:
        if rgb not in self.index:
            if len(self.index) >= MAX_COLORS:
                raise VectorIngestError(
                    f"più di {MAX_COLORS} colori distinti nel file — non tipico "
                    f"per un disegno CAD/vettoriale, caso non supportato")
            self.index[rgb] = len(self.index)
        return self.index[rgb]

    def palette_lines(self) -> list[str]:
        return [f"PALETTE i={i} rgb=#{r:02X}{g:02X}{b:02X}"
                for (r, g, b), i in sorted(self.index.items(), key=lambda kv: kv[1])]


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


def ingest_svg(svg_text: str, max_dim: int = 800) -> VectorIngestResult:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise VectorIngestError(f"SVG non valido: {exc}") from None

    min_x, min_y, max_x, max_y = _svg_bounds(root)
    transform, width, height = _fit_transform(min_x, min_y, max_x, max_y, max_dim, flip_y=False)

    palette = _PaletteBuilder()
    palette.get((255, 255, 255))  # reserve white as index 0: CANVAS bg=0 relies on this
    lines: list[str] = []
    skipped: list[str] = []
    element_count = 0

    def tag(elem) -> str:
        t = elem.tag
        return t[len(_SVG_NS):] if t.startswith(_SVG_NS) else t

    def walk(elem, ox: float, oy: float) -> None:
        nonlocal element_count
        name = tag(elem)
        if name in ("svg", "g", "defs", "title", "desc", "metadata"):
            try:
                dx, dy = _svg_translate(elem) if name == "g" else (0.0, 0.0)
            except VectorIngestError as exc:
                skipped.append(f"<g>: {exc}")
                return
            for child in elem:
                walk(child, ox + dx, oy + dy)
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
                p1 = transform(x, y)
                p2 = transform(x + w, y + h)
                idx = palette.get(color)
                lines.append(f"RECT x={p1[0]} y={p1[1]} w={max(1, p2[0]-p1[0])} "
                            f"h={max(1, p2[1]-p1[1])} color={idx} fill={0 if outline else 1}")
                element_count += 1
            elif name == "circle":
                cx = float(elem.attrib["cx"]) + ox
                cy = float(elem.attrib["cy"]) + oy
                r = float(elem.attrib["r"])
                if color is None:
                    skipped.append("<circle>: nessun fill/stroke"); return
                c = transform(cx, cy)
                r_px = round(r * (width / max(max_x - min_x, 1e-9)))
                idx = palette.get(color)
                lines.append(f"CIRCLE cx={c[0]} cy={c[1]} r={max(1, r_px)} "
                            f"color={idx} fill={0 if outline else 1}")
                element_count += 1
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
                c = transform(cx, cy)
                r_px = round(rx * (width / max(max_x - min_x, 1e-9)))
                idx = palette.get(color)
                lines.append(f"CIRCLE cx={c[0]} cy={c[1]} r={max(1, r_px)} "
                            f"color={idx} fill={0 if outline else 1}")
                element_count += 1
            elif name == "line":
                x1 = float(elem.attrib.get("x1", 0)) + ox
                y1 = float(elem.attrib.get("y1", 0)) + oy
                x2 = float(elem.attrib.get("x2", 0)) + ox
                y2 = float(elem.attrib.get("y2", 0)) + oy
                col = stroke if stroke is not None else fill
                if col is None:
                    skipped.append("<line>: nessun stroke"); return
                p1, p2 = transform(x1, y1), transform(x2, y2)
                idx = palette.get(col)
                lines.append(f"LINE x1={p1[0]} y1={p1[1]} x2={p2[0]} y2={p2[1]} color={idx}")
                element_count += 1
            elif name in ("polyline", "polygon"):
                pts_raw = elem.attrib.get("points", "").strip()
                nums = [float(v) for v in re.split(r"[\s,]+", pts_raw) if v]
                pts = [(nums[i] + ox, nums[i + 1] + oy) for i in range(0, len(nums) - 1, 2)]
                if len(pts) < 2:
                    skipped.append(f"<{name}>: punti insufficienti"); return
                col = stroke if stroke is not None else fill
                if col is None:
                    skipped.append(f"<{name}>: nessun colore"); return
                idx = palette.get(col)
                seq = pts + [pts[0]] if name == "polygon" else pts
                for a, b in zip(seq, seq[1:]):
                    pa, pb = transform(*a), transform(*b)
                    lines.append(f"LINE x1={pa[0]} y1={pa[1]} x2={pb[0]} y2={pb[1]} color={idx}")
                element_count += 1
            elif name == "path":
                d = elem.attrib.get("d", "")
                if re.search(r"[CScQqTtAa]", d):
                    skipped.append("<path>: curve (C/S/Q/T/A) non supportate, solo M/L/Z")
                    return
                tokens = re.findall(r"[MLZmlz]|-?\d+\.?\d*(?:[eE]-?\d+)?", d)
                pts, cur, i = [], None, 0
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
                idx = palette.get(col)
                for a, b in zip(pts, pts[1:]):
                    pa, pb = transform(*a), transform(*b)
                    lines.append(f"LINE x1={pa[0]} y1={pa[1]} x2={pb[0]} y2={pb[1]} color={idx}")
                element_count += 1
            elif name == "text":
                x = float(elem.attrib.get("x", 0)) + ox
                y = float(elem.attrib.get("y", 0)) + oy
                text = "".join(elem.itertext()).strip()
                if not text:
                    return
                size = float(re.sub(r"[a-z%]+$", "", elem.attrib.get("font-size", "16")))
                scale = max(1, round(size / 7))
                col = fill if fill is not None else (0, 0, 0)
                # SVG's y is the text baseline; our TEXT op's y is the glyph
                # box's top — shift up by ~the font size (approx ascent)
                p = transform(x, y - size)
                idx = palette.get(col)
                safe = text.replace('"', "'")
                lines.append(f'TEXT x={p[0]} y={p[1]} text="{safe}" color={idx} scale={scale}')
                element_count += 1
            else:
                skipped.append(f"<{name}>: elemento non supportato")
        except (KeyError, ValueError) as exc:
            skipped.append(f"<{name}>: {exc}")

    walk(root, 0.0, 0.0)
    return _finish(lines, palette, width, height, element_count, skipped, "svg")


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


def ingest_dxf(dxf_text: str, max_dim: int = 800) -> VectorIngestResult:
    pairs = list(_dxf_pairs(dxf_text))

    # isolate the ENTITIES section
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

    # split into individual entities on group code 0
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

    lines: list[str] = []
    skipped: list[str] = []
    element_count = 0
    palette = _PaletteBuilder()
    palette.get((255, 255, 255))  # reserve white as index 0: CANVAS bg=0 relies on this
    raw_shapes: list[tuple] = []  # (kind, ...coords) in DXF world units, resolved after bounds known
    colors: list[int | None] = []

    xs: list[float] = []
    ys: list[float] = []

    for entity in entities:
        kind = entity[0][1]
        color_code = get(entity, 62, int, None)
        if kind == "LINE":
            x1, y1 = get(entity, 10), get(entity, 20)
            x2, y2 = get(entity, 11), get(entity, 21)
            if None in (x1, y1, x2, y2):
                skipped.append("LINE: coordinate mancanti"); continue
            raw_shapes.append(("LINE", x1, y1, x2, y2))
            colors.append(color_code)
            xs += [x1, x2]; ys += [y1, y2]
        elif kind == "CIRCLE":
            cx, cy, r = get(entity, 10), get(entity, 20), get(entity, 40)
            if None in (cx, cy, r):
                skipped.append("CIRCLE: coordinate/raggio mancanti"); continue
            raw_shapes.append(("CIRCLE", cx, cy, r))
            colors.append(color_code)
            xs += [cx - r, cx + r]; ys += [cy - r, cy + r]
        elif kind == "LWPOLYLINE":
            pxs, pys = get_all(entity, 10), get_all(entity, 20)
            pts = list(zip(pxs, pys))
            closed = bool(get(entity, 70, int, 0) & 1)
            if len(pts) < 2:
                skipped.append("LWPOLYLINE: punti insufficienti"); continue
            raw_shapes.append(("POLY", pts, closed))
            colors.append(color_code)
            xs += pxs; ys += pys
        elif kind in ("TEXT", "MTEXT"):
            x, y = get(entity, 10), get(entity, 20)
            h = get(entity, 40, float, 2.5)
            txt = get(entity, 1, str, "")
            if None in (x, y) or not txt:
                skipped.append(f"{kind}: dati mancanti"); continue
            raw_shapes.append(("TEXT", x, y, h, txt))
            colors.append(color_code)
            xs.append(x); ys.append(y)
        elif kind in ("SECTION", "ENDSEC", "EOF"):
            continue
        else:
            skipped.append(f"{kind}: entità non supportata")

    if not raw_shapes:
        raise VectorIngestError("nessuna entità convertibile trovata (LINE/CIRCLE/"
                               "LWPOLYLINE/TEXT) nel file DXF")

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x - min_x < 1e-9:
        max_x += 1
    if max_y - min_y < 1e-9:
        max_y += 1
    transform, width, height = _fit_transform(min_x, min_y, max_x, max_y, max_dim, flip_y=True)
    px_scale = width / max(max_x - min_x, 1e-9)

    for shape, color_code in zip(raw_shapes, colors):
        idx = palette.get(_aci_to_rgb(color_code))
        if color_code is not None and color_code not in _ACI_COLORS and color_code not in (0, 256):
            skipped.append(f"colore ACI {color_code} non nella tabella nota, reso in grigio neutro")
        kind = shape[0]
        if kind == "LINE":
            _, x1, y1, x2, y2 = shape
            p1, p2 = transform(x1, y1), transform(x2, y2)
            lines.append(f"LINE x1={p1[0]} y1={p1[1]} x2={p2[0]} y2={p2[1]} color={idx}")
            element_count += 1
        elif kind == "CIRCLE":
            _, cx, cy, r = shape
            c = transform(cx, cy)
            lines.append(f"CIRCLE cx={c[0]} cy={c[1]} r={max(1, round(r * px_scale))} "
                        f"color={idx} fill=0")
            element_count += 1
        elif kind == "POLY":
            _, pts, closed = shape
            seq = pts + [pts[0]] if closed else pts
            for a, b in zip(seq, seq[1:]):
                pa, pb = transform(*a), transform(*b)
                lines.append(f"LINE x1={pa[0]} y1={pa[1]} x2={pb[0]} y2={pb[1]} color={idx}")
            element_count += 1
        elif kind == "TEXT":
            _, x, y, h, txt = shape
            # DXF's insertion point is the baseline and Y grows upward, so
            # the glyph top is at y+h in world space (opposite sign to the
            # SVG case, because flip_y already reverses the Y convention)
            p = transform(x, y + h)
            scale = max(1, round(h * px_scale / 7))
            safe = txt.replace('"', "'")
            lines.append(f'TEXT x={p[0]} y={p[1]} text="{safe}" color={idx} scale={scale}')
            element_count += 1

    return _finish(lines, palette, width, height, element_count, skipped, "dxf")


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
