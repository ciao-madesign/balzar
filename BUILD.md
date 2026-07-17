# Build dell'app desktop Balzar (beta)

Istruzioni per produrre l'eseguibile desktop installabile su licenza, senza
store. Ordine di rilascio beta: **macOS prima, poi Windows** (vedi
`ROADMAP.md`). iOS fuori scope.

Questo file copre il **desktop**. L'app desktop esiste già (`balzar/gui.py`);
qui c'è solo come impacchettarla.

---

## 0. Prerequisiti (macchina di build)

- Python 3.11 o 3.12 con Tk (macOS/Windows lo includono; su Linux serve
  `python3-tk`).
- Dipendenze:

  ```
  pip install -r requirements.txt pyinstaller
  ```

- Libreria nativa per la lettura QR da foto (`pyzbar`/`libzbar`):
  - **macOS**: `brew install zbar`
  - **Windows**: inclusa nella wheel di `pyzbar`, nessuna azione
  - (senza di essa l'app parte comunque, solo "Scansiona foto QR" è disattivo;
    la scansione con fotocamera via browser non la richiede)

- Verifica che i test passino prima di impacchettare:

  ```
  python3 -m unittest discover -s tests
  ```

---

## 1. Impostare la chiave di licenza beta (una volta per build)

La beta ha una **chiave unica condivisa**, decisa da te. Il gate è
**fail-closed**: senza chiave impostata, la build impacchettata si rifiuta di
partire (di proposito).

1. Calcola l'hash della tua chiave senza farla passare in chiaro nei
   sorgenti/git:

   ```
   python3 -m balzar.license hash-key
   ```

   (chiede la chiave con input nascosto, stampa il suo SHA-256)

2. Incolla il valore in `BETA_KEY_SHA256` dentro `balzar/license.py`:

   ```python
   BETA_KEY_SHA256 = "…il tuo hash…"
   ```

   **Non committare** questa modifica se non vuoi che l'hash finisca in git —
   impostala solo sulla macchina di build al momento del packaging. (L'hash
   non rivela la chiave, ma tenerlo fuori dal repo pubblico è più pulito.)

Verifica: `python3 -m balzar.license status` → `configurato: True`.

---

## 2. Build macOS (sul tuo MacBook Air, Apple Silicon / arm64)

1. (Opzionale ma consigliato) genera l'icona `.icns` dal PNG già nel repo —
   solo su macOS, richiede gli strumenti di sistema `sips`/`iconutil`:

   ```sh
   mkdir -p balzar.iconset
   for s in 16 32 64 128 256 512; do
     sips -z $s $s   assets/balzar.png --out balzar.iconset/icon_${s}x${s}.png
     sips -z $((s*2)) $((s*2)) assets/balzar.png --out balzar.iconset/icon_${s}x${s}@2x.png
   done
   iconutil -c icns balzar.iconset -o assets/balzar.icns
   rm -rf balzar.iconset
   ```

   Se salti questo passo, la build usa comunque `assets/balzar.png` come
   fallback (il `.spec` è guardato: nessuna icona mancante rompe la build).

2. Impacchetta con lo spec (NON con il vecchio comando `--onefile … balzar-app.py`,
   che ignora asset e icona):

   ```sh
   pyinstaller balzar.spec
   ```

   Risultato in `dist/`: `Balzar.app` (bundle) e l'eseguibile `balzar`.

3. Avvia: doppio clic su `dist/Balzar.app`. All'avvio chiederà la chiave beta.

**Per i tester (macOS, app non notarizzata)**: al primo avvio Gatekeeper la
blocca. Aggiramento: **clic destro sull'app → Apri → Apri** (una volta sola),
oppure Impostazioni di Sistema → Privacy e sicurezza → "Apri comunque". La
notarizzazione a pagamento è rimandata a dopo la beta.

---

## 3. Build Windows (su una macchina Windows reale)

1. Stessi prerequisiti (Python + `pip install -r requirements.txt pyinstaller`).
2. Imposta la chiave beta (passo 1).
3. Impacchetta:

   ```
   pyinstaller balzar.spec
   ```

   Risultato: `dist\balzar.exe` (con l'icona `assets\balzar.ico`).

4. (Opzionale) installer con **Inno Setup** o **NSIS** (gratuiti): avvolgono
   `balzar.exe` in un `setup.exe` con voce nel menu Start. Per la beta puoi
   anche distribuire direttamente `balzar.exe`.

**Per i tester (Windows, exe non firmato)**: SmartScreen mostra "Windows ha
protetto il PC". Aggiramento: **Ulteriori informazioni → Esegui comunque**.
La firma con certificato EV è rimandata a dopo la beta.

---

## 4. Cosa è rimandato oltre la beta (deliberatamente)

Firma EV Windows, notarizzazione Apple ($99/anno), auto-update, `.app`
universale Intel+arm, installer rifinito. Vedi `ROADMAP.md`.
