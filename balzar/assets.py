"""Percorso dei file vendorizzati di terze parti (JS: model-viewer, jsQR,
qr-transport-core, qr-camera-scanner) sia in sviluppo sia dentro un binario
PyInstaller.

- In sviluppo, i file vivono nella radice del repository, un livello sopra il
  package `balzar/`.
- In un bundle PyInstaller (onefile o onedir) vengono estratti in
  `sys._MEIPASS`; il file `balzar.spec` li aggiunge a `datas` con destinazione
  `.` (la radice del bundle), quindi qui vanno cercati la'.

Un solo punto di verita' per "dove stanno gli asset", cosi' un cambio di
packaging non va inseguito in piu' moduli (viewer3d.py, live_scan_server.py)."""

from __future__ import annotations

import os
import sys


def asset_root() -> str:
    """Directory che contiene i file vendorizzati, corretta anche sotto
    PyInstaller (`sys.frozen`/`sys._MEIPASS`)."""
    if getattr(sys, "frozen", False):
        # in un bundle PyInstaller gli asset di `datas` con dest '.' stanno qui
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def vendored_path(name: str) -> str:
    """Percorso assoluto di un file vendorizzato (es. 'model-viewer.min.js')."""
    return os.path.join(asset_root(), name)
