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
from balzar.scene3d import (Scene3DError, decode_payload, encode_3dxml_file,
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
        self.assertEqual(len(gltf["meshes"]), 2)
        self.assertEqual(gltf["asset"]["version"], "2.0")
        # 3 leaf instances (2x PartA, 1x PartB) must appear as mesh-bearing
        # nodes somewhere in the (duplicated, tree-shaped) node list
        mesh_nodes = [n for n in gltf["nodes"] if "mesh" in n]
        self.assertEqual(len(mesh_nodes), 3)


if __name__ == "__main__":
    unittest.main()
