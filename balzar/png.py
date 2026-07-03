"""Minimal pure-Python PNG writer (RGB8, no interlace, no dependencies)."""

from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data)))


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Encode raw row-major RGB8 bytes (len == w*h*3) as a PNG file."""
    if len(rgb) != width * height * 3:
        raise ValueError("rgb buffer size does not match dimensions")
    stride = width * 3
    raw = b"".join(
        b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def write_png(path: str, width: int, height: int, rgb: bytes) -> int:
    data = png_bytes(width, height, rgb)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)
