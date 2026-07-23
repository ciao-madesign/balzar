"""Guscio nativo del prodotto desktop: una finestra pywebview che mostra la
UI web (servita in locale da balzar/localserver.py), con il gate di licenza
beta interamente web-based (nessun Tkinter in questo percorso).

Flusso all'avvio (ROADMAP.md Fase 1b Passo 3, CLAUDE.md 12.5):
  - `license.startup_decision(frozen)` decide:
      OPEN / ACTIVATED  -> apri direttamente /index.html
      NEED_KEY          -> apri /activate.html (form -> POST /api/activate)
      UNCONFIGURED      -> finestra d'errore (build senza chiave, fail-closed)
  - il server locale gira su 127.0.0.1 (offline); la finestra usa il webview
    nativo del SO (WKWebView/WebView2/WebKitGTK) -- un normale programma
    locale, nessun browser visibile, nessuna rete.

Fallback: se `pywebview` non e' disponibile (o `--classic`), si ricade sulla
GUI Tkinter esistente (balzar.gui.main). Cosi' l'app parte comunque.

NOTA di verifica: server + /api/activate sono testabili in CI (via urllib,
tests/test_localserver.py); la finestra pywebview vera si valida sul Mac
(nessun backend webview in ambiente Linux headless).
"""

from __future__ import annotations

import sys

from . import license as license_gate
from . import localserver

_WINDOW_TITLE = "Balzar"
_UNCONFIGURED_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{height:100%;margin:0;background:#1c1c1c;color:#eee;
font-family:-apple-system,Segoe UI,sans-serif;display:flex;align-items:center;
justify-content:center;text-align:center}div{max-width:420px;padding:2rem}
h1{font-size:1.1rem}p{color:#999;font-size:.9rem}</style></head><body><div>
<h1>Build non configurata</h1><p>Questa build non ha una chiave di licenza
impostata e non puo' essere avviata. (Errore di produzione della build: la
chiave beta non e' stata impostata prima del packaging.)</p></div></body></html>"""


def _activate(body: dict):
    """Route locale POST /api/activate: verifica e persiste l'attivazione.
    Ritorna (status, obj) come i handler, ma non prende `limits`."""
    key = body.get("key", "")
    return 200, {"ok": True, "activated": bool(license_gate.activate(key))}


def _initial_path(decision: str) -> str:
    """Quale pagina aprire per prima, data la decisione del gate."""
    if decision == license_gate.STARTUP_NEED_KEY:
        return "/activate.html"
    return "/index.html"  # OPEN / ACTIVATED


def run() -> int:
    """Avvia il guscio pywebview. Solleva ImportError se pywebview manca
    (il chiamante `main` ricade su Tkinter)."""
    import webview  # deferred: assente in CI Linux headless, presente nel pacchetto

    decision = license_gate.startup_decision(getattr(sys, "frozen", False))
    if decision == license_gate.STARTUP_UNCONFIGURED:
        webview.create_window(_WINDOW_TITLE, html=_UNCONFIGURED_HTML,
                              width=520, height=360)
        webview.start()
        return 0

    server, url = localserver.start_local_server(
        extra_routes={"/api/activate": _activate})
    try:
        webview.create_window(_WINDOW_TITLE, url=url + _initial_path(decision),
                              width=1200, height=800, min_size=(900, 600))
        webview.start()  # blocca finche' la finestra non viene chiusa
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main(argv=None) -> int:
    """Entry point del prodotto desktop. Default: guscio pywebview; ricade
    sulla GUI Tkinter se pywebview manca o con `--classic`."""
    argv = sys.argv[1:] if argv is None else argv
    if "--classic" not in argv:
        try:
            return run()
        except ImportError:
            sys.stderr.write(
                "pywebview non disponibile: avvio la GUI classica (Tkinter).\n")
    from .gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
