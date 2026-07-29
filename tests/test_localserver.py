"""Test del server locale (balzar/localserver.py) senza Playwright: avvia il
server su una porta effimera e lo interroga con urllib. Copre la superficie
HTTP (statici serviti, traversal rifiutato, /api instradato, endpoint
sconosciuto 404) -- la finestra pywebview vera si valida a parte, questo e' il
guscio HTTP che le sta sotto."""

import base64
import json
import unittest
import urllib.error
import urllib.request

from balzar import localserver
from balzar.payload import encode_payload

_PROGRAM = "CANVAS w=8 h=8\nPALETTE i=0 rgb=#000000\nPALETTE i=1 rgb=#ffffff\nRECT x=1 y=1 w=3 h=3 color=1\n"


class LocalServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = localserver.start_local_server(port=0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        return urllib.request.urlopen(self.url + path, timeout=10)

    def _post(self, path, obj):
        req = urllib.request.Request(
            self.url + path, data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req, timeout=30)

    def test_root_serves_index(self):
        body = self._get("/").read().decode("utf-8", "replace")
        self.assertIn("balzar", body.lower())

    def test_serves_a_vendored_js_file(self):
        resp = self._get("/jsQR.min.js")
        self.assertEqual(resp.status, 200)
        self.assertGreater(len(resp.read()), 0)

    def test_path_traversal_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/../CLAUDE.md")
        self.assertEqual(cm.exception.code, 404)

    def test_unknown_api_route_is_404(self):
        req = urllib.request.Request(self.url + "/api/nope", data=b"{}",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 404)

    def test_api_render_roundtrips_a_real_payload(self):
        payload_b64 = base64.b64encode(encode_payload(_PROGRAM)).decode("ascii")
        resp = self._post("/api/render", {"data": payload_b64})
        obj = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(obj.get("ok"), obj)
        self.assertEqual(obj.get("kind"), "2d")
        self.assertIn("png_base64", obj)

    def test_api_bad_json_is_500_not_crash(self):
        # corpo non-JSON: il server risponde con un errore, non chiude il socket
        req = urllib.request.Request(self.url + "/api/render", data=b"not json",
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 500)


if __name__ == "__main__":
    unittest.main()
