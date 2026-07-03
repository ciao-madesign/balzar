"""Compact binary payload: the physical carrier of the description.

Layout:  b"BZR1" | uint32 canonical-length | uint32 crc32 | deflate(canonical)

The payload encodes the *canonical* program text (comments stripped,
whitespace normalized), so equivalent sources map to identical payloads.
`to_base64` yields a text form that fits directly into a QR code
(a version-40 binary QR holds ~2953 bytes).
"""

from __future__ import annotations

import base64
import struct
import zlib

MAGIC = b"BZR1"
QR_V40_BINARY_CAPACITY = 2953

from .dsl import canonical


class PayloadError(ValueError):
    pass


def encode_payload(program_text: str) -> bytes:
    text = canonical(program_text).encode("utf-8")
    body = zlib.compress(text, 9)
    header = MAGIC + struct.pack(">II", len(text), zlib.crc32(text))
    return header + body


def decode_payload(data: bytes) -> str:
    if len(data) < 12 or data[:4] != MAGIC:
        raise PayloadError("not a balzar payload (bad magic)")
    length, crc = struct.unpack(">II", data[4:12])
    try:
        text = zlib.decompress(data[12:])
    except zlib.error as exc:
        raise PayloadError(f"corrupt payload body: {exc}") from None
    if len(text) != length or zlib.crc32(text) != crc:
        raise PayloadError("payload integrity check failed (length/CRC mismatch)")
    return text.decode("utf-8")


def to_base64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def from_base64(text: str) -> bytes:
    return base64.b64decode(text.strip().encode("ascii"), validate=True)


def fits_in_qr(payload: bytes) -> bool:
    return len(payload) <= QR_V40_BINARY_CAPACITY
