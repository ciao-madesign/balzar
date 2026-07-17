# Note sui componenti di terze parti

Balzar è software proprietario (vedi `LICENSE`), ma incorpora e distribuisce
componenti open-source di terze parti, ciascuno soggetto alla propria licenza.
Questo file li elenca con estrema trasparenza, per rispettarne i termini e per
non esporre il progetto ad alcun rischio legale.

La "riserva di tutti i diritti" del `LICENSE` di Balzar riguarda **solo** il
codice e i materiali originali del progetto. I componenti qui sotto restano
regolati dalle rispettive licenze; nulla nel `LICENSE` di Balzar le limita.

Nessuno di questi componenti è stato modificato rispetto alla versione
upstream, salvo dove diversamente indicato.

---

## Dipendenze Python (motore + app desktop)

Impacchettate nell'eseguibile desktop (PyInstaller) o richieste a runtime.

| Componente | Versione | Licenza | Ruolo |
|---|---|---|---|
| Python (interprete + stdlib) | 3.x | PSF License Agreement (permissiva) | runtime, impacchettato da PyInstaller |
| Pillow | (da `requirements.txt`) | HPND (MIT-CMU, permissiva) | decodifica immagini/GIF, unico modulo che dipende da Pillow |
| qrcode | (da `requirements.txt`) | BSD (permissiva) | generazione QR (puro Python) |
| pyzbar | (opzionale) | MIT (permissiva) | wrapper Python per la lettura QR |
| **libzbar** (libreria C nativa) | di sistema | **LGPL-2.1-or-later** | lettura QR nativa, caricata da pyzbar |

### Obbligo LGPL per libzbar — come è rispettato

`libzbar` è l'unico componente non-permissivo. La LGPL-2.1 consente l'uso in un
prodotto proprietario **a condizione** che la libreria LGPL resti sostituibile
e collegata dinamicamente. In Balzar questo è già garantito per costruzione:

- `pyzbar` carica `libzbar` **dinamicamente via `ctypes`**, mai linkata
  staticamente né fusa nel codice (verificato: nell'eseguibile PyInstaller
  `libzbar.so.0` è inclusa come file binario separato, non incorporata — vedi
  la nota tecnica di progetto §9.13).
- L'utente resta libero di sostituire quel file con una propria build di
  `libzbar`.
- Sorgente di `libzbar`: https://github.com/mchehab/zbar (progetto ZBar).

Nessun obbligo LGPL grava sul codice originale di Balzar, solo su `libzbar`
stessa, e quell'obbligo (linking dinamico + disponibilità del sorgente
upstream) è soddisfatto.

---

## Componenti JavaScript vendorizzati (viewer 3D + lettura QR nel browser)

Distribuiti come file statici nel repository (non da CDN, per l'uso offline).

| Componente | Versione | Licenza | File |
|---|---|---|---|
| @google/model-viewer | 4.3.1 | Apache-2.0 | `model-viewer.min.js` |
| jsQR | 1.4.0 | Apache-2.0 | `jsQR.min.js` |

### Componenti incorporati dentro model-viewer

La build di `model-viewer` include, incorporati, alcuni file di terze parti con
la propria attribuzione preservata negli header `@license` del file
`model-viewer.min.js`:

- porzioni © Google LLC — **BSD-3-Clause** (utility matematiche derivate da
  three.js);
- libreria `lit` / `ReactiveElement` — **BSD-3-Clause**.

La licenza del **pacchetto nel suo complesso** resta Apache-2.0; gli header
BSD-3-Clause sono attribuzioni preservate per singoli file presi in prestito,
non licenze in conflitto (vedi §9.13).

### Obblighi Apache-2.0 — come sono rispettati

- Gli avvisi di copyright e gli header `@license` all'interno dei file
  vendorizzati sono **conservati intatti**.
- I file non sono stati modificati (`jsQR.min.js` e `model-viewer.min.js` sono
  le build UMD ufficiali dei rispettivi pacchetti npm).
- Testo delle licenze Apache-2.0:
  https://www.apache.org/licenses/LICENSE-2.0

---

## Verifica

Le licenze qui elencate sono state verificate sui file/pacchetti reali usati
dal progetto (metadata pip, header dei file vendorizzati, file LICENSE
upstream), non a memoria — coerentemente con il principio "misura, non
stimare" del progetto. Aggiornare questo file ogni volta che si aggiunge,
rimuove o aggiorna una dipendenza.
