"""SVG export: works for the vector-safe DSL subset, refuses everything
else with a clear reason instead of silently rasterizing or guessing."""

import os
import unittest
import xml.etree.ElementTree as ET

from balzar.svg import UnsupportedForSVG, render_svg

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def load(name: str) -> str:
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return fh.read()


class TestSvgSupportedCases(unittest.TestCase):
    def test_etichetta_bom_is_valid_xml_and_exports(self):
        svg = render_svg(load("etichetta_bom.bzr"))
        root = ET.fromstring(svg)  # raises if not well-formed XML
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("<text", svg)   # BOM labels became real text
        self.assertIn("<circle", svg)  # bolts/flange stayed real circles

    def test_schema_tecnico_uses_copy_for_bolts(self):
        svg = render_svg(load("schema_tecnico.bzr"))
        ET.fromstring(svg)
        # 8 bolts via COPY -> 8 translated <g> groups, each with its own circle
        self.assertGreaterEqual(svg.count("<g transform="), 8)

    def test_simple_rect_and_circle(self):
        prog = ("CANVAS w=20 h=20 bg=1\n"
                "RECT x=1 y=1 w=5 h=5 color=2 fill=1\n"
                "CIRCLE cx=10 cy=10 r=4 color=3 fill=0\n")
        svg = render_svg(prog)
        ET.fromstring(svg)
        self.assertIn('width="20"', svg)
        self.assertIn('fill="none"', svg)  # unfilled circle

    def test_tile_becomes_svg_pattern(self):
        prog = ("CANVAS w=16 h=16 bg=0\n"
                "REGION name=T x=0 y=0 w=4 h=4\n"
                "RECT x=0 y=0 w=4 h=4 color=1 fill=1\n"
                "TILE src=T dst=FULL\n")
        svg = render_svg(prog)
        ET.fromstring(svg)
        self.assertIn("<pattern", svg)
        self.assertIn("patternUnits", svg)


class TestSvgRefusesUnsafeOps(unittest.TestCase):
    def _assert_refused(self, prog: str, mentions: str):
        with self.assertRaises(UnsupportedForSVG) as ctx:
            render_svg(prog)
        self.assertIn(mentions, str(ctx.exception))

    def test_refuses_fractal(self):
        self._assert_refused(load("frattale.bzr"), "FRACTAL")

    def test_refuses_shift_and_noise(self):
        self._assert_refused(load("pattern_tile.bzr"), "SHIFT")

    def test_refuses_setpix(self):
        self._assert_refused(load("animazione.bzr"), "SETPIX")

    def test_refuses_multi_frame(self):
        self._assert_refused(load("esploso_industriale.bzr"), "piu' di un frame")

    def test_refuses_rotate_mirror_scale(self):
        base = "CANVAS w=8 h=8 bg=0\nREGION name=A x=0 y=0 w=4 h=4\n"
        for op in ("ROTATE region=A angle=90", "MIRROR region=A axis=x",
                   "SCALE src=A dst=A"):
            with self.assertRaises(UnsupportedForSVG):
                render_svg(base + op)

    def test_error_names_unsupported_op(self):
        with self.assertRaises(UnsupportedForSVG) as ctx:
            render_svg("CANVAS w=8 h=8 bg=0\nNOISE region=FULL color=1 density=0.1")
        self.assertIn("NOISE", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
