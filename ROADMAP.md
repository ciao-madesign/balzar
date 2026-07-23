# Roadmap verso la beta (e verso il prodotto)

Documento di pianificazione del percorso da "codice funzionante" a prodotto
installabile. Le **funzionalità** sono già complete (Balzar Studio: encoder di
tutti i formati; Balzar Live: apri/scansiona/viewer 3D/libreria — vedi
`CLAUDE.md`); ciò che manca è **packaging, distribuzione, UI e igiene legale**,
non capacità del motore.

## Obiettivo finale (deciso con l'utente)

Il prodotto finale è **un'app desktop (e in futuro mobile) installabile e
usabile come qualsiasi programma — tipo Microsoft Word**: la scarichi, la
installi con un gesto banale che chiunque sa fare, la apri con un doppio clic,
e lavora come un **normale programma locale** (finestra nativa, offline, nessun
terminale, nessun browser visibile, nessuna rete).

Concretamente questo significa tre cose, tutte già nella roadmap:

| | Beta (in corso) | Prodotto finale "come Word" |
|---|---|---|
| Finestra nativa, offline, nessun terminale/browser | ✅ (guscio WebView, sotto) | ✅ |
| Installer banale (trascina in Applicazioni / `setup.exe`) | da aggiungere | ✅ |
| Firma del codice → zero avvisi di sicurezza | rimandata | ✅ |

**Divisione dei ruoli** (importante): tutta la complessità — build, firma,
creazione dell'installer — è lato **sviluppatore**, una volta sola. L'**utente
finale** riceve solo il `.dmg`/`.exe` e fa il gesto che tutti conoscono
(trascina/doppio clic). Nessun utente compilerà mai nulla.

## Decisione di architettura UI: una sola interfaccia, guscio nativo

Deciso con l'utente (dopo aver constatato che la GUI Tkinter desktop è densa,
non progettata, e diversa dalla demo web appena ridisegnata): **unificare tutte
le superfici sulla stessa UI web**, dentro un **guscio nativo**.

- Il desktop diventa una **finestra pywebview** (usa il webview nativo del SO:
  WKWebView su macOS, WebView2 su Windows, WebKitGTK su Linux) che mostra la
  stessa `index.html` + `app.js` + `style.css` della demo, servita da un
  **server locale in-process** che instrada `/api/*` ai `handle_*` già
  esistenti in `webapi.py`, con `LOCAL_LIMITS` (niente limiti Vercel).
- **Pienamente offline e "programma locale"**: il server gira su `127.0.0.1`
  dentro il processo dell'app; nessun browser visibile, nessuna rete. È lo
  stesso schema di app installabili come VS Code / Slack / Spotify (web-tech in
  un guscio nativo), non "un sito".
- **Una sola UI** per web, desktop e Android (Fase 2 usa già lo stesso schema):
  il round **stile** si fa una volta sola sulla web UI e migliora tutte e tre
  le superfici insieme.
- **La GUI Tkinter resta come fallback** (raggiungibile es. `--classic`, o
  automaticamente se `pywebview` non è disponibile), non viene cancellata.

---

## Licenza — regime attuale

- **Tutti i diritti riservati** a Michele Aldeni (`LICENSE`, licenza
  proprietaria closed-beta).
- Ogni componente di terze parti citato con trasparenza in
  `THIRD-PARTY-NOTICES.md` (obblighi Apache-2.0 e LGPL-2.1 documentati e
  rispettati).
- Dopo la beta: decisione su commercializzazione o apertura della licenza —
  rimandata, non ancora presa.

### Gate di licenza beta (soft gate, non DRM)

All'avvio l'app chiede una **chiave di attivazione**. Per la beta la chiave è
**unica e condivisa**, decisa da Michele. Cancello beta, non protezione
anti-copia robusta: `balzar/license.py` confronta l'hash SHA-256 della chiave
(mai la chiave in chiaro), persiste l'attivazione in
`~/.balzar/activation.json`; fail-closed se non configurata. L'hash si imposta
in build con `python3 -m balzar.license hash-key`. Il meccanismo vero
(per-utente) verrà dopo la beta. Nel guscio WebView il gate va agganciato prima
di aprire la finestra (riuso della logica `startup_decision`).

---

## Fase 0 — Igiene legale ✅ (fatta)

- [x] `LICENSE` proprietario (© 2026 Michele Aldeni, tutti i diritti riservati).
- [x] `THIRD-PARTY-NOTICES.md` con ogni dipendenza, licenza e obblighi.
- [x] `balzar/license.py` — gate di licenza beta offline + test.

## Fase 1a — Packaging desktop Tkinter ✅ (fatto, resta come fallback)

Il primo giro di packaging, ora **fallback** (non più la UI primaria):

- [x] Gate `license.py` agganciato all'avvio della GUI Tkinter (`gui.py`).
- [x] `balzar.spec` (datas dei JS vendorizzati + icona per-OS + `Balzar.app`),
      `assets.py` frozen-aware, `requirements.txt` con `pyzbar`/`libzbar0`.
- [x] Ingestione SVG/DXF aggiunta al desktop (gap trovato alla prima build reale
      su Mac — `CLAUDE.md` §12.4).
- [x] Build macOS reale verificata sul MacBook Air (`.app` funzionante, gate +
      Studio/Live + SVG/DXF).

## Fase 1b — Guscio WebView desktop (UI primaria) — IN CORSO

Passi stabiliti (ordine di esecuzione):

- [x] **Passo 1 ✅** — server locale di produzione (`balzar/localserver.py`):
      serve i file statici del frontend + instrada `/api/*` ai `handle_*` di
      `webapi.py`, con `LOCAL_LIMITS`, frozen-aware. Verificato con l'harness
      Playwright sul server di produzione + 6 test `urllib`.
- [x] **Passo 2 ✅** — bundling del frontend nel `.spec` (glob: 16 file —
      html/css/js + `landing-img/`), frozen-aware.
- [x] **Passo 3 ✅ (costruito, finestra da validare sul Mac)** — guscio
      pywebview: `balzar/webview_app.py` (gate → server locale → finestra),
      gate web-based (`activate.html` + route `/api/activate` iniettato in
      `localserver`), `balzar-app.py` → WebView con fallback Tkinter,
      `pywebview` in `requirements.txt`. Verificato qui: 6 test + flusso
      Playwright sull'`activate.html`. **La finestra nativa la valida Michele
      sul Mac** (nessun backend webview in Linux headless).
- [ ] **Passo 4** — dettagli desktop nella WebView: download file
      (payload/PNG/GLB) via API di salvataggio nativa di pywebview; **Libreria
      rimandata** nella versione WebView (resta nel fallback Tkinter per la
      beta — è una feature solo-desktop non presente nella web UI).

## Fase 1c — Esperienza "come Word" (installer + firma)

- [ ] Installer: `.dmg` con trascinamento in Applicazioni (macOS) / `setup.exe`
      Inno Setup o NSIS (Windows). *Sul Mac/Windows dell'utente.*
- [ ] **Rimandato oltre la beta**: notarizzazione Apple ($99/anno), certificato
      firma Windows, auto-update, `.app` universale Intel+arm. Fino ad allora i
      tester usano il bypass Gatekeeper/SmartScreen una volta sola (`BUILD.md`).

## Fase 2 — Beta Android (stesso guscio WebView)

Stesso schema server locale + WebView, dentro un APK — quindi **condivide il
`localserver.py` e la UI web del Passo 1**, nessuna terza interfaccia.

- [ ] Packaging (Chaquopy vs BeeWare/Briefcase) + impalcatura server+WebView.
- [ ] Gate `license.py` all'avvio.
- [ ] Build APK (SDK Android non presente in questo ambiente Linux) + firma con
      chiave auto-generata (requisito Android, non un cancello di store).
- [ ] Sideload su device + checklist funzionale.

**App nativa mobile futura**: desiderabile per footprint/UX native, **non** per
l'offline (già garantito dal server locale). Documentata per non perderla, con
la motivazione corretta.

---

## Decisioni di prodotto ancora aperte (non-codice)

- Distribuzione commerciale / apertura licenza — dopo la beta.
- Meccanismo di licenza per-utente definitivo — dopo la beta.
- Balzar Bridge (integrazione PLC/HMI per Balzar Live automatico): solo
  scoping, nessuna decisione su protocollo/vendor (`CLAUDE.md` §9.19). Non
  serve per un v1 manuale.
