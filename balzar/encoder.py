"""Best-effort automatic encoder: RGB pixels -> balzar DSL program.

This is the missing half of the pipeline described in README §5.1. It does
NOT reinvent an entropy coder: it looks for the same structure a human
author uses in the example programs (flat regions, periodic tiling) and
falls back, honestly, to one RECT per pixel where no structure is found.

The result is lossless whenever the image already has <=256 distinct
colors (icons, screenshots, line art, CAD exports, our own renders).
Above that — real UI screenshots included: anti-aliased text/icons and
soft shadows routinely produce thousands of near-identical shades — it
looks for the finest per-channel rounding step that brings the count
back under 256 before falling back to a fixed, coarse 3-3-2 palette.
A UI screenshot with 455 distinct colors from anti-aliasing needs only
+-2 per channel (step 4) to fit in 256, instead of the +-16/+-32 the
fixed fallback would force — visibly less banding for a real cost of a
few less-precise shades nobody notices. Either way, the program is
verified by rendering it back and diffing against the quantized source
before being handed to the caller: what you download always reproduces
exactly what balzar actually generates.

This is precisely where the theoretical limit from README §8 becomes
visible instead of theoretical: structured input compresses hugely,
photographic/noisy input does not, and the tool says so instead of
pretending otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dsl import canonical
from .interpreter import render as render_program
from .payload import encode_payload

# below this many covered pixels a RECT isn't worth its own instruction
# text overhead disappears anyway once deflate sees the repetition, so this
# only controls how many instructions we emit, not correctness
_MIN_RECT_AREA = 1

# a wrong tile candidate fails at the first mismatching row segment, so
# testing every divisor is cheap in practice; the cap only bounds the
# pathological almost-periodic case
_MAX_DIVISOR_CANDIDATES = 24


# per-channel rounding steps tried, finest first, until the rounded color
# count fits in 256. Step 64 leaves only 4 possible values per channel
# (0/64/128/192), so at most 4^3=64 distinct colors — by that pigeonhole
# argument this loop always terminates; there is no further "emergency"
# fallback because none is ever reachable, and pretending one exists
# would be exactly the kind of unreachable, misleading code this project
# avoids.
_ROUNDING_STEPS = (2, 4, 8, 12, 16, 24, 32, 48, 64)


@dataclass
class EncodeResult:
    program_text: str
    payload: bytes
    width: int
    height: int
    palette_size: int
    lossless: bool
    color_step: int  # 0 = exact; else the per-channel rounding step used
    instruction_count: int
    tile: tuple[int, int] | None

    def fidelity_label(self) -> str:
        """One consistent, honest description for CLI/GUI/web — never just
        a bare True/False that hides how lossy 'lossy' actually is."""
        if self.lossless:
            return "esatta (lossless)"
        if self.color_step <= 16:
            return f"quantizzata fine (arrotondamento colore, passo {self.color_step})"
        if self.color_step <= 32:
            return f"quantizzata media (arrotondamento colore, passo {self.color_step})"
        return f"quantizzata grezza (arrotondamento colore, passo {self.color_step})"


def _count_unique_rounded(colors: list[bytes], step: int) -> int:
    seen = set()
    for c in colors:
        seen.add((c[0] // step * step, c[1] // step * step, c[2] // step * step))
        if len(seen) > 256:
            return len(seen)
    return len(seen)


def _quantize(width: int, height: int, rgb: bytes) -> tuple[list[int], dict[int, tuple[int, int, int]], bool, int]:
    """Map RGB pixels to palette indices.

    Returns (indices, palette, lossless, color_step). See module docstring
    for why a graduated rounding search beats jumping straight to a fixed
    coarse palette whenever the exact color count is just over 256.
    """
    n = width * height
    colors = [rgb[i * 3:i * 3 + 3] for i in range(n)]
    unique = {}
    for c in colors:
        key = (c[0], c[1], c[2])
        if key not in unique:
            unique[key] = len(unique)
        if len(unique) > 256:
            break

    if len(unique) <= 256:
        # order by first appearance: fully deterministic, no frequency sort needed
        palette = {idx: color for color, idx in unique.items()}
        indices = [unique[(c[0], c[1], c[2])] for c in colors]
        return indices, palette, True, 0

    for step in _ROUNDING_STEPS:
        if _count_unique_rounded(colors, step) <= 256:
            unique = {}
            indices = [0] * n
            for i, c in enumerate(colors):
                key = (c[0] // step * step, c[1] // step * step, c[2] // step * step)
                idx = unique.setdefault(key, len(unique))
                indices[i] = idx
            palette = {idx: color for color, idx in unique.items()}
            return indices, palette, False, step

    raise AssertionError("unreachable: step=64 always yields <=64 colors")


def _divisors(n: int) -> list[int]:
    out = [d for d in range(2, n) if n % d == 0]
    out.sort()
    return out[:_MAX_DIVISOR_CANDIDATES]


def _find_tile(width: int, height: int, idx: list[int]) -> tuple[int, int] | None:
    """Smallest-area (tw, th) such that idx tiles exactly across the canvas."""
    candidates = []
    for tw in [width] + _divisors(width):
        for th in [height] + _divisors(height):
            if tw == width and th == height:
                continue
            candidates.append((tw * th, tw, th))
    candidates.sort()

    for _, tw, th in candidates:
        ok = True
        for y in range(height):
            sy = y % th
            row_base = y * width
            srow_base = sy * width
            for x in range(0, width, tw):
                # compare this tw-wide block against the reference tile row
                seg_len = min(tw, width - x)
                if idx[row_base + x:row_base + x + seg_len] != \
                   idx[srow_base:srow_base + seg_len]:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return tw, th
    return None


def _greedy_rects(width: int, height: int, idx: list[int],
                   x0: int = 0, y0: int = 0, w: int = None, h: int = None) -> list[tuple[int, int, int, int, int]]:
    """Greedy maximal same-color rectangle covering of a sub-window.

    Deterministic row-major scan: grow each uncovered cell to the widest
    same-color run, then the tallest run of identical such rows. Not
    optimal (exact minimal rectangle cover is NP-hard) but fast and good
    enough to collapse the flat regions that dominate diagrams/icons/CAD.
    """
    w = width if w is None else w
    h = height if h is None else h
    claimed = bytearray(w * h)
    rects = []

    def at(px: int, py: int) -> int:
        return idx[(y0 + py) * width + (x0 + px)]

    for y in range(h):
        x = 0
        while x < w:
            if claimed[y * w + x]:
                x += 1
                continue
            color = at(x, y)
            rw = 1
            while x + rw < w and not claimed[y * w + x + rw] and at(x + rw, y) == color:
                rw += 1
            rh = 1
            while y + rh < h:
                row_ok = True
                for i in range(rw):
                    if claimed[(y + rh) * w + x + i] or at(x + i, y + rh) != color:
                        row_ok = False
                        break
                if not row_ok:
                    break
                rh += 1
            if rw * rh >= _MIN_RECT_AREA:
                for j in range(rh):
                    base = (y + j) * w + x
                    for i in range(rw):
                        claimed[base + i] = 1
                rects.append((x, y, rw, rh, color))
            x += rw
    return rects


def _emit_rect(x: int, y: int, rw: int, rh: int, color: int) -> str:
    # a lone pixel is shorter as SETPIX than as a degenerate RECT
    if rw == 1 and rh == 1:
        return f"SETPIX x={x} y={y} color={color}"
    return f"RECT x={x} y={y} w={rw} h={rh} color={color} fill=1"


def encode_image(width: int, height: int, rgb: bytes) -> EncodeResult:
    idx, palette, lossless, color_step = _quantize(width, height, rgb)
    tile = _find_tile(width, height, idx)

    lines = [f"CANVAS w={width} h={height} bg=0"]
    for i, (r, g, b) in sorted(palette.items()):
        lines.append(f"PALETTE i={i} rgb=#{r:02X}{g:02X}{b:02X}")

    n_instr = 0
    if tile is not None:
        tw, th = tile
        lines.append(f"REGION name=TILE x=0 y=0 w={tw} h={th}")
        for (x, y, rw, rh, color) in _greedy_rects(width, height, idx, 0, 0, tw, th):
            lines.append(_emit_rect(x, y, rw, rh, color))
            n_instr += 1
        lines.append("TILE src=TILE dst=FULL")
        n_instr += 2
    else:
        for (x, y, rw, rh, color) in _greedy_rects(width, height, idx):
            lines.append(_emit_rect(x, y, rw, rh, color))
            n_instr += 1

    program_text = "\n".join(lines) + "\n"

    # self-check: the payload we hand out must reproduce exactly what we
    # just analyzed, or the "lossless" claim above would be a lie
    result = render_program(program_text)
    rebuilt = list(result.frames[0])
    if rebuilt != idx:
        raise RuntimeError("encoder self-check failed: rendered output does not "
                           "match the quantized source (internal bug)")

    payload = encode_payload(program_text)
    return EncodeResult(
        program_text=canonical(program_text),
        payload=payload,
        width=width,
        height=height,
        palette_size=len(palette),
        lossless=lossless,
        color_step=color_step,
        instruction_count=n_instr,
        tile=tile,
    )
