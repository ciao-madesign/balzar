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


if __name__ == "__main__":
    unittest.main()
