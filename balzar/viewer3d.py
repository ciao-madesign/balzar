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

Search bar + component info table (maintenance flow): typing ANY value
searches every column of an uploaded CSV, not just a fixed
codice_allarme/nome_componente pair -- real tables in the field have
whatever columns the maintenance team already keeps (component name,
alarm code, function, spare part number, "cleans every 6 months"...),
in whatever order, under whatever header names. parse_component_table
(below) makes no assumption about column meaning beyond requiring a
header row (there is no way to know what an arbitrary column like
"info" means otherwise); the ONE column tied to the 3D model -- the
one used to drive highlightNames -- is auto-detected in JS at load
time by testing which column's values match the most real BOM part
names, not by column position or header wording, so a "componente" and
a "nome_componente" column both work with zero configuration and zero
assumption about where it sits in the row. A search match shows the
WHOLE matching row(s), not just a highlighted part: the searchNote
line stays a short summary, the new #search-results panel renders the
full row(s) with the table's own column headers as labels. The mapping
can be uploaded by hand in the browser (client-side, no server
round-trip) or baked into the page at generation time via
open_glb_in_browser's info_table parameter, which is what makes
automation possible: a page generated once with the current table
embedded can be re-opened with a `?q=<code>` URL parameter (from a
script, an HMI button, a QR code) and the search happens with zero
typing -- see CLAUDE.md for the proposed automation mechanisms this
enables.
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
#search-panel{{position:absolute;bottom:52px;left:12px;right:12px;display:flex;
              flex-direction:column;gap:6px;max-height:50vh}}
#search-note{{margin:0;color:#eee;font-size:12px;background:rgba(20,20,20,0.7);
             padding:4px 8px;border-radius:4px;flex-shrink:0;max-height:60px;overflow-y:auto}}
#search-results{{overflow:auto;background:rgba(20,20,20,0.92);border-radius:6px;display:none}}
#search-results.open{{display:block}}
#search-results table{{width:100%;border-collapse:collapse;font-size:12px}}
#search-results th,#search-results td{{padding:4px 8px;border-bottom:1px solid #333;
                                       text-align:left;color:#eee}}
#search-results th{{background:#2a2a2a;position:sticky;top:0}}
#doc-index{{position:absolute;top:54px;left:12px;max-width:260px;max-height:60vh;overflow-y:auto;
           background:rgba(20,20,20,0.85);color:#eee;padding:8px 12px;border-radius:6px;
           font-size:13px}}
body.no-3d #doc-index{{top:12px}}
#doc-index h3{{margin:0 0 6px 0;font-size:14px;color:#fff}}
#doc-index ul{{list-style:none;margin:0;padding:0}}
#doc-index li{{padding:3px 4px;cursor:pointer;border-radius:4px;display:flex;gap:6px;align-items:baseline}}
#doc-index li:hover{{background:#333}}
#doc-index .role{{font-size:10px;text-transform:uppercase;color:#9cf;border:1px solid #456;
                 border-radius:3px;padding:0 4px}}
#doc-overlay{{position:absolute;top:24px;left:24px;right:24px;bottom:24px;background:#fbfbfb;
             color:#111;border-radius:8px;display:none;flex-direction:column;overflow:hidden;
             box-shadow:0 4px 30px rgba(0,0,0,0.6)}}
#doc-overlay.open{{display:flex}}
#doc-overlay-head{{display:flex;align-items:center;gap:12px;padding:8px 14px;border-bottom:1px solid #ddd;
                  background:#efefef}}
#doc-overlay-title{{font-weight:bold;flex:1;font-size:14px;word-break:break-all}}
#doc-overlay-close{{border:1px solid #999;background:#fff;border-radius:6px;padding:4px 10px;cursor:pointer}}
#doc-overlay-body{{flex:1;overflow:auto;padding:14px}}
#doc-overlay-body pre{{white-space:pre-wrap;word-break:break-word;font-size:13px;margin:0}}
#doc-overlay-body table{{border-collapse:collapse;font-size:13px}}
#doc-overlay-body td,#doc-overlay-body th{{border:1px solid #ccc;padding:2px 8px;text-align:left}}
#doc-overlay-body img{{max-width:100%;height:auto}}
</style>
</head>
<body class="{body_class}">
{threed_section}
{bom_html}
{doc_index_html}
<div id="doc-overlay">
  <div id="doc-overlay-head">
    <span id="doc-overlay-title"></span>
    <button id="doc-overlay-close" type="button">Chiudi</button>
  </div>
  <div id="doc-overlay-body"></div>
</div>
<script>
window.__BALZAR_INFO_TABLE__ = {info_table_json};
window.__BALZAR_DOCS__ = {docs_json};
{select_js}
{doc_js}
</script>
</body>
</html>
"""

# The 3D cluster (model-viewer + its controls), interpolated into
# {threed_section} only when the bundle actually has a 3D item -- a
# document-only bundle omits it entirely and shows just the index.
_THREED_SECTION = """<model-viewer id="mv" src="model.glb" camera-controls auto-rotate
             shadow-intensity="0.6" exposure="1.1" field-of-view="30deg"></model-viewer>
<button id="reset-btn" type="button">Mostra tutto</button>
<button id="export-btn" type="button" disabled>Esporta scheda ricambio</button>
<div id="search-panel">
  <div id="search-results"></div>
  <p id="search-note"></p>
</div>
<div id="search-bar">
  <input id="search-input" type="text" placeholder="Cerca in qualunque colonna della tabella…">
  <button id="search-btn" type="button">Cerca</button>
  <label id="alarm-csv-label" for="alarm-csv-input">Carica tabella componenti (CSV)</label>
  <input id="alarm-csv-input" type="file" accept=".csv,text/csv" style="display:none">
</div>"""

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
  var searchResultsEl = document.getElementById('search-results');
  var HIGHLIGHT = [1.0, 0.55, 0.05, 1.0];
  var DIM_ALPHA = 0.12;
  var originalColors = null;
  var selectedNames = [];   // names currently highlighted -- 0, 1 or many
  var selectedCount = null; // BOM count, only meaningful for exactly 1 name
  // The component info table: arbitrary headers/rows (parse_component_table
  // on the Python side), no assumption about which column means what.
  // componentColumnIndex is auto-detected once per table load (see
  // detectComponentColumn) by testing which column's values match the
  // most real BOM part names -- not by header wording or position, so a
  // "componente"/"nome componente"/"nome_componente" column all work with
  // zero configuration, and a table with no component-like column at all
  // still searches/displays fine, just without 3D highlighting.
  var tableHeaders = [];
  var tableRows = [];
  var componentColumnIndex = -1;
  var allPartNames = Array.prototype.map.call(
      document.querySelectorAll('#bom tr.part'), function(row){ return row.dataset.partName; });
  // display label (BOM row name, e.g. "RESERVOIR1") -> exact glTF
  // material names to highlight for it -- a single-item array equal to
  // the label itself for an ordinary leaf row, or a whole collapsed
  // sub-assembly's own descendant materials (scene3d.py generate_bom's
  // collapse_names) -- built once from data-material-names so neither
  // highlightNames nor the click handler ever reconstruct the naming
  // convention (COLLAPSE_SEPARATOR) themselves.
  var labelToMaterialNames = new Map();
  var materialNameToLabel = new Map();
  document.querySelectorAll('#bom tr.part').forEach(function(row){
    var names = JSON.parse(row.dataset.materialNames || '[]');
    labelToMaterialNames.set(row.dataset.partName, names);
    names.forEach(function(n){ materialNameToLabel.set(n, row.dataset.partName); });
  });

  // For each column, count how many of its (trimmed, case-insensitive)
  // values match a real BOM part name -- the column with the most
  // matches is "the component column" for highlighting purposes. Not
  // header wording or position: real tables call this column anything
  // ("componente", "nome componente", "parte"...), and testing content
  // instead of the header name means zero configuration is needed and
  // zero assumption about column order. A table with no column that
  // matches any real part name returns -1 -- rows still search/display
  // fine, they just never drive a 3D highlight.
  function detectComponentColumn(headers, rows){
    if (!headers.length || !rows.length) return -1;
    var partNameSet = new Set(allPartNames.map(function(n){ return n.toLowerCase(); }));
    var bestCol = -1, bestCount = 0;
    for (var col = 0; col < headers.length; col++){
      var count = 0;
      rows.forEach(function(row){
        var v = (row[col] || '').trim().toLowerCase();
        if (v && partNameSet.has(v)) count++;
      });
      if (count > bestCount){ bestCount = count; bestCol = col; }
    }
    return bestCount > 0 ? bestCol : -1;
  }

  function loadInfoTable(table){
    tableHeaders = (table && table.headers) || [];
    tableRows = (table && table.rows) || [];
    componentColumnIndex = detectComponentColumn(tableHeaders, tableRows);
  }

  function escapeHtml(s){
    var d = document.createElement('div');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  function renderResultsTable(headers, rows){
    if (!headers || !rows || !rows.length){
      searchResultsEl.innerHTML = '';
      searchResultsEl.classList.remove('open');
      return;
    }
    var html = '<table><thead><tr>' + headers.map(function(h){
      return '<th>' + escapeHtml(h) + '</th>';
    }).join('') + '</tr></thead><tbody>';
    rows.forEach(function(row){
      html += '<tr>' + row.map(function(c){ return '<td>' + escapeHtml(c) + '</td>'; }).join('') + '</tr>';
    });
    html += '</tbody></table>';
    searchResultsEl.innerHTML = html;
    searchResultsEl.classList.add('open');
  }

  function cacheColors(){
    originalColors = new Map();
    mv.model.materials.forEach(function(m){
      originalColors.set(m, m.pbrMetallicRoughness.baseColorFactor.slice());
    });
    loadInfoTable(window.__BALZAR_INFO_TABLE__);
    if (tableRows.length){
      searchNote.textContent = 'Tabella disponibile (' + tableRows.length + ' riga/e, ' +
        tableHeaders.length + ' colonne: ' + tableHeaders.join(', ') + ') -- cerca subito un valore.';
    }
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
  function highlightNames(labels){
    // labels are display labels (BOM row names) -- expanded here to the
    // exact glTF material names to recolor, so a collapsed sub-assembly
    // highlights precisely its own descendants and setSelection below
    // keeps working off display labels unchanged (BOM row .selected
    // toggling, export-sheet count lookup, etc. are untouched).
    if (!originalColors) return;
    var materialTargets = new Set();
    labels.forEach(function(label){
      (labelToMaterialNames.get(label) || [label]).forEach(function(n){ materialTargets.add(n); });
    });
    mv.model.materials.forEach(function(m){
      var orig = originalColors.get(m);
      if (materialTargets.has(m.name)){
        m.pbrMetallicRoughness.setBaseColorFactor(HIGHLIGHT);
      } else {
        m.pbrMetallicRoughness.setBaseColorFactor([orig[0], orig[1], orig[2], DIM_ALPHA]);
      }
    });
    setSelection(labels);
  }
  function selectMaterial(material){
    // a direct click on the 3D model resolves the clicked material back
    // to its owning label (the whole collapsed group, if it's inside
    // one) so the corresponding BOM row gets selected too -- not just
    // that one exact placement, once it's inside a collapsed group.
    highlightNames([materialNameToLabel.get(material.name) || material.name]);
  }
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

  function parseComponentTable(text){
    // Generic CSV, arbitrary columns -- no quoted-comma support (a full
    // RFC4180 parser is overkill client-side, declared honestly rather
    // than silently mishandling an edge case nobody asked for). The
    // first row is ALWAYS the header: with free-form columns (component
    // name, alarm code, spare part, "cleans every 6 months"...) there is
    // no way to know what a column means without one, so unlike the old
    // fixed two-column format this is a hard requirement now, not a
    // heuristic guess.
    var lines = text.split(/\\r?\\n/).filter(function(l){ return l.trim().length > 0; });
    if (!lines.length) return {headers: [], rows: []};
    var headers = lines[0].split(',').map(function(h){ return h.trim(); });
    var rows = [];
    for (var i = 1; i < lines.length; i++){
      var cells = lines[i].split(',').map(function(c){ return c.trim(); });
      while (cells.length < headers.length) cells.push('');
      if (cells.length > headers.length) cells = cells.slice(0, headers.length);
      rows.push(cells);
    }
    return {headers: headers, rows: rows};
  }

  function runSearch(query){
    query = (query || '').trim();
    if (!query){ resetAll(); renderResultsTable(null, null); return; }
    var qLower = query.toLowerCase();

    if (tableHeaders.length && tableRows.length){
      var matchedRows = tableRows.filter(function(row){
        return row.some(function(cell){ return cell.toLowerCase().indexOf(qLower) !== -1; });
      });
      if (matchedRows.length){
        var componentValues = new Set();
        if (componentColumnIndex >= 0){
          matchedRows.forEach(function(row){
            var v = row[componentColumnIndex];
            if (v) componentValues.add(v);
          });
        }
        if (componentValues.size){
          highlightNames(Array.from(componentValues));
        } else {
          // a purely informational row (no recognized component value)
          // is not a failed search -- show it, just without touching
          // the 3D view, rather than dimming the whole model for no
          // reason.
          resetAll();
        }
        renderResultsTable(tableHeaders, matchedRows);
        searchNote.textContent = matchedRows.length + ' riga/e trovata/e per "' + query + '".';
        return;
      }
    }

    // no table loaded, or nothing matched in it -- fall back to
    // searching BOM part names directly, same as before this table
    // existed at all.
    var exact = allPartNames.filter(function(n){ return n.toLowerCase() === qLower; });
    var matches = exact.length ? exact : allPartNames.filter(function(n){
      return n.toLowerCase().indexOf(qLower) !== -1;
    });
    if (matches.length){
      highlightNames(matches);
      renderResultsTable(null, null);
      searchNote.textContent = matches.length + ' componente/i trovato/i per "' + query + '".';
    } else {
      resetAll();
      renderResultsTable(null, null);
      searchNote.textContent = 'Nessuna corrispondenza trovata per "' + query + '".';
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
      loadInfoTable(parseComponentTable(String(reader.result)));
      renderResultsTable(null, null);
      searchNote.textContent = 'Tabella caricata: ' + tableRows.length + ' riga/e, ' +
        tableHeaders.length + ' colonne (' + tableHeaders.join(', ') + ').';
    };
    reader.readAsText(file);
  });
})();
"""


def _bom_html(bom_lines: list[tuple[str, int, list[str]]] | None) -> str:
    """`material_names` (third element, one entry per row) is the exact
    set of glTF material names this row should highlight -- a single-
    item list matching `name` for an ordinary leaf row, or a whole
    collapsed sub-assembly's own descendant materials (scene3d.py's
    generate_bom collapse_names) -- embedded as JSON in a data attribute
    so _SELECT_JS never has to reconstruct the naming convention itself."""
    if not bom_lines:
        return ""
    rows = "".join(
        f'<tr class="part" data-part-name="{html.escape(name)}" data-part-count="{count}" '
        f'data-material-names=\'{html.escape(json.dumps(material_names))}\'>'
        f'<td>{html.escape(name)}</td><td class="qty">x{count}</td></tr>'
        for name, count, material_names in bom_lines
    )
    return f'<div id="bom"><h3>Distinta base</h3><table>{rows}</table></div>'


def _doc_index_html(documents: list[dict] | None) -> str:
    """The navigable index of consultable documents extracted from the
    bundle -- one clickable entry per document (its content is embedded
    in window.__BALZAR_DOCS__ and rendered/downloaded by _DOC_JS from
    the entry's index). Empty string (panel absent) when the bundle
    carries no documents, e.g. a plain 3D assembly."""
    if not documents:
        return ""
    lis = "".join(
        f'<li data-doc-index="{i}"><span class="role">{html.escape(d["role"])}</span>'
        f'<span>{html.escape(d["label"])}</span></li>'
        for i, d in enumerate(documents)
    )
    return f'<div id="doc-index"><h3>Documenti nel bundle</h3><ul>{lis}</ul></div>'


# Client-side document index + inline viewer. Kept a separate literal
# (not shared with app.js) for the same reason as _SELECT_JS: one is
# embedded in a Python template here, the other loads as a static
# <script> in the web demo. Inline rendering covers the browser-native
# simple formats (text and images); anything structured (html/xml/json/
# pdf/dxf/binary) is offered for download instead of a fake preview --
# the same honesty rule as svg.py refusing ops it can't represent.
_DOC_JS = """
(function(){
  var docs = window.__BALZAR_DOCS__ || [];
  var indexEl = document.getElementById('doc-index');
  if (!indexEl) return;
  var overlay = document.getElementById('doc-overlay');
  var titleEl = document.getElementById('doc-overlay-title');
  var bodyEl = document.getElementById('doc-overlay-body');
  var TEXT_EXT = ['txt', 'md', 'log'];
  var IMG_MIME = {png:'image/png', gif:'image/gif', svg:'image/svg+xml',
                  jpg:'image/jpeg', jpeg:'image/jpeg', webp:'image/webp', bmp:'image/bmp'};

  function ext(label){ var m = /\\.([^.]+)$/.exec(label.toLowerCase()); return m ? m[1] : ''; }
  function bytes(doc){
    var bin = atob(doc.b64);
    var a = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    return a;
  }
  function asText(doc){ return new TextDecoder('utf-8').decode(bytes(doc)); }

  function renderCsvTable(text){
    var table = document.createElement('table');
    text.split(/\\r?\\n/).forEach(function(line){
      if (!line.length) return;
      var tr = document.createElement('tr');
      line.split(',').forEach(function(cell){
        var td = document.createElement('td');
        td.textContent = cell;
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    return table;
  }

  function download(doc){
    var blob = new Blob([bytes(doc)], {type: 'application/octet-stream'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = doc.label; a.click();
    URL.revokeObjectURL(url);
  }

  function open(doc){
    var e = ext(doc.label);
    titleEl.textContent = doc.label;
    bodyEl.innerHTML = '';
    if (e === 'csv'){
      bodyEl.appendChild(renderCsvTable(asText(doc)));
    } else if (TEXT_EXT.indexOf(e) >= 0){
      var pre = document.createElement('pre');
      pre.textContent = asText(doc);
      bodyEl.appendChild(pre);
    } else if (IMG_MIME[e]){
      var img = document.createElement('img');
      img.src = 'data:' + IMG_MIME[e] + ';base64,' + doc.b64;
      bodyEl.appendChild(img);
    } else {
      // structured/binary: no honest inline preview -- download instead,
      // and say so rather than showing an empty overlay
      var note = document.createElement('p');
      note.textContent = 'Formato non visualizzabile inline: scaricato per la consultazione con l\\'app di sistema.';
      bodyEl.appendChild(note);
      download(doc);
      return; // no overlay for a pure download
    }
    overlay.classList.add('open');
  }

  indexEl.querySelectorAll('li[data-doc-index]').forEach(function(li){
    li.addEventListener('click', function(){ open(docs[+li.dataset.docIndex]); });
  });
  document.getElementById('doc-overlay-close').addEventListener('click', function(){
    overlay.classList.remove('open');
  });
})();
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - matches base class signature
        pass  # the desktop app has no console for this to usefully go to


class ComponentTable:
    """A generic component/maintenance info table: whatever columns a
    real CSV actually has (component name, alarm code, function, spare
    part, "cleans every 6 months"...), in whatever order, under whatever
    header names -- no assumption about column meaning baked into the
    data model itself. Which column (if any) drives the 3D highlight is
    decided later, client-side, by testing content against the model's
    own BOM part names (detectComponentColumn in the JS templates
    below); this class only carries the parsed rows."""

    def __init__(self, headers: list[str], rows: list[list[str]]):
        self.headers = headers
        self.rows = rows

    def all_values(self) -> set[str]:
        """Every non-empty cell across the whole table -- used as the
        candidate set for scene3d.generate_bom's collapse_names. There's
        no way to know which column is "the component" before the BOM
        exists (collapse_names is an INPUT to building it), so instead
        of guessing a column, every cell value is offered as a
        candidate; a candidate that doesn't match any real group name is
        simply ignored downstream (generate_bom's documented behavior),
        so this is safe, not just convenient."""
        return {cell for row in self.rows for cell in row if cell.strip()}

    def to_json_dict(self) -> dict:
        return {"headers": self.headers, "rows": self.rows}


def parse_component_table_text(text: str) -> ComponentTable:
    """Generic CSV -> ComponentTable(headers, rows). Uses the stdlib
    `csv` module (unlike the client-side JS parser, which is a plain
    split() -- a real CSV reader is cheap in Python and handles quoted
    commas correctly, so no need to declare the same limitation twice).
    Takes text directly (not a path) so it works equally on a CSV loaded
    from disk or one unpacked from a bundle (balzar/bundle.py) in
    memory.

    The first row is ALWAYS the header (declared column names) --
    unlike the old fixed two-column format (codice_allarme,
    nome_componente), which could heuristically guess whether row 0 was
    a header or already data, arbitrary columns give no such anchor: a
    column named "info" or "ricambio" is meaningless without a header
    saying so. A file with no rows at all (blank) returns an empty
    table rather than raising -- an ordinary "nothing uploaded yet"
    state, not an error condition."""
    import io as _io

    reader = csv.reader(_io.StringIO(text))
    try:
        first = next(reader)
    except StopIteration:
        return ComponentTable(headers=[], rows=[])
    headers = [h.strip() for h in first]
    if not any(headers):
        raise ValueError("la tabella componenti richiede una riga di intestazione "
                         "(i nomi delle colonne) come prima riga del CSV")
    rows: list[list[str]] = []
    for cells in reader:
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            cells = cells[:len(headers)]
        rows.append(cells)
    return ComponentTable(headers=headers, rows=rows)


def parse_component_table(path: str) -> ComponentTable:
    """parse_component_table_text, reading from a file path."""
    with open(path, encoding="utf-8") as fh:
        return parse_component_table_text(fh.read())


def _render_viewer_page(glb: bytes | None, bom_lines, info_table, documents,
                        work_dir: str) -> http.server.HTTPServer:
    """Write model.glb (if any) + viewer.html + a copy of
    model-viewer.min.js, then serve `work_dir` on an ephemeral localhost
    port and open the default browser. The single page builder behind
    both the pure-3D viewer and the bundle viewer: the 3D cluster
    (model-viewer + controls + BOM + search) is present only when there
    is a GLB, and the document index is present only when there are
    documents, so a document-only bundle renders an index-only page and
    a plain assembly renders exactly the old 3D page. Returns the
    running server (`.server_address[1]` is the port), so a caller that
    wants to avoid spawning a second server for content it already has
    open (gui.py's library panel) can reopen a browser tab at the same
    port instead, and can `.shutdown()` it explicitly when done."""
    if glb is not None:
        with open(os.path.join(work_dir, "model.glb"), "wb") as fh:
            fh.write(glb)
    # </script> inside a cell value or label would otherwise close the
    # tag early -- escape the slash so embedded JSON stays inert text to
    # the HTML parser (same mitigation for the info table and every
    # doc's label).
    info_table_json = json.dumps(
        info_table.to_json_dict() if info_table else {"headers": [], "rows": []}
    ).replace("</", "<\\/")
    docs_json = json.dumps(documents or []).replace("</", "<\\/")
    html_out = _PAGE_TEMPLATE.format(
        body_class="" if glb is not None else "no-3d",
        threed_section=_THREED_SECTION if glb is not None else "",
        bom_html=_bom_html(bom_lines) if glb is not None else "",
        doc_index_html=_doc_index_html(documents),
        info_table_json=info_table_json,
        docs_json=docs_json,
        select_js=_SELECT_JS if glb is not None else "",
        doc_js=_DOC_JS)
    with open(os.path.join(work_dir, "viewer.html"), "w", encoding="utf-8") as fh:
        fh.write(html_out)
    if os.path.exists(_MODEL_VIEWER_JS):
        shutil.copy(_MODEL_VIEWER_JS, os.path.join(work_dir, "model-viewer.min.js"))

    handler_cls = lambda *a, **kw: _QuietHandler(*a, directory=work_dir, **kw)  # noqa: E731
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open(f"http://127.0.0.1:{port}/viewer.html")
    return server


def open_glb_in_browser(glb: bytes, bom_lines: list[tuple[str, int, list[str]]] | None,
                        work_dir: str,
                        info_table: ComponentTable | None = None,
                        documents: list[dict] | None = None) -> "http.server.HTTPServer":
    """Write model.glb + viewer.html + a copy of model-viewer.min.js into
    `work_dir`, serve it on an ephemeral localhost port, open the default
    browser. `work_dir` is the caller's responsibility (a TemporaryDirectory
    the caller keeps alive for as long as the viewer might be open — this
    function does not clean up after itself, since the HTTP server keeps
    serving from it for the life of the background thread).

    `info_table` (optional, from parse_component_table or built by hand)
    gets baked into the page as a JS literal so the search works
    immediately on load, with no manual CSV upload step -- the piece that
    makes automation possible: a page generated once can be re-opened
    with `?q=<value>` (from a script, an HMI button, a QR code) and the
    matching row(s)/component highlight with zero typing.

    `documents` (optional, each {role, label, b64}) adds a navigable
    index of consultable documents alongside the model -- see
    open_bundle_in_browser, which builds it from a bundle's doc items.

    Returns the running server (see _render_viewer_page)."""
    return _render_viewer_page(glb, bom_lines, info_table, documents, work_dir)


def _render_2d_item(item) -> list[dict]:
    """Render a KIND_2D bundle item (a full BZR1 program) into doc-index
    entries -- generated fresh here, at view time, from the program, not
    stored as pixels: the same "describe, don't store" principle as the
    rest of balzar, applied to one document inside a bundle instead of
    the main payload. Always produces a PNG (or GIF if the program has
    more than one frame); also an SVG entry when the program is in the
    vector-safe subset (balzar/svg.py) -- both get a real image
    extension as their label (.png/.gif/.svg), so the SAME image-preview
    code path in _DOC_JS picks them up automatically, no new client-side
    branch needed for this to work."""
    import base64 as _b64
    import io as _io

    from PIL import Image

    from .interpreter import render as render_program
    from .payload import decode_payload as decode_2d
    from .png import png_bytes

    program_text = decode_2d(item.data)
    result = render_program(program_text)
    stem = os.path.splitext(item.label)[0]
    docs = []

    if len(result.frames) == 1:
        data = png_bytes(result.width, result.height, result.frame_rgb(0))
        docs.append({"role": "tavola 2D", "label": stem + ".png",
                    "b64": _b64.b64encode(data).decode("ascii")})
    else:
        images = [Image.frombytes("RGB", (result.width, result.height), result.frame_rgb(i))
                 for i in range(len(result.frames))]
        buf = _io.BytesIO()
        images[0].save(buf, format="GIF", save_all=True, append_images=images[1:],
                       duration=200, loop=0)
        docs.append({"role": "tavola 2D (animata)", "label": stem + ".gif",
                    "b64": _b64.b64encode(buf.getvalue()).decode("ascii")})

    try:
        from .svg import UnsupportedForSVG, render_svg
        svg_text = render_svg(program_text)
        docs.append({"role": "tavola 2D (vettoriale)", "label": stem + ".svg",
                    "b64": _b64.b64encode(svg_text.encode("utf-8")).decode("ascii")})
    except UnsupportedForSVG:
        pass  # not in the vector-safe subset -- the PNG/GIF above still stands

    return docs


def _documents_from_items(items) -> list[dict]:
    """Build the viewer's document list from a bundle's 2d/alarm/doc
    items (base64 each, role tag for the index badge) -- the 3D item is
    NOT a document here, it's the main view, so it's excluded. An alarm
    table is included: it powers the search AND is itself a consultable
    table."""
    import base64 as _b64

    from .bundle import KIND_2D, KIND_ALARM, KIND_DOC, is_alarm_kind
    docs = []
    for it in items:
        if it.kind == KIND_2D:
            docs.extend(_render_2d_item(it))
            continue
        if is_alarm_kind(it.kind):
            role = "allarmi"
        elif it.kind == KIND_DOC:
            role = "doc"
        else:
            continue  # 3D item is the main view, not an index entry
        docs.append({"role": role, "label": it.label,
                     "b64": _b64.b64encode(it.data).decode("ascii")})
    return docs


def open_bundle_in_browser(bundle_data: bytes, work_dir: str) -> "http.server.HTTPServer":
    """Open a multi-document bundle (balzar/bundle.py): the "3d" item
    (if any) becomes the model.glb + BOM this module already shows, any
    alarm item wires the search bar with no manual upload, and every
    alarm/doc item also appears in a navigable document index that can
    be consulted inline (text/CSV/image) or downloaded (structured
    formats). A bundle with NO 3D item is valid -- it renders an
    index-only page of its documents.

    Deliberately local imports (scene3d.py/gltf.py, not used by the rest
    of this module) so the plain GLB+BOM path stays as decoupled from the
    3D encoding stack as before -- this function is the only place in
    viewer3d.py that needs to know a bundle exists.

    Returns the running server (see _render_viewer_page)."""
    from .bundle import BundleError, decode_bundle, is_alarm_kind
    from .bundle import KIND_3D
    from .gltf import scene3d_to_glb
    from .scene3d import Scene3DError, decode_payload as decode_scene, generate_bom

    try:
        items = decode_bundle(bundle_data)
    except BundleError as exc:
        raise ValueError(f"bundle non valido: {exc}") from None

    documents = _documents_from_items(items)

    # a bundle with more than one alarm-marked CSV is valid (the format
    # doesn't forbid it) but they could have entirely different columns
    # -- concatenating rows across mismatched schemas the way the old
    # fixed two-column format safely could doesn't generalize, so only
    # the first one becomes the info table, same "first one wins, not
    # silently merged" principle already used below for multiple 3D items
    info_table = None
    for it in items:
        if is_alarm_kind(it.kind):
            info_table = parse_component_table_text(it.data.decode("utf-8"))
            break
    # any cell value across the whole table is offered as a candidate to
    # collapse its own BOM/GLB entry into a single row/highlight group
    # instead of expanding to every leaf part underneath -- see
    # scene3d.generate_bom's collapse_names; a candidate that doesn't
    # match a real group name is simply ignored, so this doesn't need to
    # know which column is "the component" (that's decided later,
    # client-side, once the BOM this very call produces actually exists)
    collapse_names = info_table.all_values() if info_table else None

    three_d_items = [it for it in items if it.kind == KIND_3D]
    if not three_d_items:
        # a document-only bundle: no model, just the navigable index
        if not documents:
            raise ValueError("il bundle e' vuoto: niente da mostrare")
        return _render_viewer_page(None, None, None, documents, work_dir)

    # a bundle with more than one 3D item is valid (the format doesn't
    # forbid it) but this viewer shows exactly one model -- the first one,
    # not silently merged or dropped without saying so
    try:
        scene = decode_scene(three_d_items[0].data)
    except Scene3DError as exc:
        raise ValueError(f"assieme 3D nel bundle non valido: {exc}") from None

    glb = scene3d_to_glb(scene, collapse_names=collapse_names)
    bom_lines = [(e.name, e.count, e.material_names)
                for e in generate_bom(scene, collapse_names)]
    return open_glb_in_browser(glb, bom_lines, work_dir, info_table=info_table,
                               documents=documents or None)
