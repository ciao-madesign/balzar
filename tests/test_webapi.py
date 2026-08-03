"""Web demo backend (balzar/webapi.py): the new encoding flows (vector,
video, sequence) and the QR generator added to the Vercel demo. Success
paths, error paths, and the truncation/omission behavior driven by Limits."""

import base64
import io
import os
import tempfile
import unittest

from balzar.webapi import (LOCAL_LIMITS, Limits, handle_encode, handle_encode_3d,
                           handle_encode_sequence, handle_encode_vector,
                           handle_encode_video, handle_qr, handle_render)

SVG_FLANGE = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
  <circle cx="100" cy="100" r="80" fill="none" stroke="#000000"/>
</svg>"""

DXF_FLANGE = """0
SECTION
2
ENTITIES
0
CIRCLE
8
CARCASSA
62
8
10
200.0
20
200.0
40
150.0
0
ENDSEC
0
EOF
"""

DXF_STEP2 = """0
SECTION
2
ENTITIES
0
CIRCLE
8
CARCASSA
62
8
10
200.0
20
200.0
40
150.0
0
CIRCLE
8
FLANGIA
62
7
10
200.0
20
200.0
40
60.0
0
ENDSEC
0
EOF
"""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _make_png(w=40, h=30):
    from PIL import Image
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for x in range(5, 15):
        for y in range(5, 15):
            img.putpixel((x, y), (200, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestHandleEncode(unittest.TestCase):
    """The 'Comprimi immagine' tab (tab 1) — first-frame-only raster
    encoder, previously untested at the webapi layer."""

    def test_success(self):
        status, resp = handle_encode({"data": _b64(_make_png()), "max_dim": 200}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertIn("mean_color_error", resp)

    def test_missing_data(self):
        status, resp = handle_encode({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_encode({"data": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_non_image_gives_clean_400_not_500(self):
        status, resp = handle_encode({"data": _b64(b"not an image")}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleRender(unittest.TestCase):
    """The 'Apri programma (.bzr/.bzp)' tab (tab 5) — previously untested
    at the webapi layer despite being one of the five demo flows."""

    def _payload(self):
        from balzar.payload import encode_payload
        return encode_payload("CANVAS w=4 h=4 bg=0\nPALETTE i=1 rgb=#FF0000\n"
                              "RECT x=0 y=0 w=2 h=2 color=1 fill=1\n")

    def test_success_with_bzp_payload(self):
        status, resp = handle_render({"data": _b64(self._payload())}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual((resp["width"], resp["height"]), (4, 4))
        self.assertIn("payload_base64", resp)

    def test_success_with_bzr_source_text(self):
        program = "CANVAS w=3 h=3 bg=0\n"
        status, resp = handle_render({"data": _b64(program.encode("utf-8"))}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual((resp["width"], resp["height"]), (3, 3))

    def test_missing_data(self):
        status, resp = handle_render({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_render({"data": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_corrupt_payload_magic_gives_clean_400_not_500(self):
        garbage = _b64(b"\xff\xfe\xfd not a program and not utf-8 \x00")
        status, resp = handle_render({"data": garbage}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_invalid_program_text_gives_clean_400_not_500(self):
        status, resp = handle_render({"data": _b64(b"THIS IS NOT A VALID BALZAR PROGRAM")},
                                     LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_2d_response_carries_kind_discriminator(self):
        status, resp = handle_render({"data": _b64(self._payload())}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["kind"], "2d")

    def test_bare_bzm1_payload_opens_as_3d(self):
        from balzar.scene3d import encode_payload as encode_scene, parse_3dxml
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "assembly.3dxml")
            with open(path, "wb") as fh:
                fh.write(_make_3dxml_bytes())
            payload = encode_scene(parse_3dxml(path))

        status, resp = handle_render({"data": _b64(payload)}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "3d")
        self.assertEqual(resp["shape_count"], 1)
        self.assertEqual(resp["instance_count"], 2)
        self.assertFalse(resp["glb_omitted"])
        self.assertTrue(resp["glb_base64"])
        self.assertIn("payload_base64", resp)

    def test_corrupt_bzm1_gives_clean_400_not_500(self):
        from balzar.scene3d import MAGIC
        garbage = MAGIC + b"\x00" * 20
        status, resp = handle_render({"data": _b64(garbage)}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_bzx1_bundle_with_3d_and_alarm_opens_with_both(self):
        from balzar.bundle import BundleItem, KIND_3D, KIND_ALARM, encode_bundle
        from balzar.scene3d import encode_payload as encode_scene, parse_3dxml
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "assembly.3dxml")
            with open(path, "wb") as fh:
                fh.write(_make_3dxml_bytes())
            scene_payload = encode_scene(parse_3dxml(path))

        csv_text = "codice_allarme,nome_componente\nE100,Bullone-M6\n"
        bundle = encode_bundle([
            BundleItem(KIND_3D, "assembly.b3d", scene_payload),
            BundleItem(KIND_ALARM, "alarms.csv", csv_text.encode("utf-8")),
        ])

        status, resp = handle_render({"data": _b64(bundle)}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "bundle")
        self.assertTrue(resp["has_3d"])
        self.assertEqual(resp["shape_count"], 1)
        self.assertTrue(resp["glb_base64"])
        self.assertEqual(resp["info_table"],
                         {"headers": ["codice_allarme", "nome_componente"], "rows": [["E100", "Bullone-M6"]]})
        self.assertTrue(any(d["label"] == "alarms.csv" for d in resp["documents"]))

    def test_bzx1_documents_only_bundle_has_no_3d(self):
        from balzar.bundle import BundleItem, KIND_DOC, encode_bundle
        bundle = encode_bundle([BundleItem(KIND_DOC, "note.txt", b"ciao mondo")])

        status, resp = handle_render({"data": _b64(bundle)}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["kind"], "bundle")
        self.assertFalse(resp["has_3d"])
        self.assertNotIn("glb_base64", resp)
        self.assertEqual(len(resp["documents"]), 1)
        self.assertEqual(resp["documents"][0]["label"], "note.txt")

    def test_corrupt_bzx1_gives_clean_400_not_500(self):
        from balzar.bundle import MAGIC
        garbage = MAGIC + b"\x00" * 20
        status, resp = handle_render({"data": _b64(garbage)}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleEncodeVector(unittest.TestCase):
    def test_svg_success(self):
        status, resp = handle_encode_vector(
            {"data": _b64(SVG_FLANGE.encode()), "filename": "flange.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["source_format"], "svg")
        self.assertEqual(resp["skipped"], [])
        self.assertTrue(resp["svg_available"])
        self.assertIn("preview_png_base64", resp)

    def test_dxf_success(self):
        status, resp = handle_encode_vector(
            {"data": _b64(DXF_FLANGE.encode()), "filename": "flange.dxf"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["source_format"], "dxf")
        self.assertEqual(resp["element_count"], 1)

    def test_missing_data(self):
        status, resp = handle_encode_vector({"filename": "flange.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_unrecognized_extension(self):
        status, resp = handle_encode_vector(
            {"data": _b64(b"hello"), "filename": "flange.txt"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertIn("estensione", resp["error"])

    def test_invalid_svg_gives_clean_400_not_500(self):
        status, resp = handle_encode_vector(
            {"data": _b64(b"<svg><circle cx=oops></svg>"), "filename": "bad.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_encode_vector(
            {"data": "not-valid-base64!!!", "filename": "flange.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_program_truncation_uses_limit(self):
        tiny_limits = Limits(max_upload_bytes=LOCAL_LIMITS.max_upload_bytes,
                             max_analysis_dim=LOCAL_LIMITS.max_analysis_dim,
                             max_preview_dim=LOCAL_LIMITS.max_preview_dim,
                             max_program_chars=5,
                             max_payload_b64_bytes=LOCAL_LIMITS.max_payload_b64_bytes,
                             max_video_frames=LOCAL_LIMITS.max_video_frames)
        status, resp = handle_encode_vector(
            {"data": _b64(SVG_FLANGE.encode()), "filename": "flange.svg"}, tiny_limits)
        self.assertEqual(status, 200)
        self.assertTrue(resp["program_truncated"])


def _make_3dxml_bytes():
    """Minimal in-memory 3DXML: one shape ('Bullone-M6') placed twice —
    mirrors the fixture already used in tests/test_scene3d.py."""
    import zipfile
    from io import BytesIO

    manifest = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Manifest xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
               'xsi:noNamespaceSchemaLocation="Manifest.xsd">'
               '<Root>main.3dxml</Root></Manifest>')
    main_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Model_3dxml xmlns="http://www.3ds.com/xsd/3DXML" '
               'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
               '<ProductStructure root="1">'
               '<Reference3D id="1" name="Root"/>'
               '<Instance3D id="2" name="inst_A"><IsAggregatedBy>1</IsAggregatedBy>'
               '<IsInstanceOf>3</IsInstanceOf>'
               '<RelativeMatrix>1 0 0 0 1 0 0 0 1 0 0 0</RelativeMatrix></Instance3D>'
               '<Reference3D id="3" name="Bullone-M6"/>'
               '<ReferenceRep id="4" name="R" associatedFile="urn:3DXML:s.3DRep"/>'
               '<InstanceRep id="5" name="IR"><IsAggregatedBy>3</IsAggregatedBy>'
               '<IsInstanceOf>4</IsInstanceOf></InstanceRep>'
               '<Instance3D id="6" name="inst_B"><IsAggregatedBy>1</IsAggregatedBy>'
               '<IsInstanceOf>3</IsInstanceOf>'
               '<RelativeMatrix>1 0 0 0 1 0 0 0 1 5 0 0</RelativeMatrix></Instance3D>'
               '</ProductStructure></Model_3dxml>')
    shape = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<XMLRepresentation xmlns="http://www.3ds.com/xsd/3DXML" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<Root xsi:type="BagRepType" id="1"><Rep xsi:type="PolygonalRepType" id="2">'
            '<Faces><Face strips="0 1 2"><SurfaceAttributes>'
            '<Color xsi:type="RGBAColorType" red="1" green="0" blue="0" alpha="1"/>'
            '</SurfaceAttributes></Face></Faces>'
            '<VertexBuffer><Positions>0 0 0 1 0 0 0 1 0</Positions>'
            '<Normals>0 0 1 0 0 1 0 0 1</Normals></VertexBuffer></Rep></Root>'
            '</XMLRepresentation>')
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Manifest.xml", manifest)
        zf.writestr("main.3dxml", main_xml)
        zf.writestr("s.3DRep", shape)
    return buf.getvalue()


def _make_3dxml_with_group_bytes():
    """A HEATER1 sub-assembly wrapping two distinct leaf parts (BoltA,
    BoltB) -- unlike _make_3dxml_bytes's fixture (a single leaf placed
    twice, no real group), this exercises collapse_names on a genuine
    sub-assembly with several children."""
    import zipfile
    from io import BytesIO

    manifest = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Manifest xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
               'xsi:noNamespaceSchemaLocation="Manifest.xsd">'
               '<Root>main.3dxml</Root></Manifest>')
    main_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Model_3dxml xmlns="http://www.3ds.com/xsd/3DXML" '
               'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
               '<ProductStructure root="1">'
               '<Reference3D id="1" name="Root"/>'
               '<Instance3D id="2" name="inst_heater"><IsAggregatedBy>1</IsAggregatedBy>'
               '<IsInstanceOf>3</IsInstanceOf>'
               '<RelativeMatrix>1 0 0 0 1 0 0 0 1 0 0 0</RelativeMatrix></Instance3D>'
               '<Reference3D id="3" name="HEATER1"/>'
               '<Instance3D id="4" name="inst_a"><IsAggregatedBy>3</IsAggregatedBy>'
               '<IsInstanceOf>5</IsInstanceOf>'
               '<RelativeMatrix>1 0 0 0 1 0 0 0 1 0 0 0</RelativeMatrix></Instance3D>'
               '<Reference3D id="5" name="BoltA"/>'
               '<ReferenceRep id="6" name="RA" associatedFile="urn:3DXML:a.3DRep"/>'
               '<InstanceRep id="7" name="IRA"><IsAggregatedBy>5</IsAggregatedBy>'
               '<IsInstanceOf>6</IsInstanceOf></InstanceRep>'
               '<Instance3D id="8" name="inst_b"><IsAggregatedBy>3</IsAggregatedBy>'
               '<IsInstanceOf>9</IsInstanceOf>'
               '<RelativeMatrix>1 0 0 0 1 0 0 0 1 5 0 0</RelativeMatrix></Instance3D>'
               '<Reference3D id="9" name="BoltB"/>'
               '<ReferenceRep id="10" name="RB" associatedFile="urn:3DXML:b.3DRep"/>'
               '<InstanceRep id="11" name="IRB"><IsAggregatedBy>9</IsAggregatedBy>'
               '<IsInstanceOf>10</IsInstanceOf></InstanceRep>'
               '</ProductStructure></Model_3dxml>')

    def shape_rep(fname):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<XMLRepresentation xmlns="http://www.3ds.com/xsd/3DXML" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<Root xsi:type="BagRepType" id="1"><Rep xsi:type="PolygonalRepType" id="2">'
                '<Faces><Face strips="0 1 2"><SurfaceAttributes>'
                '<Color xsi:type="RGBAColorType" red="1" green="0" blue="0" alpha="1"/>'
                '</SurfaceAttributes></Face></Faces>'
                '<VertexBuffer><Positions>0 0 0 1 0 0 0 1 0</Positions>'
                '<Normals>0 0 1 0 0 1 0 0 1</Normals></VertexBuffer></Rep></Root>'
                '</XMLRepresentation>')

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Manifest.xml", manifest)
        zf.writestr("main.3dxml", main_xml)
        zf.writestr("a.3DRep", shape_rep("a"))
        zf.writestr("b.3DRep", shape_rep("b"))
    return buf.getvalue()


class TestHandleEncode3D(unittest.TestCase):
    def test_success(self):
        status, resp = handle_encode_3d({"data": _b64(_make_3dxml_bytes())}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["shape_count"], 1)
        self.assertEqual(resp["instance_count"], 2)
        self.assertEqual(resp["bom"],
                        [{"name": "Bullone-M6", "count": 2, "material_names": ["Bullone-M6"]}])
        self.assertFalse(resp["glb_omitted"])
        self.assertGreater(len(resp["glb_base64"]), 0)
        self.assertIn("payload_base64", resp)

    def test_merge_names_field_is_optional_and_defaults_to_unchanged(self):
        status, resp = handle_encode_3d({"data": _b64(_make_3dxml_bytes())}, LOCAL_LIMITS)
        status2, resp2 = handle_encode_3d(
            {"data": _b64(_make_3dxml_bytes()), "merge_names": ""}, LOCAL_LIMITS)
        self.assertEqual(status2, 200)
        self.assertEqual(resp2["payload_base64"], resp["payload_base64"])

    def test_merge_names_field_merges_named_group(self):
        status, resp = handle_encode_3d(
            {"data": _b64(_make_3dxml_bytes()), "merge_names": "Root"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        # Root's two Bullone-M6 instances concatenated into one merged
        # shape -- still valid, self-consistent output, no crash
        self.assertEqual(resp["shape_count"], 1)

    def test_missing_data(self):
        status, resp = handle_encode_3d({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_encode_3d({"data": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_invalid_3dxml_gives_clean_400_not_500(self):
        status, resp = handle_encode_3d({"data": _b64(b"not a zip file")}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_glb_omitted_when_over_limit(self):
        tiny_limits = Limits(max_upload_bytes=LOCAL_LIMITS.max_upload_bytes,
                             max_analysis_dim=LOCAL_LIMITS.max_analysis_dim,
                             max_preview_dim=LOCAL_LIMITS.max_preview_dim,
                             max_program_chars=LOCAL_LIMITS.max_program_chars,
                             max_payload_b64_bytes=10,
                             max_video_frames=LOCAL_LIMITS.max_video_frames)
        status, resp = handle_encode_3d({"data": _b64(_make_3dxml_bytes())}, tiny_limits)
        self.assertEqual(status, 200)
        self.assertTrue(resp["glb_omitted"])
        self.assertEqual(resp["glb_base64"], "")
        self.assertTrue(resp["payload_omitted"])

    def test_no_alarm_csv_means_not_bundled(self):
        status, resp = handle_encode_3d({"data": _b64(_make_3dxml_bytes())}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertFalse(resp["bundled"])
        self.assertEqual(resp["info_table"], {"headers": [], "rows": []})

    def test_alarm_csv_produces_a_bundle_payload(self):
        from balzar.bundle import is_bundle
        from balzar.payload import from_base64

        csv_text = "codice_allarme,nome_componente\nE100,Bullone-M6\nE200,Bullone-M6\n"
        status, resp = handle_encode_3d(
            {"data": _b64(_make_3dxml_bytes()), "alarm_csv": _b64(csv_text.encode("utf-8"))},
            LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["bundled"])
        self.assertEqual(resp["info_table"], {
            "headers": ["codice_allarme", "nome_componente"],
            "rows": [["E100", "Bullone-M6"], ["E200", "Bullone-M6"]],
        })
        payload = from_base64(resp["payload_base64"])
        self.assertTrue(is_bundle(payload))

    def test_bundled_payload_still_decodes_to_the_same_3d_scene(self):
        from balzar.bundle import KIND_3D, decode_bundle
        from balzar.payload import from_base64
        from balzar.scene3d import decode_payload as decode_scene, generate_bom

        csv_text = "E100,Bullone-M6\n"
        status, resp = handle_encode_3d(
            {"data": _b64(_make_3dxml_bytes()), "alarm_csv": _b64(csv_text.encode("utf-8"))},
            LOCAL_LIMITS)
        self.assertEqual(status, 200)
        items = decode_bundle(from_base64(resp["payload_base64"]))
        three_d = next(it for it in items if it.kind == KIND_3D)
        scene = decode_scene(three_d.data)
        bom = generate_bom(scene)
        self.assertEqual([(e.name, e.count) for e in bom], [("Bullone-M6", 2)])

    def test_malformed_alarm_csv_base64_gives_clean_400(self):
        status, resp = handle_encode_3d(
            {"data": _b64(_make_3dxml_bytes()), "alarm_csv": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_alarm_csv_naming_a_subassembly_collapses_its_bom_row(self):
        # end-to-end: an alarm table naming a whole sub-assembly
        # ("HEATER1") collapses it to one BOM row instead of expanding
        # to BoltA/BoltB, and the GLB gets suffixed materials matching
        # material_names -- wiring test for scene3d.generate_bom's
        # collapse_names (unit-tested in depth in tests/test_scene3d.py)
        import json
        import struct

        csv_text = "codice_allarme,nome_componente\nA06,HEATER1\n"
        status, resp = handle_encode_3d(
            {"data": _b64(_make_3dxml_with_group_bytes()), "alarm_csv": _b64(csv_text.encode())},
            LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["bundled"])
        self.assertEqual(len(resp["bom"]), 1)
        entry = resp["bom"][0]
        self.assertEqual(entry["name"], "HEATER1")
        self.assertEqual(entry["count"], 1)
        self.assertEqual(set(entry["material_names"]), {"BoltA§HEATER1", "BoltB§HEATER1"})

        glb = base64.b64decode(resp["glb_base64"])
        json_len, _ = struct.unpack_from("<II", glb, 12)
        gltf = json.loads(glb[20:20 + json_len].decode("utf-8"))
        glb_names = {m["name"] for m in gltf["materials"]}
        self.assertEqual(glb_names, {"BoltA§HEATER1", "BoltB§HEATER1"})

    def test_no_alarm_match_leaves_bom_uncollapsed(self):
        # the alarm table's component name doesn't match anything in
        # this scene -- collapse_names is still passed through, but
        # since nothing matches, the BOM stays fully expanded (honest:
        # no silent behavior change when a name is simply wrong/typo'd)
        csv_text = "codice_allarme,nome_componente\nA01,DOES_NOT_EXIST\n"
        status, resp = handle_encode_3d(
            {"data": _b64(_make_3dxml_with_group_bytes()), "alarm_csv": _b64(csv_text.encode())},
            LOCAL_LIMITS)
        self.assertEqual(status, 200)
        names = {e["name"] for e in resp["bom"]}
        self.assertEqual(names, {"BoltA", "BoltB"})

    def test_bzr_document_is_rendered_into_png_and_svg(self):
        bzr_text = ("CANVAS w=40 h=40 bg=1\nPALETTE i=2 rgb=#FF0000\n"
                   "RECT x=5 y=5 w=10 h=10 color=2 fill=1\n")
        status, resp = handle_encode_3d({
            "data": _b64(_make_3dxml_bytes()),
            "documents": [{"label": "tavola.bzr", "data": _b64(bzr_text.encode("utf-8"))}],
        }, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["bundled"])
        labels = [d["label"] for d in resp["documents"]]
        self.assertIn("tavola.png", labels)
        self.assertIn("tavola.svg", labels)

    def test_invalid_bzr_document_gives_clean_400(self):
        status, resp = handle_encode_3d({
            "data": _b64(_make_3dxml_bytes()),
            "documents": [{"label": "broken.bzr", "data": _b64(b"BOGUS x=1\n")}],
        }, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])
        self.assertIn("broken.bzr", resp["error"])

    def test_plain_document_stays_a_generic_doc(self):
        status, resp = handle_encode_3d({
            "data": _b64(_make_3dxml_bytes()),
            "documents": [{"label": "note.txt", "data": _b64(b"hello")}],
        }, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["documents"], [{"role": "doc", "label": "note.txt",
                                             "b64": _b64(b"hello")}])


class TestHandleEncodeVideo(unittest.TestCase):
    def _make_gif(self, n_frames=4):
        from PIL import Image
        frames = []
        for i in range(n_frames):
            img = Image.new("RGB", (40, 30), (255, 255, 255))
            for x in range(5 + i * 5, 15 + i * 5):
                for y in range(5, 15):
                    img.putpixel((x, y), (200, 0, 0))
            frames.append(img)
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                      duration=100, loop=0)
        return buf.getvalue()

    def test_success(self):
        try:
            data = self._make_gif()
        except ImportError:
            self.skipTest("Pillow non installato")
        status, resp = handle_encode_video({"data": _b64(data), "max_dim": 200}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["frame_count"], 4)
        self.assertIn("preview_gif_base64", resp)

    def test_single_frame_rejected(self):
        from PIL import Image
        img = Image.new("RGB", (10, 10), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        status, resp = handle_encode_video({"data": _b64(buf.getvalue())}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertIn("un solo frame", resp["error"])

    def test_missing_data(self):
        status, resp = handle_encode_video({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_non_image_gives_clean_400_not_500(self):
        status, resp = handle_encode_video({"data": _b64(b"not an image")}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_encode_video({"data": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleEncodeSequence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _files(self, *contents_and_names):
        return [{"filename": name, "data": _b64(content.encode() if isinstance(content, str) else content)}
                for content, name in contents_and_names]

    def test_vector_sequence_success(self):
        files = self._files((DXF_FLANGE, "step1.dxf"), (DXF_STEP2, "step2.dxf"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["frame_count"], 2)
        self.assertEqual(len(resp["preview_frames_png_base64"]), 2)

    def test_raster_sequence_success(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow non installato")
        contents = []
        for i, offset in enumerate([0, 5]):
            img = Image.new("RGB", (30, 20), (255, 255, 255))
            for x in range(5 + offset, 12 + offset):
                for y in range(5, 12):
                    img.putpixel((x, y), (0, 200, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            contents.append((buf.getvalue(), f"f{i}.png"))
        files = self._files(*contents)
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["source_format"], "raster")
        self.assertEqual(resp["frame_count"], 2)
        self.assertEqual(len(resp["preview_frames_png_base64"]), 2)

    def test_single_file_rejected(self):
        files = self._files((DXF_FLANGE, "step1.dxf"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_mixed_vector_formats_rejected(self):
        files = self._files((SVG_FLANGE, "a.svg"), (DXF_FLANGE, "b.dxf"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_non_image_in_raster_path_gives_clean_400_not_500(self):
        files = self._files((DXF_FLANGE, "a.dxf"), (b"not an image", "b.png"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_missing_files_field(self):
        status, resp = handle_encode_sequence({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_file_without_content_rejected(self):
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "b.dxf"}]
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_malformed_base64_gives_clean_400_not_500(self):
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "b.dxf", "data": "not-valid-base64!!!"}]
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleEncodeIndependent(unittest.TestCase):
    """mode='independent' dispatch inside handle_encode_sequence: no
    format restriction, fault isolation per file, single file allowed."""

    def test_mixed_formats_all_succeed(self):
        files = [{"filename": "a.svg", "data": _b64(SVG_FLANGE.encode())},
                {"filename": "b.dxf", "data": _b64(DXF_FLANGE.encode())}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["file_count"], 2)
        self.assertEqual(resp["success_count"], 2)
        self.assertEqual(resp["items"][0]["filename"], "a.svg")
        self.assertEqual(resp["items"][1]["filename"], "b.dxf")
        self.assertTrue(all(it["ok"] for it in resp["items"]))

    def test_broken_file_reported_not_500(self):
        files = [{"filename": "good.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "bad.svg", "data": _b64(b"<svg><circle cx=oops></svg>")}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["success_count"], 1)
        self.assertTrue(resp["items"][0]["ok"])
        self.assertFalse(resp["items"][1]["ok"])
        self.assertIn("error", resp["items"][1])

    def test_single_file_allowed(self):
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["file_count"], 1)

    def test_malformed_base64_isolated_not_500_and_order_preserved(self):
        """A corrupt base64 blob for one file must not crash the whole
        batch (the same fault-isolation guarantee as a parseable-but-
        broken file), and the surviving files must keep their original
        position in the response."""
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "bad.svg", "data": "not-valid-base64!!!"},
                {"filename": "c.dxf", "data": _b64(DXF_STEP2.encode())}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["file_count"], 3)
        self.assertEqual(resp["success_count"], 2)
        self.assertEqual([it["filename"] for it in resp["items"]], ["a.dxf", "bad.svg", "c.dxf"])
        self.assertTrue(resp["items"][0]["ok"])
        self.assertFalse(resp["items"][1]["ok"])
        self.assertIn("error", resp["items"][1])
        self.assertTrue(resp["items"][2]["ok"])

    def test_empty_files_rejected(self):
        status, resp = handle_encode_sequence(
            {"files": [], "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)


class TestHandleQr(unittest.TestCase):
    def test_small_payload_is_a_single_qr(self):
        status, resp = handle_qr({"payload_base64": _b64(b"hello world")}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["single_qr"])
        self.assertIn("qr_png_base64", resp)

    def test_large_payload_becomes_a_grid(self):
        big = b"x" * 6000
        status, resp = handle_qr({"payload_base64": _b64(big)}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertFalse(resp["single_qr"])

    def test_missing_payload(self):
        status, resp = handle_qr({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_malformed_base64_gives_clean_400_not_500(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        status, resp = handle_qr({"payload_base64": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_qr_roundtrips_via_zbar_if_available(self):
        try:
            from pyzbar.pyzbar import decode as zbar_decode
        except ImportError:
            self.skipTest("pyzbar non installato")
        from PIL import Image

        from balzar.payload import assemble_chunks, from_base64

        payload = b"balzar QR roundtrip check"
        status, resp = handle_qr({"payload_base64": _b64(payload)}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        img = Image.open(io.BytesIO(base64.b64decode(resp["qr_png_base64"])))
        results = zbar_decode(img)
        self.assertEqual(len(results), 1)
        chunk = from_base64(results[0].data.decode("ascii"))
        self.assertEqual(assemble_chunks([chunk]), payload)

    def test_invalid_mode_rejected(self):
        status, resp = handle_qr(
            {"payload_base64": _b64(b"hello"), "mode": "mp4"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_grid_dim_is_clamped_to_sane_range(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        # 100 is absurd for a public endpoint (huge composed image); must
        # clamp instead of trying to honour it literally.
        status, resp = handle_qr(
            {"payload_base64": _b64(b"x" * 200), "mode": "gif", "grid_dim": 100}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["grid_dim"], 8)

    def test_grid_dim_1_is_allowed_not_clamped_up(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        # grid_dim=1 (one QR per frame, no grid) is the only grid_dim a
        # live camera can reliably decode continuously -- must pass
        # through unchanged, not get clamped up to the old floor of 2.
        status, resp = handle_qr(
            {"payload_base64": _b64(b"x" * 200), "mode": "pages", "grid_dim": 1}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["grid_dim"], 1)

    def test_grid_dim_below_1_is_clamped_up_to_1(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        status, resp = handle_qr(
            {"payload_base64": _b64(b"x" * 200), "mode": "gif", "grid_dim": 0}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["grid_dim"], 1)

    def test_gif_mode_single_frame_for_small_payload(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        status, resp = handle_qr(
            {"payload_base64": _b64(b"tiny"), "mode": "gif"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["mode"], "gif")
        self.assertEqual(resp["n_frames"], 1)
        self.assertFalse(resp["gif_omitted"])
        self.assertIn("qr_gif_base64", resp)
        self.assertIn("estimated_scan_seconds_low", resp)
        self.assertIn("estimated_scan_seconds_high", resp)
        self.assertLessEqual(resp["estimated_scan_seconds_low"], resp["estimated_scan_seconds_high"])

    def test_gif_mode_produces_multiple_frames_for_a_large_payload(self):
        # grid_dim=4 default -> 16 QR/frame, ~2206 raw bytes/chunk
        # (CHUNK_RAW_BYTES): 40000 bytes needs > 16 chunks, so this must
        # split into more than one frame -- the whole point of this mode.
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        big = b"y" * 40000
        status, resp = handle_qr({"payload_base64": _b64(big), "mode": "gif"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertGreater(resp["n_frames"], 1)
        self.assertEqual(resp["grid_dim"], 4)
        # more frames -> a proportionally larger estimate, not a fixed number
        self.assertGreater(resp["estimated_scan_seconds_low"], resp["n_frames"])

    def test_pages_mode_returns_one_png_per_frame(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        big = b"z" * 40000
        status, resp = handle_qr({"payload_base64": _b64(big), "mode": "pages"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["mode"], "pages")
        self.assertGreater(resp["n_frames"], 1)
        self.assertEqual(len(resp["pages"]), resp["n_frames"])
        for page in resp["pages"]:
            self.assertIn("png_base64", page)
            self.assertGreater(page["width"], 0)
        self.assertIn("estimated_scan_seconds_low", resp)
        self.assertIn("estimated_scan_seconds_high", resp)

    def test_pages_mode_roundtrips_via_zbar_and_livescanner_if_available(self):
        try:
            from pyzbar.pyzbar import decode as zbar_decode
        except ImportError:
            self.skipTest("pyzbar non installato")
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        from balzar.qr import LiveScanner

        payload = b"balzar multi-frame QR roundtrip" * 1000
        status, resp = handle_qr(
            {"payload_base64": _b64(payload), "mode": "pages", "grid_dim": 2}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertGreater(resp["n_frames"], 1)

        scanner = LiveScanner()
        complete = False
        for page in resp["pages"]:
            complete, _missing = scanner.add(base64.b64decode(page["png_base64"]))
        self.assertTrue(complete)
        self.assertEqual(scanner.result(), payload)

    def test_response_omits_gif_when_over_the_response_size_limit(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        tiny_limits = Limits(max_upload_bytes=10_000_000, max_analysis_dim=800,
                             max_preview_dim=400, max_program_chars=300_000,
                             max_payload_b64_bytes=1000, max_video_frames=40)
        status, resp = handle_qr({"payload_base64": _b64(b"small"), "mode": "gif"}, tiny_limits)
        self.assertEqual(status, 200)
        self.assertTrue(resp["gif_omitted"])
        self.assertEqual(resp["qr_gif_base64"], "")


if __name__ == "__main__":
    unittest.main()
