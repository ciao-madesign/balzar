"""Multi-document bundle: several typed sub-documents (a 3D assembly, an
alarm-code table, generic consultable documents) carried as ONE opaque
blob of bytes -- so the existing physical-carrier machinery
(chunk_payload/payload_to_qr_frames/LiveScanner in payload.py/qr.py,
which already treats *any* payload as opaque bytes with a CRC) carries a
bundle through completely unchanged, exactly like it already carries a
bare BZM1 or BZR1 payload. No changes needed anywhere in qr.py for this.

Format:  b"BZX1" | u16 version | u16 item_count | u32 body_length
         | u32 crc32(body) | deflate(body)

body = concatenation of items, each:
    u8  kind_len | kind (ascii)     -- KIND_3D / KIND_2D / KIND_ALARM / KIND_DOC
    u8  label_len | label (utf-8)   -- human-readable, e.g. a filename
    u32 data_len | data             -- the item's own native bytes

The `kind` is a ROLE, not a file type: it tells the reader how to
dispatch the item (KIND_3D -> the model viewer + BOM; KIND_2D -> a BZR1
program rendered fresh into the doc index, see viewer3d._render_2d_item;
KIND_ALARM -> the search bar; KIND_DOC -> the navigable document index,
just consultable, not linked to the 3D). A .csv is KIND_ALARM only when
the user explicitly marks it as the alarm table; an unmarked .csv (or
any other non-3D/non-2D file) is a KIND_DOC. Content type for a KIND_DOC
is inferred from its label's extension at view time, not stored here;
KIND_2D always gets rendered, its content type is never ambiguous.

Deliberately ONE compress+CRC pass over the whole concatenated body,
not one per item: each item's own native format already self-checks on
its own decode where that matters (BZM1 has its own length+CRC), and a
single outer pass compresses better across items than N separate passes
would (same reasoning already applied by BZM1/BZR1 for their own
bodies).

A bundle needs at least one item but NOT a 3D item -- a pure set of
consultable documents (no 3D at all) is valid, and viewer3d.py renders
an index-only page for it.

Scope, explicit: PDF technical drawings and any structured format the
viewer can't render inline are carried as raw KIND_DOC bytes (offered
for download, not previewed). balzar has no PDF encoder -- carrying one
is pure raw carriage with no compression claim, and a real PDF drawing
is typically large enough to blow past the "fits in a handful of QR
codes" property that makes the physical carrier useful (see CLAUDE.md).
Nothing here stops you bundling one, but the size math is on you.
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"BZX1"
_HEADER_LEN = 4 + 2 + 2 + 4 + 4

# item roles, not file extensions: the kind says what the item IS FOR,
# so the reader knows how to dispatch it, independent of its content type
KIND_3D = "3d"        # a BZM1 3D assembly -> the model viewer + BOM
KIND_ALARM = "alarm"  # a codice_allarme,nome_componente CSV -> wired to the search bar
KIND_2D = "2d"        # a BZR1 2D program (drawing/schematic) -> rendered PNG/GIF/SVG in the index
KIND_DOC = "doc"      # a generic consultable document -> the navigable index, not linked to the 3D

# back-compat alias: earlier bundles tagged the alarm table "csv". Reading
# still accepts it (see decode dispatch in viewer3d/gui), new bundles use
# KIND_ALARM. A generic CSV that is NOT an alarm table is a KIND_DOC.
KIND_CSV = KIND_ALARM


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


def is_alarm_kind(kind: str) -> bool:
    """True for an item that powers the alarm search. Accepts the legacy
    "csv" tag as well as KIND_ALARM so a bundle written before the role
    was renamed still wires its search bar."""
    return kind in (KIND_ALARM, "csv")


# ------------------------------------------------------ file-based helper

def encode_bundle_files(paths: list[str], alarm_paths=None) -> bytes:
    """Dispatch each path to a bundle item and pack them together.

    Roles are assigned explicitly, never guessed from content:
    - `.3dxml`/`.b3d` -> KIND_3D (the model);
    - `.bzr`/`.bzp` -> KIND_2D (a 2D drawing/schematic program -- the
      viewer renders it fresh at open time, see viewer3d._render_2d_item,
      the same "generate, don't store pixels" principle as the rest of
      balzar applied to a bundled document instead of the main payload);
    - any path listed in `alarm_paths` -> KIND_ALARM (the search table),
      validated as UTF-8;
    - every other file -> KIND_DOC (a generic consultable document,
      raw bytes, rendered/downloaded by the viewer from its extension).

    So a `.csv` is an alarm table ONLY when the caller marks it in
    `alarm_paths`; an unmarked `.csv` is just a document. No 3D file is
    required -- a bundle of documents alone is valid.

    Strict on purpose (unlike encode_independent's per-file isolation in
    sequence.py): a bundle is a deliberate small set the user assembled
    for one scan, so a genuinely broken 3D/2D/alarm file refuses up front
    with a clear reason rather than being silently dropped. A generic
    doc, by contrast, is carried as-is with no parsing that could fail."""
    from .interpreter import render as render_2d
    from .payload import MAGIC as BZR1_MAGIC
    from .payload import encode_payload as encode_2d
    from .scene3d import Scene3DError, encode_payload as encode_scene, parse_3dxml
    from .scene3d import MAGIC as BZM1_MAGIC

    alarm_set = {os.path.abspath(p) for p in (alarm_paths or [])}

    items: list[BundleItem] = []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        label = os.path.basename(path)
        if os.path.abspath(path) in alarm_set:
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BundleError(f"{label}: tabella allarmi non e' UTF-8 valido: {exc}") from None
            items.append(BundleItem(KIND_ALARM, label, data))
        elif ext == ".3dxml":
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
        elif ext == ".bzr":
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            try:
                render_2d(text)  # a real render, not just tokenizing --
                                 # canonical() alone accepts a program
                                 # with unknown/malformed instructions,
                                 # only actually running it catches that
                data = encode_2d(text)
            except (SyntaxError, ValueError, RuntimeError) as exc:
                raise BundleError(f"{label}: {exc}") from None
            items.append(BundleItem(KIND_2D, label, data))
        elif ext == ".bzp":
            with open(path, "rb") as fh:
                data = fh.read()
            if data[:4] != BZR1_MAGIC:
                raise BundleError(f"{label}: non e' un payload BZR1 valido")
            items.append(BundleItem(KIND_2D, label, data))
        else:
            with open(path, "rb") as fh:
                items.append(BundleItem(KIND_DOC, label, fh.read()))
    return encode_bundle(items)
