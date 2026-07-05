"""PNG writer: pixel-exact round-trip, and a regression guard for the
adaptive-filter regression found in session (tiled content got *larger*
under naive per-row adaptive filtering than under the old always-None
writer; fixed by keeping whichever of the two actually compresses smaller)."""

import io
import random
import struct
import unittest
import zlib

from PIL import Image

from balzar.png import png_bytes


def _solid(w, h, color=(7, 8, 9)):
    return bytes(color) * (w * h)


def _gradient(w, h):
    out = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            out[i] = x % 256
            out[i + 1] = y % 256
            out[i + 2] = (x + y) % 256
    return bytes(out)


def _tiled_rect_rows(w, h, tile=8):
    """Rows that repeat exactly every `tile` pixels vertically, the case
    that regressed under naive adaptive filtering."""
    out = bytearray(w * h * 3)
    colors = [(255, 0, 0), (0, 255, 0)]
    for y in range(h):
        color = colors[(y // tile) % 2]
        for x in range(w):
            i = (y * w + x) * 3
            out[i:i + 3] = bytes(color)
    return bytes(out)


def _noise(w, h, seed=1):
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(w * h * 3))


def _old_unfiltered_png_bytes(width, height, rgb):
    """The pre-session writer: filter type 0 (None) on every scanline,
    kept here only to measure against, not as production code."""
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class TestPngRoundtrip(unittest.TestCase):
    def _assert_pixel_exact(self, w, h, rgb):
        data = png_bytes(w, h, rgb)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        self.assertEqual(img.size, (w, h))
        self.assertEqual(img.tobytes(), rgb)

    def test_solid_color_roundtrip(self):
        self._assert_pixel_exact(32, 32, _solid(32, 32))

    def test_gradient_roundtrip(self):
        self._assert_pixel_exact(64, 64, _gradient(64, 64))

    def test_tiled_rows_roundtrip(self):
        self._assert_pixel_exact(64, 64, _tiled_rect_rows(64, 64))

    def test_noise_roundtrip(self):
        self._assert_pixel_exact(48, 48, _noise(48, 48))

    def test_gradient_benefits_from_adaptive_filtering(self):
        """The case adaptive filtering exists for: smooth per-pixel
        change makes Sub/Paeth outputs near-constant, far smaller after
        DEFLATE than the unfiltered stream."""
        w, h = 256, 256
        rgb = _gradient(w, h)
        adaptive = png_bytes(w, h, rgb)
        old = _old_unfiltered_png_bytes(w, h, rgb)
        self.assertLess(len(adaptive), len(old) // 10)

    def test_never_regresses_versus_unfiltered(self):
        """The regression found in session: naive per-row adaptive
        filtering made a tiled/repeating-row image *larger* than the old
        always-None writer, because it broke the row-to-row byte
        identity DEFLATE was matching. png_bytes must never be worse."""
        for name, rgb, w, h in [
            ("tiled_rows", _tiled_rect_rows(128, 128), 128, 128),
            ("solid", _solid(32, 32), 32, 32),
            ("noise", _noise(64, 64), 64, 64),
            ("gradient", _gradient(64, 64), 64, 64),
        ]:
            with self.subTest(case=name):
                adaptive = png_bytes(w, h, rgb)
                old = _old_unfiltered_png_bytes(w, h, rgb)
                self.assertLessEqual(len(adaptive), len(old))


if __name__ == "__main__":
    unittest.main()
