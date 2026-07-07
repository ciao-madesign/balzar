"""balzar/raw_qr_logic.py's pure logic (no Tkinter here — same principle
already followed for balzar/library.py: widget interaction is verified
manually under Xvfb, not in unittest, but the file/QR logic underneath
it is plain Python and testable directly).

Skipped entirely if qrcode/pyzbar (+ system libzbar) aren't installed,
same as tests/test_qr.py.
"""

import os
import tempfile
import unittest

try:
    import qrcode  # noqa: F401
    from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
    HAVE_QR_DEPS = True
except ImportError:
    HAVE_QR_DEPS = False


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestRawQrGuiLogic(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _path(self, name):
        return os.path.join(self.tmpdir.name, name)

    def test_encode_then_assemble_roundtrip_on_arbitrary_bytes(self):
        from balzar.raw_qr_logic import RawQrAssembler, encode_file_to_qr_frames

        original = bytes(range(256)) * 20  # 5120 B, not a balzar payload at all
        src = self._path("arbitrary.bin")
        with open(src, "wb") as fh:
            fh.write(original)

        out_dir = self._path("qr_out")
        n_frames, n_bytes = encode_file_to_qr_frames(src, grid_dim=2, out_dir=out_dir)
        self.assertEqual(n_bytes, len(original))
        self.assertGreaterEqual(n_frames, 1)
        frame_files = sorted(f for f in os.listdir(out_dir) if f.endswith(".png"))
        self.assertEqual(len(frame_files), n_frames)

        assembler = RawQrAssembler()
        complete, missing = False, None
        for f in frame_files:
            complete, missing = assembler.add_image(os.path.join(out_dir, f), grid_dim=2)
        self.assertTrue(complete, f"still missing: {missing}")
        self.assertEqual(assembler.result(), original)

    def test_assembler_ignores_a_path_already_processed(self):
        from balzar.raw_qr_logic import RawQrAssembler, encode_file_to_qr_frames

        original = b"hello raw qr transport" * 5
        src = self._path("small.bin")
        with open(src, "wb") as fh:
            fh.write(original)
        out_dir = self._path("qr_out")
        n_frames, _ = encode_file_to_qr_frames(src, grid_dim=1, out_dir=out_dir)
        self.assertEqual(n_frames, 1)
        frame_path = os.path.join(out_dir, os.listdir(out_dir)[0])

        assembler = RawQrAssembler()
        first = assembler.add_image(frame_path)
        second = assembler.add_image(frame_path)  # same path again: no-op, not a re-read
        self.assertEqual(first, second)
        self.assertTrue(first[0])
        self.assertEqual(assembler.result(), original)

    def test_partial_scan_reports_missing_chapters(self):
        from balzar.raw_qr_logic import RawQrAssembler, encode_file_to_qr_frames

        original = bytes(range(256)) * 60  # forces several chunks/frames
        src = self._path("bigger.bin")
        with open(src, "wb") as fh:
            fh.write(original)
        out_dir = self._path("qr_out")
        n_frames, _ = encode_file_to_qr_frames(src, grid_dim=2, out_dir=out_dir)
        self.assertGreater(n_frames, 1, "test needs more than one frame to prove partial scan works")
        frame_files = sorted(f for f in os.listdir(out_dir) if f.endswith(".png"))

        assembler = RawQrAssembler()
        complete, missing = assembler.add_image(os.path.join(out_dir, frame_files[0]), grid_dim=2)
        self.assertFalse(complete)
        self.assertTrue(missing)
        with self.assertRaises(ValueError):
            assembler.result()


if __name__ == "__main__":
    unittest.main()
