"""Test del guscio WebView (balzar/webview_app.py) senza aprire una finestra
vera: la scelta della pagina iniziale, il route /api/activate iniettato nel
server locale, e il fallback quando pywebview manca. La finestra pywebview
stessa si valida sul Mac (nessun backend webview in CI Linux headless)."""

import base64
import json
import os
import tempfile
import unittest
import urllib.request

from balzar import license as lic
from balzar import localserver, webview_app

try:
    import webview  # noqa: F401
    _HAS_WEBVIEW = True
except ImportError:
    _HAS_WEBVIEW = False

_KEY = "WEBVIEW-TEST-KEY"


class InitialPathTest(unittest.TestCase):
    def test_need_key_opens_activation_page(self):
        self.assertEqual(webview_app._initial_path(lic.STARTUP_NEED_KEY),
                         "/activate.html")

    def test_open_and_activated_open_the_app(self):
        self.assertEqual(webview_app._initial_path(lic.STARTUP_OPEN), "/index.html")
        self.assertEqual(webview_app._initial_path(lic.STARTUP_ACTIVATED), "/index.html")


class ActivateRouteTest(unittest.TestCase):
    """Il route /api/activate iniettato in localserver: verifica la chiave e
    persiste l'attivazione, esattamente come farebbe la pagina activate.html."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_env = os.environ.get("BALZAR_LICENSE_DIR")
        os.environ["BALZAR_LICENSE_DIR"] = self._tmp
        self._old_hash = lic.BETA_KEY_SHA256
        lic.BETA_KEY_SHA256 = lic._hash_key(_KEY)
        self.server, self.url = localserver.start_local_server(
            extra_routes={"/api/activate": webview_app._activate})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        lic.BETA_KEY_SHA256 = self._old_hash
        if self._old_env is None:
            os.environ.pop("BALZAR_LICENSE_DIR", None)
        else:
            os.environ["BALZAR_LICENSE_DIR"] = self._old_env

    def _activate(self, key):
        req = urllib.request.Request(
            self.url + "/api/activate",
            data=json.dumps({"key": key}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def test_wrong_key_not_activated(self):
        obj = self._activate("nope")
        self.assertTrue(obj["ok"])
        self.assertFalse(obj["activated"])
        self.assertFalse(lic.is_activated())

    def test_correct_key_activates_and_persists(self):
        obj = self._activate(_KEY)
        self.assertTrue(obj["activated"])
        self.assertTrue(lic.is_activated())

    def test_activation_page_is_served(self):
        body = urllib.request.urlopen(self.url + "/activate.html", timeout=10).read()
        self.assertIn(b"/api/activate", body)


@unittest.skipIf(_HAS_WEBVIEW, "pywebview presente: run() aprirebbe una finestra vera")
class FallbackTest(unittest.TestCase):
    def test_run_raises_importerror_without_pywebview(self):
        # senza pywebview, run() solleva ImportError -> main() ricade su Tkinter
        with self.assertRaises(ImportError):
            webview_app.run()


if __name__ == "__main__":
    unittest.main()
