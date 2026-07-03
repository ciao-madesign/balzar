"""True vector SVG export — a second rendering target for the same DSL.

PNG (png.py) rasterizes every program, always: any instruction can be
represented as pixels. SVG cannot make that promise honestly. Only a
subset of ops has a direct, exact vector meaning:

    CANVAS, PALETTE, SEED (no-op here), REGION, LOOP, FRAME (<=1),
    RECT, LINE, CIRCLE, TEXT, FILL, COPY, TILE

Ops with no clean vector meaning (SHIFT, ROTATE, MIRROR, SCALE, SWAP,
MAP, INVERT, NOISE, SCATTER, FRACTAL, SETPIX, or more than one FRAME —
i.e. a video) raise UnsupportedForSVG naming the exact instruction,
instead of silently rasterizing a patch and pretending the file is still
"real" vector. Use PNG for those; it always works.

TILE maps to SVG's native <pattern> (a genuine, scalable tiling fill,
not a copy-pasted raster); COPY duplicates the elements found in its
source region into a translated <g> at the destination — both reuse
whatever vector elements were already emitted, so a copied circle stays
a real circle, not a raster patch.

TEXT is emitted as real, editable <text> (generic monospace), not a
pixel-perfect reproduction of the 5x7 bitmap font (balzar/font5x7.py) —
that is a deliberate tradeoff: editable vector text in Illustrator/
Inkscape beats an exact glyph match nobody can select or restyle.
"""

from __future__ import annotations

from .dsl import Loop, eval_int, parse
from .grid import DEFAULT_PALETTE


class UnsupportedForSVG(ValueError):
    """Raised when a program uses an op with no direct vector meaning."""


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _parse_rgb(value: str) -> tuple[int, int, int]:
    n = int(value[1:], 16)
    return (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF


class _SvgContext:
    def __init__(self) -> None:
        self.width: int | None = None
        self.height: int | None = None
        self.bg = 0
        self.palette: dict[int, tuple[int, int, int]] = dict(DEFAULT_PALETTE)
        self.regions: dict[str, tuple[int, int, int, int]] = {}
        self.elements: list[tuple[int, int, int, int, str]] = []
        self.patterns: list[str] = []
        self.frame_count = 0

    def color_hex(self, idx: int) -> str:
        r, g, b = self.palette.get(idx, (0, 0, 0))
        return f"#{r:02x}{g:02x}{b:02x}"

    def region(self, name: str) -> tuple[int, int, int, int]:
        if name == "FULL":
            return (0, 0, self.width, self.height)
        if name not in self.regions:
            raise UnsupportedForSVG(f"regione '{name}' non definita")
        return self.regions[name]

    def exec_block(self, block: list, env: dict) -> None:
        for node in block:
            if isinstance(node, Loop):
                count = eval_int(node.count_expr, env)
                for k in range(count):
                    inner = dict(env)
                    inner[node.var] = k
                    self.exec_block(node.body, inner)
            else:
                self._exec_instr(node, env)

    def _geti(self, args: dict, env: dict, key: str, default=None) -> int:
        if key in args:
            return eval_int(args[key], env)
        if default is None:
            raise UnsupportedForSVG(f"argomento '{key}' mancante")
        return default

    def _exec_instr(self, instr, env: dict) -> None:
        name, args = instr.name, instr.args
        g = lambda key, default=None: self._geti(args, env, key, default)  # noqa: E731

        if name == "CANVAS":
            self.width, self.height, self.bg = g("w"), g("h"), g("bg", 0)
        elif name == "PALETTE":
            self.palette[g("i")] = _parse_rgb(args["rgb"])
        elif name == "SEED":
            pass  # no vector-safe op reads the RNG, so this is a true no-op here
        elif name == "REGION":
            self.regions[args["name"]] = (g("x"), g("y"), g("w"), g("h"))
        elif name == "FRAME":
            self.frame_count += 1
            if self.frame_count > 1:
                raise UnsupportedForSVG(
                    "il programma ha piu' di un frame (video/animazione): "
                    "l'export SVG supporta solo immagini singole, usa PNG/GIF")
        elif name == "RECT":
            x, y, w, h = g("x"), g("y"), g("w"), g("h")
            col = self.color_hex(g("color"))
            if g("fill", 1):
                svg = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{col}"/>'
            else:
                svg = (f'<rect x="{x + 0.5}" y="{y + 0.5}" width="{w - 1}" '
                       f'height="{h - 1}" fill="none" stroke="{col}"/>')
            self.elements.append((x, y, w, h, svg))
        elif name == "LINE":
            x1, y1, x2, y2 = g("x1"), g("y1"), g("x2"), g("y2")
            col = self.color_hex(g("color"))
            svg = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}"/>'
            self.elements.append((min(x1, x2), min(y1, y2),
                                  abs(x2 - x1) + 1, abs(y2 - y1) + 1, svg))
        elif name == "CIRCLE":
            cx, cy, r = g("cx"), g("cy"), g("r")
            col = self.color_hex(g("color"))
            if g("fill", 0):
                svg = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{col}"/>'
            else:
                svg = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}"/>'
            self.elements.append((cx - r, cy - r, 2 * r, 2 * r, svg))
        elif name == "TEXT":
            x, y, scale = g("x"), g("y"), g("scale", 1)
            col = self.color_hex(g("color"))
            from .font5x7 import GLYPH_HEIGHT
            size = GLYPH_HEIGHT * scale
            text = args["text"]
            svg = (f'<text x="{x}" y="{y + size}" font-family="monospace" '
                   f'font-size="{size}" fill="{col}" xml:space="preserve">'
                   f'{_xml_escape(text)}</text>')
            self.elements.append((x, y, (5 + 1) * scale * len(text), size, svg))
        elif name == "FILL":
            x, y, w, h = self.region(args["region"])
            col = self.color_hex(g("color"))
            self.elements.append(
                (x, y, w, h, f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{col}"/>'))
        elif name == "COPY":
            sx, sy, sw, sh = self.region(args["src"])
            dx, dy, dw, dh = self.region(args["dst"])
            inner = self._elements_within(sx, sy, sw, sh)
            if not inner:
                raise UnsupportedForSVG("COPY: nessun elemento vettoriale nella regione sorgente")
            group = (f'<g transform="translate({dx - sx},{dy - sy})">'
                     + "".join(svg for *_, svg in inner) + "</g>")
            self.elements.append((dx, dy, dw, dh, group))
        elif name == "TILE":
            sx, sy, sw, sh = self.region(args["src"])
            dx, dy, dw, dh = self.region(args["dst"])
            inner = self._elements_within(sx, sy, sw, sh)
            if not inner:
                raise UnsupportedForSVG("TILE: nessun elemento vettoriale nella regione sorgente")
            pid = f"tile{len(self.patterns)}"
            body = "".join(
                f'<g transform="translate({-sx},{-sy})">{svg}</g>' for *_, svg in inner)
            self.patterns.append(
                f'<pattern id="{pid}" x="{dx}" y="{dy}" width="{sw}" height="{sh}" '
                f'patternUnits="userSpaceOnUse">{body}</pattern>')
            self.elements.append(
                (dx, dy, dw, dh,
                 f'<rect x="{dx}" y="{dy}" width="{dw}" height="{dh}" fill="url(#{pid})"/>'))
        else:
            raise UnsupportedForSVG(
                f"'{name}' non ha un equivalente vettoriale diretto (richiede "
                f"rasterizzazione) — l'export SVG non e' disponibile per questo "
                f"programma, usa PNG")

    def _elements_within(self, x, y, w, h):
        return [e for e in self.elements
                if e[0] >= x and e[1] >= y and e[0] + e[2] <= x + w and e[1] + e[3] <= y + h]

    def finish(self) -> str:
        if self.width is None:
            raise UnsupportedForSVG("nessun CANVAS dichiarato")
        bg = self.color_hex(self.bg)
        defs = "".join(self.patterns)
        body = "".join(svg for *_, svg in self.elements)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" '
            f'height="{self.height}" viewBox="0 0 {self.width} {self.height}">\n'
            f'<rect x="0" y="0" width="{self.width}" height="{self.height}" fill="{bg}"/>\n'
            f'<defs>{defs}</defs>\n{body}\n</svg>\n'
        )


def render_svg(program_text: str) -> str:
    """Program text -> SVG document, or raises UnsupportedForSVG."""
    ctx = _SvgContext()
    ctx.exec_block(parse(program_text), {})
    return ctx.finish()
