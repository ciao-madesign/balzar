"""Web demo backend (balzar/webapi.py): the new encoding flows (vector,
video, sequence) and the QR generator added to the Vercel demo. Success
paths, error paths, and the truncation/omission behavior driven by Limits."""

import base64
import io
import os
import tempfile
import unittest

from balzar.webapi import (LOCAL_LIMITS, Limits, handle_encode,
                           handle_encode_sequence, handle_encode_vector,
                           handle_encode_video, handle_qr, handle_render)

SVG_FLANGE = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
  <circle cx="100" cy="100" r="80" fill="none" stroke="#000000"/>
</svg>"""

DXF_FLANGE = """0
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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _make_png(w=40, h=30):
    from PIL import Image
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for x in range(5, 15):
        for y in range(5, 15):
            img.putpixel((x, y), (200, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestHandleEncode(unittest.TestCase):
    """The 'Comprimi immagine' tab (tab 1) — first-frame-only raster
    encoder, previously untested at the webapi layer."""

    def test_success(self):
        status, resp = handle_encode({"data": _b64(_make_png()), "max_dim": 200}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertIn("mean_color_error", resp)

    def test_missing_data(self):
        status, resp = handle_encode({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_encode({"data": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_non_image_gives_clean_400_not_500(self):
        status, resp = handle_encode({"data": _b64(b"not an image")}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleRender(unittest.TestCase):
    """The 'Apri programma (.bzr/.bzp)' tab (tab 5) — previously untested
    at the webapi layer despite being one of the five demo flows."""

    def _payload(self):
        from balzar.payload import encode_payload
        return encode_payload("CANVAS w=4 h=4 bg=0\nPALETTE i=1 rgb=#FF0000\n"
                              "RECT x=0 y=0 w=2 h=2 color=1 fill=1\n")

    def test_success_with_bzp_payload(self):
        status, resp = handle_render({"data": _b64(self._payload())}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual((resp["width"], resp["height"]), (4, 4))
        self.assertIn("payload_base64", resp)

    def test_success_with_bzr_source_text(self):
        program = "CANVAS w=3 h=3 bg=0\n"
        status, resp = handle_render({"data": _b64(program.encode("utf-8"))}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual((resp["width"], resp["height"]), (3, 3))

    def test_missing_data(self):
        status, resp = handle_render({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_render({"data": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_corrupt_payload_magic_gives_clean_400_not_500(self):
        garbage = _b64(b"\xff\xfe\xfd not a program and not utf-8 \x00")
        status, resp = handle_render({"data": garbage}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_invalid_program_text_gives_clean_400_not_500(self):
        status, resp = handle_render({"data": _b64(b"THIS IS NOT A VALID BALZAR PROGRAM")},
                                     LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleEncodeVector(unittest.TestCase):
    def test_svg_success(self):
        status, resp = handle_encode_vector(
            {"data": _b64(SVG_FLANGE.encode()), "filename": "flange.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["source_format"], "svg")
        self.assertEqual(resp["skipped"], [])
        self.assertTrue(resp["svg_available"])
        self.assertIn("preview_png_base64", resp)

    def test_dxf_success(self):
        status, resp = handle_encode_vector(
            {"data": _b64(DXF_FLANGE.encode()), "filename": "flange.dxf"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["source_format"], "dxf")
        self.assertEqual(resp["element_count"], 1)

    def test_missing_data(self):
        status, resp = handle_encode_vector({"filename": "flange.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_unrecognized_extension(self):
        status, resp = handle_encode_vector(
            {"data": _b64(b"hello"), "filename": "flange.txt"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertIn("estensione", resp["error"])

    def test_invalid_svg_gives_clean_400_not_500(self):
        status, resp = handle_encode_vector(
            {"data": _b64(b"<svg><circle cx=oops></svg>"), "filename": "bad.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_encode_vector(
            {"data": "not-valid-base64!!!", "filename": "flange.svg"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_program_truncation_uses_limit(self):
        tiny_limits = Limits(max_upload_bytes=LOCAL_LIMITS.max_upload_bytes,
                             max_analysis_dim=LOCAL_LIMITS.max_analysis_dim,
                             max_preview_dim=LOCAL_LIMITS.max_preview_dim,
                             max_program_chars=5,
                             max_payload_b64_bytes=LOCAL_LIMITS.max_payload_b64_bytes,
                             max_video_frames=LOCAL_LIMITS.max_video_frames)
        status, resp = handle_encode_vector(
            {"data": _b64(SVG_FLANGE.encode()), "filename": "flange.svg"}, tiny_limits)
        self.assertEqual(status, 200)
        self.assertTrue(resp["program_truncated"])


class TestHandleEncodeVideo(unittest.TestCase):
    def _make_gif(self, n_frames=4):
        from PIL import Image
        frames = []
        for i in range(n_frames):
            img = Image.new("RGB", (40, 30), (255, 255, 255))
            for x in range(5 + i * 5, 15 + i * 5):
                for y in range(5, 15):
                    img.putpixel((x, y), (200, 0, 0))
            frames.append(img)
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                      duration=100, loop=0)
        return buf.getvalue()

    def test_success(self):
        try:
            data = self._make_gif()
        except ImportError:
            self.skipTest("Pillow non installato")
        status, resp = handle_encode_video({"data": _b64(data), "max_dim": 200}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["frame_count"], 4)
        self.assertIn("preview_gif_base64", resp)

    def test_single_frame_rejected(self):
        from PIL import Image
        img = Image.new("RGB", (10, 10), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        status, resp = handle_encode_video({"data": _b64(buf.getvalue())}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertIn("un solo frame", resp["error"])

    def test_missing_data(self):
        status, resp = handle_encode_video({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_non_image_gives_clean_400_not_500(self):
        status, resp = handle_encode_video({"data": _b64(b"not an image")}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_malformed_base64_gives_clean_400_not_500(self):
        status, resp = handle_encode_video({"data": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleEncodeSequence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _files(self, *contents_and_names):
        return [{"filename": name, "data": _b64(content.encode() if isinstance(content, str) else content)}
                for content, name in contents_and_names]

    def test_vector_sequence_success(self):
        files = self._files((DXF_FLANGE, "step1.dxf"), (DXF_STEP2, "step2.dxf"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["frame_count"], 2)
        self.assertEqual(len(resp["preview_frames_png_base64"]), 2)

    def test_raster_sequence_success(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow non installato")
        contents = []
        for i, offset in enumerate([0, 5]):
            img = Image.new("RGB", (30, 20), (255, 255, 255))
            for x in range(5 + offset, 12 + offset):
                for y in range(5, 12):
                    img.putpixel((x, y), (0, 200, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            contents.append((buf.getvalue(), f"f{i}.png"))
        files = self._files(*contents)
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["source_format"], "raster")
        self.assertEqual(resp["frame_count"], 2)
        self.assertEqual(len(resp["preview_frames_png_base64"]), 2)

    def test_single_file_rejected(self):
        files = self._files((DXF_FLANGE, "step1.dxf"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_mixed_vector_formats_rejected(self):
        files = self._files((SVG_FLANGE, "a.svg"), (DXF_FLANGE, "b.dxf"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_non_image_in_raster_path_gives_clean_400_not_500(self):
        files = self._files((DXF_FLANGE, "a.dxf"), (b"not an image", "b.png"))
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_missing_files_field(self):
        status, resp = handle_encode_sequence({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_file_without_content_rejected(self):
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "b.dxf"}]
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)

    def test_malformed_base64_gives_clean_400_not_500(self):
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "b.dxf", "data": "not-valid-base64!!!"}]
        status, resp = handle_encode_sequence({"files": files}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])


class TestHandleEncodeIndependent(unittest.TestCase):
    """mode='independent' dispatch inside handle_encode_sequence: no
    format restriction, fault isolation per file, single file allowed."""

    def test_mixed_formats_all_succeed(self):
        files = [{"filename": "a.svg", "data": _b64(SVG_FLANGE.encode())},
                {"filename": "b.dxf", "data": _b64(DXF_FLANGE.encode())}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["file_count"], 2)
        self.assertEqual(resp["success_count"], 2)
        self.assertEqual(resp["items"][0]["filename"], "a.svg")
        self.assertEqual(resp["items"][1]["filename"], "b.dxf")
        self.assertTrue(all(it["ok"] for it in resp["items"]))

    def test_broken_file_reported_not_500(self):
        files = [{"filename": "good.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "bad.svg", "data": _b64(b"<svg><circle cx=oops></svg>")}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["success_count"], 1)
        self.assertTrue(resp["items"][0]["ok"])
        self.assertFalse(resp["items"][1]["ok"])
        self.assertIn("error", resp["items"][1])

    def test_single_file_allowed(self):
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["file_count"], 1)

    def test_malformed_base64_isolated_not_500_and_order_preserved(self):
        """A corrupt base64 blob for one file must not crash the whole
        batch (the same fault-isolation guarantee as a parseable-but-
        broken file), and the surviving files must keep their original
        position in the response."""
        files = [{"filename": "a.dxf", "data": _b64(DXF_FLANGE.encode())},
                {"filename": "bad.svg", "data": "not-valid-base64!!!"},
                {"filename": "c.dxf", "data": _b64(DXF_STEP2.encode())}]
        status, resp = handle_encode_sequence(
            {"files": files, "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["file_count"], 3)
        self.assertEqual(resp["success_count"], 2)
        self.assertEqual([it["filename"] for it in resp["items"]], ["a.dxf", "bad.svg", "c.dxf"])
        self.assertTrue(resp["items"][0]["ok"])
        self.assertFalse(resp["items"][1]["ok"])
        self.assertIn("error", resp["items"][1])
        self.assertTrue(resp["items"][2]["ok"])

    def test_empty_files_rejected(self):
        status, resp = handle_encode_sequence(
            {"files": [], "mode": "independent"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)


class TestHandleQr(unittest.TestCase):
    def test_small_payload_is_a_single_qr(self):
        status, resp = handle_qr({"payload_base64": _b64(b"hello world")}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["single_qr"])
        self.assertIn("qr_png_base64", resp)

    def test_large_payload_becomes_a_grid(self):
        big = b"x" * 6000
        status, resp = handle_qr({"payload_base64": _b64(big)}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertFalse(resp["single_qr"])

    def test_missing_payload(self):
        status, resp = handle_qr({}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_malformed_base64_gives_clean_400_not_500(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest("qrcode non installato")
        status, resp = handle_qr({"payload_base64": "not-valid-base64!!!"}, LOCAL_LIMITS)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])

    def test_qr_roundtrips_via_zbar_if_available(self):
        try:
            from pyzbar.pyzbar import decode as zbar_decode
        except ImportError:
            self.skipTest("pyzbar non installato")
        from PIL import Image

        from balzar.payload import assemble_chunks, from_base64

        payload = b"balzar QR roundtrip check"
        status, resp = handle_qr({"payload_base64": _b64(payload)}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        img = Image.open(io.BytesIO(base64.b64decode(resp["qr_png_base64"])))
        results = zbar_decode(img)
        self.assertEqual(len(results), 1)
        chunk = from_base64(results[0].data.decode("ascii"))
        self.assertEqual(assemble_chunks([chunk]), payload)


if __name__ == "__main__":
    unittest.main()
