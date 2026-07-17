"""Test della risoluzione dei file vendorizzati (balzar/assets.py). In
sviluppo devono puntare alla radice del repo e i file devono esistere davvero
-- e' la garanzia che il pacchetto PyInstaller (che li aggiunge a datas) non
si rompa perche' un nome e' cambiato senza aggiornare il .spec."""

import os
import unittest

from balzar import assets

# I quattro file che il .spec bundla e che viewer3d/live_scan_server servono.
VENDORED = (
    "model-viewer.min.js",
    "jsQR.min.js",
    "qr-transport-core.js",
    "qr-camera-scanner.js",
)


class AssetsTest(unittest.TestCase):
    def test_asset_root_is_repo_root_in_dev(self):
        # la radice deve contenere il package balzar/
        self.assertTrue(os.path.isdir(os.path.join(assets.asset_root(), "balzar")))

    def test_vendored_path_joins_name(self):
        p = assets.vendored_path("model-viewer.min.js")
        self.assertTrue(p.endswith("model-viewer.min.js"))
        self.assertEqual(os.path.dirname(p), assets.asset_root())

    def test_all_vendored_files_exist(self):
        for name in VENDORED:
            with self.subTest(name=name):
                self.assertTrue(os.path.exists(assets.vendored_path(name)),
                                f"file vendorizzato mancante: {name}")


if __name__ == "__main__":
    unittest.main()
