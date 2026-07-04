"""Vector ingestion (SVG/DXF -> DSL): correctness on the common CAD
entities, and honest reporting of what gets skipped and why."""

import unittest

from balzar.interpreter import render
from balzar.payload import decode_payload
from balzar.vectorio import VectorIngestError, ingest_dxf, ingest_svg

SVG_FLANGE = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400">
  <circle cx="200" cy="200" r="150" fill="none" stroke="#000000"/>
  <circle cx="200" cy="200" r="60" fill="none" stroke="#000000"/>
  <g transform="translate(200,200)">
    <circle cx="0" cy="-120" r="10" fill="#c0392b"/>
    <circle cx="120" cy="0" r="10" fill="#c0392b"/>
  </g>
  <line x1="20" y1="200" x2="380" y2="200" stroke="#888888"/>
  <text x="130" y="395" font-size="18" fill="#000000">FLANGIA FL-100</text>
</svg>"""

DXF_FLANGE = """0
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
8
10
20.0
20
200.0
11
380.0
21
200.0
0
LWPOLYLINE
8
0
62
3
90
4
70
1
10
50.0
20
50.0
10
350.0
20
50.0
10
350.0
20
350.0
10
50.0
20
350.0
0
TEXT
8
0
62
7
10
60.0
20
15.0
40
14.0
1
FLANGIA DXF
0
ENDSEC
0
EOF
"""


class TestSvgIngestion(unittest.TestCase):
    def test_flange_converts_everything(self):
        result = ingest_svg(SVG_FLANGE)
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.element_count, 6)  # 2 circles + 2 in <g> + 1 line + 1 text
        rendered = render(result.program_text)
        self.assertEqual((rendered.width, rendered.height), (result.width, result.height))

    def test_text_is_real_text_op_not_rects(self):
        result = ingest_svg(SVG_FLANGE)
        self.assertIn("TEXT ", result.program_text)
        self.assertIn("FLANGIA FL-100", result.program_text)

    def test_group_translate_applied(self):
        # the two circles inside <g transform="translate(200,200)"> must
        # not land at their raw (0,-120)/(120,0) coordinates
        result = ingest_svg(SVG_FLANGE)
        self.assertNotIn("cx=0 cy=-120", result.program_text.replace(" ", ""))

    def test_payload_roundtrip_is_deterministic(self):
        r1 = ingest_svg(SVG_FLANGE)
        r2 = ingest_svg(SVG_FLANGE)
        self.assertEqual(r1.payload, r2.payload)
        restored = render(decode_payload(r1.payload))
        self.assertEqual(restored.frames, render(r1.program_text).frames)

    def test_unsupported_curve_path_is_skipped_not_dropped_silently(self):
        svg = """<svg width="100" height="100">
          <path d="M10,10 C20,20 40,20 50,10" stroke="#000"/>
          <circle cx="50" cy="50" r="10" fill="#f00"/>
        </svg>"""
        result = ingest_svg(svg)
        self.assertEqual(result.element_count, 1)  # only the circle
        self.assertTrue(any("curve" in s for s in result.skipped))

    def test_unsupported_transform_is_skipped(self):
        svg = """<svg width="100" height="100">
          <g transform="rotate(45)"><circle cx="10" cy="10" r="5" fill="#000"/></g>
          <circle cx="50" cy="50" r="10" fill="#f00"/>
        </svg>"""
        result = ingest_svg(svg)
        self.assertEqual(result.element_count, 1)
        self.assertTrue(any("transform" in s for s in result.skipped))

    def test_invalid_xml_raises_clear_error(self):
        with self.assertRaises(VectorIngestError):
            ingest_svg("<svg><circle cx=oops></svg>")

    def test_white_background_regardless_of_first_color_seen(self):
        # regression: CANVAS bg must always be white, not whatever color
        # happened to be assigned palette index 1 first
        result = ingest_svg(SVG_FLANGE)
        rendered = render(result.program_text)
        corner = rendered.frame_rgb(0)[0:3]
        self.assertEqual(corner, bytes((255, 255, 255)))


class TestDxfIngestion(unittest.TestCase):
    def test_flange_converts_everything(self):
        result = ingest_dxf(DXF_FLANGE)
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.element_count, 4)

    def test_aci_colors_resolved(self):
        result = ingest_dxf(DXF_FLANGE)
        self.assertIn("#FF0000", result.program_text)  # ACI 1 = red

    def test_text_entity_becomes_text_op(self):
        result = ingest_dxf(DXF_FLANGE)
        self.assertIn("FLANGIA DXF", result.program_text)

    def test_y_axis_is_flipped(self):
        # DXF Y grows upward; a point near the DXF-world top must land
        # near the top of the pixel canvas (small pixel-y), not the bottom
        result = ingest_dxf(DXF_FLANGE)
        rendered = render(result.program_text)
        top_rows_have_ink = any(
            v != 0 for v in rendered.frames[0][:rendered.width * 5])
        self.assertTrue(top_rows_have_ink)

    def test_unknown_aci_color_is_disclosed(self):
        dxf = """0
SECTION
2
ENTITIES
0
CIRCLE
8
0
62
200
10
10.0
20
10.0
40
5.0
0
ENDSEC
0
EOF
"""
        result = ingest_dxf(dxf)
        self.assertTrue(any("ACI 200" in s for s in result.skipped))

    def test_unsupported_entity_is_skipped(self):
        dxf = """0
SECTION
2
ENTITIES
0
ARC
8
0
10
10.0
20
10.0
40
5.0
50
0.0
51
90.0
0
CIRCLE
8
0
10
10.0
20
10.0
40
5.0
0
ENDSEC
0
EOF
"""
        result = ingest_dxf(dxf)
        self.assertEqual(result.element_count, 1)
        self.assertTrue(any("ARC" in s for s in result.skipped))

    def test_missing_entities_section_raises(self):
        with self.assertRaises(VectorIngestError):
            ingest_dxf("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n")

    def test_no_convertible_entities_raises(self):
        dxf = """0
SECTION
2
ENTITIES
0
ARC
8
0
10
10.0
20
10.0
40
5.0
50
0.0
51
90.0
0
ENDSEC
0
EOF
"""
        with self.assertRaises(VectorIngestError):
            ingest_dxf(dxf)


if __name__ == "__main__":
    unittest.main()
