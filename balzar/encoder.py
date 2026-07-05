"""Best-effort automatic encoder: RGB pixels -> balzar DSL program.

This is the missing half of the pipeline described in README §5.1. It does
NOT reinvent an entropy coder: it looks for the same structure a human
author uses in the example programs (flat regions, periodic tiling) and
falls back, honestly, to one RECT per pixel where no structure is found.

The result is lossless whenever the image already has <=256 distinct
colors (icons, screenshots, line art, CAD exports, our own renders).
Above that — real UI screenshots included: anti-aliased text/icons and
soft shadows routinely produce thousands of near-identical shades — it
falls back to a median-cut quantizer (_median_cut_quantize): split color
space into <=256 boxes by repeatedly cutting the box with the largest
population-weighted range along its widest channel, then represent each
box by the population-weighted average of the colors in it. Unlike a
fixed per-channel rounding grid (the previous approach), this spends the
256-color budget where the image actually has detail — a screenshot with
a lot of near-white background and a little saturated color keeps the
saturated detail instead of averaging it away with the background. The
actual quantization error introduced (mean per-pixel RGB distance,
0 if lossless) is measured and disclosed, never a bare lossless/lossy
boolean. Either way, the program is verified by rendering it back and
diffing against the quantized source before being handed to the caller:
what you download always reproduces exactly what balzar actually
generates.

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


@dataclass
class EncodeResult:
    program_text: str
    payload: bytes
    width: int
    height: int
    palette_size: int
    lossless: bool
    mean_color_error: float  # 0.0 if exact; else mean per-pixel RGB distance introduced
    instruction_count: int
    tile: tuple[int, int] | None

    def fidelity_label(self) -> str:
        """One consistent, honest description for CLI/GUI/web — never just
        a bare True/False that hides how lossy 'lossy' actually is."""
        if self.lossless:
            return "esatta (lossless)"
        if self.mean_color_error <= 2:
            return f"quantizzata fine (errore medio colore {self.mean_color_error})"
        if self.mean_color_error <= 8:
            return f"quantizzata media (errore medio colore {self.mean_color_error})"
        return f"quantizzata grezza (errore medio colore {self.mean_color_error})"


def _median_cut_boxes(items: list[tuple[tuple[int, int, int], int]],
                      max_boxes: int) -> list[list[tuple[tuple[int, int, int], int]]]:
    """Split (color, pixel_count) pairs into <=max_boxes groups: repeatedly
    cut the box with the largest population-weighted channel range, along
    its widest channel, at the population-weighted median. Deterministic —
    ties broken by first-appearance order, same contract as the rest of
    the encoder."""
    boxes = [items]

    def channel_ranges(box):
        rs = [c[0] for c, _ in box]
        gs = [c[1] for c, _ in box]
        bs = [c[2] for c, _ in box]
        return (max(rs) - min(rs), max(gs) - min(gs), max(bs) - min(bs))

    def weighted_volume(box):
        return max(channel_ranges(box)) * sum(cnt for _, cnt in box)

    def split(box):
        channel = channel_ranges(box).index(max(channel_ranges(box)))
        ordered = sorted(box, key=lambda item: item[0][channel])
        total = sum(cnt for _, cnt in ordered)
        half, acc, cut = total / 2, 0, len(ordered) // 2
        for i, (_, cnt) in enumerate(ordered):
            acc += cnt
            if acc >= half:
                cut = i + 1
                break
        cut = max(1, min(cut, len(ordered) - 1))
        return ordered[:cut], ordered[cut:]

    while len(boxes) < max_boxes:
        splittable = [i for i, b in enumerate(boxes) if len(b) > 1]
        if not splittable:
            break
        i = max(splittable, key=lambda i: weighted_volume(boxes[i]))
        b1, b2 = split(boxes[i])
        boxes[i:i + 1] = [b1, b2]

    return boxes


# Median-cut's repeated per-box sorting is fine for a few thousand distinct
# colors but not for the hundreds of thousands a high-entropy image can
# have (measured: 400x400 pure noise, ~140k uniques, took 26s uncapped).
# Above this many uniques, colors are pre-grouped by the coarsest uniform
# step that fits the budget (same doubling-step, pigeonhole-terminates
# idea as the old fixed-grid fallback) before median-cut ever runs. This
# only affects low-structure content (photos/noise) that gets no useful
# compression either way; the realistic target case — a few hundred to a
# couple thousand near-duplicate shades from anti-aliasing — never has
# enough uniques to hit this path, so its quality is unaffected.
_MEDIAN_CUT_MAX_INPUT = 4096


def _pre_bucket(unique_counts: dict[tuple[int, int, int], int], max_items: int = _MEDIAN_CUT_MAX_INPUT):
    """Coarsen `unique_counts` down to <=max_items groups if needed.
    Returns (bucketed_counts, color_to_bucket) — bucketed_counts feeds
    median-cut, color_to_bucket lets every original color find its final
    palette index afterwards."""
    if len(unique_counts) <= max_items:
        return dict(unique_counts), {c: c for c in unique_counts}

    step = 2
    while True:
        buckets: dict[tuple[int, int, int], int] = {}
        color_to_bucket: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        for color, cnt in unique_counts.items():
            key = (color[0] // step * step, color[1] // step * step, color[2] // step * step)
            buckets[key] = buckets.get(key, 0) + cnt
            color_to_bucket[color] = key
        if len(buckets) <= max_items:
            return buckets, color_to_bucket
        step *= 2


def _median_cut_quantize(unique_counts: dict[tuple[int, int, int], int], max_colors: int = 256):
    """(color -> pixel count) for every distinct source color -> (color ->
    palette index, palette). Each box's representative is the
    population-weighted average of the colors assigned to it, so the
    result adapts to the image's actual distribution instead of a fixed
    per-channel grid (see module docstring)."""
    bucketed_counts, color_to_bucket = _pre_bucket(unique_counts)
    boxes = _median_cut_boxes(list(bucketed_counts.items()), max_colors)

    bucket_to_index: dict[tuple[int, int, int], int] = {}
    palette: dict[int, tuple[int, int, int]] = {}
    for idx, box in enumerate(boxes):
        total = sum(cnt for _, cnt in box)
        r = round(sum(c[0] * cnt for c, cnt in box) / total)
        g = round(sum(c[1] * cnt for c, cnt in box) / total)
        b = round(sum(c[2] * cnt for c, cnt in box) / total)
        palette[idx] = (r, g, b)
        for bucket, _ in box:
            bucket_to_index[bucket] = idx
    color_to_index = {color: bucket_to_index[bucket] for color, bucket in color_to_bucket.items()}
    return color_to_index, palette


def _quantize(width: int, height: int,
             rgb: bytes) -> tuple[list[int], dict[int, tuple[int, int, int]], bool, float]:
    """Map RGB pixels to palette indices.

    Returns (indices, palette, lossless, mean_color_error). See module
    docstring for the median-cut fallback used above 256 distinct colors.
    """
    n = width * height
    colors = [rgb[i * 3:i * 3 + 3] for i in range(n)]
    unique_counts: dict[tuple[int, int, int], int] = {}
    for c in colors:
        key = (c[0], c[1], c[2])
        unique_counts[key] = unique_counts.get(key, 0) + 1

    if len(unique_counts) <= 256:
        # order by first appearance: fully deterministic, no frequency sort needed
        palette = {idx: color for idx, color in enumerate(unique_counts)}
        index_of = {color: idx for idx, color in palette.items()}
        indices = [index_of[(c[0], c[1], c[2])] for c in colors]
        return indices, palette, True, 0.0

    color_to_index, palette = _median_cut_quantize(unique_counts)
    indices = [color_to_index[(c[0], c[1], c[2])] for c in colors]

    total_error = 0.0
    for color, cnt in unique_counts.items():
        r, g, b = palette[color_to_index[color]]
        total_error += cnt * (abs(color[0] - r) + abs(color[1] - g) + abs(color[2] - b)) / 3
    return indices, palette, False, round(total_error / n, 2)


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
    idx, palette, lossless, mean_color_error = _quantize(width, height, rgb)
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
        mean_color_error=mean_color_error,
        instruction_count=n_instr,
        tile=tile,
    )
