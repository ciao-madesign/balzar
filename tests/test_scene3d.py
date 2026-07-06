"""3D parametric assemblies (scene3d.py): 3DXML ingestion, the BZM1
binary payload round-trip/self-check, and the glTF/GLB export (gltf.py).

Uses a small synthetic 3DXML fixture built in-memory (not a real CAD
file — those are large and proprietary) that mirrors the real structure
found in a real-world assembly during scoping (CLAUDE.md SS9): a root
group containing two instances of one leaf part (instancing a single
shape) plus one instance of a nested sub-group containing a second leaf
part (a DAG, not a flat list — reuse across nesting)."""

import io
import os
import struct
import tempfile
import unittest
import zipfile

from balzar.gltf import scene3d_to_glb
from balzar.scene3d import (Scene3DError, _decode_matrix, _encode_matrix,
                            _quantized_copy, decode_payload, encode_3dxml_file,
                            encode_payload, parse_3dxml)

_MANIFEST = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Manifest xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:noNamespaceSchemaLocation="Manifest.xsd">'
            '<Root>main.3dxml</Root></Manifest>')

_MAIN_3DXML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Model_3dxml xmlns="http://www.3ds.com/xsd/3DXML" '
              'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
              '<ProductStructure root="1">'
              '<Reference3D id="1" name="Root"/>'
              '<Instance3D id="2" name="inst_A_1">'
              '<IsAggregatedBy>1</IsAggregatedBy><IsInstanceOf>3</IsInstanceOf>'
              '<RelativeMatrix>1 0 0 0 1 0 0 0 1 0 0 0</RelativeMatrix></Instance3D>'
              '<Reference3D id="3" name="PartA"/>'
              '<ReferenceRep id="4" name="PartA_Rep" associatedFile="urn:3DXML:shapeA.3DRep"/>'
              '<InstanceRep id="5" name="PartA_InstRep">'
              '<IsAggregatedBy>3</IsAggregatedBy><IsInstanceOf>4</IsInstanceOf></InstanceRep>'
              '<Instance3D id="6" name="inst_A_2">'
              '<IsAggregatedBy>1</IsAggregatedBy><IsInstanceOf>3</IsInstanceOf>'
              '<RelativeMatrix>1 0 0 0 1 0 0 0 1 10 0 0</RelativeMatrix></Instance3D>'
              '<Instance3D id="7" name="inst_group">'
              '<IsAggregatedBy>1</IsAggregatedBy><IsInstanceOf>8</IsInstanceOf>'
              '<RelativeMatrix>1 0 0 0 1 0 0 0 1 0 10 0</RelativeMatrix></Instance3D>'
              '<Reference3D id="8" name="SubGroup"/>'
              '<Instance3D id="9" name="inst_B_1">'
              '<IsAggregatedBy>8</IsAggregatedBy><IsInstanceOf>10</IsInstanceOf>'
              '<RelativeMatrix>0 -1 0 1 0 0 0 0 1 0 0 5</RelativeMatrix></Instance3D>'
              '<Reference3D id="10" name="PartB"/>'
              '<ReferenceRep id="11" name="PartB_Rep" associatedFile="urn:3DXML:shapeB.3DRep"/>'
              '<InstanceRep id="12" name="PartB_InstRep">'
              '<IsAggregatedBy>10</IsAggregatedBy><IsInstanceOf>11</IsInstanceOf></InstanceRep>'
              '</ProductStructure></Model_3dxml>')


def _shape_rep(color=(255, 0, 0)):
    r, g, b = (c / 255.0 for c in color)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<XMLRepresentation xmlns="http://www.3ds.com/xsd/3DXML" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
           '<Root xsi:type="BagRepType" id="1"><Rep xsi:type="PolygonalRepType" id="2">'
           '<Faces><Face strips="0 1 2">'
           f'<SurfaceAttributes><Color xsi:type="RGBAColorType" '
           f'red="{r:.6f}" green="{g:.6f}" blue="{b:.6f}" alpha="1.000000"/>'
           '</SurfaceAttributes></Face></Faces>'
           '<VertexBuffer><Positions>0.0 0.0 0.0 1.0 0.0 0.0 0.0 1.0 0.0</Positions>'
           '<Normals>0 0 1 0 0 1 0 0 1</Normals></VertexBuffer></Rep></Root>'
           '</XMLRepresentation>')


def _write_fixture_3dxml(path: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Manifest.xml", _MANIFEST)
        zf.writestr("main.3dxml", _MAIN_3DXML)
        zf.writestr("shapeA.3DRep", _shape_rep((255, 0, 0)))
        zf.writestr("shapeB.3DRep", _shape_rep((0, 255, 0)))


class TestParse3dxml(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "fixture.3dxml")
        _write_fixture_3dxml(self.path)

    def test_two_unique_shapes_three_instances(self):
        scene = parse_3dxml(self.path)
        self.assertEqual(len(scene.shapes), 2)
        # references: Root, PartA, SubGroup, PartB = 4 (each parsed once,
        # PartA reused by two Instance3D edges -- the DAG reuse under test)
        self.assertEqual(len(scene.references), 4)
        root = scene.references[scene.root]
        self.assertEqual(root.name, "Root")
        # 3 instance EDGES (inst_A_1, inst_A_2, inst_group) -- edges are not
        # deduped, only the Reference they point at is
        self.assertEqual(len(root.children), 3)

    def test_partA_referenced_not_duplicated(self):
        scene = parse_3dxml(self.path)
        # inst_A_1 and inst_A_2 are two distinct instance edges, but must
        # point at the very same Reference (and therefore the same shape)
        root = scene.references[scene.root]
        targets = [child[0] for child in root.children]
        self.assertEqual(len(targets), 3)
        self.assertEqual(len(set(targets)), 2)  # PartA appears once, SubGroup once
        part_a_ref_indices = [t for t in targets if scene.references[t].shape_index is not None]
        self.assertEqual(len(part_a_ref_indices), 2)
        shape_indices = {scene.references[t].shape_index for t in part_a_ref_indices}
        self.assertEqual(len(shape_indices), 1)  # same underlying shape, not duplicated

    def test_names_preserved(self):
        scene = parse_3dxml(self.path)
        names = {ref.name for ref in scene.references}
        self.assertEqual(names, {"Root", "PartA", "SubGroup", "PartB"})

    def test_missing_manifest_rejected(self):
        bad_path = os.path.join(self.tmpdir.name, "bad.3dxml")
        with zipfile.ZipFile(bad_path, "w") as zf:
            zf.writestr("not_a_manifest.txt", "hello")
        with self.assertRaises(Scene3DError):
            parse_3dxml(bad_path)

    def test_not_a_zip_rejected(self):
        bad_path = os.path.join(self.tmpdir.name, "bad2.3dxml")
        with open(bad_path, "wb") as fh:
            fh.write(b"not a zip file at all")
        with self.assertRaises(Scene3DError):
            parse_3dxml(bad_path)


class TestPayloadRoundtrip(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "fixture.3dxml")
        _write_fixture_3dxml(self.path)

    def test_encode_decode_roundtrip_exact(self):
        scene = parse_3dxml(self.path)
        payload = encode_payload(scene)
        rebuilt = decode_payload(payload)
        self.assertEqual(rebuilt, scene)

    def test_encode_3dxml_file_self_check_passes(self):
        result = encode_3dxml_file(self.path)
        self.assertEqual(result.shape_count, 2)
        self.assertEqual(result.reference_count, 4)
        self.assertEqual(result.instance_count, 4)  # inst_A_1, inst_A_2, inst_group, inst_B_1
        self.assertGreater(len(result.payload), 0)
        # this fixture's vertices all sit exactly at their shape's bbox
        # corners, so int16 quantization happens to be lossless here
        self.assertEqual(result.mean_vertex_error, 0.0)

    def test_corrupt_payload_detected(self):
        result = encode_3dxml_file(self.path)
        corrupt = result.payload[:-1] + bytes([result.payload[-1] ^ 0xFF])
        with self.assertRaises(Scene3DError):
            decode_payload(corrupt)

    def test_bad_magic_rejected(self):
        with self.assertRaises(Scene3DError):
            decode_payload(b"NOPE" + b"\x00" * 20)

    def test_truncated_payload_rejected(self):
        with self.assertRaises(Scene3DError):
            decode_payload(b"BZM1\x00\x00")

    def test_bom_counts_leaf_placements_by_name(self):
        result = encode_3dxml_file(self.path)
        by_name = {e.name: e.count for e in result.bom}
        # PartA is placed twice (inst_A_1, inst_A_2), PartB once (inst_B_1,
        # nested inside SubGroup) -- SubGroup itself is a group, not a leaf,
        # and must not appear in the BOM
        self.assertEqual(by_name, {"PartA": 2, "PartB": 1})
        self.assertNotIn("SubGroup", by_name)
        self.assertNotIn("Root", by_name)


class TestBom(unittest.TestCase):
    """generate_bom on hand-built scenes, isolated from 3DXML parsing —
    in particular the case parse_3dxml's own fixture doesn't exercise:
    a repeated sub-assembly multiplying the count of the parts nested
    inside it (CLAUDE.md SS9.2's real example: one geometry placed 360
    times through nested reuse, backed by a single Reference3D)."""

    def test_repeated_subassembly_multiplies_nested_part_count(self):
        from balzar.scene3d import Reference, Scene3D, Shape, generate_bom

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        screw = Shape(name="Screw", color=(200, 200, 200),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        # SubAssembly (ref 1) places the screw (leaf ref 0) twice
        leaf_screw = Reference(name="ScrewLeaf", shape_index=0, children=[])
        sub_assembly = Reference(name="SubAssembly", shape_index=None, children=[
            (2, "screw_1", identity), (2, "screw_2", identity),
        ])
        # Root places SubAssembly three times -> 2 screws x 3 = 6 total
        root = Reference(name="Root", shape_index=None, children=[
            (1, "sub_1", identity), (1, "sub_2", identity), (1, "sub_3", identity),
        ])
        scene = Scene3D(shapes=[screw], references=[root, sub_assembly, leaf_screw], root=0)

        bom = generate_bom(scene)
        self.assertEqual(len(bom), 1)
        self.assertEqual(bom[0].name, "ScrewLeaf")
        self.assertEqual(bom[0].count, 6)

    def test_unnamed_leaf_gets_explicit_placeholder_label(self):
        from balzar.scene3d import Reference, Scene3D, Shape, generate_bom

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        shape = Shape(name=None, color=(1, 2, 3),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        leaf = Reference(name=None, shape_index=0, children=[])
        root = Reference(name="Root", shape_index=None, children=[(1, None, identity)])
        scene = Scene3D(shapes=[shape], references=[root, leaf], root=0)

        bom = generate_bom(scene)
        self.assertEqual(len(bom), 1)
        self.assertIn("senza nome", bom[0].name)
        self.assertEqual(bom[0].count, 1)


class TestQuantizationAndCompactTransforms(unittest.TestCase):
    """The three size optimizations applied on top of the first working
    version (CLAUDE.md SS9.2/SS9.7): int16 vertex quantization, uint16
    strip indices, and the compact axis-aligned rotation code."""

    def test_interior_vertex_gets_a_small_disclosed_error(self):
        from balzar.scene3d import Reference, Scene3D, Shape

        # an interior point (not at the shape's bbox corners) so
        # quantization is genuinely lossy here, unlike the corner-only
        # fixture used elsewhere in this file
        shape = Shape(name="Tri", color=(10, 20, 30),
                     vertices=[(0.0, 0.0, 0.0), (100.0, 0.0, 0.0),
                              (0.0, 100.0, 0.0), (33.333333, 66.666667, 0.0)],
                     strips=[[0, 1, 2, 3]])
        ref = Reference(name="Leaf", shape_index=0, children=[])
        scene = Scene3D(shapes=[shape], references=[ref], root=0)

        quantized, mean_error = _quantized_copy(scene)
        self.assertGreater(mean_error, 0.0)
        self.assertLess(mean_error, 0.01)  # int16 over a 100-unit range: tiny
        self.assertNotEqual(quantized.shapes[0].vertices, shape.vertices)

    def test_axis_aligned_rotation_survives_compact_roundtrip(self):
        identity_ish = (0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 5.0, -5.0, 12.0)
        encoded = _encode_matrix(identity_ish)
        self.assertEqual(encoded[0], 0)  # kind=0: compact trit code path
        decoded, _ = _decode_matrix(encoded, 0)
        self.assertEqual(decoded, identity_ish)

    def test_arbitrary_rotation_falls_back_to_full_floats(self):
        import math
        c, s = math.cos(0.3), math.sin(0.3)
        arbitrary = (c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0)
        encoded = _encode_matrix(arbitrary)
        self.assertEqual(encoded[0], 1)  # kind=1: full-float fallback path
        decoded, _ = _decode_matrix(encoded, 0)
        for a, b in zip(decoded, arbitrary):
            self.assertAlmostEqual(a, b, places=5)

    def test_shape_over_65535_vertices_rejected_not_truncated(self):
        from balzar.scene3d import Reference, Scene3D, Shape

        shape = Shape(name="TooBig", color=(1, 2, 3),
                     vertices=[(float(i), 0.0, 0.0) for i in range(65536)],
                     strips=[])
        ref = Reference(name="Leaf", shape_index=0, children=[])
        scene = Scene3D(shapes=[shape], references=[ref], root=0)
        with self.assertRaises(Scene3DError):
            encode_payload(scene)


class TestGltfExport(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "fixture.3dxml")
        _write_fixture_3dxml(self.path)

    def test_glb_has_valid_header(self):
        scene = parse_3dxml(self.path)
        glb = scene3d_to_glb(scene)
        magic, version, length = struct.unpack_from("<4sII", glb, 0)
        self.assertEqual(magic, b"glTF")
        self.assertEqual(version, 2)
        self.assertEqual(length, len(glb))

    def test_glb_json_chunk_parses_and_references_two_meshes(self):
        import json
        scene = parse_3dxml(self.path)
        glb = scene3d_to_glb(scene)
        json_len, json_type = struct.unpack_from("<II", glb, 12)
        self.assertEqual(json_type, 0x4E4F534A)
        gltf = json.loads(glb[20:20 + json_len].decode("utf-8"))
        # one mesh (and one material) per LEAF INSTANCE, not per unique
        # shape: 3 placements (2x PartA, 1x PartB) even though there are
        # only 2 unique shapes underneath -- this is what lets a click on
        # one specific PartA instance be told apart from its sibling via
        # model-viewer's materialFromPoint (see gltf.py's module docstring)
        self.assertEqual(len(gltf["meshes"]), 3)
        self.assertEqual(gltf["asset"]["version"], "2.0")
        mesh_nodes = [n for n in gltf["nodes"] if "mesh" in n]
        self.assertEqual(len(mesh_nodes), 3)

    def test_each_instance_gets_its_own_named_material_with_alpha_blend(self):
        import json
        scene = parse_3dxml(self.path)
        glb = scene3d_to_glb(scene)
        json_len, _ = struct.unpack_from("<II", glb, 12)
        gltf = json.loads(glb[20:20 + json_len].decode("utf-8"))

        self.assertEqual(len(gltf["materials"]), 3)
        names = sorted(m["name"] for m in gltf["materials"])
        # 2x "inst_A_1"/"inst_A_2"-style PartA placements share the same
        # BOM display name (the underlying Reference3D is named "PartA"
        # for both), the PartB placement has its own -- three materials,
        # two distinct names, matching bom_display_name's own grouping
        self.assertEqual(len(names), 3)
        for m in gltf["materials"]:
            self.assertEqual(m["alphaMode"], "BLEND")
            self.assertIn("name", m)

    def test_instance_meshes_share_the_same_geometry_accessors(self):
        import json
        scene = parse_3dxml(self.path)
        glb = scene3d_to_glb(scene)
        json_len, _ = struct.unpack_from("<II", glb, 12)
        gltf = json.loads(glb[20:20 + json_len].decode("utf-8"))

        # the two PartA instances must reuse the SAME position/index
        # accessors (geometry dedup preserved) even though each has its
        # own mesh+material entry
        partA_meshes = [m for m in gltf["meshes"] if m["name"] == "PartA"]
        self.assertEqual(len(partA_meshes), 2)
        accessor_pairs = {
            (m["primitives"][0]["attributes"]["POSITION"], m["primitives"][0]["indices"])
            for m in partA_meshes
        }
        self.assertEqual(len(accessor_pairs), 1)
        # but distinct materials, so a click can select just one
        material_indices = {m["primitives"][0]["material"] for m in partA_meshes}
        self.assertEqual(len(material_indices), 2)


if __name__ == "__main__":
    unittest.main()
