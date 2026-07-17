"""Local desktop entry point for continuous camera QR acquisition.

Tkinter has no camera API of its own, so this follows the exact same
"delegate to a local page in the system browser" pattern already used by
viewer3d.py for GLB viewing: instead of writing camera capture in Python
(which would mean a new native dependency like OpenCV, never used
anywhere else in this project), this writes a tiny local web page that
reuses the SAME browser engine already vendored and proven for
trasporto-qr.html and Balzar Live's own continuous-acquisition tab
(jsQR.min.js / qr-transport-core.js / qr-camera-scanner.js) -- no new
JS, only wiring.

The one piece the web-tab version doesn't need: getting the result BACK
out of the browser and into the desktop process. The page POSTs its
reconstructed bytes (base64) to a /submit endpoint on the very same
ephemeral HTTPServer that's serving the page, whose handler puts them on
a queue.Queue -- gui.py polls this queue with the exact same
root.after(100, ...) pattern already used for _poll_queue, so the
Tkinter main thread is never blocked waiting on the browser."""

from __future__ import annotations

import base64
import http.server
import json
import os
import queue
import shutil
import threading
import webbrowser

from .assets import vendored_path

# Frozen-aware (PyInstaller): vedi balzar/assets.py. Questi tre file sono
# aggiunti a datas nel .spec, altrimenti la scansione fotocamera si romperebbe
# nel pacchetto.
_VENDORED_JS = ("jsQR.min.js", "qr-transport-core.js", "qr-camera-scanner.js")

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>balzar — scansione QR (fotocamera)</title>
<script src="jsQR.min.js"></script>
<script src="qr-transport-core.js"></script>
<script src="qr-camera-scanner.js"></script>
<style>
html,body{margin:0;height:100%;background:#1c1c1c;color:#eee;font-family:sans-serif;
          display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px}
h1{font-size:16px;font-weight:600;margin:0}
video{width:min(90vw,640px);border-radius:8px;background:#000}
#progress{font-size:13px;min-height:1.4em;text-align:center;max-width:90vw}
#progress.error{color:#e77}
#progress.done{color:#7d7}
button{padding:8px 16px;border-radius:6px;border:1px solid #555;background:#2a2a2a;
       color:#eee;font:inherit;cursor:pointer}
button:hover{border-color:#c77a2e}
p.hint{font-size:12px;color:#999;max-width:520px;text-align:center;margin:0}
</style>
</head>
<body>
<h1>Inquadra la sequenza QR (griglia 1x1, acquisizione continua)</h1>
<p class="hint">Inquadra lo schermo/pagina che mostra la sequenza QR e tienila ferma: la
  lettura si completa da sola, senza altri tocchi, e il file si apre automaticamente
  nell'app desktop. Puoi chiudere questa scheda una volta completato.</p>
<video id="cam-video" autoplay muted playsinline></video>
<p id="progress">Fotocamera non avviata.</p>
<button id="start-btn" type="button">Avvia fotocamera</button>
<button id="stop-btn" type="button" hidden>Ferma fotocamera</button>
<script>
(function() {
  const video = document.getElementById("cam-video");
  const progressEl = document.getElementById("progress");
  const startBtn = document.getElementById("start-btn");
  const stopBtn = document.getElementById("stop-btn");
  let scanner = null;
  let lastFrameSampleCount = null;

  async function submitBytes(bytes) {
    const b64 = bytesToB64(bytes);
    const res = await fetch("/submit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({data_base64: b64}),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "invio al desktop fallito");
  }

  startBtn.addEventListener("click", async () => {
    startBtn.hidden = true;
    progressEl.classList.remove("error", "done");
    progressEl.textContent = "Richiesta permesso fotocamera...";
    scanner = new ContinuousQrScanner({
      video: video,
      gridDim: 1,
      onProgress: (st) => {
        if (st.complete) return;
        const missingTxt = st.missing ? `mancano ${st.missing.length}` : "in attesa del primo QR";
        const hint = lastFrameSampleCount === 0
          ? " (nessun QR in questa inquadratura, avvicina/allontana la fotocamera)" : "";
        progressEl.textContent = `${st.have}/${st.total || "?"} capitoli letti — ${missingTxt}.${hint}`;
      },
      onFrameSample: (n) => { lastFrameSampleCount = n; },
      onError: (e) => {
        progressEl.classList.add("error");
        progressEl.textContent = "Errore fotocamera: " + (e && e.message ? e.message : String(e));
      },
      onComplete: async (bytes) => {
        stopBtn.hidden = true;
        progressEl.textContent = "Completo, invio all'app desktop in corso…";
        try {
          await submitBytes(bytes);
          progressEl.classList.add("done");
          progressEl.textContent = "Fatto: il file si sta aprendo nell'app desktop. Puoi chiudere questa scheda.";
        } catch (e) {
          progressEl.classList.add("error");
          progressEl.textContent = "Errore inviando i dati all'app desktop: " + e.message;
        }
      },
    });
    try {
      await scanner.start();
      stopBtn.hidden = false;
    } catch (e) {
      startBtn.hidden = false;
      progressEl.textContent = "Fotocamera non avviata: " + (e && e.message ? e.message : String(e));
    }
  });

  stopBtn.addEventListener("click", () => {
    if (scanner) scanner.stop();
    stopBtn.hidden = true;
    startBtn.hidden = false;
    progressEl.textContent = "Fotocamera fermata manualmente.";
  });
})();
</script>
</body>
</html>
"""


class _LiveScanHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the static page + vendored JS (GET, inherited from
    SimpleHTTPRequestHandler) and accepts exactly one POST endpoint,
    /submit, carrying the reconstructed bytes back from the browser."""

    result_queue: "queue.Queue[bytes]"  # set per-subclass by _make_handler

    def do_POST(self) -> None:
        if self.path != "/submit":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            data = base64.b64decode(payload["data_base64"])
        except Exception as exc:  # malformed request from a browser bug, not a server crash
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        self.result_queue.put(data)
        self._send_json(200, {"ok": True})

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # noqa: A002 - matches base class signature
        pass  # the desktop app has no console for this to usefully go to


def start_live_scan_server(work_dir: str) -> tuple[http.server.HTTPServer, "queue.Queue[bytes]"]:
    """Write index.html + the 3 vendored JS files into `work_dir`, serve
    it on an ephemeral localhost port, open the default browser to it,
    and return (server, result_queue). The caller (gui.py) polls
    result_queue.get_nowait() from its own Tkinter-safe timer loop
    (root.after) rather than blocking on it -- same non-blocking
    principle already used for every other background job in this app.

    `work_dir` is the caller's responsibility to keep alive (and clean
    up) for as long as the server might still be serving from it, same
    contract as viewer3d.open_glb_in_browser."""
    with open(os.path.join(work_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(_PAGE_TEMPLATE)
    for name in _VENDORED_JS:
        src = vendored_path(name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(work_dir, name))

    result_queue: "queue.Queue[bytes]" = queue.Queue()

    # the queue can't be passed through SimpleHTTPRequestHandler's
    # constructor (BaseHTTPRequestHandler.__init__ handles the request
    # immediately), so it's set as a class attribute on a dedicated
    # subclass instead -- same trick needed because Python's http.server
    # handler classes are instantiated per-request, not once
    handler_cls = type("_BoundLiveScanHandler", (_LiveScanHandler,), {"result_queue": result_queue})
    server = http.server.HTTPServer(("127.0.0.1", 0), lambda *a, **kw: handler_cls(*a, directory=work_dir, **kw))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webbrowser.open(f"http://127.0.0.1:{port}/index.html")
    return server, result_queue
