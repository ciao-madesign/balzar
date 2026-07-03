"""End-to-end checks on the example programs and the expansion factor."""

import os
import unittest

from balzar.interpreter import render
from balzar.payload import QR_V40_BINARY_CAPACITY, encode_payload
from balzar.png import png_bytes

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def load(name: str) -> str:
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return fh.read()


class TestExamples(unittest.TestCase):
    def test_all_examples_render_and_fit_in_a_qr(self):
        for name in sorted(os.listdir(EXAMPLES)):
            if not name.endswith(".bzr"):
                continue
            with self.subTest(example=name):
                program = load(name)
                payload = encode_payload(program)
                result = render(program)
                self.assertGreater(len(result.frames), 0)
                self.assertLessEqual(len(payload), QR_V40_BINARY_CAPACITY,
                                     f"{name} payload does not fit in a QR")

    def test_pattern_tile_expansion_factor(self):
        program = load("pattern_tile.bzr")
        payload = encode_payload(program)
        result = render(program)
        factor = result.raw_rgb_size / len(payload)
        # 1024x1024 RGB from a few hundred bytes: > 1000x by construction
        self.assertGreater(factor, 1000)

    def test_animation_emits_24_frames(self):
        result = render(load("animazione.bzr"))
        self.assertEqual(len(result.frames), 24)
        self.assertNotEqual(result.frames[0], result.frames[23])

    def test_png_output_is_valid_signature(self):
        result = render(load("frattale.bzr"))
        data = png_bytes(result.width, result.height, result.frame_rgb(0))
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IEND", data)


if __name__ == "__main__":
    unittest.main()
