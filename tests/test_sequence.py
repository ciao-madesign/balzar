"""Multi-file sequences (sequence.py): vector-delta and raster-delta paths,
mixed-format rejection, and the single-file guard."""

import os
import tempfile
import unittest

from balzar.interpreter import render
from balzar.sequence import (SequenceError, encode_raster_sequence,
                             encode_vector_sequence)

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


if __name__ == "__main__":
    unittest.main()
