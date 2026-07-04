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

    def test_near_256_colors_gets_fine_rounding_not_coarse_fallback(self):
        """The case that motivated this: anti-aliased UI screenshots land
        just over 256 colors and deserve +-1/+-2 per channel, not the old
        crude fixed 3-3-2 palette (+-16/+-32)."""
        w, h = 24, 24
        rng = random.Random(1)
        base = [(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                for _ in range(100)]
        colors = []
        for _ in range(w * h):
            r, g, b = rng.choice(base)
            # small jitter, like anti-aliasing shades of a few base colors:
            # independent per-pixel draw -> up to 100*6=600 exact colors
            # (well over 256), but only ~6x the buckets once rounded
            jitter = rng.randrange(6)
            colors.append(((r + jitter) % 256, (g + jitter) % 256, (b + jitter) % 256))
        rgb = b"".join(bytes(c) for c in colors)

        exact_colors = len(set(colors))
        self.assertGreater(exact_colors, 256, "test setup should exceed 256 exact colors")

        result = encode_image(w, h, rgb)
        self.assertFalse(result.lossless)
        self.assertGreater(result.color_step, 0, "should use graduated rounding")
        self.assertLessEqual(result.color_step, 16, "jitter of 5 should need only fine rounding")
        self.assertIn("arrotondamento colore", result.fidelity_label())

        rendered = render(decode_payload(result.payload))
        self.assertEqual((rendered.width, rendered.height), (w, h))

    def test_true_high_entropy_still_needs_the_coarsest_step(self):
        """Genuine noise must NOT be reported as finely quantized — it
        should need the coarsest rounding step, same ballpark as the old
        fixed 3-3-2 fallback, and say so ('quantizzata grezza')."""
        w, h = 24, 24
        rng = random.Random(2)
        rgb = bytes(rng.randrange(256) for _ in range(w * h * 3))
        result = encode_image(w, h, rgb)
        self.assertFalse(result.lossless)
        self.assertGreaterEqual(result.color_step, 48)
        self.assertIn("grezza", result.fidelity_label())

    def test_exact_palette_has_zero_color_step(self):
        result = encode_image(32, 32, _solid_blocks(32, 32))
        self.assertTrue(result.lossless)
        self.assertEqual(result.color_step, 0)
        self.assertEqual(result.fidelity_label(), "esatta (lossless)")


if __name__ == "__main__":
    unittest.main()
