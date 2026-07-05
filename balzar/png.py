"""Minimal pure-Python PNG writer (RGB8, no interlace, no dependencies).

Each scanline picks whichever of the five standard PNG filters (None, Sub,
Up, Average, Paeth) minimizes the sum of absolute signed byte values of the
filtered row — the same "minimum sum of absolute differences" heuristic
used by reference PNG encoders. Filtering exploits local pixel correlation
(a rectangle's interior repeats the same bytes row after row and column
after column) that plain DEFLATE alone does not find as well, since DEFLATE
matches byte sequences, not the arithmetic relationship between neighbors.
"""

from __future__ import annotations

import struct
import zlib

_BPP = 3  # bytes per pixel, RGB8


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _score(row: bytes) -> int:
    return sum(v if v < 128 else 256 - v for v in row)


def _filter_sub(row: bytes) -> tuple[bytearray, int]:
    out = bytearray(len(row))
    total = 0
    for i, cur in enumerate(row):
        a = row[i - _BPP] if i >= _BPP else 0
        v = (cur - a) & 0xFF
        out[i] = v
        total += v if v < 128 else 256 - v
    return out, total


def _filter_up(row: bytes, prev: bytes | None) -> tuple[bytearray, int]:
    out = bytearray(len(row))
    total = 0
    for i, cur in enumerate(row):
        b = prev[i] if prev is not None else 0
        v = (cur - b) & 0xFF
        out[i] = v
        total += v if v < 128 else 256 - v
    return out, total


def _filter_average(row: bytes, prev: bytes | None) -> tuple[bytearray, int]:
    out = bytearray(len(row))
    total = 0
    for i, cur in enumerate(row):
        a = row[i - _BPP] if i >= _BPP else 0
        b = prev[i] if prev is not None else 0
        v = (cur - ((a + b) >> 1)) & 0xFF
        out[i] = v
        total += v if v < 128 else 256 - v
    return out, total


def _filter_paeth(row: bytes, prev: bytes | None) -> tuple[bytearray, int]:
    out = bytearray(len(row))
    total = 0
    for i, cur in enumerate(row):
        a = row[i - _BPP] if i >= _BPP else 0
        b = prev[i] if prev is not None else 0
        c = prev[i - _BPP] if (prev is not None and i >= _BPP) else 0
        v = (cur - _paeth(a, b, c)) & 0xFF
        out[i] = v
        total += v if v < 128 else 256 - v
    return out, total


def _filter_scanline(row: bytes, prev: bytes | None) -> bytes:
    """Pick the filter type minimizing the sum of absolute signed bytes."""
    best_type = 0
    best_bytes = row
    best_score = _score(row)

    for ftype, (candidate, score) in (
        (1, _filter_sub(row)),
        (2, _filter_up(row, prev)),
        (3, _filter_average(row, prev)),
        (4, _filter_paeth(row, prev)),
    ):
        if score < best_score:
            best_type, best_bytes, best_score = ftype, candidate, score

    return bytes([best_type]) + bytes(best_bytes)


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Encode raw row-major RGB8 bytes (len == w*h*3) as a PNG file.

    The per-row MSAD heuristic picks the locally cheapest filter per
    scanline, but "locally cheapest" is not always "globally smallest
    after DEFLATE": on content with exact row-to-row byte repetition
    (e.g. a tiled rectangle pattern), filtering breaks the very byte
    identity DEFLATE was matching across rows, and unfiltered (type 0)
    compresses smaller. Rather than guess which case applies, both are
    actually compressed and the smaller one wins — never worse than the
    old always-unfiltered writer, and strictly better whenever adaptive
    filtering genuinely helps (gradients, photographic content).
    """
    if len(rgb) != width * height * 3:
        raise ValueError("rgb buffer size does not match dimensions")
    stride = width * 3

    none_raw = b"".join(
        b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height)
    )

    prev_row: bytes | None = None
    adaptive_rows = []
    for y in range(height):
        row = rgb[y * stride:(y + 1) * stride]
        adaptive_rows.append(_filter_scanline(row, prev_row))
        prev_row = row
    adaptive_raw = b"".join(adaptive_rows)

    none_compressed = zlib.compress(none_raw, 9)
    adaptive_compressed = zlib.compress(adaptive_raw, 9)
    idat = adaptive_compressed if len(adaptive_compressed) < len(none_compressed) else none_compressed

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", idat)
            + _chunk(b"IEND", b""))


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data)))


def write_png(path: str, width: int, height: int, rgb: bytes) -> int:
    data = png_bytes(width, height, rgb)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)
