"""CLI (balzar/cli.py): the primary user-facing surface for every
compression/re-expansion flow (render/encode/decode/chunks/scan/assemble
plus the four encode-* variants), previously with zero automated
coverage — only exercised manually per session. Calls balzar.cli.main()
directly (no subprocess) so failures show a normal traceback if
something regresses.

Also verifies the honesty contract at the CLI boundary: main() wraps
args.func(args) in a single top-level except (ValueError, SyntaxError,
OSError), so a bad input file or an invalid program must produce a
clean "errore: ..." line and exit code 1, never a raw Python traceback.
"""

import contextlib
import io
import os
import tempfile
import unittest

from balzar.cli import main

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
ENDSEC
0
EOF
"""

SVG_SIMPLE = ('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
             '<circle cx="10" cy="10" r="5"/></svg>')


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _path(self, name):
        return os.path.join(self.tmpdir.name, name)

    # ---------------------------------------------------------- render

    def test_render_writes_png_and_reports_expansion(self):
        prog = self._write("p.bzr", "CANVAS w=4 h=4 bg=0\nPALETTE i=1 rgb=#FF0000\n"
                                    "RECT x=0 y=0 w=2 h=2 color=1 fill=1\n")
        out_dir = self._path("out")
        code, out, err = _run(["render", prog, "-o", out_dir])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "p.png")))
        self.assertIn("espansione", out)

    def test_render_svg_unsupported_program_warns_not_crashes(self):
        prog = self._write("noise.bzr", "CANVAS w=4 h=4 bg=0\nPALETTE i=1 rgb=#FF0000\n"
                                        "NOISE region=FULL color=1 density=0.5\n")
        out_dir = self._path("out")
        code, out, err = _run(["render", prog, "-o", out_dir, "--svg"])
        self.assertEqual(code, 0)
        self.assertIn("non disponibile", err)

    # ---------------------------------------------------- encode/decode

    def test_encode_then_decode_roundtrip(self):
        prog = self._write("p.bzr", "CANVAS w=2 h=2 bg=0\n")
        code, out, err = _run(["encode", prog])
        self.assertEqual(code, 0)
        payload_path = os.path.splitext(prog)[0] + ".bzp"
        self.assertTrue(os.path.exists(payload_path))

        decoded_path = self._path("decoded.bzr")
        code, out, err = _run(["decode", payload_path, "-o", decoded_path])
        self.assertEqual(code, 0)
        with open(decoded_path, encoding="utf-8") as fh:
            self.assertIn("CANVAS", fh.read())

    def test_encode_invalid_program_gives_clean_error_not_traceback(self):
        prog = self._write("bad.bzr", "THIS IS NOT A VALID BALZAR PROGRAM\n")
        code, out, err = _run(["encode", prog])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    def test_missing_input_file_gives_clean_error_not_traceback(self):
        code, out, err = _run(["render", self._path("nope.bzr")])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    def test_info_reports_expansion_factor(self):
        prog = self._write("p.bzr", "CANVAS w=8 h=8 bg=0\nPALETTE i=1 rgb=#00FF00\n"
                                    "RECT x=0 y=0 w=8 h=8 color=1 fill=1\n")
        code, out, err = _run(["info", prog])
        self.assertEqual(code, 0)
        self.assertIn("espansione", out)
        self.assertIn("QR singolo", out)

    # ------------------------------------------------------ encode-image

    def test_encode_image_lossless_solid_color(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow non installato")
        img_path = self._path("solid.png")
        Image.new("RGB", (16, 16), (10, 20, 30)).save(img_path)
        code, out, err = _run(["encode-image", img_path])
        self.assertEqual(code, 0)
        self.assertIn("esatta", out)
        self.assertTrue(os.path.exists(os.path.splitext(img_path)[0] + ".bzp"))

    def test_encode_image_missing_file_gives_clean_error(self):
        code, out, err = _run(["encode-image", self._path("nope.png")])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    # ----------------------------------------------------- encode-vector

    def test_encode_vector_svg(self):
        svg_path = self._write("shape.svg", SVG_SIMPLE)
        code, out, err = _run(["encode-vector", svg_path])
        self.assertEqual(code, 0)
        self.assertIn("elementi:", out)
        self.assertTrue(os.path.exists(os.path.splitext(svg_path)[0] + ".bzp"))

    def test_encode_vector_dxf(self):
        dxf_path = self._write("shape.dxf", DXF_STEP1)
        code, out, err = _run(["encode-vector", dxf_path])
        self.assertEqual(code, 0)
        self.assertIn("DXF", out)

    def test_encode_vector_bad_extension_gives_clean_error(self):
        txt_path = self._write("shape.txt", "hello")
        code, out, err = _run(["encode-vector", txt_path])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    # --------------------------------------------------------- encode-3d

    def _write_minimal_3dxml(self, name):
        import zipfile
        manifest = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Manifest xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                   'xsi:noNamespaceSchemaLocation="Manifest.xsd">'
                   '<Root>main.3dxml</Root></Manifest>')
        main_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Model_3dxml xmlns="http://www.3ds.com/xsd/3DXML" '
                   'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                   '<ProductStructure root="1">'
                   '<Reference3D id="1" name="Root"/>'
                   '<Instance3D id="2" name="inst_A">'
                   '<IsAggregatedBy>1</IsAggregatedBy><IsInstanceOf>3</IsInstanceOf>'
                   '<RelativeMatrix>1 0 0 0 1 0 0 0 1 0 0 0</RelativeMatrix></Instance3D>'
                   '<Reference3D id="3" name="PartA"/>'
                   '<ReferenceRep id="4" name="PartA_Rep" associatedFile="urn:3DXML:shapeA.3DRep"/>'
                   '<InstanceRep id="5" name="PartA_InstRep">'
                   '<IsAggregatedBy>3</IsAggregatedBy><IsInstanceOf>4</IsInstanceOf></InstanceRep>'
                   '</ProductStructure></Model_3dxml>')
        shape_rep = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<XMLRepresentation xmlns="http://www.3ds.com/xsd/3DXML" '
                    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                    '<Root xsi:type="BagRepType" id="1"><Rep xsi:type="PolygonalRepType" id="2">'
                    '<Faces><Face strips="0 1 2"><SurfaceAttributes>'
                    '<Color xsi:type="RGBAColorType" red="1" green="0" blue="0" alpha="1"/>'
                    '</SurfaceAttributes></Face></Faces>'
                    '<VertexBuffer><Positions>0 0 0 1 0 0 0 1 0</Positions>'
                    '<Normals>0 0 1 0 0 1 0 0 1</Normals></VertexBuffer></Rep></Root>'
                    '</XMLRepresentation>')
        path = self._path(name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Manifest.xml", manifest)
            zf.writestr("main.3dxml", main_xml)
            zf.writestr("shapeA.3DRep", shape_rep)
        return path

    def test_encode_3d_then_render_3d_roundtrip(self):
        path = self._write_minimal_3dxml("assembly.3dxml")
        code, out, err = _run(["encode-3d", path])
        self.assertEqual(code, 0)
        self.assertIn("forme uniche: 1", out)
        payload_path = os.path.splitext(path)[0] + ".b3d"
        self.assertTrue(os.path.exists(payload_path))

        glb_path = self._path("out.glb")
        code, out, err = _run(["render-3d", payload_path, "-o", glb_path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(glb_path))
        with open(glb_path, "rb") as fh:
            self.assertEqual(fh.read(4), b"glTF")

    def test_encode_3d_missing_manifest_gives_clean_error(self):
        import zipfile
        bad_path = self._path("bad.3dxml")
        with zipfile.ZipFile(bad_path, "w") as zf:
            zf.writestr("not_a_manifest.txt", "hello")
        code, out, err = _run(["encode-3d", bad_path])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    def test_render_3d_corrupt_payload_gives_clean_error(self):
        bad_path = self._write("bad.b3d", "not a real BZM1 payload")
        code, out, err = _run(["render-3d", bad_path])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    # ----------------------------------------------------- encode-bundle

    def test_encode_bundle_3dxml_plus_marked_alarm(self):
        xml_path = self._write_minimal_3dxml("assembly.3dxml")
        csv_path = self._write("alarms.csv", "E100,PartA\nE200,PartA\n")
        out_path = self._path("assembly.bzx")
        code, out, err = _run(["encode-bundle", xml_path, csv_path,
                               "--alarm", csv_path, "-o", out_path])
        self.assertEqual(code, 0)
        self.assertIn("2 elementi", out)
        self.assertIn("alarm", out)
        with open(out_path, "rb") as fh:
            self.assertEqual(fh.read(4), b"BZX1")

    def test_encode_bundle_carries_arbitrary_doc_and_supports_no_3d(self):
        # a doc-only bundle (no 3D) with an arbitrary format is valid:
        # the format is carried as a generic consultable document, not
        # rejected -- the viewer offers it for download
        txt_path = self._write("manual.txt", "istruzioni")
        pdf_path = self._write("drawing.pdf", "%PDF-1.4 fake")
        out_path = self._path("docs.bzx")
        code, out, err = _run(["encode-bundle", txt_path, pdf_path, "-o", out_path])
        self.assertEqual(code, 0)
        self.assertIn("2 elementi", out)
        self.assertIn("doc", out)
        with open(out_path, "rb") as fh:
            self.assertEqual(fh.read(4), b"BZX1")

    def test_encode_bundle_alarm_not_in_inputs_gives_clean_error(self):
        xml_path = self._write_minimal_3dxml("assembly.3dxml")
        stray = self._write("stray.csv", "E1,X\n")
        code, out, err = _run(["encode-bundle", xml_path, "--alarm", stray])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    def test_encode_bundle_2d_tavola_no_3d_required(self):
        bzr_path = self._write("tavola.bzr",
                               "CANVAS w=30 h=30 bg=1\nPALETTE i=2 rgb=#FF0000\n"
                               "RECT x=5 y=5 w=10 h=10 color=2 fill=1\n")
        out_path = self._path("tavola.bzx")
        code, out, err = _run(["encode-bundle", bzr_path, "-o", out_path])
        self.assertEqual(code, 0)
        self.assertIn("1 elementi", out)
        self.assertIn("2d", out)
        with open(out_path, "rb") as fh:
            self.assertEqual(fh.read(4), b"BZX1")

    def test_encode_bundle_invalid_bzr_gives_clean_error(self):
        bzr_path = self._write("broken.bzr", "BOGUS x=1\n")
        code, out, err = _run(["encode-bundle", bzr_path])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertIn("broken.bzr", err)
        self.assertNotIn("Traceback", err)

    # ------------------------------------------------------ encode-video

    def test_encode_video_gif(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow non installato")
        frames = []
        for i in range(3):
            img = Image.new("RGB", (20, 20), (255, 255, 255))
            for x in range(2 + i * 3, 6 + i * 3):
                for y in range(2, 6):
                    img.putpixel((x, y), (200, 0, 0))
            frames.append(img)
        gif_path = self._path("anim.gif")
        frames[0].save(gif_path, save_all=True, append_images=frames[1:],
                      duration=100, loop=0)
        code, out, err = _run(["encode-video", gif_path])
        self.assertEqual(code, 0)
        self.assertIn("3 frame", out)
        self.assertTrue(os.path.exists(os.path.splitext(gif_path)[0] + ".bzp"))

    # --------------------------------------------------- encode-sequence

    def test_encode_sequence_vector_mode(self):
        step1 = self._write("step1.dxf", DXF_STEP1)
        step2 = self._write("step2.dxf", DXF_STEP2)
        out_path = self._path("seq.bzp")
        code, out, err = _run(["encode-sequence", step1, step2, "-o", out_path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_path))
        self.assertIn("2 frame", out)

    def test_encode_sequence_single_file_rejected(self):
        step1 = self._write("step1.dxf", DXF_STEP1)
        code, out, err = _run(["encode-sequence", step1])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)

    def test_encode_sequence_independent_mode_isolates_broken_file(self):
        good = self._write("good.dxf", DXF_STEP1)
        bad = self._write("bad.svg", "<svg><circle cx=oops></svg>")
        code, out, err = _run(["encode-sequence", good, bad, "--mode", "independent"])
        self.assertEqual(code, 0)
        self.assertIn("1 codificati", out)
        self.assertIn("1 falliti", out)
        self.assertTrue(os.path.exists(os.path.splitext(good)[0] + ".bzp"))

    # ---------------------------------------------------- explode-vector

    def test_explode_vector_multi_layer(self):
        dxf_path = self._write("multi.dxf", DXF_MULTI_LAYER)
        out_path = self._path("esploso.bzp")
        code, out, err = _run(["explode-vector", dxf_path, "-o", out_path, "--steps", "3"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_path))
        self.assertIn("4 frame", out)

    def test_explode_vector_single_layer_rejected(self):
        dxf_path = self._write("single.dxf", DXF_STEP1)
        code, out, err = _run(["explode-vector", dxf_path])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    # --------------------------------------------- chunks / scan / assemble

    def test_chunks_text_and_assemble_roundtrip(self):
        prog = self._write("p.bzr", "CANVAS w=6 h=6 bg=0\nPALETTE i=1 rgb=#0000FF\n"
                                    "RECT x=0 y=0 w=6 h=6 color=1 fill=1\n")
        chunks_dir = self._path("chunks")
        code, out, err = _run(["chunks", prog, "-o", chunks_dir])
        self.assertEqual(code, 0)
        self.assertTrue(any(f.endswith(".txt") for f in os.listdir(chunks_dir)))

        assembled_path = self._path("assembled.bzp")
        code, out, err = _run(["assemble", chunks_dir, "-o", assembled_path])
        self.assertEqual(code, 0)
        self.assertIn("integrita' verificata", out)

        render_dir = self._path("rendered")
        code, out, err = _run(["render", assembled_path, "-o", render_dir])
        self.assertEqual(code, 0)
        # stem is derived from the render input's own filename (assembled.bzp),
        # not the original source (p.bzr) — the assembled payload carries no
        # memory of where it came from, by design
        self.assertTrue(os.path.exists(os.path.join(render_dir, "assembled.png")))

    def test_chunks_qr_and_scan_roundtrip(self):
        try:
            import qrcode  # noqa: F401
            from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
        except ImportError:
            self.skipTest("richiede qrcode + pyzbar (+ libzbar di sistema)")

        prog = self._write("p.bzr", "CANVAS w=6 h=6 bg=0\nPALETTE i=1 rgb=#0000FF\n"
                                    "RECT x=0 y=0 w=6 h=6 color=1 fill=1\n")
        qr_dir = self._path("qr")
        code, out, err = _run(["chunks", prog, "-o", qr_dir, "--qr"])
        self.assertEqual(code, 0)
        qr_files = [f for f in os.listdir(qr_dir) if f.endswith(".png")]
        self.assertEqual(len(qr_files), 1)

        render_dir = self._path("rendered")
        scanned_path = self._path("scanned.bzp")
        code, out, err = _run(["scan", os.path.join(qr_dir, qr_files[0]),
                               "-o", scanned_path, "--render", render_dir])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(scanned_path))
        # stem comes from the scanned QR image's own filename, not "p"
        scanned_stem = os.path.splitext(qr_files[0])[0]
        self.assertTrue(os.path.exists(os.path.join(render_dir, f"{scanned_stem}.png")))

    def test_scan_missing_qr_gives_clean_error_not_traceback(self):
        try:
            from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
            from PIL import Image
        except ImportError:
            self.skipTest("richiede pyzbar (+ libzbar di sistema) e Pillow")
        from PIL import Image
        blank_path = self._path("blank.png")
        Image.new("RGB", (50, 50), (255, 255, 255)).save(blank_path)
        code, out, err = _run(["scan", blank_path])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)

    # ------------------------------------------ chunks/scan --raw (physical
    # QR transport of arbitrary bytes, bypassing the balzar payload format)

    def test_chunks_raw_qr_grid_dim_and_scan_raw_roundtrip(self):
        try:
            import qrcode  # noqa: F401
            from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
        except ImportError:
            self.skipTest("richiede qrcode + pyzbar (+ libzbar di sistema)")

        # byte arbitrari, non un programma/payload balzar valido (nessun
        # magic BZR1/BZM1/BZC1/BZX1) — deve comunque transitare intatto
        original = bytes(range(256)) * 40  # 10.240 byte, forza piu' capitoli
        src = self._path("arbitrary.bin")
        with open(src, "wb") as fh:
            fh.write(original)

        qr_dir = self._path("qr_raw")
        code, out, err = _run(["chunks", src, "-o", qr_dir, "--raw", "--qr",
                               "--grid-dim", "2"])
        self.assertEqual(code, 0)
        frames = sorted(f for f in os.listdir(qr_dir) if f.endswith(".png"))
        self.assertGreater(len(frames), 1, "10KB dovrebbero servire piu' fotogrammi 2x2")

        out_path = self._path("rebuilt.bin")
        code, out, err = _run(["scan", *[os.path.join(qr_dir, f) for f in frames],
                               "--raw", "-o", out_path])
        self.assertEqual(code, 0)
        self.assertIn("integrita' verificata", out)
        with open(out_path, "rb") as fh:
            rebuilt = fh.read()
        self.assertEqual(rebuilt, original)

    def test_chunks_grid_dim_without_qr_is_a_clean_error(self):
        src = self._path("x.bin")
        with open(src, "wb") as fh:
            fh.write(b"whatever")
        code, out, err = _run(["chunks", src, "--raw", "--grid-dim", "2",
                               "-o", self._path("out")])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertIn("--grid-dim", err)

    def test_scan_raw_without_output_is_a_clean_error(self):
        try:
            from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
            from PIL import Image
        except ImportError:
            self.skipTest("richiede pyzbar (+ libzbar di sistema) e Pillow")
        from PIL import Image
        blank_path = self._path("blank.png")
        Image.new("RGB", (50, 50), (255, 255, 255)).save(blank_path)
        code, out, err = _run(["scan", blank_path, "--raw"])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertIn("--raw", err)

    def test_scan_raw_and_render_together_is_a_clean_error(self):
        try:
            from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
            from PIL import Image
        except ImportError:
            self.skipTest("richiede pyzbar (+ libzbar di sistema) e Pillow")
        from PIL import Image
        blank_path = self._path("blank.png")
        Image.new("RGB", (50, 50), (255, 255, 255)).save(blank_path)
        code, out, err = _run(["scan", blank_path, "--raw", "--render",
                               self._path("out"), "-o", self._path("x.bin")])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)

    def test_chunks_raw_on_non_balzar_file_would_fail_without_raw_flag(self):
        # controprova: senza --raw un file binario arbitrario (non testo
        # UTF-8, non un payload balzar) viene rifiutato onestamente invece
        # di essere accettato come se fosse un programma DSL — nessuna
        # regressione silenziosa che tratti byte arbitrari come default
        src = self._path("not_a_program.bin")
        with open(src, "wb") as fh:
            fh.write(b"\xff\xfe\x00\x01not valid utf-8 \x80\x81")
        code, out, err = _run(["chunks", src, "-o", self._path("out")])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
