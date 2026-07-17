# -*- mode: python ; coding: utf-8 -*-
#
# Spec PyInstaller per l'app desktop Balzar (Balzar Studio + Balzar Live).
# Build:
#   pip install -r requirements.txt pyinstaller
#   pyinstaller balzar.spec          # NON `--onefile ... balzar-app.py`
#
# Cosa questo .spec aggiunge rispetto al comando base:
#  - datas: i file JS di terze parti vendorizzati (model-viewer per la vista
#    3D; jsQR + qr-transport-core + qr-camera-scanner per la scansione QR con
#    fotocamera nel browser). SENZA questi, la vista 3D e la scansione
#    fotocamera si romperebbero nel pacchetto: a runtime i moduli li cercano
#    in balzar/assets.py, che sotto PyInstaller guarda in sys._MEIPASS (la
#    radice del bundle, dove dest '.' li colloca).
#  - icona applicazione, per piattaforma (Windows .ico / macOS .icns), se
#    presente -- guardata da os.path.exists cosi' una icona mancante non
#    rompe la build.
#
# La libreria nativa libzbar (LGPL-2.1, lettura QR via pyzbar/ctypes) viene
# inclusa automaticamente da PyInstaller come binario separato (verificato su
# Linux, CLAUDE.md §9.13) -- coerente con l'obbligo LGPL di linking dinamico.

import os
import sys

block_cipher = None

# --- asset vendorizzati da includere nel bundle (dest '.' = radice) ---------
_vendored = [
    "model-viewer.min.js",
    "jsQR.min.js",
    "qr-transport-core.js",
    "qr-camera-scanner.js",
]
datas = [(name, ".") for name in _vendored if os.path.exists(name)]

# --- icona per piattaforma (guardata: se manca, build senza icona) ----------
if sys.platform == "darwin":
    _icon = "assets/balzar.icns"
elif sys.platform == "win32":
    _icon = "assets/balzar.ico"
else:
    _icon = "assets/balzar.png"
icon = _icon if os.path.exists(_icon) else None


a = Analysis(
    ['balzar-app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='balzar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

# Su macOS, produce anche un bundle .app (oltre all'eseguibile) cosi'
# Finder/Gatekeeper lo trattano come un'applicazione vera.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name='Balzar.app',
        icon=icon,
        bundle_identifier='com.michelealdeni.balzar',
        info_plist={
            'CFBundleName': 'Balzar',
            'CFBundleDisplayName': 'Balzar',
            'CFBundleShortVersionString': '0.9.0',
            'CFBundleVersion': '0.9.0b1',
            'NSHighResolutionCapable': True,
        },
    )
