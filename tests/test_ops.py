"""Unit checks for the individual transformation operations."""

import unittest

from balzar.interpreter import render


def run(body: str, w: int = 8, h: int = 8):
    return render(f"CANVAS w={w} h={h} bg=0\n{body}")


class TestGeometric(unittest.TestCase):
    def test_shift_wrap(self):
        r = run("SETPIX x=0 y=0 color=1\n"
                "REGION name=A x=0 y=0 w=4 h=4\n"
                "SHIFT region=A dx=1 dy=2 wrap=1")
        frame = r.frames[0]
        self.assertEqual(frame[2 * 8 + 1], 1)
        self.assertEqual(frame[0], 0)

    def test_shift_no_wrap_fills(self):
        r = run("REGION name=A x=0 y=0 w=4 h=4\n"
                "FILL region=A color=3\n"
                "SHIFT region=A dx=2 dy=0 wrap=0 fill=9")
        frame = r.frames[0]
        self.assertEqual(frame[0], 9)   # vacated
        self.assertEqual(frame[3], 3)   # moved content

    def test_rotate_90(self):
        r = run("SETPIX x=0 y=0 color=1\n"
                "REGION name=A x=0 y=0 w=4 h=4\n"
                "ROTATE region=A angle=90")
        frame = r.frames[0]
        # top-left corner goes to top-right under a clockwise quarter turn
        self.assertEqual(frame[3], 1)
        self.assertEqual(frame[0], 0)

    def test_rotate_four_times_is_identity(self):
        base = ("REGION name=A x=1 y=1 w=5 h=5\n"
                "SETPIX x=2 y=1 color=7\nSETPIX x=3 y=4 color=2\n")
        r0 = run(base)
        r4 = run(base + "ROTATE region=A angle=90\n" * 4)
        self.assertEqual(r0.frames, r4.frames)

    def test_rotate_rejects_non_square_quarter(self):
        with self.assertRaises(ValueError):
            run("REGION name=A x=0 y=0 w=4 h=2\nROTATE region=A angle=90")

    def test_mirror_twice_is_identity(self):
        base = "SETPIX x=1 y=2 color=5\nREGION name=A x=0 y=0 w=8 h=8\n"
        r0 = run(base)
        r2 = run(base + "MIRROR region=A axis=x\nMIRROR region=A axis=x")
        self.assertEqual(r0.frames, r2.frames)

    def test_mirror_x(self):
        r = run("SETPIX x=0 y=0 color=1\n"
                "REGION name=A x=0 y=0 w=8 h=8\n"
                "MIRROR region=A axis=x")
        self.assertEqual(r.frames[0][7], 1)

    def test_scale_nearest_neighbour(self):
        r = run("REGION name=S x=0 y=0 w=2 h=2\n"
                "REGION name=D x=0 y=4 w=4 h=4\n"
                "SETPIX x=0 y=0 color=9\n"
                "SCALE src=S dst=D")
        frame = r.frames[0]
        # the 9 in src (0,0) expands to the 2x2 top-left block of dst
        self.assertEqual(frame[4 * 8 + 0], 9)
        self.assertEqual(frame[5 * 8 + 1], 9)
        self.assertEqual(frame[6 * 8 + 2], 0)


class TestStructural(unittest.TestCase):
    def test_copy_and_swap(self):
        r = run("REGION name=A x=0 y=0 w=2 h=2\n"
                "REGION name=B x=4 y=0 w=2 h=2\n"
                "FILL region=A color=1\nFILL region=B color=2\n"
                "SWAP a=A b=B")
        frame = r.frames[0]
        self.assertEqual(frame[0], 2)
        self.assertEqual(frame[4], 1)

    def test_copy_size_mismatch_raises(self):
        with self.assertRaises(ValueError):
            run("REGION name=A x=0 y=0 w=2 h=2\n"
                "REGION name=B x=4 y=0 w=3 h=2\n"
                "COPY src=A dst=B")

    def test_tile_repeats_pattern(self):
        r = run("REGION name=T x=0 y=0 w=2 h=2\n"
                "SETPIX x=0 y=0 color=5\n"
                "TILE src=T dst=FULL")
        frame = r.frames[0]
        for y in range(0, 8, 2):
            for x in range(0, 8, 2):
                self.assertEqual(frame[y * 8 + x], 5)
                self.assertEqual(frame[y * 8 + x + 1], 0)


class TestText(unittest.TestCase):
    def test_text_draws_known_glyph(self):
        # 'I' is a solid vertical bar with top/bottom serifs: column 2 of
        # its 5x7 cell must be fully lit, columns 0 and 4 only at the caps
        r = run("TEXT x=0 y=0 text=\"I\" color=1", w=8, h=8)
        frame = r.frames[0]
        for row in range(7):
            self.assertEqual(frame[row * 8 + 2], 1, f"row {row} col 2")

    def test_unknown_char_renders_solid_fallback_not_silently(self):
        r = run("TEXT x=0 y=0 text=\"@\" color=1", w=8, h=8)
        frame = r.frames[0]
        # fallback glyph is a fully solid 5x7 block
        for row in range(7):
            for col in range(5):
                self.assertEqual(frame[row * 8 + col], 1)

    def test_scale_multiplies_glyph_size(self):
        r1 = run("TEXT x=0 y=0 text=\"1\" color=1 scale=1", w=20, h=20)
        r2 = run("TEXT x=0 y=0 text=\"1\" color=1 scale=2", w=20, h=20)
        count1 = sum(1 for v in r1.frames[0] if v == 1)
        count2 = sum(1 for v in r2.frames[0] if v == 1)
        self.assertEqual(count2, count1 * 4)

    def test_cursor_advances_between_characters(self):
        r = run("TEXT x=0 y=0 text=\"II\" color=1", w=20, h=8)
        frame = r.frames[0]
        # second 'I' starts 6 columns after the first (5 wide + 1 spacing)
        self.assertEqual(frame[0 * 20 + 2], 1)
        self.assertEqual(frame[0 * 20 + 8], 1)

    def test_quoted_text_with_spaces_survives_payload_roundtrip(self):
        from balzar.payload import decode_payload, encode_payload
        prog = 'CANVAS w=64 h=16 bg=0\nTEXT x=1 y=1 text="QTY 12" color=1\n'
        payload = encode_payload(prog)
        restored = decode_payload(payload)
        self.assertEqual(render(restored).frames, render(prog).frames)
        self.assertIn('"QTY 12"', restored)


class TestDifferentialAndGenerative(unittest.TestCase):
    def test_map_recolors_only_matching(self):
        r = run("FILL region=FULL color=3\nSETPIX x=0 y=0 color=1\n"
                "MAP region=FULL src=3 dst=6")
        frame = r.frames[0]
        self.assertEqual(frame[0], 1)
        self.assertEqual(frame[1], 6)

    def test_invert(self):
        r = run("FILL region=FULL color=0\nINVERT region=FULL ncolors=16")
        self.assertEqual(r.frames[0][0], 15)

    def test_line_endpoints(self):
        r = run("LINE x1=0 y1=0 x2=7 y2=7 color=4")
        frame = r.frames[0]
        self.assertEqual(frame[0], 4)
        self.assertEqual(frame[7 * 8 + 7], 4)

    def test_frames_are_emitted_per_frame_op(self):
        r = run("LOOP var=i count=3\nSETPIX x=i y=0 color=1\nFRAME\nENDLOOP")
        self.assertEqual(len(r.frames), 3)
        self.assertNotEqual(r.frames[0], r.frames[2])

    def test_out_of_bounds_drawing_is_clipped(self):
        r = run("CIRCLE cx=0 cy=0 r=5 color=2 fill=1\n"
                "LINE x1=-3 y1=-3 x2=10 y2=10 color=3")
        self.assertEqual(r.width, 8)  # no exception raised

    def test_region_out_of_canvas_raises(self):
        with self.assertRaises(ValueError):
            run("REGION name=A x=6 y=6 w=4 h=4")

    def test_expressions_and_loop_vars(self):
        r = run("LOOP var=i count=4\nSETPIX x=i*2 y=i color=1\nENDLOOP")
        frame = r.frames[0]
        for i in range(4):
            self.assertEqual(frame[i * 8 + i * 2], 1)

    def test_unknown_instruction_raises(self):
        with self.assertRaises(ValueError):
            run("EXPLODE x=1")

    def test_unknown_argument_raises(self):
        with self.assertRaises(ValueError):
            run("SETPIX x=1 y=1 color=1 wat=2")

    def test_expression_cannot_call_functions(self):
        from balzar.dsl import eval_expr
        with self.assertRaises(ValueError):
            eval_expr("abs(1)", {})
        with self.assertRaises(ValueError):
            eval_expr("__import__", {})


if __name__ == "__main__":
    unittest.main()
