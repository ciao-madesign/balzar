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

from balzar.bundle import (KIND_3D, KIND_CSV, BundleError, BundleItem,
                           decode_bundle, encode_bundle, encode_bundle_files,
                           is_bundle)
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

    def test_3dxml_plus_csv_bundle_decodes_to_both(self):
        data = encode_bundle_files([self.xml_path, self.csv_path])
        items = decode_bundle(data)
        self.assertEqual([it.kind for it in items], [KIND_3D, KIND_CSV])
        scene = decode_scene(items[0].data)
        bom = generate_bom(scene)
        self.assertEqual(sorted(e.name for e in bom), ["PartA", "PartB"])
        csv_text = items[1].data.decode("utf-8")
        self.assertIn("E100,PartA", csv_text)

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

    def test_unsupported_extension_rejected_with_filename(self):
        bad_path = os.path.join(self.tmp, "drawing.pdf")
        with open(bad_path, "wb") as fh:
            fh.write(b"%PDF-1.4 fake")
        with self.assertRaises(BundleError) as ctx:
            encode_bundle_files([self.xml_path, bad_path])
        self.assertIn("drawing.pdf", str(ctx.exception))

    def test_invalid_3dxml_error_includes_filename(self):
        bad_xml = os.path.join(self.tmp, "broken.3dxml")
        with open(bad_xml, "wb") as fh:
            fh.write(b"not a zip file")
        with self.assertRaises(BundleError) as ctx:
            encode_bundle_files([bad_xml])
        self.assertIn("broken.3dxml", str(ctx.exception))


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
