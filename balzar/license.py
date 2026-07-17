"""Gate di licenza per la beta chiusa -- soft gate offline, NON un DRM
robusto.

Requisito di prodotto (deciso in sessione): all'avvio, l'app chiede una
chiave di attivazione. Per la beta la chiave e' UNICA e condivisa, decisa
dal titolare del copyright (Michele Aldeni). Questo modulo e' il meccanismo;
il wiring nelle interfacce (GUI desktop, WebView Android) e' per-frontend.

Cosa fa, e cosa NON fa (onesto per costruzione):

  - Confronta l'HASH SHA-256 della chiave inserita con un hash incorporato
    (`BETA_KEY_SHA256`), mai la chiave in chiaro. Il confronto usa
    `hmac.compare_digest` (tempo costante), anche se qui non e' una vera
    difesa crittografica -- e' igiene, non sicurezza.
  - Una volta validata, persiste l'attivazione in `~/.balzar/activation.json`
    cosi' l'utente non reinserisce la chiave a ogni avvio.
  - NON e' una protezione anti-copia: il codice Python e' ispezionabile, e
    l'hash della chiave e' comunque incorporato nel binario distribuito (deve
    esserlo -- la chiave e' la stessa per tutti in beta). Scoraggia la
    condivisione casuale della chiave, non un attaccante determinato. Il
    meccanismo vero (chiavi per-utente, firma asimmetrica) verra' DOPO la beta.

Impostare la chiave beta senza farla transitare in chiaro nei sorgenti/git:

    python3 -m balzar.license hash-key      # chiede la chiave (nascosta),
                                            # stampa il suo SHA-256 da
                                            # incollare in BETA_KEY_SHA256

Finche' `BETA_KEY_SHA256` resta vuoto, il gate e' "non configurato" e
`verify_key` rifiuta qualunque chiave (fail-closed): una build senza chiave
impostata non e' utilizzabile, per scelta esplicita, non per svista.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time

# Hash SHA-256 (hexdigest) della chiave beta condivisa. Vuoto = non
# configurato -> il gate rifiuta tutto (fail-closed). Impostare in fase di
# build con `python3 -m balzar.license hash-key` (vedi docstring del modulo).
BETA_KEY_SHA256 = ""


def _hash_key(key: str) -> str:
    """Hash canonico di una chiave: trim degli spazi ai bordi (un incolla
    con newline finale non deve invalidare la chiave), UTF-8, SHA-256."""
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def is_configured() -> bool:
    """True se una chiave beta e' stata incorporata in questa build."""
    return bool(BETA_KEY_SHA256)


def verify_key(key: str) -> bool:
    """True solo se il gate e' configurato E l'hash della chiave inserita
    coincide con quello incorporato. Confronto a tempo costante."""
    if not BETA_KEY_SHA256:
        return False
    return hmac.compare_digest(_hash_key(key), BETA_KEY_SHA256)


def _activation_dir() -> str:
    """Sovrascrivibile via BALZAR_LICENSE_DIR (test, o un utente che vuole lo
    stato altrove) -- default a una cartella nascosta nella home, creata al
    primo uso. Stessa convenzione di `library.py`."""
    path = os.environ.get("BALZAR_LICENSE_DIR") or os.path.join(
        os.path.expanduser("~"), ".balzar")
    os.makedirs(path, exist_ok=True)
    return path


def _activation_path() -> str:
    return os.path.join(_activation_dir(), "activation.json")


def is_activated() -> bool:
    """True se una chiave valida per QUESTA build e' gia' stata inserita in
    passato e persistita. L'attivazione salvata memorizza l'hash con cui e'
    stata fatta: se la build cambia chiave (BETA_KEY_SHA256 diverso), la
    vecchia attivazione non vale piu' e va reinserita -- evita che una
    chiave revocata resti buona solo perche' gia' attivata una volta."""
    if not BETA_KEY_SHA256:
        return False
    try:
        with open(_activation_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return bool(data.get("activated")) and data.get("key_hash") == BETA_KEY_SHA256


def activate(key: str) -> bool:
    """Verifica la chiave e, se valida, persiste l'attivazione. Ritorna
    l'esito. Scrittura atomica (tmp + os.replace) come in `library.py`, cosi'
    un crash a meta' scrittura non lascia un file di stato corrotto."""
    if not verify_key(key):
        return False
    data = {
        "activated": True,
        "key_hash": BETA_KEY_SHA256,
        "activated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    directory = _activation_dir()
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _activation_path())
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def deactivate() -> None:
    """Rimuove lo stato di attivazione (per test o per un reset esplicito)."""
    try:
        os.unlink(_activation_path())
    except OSError:
        pass


# Esiti della decisione all'avvio, per l'interfaccia che monta il gate (GUI
# desktop, WebView). Separati dalla UI cosi' la politica e' testabile senza
# Tk/browser.
STARTUP_OPEN = "open"                  # build di sviluppo: nessun gate
STARTUP_UNCONFIGURED = "unconfigured"  # impacchettata ma senza chiave: rifiuta
STARTUP_ACTIVATED = "activated"        # gia' attivata: parte
STARTUP_NEED_KEY = "need_key"          # chiedi la chiave


def startup_decision(frozen: bool) -> str:
    """Cosa deve fare l'interfaccia all'avvio, dato se gira come binario
    impacchettato (`frozen`, es. PyInstaller `sys.frozen`).

    Politica: il gate si applica a una build IMPACCHETTATA o a una build in
    cui una chiave e' stata incorporata; una build di sviluppo da sorgente e
    non configurata resta aperta (comodita' di sviluppo, non un buco -- una
    build impacchettata senza chiave viene comunque rifiutata, fail-closed,
    intercettando l'errore di 'ho dimenticato di impostare la chiave')."""
    if not (is_configured() or frozen):
        return STARTUP_OPEN
    if not is_configured():
        return STARTUP_UNCONFIGURED
    if is_activated():
        return STARTUP_ACTIVATED
    return STARTUP_NEED_KEY


def _main(argv=None) -> int:
    import argparse
    import getpass

    parser = argparse.ArgumentParser(
        prog="python3 -m balzar.license",
        description="Utility per il gate di licenza beta.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser(
        "hash-key",
        help="chiede una chiave (input nascosto) e stampa il suo SHA-256 da "
             "incollare in BETA_KEY_SHA256")
    status = sub.add_parser("status", help="mostra se il gate e' configurato/attivato")
    args = parser.parse_args(argv)

    if args.cmd == "hash-key":
        key = getpass.getpass("Chiave beta (non verra' mostrata): ")
        if not key.strip():
            print("errore: chiave vuota")
            return 1
        print(_hash_key(key))
        print("\nIncolla il valore sopra in BETA_KEY_SHA256 dentro balzar/license.py")
        return 0
    if args.cmd == "status":
        print(f"configurato: {is_configured()}")
        print(f"attivato:    {is_activated()}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
