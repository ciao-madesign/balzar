"""Encoder correctness: lossless round-trip on structured images, honest
behaviour (no crash, no false compression claim) on unstructured ones."""

import random
import unittest

from balzar.encoder import encode_image
from balzar.interpreter import render
from balzar.payload import decode_payload


def _solid_blocks(w: int, h: int) -> bytes:
    """Four flat-colored quadrants: the easiest possible case."""
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    out = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            q = (2 if y >= h // 2 else 0) + (1 if x >= w // 2 else 0)
            out[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(colors[q])
    return bytes(out)


def _checkerboard_tile(w: int, h: int, tile: int = 8) -> bytes:
    out = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            on = ((x // tile) + (y // tile)) % 2 == 0
            color = (10, 10, 10) if on else (240, 240, 240)
            out[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(color)
    return bytes(out)


def _noise(w: int, h: int, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(w * h * 3))


def _pixels_from_rgb(width, height, rgb):
    return [(rgb[i * 3] << 16) | (rgb[i * 3 + 1] << 8) | rgb[i * 3 + 2]
            for i in range(width * height)]


class TestEncoderRoundtrip(unittest.TestCase):
    def _assert_lossless_roundtrip(self, w, h, rgb):
        result = encode_image(w, h, rgb)
        self.assertTrue(result.lossless)
        decoded_text = decode_payload(result.payload)
        rendered = render(decoded_text)
        got = rendered.frame_rgb(0)
        self.assertEqual(_pixels_from_rgb(w, h, got), _pixels_from_rgb(w, h, rgb))
        return result

    def test_solid_blocks_lossless_and_tiny(self):
        rgb = _solid_blocks(64, 64)
        result = self._assert_lossless_roundtrip(64, 64, rgb)
        raw_size = 64 * 64 * 3
        self.assertLess(len(result.payload), raw_size // 50)

    def test_checkerboard_detected_as_tile(self):
        rgb = _checkerboard_tile(128, 128, tile=8)
        result = self._assert_lossless_roundtrip(128, 128, rgb)
        self.assertIsNotNone(result.tile)
        # a tiled 16x16 repeating unit should compress far below raw size
        self.assertLess(len(result.payload), 500)

    def test_single_color_image(self):
        rgb = bytes([7, 8, 9] * (32 * 32))
        result = self._assert_lossless_roundtrip(32, 32, rgb)
        self.assertLessEqual(result.palette_size, 1)

    def test_noise_gives_no_compression_gain(self):
        w, h = 48, 48
        rgb = _noise(w, h)
        result = encode_image(w, h, rgb)
        # must still decode and render without raising, even if lossy/huge
        rendered = render(decode_payload(result.payload))
        self.assertEqual((rendered.width, rendered.height), (w, h))
        raw_size = w * h * 3
        # honesty check: unstructured input must NOT report a compression win
        self.assertGreaterEqual(len(result.payload), raw_size * 0.5)

    def test_more_than_256_colors_is_disclosed_as_lossy(self):
        w, h = 20, 20
        rng = random.Random(0)
        seen = set()
        while len(seen) < w * h:
            seen.add((rng.randrange(256), rng.randrange(256), rng.randrange(256)))
        rgb = b"".join(bytes(c) for c in seen)
        result = encode_image(w, h, rgb)
        self.assertFalse(result.lossless)
        # still must render without raising
        rendered = render(decode_payload(result.payload))
        self.assertEqual(rendered.width, w)


if __name__ == "__main__":
    unittest.main()
