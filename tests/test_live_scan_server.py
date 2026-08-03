"""balzar/live_scan_server.py: the local HTTPServer that bridges a
browser-based continuous QR camera scan back into the desktop app.
Pure protocol tests (GET/POST over real sockets) -- no Tkinter, no
browser, no real camera: those pieces are verified manually under Xvfb
with a fake camera, same principle already followed for the rest of
this project's browser-facing UI (viewer3d.py, gui.py)."""

import base64
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from balzar.live_scan_server import start_live_scan_server


class TestLiveScanServer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        with mock.patch("balzar.live_scan_server.webbrowser.open") as self.browser_open:
            self.server, self.result_queue = start_live_scan_server(self._tmp.name)
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.status, resp.read()

    def _post(self, path, body: bytes):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_opens_the_system_browser_to_the_served_page(self):
        self.browser_open.assert_called_once()
        (url,), _ = self.browser_open.call_args
        self.assertEqual(url, f"http://127.0.0.1:{self.port}/index.html")

    def test_serves_the_page_and_all_three_vendored_js_files(self):
        status, body = self._get("/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"ContinuousQrScanner", body)
        for name in ("jsQR.min.js", "qr-transport-core.js", "qr-camera-scanner.js"):
            status, body = self._get(f"/{name}")
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 0)

    def test_submit_puts_the_decoded_bytes_on_the_queue(self):
        raw = b"\x00hello world\xff not utf-8 \x80"
        body = json.dumps({"data_base64": base64.b64encode(raw).decode()}).encode()
        status, resp = self._post("/submit", body)
        self.assertEqual(status, 200)
        self.assertTrue(resp["ok"])
        self.assertEqual(self.result_queue.get(timeout=2), raw)

    def test_submit_with_malformed_body_returns_400_not_a_crash(self):
        status, resp = self._post("/submit", b"not json at all")
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])
        self.assertTrue(self.result_queue.empty())

    def test_submit_with_invalid_base64_returns_400_not_a_crash(self):
        body = json.dumps({"data_base64": "not valid base64!!!"}).encode()
        status, resp = self._post("/submit", body)
        self.assertEqual(status, 400)
        self.assertFalse(resp["ok"])
        self.assertTrue(self.result_queue.empty())

    def test_unknown_path_returns_404(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/nope", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
