# Roadmap verso la beta

Documento di pianificazione del percorso da "codice funzionante" a "beta
installabile e testabile dai primi utenti". Le **funzionalità** sono già
complete (Balzar Studio: encoder di tutti i formati; Balzar Live: apri/
scansiona/viewer 3D/libreria — vedi `CLAUDE.md`); ciò che manca è
**packaging, distribuzione e igiene legale**, non capacità del motore.

Modello di distribuzione scelto: **programma installabile su licenza**, non
pubblicazione su uno store. Per Windows, macOS e Android questo è possibile
senza passare da alcun marketplace (iOS resta l'unica eccezione, fuori scope).

Ordine di rilascio deciso: **prima desktop (macOS/Windows), poi Android.**

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

Requisito deciso: all'avvio l'app chiede una **chiave di attivazione**. Per la
beta la chiave è **unica e condivisa**, decisa da Michele. È un cancello beta,
non una protezione anti-copia robusta:

- meccanismo: `balzar/license.py` — confronta l'**hash** SHA-256 della chiave
  inserita con un hash incorporato, mai la chiave in chiaro; l'attivazione è
  persistita localmente (`~/.balzar/activation.json`) così non va reinserita a
  ogni avvio;
- onestà: il codice è ispezionabile, quindi il gate scoraggia la condivisione
  casuale, non un attaccante determinato — il meccanismo vero (chiavi
  per-utente, firma) verrà dopo la beta;
- l'hash della chiave beta va impostato in fase di build da Michele
  (`python3 -m balzar.license hash-key`), senza far transitare la chiave in
  chiaro nei sorgenti o nella cronologia git.

---

## Fase 0 — Igiene legale ✅ (fatta)

- [x] `LICENSE` proprietario (© 2026 Michele Aldeni, tutti i diritti riservati).
- [x] `THIRD-PARTY-NOTICES.md` con ogni dipendenza, licenza e obblighi.
- [x] `balzar/license.py` — gate di licenza beta offline + test.

---

## Fase 1 — Beta desktop (macOS + Windows)

Test primario sul MacBook Air di Michele (Apple Silicon / arm64).

Passaggi minimi per una beta installabile e funzionante:

- [ ] `balzar.spec`: icona applicazione, nome, versione, metadati.
- [ ] `requirements.txt` completo + nota sulle dipendenze **native**
      (`libzbar0`) necessarie sulla macchina di build.
- [ ] Wiring del gate `license.py` nell'avvio della GUI desktop (`gui.py`).
- [ ] **Build macOS** su MacBook Air (`pyinstaller balzar.spec`) → `.app` /
      `.dmg` arm64 per il test personale.
- [ ] **Build Windows** su una macchina Windows reale → eseguibile / installer
      minimale (Inno Setup o NSIS).
- [ ] Istruzioni per i tester per aggirare Gatekeeper (macOS: clic-destro →
      Apri) e SmartScreen (Windows: "Esegui comunque") **senza** firma a
      pagamento.

**Rimandato oltre la beta** (deliberatamente escluso dal "minimo"):
firma EV Windows, notarizzazione Apple ($99/anno), auto-update, installer
rifinito, `.app` universale Intel+arm.

**Da qui (questo ambiente Linux) è producibile**: `balzar.spec` rifinito,
`requirements.txt`, il gate `license.py`, gli script di build e le istruzioni.
**Non producibile da qui**: i binari macOS/Windows reali (servono quelle
macchine — Michele li compila).

---

## Fase 2 — Beta Android

Approccio scelto per la beta: **server Python locale + WebView dentro un APK**.

- riusa l'intera UI web esistente (`index.html` + `webapi.py`) e la scansione
  QR già lato browser (`jsQR`/`ContinuousQrScanner`) — nessuna riscrittura UI;
- **è pienamente offline**: il server gira su `127.0.0.1` sul telefono stesso,
  niente esce dal dispositivo; `model-viewer`/`jsQR` sono già vendorizzati in
  locale, non da CDN. La WebView è solo la tecnologia di rendering della UI,
  non una dipendenza da internet;
- packaging candidato: Chaquopy (Python dentro un progetto Android) oppure
  BeeWare/Briefcase.

Passaggi:

- [ ] Fissare il packaging (Chaquopy vs BeeWare) e l'impalcatura server+WebView.
- [ ] Gate `license.py` all'avvio anche nella WebView beta.
- [ ] Build APK (richiede un SDK Android/Buildozer non presente in questo
      ambiente Linux) e firma con chiave auto-generata (gratuita, requisito
      Android, **non** un cancello di store).
- [ ] Sideload su un device di test → checklist: encode, viewer 3D in WebView,
      scansione QR fotocamera, libreria.

**Nota di progetto importante — app nativa futura**: la beta WebView+server è
già offline, quindi **non** contraddice il concetto "tutto offline". Una futura
**app nativa vera** resta desiderabile, ma per ragioni diverse dall'offline:
footprint (niente interprete Python impacchettato → APK più leggero),
integrazione fotocamera/gesti nativa, avvio più rapido, UX mobile migliore, e
distribuzione più pulita. Documentata qui perché non vada persa, con la
**motivazione corretta** — non "altrimenti non è offline", che sarebbe
tecnicamente falso.

**Da qui è producibile**: l'impalcatura del server locale + WebView e il codice
di wiring. **Non producibile da qui**: l'APK reale e il test su device.

---

## Decisioni di prodotto ancora aperte (non-codice)

- Distribuzione commerciale / apertura licenza — dopo la beta.
- Meccanismo di licenza per-utente definitivo — dopo la beta.
- Balzar Bridge (integrazione PLC/HMI per Balzar Live automatico): solo
  scoping, nessuna decisione su protocollo/vendor (`CLAUDE.md` §9.19). Non
  serve per un v1 manuale.
