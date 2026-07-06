"""Scene3D -> glTF/GLB: a second rendering target for the 3D scene, same
role svg.py plays for the 2D DSL — the compact BZM1 payload (scene3d.py)
stays the source of truth for storage/QR transport, this module only
serves visualization by delegating actual rendering to a mature existing
engine (<model-viewer>, Three.js/WebGL under the hood) instead of writing
a rasterizer of our own.

Important asymmetry, not a bug: glTF's node graph is a TREE (each node
has one parent), while our Reference3D/Instance3D model is a DAG (a
repeated sub-assembly is defined once, instanced by many parents — see
scene3d.py). glTF only offers node-level reuse for MESHES (many nodes
may point at the same mesh index), not for whole sub-trees. So this
exporter duplicates node sub-trees for every instance of a repeated
group, while still pointing every leaf node at the same shared mesh
index for a given shape — geometry data in the exported GLB stays
deduplicated (one copy per unique shape), only the (tiny) JSON node
list is as large as the fully-expanded instance count. The GLB is a
rendering artifact, not a transport format — exactly like PNG vs the 2D
payload: compactness lives in BZM1, not here.

Triangle strips are flattened to plain triangle lists (glTF mode 4) for
maximum viewer compatibility, rather than relying on every consumer
supporting mode 5 (TRIANGLE_STRIP) correctly.

Materials are one PER LEAF INSTANCE, not deduped by colour: this is
what lets a click-to-select UI (model-viewer's public materialFromPoint
API) tell two placements of the same part apart, since each gets its
own Material object even though they render identically by default.
The (tiny) cost is JSON only -- every per-instance mesh still points at
the SAME shared POSITION/indices accessors for its shape, so the
geometry buffer stays exactly as deduplicated as before; only the
meshes[]/materials[] JSON arrays grow from shape_count to instance
count. Every material is exported with alphaMode="BLEND" so a viewer
can dim/hide non-selected instances via alpha, not just recolour them
-- a real isolate, not just a highlight, using only documented
model-viewer Material API (pbrMetallicRoughness.setBaseColorFactor).
"""

from __future__ import annotations

import json
import struct

from .scene3d import Reference, Scene3D, bom_display_name

_IDENTITY_16 = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]

_GLTF_ARRAY_BUFFER = 34962
_GLTF_ELEMENT_ARRAY_BUFFER = 34963
_GLTF_FLOAT = 5126
_GLTF_UNSIGNED_INT = 5125


def _matrix_to_gltf(m: tuple[float, ...]) -> list[float]:
    """RelativeMatrix (9 rotation values row-major + 3 translation) ->
    glTF's column-major 4x4 node matrix. Verified, not just assumed
    (CLAUDE.md SS9.7): algebraically, feeding a known +90deg CCW
    rotation about Z through this transpose maps point (1,0,0) to
    (0,1,0) exactly as expected; visually, a Chromium+Playwright render
    via <model-viewer> of a translated-only pair vs a 90deg-rotated
    instance shows the rotated one with a genuinely different shape
    orientation, not a mirrored or corrupted one."""
    r = m[:9]
    t = m[9:12]
    return [r[0], r[3], r[6], 0.0,
           r[1], r[4], r[7], 0.0,
           r[2], r[5], r[8], 0.0,
           t[0], t[1], t[2], 1.0]


def _strip_to_triangles(strip: list[int]) -> list[int]:
    tris: list[int] = []
    for i in range(len(strip) - 2):
        a, b, c = strip[i], strip[i + 1], strip[i + 2]
        if i % 2 == 0:
            tris.extend((a, b, c))
        else:
            tris.extend((b, a, c))
    return tris


def _build_reference_node(scene: Scene3D, ref_index: int,
                          shape_accessors: list[tuple[int, int]],
                          meshes: list[dict], materials: list[dict],
                          nodes: list[dict]) -> int:
    """Recursively emit the node for `Reference[ref_index]`'s own content
    (mesh if it's a leaf, children if it's a group), wrapping every child
    instance in its own node carrying that instance's transform+name.
    Returns the index of the appended content node.

    A leaf gets its own mesh+material every time this is called (once
    per placement, since this function is deliberately not memoized by
    ref_index -- see the module docstring on the DAG-vs-tree asymmetry)
    -- so two instances of the same repeated part end up as two
    distinct Material objects a click can tell apart, even though both
    reference the SAME position/index accessors underneath."""
    ref: Reference = scene.references[ref_index]
    node: dict = {}
    if ref.name:
        node["name"] = ref.name
    if ref.shape_index is not None:
        shape = scene.shapes[ref.shape_index]
        pos_accessor, idx_accessor = shape_accessors[ref.shape_index]
        display_name = bom_display_name(ref)
        r, g, b = shape.color
        materials.append({
            "name": display_name,
            "pbrMetallicRoughness": {"baseColorFactor": [r / 255.0, g / 255.0, b / 255.0, 1.0]},
            "alphaMode": "BLEND",
        })
        mesh = {"name": display_name,
               "primitives": [{"attributes": {"POSITION": pos_accessor},
                              "indices": idx_accessor,
                              "material": len(materials) - 1,
                              "mode": 4}]}
        meshes.append(mesh)
        node["mesh"] = len(meshes) - 1
    if ref.children:
        child_indices = []
        for target, inst_name, matrix in ref.children:
            content_idx = _build_reference_node(scene, target, shape_accessors,
                                                 meshes, materials, nodes)
            instance_node: dict = {"children": [content_idx]}
            gm = _matrix_to_gltf(matrix)
            if gm != _IDENTITY_16:
                instance_node["matrix"] = gm
            if inst_name:
                instance_node["name"] = inst_name
            nodes.append(instance_node)
            child_indices.append(len(nodes) - 1)
        node["children"] = child_indices
    nodes.append(node)
    return len(nodes) - 1


def scene3d_to_glb(scene: Scene3D) -> bytes:
    buffer = bytearray()
    accessors: list[dict] = []
    buffer_views: list[dict] = []
    meshes: list[dict] = []
    materials: list[dict] = []

    def add_buffer_view(data: bytes, target: int) -> int:
        while len(buffer) % 4 != 0:
            buffer.append(0)
        offset = len(buffer)
        buffer.extend(data)
        buffer_views.append({"buffer": 0, "byteOffset": offset,
                             "byteLength": len(data), "target": target})
        return len(buffer_views) - 1

    # geometry only, one accessor pair per unique SHAPE -- meshes and
    # materials are built per-instance below (see _build_reference_node)
    shape_accessors: list[tuple[int, int]] = []
    for shape in scene.shapes:
        pos_bytes = b"".join(struct.pack("<fff", *v) for v in shape.vertices)
        pos_view = add_buffer_view(pos_bytes, _GLTF_ARRAY_BUFFER)
        if shape.vertices:
            xs = [v[0] for v in shape.vertices]
            ys = [v[1] for v in shape.vertices]
            zs = [v[2] for v in shape.vertices]
            bbox_min, bbox_max = [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]
        else:
            bbox_min = bbox_max = [0.0, 0.0, 0.0]
        accessors.append({"bufferView": pos_view, "componentType": _GLTF_FLOAT,
                          "count": len(shape.vertices), "type": "VEC3",
                          "min": bbox_min, "max": bbox_max})
        pos_accessor = len(accessors) - 1

        tri_indices: list[int] = []
        for strip in shape.strips:
            tri_indices.extend(_strip_to_triangles(strip))
        idx_bytes = b"".join(struct.pack("<I", i) for i in tri_indices)
        idx_view = add_buffer_view(idx_bytes, _GLTF_ELEMENT_ARRAY_BUFFER)
        accessors.append({"bufferView": idx_view, "componentType": _GLTF_UNSIGNED_INT,
                          "count": len(tri_indices), "type": "SCALAR"})
        idx_accessor = len(accessors) - 1

        shape_accessors.append((pos_accessor, idx_accessor))

    nodes: list[dict] = []
    root_node_idx = _build_reference_node(scene, scene.root, shape_accessors,
                                          meshes, materials, nodes)

    gltf_json = {
        "asset": {"version": "2.0", "generator": "balzar scene3d"},
        "scene": 0,
        "scenes": [{"nodes": [root_node_idx]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(buffer)}],
    }
    return _pack_glb(gltf_json, bytes(buffer))


def _pack_glb(gltf_json: dict, bin_chunk: bytes) -> bytes:
    json_bytes = json.dumps(gltf_json, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)  # pad with spaces per spec

    bin_bytes = bin_chunk + b"\x00" * ((4 - len(bin_chunk) % 4) % 4)  # pad with zeros

    json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk_full = struct.pack("<II", len(bin_bytes), 0x004E4942) + bin_bytes

    total_length = 12 + len(json_chunk) + len(bin_chunk_full)
    header = struct.pack("<4sII", b"glTF", 2, total_length)
    return header + json_chunk + bin_chunk_full
