"""Physical QR carrier: payload <-> printable image(s), one photo either way.

Skipped entirely if qrcode/pyzbar (+ system libzbar) aren't installed —
these are optional, desktop-only dependencies, not part of the core
engine (see balzar/qr.py docstring for why raw bytes aren't used).
"""

import io
import unittest

try:
    import qrcode  # noqa: F401
    from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
    HAVE_QR_DEPS = True
except ImportError:
    HAVE_QR_DEPS = False

from balzar.payload import encode_payload


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestQRCarrier(unittest.TestCase):
    def test_small_payload_single_qr_roundtrip(self):
        from balzar.qr import payload_to_qr_image, scan_image_bytes
        payload = encode_payload("CANVAS w=32 h=32 bg=0\nFILL region=FULL color=3")
        img = payload_to_qr_image(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.assertEqual(scan_image_bytes(buf.getvalue()), payload)

    def test_large_payload_becomes_grid_and_roundtrips(self):
        from balzar.qr import CHUNK_RAW_BYTES, payload_to_qr_image, scan_image_bytes
        # force a payload bigger than one chunk: several KB of distinct
        # instructions defeat deflate, so the encoded payload stays large
        lines = ["CANVAS w=64 h=64 bg=0"]
        for i in range(2000):
            lines.append(f"SETPIX x={i % 64} y={(i * 7) % 64} color={i % 251}")
        payload = encode_payload("\n".join(lines))
        self.assertGreater(len(payload), CHUNK_RAW_BYTES)

        img = payload_to_qr_image(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.assertEqual(scan_image_bytes(buf.getvalue()), payload)

    def test_scan_rejects_image_with_no_qr(self):
        from PIL import Image

        from balzar.qr import scan_image_bytes
        blank = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        blank.save(buf, format="PNG")
        with self.assertRaises(ValueError):
            scan_image_bytes(buf.getvalue())


def _big_payload(n_lines=28000):
    lines = ["CANVAS w=64 h=64 bg=0"]
    for i in range(n_lines):
        lines.append(f"SETPIX x={i % 64} y={(i * 7) % 64} color={i % 251}")
    return encode_payload("\n".join(lines))


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestQRFrameSequence(unittest.TestCase):
    def test_small_payload_is_a_single_frame_no_label(self):
        from balzar.qr import payload_to_qr_frames
        payload = encode_payload("CANVAS w=32 h=32 bg=0\nFILL region=FULL color=3")
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertEqual(len(frames), 1)

    def test_grid_dim_caps_codes_per_frame(self):
        from balzar.qr import CHUNK_RAW_BYTES, payload_to_qr_frames
        payload = _big_payload()
        chunk_count = -(-len(payload) // CHUNK_RAW_BYTES)  # rough lower bound
        self.assertGreater(chunk_count, 16)

        frames_4 = payload_to_qr_frames(payload, grid_dim=4)
        frames_8 = payload_to_qr_frames(payload, grid_dim=8)
        # a tighter cap can only mean the same or more frames, never fewer
        self.assertGreaterEqual(len(frames_4), len(frames_8))
        self.assertGreater(len(frames_4), 1)

    def test_frame_sequence_roundtrips_via_live_scanner(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        scanner = LiveScanner()
        done = False
        for frame in frames:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            done, missing = scanner.add(buf.getvalue())
        self.assertTrue(done)
        self.assertEqual(scanner.result(), payload)

    def test_grid_dim_hint_gives_bit_identical_result(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        scanner = LiveScanner()
        for frame in frames:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            scanner.add(buf.getvalue(), grid_dim=4)
        self.assertEqual(scanner.result(), payload)

    def test_grid_dim_hint_falls_back_when_tiling_is_a_mismatch(self):
        # a single, un-gridded QR: the grid_dim=4 hint cannot possibly
        # apply (there's only one code, not 16) -- must still work via
        # the whole-image fallback, not silently find nothing
        from balzar.qr import LiveScanner, payload_to_qr_image
        payload = encode_payload("CANVAS w=16 h=16 bg=0\nFILL region=FULL color=2")
        img = payload_to_qr_image(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        scanner = LiveScanner()
        done, missing = scanner.add(buf.getvalue(), grid_dim=4)
        self.assertTrue(done)
        self.assertEqual(scanner.result(), payload)

    def test_scan_image_bytes_grid_dim_hint_matches_default(self):
        import math

        from balzar.payload import chunk_payload
        from balzar.qr import CHUNK_RAW_BYTES, payload_to_qr_image, scan_image_bytes
        payload = _big_payload()
        img = payload_to_qr_image(payload)  # one auto-sized grid, all chunks
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        n_chunks = len(chunk_payload(payload, chunk_size=CHUNK_RAW_BYTES))
        grid_dim_hint = math.ceil(math.sqrt(n_chunks))

        assembled_default = scan_image_bytes(buf.getvalue())
        assembled_hinted = scan_image_bytes(buf.getvalue(), grid_dim=grid_dim_hint)
        self.assertEqual(assembled_default, payload)
        self.assertEqual(assembled_hinted, payload)

    def test_live_scanner_accepts_frames_out_of_order_and_repeated(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 2)

        order = list(reversed(frames)) + [frames[0]]  # reversed, plus a repeat
        scanner = LiveScanner()
        for frame in order:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            scanner.add(buf.getvalue())
        self.assertEqual(scanner.result(), payload)

    def test_live_scanner_reports_missing_chunks_before_done(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        scanner = LiveScanner()
        buf = io.BytesIO()
        frames[0].save(buf, format="PNG")
        done, missing = scanner.add(buf.getvalue())
        self.assertFalse(done)
        self.assertTrue(missing)
        with self.assertRaises(ValueError):
            scanner.result()

    def test_gif_bundle_roundtrips_through_live_scanner(self):
        from balzar.qr import (LiveScanner, frames_to_gif, gif_to_frames,
                               payload_to_qr_frames)
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        gif_bytes = frames_to_gif(frames, duration_ms=200)
        replayed = gif_to_frames(gif_bytes)
        self.assertEqual(len(replayed), len(frames))

        scanner = LiveScanner()
        for frame in replayed:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            scanner.add(buf.getvalue())
        self.assertEqual(scanner.result(), payload)

    def test_file_bundle_roundtrips_through_live_scanner(self):
        import shutil
        import tempfile

        from balzar.qr import LiveScanner, frames_to_files, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        out_dir = tempfile.mkdtemp()
        try:
            paths = frames_to_files(frames, out_dir)
            self.assertEqual(len(paths), len(frames))
            scanner = LiveScanner()
            for path in paths:
                with open(path, "rb") as fh:
                    scanner.add(fh.read())
            self.assertEqual(scanner.result(), payload)
        finally:
            shutil.rmtree(out_dir)


if __name__ == "__main__":
    unittest.main()
