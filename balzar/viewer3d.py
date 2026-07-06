"""Local 3D viewing: GLB + BOM -> a page in the system's default browser.

Same "delegate rendering to a mature engine" principle as gltf.py's
choice of target (model-viewer/Three.js, not a rasterizer written here).
This module is the last mile for the *desktop* app specifically: Tkinter
cannot show a GLB itself, so instead of writing a 3D view widget from
scratch, this writes a tiny local web page (vendored `model-viewer.min.js`
sits at the repo root — no CDN, so this keeps working with no network,
consistent with balzar's offline-first stance) and opens it.

`file://` does not work for this: Chrome (and other browsers) block the
fetch/XHR that <model-viewer> uses internally to load the GLB when the
page origin is `file://` ("CORS policy: cross origin requests only
supported for http/https"), even though the GLB sits right next to the
HTML in the same directory. Verified in session while producing the
diagnostic screenshots in CLAUDE.md SS9.7. The fix is the same one used
there: serve the temp directory over plain HTTP on localhost instead.
"""

from __future__ import annotations

import http.server
import os
import shutil
import threading
import webbrowser

_MODEL_VIEWER_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "model-viewer.min.js")

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>balzar — assieme 3D</title>
<script src="model-viewer.min.js"></script>
<style>
html,body{{margin:0;height:100%;background:#1c1c1c;font-family:sans-serif}}
model-viewer{{width:100%;height:100%}}
#bom{{position:absolute;top:12px;right:12px;max-height:80vh;overflow-y:auto;
     background:rgba(20,20,20,0.85);color:#eee;padding:10px 14px;border-radius:6px;
     font-size:13px;line-height:1.5}}
#bom h3{{margin:0 0 6px 0;font-size:14px;color:#fff}}
#bom table{{border-collapse:collapse}}
#bom td{{padding:1px 6px}}
#bom td.qty{{text-align:right;color:#9cf}}
</style>
</head>
<body>
<model-viewer src="model.glb" camera-controls auto-rotate
             shadow-intensity="0.6" exposure="1.1" field-of-view="30deg"></model-viewer>
{bom_html}
</body>
</html>
"""


def _bom_html(bom_lines: list[tuple[str, int]] | None) -> str:
    if not bom_lines:
        return ""
    rows = "".join(f"<tr><td>{name}</td><td class=\"qty\">x{count}</td></tr>"
                   for name, count in bom_lines)
    return f'<div id="bom"><h3>Distinta base</h3><table>{rows}</table></div>'


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - matches base class signature
        pass  # the desktop app has no console for this to usefully go to


def open_glb_in_browser(glb: bytes, bom_lines: list[tuple[str, int]] | None,
                        work_dir: str) -> None:
    """Write model.glb + viewer.html + a copy of model-viewer.min.js into
    `work_dir`, serve it on an ephemeral localhost port, open the default
    browser. `work_dir` is the caller's responsibility (a TemporaryDirectory
    the caller keeps alive for as long as the viewer might be open — this
    function does not clean up after itself, since the HTTP server keeps
    serving from it for the life of the background thread)."""
    with open(os.path.join(work_dir, "model.glb"), "wb") as fh:
        fh.write(glb)
    with open(os.path.join(work_dir, "viewer.html"), "w", encoding="utf-8") as fh:
        fh.write(_PAGE_TEMPLATE.format(bom_html=_bom_html(bom_lines)))
    if os.path.exists(_MODEL_VIEWER_JS):
        shutil.copy(_MODEL_VIEWER_JS, os.path.join(work_dir, "model-viewer.min.js"))

    handler_cls = lambda *a, **kw: _QuietHandler(*a, directory=work_dir, **kw)  # noqa: E731
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open(f"http://127.0.0.1:{port}/viewer.html")
