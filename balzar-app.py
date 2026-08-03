"""Entry point del prodotto desktop, usato direttamente e da PyInstaller.

    python3 balzar-app.py            # guscio WebView (pywebview)
    python3 balzar-app.py --classic  # GUI classica Tkinter (fallback)

Di default avvia il guscio nativo pywebview che mostra la UI web servita in
locale (balzar/webview_app.py); se pywebview non e' disponibile ricade
automaticamente sulla GUI Tkinter. Vedi ROADMAP.md Fase 1b / CLAUDE.md 12.5.

Build di un eseguibile standalone (Windows .exe / macOS .app / Linux):

    pip install -r requirements.txt pyinstaller
    pyinstaller balzar.spec

Il risultato in dist/ gira completamente offline, senza installazione di
Python.
"""

from balzar.webview_app import main

if __name__ == "__main__":
    raise SystemExit(main())
