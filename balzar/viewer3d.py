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

Search bar + alarm table (maintenance flow): typing a component name
reuses the exact same highlight path as clicking a BOM row (now
generalized to a *set* of names, not one, since one alarm can affect
several components at once). An optional two-column CSV
(codice_allarme,nome_componente, see parse_alarm_csv) maps an alarm
code to the component name(s) it affects -- an operator reads a code
off the machine, types it here, and sees exactly which part lit up
without knowing the CAD component name in advance. The mapping can be
uploaded by hand in the browser (client-side, no server round-trip) or
baked into the page at generation time via open_glb_in_browser's
alarm_rows parameter, which is what makes automation possible: a page
generated once with the current alarm table embedded can be re-opened
with a `?q=<code>` URL parameter (from a script, an HMI button, a QR
code) and the highlight happens with zero typing -- see CLAUDE.md for
the proposed automation mechanisms this enables.
"""

from __future__ import annotations

import csv
import html
import http.server
import json
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
#search-bar{{position:absolute;bottom:12px;left:12px;right:12px;display:flex;gap:8px;
            align-items:center;flex-wrap:wrap}}
#search-input{{flex:1;min-width:200px;padding:6px 10px;border-radius:6px;border:1px solid #555;
              background:rgba(20,20,20,0.85);color:#eee;font:inherit}}
#search-btn,#alarm-csv-label{{padding:6px 10px;border-radius:6px;border:1px solid #555;
                             background:rgba(20,20,20,0.85);color:#eee;font:inherit;
                             cursor:pointer;font-size:13px}}
#search-btn:hover,#alarm-csv-label:hover{{border-color:#c77a2e}}
#search-note{{position:absolute;bottom:52px;left:12px;right:12px;margin:0;color:#eee;
             font-size:12px;background:rgba(20,20,20,0.7);padding:4px 8px;border-radius:4px;
             max-height:60px;overflow-y:auto}}
</style>
</head>
<body>
<model-viewer id="mv" src="model.glb" camera-controls auto-rotate
             shadow-intensity="0.6" exposure="1.1" field-of-view="30deg"></model-viewer>
<button id="reset-btn" type="button">Mostra tutto</button>
<button id="export-btn" type="button" disabled>Esporta scheda ricambio</button>
{bom_html}
<p id="search-note"></p>
<div id="search-bar">
  <input id="search-input" type="text" placeholder="Cerca componente o codice allarme…">
  <button id="search-btn" type="button">Cerca</button>
  <label id="alarm-csv-label" for="alarm-csv-input">Carica tabella allarmi (CSV)</label>
  <input id="alarm-csv-input" type="file" accept=".csv,text/csv" style="display:none">
</div>
<script>
window.__BALZAR_ALARM_ROWS__ = {alarm_rows_json};
{select_js}
</script>
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
  var searchInput = document.getElementById('search-input');
  var searchBtn = document.getElementById('search-btn');
  var searchNote = document.getElementById('search-note');
  var alarmCsvInput = document.getElementById('alarm-csv-input');
  var HIGHLIGHT = [1.0, 0.55, 0.05, 1.0];
  var DIM_ALPHA = 0.12;
  var originalColors = null;
  var selectedNames = [];   // names currently highlighted -- 0, 1 or many
  var selectedCount = null; // BOM count, only meaningful for exactly 1 name
  // alarm code (trimmed, uppercased) -> [component name, ...]. A code can
  // map to several components (one alarm, several affected parts).
  var alarmMap = new Map();
  var allPartNames = Array.prototype.map.call(
      document.querySelectorAll('#bom tr.part'), function(row){ return row.dataset.partName; });

  function loadAlarmRows(rows){
    (rows || []).forEach(function(pair){
      var key = String(pair[0]).trim().toUpperCase();
      if (!alarmMap.has(key)) alarmMap.set(key, []);
      alarmMap.get(key).push(pair[1]);
    });
  }

  function cacheColors(){
    originalColors = new Map();
    mv.model.materials.forEach(function(m){
      originalColors.set(m, m.pbrMetallicRoughness.baseColorFactor.slice());
    });
    loadAlarmRows(window.__BALZAR_ALARM_ROWS__);
    var q = new URLSearchParams(location.search).get('q');
    if (q){ searchInput.value = q; runSearch(q); }
  }
  function resetAll(){
    if (!originalColors) return;
    mv.model.materials.forEach(function(m){
      m.pbrMetallicRoughness.setBaseColorFactor(originalColors.get(m));
    });
    setSelection([]);
  }
  function highlightNames(names){
    if (!originalColors) return;
    var nameSet = new Set(names);
    mv.model.materials.forEach(function(m){
      var orig = originalColors.get(m);
      if (nameSet.has(m.name)){
        m.pbrMetallicRoughness.setBaseColorFactor(HIGHLIGHT);
      } else {
        m.pbrMetallicRoughness.setBaseColorFactor([orig[0], orig[1], orig[2], DIM_ALPHA]);
      }
    });
    setSelection(names);
  }
  function selectMaterial(material){ highlightNames([material.name]); }
  function selectByName(name){ highlightNames([name]); }
  function setSelection(names){
    var nameSet = new Set(names);
    document.querySelectorAll('#bom tr.part').forEach(function(row){
      row.classList.toggle('selected', nameSet.has(row.dataset.partName));
    });
    selectedNames = names;
    if (names.length === 1){
      var row = document.querySelector('#bom tr.part[data-part-name="' + CSS.escape(names[0]) + '"]');
      selectedCount = row ? row.dataset.partCount : null;
    } else {
      selectedCount = null;
    }
    // a part sheet is a picture of ONE part -- export stays disabled for
    // zero or multiple matches (an alarm affecting several components has
    // no single "the" part to print a sheet for).
    exportBtn.disabled = (selectedNames.length !== 1);
  }

  function parseAlarmCsv(text){
    // Simple two-column CSV (codice_allarme,nome_componente), no quoted-
    // comma support -- a full RFC4180 parser is overkill for a two-field
    // lookup table, declared honestly rather than silently mishandling
    // an edge case nobody asked for.
    var map = new Map();
    text.split(/\\r?\\n/).forEach(function(line, i){
      if (!line.trim()) return;
      var parts = line.split(',');
      if (parts.length < 2) return;
      var code = parts[0].trim();
      if (i === 0 && /codice|code|allarme|alarm/i.test(code)) return; // skip header row
      var name = parts.slice(1).join(',').trim();
      var key = code.toUpperCase();
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(name);
    });
    return map;
  }

  function runSearch(query){
    query = (query || '').trim();
    if (!query){ resetAll(); return; }
    var key = query.toUpperCase();
    if (alarmMap.has(key)){
      var names = alarmMap.get(key);
      highlightNames(names);
      searchNote.textContent = 'Allarme ' + query + ': ' + names.length +
        ' componente/i evidenziato/i (' + names.join(', ') + ').';
      return;
    }
    var qLower = query.toLowerCase();
    var exact = allPartNames.filter(function(n){ return n.toLowerCase() === qLower; });
    var matches = exact.length ? exact : allPartNames.filter(function(n){
      return n.toLowerCase().indexOf(qLower) !== -1;
    });
    if (matches.length){
      highlightNames(matches);
      searchNote.textContent = matches.length + ' componente/i trovato/i per "' + query + '".';
    } else {
      resetAll();
      searchNote.textContent = 'Nessun componente o codice allarme trovato per "' + query + '".';
    }
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
    if (selectedNames.length !== 1) return;
    var selectedName = selectedNames[0];
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
  searchBtn.addEventListener('click', function(){ runSearch(searchInput.value); });
  searchInput.addEventListener('keydown', function(ev){
    if (ev.key === 'Enter') runSearch(searchInput.value);
  });
  alarmCsvInput.addEventListener('change', function(){
    var file = alarmCsvInput.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(){
      alarmMap = parseAlarmCsv(String(reader.result));
      searchNote.textContent = 'Tabella allarmi caricata: ' + alarmMap.size + ' codici allarme.';
    };
    reader.readAsText(file);
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


def parse_alarm_csv_text(text: str) -> list[tuple[str, str]]:
    """Two-column CSV (codice_allarme,nome_componente) -> [(code, name), ...].
    One alarm code can appear on several rows (several affected
    components); an optional header row is detected and skipped by the
    same heuristic as the browser-side parser (first cell mentions
    "code"/"codice"/"allarme"/"alarm"), not required. Uses the stdlib
    `csv` module here (unlike the client-side JS parser, which is a
    plain split() -- a real CSV reader is cheap in Python and handles
    quoted commas correctly, so no need to declare the same limitation
    twice). Takes text directly (not a path) so it works equally on a
    CSV loaded from disk or one unpacked from a bundle (balzar/bundle.py)
    in memory."""
    import io as _io

    rows: list[tuple[str, str]] = []
    for i, cells in enumerate(csv.reader(_io.StringIO(text))):
        if len(cells) < 2 or not cells[0].strip():
            continue
        code = cells[0].strip()
        if i == 0 and any(w in code.lower() for w in ("code", "codice", "allarme", "alarm")):
            continue
        rows.append((code, ",".join(cells[1:]).strip()))
    return rows


def parse_alarm_csv(path: str) -> list[tuple[str, str]]:
    """parse_alarm_csv_text, reading from a file path."""
    with open(path, encoding="utf-8") as fh:
        return parse_alarm_csv_text(fh.read())


def open_glb_in_browser(glb: bytes, bom_lines: list[tuple[str, int]] | None,
                        work_dir: str,
                        alarm_rows: list[tuple[str, str]] | None = None) -> None:
    """Write model.glb + viewer.html + a copy of model-viewer.min.js into
    `work_dir`, serve it on an ephemeral localhost port, open the default
    browser. `work_dir` is the caller's responsibility (a TemporaryDirectory
    the caller keeps alive for as long as the viewer might be open — this
    function does not clean up after itself, since the HTTP server keeps
    serving from it for the life of the background thread).

    `alarm_rows` (optional, from parse_alarm_csv or built by hand) gets
    baked into the page as a JS literal so the alarm-code search works
    immediately on load, with no manual CSV upload step -- the piece that
    makes automation possible: a page generated once can be re-opened
    with `?q=<code>` (from a script, an HMI button, a QR code) and the
    matching component highlights with zero typing."""
    with open(os.path.join(work_dir, "model.glb"), "wb") as fh:
        fh.write(glb)
    # </script> inside a component/alarm name would otherwise close the
    # tag early -- escape the slash so the JSON stays inert text to the
    # HTML parser, same mitigation as embedding any untrusted JSON in a
    # <script> block.
    alarm_rows_json = json.dumps(alarm_rows or []).replace("</", "<\\/")
    with open(os.path.join(work_dir, "viewer.html"), "w", encoding="utf-8") as fh:
        fh.write(_PAGE_TEMPLATE.format(
            bom_html=_bom_html(bom_lines),
            alarm_rows_json=alarm_rows_json,
            select_js=_SELECT_JS))
    if os.path.exists(_MODEL_VIEWER_JS):
        shutil.copy(_MODEL_VIEWER_JS, os.path.join(work_dir, "model-viewer.min.js"))

    handler_cls = lambda *a, **kw: _QuietHandler(*a, directory=work_dir, **kw)  # noqa: E731
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open(f"http://127.0.0.1:{port}/viewer.html")


def open_bundle_in_browser(bundle_data: bytes, work_dir: str) -> None:
    """Same as open_glb_in_browser, but the input is a multi-document
    bundle (balzar/bundle.py) instead of a bare GLB -- unpacks the "3d"
    item into the model.glb + BOM this module already knows how to show,
    and any "csv" item(s) straight into alarm_rows with no manual upload
    step in the browser: one scan, everything already wired.

    Deliberately local imports (scene3d.py/gltf.py, not used by the rest
    of this module) so the plain GLB+BOM path above stays exactly as
    decoupled from the 3D encoding stack as before -- this function is
    the only place in viewer3d.py that needs to know a bundle exists."""
    from .bundle import KIND_3D, KIND_CSV, BundleError, decode_bundle
    from .gltf import scene3d_to_glb
    from .scene3d import Scene3DError, decode_payload as decode_scene, generate_bom

    try:
        items = decode_bundle(bundle_data)
    except BundleError as exc:
        raise ValueError(f"bundle non valido: {exc}") from None

    three_d_items = [it for it in items if it.kind == KIND_3D]
    if not three_d_items:
        kinds = ", ".join(sorted({it.kind for it in items})) or "nessuno"
        raise ValueError(f"il bundle non contiene un assieme 3D da visualizzare (trovato: {kinds})")
    # a bundle with more than one 3D item is valid (the format doesn't
    # forbid it) but this viewer shows exactly one model -- the first one,
    # not silently merged or dropped without saying so
    try:
        scene = decode_scene(three_d_items[0].data)
    except Scene3DError as exc:
        raise ValueError(f"assieme 3D nel bundle non valido: {exc}") from None

    glb = scene3d_to_glb(scene)
    bom = generate_bom(scene)
    bom_lines = [(e.name, e.count) for e in bom]

    alarm_rows: list[tuple[str, str]] = []
    for csv_item in (it for it in items if it.kind == KIND_CSV):
        alarm_rows.extend(parse_alarm_csv_text(csv_item.data.decode("utf-8")))

    open_glb_in_browser(glb, bom_lines, work_dir, alarm_rows=alarm_rows or None)
