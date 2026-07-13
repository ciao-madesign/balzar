"""3D parametric assemblies: 3DXML ingestion -> compact payload (BZM1).

Source format is 3DXML (Dassault, published schema), not STEP and not
the proprietary SOLIDWORKS Composer .smg binary blob — see CLAUDE.md
SS9.1 for why. A 3DXML file is a ZIP: Manifest.xml points at the main
scene-graph document (Reference3D/Instance3D tree, named nodes,
RelativeMatrix transforms), and every unique tessellated shape is
externalized as its own <hash>.3DRep file (plain XML: Positions/Faces
strips) referenced by ReferenceRep/InstanceRep — geometry deduplication
is already the structure of the format, not something detected here.

The Reference3D/Instance3D graph is a DAG, not a tree: a Reference3D
(e.g. a repeated sub-assembly) can be targeted by more than one
Instance3D. `parse_3dxml` preserves that reuse (a Reference is parsed
once no matter how many parents instance it) — that reuse is where most
of the compression comes from (see CLAUDE.md SS9.2: ~20.8x instancing on
the real test assembly), so flattening it away here would throw away
the whole point.

Size optimizations applied in the binary payload (all prototyped and
measured against the real test assembly before landing here — see
CLAUDE.md SS9.2/SS9.7 for the numbers, not re-derived from scratch):
  - vertex positions: int16 per-shape (own bounding box -> its own
    scale/offset), not float32 -- a real, disclosed precision loss
    (`mean_vertex_error` on the encode result says exactly how much),
    same honesty pattern as `mean_color_error` in encoder.py: the
    self-check compares against the already-quantized source, not the
    original full-precision vertices.
  - triangle-strip indices: uint16 per shape by default (most real
    shapes are well under 65536 vertices), widened to uint32 only for
    the shape that actually needs it -- a real assembly (CLAUDE.md
    SS9.30) surfaced a single tessellated surface with 290,192 vertices
    and 80,535 strips, over BOTH the uint16 index range and the
    original uint16 strip-count field. n_strips is now uint32
    unconditionally (payload version 2); a version-1 payload (which by
    construction never held an over-limit shape) still decodes correctly
    with the old fixed widths.
  - RelativeMatrix rotation: a 2-byte code for the common case (a pure
    axis permutation with entries only in {-1,0,1} -- measured as 100%
    of leaf-level placements on the real test assembly), falling back
    to 9 raw floats only when the rotation is a genuine arbitrary angle.

`generate_bom` produces the other half of the "scan a code, see the
exploded view AND the parts list" vision (the 2D precedent is
examples/etichetta_bom.bzr): a flat name -> quantity table, counting
every leaf placement with full multiplicity through nested sub-assembly
repetition, not the number of Reference3D leaf definitions.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field

MAGIC = b"BZM1"
_NS = {"d": "http://www.3ds.com/xsd/3DXML"}
_NO_NAME = 0xFFFF


class Scene3DError(ValueError):
    pass


@dataclass
class Shape:
    """One unique tessellated geometry (one <hash>.3DRep in the source)."""
    name: str | None
    color: tuple[int, int, int]
    vertices: list[tuple[float, float, float]]
    strips: list[list[int]]


@dataclass
class Reference:
    """One Reference3D: either a leaf part (shape_index set) or a group/
    sub-assembly whose children are placements of other References."""
    name: str | None
    shape_index: int | None
    # each child: (target_reference_index, instance_name, 12-value RelativeMatrix)
    children: list[tuple[int, str | None, tuple[float, ...]]] = field(default_factory=list)


@dataclass
class Scene3D:
    shapes: list[Shape]
    references: list[Reference]
    root: int


@dataclass
class BomEntry:
    """One line of the bill of materials: a named leaf part (or, when
    collapsed -- see generate_bom's collapse_names -- a whole named
    sub-assembly) and how many times it's actually placed in the
    assembled scene. `shape_index` is None for a collapsed sub-assembly
    row (it has no single shape of its own). `material_names` is the
    exact set of glTF material names (balzar/gltf.py) this row should
    highlight -- a single-item list equal to `name` for an ordinary leaf
    row (today's behaviour, unchanged), or the full set of that specific
    sub-assembly's own descendant leaf materials for a collapsed row."""
    name: str
    shape_index: int | None
    count: int
    material_names: list[str]


@dataclass
class Scene3DEncodeResult:
    payload: bytes
    shape_count: int
    reference_count: int
    instance_count: int
    vertex_count: int
    triangle_index_count: int
    mean_vertex_error: float  # avg per-axis abs distance introduced by int16
                              # quantization -- 0.0 only for a degenerate
                              # (single-point or perfectly flat) shape
    bom: list[BomEntry]


def _f32(v: float) -> float:
    """Round-trip through float32 immediately at parse time, so the
    in-memory Scene3D already IS the precision that will be stored —
    the declared precision reduction from the source's text (double-
    precision-looking decimals), same honesty pattern as mean_color_error
    elsewhere: the self-check below verifies exact round-trip of the
    already-quantized value, not preservation of the source's full
    double precision."""
    return struct.unpack("<f", struct.pack("<f", v))[0]


# ------------------------------------------------- vertex quantization

def _quantize_positions(vertices: list[tuple[float, float, float]]):
    """Per-shape int16 quantization: each shape gets its own bounding box
    as scale/offset (a small local part gets far more precision out of
    16 bits than one scale shared across the whole assembly would).
    Returns (lo, scale, quantized) where quantized is a list of
    (qx,qy,qz) int16-range triples; dequantize with `_dequantize_positions`
    using the same (lo, scale)."""
    if not vertices:
        return (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), []
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    lo = tuple(_f32(v) for v in (min(xs), min(ys), min(zs)))
    hi = (max(xs), max(ys), max(zs))
    # a flat axis (hi == lo, e.g. a planar face) gets scale=1 as a safe
    # placeholder -- every vertex quantizes to the same code and
    # dequantizes back to exactly `lo` on that axis, zero error
    scale = tuple(_f32((hi[k] - lo[k]) / 65534) if hi[k] > lo[k] else 1.0 for k in range(3))
    quantized = []
    for x, y, z in vertices:
        q = (
            round((x - lo[0]) / scale[0]) - 32767,
            round((y - lo[1]) / scale[1]) - 32767,
            round((z - lo[2]) / scale[2]) - 32767,
        )
        quantized.append(tuple(max(-32767, min(32767, v)) for v in q))
    return lo, scale, quantized


def _dequantize_positions(lo, scale, quantized) -> list[tuple[float, float, float]]:
    return [
        (_f32(lo[0] + (qx + 32767) * scale[0]),
         _f32(lo[1] + (qy + 32767) * scale[1]),
         _f32(lo[2] + (qz + 32767) * scale[2]))
        for qx, qy, qz in quantized
    ]


# ------------------------------------------- compact transform encoding

def _encode_matrix(m: tuple[float, ...]) -> bytes:
    """RelativeMatrix (9 rotation + 3 translation) -> bytes. The common
    case on real assemblies (measured: 100% of leaf placements on the
    test file) is a pure axis permutation -- every entry exactly -1, 0
    or 1 -- encodable as one base-3 digit per entry (3**9 = 19683 fits
    in 2 bytes) instead of 9 raw floats. Anything else (a genuine
    arbitrary rotation angle) falls back to the 9 floats untouched."""
    rot, tr = m[:9], m[9:12]
    if all(abs(v - round(v)) < 1e-6 and round(v) in (-1, 0, 1) for v in rot):
        code = 0
        for v in rot:
            code = code * 3 + (round(v) + 1)
        out = struct.pack("<BH", 0, code)
    else:
        out = struct.pack("<B", 1) + struct.pack("<9f", *rot)
    return out + struct.pack("<3f", *tr)


def _decode_matrix(data: bytes, off: int) -> tuple[tuple[float, ...], int]:
    (kind,) = struct.unpack_from("<B", data, off); off += 1
    if kind == 0:
        (code,) = struct.unpack_from("<H", data, off); off += 2
        digits = []
        for _ in range(9):
            digits.append(code % 3)
            code //= 3
        digits.reverse()  # extraction order is least-significant first
                          # (== last-encoded first); reverse to restore
                          # the original r[0]..r[8] order
        rot = tuple(float(d - 1) for d in digits)
    else:
        rot = struct.unpack_from("<9f", data, off); off += 36
    tr = struct.unpack_from("<3f", data, off); off += 12
    return rot + tr, off


# --------------------------------------------------------------- parsing

def _parse_matrix(text: str | None) -> tuple[float, ...]:
    if not text:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    vals = [_f32(float(x)) for x in text.split()]
    if len(vals) != 12:
        raise Scene3DError(f"RelativeMatrix con {len(vals)} valori, attesi 12")
    return tuple(vals)


def _parse_3drep(data: bytes) -> Shape:
    tree = ET.fromstring(data)
    color = (255, 255, 255)
    color_el = tree.find(".//d:Color", _NS)
    if color_el is not None:
        r = float(color_el.get("red", "1"))
        g = float(color_el.get("green", "1"))
        b = float(color_el.get("blue", "1"))
        color = (round(r * 255), round(g * 255), round(b * 255))

    positions_el = tree.find(".//d:Positions", _NS)
    vals = [_f32(float(x)) for x in
           (positions_el.text.split() if positions_el is not None and positions_el.text else [])]
    if len(vals) % 3 != 0:
        raise Scene3DError(".3DRep con un numero di coordinate non multiplo di 3")
    vertices = [(vals[i], vals[i + 1], vals[i + 2]) for i in range(0, len(vals), 3)]

    strips: list[list[int]] = []
    for face_el in tree.findall(".//d:Face", _NS):
        for strip_text in (face_el.get("strips") or "").split(","):
            strip_text = strip_text.strip()
            if strip_text:
                strips.append([int(x) for x in strip_text.split()])

    return Shape(name=None, color=color, vertices=vertices, strips=strips)


def parse_3dxml(path: str) -> Scene3D:
    """3DXML file -> Scene3D, preserving names/grouping and the DAG reuse
    of repeated sub-assemblies (built once, referenced by index)."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise Scene3DError(f"non e' un archivio 3DXML valido: {exc}") from None

    with zf:
        try:
            manifest = ET.fromstring(zf.read("Manifest.xml"))
        except KeyError:
            raise Scene3DError("Manifest.xml mancante nell'archivio 3DXML")
        root_el = manifest.find("Root")
        if root_el is None or not root_el.text:
            raise Scene3DError("Manifest.xml non indica il documento radice (<Root>)")
        main_name = root_el.text.strip()
        try:
            main_xml = zf.read(main_name)
        except KeyError:
            raise Scene3DError(f"documento radice '{main_name}' non trovato nell'archivio")

        tree = ET.fromstring(main_xml)
        ps = tree.find("d:ProductStructure", _NS)
        if ps is None:
            raise Scene3DError("nessun <ProductStructure> nel documento 3DXML")
        root_id = ps.get("root")
        if root_id is None:
            raise Scene3DError("<ProductStructure> senza attributo 'root'")

        ref_names: dict[str, str | None] = {}
        inst3d_by_parent: dict[str, list[tuple[str, str | None, str | None]]] = {}
        instrep_by_owner: dict[str, str] = {}
        refrep_file: dict[str, str] = {}

        for el in ps:
            tag = el.tag.split("}")[-1]
            if tag == "Reference3D":
                ref_names[el.get("id")] = el.get("name") or None
            elif tag == "Instance3D":
                parent = el.findtext("d:IsAggregatedBy", namespaces=_NS)
                target = el.findtext("d:IsInstanceOf", namespaces=_NS)
                mtx_el = el.find("d:RelativeMatrix", _NS)
                mtx_text = mtx_el.text if mtx_el is not None else None
                inst3d_by_parent.setdefault(parent, []).append(
                    (target, el.get("name") or None, mtx_text))
            elif tag == "ReferenceRep":
                assoc = el.get("associatedFile") or ""
                refrep_file[el.get("id")] = assoc.split(":")[-1]
            elif tag == "InstanceRep":
                owner = el.findtext("d:IsAggregatedBy", namespaces=_NS)
                target = el.findtext("d:IsInstanceOf", namespaces=_NS)
                instrep_by_owner[owner] = target

        shapes: list[Shape] = []
        geom_file_to_shape_index: dict[str, int] = {}

        def shape_index_for(ref_id: str) -> int:
            rep_id = instrep_by_owner[ref_id]
            fname = refrep_file.get(rep_id)
            if fname is None:
                raise Scene3DError(f"ReferenceRep '{rep_id}' senza associatedFile")
            if fname in geom_file_to_shape_index:
                return geom_file_to_shape_index[fname]
            try:
                raw = zf.read(fname)
            except KeyError:
                raise Scene3DError(f"geometria '{fname}' referenziata ma assente nell'archivio")
            idx = len(shapes)
            shapes.append(_parse_3drep(raw))
            geom_file_to_shape_index[fname] = idx
            return idx

        ref_index_of: dict[str, int] = {}
        references_by_index: dict[int, Reference] = {}

        def build(ref_id: str) -> int:
            if ref_id in ref_index_of:
                return ref_index_of[ref_id]
            idx = len(ref_index_of)
            ref_index_of[ref_id] = idx  # reserve before recursing: a DAG reference
                                        # reached again later just returns this index
            shape_idx = shape_index_for(ref_id) if ref_id in instrep_by_owner else None
            children = [
                (build(target), inst_name, _parse_matrix(mtx_text))
                for target, inst_name, mtx_text in inst3d_by_parent.get(ref_id, [])
            ]
            references_by_index[idx] = Reference(
                name=ref_names.get(ref_id), shape_index=shape_idx, children=children)
            return idx

        root_idx = build(root_id)
        references = [references_by_index[i] for i in range(len(references_by_index))]
        return Scene3D(shapes=shapes, references=references, root=root_idx)


# ------------------------------------------------------- binary payload

def _pack_str_table(names: list[str]) -> bytes:
    out = struct.pack("<I", len(names))
    for n in names:
        b = n.encode("utf-8")
        out += struct.pack("<H", len(b)) + b
    return out


def _unpack_str_table(data: bytes, off: int) -> tuple[list[str], int]:
    (count,) = struct.unpack_from("<I", data, off); off += 4
    names = []
    for _ in range(count):
        (ln,) = struct.unpack_from("<H", data, off); off += 2
        names.append(data[off:off + ln].decode("utf-8")); off += ln
    return names, off


def _serialize(scene: Scene3D) -> bytes:
    names: list[str] = []
    name_index: dict[str, int] = {}

    def name_idx(n: str | None) -> int:
        if n is None:
            return _NO_NAME
        if n not in name_index:
            name_index[n] = len(names)
            names.append(n)
        return name_index[n]

    for shape in scene.shapes:
        name_idx(shape.name)
    for ref in scene.references:
        name_idx(ref.name)
        for _, inst_name, _ in ref.children:
            name_idx(inst_name)

    out = bytearray()
    out += _pack_str_table(names)

    out += struct.pack("<H", len(scene.shapes))
    for shape in scene.shapes:
        # A real assembly (CLAUDE.md SS9.30) surfaced two hard limits a
        # synthetic test fixture never approached: a single tessellated
        # surface with 290,192 vertices (over the uint16 index range) and
        # 80,535 strips on that same shape (over a uint16 *count*, not
        # just index value). Fixed instead of raising: n_strips is now a
        # uint32 count unconditionally (2 extra bytes/shape, negligible),
        # and strip index VALUES widen to uint32 only for the rare shape
        # that actually needs it -- derived from n_verts already stored
        # (>65535), not a new per-shape flag, so ordinary small shapes
        # (the vast majority) keep paying nothing for this.
        wide = len(shape.vertices) > 65535
        idx_fmt = "I" if wide else "H"
        out += struct.pack("<H", name_idx(shape.name))
        out += struct.pack("<BBB", *shape.color)
        out += struct.pack("<I", len(shape.vertices))
        lo, scale, quantized = _quantize_positions(shape.vertices)
        out += struct.pack("<3f", *lo)
        out += struct.pack("<3f", *scale)
        for qx, qy, qz in quantized:
            out += struct.pack("<3h", qx, qy, qz)
        out += struct.pack("<I", len(shape.strips))
        for strip in shape.strips:
            out += struct.pack("<H", len(strip))
            for idx in strip:
                out += struct.pack(f"<{idx_fmt}", idx)

    out += struct.pack("<I", len(scene.references))
    for ref in scene.references:
        out += struct.pack("<H", name_idx(ref.name))
        out += struct.pack("<BH", 1 if ref.shape_index is not None else 0,
                           ref.shape_index if ref.shape_index is not None else 0)
        out += struct.pack("<I", len(ref.children))
        for target, inst_name, matrix in ref.children:
            out += struct.pack("<I", target)
            out += struct.pack("<H", name_idx(inst_name))
            out += _encode_matrix(matrix)

    out += struct.pack("<I", scene.root)
    return bytes(out)


def _deserialize(data: bytes, version: int = 2) -> Scene3D:
    """`version` selects the on-disk shape of n_strips/strip-index width:
    version 1 (pre-CLAUDE.md SS9.30) always wrote both as uint16 -- by
    construction a version-1 payload never held a shape over 65535
    vertices (encode_payload raised instead), so reading it back with the
    OLD fixed widths is exact, no wide-index branch needed for it.
    Version 2 widens n_strips to uint32 unconditionally and lets a
    shape's strip index values be uint32 too, but only when its own
    vertex count actually needs it. Kept, not dropped, because the
    desktop app's local library (balzar/library.py, CLAUDE.md SS9.22)
    persists .b3d files across balzar upgrades -- a real, not
    hypothetical, reason an old-format file could still be opened."""
    names, off = _unpack_str_table(data, 0)

    def name_at(idx: int) -> str | None:
        return None if idx == _NO_NAME else names[idx]

    (n_shapes,) = struct.unpack_from("<H", data, off); off += 2
    shapes = []
    for _ in range(n_shapes):
        (name_i,) = struct.unpack_from("<H", data, off); off += 2
        color = struct.unpack_from("<BBB", data, off); off += 3
        (n_verts,) = struct.unpack_from("<I", data, off); off += 4
        lo = struct.unpack_from("<3f", data, off); off += 12
        scale = struct.unpack_from("<3f", data, off); off += 12
        quantized = []
        for _ in range(n_verts):
            q = struct.unpack_from("<3h", data, off); off += 6
            quantized.append(q)
        vertices = _dequantize_positions(lo, scale, quantized)
        if version >= 2:
            (n_strips,) = struct.unpack_from("<I", data, off); off += 4
            wide = n_verts > 65535
        else:
            (n_strips,) = struct.unpack_from("<H", data, off); off += 2
            wide = False
        idx_fmt = "I" if wide else "H"
        idx_size = 4 if wide else 2
        strips = []
        for _ in range(n_strips):
            (slen,) = struct.unpack_from("<H", data, off); off += 2
            idxs = list(struct.unpack_from(f"<{slen}{idx_fmt}", data, off)); off += idx_size * slen
            strips.append(idxs)
        shapes.append(Shape(name=name_at(name_i), color=color, vertices=vertices, strips=strips))

    (n_refs,) = struct.unpack_from("<I", data, off); off += 4
    references = []
    for _ in range(n_refs):
        (name_i,) = struct.unpack_from("<H", data, off); off += 2
        has_shape, shape_i = struct.unpack_from("<BH", data, off); off += 3
        (n_children,) = struct.unpack_from("<I", data, off); off += 4
        children = []
        for _ in range(n_children):
            (target,) = struct.unpack_from("<I", data, off); off += 4
            (inst_name_i,) = struct.unpack_from("<H", data, off); off += 2
            matrix, off = _decode_matrix(data, off)
            children.append((target, name_at(inst_name_i), matrix))
        references.append(Reference(
            name=name_at(name_i),
            shape_index=shape_i if has_shape else None,
            children=children,
        ))

    (root,) = struct.unpack_from("<I", data, off); off += 4
    return Scene3D(shapes=shapes, references=references, root=root)


def encode_payload(scene: Scene3D) -> bytes:
    body = _serialize(scene)
    header = MAGIC + struct.pack("<HII", 2, len(body), zlib.crc32(body))
    return header + zlib.compress(body, 9)


def decode_payload(data: bytes) -> Scene3D:
    if len(data) < 14 or data[:4] != MAGIC:
        raise Scene3DError("non e' un payload balzar 3D (magic BZM1 non valido)")
    version, length, crc = struct.unpack_from("<HII", data, 4)
    if version not in (1, 2):
        raise Scene3DError(f"versione BZM1 non supportata: {version}")
    try:
        body = zlib.decompress(data[14:])
    except zlib.error as exc:
        raise Scene3DError(f"corpo del payload corrotto: {exc}") from None
    if len(body) != length or zlib.crc32(body) != crc:
        raise Scene3DError("controllo di integrita' del payload fallito (lunghezza/CRC)")
    return _deserialize(body, version)


# ------------------------------------------------------------- top level

def bom_display_name(ref: Reference) -> str:
    """The label a leaf reference shows up under in the BOM -- also
    reused by gltf.py as the glTF material/mesh name, so a part clicked
    in the 3D view and its BOM row can be matched by this same string."""
    return ref.name or f"(senza nome, forma {ref.shape_index})"


_AUTO_GENERATED_LEAF_NAME = re.compile(r"Object \d+")


def effective_display_name(parent: Reference | None, ref: Reference) -> str:
    """bom_display_name, but preferring a wrapping reference's name over
    the leaf's own specifically when the leaf's own name looks like the
    export tool's auto-generated placeholder ("Object N", assigned by
    the software, not the engineer) rather than a real part name.

    Confirmed on a real file (CLAUDE.md SS9.12): every one of the 245
    real leaf placements had exactly this shape -- a "product" reference
    with a real, meaningful part/sub-assembly name (e.g.
    "VASCA_ACCUMULO_SUB009") wrapping, via a single otherwise-unnamed
    Instance3D, the reference that actually holds the geometry, which
    the CAD export labelled "Object 13". None of the 88 underlying leaf
    references was ever reached through two differently-named wrappers,
    so preferring the wrapper's name is unambiguous here, not a guess.

    The regex match matters, not just "does a 1-child wrapper exist":
    an early version of this function fired for ANY single-child
    wrapper regardless of the leaf's own name, and broke on synthetic
    test fixtures where the leaf already had a perfectly good name of
    its own (e.g. "PartB" wrapped by a "SubGroup") -- overriding an
    already-meaningful name with a less specific one, and even
    overriding the explicit "(senza nome, ...)" placeholder for a truly
    unnamed leaf with an unrelated ancestor's name. Restricting the
    trigger to the exact observed auto-generated pattern fixes both:
    it only ever replaces a name the CAD tool invented, never one a
    human (or bom_display_name's own placeholder) already gave it."""
    if (ref.name and _AUTO_GENERATED_LEAF_NAME.fullmatch(ref.name)
            and parent is not None and parent.name and parent.shape_index is None
            and len(parent.children) == 1):
        return parent.name
    return bom_display_name(ref)


COLLAPSE_SEPARATOR = "§"  # "S" -- essentially never appears in a CAD part
# name, used to scope a leaf's glTF material name to the collapsed
# sub-assembly it was gathered under (balzar/gltf.py). Needed because a
# leaf can carry the CAD export tool's auto-generated placeholder name
# ("Object N", see effective_display_name), and the SAME placeholder can
# recur under a different, unrelated sub-assembly -- verified on a real
# assembly (not a hypothetical): two different reservoir sub-assemblies
# shared several "Object N" leaf names. Without this suffix, resolving
# a collapsed group's `material_names` (below) would highlight parts of
# a sibling group too.


def _collect_leaf_material_names(scene: Scene3D, ref_index: int, group_name: str) -> set[str]:
    """Every distinct glTF material name a leaf reachable under
    `ref_index` will carry once `group_name` is its collapse context
    (balzar/gltf.py suffixes leaf material names the same way) -- used
    to populate a collapsed BomEntry.material_names so it can highlight
    exactly its own descendants, never a sibling group's."""
    names: set[str] = set()

    def walk(idx: int, parent: Reference | None) -> None:
        ref = scene.references[idx]
        if ref.shape_index is not None:
            names.add(f"{effective_display_name(parent, ref)}{COLLAPSE_SEPARATOR}{group_name}")
            return
        for target, _inst_name, _matrix in ref.children:
            walk(target, ref)

    walk(ref_index, None)
    return names


def generate_bom(scene: Scene3D, collapse_names: set[str] | None = None) -> list[BomEntry]:
    """Flat bill of materials: every named leaf part and how many times
    it's actually placed, walking the full DAG with multiplicity (the
    same reachability walk already used for instance_count/mean_vertex_error
    elsewhere in this module) -- NOT the number of Reference3D leaf
    definitions, which would undercount a part reused by a repeated
    sub-assembly (see CLAUDE.md SS9.2: one real geometry was placed 360
    times through nested reuse, but is only ONE Reference3D definition).

    Entries are keyed by (name, shape_index): two differently-named
    references to the same geometry are different BOM lines (a screw and
    a rivet can share a shape and still be different parts); two
    identically-unnamed references to the same geometry collapse into
    one line, since there's nothing else to tell them apart as distinct
    part types. A reference with no name is labelled explicitly rather
    than silently merged into an unrelated bucket.

    `collapse_names`, if given, is a set of Reference3D names -- e.g. an
    alarm table's component column, which may name a whole sub-assembly
    ("HEATER1") rather than one physical part -- that should each become
    a SINGLE BOM row instead of expanding down to their individual leaf
    parts. Only names that are actually non-leaf group references in
    this scene are collapsed; a name that happens to already be an
    ordinary leaf part is left alone (nothing to collapse, it's already
    atomic). Without collapse_names (the default), behaves exactly as
    before -- every leaf part expanded, one row each."""
    counts: dict[tuple[str, int | None], int] = {}
    order: list[tuple[str, int | None]] = []
    materials: dict[tuple[str, int | None], set[str]] = {}
    names_to_collapse = collapse_names or ()

    def walk(ref_index: int, parent: Reference | None) -> None:
        ref = scene.references[ref_index]
        if ref.name in names_to_collapse and ref.shape_index is None:
            key = (ref.name, None)
            if key not in counts:
                counts[key] = 0
                order.append(key)
                materials[key] = set()
            counts[key] += 1
            materials[key] |= _collect_leaf_material_names(scene, ref_index, ref.name)
            return  # the whole group is one BOM row -- do not descend further
        if ref.shape_index is not None:
            name = effective_display_name(parent, ref)
            key = (name, ref.shape_index)
            if key not in counts:
                counts[key] = 0
                order.append(key)
                materials[key] = {name}
            counts[key] += 1
        for target, _inst_name, _matrix in ref.children:
            walk(target, ref)

    walk(scene.root, None)
    return [BomEntry(name=name, shape_index=shape_idx, count=counts[(name, shape_idx)],
                     material_names=sorted(materials[(name, shape_idx)]))
           for name, shape_idx in order]


def _quantized_copy(scene: Scene3D) -> tuple[Scene3D, float]:
    """The scene as it will actually come back out of decode_payload:
    every shape's vertices already rounded through the same int16
    quantize-then-dequantize round trip _serialize/_deserialize use.
    Comparing the self-check against THIS (not the original full-
    precision `scene`) is the same honesty pattern as the 2D encoder's
    self-check against its already-quantized palette indices, not the
    raw source RGB. Also returns the mean per-axis error introduced,
    for honest disclosure in Scene3DEncodeResult."""
    quantized_shapes = []
    total_error = 0.0
    total_axis_samples = 0
    for shape in scene.shapes:
        lo, scale, q = _quantize_positions(shape.vertices)
        dequantized = _dequantize_positions(lo, scale, q)
        for (x, y, z), (qx, qy, qz) in zip(shape.vertices, dequantized):
            total_error += abs(x - qx) + abs(y - qy) + abs(z - qz)
            total_axis_samples += 3
        quantized_shapes.append(Shape(name=shape.name, color=shape.color,
                                      vertices=dequantized, strips=shape.strips))
    mean_error = round(total_error / total_axis_samples, 6) if total_axis_samples else 0.0
    return Scene3D(shapes=quantized_shapes, references=scene.references, root=scene.root), mean_error


def encode_3dxml_file(path: str) -> Scene3DEncodeResult:
    scene = parse_3dxml(path)
    payload = encode_payload(scene)
    quantized_scene, mean_vertex_error = _quantized_copy(scene)

    # self-check: decoding the payload we just produced must reconstruct
    # the already-quantized scene exactly -- never claim a round-trip
    # works without verifying it
    rebuilt = decode_payload(payload)
    if rebuilt != quantized_scene:
        raise RuntimeError("scene3d encoder self-check failed: decoded payload "
                          "does not match the quantized source (internal bug)")

    instance_count = sum(len(r.children) for r in scene.references)
    vertex_count = sum(len(s.vertices) for s in scene.shapes)
    triangle_index_count = sum(len(strip) for s in scene.shapes for strip in s.strips)

    return Scene3DEncodeResult(
        payload=payload,
        shape_count=len(scene.shapes),
        reference_count=len(scene.references),
        instance_count=instance_count,
        vertex_count=vertex_count,
        triangle_index_count=triangle_index_count,
        mean_vertex_error=mean_vertex_error,
        bom=generate_bom(scene),
    )
