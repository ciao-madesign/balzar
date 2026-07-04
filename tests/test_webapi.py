"""Web demo backend (balzar/webapi.py): the new encoding flows (vector,
video, sequence) and the QR generator added to the Vercel demo. Success
paths, error paths, and the truncation/omission behavior driven by Limits."""

import base64
import io
import os
import tempfile
import unittest

from balzar.webapi import (LOCAL_LIMITS, Limits, handle_encode_sequence,
                           handle_encode_vector, handle_encode_video, handle_qr)

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
