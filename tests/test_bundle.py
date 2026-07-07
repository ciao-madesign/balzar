"""Multi-document bundle (balzar/bundle.py): BZX1 format round-trip,
corruption detection, and the file-dispatch helper. The key architectural
claim -- that a bundle flows through the existing QR/chunk machinery
completely unchanged, because that machinery already treats any payload
as opaque bytes -- gets its own direct test (TestBundleThroughQrCarrier).

Reuses the synthetic 3DXML fixture from test_scene3d.py rather than
duplicating it (same principle already used elsewhere in this suite:
one small in-memory fixture, no real CAD file in the repo)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_scene3d import _write_fixture_3dxml

from balzar.bundle import (KIND_2D, KIND_3D, KIND_ALARM, KIND_CSV, KIND_DOC,
                           BundleError, BundleItem, decode_bundle, encode_bundle,
                           encode_bundle_files, is_bundle)
from balzar.payload import assemble_chunks, chunk_payload
from balzar.scene3d import decode_payload as decode_scene, generate_bom


class TestBundleRoundtrip(unittest.TestCase):
    def test_two_items_roundtrip_exactly(self):
        items = [
            BundleItem(KIND_3D, "assembly.b3d", b"\x00\x01fake-bzm1-bytes\xff"),
            BundleItem(KIND_CSV, "alarms.csv", "E100,PartA\nE100,PartB\n".encode("utf-8")),
        ]
        data = encode_bundle(items)
        self.assertTrue(is_bundle(data))
        back = decode_bundle(data)
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0].kind, KIND_3D)
        self.assertEqual(back[0].label, "assembly.b3d")
        self.assertEqual(back[0].data, items[0].data)
        self.assertEqual(back[1].kind, KIND_CSV)
        self.assertEqual(back[1].data, items[1].data)

    def test_labels_with_unicode_survive(self):
        items = [BundleItem(KIND_CSV, "tabella allarmi (linea 3).csv", b"E1,X\n")]
        back = decode_bundle(encode_bundle(items))
        self.assertEqual(back[0].label, "tabella allarmi (linea 3).csv")

    def test_empty_bundle_rejected(self):
        with self.assertRaises(BundleError):
            encode_bundle([])

    def test_bad_magic_rejected(self):
        with self.assertRaises(BundleError):
            decode_bundle(b"NOPE" + b"\x00" * 20)

    def test_corrupted_body_detected(self):
        data = encode_bundle([BundleItem(KIND_CSV, "a.csv", b"E1,X\n")])
        corrupted = data[:-1] + bytes([data[-1] ^ 0xFF])
        with self.assertRaises(BundleError):
            decode_bundle(corrupted)

    def test_truncated_data_detected(self):
        data = encode_bundle([BundleItem(KIND_CSV, "a.csv", b"E1,X\n" * 50)])
        with self.assertRaises(BundleError):
            decode_bundle(data[:len(data) // 2])


class TestEncodeBundleFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.xml_path = os.path.join(self.tmp, "assembly.3dxml")
        _write_fixture_3dxml(self.xml_path)
        self.csv_path = os.path.join(self.tmp, "alarms.csv")
        with open(self.csv_path, "w", encoding="utf-8") as fh:
            fh.write("codice_allarme,nome_componente\nE100,PartA\nE100,PartB\nE200,PartB\n")

    def test_3dxml_plus_marked_alarm_csv(self):
        data = encode_bundle_files([self.xml_path, self.csv_path],
                                   alarm_paths=[self.csv_path])
        items = decode_bundle(data)
        self.assertEqual([it.kind for it in items], [KIND_3D, KIND_ALARM])
        scene = decode_scene(items[0].data)
        bom = generate_bom(scene)
        self.assertEqual(sorted(e.name for e in bom), ["PartA", "PartB"])
        self.assertIn("E100,PartA", items[1].data.decode("utf-8"))

    def test_unmarked_csv_is_a_generic_doc_not_an_alarm_table(self):
        # a CSV that isn't marked as the alarm table is just a document
        data = encode_bundle_files([self.xml_path, self.csv_path])
        items = decode_bundle(data)
        self.assertEqual([it.kind for it in items], [KIND_3D, KIND_DOC])

    def test_arbitrary_format_carried_as_generic_doc(self):
        # a format the viewer can't render (pdf) is carried as a raw doc,
        # not rejected -- it's a consultable attachment, downloaded not
        # previewed (see CLAUDE.md; PDF has no encoder, just raw carriage)
        pdf_path = os.path.join(self.tmp, "drawing.pdf")
        with open(pdf_path, "wb") as fh:
            fh.write(b"%PDF-1.4 fake")
        data = encode_bundle_files([pdf_path])
        items = decode_bundle(data)
        self.assertEqual(items[0].kind, KIND_DOC)
        self.assertEqual(items[0].label, "drawing.pdf")
        self.assertEqual(items[0].data, b"%PDF-1.4 fake")

    def test_document_only_bundle_needs_no_3d(self):
        txt_path = os.path.join(self.tmp, "manual.txt")
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write("istruzioni")
        data = encode_bundle_files([txt_path, self.csv_path])
        items = decode_bundle(data)
        self.assertEqual([it.kind for it in items], [KIND_DOC, KIND_DOC])

    def test_b3d_input_is_carried_verbatim(self):
        from balzar.scene3d import encode_payload, parse_3dxml
        scene = parse_3dxml(self.xml_path)
        b3d_path = os.path.join(self.tmp, "assembly.b3d")
        with open(b3d_path, "wb") as fh:
            fh.write(encode_payload(scene))
        data = encode_bundle_files([b3d_path])
        items = decode_bundle(data)
        self.assertEqual(items[0].kind, KIND_3D)
        with open(b3d_path, "rb") as fh:
            self.assertEqual(items[0].data, fh.read())

    def test_marked_alarm_that_is_not_utf8_rejected_with_filename(self):
        bad_alarm = os.path.join(self.tmp, "alarms_bad.csv")
        with open(bad_alarm, "wb") as fh:
            fh.write(b"\xff\xfe not utf8")
        with self.assertRaises(BundleError) as ctx:
            encode_bundle_files([bad_alarm], alarm_paths=[bad_alarm])
        self.assertIn("alarms_bad.csv", str(ctx.exception))

    def test_invalid_3dxml_error_includes_filename(self):
        bad_xml = os.path.join(self.tmp, "broken.3dxml")
        with open(bad_xml, "wb") as fh:
            fh.write(b"not a zip file")
        with self.assertRaises(BundleError) as ctx:
            encode_bundle_files([bad_xml])
        self.assertIn("broken.3dxml", str(ctx.exception))


class TestKind2D(unittest.TestCase):
    """A .bzr/.bzp file (a 2D balzar program -- a technical drawing) gets
    its own role, KIND_2D: rendered fresh at view time (viewer3d.py),
    never stored as pixels here -- bundle.py only carries the program."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write_bzr(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_bzr_file_becomes_kind_2d(self):
        path = self._write_bzr("tavola.bzr",
                               "CANVAS w=50 h=50 bg=1\nPALETTE i=2 rgb=#FF0000\n"
                               "RECT x=5 y=5 w=10 h=10 color=2 fill=1\n")
        items = decode_bundle(encode_bundle_files([path]))
        self.assertEqual(items[0].kind, KIND_2D)
        self.assertEqual(items[0].label, "tavola.bzr")

    def test_bzp_payload_carried_verbatim(self):
        from balzar.payload import encode_payload
        text = "CANVAS w=20 h=20 bg=1\n"
        bzp_path = os.path.join(self.tmp, "tavola.bzp")
        with open(bzp_path, "wb") as fh:
            fh.write(encode_payload(text))
        items = decode_bundle(encode_bundle_files([bzp_path]))
        self.assertEqual(items[0].kind, KIND_2D)
        with open(bzp_path, "rb") as fh:
            self.assertEqual(items[0].data, fh.read())

    def test_bzr_with_unknown_instruction_rejected_with_filename(self):
        path = self._write_bzr("broken.bzr", "BOGUS x=1 y=2\n")
        with self.assertRaises(BundleError) as ctx:
            encode_bundle_files([path])
        self.assertIn("broken.bzr", str(ctx.exception))
        self.assertIn("BOGUS", str(ctx.exception))

    def test_bzp_with_bad_magic_rejected(self):
        bad_path = os.path.join(self.tmp, "fake.bzp")
        with open(bad_path, "wb") as fh:
            fh.write(b"not a real payload")
        with self.assertRaises(BundleError):
            encode_bundle_files([bad_path])

    def test_3d_plus_2d_bundle_has_both_kinds(self):
        xml_path = os.path.join(self.tmp, "assembly.3dxml")
        _write_fixture_3dxml(xml_path)
        bzr_path = self._write_bzr("tavola.bzr", "CANVAS w=20 h=20 bg=1\n")
        items = decode_bundle(encode_bundle_files([xml_path, bzr_path]))
        self.assertEqual([it.kind for it in items], [KIND_3D, KIND_2D])


class TestRender2DItem(unittest.TestCase):
    """viewer3d._render_2d_item: decodes a KIND_2D item and renders it
    fresh into doc-index entries with real image extensions, so the
    existing image-preview path in _DOC_JS/app.js picks them up with no
    new client-side branch (verified separately via Playwright)."""

    def test_single_frame_program_renders_png_and_svg(self):
        from balzar.viewer3d import _render_2d_item

        text = ("CANVAS w=40 h=40 bg=1\nPALETTE i=2 rgb=#FF0000\n"
                "RECT x=5 y=5 w=10 h=10 color=2 fill=1\n")
        from balzar.payload import encode_payload
        item = BundleItem(KIND_2D, "tavola.bzr", encode_payload(text))
        docs = _render_2d_item(item)
        labels = [d["label"] for d in docs]
        self.assertIn("tavola.png", labels)
        self.assertIn("tavola.svg", labels)
        for d in docs:
            self.assertGreater(len(d["b64"]), 0)

    def test_multi_frame_program_renders_gif_not_svg(self):
        from balzar.viewer3d import _render_2d_item

        text = ("CANVAS w=20 h=20 bg=1\nPALETTE i=2 rgb=#00AA00\n"
                "RECT x=1 y=1 w=5 h=5 color=2 fill=1\nFRAME\n"
                "RECT x=10 y=10 w=5 h=5 color=2 fill=1\nFRAME\n")
        from balzar.payload import encode_payload
        item = BundleItem(KIND_2D, "anim.bzr", encode_payload(text))
        docs = _render_2d_item(item)
        labels = [d["label"] for d in docs]
        self.assertIn("anim.gif", labels)
        self.assertNotIn("anim.svg", labels)  # multi-frame is out of svg.py's scope

    def test_svg_unsafe_program_renders_only_raster(self):
        from balzar.viewer3d import _render_2d_item

        # NOISE has no vector equivalent (balzar/svg.py rejects it)
        text = ("CANVAS w=20 h=20 bg=1\nPALETTE i=2 rgb=#FF0000\n"
                "REGION name=FULL x=0 y=0 w=20 h=20\nNOISE region=FULL color=2 density=0.5\n")
        from balzar.payload import encode_payload
        item = BundleItem(KIND_2D, "rumore.bzr", encode_payload(text))
        docs = _render_2d_item(item)
        labels = [d["label"] for d in docs]
        self.assertIn("rumore.png", labels)
        self.assertNotIn("rumore.svg", labels)


class TestBundleThroughQrCarrier(unittest.TestCase):
    """The core design claim: chunk_payload/assemble_chunks (and by the
    same logic payload_to_qr_frames/LiveScanner, which build on the same
    primitives) never need to know a bundle is inside -- they already
    treat any payload as opaque bytes with a CRC."""

    def test_bundle_survives_chunking_and_reassembly_unchanged(self):
        items = [
            BundleItem(KIND_3D, "assembly.b3d", os.urandom(5000)),
            BundleItem(KIND_CSV, "alarms.csv",
                      "".join(f"E{i},Part{i}\n" for i in range(200)).encode("utf-8")),
        ]
        original = encode_bundle(items)
        chunks = chunk_payload(original)
        self.assertGreater(len(chunks), 1, "test payload should need more than one chunk")
        import random
        shuffled = chunks[:]
        random.shuffle(shuffled)
        reassembled = assemble_chunks(shuffled)
        self.assertEqual(reassembled, original)
        self.assertEqual(decode_bundle(reassembled), decode_bundle(original))


if __name__ == "__main__":
    unittest.main()
