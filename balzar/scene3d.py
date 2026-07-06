"""3D parametric assemblies: 3DXML ingestion -> compact payload (BZM1).

First working version — scope and every measurement behind the design
choices here are in CLAUDE.md SS9. Correctness (round-trip self-check)
comes first; size optimizations already prototyped and measured there
(per-shape int16 vertex quantization, compact axis-aligned rotation
codes) are a deliberate follow-up, not included yet — same "working
baseline first, then optimize with measurement" pattern already used
for the PNG adaptive filters and the median-cut quantizer.

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
"""

from __future__ import annotations

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
class Scene3DEncodeResult:
    payload: bytes
    shape_count: int
    reference_count: int
    instance_count: int
    vertex_count: int
    triangle_index_count: int


def _f32(v: float) -> float:
    """Round-trip through float32 immediately at parse time, so the
    in-memory Scene3D already IS the precision that will be stored —
    the declared precision reduction from the source's text (double-
    precision-looking decimals), same honesty pattern as mean_color_error
    elsewhere: the self-check below verifies exact round-trip of the
    already-quantized value, not preservation of the source's full
    double precision."""
    return struct.unpack("<f", struct.pack("<f", v))[0]


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
        out += struct.pack("<H", name_idx(shape.name))
        out += struct.pack("<BBB", *shape.color)
        out += struct.pack("<I", len(shape.vertices))
        for x, y, z in shape.vertices:
            out += struct.pack("<fff", x, y, z)
        out += struct.pack("<H", len(shape.strips))
        for strip in shape.strips:
            out += struct.pack("<H", len(strip))
            for idx in strip:
                out += struct.pack("<I", idx)

    out += struct.pack("<I", len(scene.references))
    for ref in scene.references:
        out += struct.pack("<H", name_idx(ref.name))
        out += struct.pack("<BH", 1 if ref.shape_index is not None else 0,
                           ref.shape_index if ref.shape_index is not None else 0)
        out += struct.pack("<I", len(ref.children))
        for target, inst_name, matrix in ref.children:
            out += struct.pack("<I", target)
            out += struct.pack("<H", name_idx(inst_name))
            out += struct.pack("<12f", *matrix)

    out += struct.pack("<I", scene.root)
    return bytes(out)


def _deserialize(data: bytes) -> Scene3D:
    names, off = _unpack_str_table(data, 0)

    def name_at(idx: int) -> str | None:
        return None if idx == _NO_NAME else names[idx]

    (n_shapes,) = struct.unpack_from("<H", data, off); off += 2
    shapes = []
    for _ in range(n_shapes):
        (name_i,) = struct.unpack_from("<H", data, off); off += 2
        color = struct.unpack_from("<BBB", data, off); off += 3
        (n_verts,) = struct.unpack_from("<I", data, off); off += 4
        vertices = []
        for _ in range(n_verts):
            xyz = struct.unpack_from("<fff", data, off); off += 12
            vertices.append(xyz)
        (n_strips,) = struct.unpack_from("<H", data, off); off += 2
        strips = []
        for _ in range(n_strips):
            (slen,) = struct.unpack_from("<H", data, off); off += 2
            idxs = list(struct.unpack_from(f"<{slen}I", data, off)); off += 4 * slen
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
            matrix = struct.unpack_from("<12f", data, off); off += 48
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
    header = MAGIC + struct.pack("<HII", 1, len(body), zlib.crc32(body))
    return header + zlib.compress(body, 9)


def decode_payload(data: bytes) -> Scene3D:
    if len(data) < 14 or data[:4] != MAGIC:
        raise Scene3DError("non e' un payload balzar 3D (magic BZM1 non valido)")
    version, length, crc = struct.unpack_from("<HII", data, 4)
    if version != 1:
        raise Scene3DError(f"versione BZM1 non supportata: {version}")
    try:
        body = zlib.decompress(data[14:])
    except zlib.error as exc:
        raise Scene3DError(f"corpo del payload corrotto: {exc}") from None
    if len(body) != length or zlib.crc32(body) != crc:
        raise Scene3DError("controllo di integrita' del payload fallito (lunghezza/CRC)")
    return _deserialize(body)


# ------------------------------------------------------------- top level

def encode_3dxml_file(path: str) -> Scene3DEncodeResult:
    scene = parse_3dxml(path)
    payload = encode_payload(scene)

    # self-check: decoding the payload we just produced must reconstruct
    # an identical scene (at the float32 precision already applied during
    # parsing) -- never claim a round-trip works without verifying it
    rebuilt = decode_payload(payload)
    if rebuilt != scene:
        raise RuntimeError("scene3d encoder self-check failed: decoded payload "
                          "does not match the parsed source (internal bug)")

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
    )
