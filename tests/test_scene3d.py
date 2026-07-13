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
                            _IDENTITY_MATRIX, _quantized_copy, decode_payload,
                            encode_3dxml_file, encode_payload, parse_3dxml)

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

    def test_auto_generated_object_n_name_prefers_wrapper_name(self):
        # the exact real-world shape confirmed in CLAUDE.md SS9.12: a
        # "product" reference with a real name (e.g. a CAD export's
        # "VASCA_ACCUMULO_SUB009") wraps -- via a single otherwise-
        # unnamed Instance3D -- the reference that actually carries the
        # geometry, auto-labelled "Object 13" by the export tool
        from balzar.scene3d import Reference, Scene3D, Shape, generate_bom

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        shape = Shape(name=None, color=(1, 2, 3),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        leaf = Reference(name="Object 13", shape_index=0, children=[])
        wrapper = Reference(name="VASCA_ACCUMULO_SUB009", shape_index=None,
                            children=[(2, None, identity)])
        root = Reference(name="Root", shape_index=None, children=[(1, None, identity)])
        scene = Scene3D(shapes=[shape], references=[root, wrapper, leaf], root=0)

        bom = generate_bom(scene)
        self.assertEqual(len(bom), 1)
        self.assertEqual(bom[0].name, "VASCA_ACCUMULO_SUB009")

    def test_already_meaningful_leaf_name_is_not_overridden_by_wrapper(self):
        # the same single-child-wrapper shape as above, but the leaf's
        # own name is NOT the auto-generated "Object N" pattern -- an
        # earlier version of this heuristic fired on ANY single-child
        # wrapper and incorrectly replaced an already-good name
        from balzar.scene3d import Reference, Scene3D, Shape, generate_bom

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        shape = Shape(name=None, color=(1, 2, 3),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        leaf = Reference(name="PartB", shape_index=0, children=[])
        wrapper = Reference(name="SubGroup", shape_index=None, children=[(2, None, identity)])
        root = Reference(name="Root", shape_index=None, children=[(1, None, identity)])
        scene = Scene3D(shapes=[shape], references=[root, wrapper, leaf], root=0)

        bom = generate_bom(scene)
        self.assertEqual(len(bom), 1)
        self.assertEqual(bom[0].name, "PartB")

    def test_ordinary_row_material_names_is_just_its_own_name(self):
        # no collapse_names given: material_names is a single-item list
        # equal to the row's own name -- unchanged from before this field
        # existed, verified explicitly since it's the fallback everyone
        # else's highlighting logic depends on
        from balzar.scene3d import Reference, Scene3D, Shape, generate_bom

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        shape = Shape(name="S", color=(1, 2, 3),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        leaf = Reference(name="PartB", shape_index=0, children=[])
        root = Reference(name="Root", shape_index=None, children=[(1, None, identity)])
        scene = Scene3D(shapes=[shape], references=[root, leaf], root=0)

        bom = generate_bom(scene)
        self.assertEqual(bom[0].material_names, ["PartB"])
        self.assertEqual(bom[0].shape_index, 0)  # shape_index stays set for an ordinary leaf


class TestBomCollapse(unittest.TestCase):
    """generate_bom's collapse_names: an alarm table can name a whole
    sub-assembly ("HEATER1") rather than one physical part -- these
    tests are built directly from the real bug found analyzing an
    uploaded assembly (CLAUDE.md SS9.19-adjacent session notes): several
    different sub-assemblies shared leaf parts with the CAD tool's
    auto-generated placeholder name ("Object N"), so naive name-based
    highlighting of one collapsed group would have lit up another
    group's parts too."""

    def _two_leaf_group_scene(self):
        from balzar.scene3d import Reference, Scene3D, Shape

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        shape = Shape(name=None, color=(10, 20, 30),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        leaf_a = Reference(name="Screw", shape_index=0, children=[])
        leaf_b = Reference(name="Washer", shape_index=0, children=[])
        group = Reference(name="HEATER1", shape_index=None, children=[
            (2, None, identity), (3, None, identity),
        ])
        root = Reference(name="Root", shape_index=None, children=[(1, None, identity)])
        return Scene3D(shapes=[shape], references=[root, group, leaf_a, leaf_b], root=0)

    def test_group_collapses_to_one_row_with_all_leaf_material_names(self):
        from balzar.scene3d import generate_bom

        scene = self._two_leaf_group_scene()
        bom = generate_bom(scene, collapse_names={"HEATER1"})
        self.assertEqual(len(bom), 1)
        self.assertEqual(bom[0].name, "HEATER1")
        self.assertEqual(bom[0].count, 1)
        self.assertIsNone(bom[0].shape_index)
        self.assertEqual(set(bom[0].material_names),
                         {"Screw§HEATER1", "Washer§HEATER1"})

    def test_collapse_name_matching_an_ordinary_leaf_is_left_alone(self):
        # "Screw" is a real leaf part, not a group -- collapsing it makes
        # no sense (nothing to collapse), so it must be untouched
        from balzar.scene3d import generate_bom

        scene = self._two_leaf_group_scene()
        bom = generate_bom(scene, collapse_names={"Screw"})
        names = {e.name for e in bom}
        self.assertIn("Screw", names)
        self.assertIn("Washer", names)
        screw = next(e for e in bom if e.name == "Screw")
        self.assertEqual(screw.material_names, ["Screw"])  # unsuffixed -- not collapsed

    def test_no_collapse_names_expands_every_leaf_as_before(self):
        from balzar.scene3d import generate_bom

        scene = self._two_leaf_group_scene()
        bom = generate_bom(scene)
        self.assertEqual({e.name for e in bom}, {"Screw", "Washer"})

    def test_repeated_group_placement_multiplies_count_shares_material_names(self):
        # the same collapsed group placed twice (mirrors a repeated
        # sub-assembly, already covered for leaves in TestBom) -- the
        # BOM row's count reflects both placements, and (by design, same
        # philosophy as an ordinary leaf row highlighting every instance
        # of its type) both placements share the same material names
        from balzar.scene3d import Reference, Scene3D, Shape, generate_bom

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        shape = Shape(name=None, color=(1, 2, 3),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        # a real (not auto-generated "Object N") leaf name, so
        # effective_display_name's single-child-wrapper preference does
        # not kick in and mask what this test is actually checking
        leaf = Reference(name="Bolt-01", shape_index=0, children=[])
        group = Reference(name="POMPA1", shape_index=None, children=[(2, None, identity)])
        root = Reference(name="Root", shape_index=None, children=[
            (1, "inst_1", identity), (1, "inst_2", identity),
        ])
        scene = Scene3D(shapes=[shape], references=[root, group, leaf], root=0)

        bom = generate_bom(scene, collapse_names={"POMPA1"})
        self.assertEqual(len(bom), 1)
        self.assertEqual(bom[0].count, 2)
        self.assertEqual(bom[0].material_names, ["Bolt-01§POMPA1"])

    def test_two_sibling_groups_sharing_ambiguous_leaf_name_do_not_cross_contaminate(self):
        # THE real bug, reproduced directly: two different sub-assemblies
        # each contain a leaf carrying the exact same auto-generated
        # placeholder name ("Object 1") -- verified on a real uploaded
        # assembly where two different reservoir sub-assemblies shared
        # several such names. Highlighting RESERVOIR1 must never light
        # up a leaf that is actually inside RESERVOIR2. Each group also
        # has a second, differently-named child so effective_display_name's
        # single-child-wrapper preference (SS9.12) does NOT kick in and
        # mask the "Object 1" name this test is specifically about.
        from balzar.scene3d import Reference, Scene3D, Shape, generate_bom

        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        shape = Shape(name=None, color=(1, 2, 3),
                     vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        # references: 0 root, 1 group_a, 2 group_b, 3/4 group_a's children,
        # 5/6 group_b's children
        leaf_ambiguous_a = Reference(name="Object 1", shape_index=0, children=[])
        leaf_other_a = Reference(name="BracketA", shape_index=0, children=[])
        leaf_ambiguous_b = Reference(name="Object 1", shape_index=0, children=[])
        leaf_other_b = Reference(name="BracketB", shape_index=0, children=[])
        group_a = Reference(name="RESERVOIR1", shape_index=None,
                            children=[(3, None, identity), (4, None, identity)])
        group_b = Reference(name="RESERVOIR2", shape_index=None,
                            children=[(5, None, identity), (6, None, identity)])
        root = Reference(name="Root", shape_index=None, children=[
            (1, None, identity), (2, None, identity),
        ])
        scene = Scene3D(shapes=[shape], references=[
            root, group_a, group_b, leaf_ambiguous_a, leaf_other_a,
            leaf_ambiguous_b, leaf_other_b,
        ], root=0)

        bom = generate_bom(scene, collapse_names={"RESERVOIR1", "RESERVOIR2"})
        by_name = {e.name: e for e in bom}
        r1_materials = set(by_name["RESERVOIR1"].material_names)
        r2_materials = set(by_name["RESERVOIR2"].material_names)
        self.assertEqual(r1_materials & r2_materials, set())
        self.assertEqual(r1_materials, {"Object 1§RESERVOIR1", "BracketA§RESERVOIR1"})
        self.assertEqual(r2_materials, {"Object 1§RESERVOIR2", "BracketB§RESERVOIR2"})

    def test_glb_export_suffixes_leaf_materials_matching_bom_material_names(self):
        # generate_bom and scene3d_to_glb must agree on the exact naming
        # convention -- verified by actually decoding the exported GLB's
        # materials, not just trusting both implementations independently
        import json

        from balzar.scene3d import generate_bom

        scene = self._two_leaf_group_scene()
        collapse = {"HEATER1"}
        bom = generate_bom(scene, collapse)
        glb = scene3d_to_glb(scene, collapse_names=collapse)
        json_len, _ = struct.unpack_from("<II", glb, 12)
        gltf = json.loads(glb[20:20 + json_len].decode("utf-8"))
        glb_material_names = {m["name"] for m in gltf["materials"]}

        heater_row = next(e for e in bom if e.name == "HEATER1")
        self.assertTrue(set(heater_row.material_names) <= glb_material_names)


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

    def test_shape_over_65535_vertices_round_trips_with_wide_indices(self):
        # a real assembly (CLAUDE.md SS9.30) had a single tessellated
        # surface with 290,192 vertices/80,535 strips -- over BOTH the
        # uint16 index range and the original uint16 strip-count field.
        # This used to raise; now it round-trips via uint32 strip
        # indices for just this shape (ordinary shapes stay uint16).
        from balzar.scene3d import Reference, Scene3D, Shape

        n = 70000  # over 65535, forces the wide-index path
        vertices = [(float(i % 1000), float(i // 1000), 0.0) for i in range(n)]
        # a strip that references a vertex index only reachable with a
        # wide (uint32) index -- the real bug: <H silently can't hold 69999
        strips = [[0, 1, 2], [n - 3, n - 2, n - 1]]
        shape = Shape(name="TooBigForUint16", color=(4, 5, 6), vertices=vertices, strips=strips)
        ref = Reference(name="Leaf", shape_index=0, children=[])
        scene = Scene3D(shapes=[shape], references=[ref], root=0)

        payload = encode_payload(scene)
        rebuilt = decode_payload(payload)
        magic, version = struct.unpack_from("<4sH", payload, 0)
        self.assertEqual(version, 2)
        self.assertEqual(len(rebuilt.shapes[0].vertices), n)
        self.assertEqual(rebuilt.shapes[0].strips, strips)

    def test_shape_over_65535_strips_round_trips(self):
        # the second real bug on the same assembly: n_strips itself (not
        # just index values) overflowed the old uint16 count field even
        # for shapes whose own vertex count fits in uint16.
        from balzar.scene3d import Reference, Scene3D, Shape

        n_strips = 70000
        vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        strips = [[0, 1, 2] for _ in range(n_strips)]
        shape = Shape(name="ManyStrips", color=(7, 8, 9), vertices=vertices, strips=strips)
        ref = Reference(name="Leaf", shape_index=0, children=[])
        scene = Scene3D(shapes=[shape], references=[ref], root=0)

        payload = encode_payload(scene)
        rebuilt = decode_payload(payload)
        self.assertEqual(len(rebuilt.shapes[0].strips), n_strips)

    def test_version_1_payload_without_a_wide_shape_still_decodes(self):
        # a version-1 payload (pre-SS9.30) is a real thing that could
        # still be sitting in a user's local library (balzar/library.py)
        # across a balzar upgrade -- by construction it never held a
        # shape over the old limits (encode_payload used to raise
        # instead), so the OLD fixed uint16 n_strips/index layout must
        # still decode correctly. _serialize itself always writes the
        # new (version 2) layout now, so the old body is hand-built here
        # to pin down exactly the bytes a pre-fix encoder used to emit.
        from balzar.scene3d import _pack_str_table, _quantize_positions
        import zlib as _zlib

        vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        strips = [[0, 1, 2]]

        out = bytearray()
        out += _pack_str_table(["Small", "Leaf"])  # name table: 0=Small, 1=Leaf
        out += struct.pack("<H", 1)  # n_shapes
        out += struct.pack("<H", 0)  # shape name index -> "Small"
        out += struct.pack("<BBB", 1, 2, 3)  # color
        out += struct.pack("<I", len(vertices))
        lo, scale, quantized = _quantize_positions(vertices)
        out += struct.pack("<3f", *lo)
        out += struct.pack("<3f", *scale)
        for qx, qy, qz in quantized:
            out += struct.pack("<3h", qx, qy, qz)
        out += struct.pack("<H", len(strips))  # old: uint16 n_strips
        for strip in strips:
            out += struct.pack("<H", len(strip))
            for idx in strip:
                out += struct.pack("<H", idx)  # old: uint16 index, unconditional
        out += struct.pack("<I", 1)  # n_refs
        out += struct.pack("<H", 1)  # ref name index -> "Leaf"
        out += struct.pack("<BH", 1, 0)  # has_shape=1, shape_index=0
        out += struct.pack("<I", 0)  # n_children
        out += struct.pack("<I", 0)  # root

        body = bytes(out)
        header = b"BZM1" + struct.pack("<HII", 1, len(body), _zlib.crc32(body))
        legacy_payload = header + _zlib.compress(body, 9)

        rebuilt = decode_payload(legacy_payload)
        self.assertEqual(rebuilt.shapes[0].vertices, vertices)
        self.assertEqual(rebuilt.shapes[0].strips, strips)
        self.assertEqual(rebuilt.shapes[0].name, "Small")


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


class TestMergeNamedGroups(unittest.TestCase):
    """merge_named_groups: an OPT-IN reserve tool (CLAUDE.md SS9.31),
    independent from generate_bom's collapse_names -- this one actually
    concatenates geometry into fewer Shape/Reference entries to shrink
    the BZM1 payload, not just group the BOM/highlight display."""

    def _two_bolts_under_a_named_group(self):
        from balzar.scene3d import Reference, Scene3D, Shape

        bolt = Shape(name="Bolt", color=(9, 9, 9),
                    vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                    strips=[[0, 1, 2]])
        bolt_ref = Reference(name="BoltDef", shape_index=0, children=[])
        # two placements of the same bolt shape under a group named
        # "Fasteners" -- one at the origin, one translated by (10,0,0)
        group = Reference(
            name="Fasteners", shape_index=None,
            children=[
                (1, "bolt_1", (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)),
                (1, "bolt_2", (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 10.0, 0.0, 0.0)),
            ])
        root = Reference(name="Root", shape_index=None,
                         children=[(2, "fasteners_inst",
                                   (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0))])
        scene = Scene3D(shapes=[bolt], references=[root, bolt_ref, group], root=0)
        return scene

    def test_no_merge_names_returns_the_same_scene_unchanged(self):
        from balzar.scene3d import merge_named_groups

        scene = self._two_bolts_under_a_named_group()
        self.assertIs(merge_named_groups(scene, None), scene)
        self.assertIs(merge_named_groups(scene, set()), scene)

    def test_merge_concatenates_geometry_at_correct_world_positions(self):
        from balzar.scene3d import merge_named_groups

        scene = self._two_bolts_under_a_named_group()
        merged = merge_named_groups(scene, {"Fasteners"})

        # only one shape and one reference left: the two separate bolt
        # placements + their own def/group refs are pruned away
        self.assertEqual(len(merged.shapes), 1)
        self.assertEqual(len(merged.references), 2)  # Root + merged Fasteners

        merged_shape = merged.shapes[0]
        self.assertEqual(merged_shape.name, "Fasteners")
        self.assertEqual(len(merged_shape.vertices), 6)  # 3 verts x 2 bolts
        # first bolt at the origin, second translated by (10,0,0) -- both
        # transforms correctly composed and applied, not just concatenated
        self.assertEqual(merged_shape.vertices[:3], [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
        self.assertEqual(merged_shape.vertices[3:], [(10.0, 0.0, 0.0), (11.0, 0.0, 0.0), (10.0, 1.0, 0.0)])
        self.assertEqual(merged_shape.strips, [[0, 1, 2], [3, 4, 5]])

    def test_merged_scene_still_round_trips_through_the_payload(self):
        # the int16-per-shape quantization the encoder already applies
        # is itself lossy (CLAUDE.md SS9.5/mean_vertex_error) -- compare
        # against the already-quantized scene, the same honesty pattern
        # encode_3dxml_file's own self-check already uses, not raw
        # pre-quantization equality (which a real float like 1.0 has no
        # guarantee of surviving unchanged through int16 round-tripping).
        from balzar.scene3d import merge_named_groups

        scene = self._two_bolts_under_a_named_group()
        merged = merge_named_groups(scene, {"Fasteners"})
        quantized_merged, _ = _quantized_copy(merged)
        payload = encode_payload(merged)
        rebuilt = decode_payload(payload)
        self.assertEqual(rebuilt, quantized_merged)

    def test_merge_reduces_payload_size_for_many_distinct_unrepeated_parts(self):
        # the case this tool actually helps: many DISTINCT small parts
        # (each used only once, e.g. small brackets/covers) grouped
        # under one named sub-assembly the caller doesn't need to see
        # individually -- merging removes their per-part Reference/
        # ReferenceRep/InstanceRep/Instance3D overhead (names, structure)
        # WITHOUT losing any deduplication benefit, because there was
        # none to lose: each shape was already used exactly once.
        from balzar.scene3d import Reference, Scene3D, Shape, merge_named_groups

        n = 50
        shapes = [Shape(name=f"Bracket{i}", color=(i % 255, 10, 20),
                        vertices=[(float(i), 0.0, 0.0), (float(i) + 1, 0.0, 0.0), (float(i), 1.0, 0.0)],
                        strips=[[0, 1, 2]]) for i in range(n)]
        refs = [Reference(name="Root", shape_index=None, children=[])]
        group_children = []
        for i in range(n):
            ref_idx = len(refs)
            refs.append(Reference(name=f"BracketDef{i}", shape_index=i, children=[]))
            group_children.append((ref_idx, f"inst_{i}", _IDENTITY_MATRIX))
        group_idx = len(refs)
        refs.append(Reference(name="Brackets", shape_index=None, children=group_children))
        refs[0] = Reference(name="Root", shape_index=None,
                            children=[(group_idx, "brackets_inst", _IDENTITY_MATRIX)])
        scene = Scene3D(shapes=shapes, references=refs, root=0)

        unmerged_payload = encode_payload(scene)
        merged_payload = encode_payload(merge_named_groups(scene, {"Brackets"}))
        self.assertLess(len(merged_payload), len(unmerged_payload))

    def test_merge_can_be_counterproductive_for_many_repeated_instances(self):
        # the opposite, equally real finding, measured not assumed: for
        # many REPEATED instances of the SAME shape (the classic "bolts"
        # case one might expect this tool to target), merging is a
        # regression, not a win -- it duplicates the already-deduplicated
        # vertex data N times (baking each instance's world position
        # into distinct quantized coordinates) in exchange for removing
        # cheap per-instance transform records that DEFLATE already
        # compresses extremely well (near-identical structured bytes).
        # Documented in CLAUDE.md SS9.31 so this isn't rediscovered as
        # a surprise later -- merge_names is opt-in specifically so a
        # caller can choose NOT to use it here.
        from balzar.scene3d import Reference, Scene3D, Shape, merge_named_groups

        bolt = Shape(name="Bolt", color=(9, 9, 9),
                    vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                    strips=[[0, 1, 2]])
        bolt_ref = Reference(name="BoltDef", shape_index=0, children=[])
        n = 200
        group_children = [
            (1, f"bolt_{i}", (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, float(i), 0.0, 0.0))
            for i in range(n)
        ]
        group = Reference(name="Fasteners", shape_index=None, children=group_children)
        root = Reference(name="Root", shape_index=None,
                         children=[(2, "fasteners_inst", _IDENTITY_MATRIX)])
        scene = Scene3D(shapes=[bolt], references=[root, bolt_ref, group], root=0)

        unmerged_payload = encode_payload(scene)
        merged_payload = encode_payload(merge_named_groups(scene, {"Fasteners"}))
        self.assertGreater(len(merged_payload), len(unmerged_payload))

    def test_unmatched_merge_name_is_silently_ignored(self):
        from balzar.scene3d import merge_named_groups

        scene = self._two_bolts_under_a_named_group()
        merged = merge_named_groups(scene, {"DoesNotExist"})
        self.assertEqual(len(merged.shapes), 1)  # unchanged
        self.assertEqual(len(merged.references), 3)  # unchanged

    def test_merge_name_matching_a_leaf_with_no_children_is_a_no_op(self):
        from balzar.scene3d import merge_named_groups

        scene = self._two_bolts_under_a_named_group()
        # "BoltDef" is a leaf (has a shape, no children) -- nothing to
        # concatenate, must not crash or corrupt anything
        merged = merge_named_groups(scene, {"BoltDef"})
        self.assertEqual(len(merged.shapes), 1)
        self.assertEqual(len(merged.references), 3)

    def test_encode_3dxml_file_accepts_optional_merge_names(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        path = os.path.join(self.tmpdir.name, "fixture.3dxml")
        _write_fixture_3dxml(path)

        # default (no merge_names) behaves exactly as before
        baseline = encode_3dxml_file(path)
        same = encode_3dxml_file(path, merge_names=None)
        self.assertEqual(baseline.payload, same.payload)

        # merging "SubGroup" (a real group in the fixture) doesn't crash
        # and produces a self-consistent result (encode_3dxml_file's own
        # internal self-check already raises on any inconsistency)
        merged_result = encode_3dxml_file(path, merge_names={"SubGroup"})
        self.assertIsInstance(merged_result.payload, bytes)


if __name__ == "__main__":
    unittest.main()
