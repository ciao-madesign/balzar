"""Frame-sequence (video) encoder: N frames -> one delta-based program.

The naive "flipbook" approach — encoding every frame independently —
throws away exactly the redundancy that makes video compressible: frame
k is usually almost identical to frame k-1. This encoder implements the
differential model of README §4.3 instead:

    frame 0   encoded in full (greedy rectangle cover, like encode_image)
    frame k   only the pixels that differ from frame k-1, covered with
              rectangles, followed by FRAME

Every rectangle painted in a delta has a single color equal to the NEW
frame's value for every pixel it covers, so repainting pixels that did
not change is harmless by construction — the render is exact, and the
self-check at the end verifies all frames pixel-per-pixel anyway.

Same honesty contract as the image encoder: shared palette across all
frames, lossless iff the union of colors fits in 256 entries, no gain
claimed on unstructured input.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dsl import canonical
from .encoder import _emit_rect, _greedy_rects, _median_cut_quantize
from .interpreter import render as render_program
from .payload import encode_payload


@dataclass
class VideoEncodeResult:
    program_text: str
    payload: bytes
    width: int
    height: int
    frame_count: int
    palette_size: int
    lossless: bool
    mean_color_error: float  # 0.0 if exact; else mean per-pixel RGB distance introduced
    instruction_count: int
    delta_pixels_total: int  # how many pixels actually changed across frames


def _quantize_frames(width: int, height: int,
                     rgb_frames: list[bytes]) -> tuple[list[list[int]], dict, bool, float]:
    """Shared palette across ALL frames, first-appearance order."""
    unique: dict[tuple[int, int, int], int] = {}
    lossless = True
    for rgb in rgb_frames:
        for i in range(width * height):
            c = (rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2])
            if c not in unique:
                if len(unique) >= 256:
                    lossless = False
                    break
                unique[c] = len(unique)
        if not lossless:
            break

    if lossless:
        palette = {idx: c for c, idx in unique.items()}
        frames_idx = []
        for rgb in rgb_frames:
            frames_idx.append([
                unique[(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2])]
                for i in range(width * height)
            ])
        return frames_idx, palette, True, 0.0

    # lossy fallback: same median-cut quantizer as the single-image
    # encoder (encoder.py), applied once across the pooled colors of
    # every frame so all frames share one palette — replaces the old
    # fixed 3-3-2 posterization for the same reason (adapts to the
    # actual color distribution instead of a fixed grid).
    n = width * height
    unique_counts: dict[tuple[int, int, int], int] = {}
    for rgb in rgb_frames:
        for i in range(n):
            c = (rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2])
            unique_counts[c] = unique_counts.get(c, 0) + 1

    color_to_index, palette = _median_cut_quantize(unique_counts)
    frames_idx = [
        [color_to_index[(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2])] for i in range(n)]
        for rgb in rgb_frames
    ]

    total_error = 0.0
    for color, cnt in unique_counts.items():
        r, g, b = palette[color_to_index[color]]
        total_error += cnt * (abs(color[0] - r) + abs(color[1] - g) + abs(color[2] - b)) / 3
    mean_error = round(total_error / (n * len(rgb_frames)), 2)
    return frames_idx, palette, False, mean_error


def _delta_rects(width: int, height: int, prev: list[int],
                 cur: list[int]) -> tuple[list[tuple[int, int, int, int, int]], int]:
    """Rectangle cover of the changed pixels between two frames.

    A rect may extend over unchanged pixels as long as their current color
    matches the rect color (repainting them is a no-op), which lets one
    instruction absorb a changed pixel embedded in a same-color area.
    Returns (rects, changed_pixel_count).
    """
    changed = bytearray(width * height)
    n_changed = 0
    for i in range(width * height):
        if prev[i] != cur[i]:
            changed[i] = 1
            n_changed += 1
    if n_changed == 0:
        return [], 0

    claimed = bytearray(width * height)
    rects = []
    for y in range(height):
        row = y * width
        x = 0
        while x < width:
            p = row + x
            if not changed[p] or claimed[p]:
                x += 1
                continue
            color = cur[p]
            # width: any same-color pixel may be included (repaint-safe)
            rw = 1
            while x + rw < width and cur[row + x + rw] == color:
                rw += 1
            # trim trailing pixels that are neither changed nor claimed-free
            # value: keep them only while they help absorb further changed
            # pixels; a simple trim back to the last changed pixel avoids
            # gratuitously wide rects on constant background rows
            while rw > 1 and not changed[row + x + rw - 1]:
                rw -= 1
            # height: whole row segment must be the rect color
            rh = 1
            while y + rh < height:
                base = (y + rh) * width + x
                ok = True
                any_changed = False
                for i in range(rw):
                    if cur[base + i] != color:
                        ok = False
                        break
                    if changed[base + i]:
                        any_changed = True
                if not ok or not any_changed:
                    break
                rh += 1
            for j in range(rh):
                base = (y + j) * width + x
                for i in range(rw):
                    claimed[base + i] = 1
            rects.append((x, y, rw, rh, color))
            x += rw
    return rects, n_changed


def encode_video(width: int, height: int,
                 rgb_frames: list[bytes]) -> VideoEncodeResult:
    if not rgb_frames:
        raise ValueError("no frames to encode")
    frames_idx, palette, lossless, mean_color_error = _quantize_frames(width, height, rgb_frames)

    lines = [f"CANVAS w={width} h={height} bg=0"]
    for i, (r, g, b) in sorted(palette.items()):
        lines.append(f"PALETTE i={i} rgb=#{r:02X}{g:02X}{b:02X}")

    n_instr = 0
    delta_total = 0

    # frame 0: full cover
    for (x, y, rw, rh, color) in _greedy_rects(width, height, frames_idx[0]):
        lines.append(_emit_rect(x, y, rw, rh, color))
        n_instr += 1
    lines.append("FRAME")
    n_instr += 1

    # frames 1..n-1: deltas only
    for k in range(1, len(frames_idx)):
        rects, n_changed = _delta_rects(width, height,
                                        frames_idx[k - 1], frames_idx[k])
        delta_total += n_changed
        for (x, y, rw, rh, color) in rects:
            lines.append(_emit_rect(x, y, rw, rh, color))
            n_instr += 1
        lines.append("FRAME")
        n_instr += 1

    program_text = "\n".join(lines) + "\n"

    # self-check: every rendered frame must match its quantized source
    result = render_program(program_text)
    if len(result.frames) != len(frames_idx):
        raise RuntimeError("video encoder self-check failed: frame count mismatch")
    for k, frame in enumerate(result.frames):
        if list(frame) != frames_idx[k]:
            raise RuntimeError(f"video encoder self-check failed at frame {k}")

    payload = encode_payload(program_text)
    return VideoEncodeResult(
        program_text=canonical(program_text),
        payload=payload,
        width=width,
        height=height,
        frame_count=len(frames_idx),
        palette_size=len(palette),
        lossless=lossless,
        mean_color_error=mean_color_error,
        instruction_count=n_instr,
        delta_pixels_total=delta_total,
    )
