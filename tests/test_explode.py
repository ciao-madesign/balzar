"""Automatic CAD explosion (explode.py): layer-based grouping, radial
movement, and the honest refusals when there's nothing to explode."""

import os
import tempfile
import unittest

from balzar.explode import ExplodeError, explode_vector_file
from balzar.interpreter import render

DXF_MULTI_LAYER = """0
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
BULLONE-N
62
1
10
200.0
20
340.0
40
15.0
0
CIRCLE
8
BULLONE-E
62
1
10
340.0
20
200.0
40
15.0
0
ENDSEC
0
EOF
"""

DXF_SINGLE_LAYER = """0
SECTION
2
ENTITIES
0
CIRCLE
8
0
62
1
10
200.0
20
200.0
40
150.0
0
LINE
8
0
62
1
10
0.0
20
0.0
11
50.0
21
50.0
0
ENDSEC
0
EOF
"""


class TestExplode(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_frame_count_is_steps_plus_one(self):
        path = self._write("multi.dxf", DXF_MULTI_LAYER)
        result = explode_vector_file(path, steps=4)
        self.assertEqual(result.frame_count, 5)
        rendered = render(result.program_text)
        self.assertEqual(len(rendered.frames), 5)

    def test_group_count_matches_layers(self):
        path = self._write("multi.dxf", DXF_MULTI_LAYER)
        result = explode_vector_file(path, steps=3)
        self.assertEqual(result.group_count, 3)

    def test_single_layer_file_is_rejected_not_guessed(self):
        path = self._write("single.dxf", DXF_SINGLE_LAYER)
        with self.assertRaises(ExplodeError):
            explode_vector_file(path, steps=3)

    def test_last_frame_ink_spreads_wider_than_first(self):
        path = self._write("multi.dxf", DXF_MULTI_LAYER)
        result = explode_vector_file(path, steps=4, spacing=0.6)
        rendered = render(result.program_text)

        def ink_row_span(rgb):
            w, h = rendered.width, rendered.height
            rows = [y for y in range(h)
                    if any(tuple(rgb[(y * w + x) * 3:(y * w + x) * 3 + 3]) != (255, 255, 255)
                          for x in range(w))]
            return min(rows), max(rows)

        first_top, first_bottom = ink_row_span(rendered.frame_rgb(0))
        last_top, last_bottom = ink_row_span(rendered.frame_rgb(result.frame_count - 1))
        self.assertLess(last_top, first_top)
        self.assertGreater(last_bottom, first_bottom)

    def test_payload_roundtrip_is_deterministic(self):
        path = self._write("multi.dxf", DXF_MULTI_LAYER)
        r1 = explode_vector_file(path, steps=3)
        r2 = explode_vector_file(path, steps=3)
        self.assertEqual(r1.payload, r2.payload)

    def test_invalid_steps_rejected(self):
        path = self._write("multi.dxf", DXF_MULTI_LAYER)
        with self.assertRaises(ExplodeError):
            explode_vector_file(path, steps=0)


if __name__ == "__main__":
    unittest.main()
