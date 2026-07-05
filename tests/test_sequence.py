"""Multi-file sequences (sequence.py): vector-delta and raster-delta paths,
mixed-format rejection, and the single-file guard."""

import os
import tempfile
import unittest

from balzar.interpreter import render
from balzar.sequence import (SequenceError, encode_independent,
                             encode_raster_sequence, encode_vector_sequence)

DXF_STEP1 = """0
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

SVG_STEP1 = """<svg width="200" height="200">
  <circle cx="100" cy="100" r="80" fill="none" stroke="#000000"/>
</svg>"""

SVG_STEP2 = """<svg width="200" height="200">
  <circle cx="100" cy="100" r="80" fill="none" stroke="#000000"/>
  <circle cx="100" cy="100" r="30" fill="#c0392b"/>
</svg>"""


class TestVectorSequence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_dxf_sequence_has_one_frame_per_file(self):
        p1 = self._write("s1.dxf", DXF_STEP1)
        p2 = self._write("s2.dxf", DXF_STEP2)
        result = encode_vector_sequence([p1, p2])
        self.assertEqual(result.frame_count, 2)
        rendered = render(result.program_text)
        self.assertEqual(len(rendered.frames), 2)

    def test_unchanged_geometry_is_deduped_across_frames(self):
        # the outer circle is identical in both steps: it must be emitted
        # only once, not twice
        p1 = self._write("s1.dxf", DXF_STEP1)
        p2 = self._write("s2.dxf", DXF_STEP2)
        result = encode_vector_sequence([p1, p2])
        self.assertEqual(result.program_text.count("r=400"), 1)

    def test_svg_sequence_also_works(self):
        p1 = self._write("s1.svg", SVG_STEP1)
        p2 = self._write("s2.svg", SVG_STEP2)
        result = encode_vector_sequence([p1, p2])
        self.assertEqual(result.frame_count, 2)
        self.assertEqual(result.source_format, "svg")

    def test_mixed_formats_rejected(self):
        p1 = self._write("s1.dxf", DXF_STEP1)
        p2 = self._write("s2.svg", SVG_STEP2)
        with self.assertRaises(SequenceError):
            encode_vector_sequence([p1, p2])

    def test_single_file_rejected(self):
        p1 = self._write("s1.dxf", DXF_STEP1)
        with self.assertRaises(SequenceError):
            encode_vector_sequence([p1])

    def test_payload_roundtrip_is_deterministic(self):
        p1 = self._write("s1.dxf", DXF_STEP1)
        p2 = self._write("s2.dxf", DXF_STEP2)
        r1 = encode_vector_sequence([p1, p2])
        r2 = encode_vector_sequence([p1, p2])
        self.assertEqual(r1.payload, r2.payload)


class TestRasterSequence(unittest.TestCase):
    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow non installato")
        self.Image = Image
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _make_png(self, name, offset):
        path = os.path.join(self.tmpdir.name, name)
        img = self.Image.new("RGB", (60, 40), (255, 255, 255))
        for x in range(10 + offset, 25 + offset):
            for y in range(10, 25):
                img.putpixel((x, y), (200, 0, 0))
        img.save(path)
        return path

    def test_three_stills_become_one_delta_video(self):
        p0 = self._make_png("f0.png", 0)
        p1 = self._make_png("f1.png", 5)
        p2 = self._make_png("f2.png", 10)
        result = encode_raster_sequence([p0, p1, p2])
        self.assertEqual(result.frame_count, 3)
        self.assertTrue(result.lossless)
        self.assertGreater(result.delta_pixels_total, 0)

    def test_single_file_rejected(self):
        p0 = self._make_png("f0.png", 0)
        with self.assertRaises(SequenceError):
            encode_raster_sequence([p0])


class TestIndependentBatch(unittest.TestCase):
    """encode_independent: each file stands on its own — no shared canvas,
    no format restriction, and (unlike the sequence functions) one broken
    file must not sink the whole batch."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_mixed_svg_and_dxf_both_succeed(self):
        p1 = self._write("a.svg", SVG_STEP1)
        p2 = self._write("b.dxf", DXF_STEP1)
        results = encode_independent([p1, p2])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(results[0].source_format, "svg")
        self.assertEqual(results[1].source_format, "dxf")

    def test_broken_file_does_not_sink_the_batch(self):
        good = self._write("good.dxf", DXF_STEP1)
        bad = self._write("bad.svg", "<svg><circle cx=oops></svg>")
        results = encode_independent([good, bad])
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)
        self.assertTrue(results[1].error)

    def test_single_file_is_allowed(self):
        p1 = self._write("a.dxf", DXF_STEP1)
        results = encode_independent([p1])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)

    def test_no_files_rejected(self):
        with self.assertRaises(SequenceError):
            encode_independent([])

    def test_raster_file_routed_to_image_encoder(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow non installato")
        path = os.path.join(self.tmpdir.name, "f.png")
        img = Image.new("RGB", (40, 30), (255, 255, 255))
        for x in range(10, 20):
            for y in range(10, 20):
                img.putpixel((x, y), (0, 0, 200))
        img.save(path)
        results = encode_independent([path])
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].source_format, "raster")


if __name__ == "__main__":
    unittest.main()
