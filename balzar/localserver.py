"""Server locale in-process che serve la UI web (index.html + app.js +
style.css + i file vendorizzati) e instrada `/api/*` ai `handle_*` di
`webapi.py`, con `LOCAL_LIMITS` (nessun limite di piattaforma Vercel).

E' il cuore del guscio WebView del prodotto desktop (ROADMAP.md Fase 1b,
CLAUDE.md 12.5): la finestra pywebview (Passo 3) punta all'URL restituito da
`start_local_server()`. Gira **solo** su `127.0.0.1` -- niente esce dal
dispositivo, nessun browser visibile, nessuna rete: un normale programma
locale che dentro usa web-tech (come VS Code/Slack), non "un sito".

Stessa logica gia' provata dal dev server dell'harness UX; qui e' la versione
del pacchetto, frozen-aware (i file statici vengono da `assets.asset_root()`,
la radice del repo in sviluppo o `sys._MEIPASS` in un bundle PyInstaller -- il
`.spec` li aggiunge a `datas`, Passo 2).

I `handle_*` di `webapi.py` NON vengono toccati: il server locale e gli
endpoint Vercel (`api/*.py`) li riusano identici, cambia solo il profilo di
limiti (LOCAL vs VERCEL) e il guscio HTTP.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .assets import asset_root
from .webapi import (LOCAL_LIMITS, handle_encode, handle_encode_3d,
                     handle_encode_sequence, handle_encode_vector,
                     handle_encode_video, handle_qr, handle_render)

# /api/* -> handler(body: dict, limits) -> (status, dict). L'unico dispatch,
# lo stesso set di endpoint della demo web (la modalita' "independent" della
# sequenza e' gestita dentro handle_encode_sequence in base a body["mode"]).
ROUTES = {
    "/api/encode": handle_encode,
    "/api/encode_vector": handle_encode_vector,
    "/api/encode_video": handle_encode_video,
    "/api/encode_sequence": handle_encode_sequence,
    "/api/encode_3d": handle_encode_3d,
    "/api/qr": handle_qr,
    "/api/render": handle_render,
}

_MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css",
         ".png": "image/png", ".gif": "image/gif", ".svg": "image/svg+xml",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
         ".ico": "image/x-icon", ".json": "application/json",
         ".woff": "font/woff", ".woff2": "font/woff2"}


def _make_handler(static_root: str, limits):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silenzioso: e' un'app, non un server
            pass

        def _send_json(self, status: int, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                path = "/index.html"
            rel = path.lstrip("/")
            abs_path = os.path.normpath(os.path.join(static_root, rel))
            # rifiuta traversal fuori dalla radice degli asset
            root = os.path.normpath(static_root)
            if not (abs_path == root or abs_path.startswith(root + os.sep)) \
                    or not os.path.isfile(abs_path):
                self.send_response(404)
                self.end_headers()
                return
            with open(abs_path, "rb") as fh:
                data = fh.read()
            ext = os.path.splitext(abs_path)[1].lower()
            self.send_response(200)
            self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            handler = ROUTES.get(self.path.split("?", 1)[0])
            if handler is None:
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0 or length > limits.max_upload_bytes:
                    self._send_json(400, {"ok": False,
                                          "error": "richiesta vuota o troppo grande"})
                    return
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                status, obj = handler(body, limits)
                self._send_json(status, obj)
            except Exception as exc:
                self._send_json(500, {"ok": False,
                                      "error": f"{type(exc).__name__}: {exc}"})

    return _Handler


def start_local_server(port: int = 0, limits=LOCAL_LIMITS,
                       static_root: str | None = None):
    """Avvia il server su 127.0.0.1 in un thread daemon e ritorna
    `(server, url)`. `port=0` sceglie una porta effimera libera. Il chiamante
    (l'entry point pywebview) punta la finestra a `url` e, alla chiusura,
    chiama `server.shutdown()` + `server.server_close()`.

    Bind esplicito a 127.0.0.1 (mai 0.0.0.0): il server non deve essere
    raggiungibile dalla rete, solo dal processo/utente locale."""
    root = static_root if static_root is not None else asset_root()
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(root, limits))
    real_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{real_port}"


def main(argv=None) -> int:
    """Esecuzione standalone, per test/sviluppo:
        python3 -m balzar.localserver [porta]
    Serve in foreground (Ctrl-C per fermare)."""
    import sys
    argv = sys.argv[1:] if argv is None else argv
    port = int(argv[0]) if argv else 8799
    server = ThreadingHTTPServer(("127.0.0.1", port),
                                 _make_handler(asset_root(), LOCAL_LIMITS))
    print(f"balzar localserver su http://127.0.0.1:{server.server_address[1]} "
          f"(radice statica: {asset_root()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
