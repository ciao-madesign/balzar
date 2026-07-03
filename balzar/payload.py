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


# ------------------------------------------------------- physical carrier
#
# A payload larger than one QR code can be split into self-describing
# chunks, each sized to fit a single QR:
#
#     b"BZC1" | u16 index | u16 total | u32 crc32(full payload) | data
#
# Print each chunk as one QR code (or engrave it, or whatever the physical
# medium is): the sequence of codes IS the content. Chunks carry their own
# position and the checksum of the whole, so they can be scanned in any
# order and reassembly verifies integrity end-to-end.

CHUNK_MAGIC = b"BZC1"
_CHUNK_HEADER = 12


def chunk_payload(payload: bytes, chunk_size: int = QR_V40_BINARY_CAPACITY) -> list[bytes]:
    if chunk_size <= _CHUNK_HEADER:
        raise PayloadError(f"chunk_size must exceed the {_CHUNK_HEADER}-byte header")
    data_size = chunk_size - _CHUNK_HEADER
    total = max(1, (len(payload) + data_size - 1) // data_size)
    if total > 0xFFFF:
        raise PayloadError("payload needs more than 65535 chunks")
    crc = zlib.crc32(payload)
    return [
        CHUNK_MAGIC + struct.pack(">HHI", i, total, crc)
        + payload[i * data_size:(i + 1) * data_size]
        for i in range(total)
    ]


def assemble_chunks(chunks: list[bytes]) -> bytes:
    """Rebuild a payload from chunks supplied in any order."""
    if not chunks:
        raise PayloadError("no chunks to assemble")
    parts: dict[int, bytes] = {}
    total = crc = None
    for chunk in chunks:
        if len(chunk) < _CHUNK_HEADER or chunk[:4] != CHUNK_MAGIC:
            raise PayloadError("not a balzar chunk (bad magic)")
        i, t, c = struct.unpack(">HHI", chunk[4:_CHUNK_HEADER])
        if total is None:
            total, crc = t, c
        elif (t, c) != (total, crc):
            raise PayloadError("chunks belong to different payloads")
        if i in parts and parts[i] != chunk[_CHUNK_HEADER:]:
            raise PayloadError(f"conflicting duplicates of chunk {i}")
        parts[i] = chunk[_CHUNK_HEADER:]
    missing = sorted(set(range(total)) - set(parts))
    if missing:
        raise PayloadError(f"missing chunks: {missing} (of {total})")
    payload = b"".join(parts[i] for i in range(total))
    if zlib.crc32(payload) != crc:
        raise PayloadError("assembled payload fails the integrity check")
    return payload
