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

Click-to-select/isolate: gltf.py now gives every leaf INSTANCE its own
Material (not deduped by colour -- see its module docstring), so
model-viewer's public materialFromPoint(x, y) API identifies exactly
which placement was clicked, even two instances of the same part.
Selecting one instance brightens it and fades every other material to
low alpha (alphaMode="BLEND" is set at export time for this) -- a real
isolate effect, not just a recolour, using only documented Material API
(pbrMetallicRoughness.setBaseColorFactor). Clicking a BOM row selects
ALL instances sharing that part name instead of a single object
identity, since a BOM line is a part TYPE, not one specific placement.

Part sheet export ("Esporta scheda ricambio"): once a part is selected,
model-viewer's public toBlob() captures the isolated view exactly as
shown (dimmed rest included), which gets stamped with the part's name
and BOM count on a plain <canvas> and downloaded as one PNG -- for a
technician who's found a defective part and wants a one-image reference
to request the replacement, the simplest version of that: a picture and
a code, not a full report generator.
"""

from __future__ import annotations

import html
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
#bom tr.part{{cursor:pointer}}
#bom tr.part:hover td{{background:#333}}
#bom tr.part.selected td{{background:#c77a2e;color:#fff}}
#reset-btn,#export-btn{{position:absolute;top:12px;padding:6px 12px;border-radius:6px;
           border:1px solid #555;background:rgba(20,20,20,0.85);color:#eee;
           font:inherit;cursor:pointer}}
#reset-btn{{left:12px}}
#export-btn{{left:120px}}
#reset-btn:hover,#export-btn:hover{{border-color:#c77a2e}}
#export-btn:disabled{{opacity:0.4;cursor:not-allowed}}
</style>
</head>
<body>
<model-viewer id="mv" src="model.glb" camera-controls auto-rotate
             shadow-intensity="0.6" exposure="1.1" field-of-view="30deg"></model-viewer>
<button id="reset-btn" type="button">Mostra tutto</button>
<button id="export-btn" type="button" disabled>Esporta scheda ricambio</button>
{bom_html}
<script>{select_js}</script>
</body>
</html>
"""

# Shared with the web demo's app.js -- same materialFromPoint-based
# selection logic, only the DOM ids differ (kept a separate literal
# here rather than a shared file since one is embedded in a Python
# f-string template and the other loads as a plain <script src>).
_SELECT_JS = """
(function(){
  var mv = document.getElementById('mv');
  var exportBtn = document.getElementById('export-btn');
  var HIGHLIGHT = [1.0, 0.55, 0.05, 1.0];
  var DIM_ALPHA = 0.12;
  var originalColors = null;
  var selectedName = null;
  var selectedCount = null;

  function cacheColors(){
    originalColors = new Map();
    mv.model.materials.forEach(function(m){
      originalColors.set(m, m.pbrMetallicRoughness.baseColorFactor.slice());
    });
  }
  function resetAll(){
    if (!originalColors) return;
    mv.model.materials.forEach(function(m){
      m.pbrMetallicRoughness.setBaseColorFactor(originalColors.get(m));
    });
    setBomSelection(null);
  }
  function selectMaterial(material){
    if (!originalColors) return;
    mv.model.materials.forEach(function(m){
      var orig = originalColors.get(m);
      if (m === material){
        m.pbrMetallicRoughness.setBaseColorFactor(HIGHLIGHT);
      } else {
        m.pbrMetallicRoughness.setBaseColorFactor([orig[0], orig[1], orig[2], DIM_ALPHA]);
      }
    });
    setBomSelection(material.name);
  }
  function selectByName(name){
    if (!originalColors) return;
    mv.model.materials.forEach(function(m){
      var orig = originalColors.get(m);
      if (m.name === name){
        m.pbrMetallicRoughness.setBaseColorFactor(HIGHLIGHT);
      } else {
        m.pbrMetallicRoughness.setBaseColorFactor([orig[0], orig[1], orig[2], DIM_ALPHA]);
      }
    });
    setBomSelection(name);
  }
  function setBomSelection(name){
    document.querySelectorAll('#bom tr.part').forEach(function(row){
      row.classList.toggle('selected', name !== null && row.dataset.partName === name);
    });
    selectedName = name;
    if (name !== null){
      var row = document.querySelector('#bom tr.part[data-part-name="' + CSS.escape(name) + '"]');
      selectedCount = row ? row.dataset.partCount : null;
    } else {
      selectedCount = null;
    }
    exportBtn.disabled = (selectedName === null);
  }

  async function exportPartSheet(){
    // mv.toDataURL() (no options, straight to displayCanvas().toDataURL())
    // instead of mv.toBlob({idealAspect:true}): the latter routes through
    // an internal offscreen-canvas resize+crop step that was measured to
    // return a fully transparent capture in some layouts (reproduced on
    // the web demo's model-viewer instance -- consistent, same byte size
    // every time, so not a timing race: no amount of waiting or retrying
    // fixed it). Losing the idealAspect crop is a cosmetic trade for a
    // capture that actually contains the model.
    if (!selectedName) return;
    var dataUrl = mv.toDataURL('image/png');
    var img = new Image();
    await new Promise(function(resolve){ img.onload = resolve; img.src = dataUrl; });

    var headerH = 64;
    var canvas = document.createElement('canvas');
    canvas.width = img.width;
    canvas.height = img.height + headerH;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, headerH);
    ctx.fillStyle = '#000000';
    ctx.font = 'bold 22px sans-serif';
    ctx.fillText(selectedName, 12, 28);
    ctx.font = '16px sans-serif';
    ctx.fillText('Quantita\\' nell\\'assieme: ' + (selectedCount || '?'), 12, 50);

    canvas.toBlob(function(sheetBlob){
      var a = document.createElement('a');
      a.href = URL.createObjectURL(sheetBlob);
      a.download = 'scheda_' + selectedName.replace(/[^a-z0-9]+/gi, '_') + '.png';
      a.click();
    }, 'image/png');
  }

  mv.addEventListener('load', cacheColors);
  mv.addEventListener('click', function(ev){
    var material = mv.materialFromPoint(ev.clientX, ev.clientY);
    if (material) selectMaterial(material); else resetAll();
  });
  document.getElementById('reset-btn').addEventListener('click', resetAll);
  exportBtn.addEventListener('click', exportPartSheet);
  document.querySelectorAll('#bom tr.part').forEach(function(row){
    row.addEventListener('click', function(){ selectByName(row.dataset.partName); });
  });
})();
"""


def _bom_html(bom_lines: list[tuple[str, int]] | None) -> str:
    if not bom_lines:
        return ""
    rows = "".join(
        f'<tr class="part" data-part-name="{html.escape(name)}" data-part-count="{count}">'
        f'<td>{html.escape(name)}</td><td class="qty">x{count}</td></tr>'
        for name, count in bom_lines
    )
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
        fh.write(_PAGE_TEMPLATE.format(bom_html=_bom_html(bom_lines), select_js=_SELECT_JS))
    if os.path.exists(_MODEL_VIEWER_JS):
        shutil.copy(_MODEL_VIEWER_JS, os.path.join(work_dir, "model-viewer.min.js"))

    handler_cls = lambda *a, **kw: _QuietHandler(*a, directory=work_dir, **kw)  # noqa: E731
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open(f"http://127.0.0.1:{port}/viewer.html")
