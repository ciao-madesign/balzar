"""Multi-document bundle: several typed sub-documents (a 3D assembly, a
CSV lookup table, ...) carried as ONE opaque blob of bytes -- so the
existing physical-carrier machinery (chunk_payload/payload_to_qr_frames/
LiveScanner in payload.py/qr.py, which already treats *any* payload as
opaque bytes with a CRC) carries a bundle through completely unchanged,
exactly like it already carries a bare BZM1 or BZR1 payload. No changes
needed anywhere in qr.py for this to work.

Format:  b"BZX1" | u16 version | u16 item_count | u32 body_length
         | u32 crc32(body) | deflate(body)

body = concatenation of items, each:
    u8  kind_len | kind (ascii)     -- KIND_3D or KIND_CSV
    u8  label_len | label (utf-8)   -- human-readable, e.g. a filename
    u32 data_len | data             -- the item's own native bytes

Deliberately ONE compress+CRC pass over the whole concatenated body,
not one per item: each item's own native format already self-checks on
its own decode where that matters (BZM1 has its own length+CRC), and a
single outer pass compresses better across items than N separate passes
would (same reasoning already applied by BZM1/BZR1 for their own
bodies).

Scope, explicit: only "3d" (a complete BZM1 blob from scene3d.py) and
"csv" (UTF-8 text, e.g. an alarm-code lookup table) are produced today.
PDF technical drawings were discussed and deliberately excluded: balzar
has no PDF encoder at all (no compression claim would be honest -- it
would be pure raw carriage), and a real PDF drawing is typically large
enough to blow past the "fits in a handful of QR codes" property that
makes the physical-carrier design useful in the field in the first
place -- see CLAUDE.md for the full reasoning. Adding a "pdf" kind
later (raw bytes, explicitly no generative compression) is possible
without changing this format, but is a decision to make deliberately,
not a side effect of building the bundle mechanism.
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"BZX1"
_HEADER_LEN = 4 + 2 + 2 + 4 + 4

KIND_3D = "3d"
KIND_CSV = "csv"


class BundleError(ValueError):
    pass


@dataclass
class BundleItem:
    kind: str
    label: str
    data: bytes


def _pack_item(item: BundleItem) -> bytes:
    kind_b = item.kind.encode("ascii")
    label_b = item.label.encode("utf-8")
    if len(kind_b) > 255 or len(label_b) > 255:
        raise BundleError("kind/label devono stare in 255 byte ciascuno")
    return (struct.pack("<B", len(kind_b)) + kind_b
            + struct.pack("<B", len(label_b)) + label_b
            + struct.pack("<I", len(item.data)) + item.data)


def _unpack_items(body: bytes, expected_count: int) -> list[BundleItem]:
    items: list[BundleItem] = []
    off = 0
    while off < len(body):
        if off + 1 > len(body):
            raise BundleError("bundle troncato (kind mancante)")
        (kind_len,) = struct.unpack_from("<B", body, off); off += 1
        kind = body[off:off + kind_len].decode("ascii"); off += kind_len
        (label_len,) = struct.unpack_from("<B", body, off); off += 1
        label = body[off:off + label_len].decode("utf-8"); off += label_len
        (data_len,) = struct.unpack_from("<I", body, off); off += 4
        data = body[off:off + data_len]; off += data_len
        if len(data) != data_len:
            raise BundleError("bundle troncato (dati di un elemento incompleti)")
        items.append(BundleItem(kind=kind, label=label, data=data))
    if len(items) != expected_count:
        raise BundleError(
            f"numero di elementi incoerente: header dichiara {expected_count}, trovati {len(items)}")
    return items


def encode_bundle(items: list[BundleItem]) -> bytes:
    if not items:
        raise BundleError("un bundle vuoto non ha senso")
    body = b"".join(_pack_item(it) for it in items)
    header = MAGIC + struct.pack("<HHII", 1, len(items), len(body), zlib.crc32(body))
    return header + zlib.compress(body, 9)


def decode_bundle(data: bytes) -> list[BundleItem]:
    if len(data) < _HEADER_LEN or data[:4] != MAGIC:
        raise BundleError("non e' un bundle balzar (magic BZX1 non valido)")
    version, item_count, body_len, crc = struct.unpack_from("<HHII", data, 4)
    if version != 1:
        raise BundleError(f"versione BZX1 non supportata: {version}")
    try:
        body = zlib.decompress(data[_HEADER_LEN:])
    except zlib.error as exc:
        raise BundleError(f"corpo del bundle corrotto: {exc}") from None
    if len(body) != body_len or zlib.crc32(body) != crc:
        raise BundleError("controllo di integrita' del bundle fallito (lunghezza/CRC)")
    return _unpack_items(body, item_count)


def is_bundle(data: bytes) -> bool:
    return data[:4] == MAGIC


# ------------------------------------------------------ file-based helper

def encode_bundle_files(paths: list[str]) -> bytes:
    """Dispatch each path to a bundle item by extension and pack them
    together. Strict on purpose (unlike encode_independent's per-file
    isolation in sequence.py): a bundle is a deliberate small set the
    user assembled for one scan, so silently dropping a file the user
    explicitly included (e.g. the alarm table, in the exact maintenance
    flow this exists for) would be a much worse failure than refusing
    up front with a clear reason."""
    from .scene3d import Scene3DError, encode_payload as encode_scene, parse_3dxml
    from .scene3d import MAGIC as BZM1_MAGIC

    items: list[BundleItem] = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        label = os.path.basename(path)
        if ext == ".3dxml":
            try:
                scene = parse_3dxml(path)
            except Scene3DError as exc:
                raise BundleError(f"{label}: {exc}") from None
            items.append(BundleItem(KIND_3D, label, encode_scene(scene)))
        elif ext == ".b3d":
            with open(path, "rb") as fh:
                data = fh.read()
            if data[:4] != BZM1_MAGIC:
                raise BundleError(f"{label}: non e' un payload BZM1 valido")
            items.append(BundleItem(KIND_3D, label, data))
        elif ext == ".csv":
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BundleError(f"{label}: CSV non e' UTF-8 valido: {exc}") from None
            items.append(BundleItem(KIND_CSV, label, data))
        else:
            raise BundleError(
                f"{label}: formato non supportato nel bundle (.{ext.lstrip('.')}) -- "
                f"atteso .3dxml/.b3d (assieme 3D) o .csv (tabella allarmi)")
    return encode_bundle(items)
