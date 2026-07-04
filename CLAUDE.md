# balzar — contesto di progetto

Questo file è il contesto persistente del progetto: cosa fa il sistema, come è
fatto, cosa è stato verificato per davvero, cosa non funziona ancora e dove
si può andare da qui. Aggiornalo quando cambi qualcosa di architetturale.

## 1. Visione

balzar non comprime dati: **genera** contenuto (immagini, sequenze di frame)
a partire da una descrizione minima (seed + programma di regole). Non è
un codec — è "compressione algoritmica basata su descrizione" (program-based
generation). Il dato diventa minimo, la descrizione diventa il contenuto, la
complessità si sposta dal file al processo generativo.

Limite teorico, sempre presente in ogni decisione di design: **complessità di
Kolmogorov**. Contenuto strutturato (CAD, pattern, icone, UI, frattali) si
comprime di ordini di grandezza. Contenuto casuale (foto, rumore, video da
fotocamera) non dà guadagno, e il sistema **lo deve dichiarare onestamente**
invece di fingere una compressione che non c'è. Questa onestà è un requisito
di prodotto, non un dettaglio tecnico: è quello che distingue balzar da un
tool di compressione bugiardo.

Il prodotto finale è un **programma desktop offline** (tipo zipper): apri un
file, lo comprimi in un payload generativo, lo salvi; apri un payload, lo
rigeneri. La demo web (Vercel) è solo una vetrina di prova online, non il
prodotto.

## 2. Stato attuale — cosa esiste e funziona

Tutto il codice sotto è stato scritto, testato con `unittest`, e per le parti
con interfaccia (web + desktop) verificato manualmente con Playwright /
screenshot reali, non solo letto.

### 2.1 Motore deterministico (stdlib pura, zero dipendenze)

| File | Ruolo |
|---|---|
| `balzar/grid.py` | Stato: griglia a indici di palette (bytearray), `Region` |
| `balzar/rng.py` | PRNG deterministico proprio: xorshift64* + splitmix64. **Mai** usare `random` — la sequenza è parte del contratto di formato |
| `balzar/dsl.py` | Parser DSL + valutatore di espressioni aritmetiche (AST whitelistato: solo `+ - * / // % **`, niente chiamate/stato/IO) |
| `balzar/ops.py` | Motore di trasformazioni: registry dichiarativo tipizzato (`@op(...)`). Geometriche (SHIFT/ROTATE/MIRROR/SCALE), strutturali (COPY/SWAP/TILE), differenziali (SETPIX/FILL/MAP/INVERT/FRAME/TEXT), generative (RECT/LINE/CIRCLE/NOISE/SCATTER/FRACTAL) |
| `balzar/font5x7.py` | Font bitmap 5×7 incorporato (A-Z, 0-9, punteggiatura tecnica) usato da `TEXT` — nessuna dipendenza da font esterni, carattere sconosciuto = blocco pieno visibile (mai silenzioso) |
| `balzar/interpreter.py` | Esegue il programma parsato → frame RGB. `MAX_STEPS` come valvola di sicurezza contro loop runaway |
| `balzar/payload.py` | Formato binario `BZR1` (magic+lunghezza+CRC32+deflate del programma canonico) e formato a capitoli `BZC1` per il supporto fisico |
| `balzar/png.py` | Writer PNG RGB8 in puro Python (nessun filtro adattivo — vedi criticità §4) |

**Garanzie di determinismo** (verificate in `tests/test_determinism.py`):
stesso payload ⇒ stessi pixel su ogni piattaforma. Niente float dove conta
(rotazioni solo 90/180/270, scaling nearest-neighbour, Bresenham per le
linee), PRNG proprio, espressioni totali. Il frattale di Mandelbrot è l'unica
eccezione dichiarata (usa double IEEE-754, riproducibile bit-a-bit tra build
CPython ma non un'astrazione intera pura).

### 2.2 Encoder automatico (immagine → programma)

`balzar/encoder.py` — il pezzo che nella spec originale (sez. 5.1) era solo
teoria, ora implementato e testato (`tests/test_encoder.py`):

1. **quantizzazione palette**: lossless se l'immagine ha già ≤256 colori
   (icone, screenshot, export CAD, pixel art); altrimenti arrotondamento
   colore a passi crescenti (2,4,8,...,64 per canale, il più fine che
   basta) — non più un fallback fisso grezzo, dichiarato con precisione
   (`color_step`, `fidelity_label()`) invece di un booleano lossless/lossy
   piatto — vedi criticità §4.2;
2. **rilevamento tiling**: prova **tutti** i divisori di w e h (i candidati
   sbagliati falliscono alla prima riga, quindi il costo è basso) — trova
   piastrelle anche grandi, es. 100×100 su un canvas 800×800;
3. **copertura greedy a rettangoli**: scansione riga per riga, ogni blocco
   di colore uniforme diventa un `RECT`; i pixel isolati diventano `SETPIX`
   (più corto di un RECT 1×1 degenere);
4. **auto-verifica obbligatoria**: il programma generato viene renderizzato
   e confrontato pixel-per-pixel con la sorgente quantizzata prima di essere
   restituito. Non si dichiara mai "lossless" senza averlo controllato.

`balzar/imageio.py` è l'**unico** modulo che dipende da Pillow (decodificare
JPEG/PNG arbitrari da zero è fuori scope — non reinventare un decoder JPEG).
Il resampling nel downscale è **NEAREST, non Lanczos**: lo smoothing
introduce centinaia di colori intermedi sui bordi e distrugge esattamente la
struttura che l'encoder sfrutta (misurato: stesso file, 11,9× con Lanczos vs
1211× con NEAREST).

### 2.3 Video (sequenze di frame)

`balzar/video.py` implementa il modello differenziale (spec sez. 4.3) sul
caso reale: frame 0 codificato per intero, ogni frame successivo costa solo
i pixel che cambiano (coperti a rettangoli, `FRAME` separa gli stati).
**Non** è un flipbook di frame indipendenti — quello butterebbe via la
ridondanza temporale, che è dove vive quasi tutta la comprimibilità di un
video. Verificato (`tests/test_video.py`) che il delta batte sempre la somma
degli encode indipendenti (>2× su un test con contenuto in movimento).

Misura reale: GIF 320×240, 30 frame, palla che attraversa una griglia
tecnica → payload 8.144 byte contro 6.912.000 byte di RGB grezzo = **849×**,
lossless su tutti i 30 frame.

**Caso d'uso "sequenza di montaggio navigabile"** (`examples/sequenza_montaggio.bzr`):
10 step di assemblaggio progressivo (i pezzi si aggiungono, mai tolti) +
BOM che cresce di una riga per step + indicatore testuale di stato che si
riscrive ogni step (`FILL` di una piccola regione + `TEXT`). Scritto a
mano con blocchi sequenziali (non `LOOP`: ogni step aggiunge un pezzo
qualitativamente diverso, il DSL non ha condizionali per esprimerlo in
un ciclo). Numeri reali misurati in sessione:

| Rappresentazione | Byte totali |
|---|---|
| RGB grezzo (10 frame 760×520) | 11.856.000 |
| 10 PNG indipendenti (il nostro `png.py`) | 57.810 |
| Ri-deflate dei 10 PNG concatenati (stima ZIP) | 42.807 |
| 10 frame codificati indipendentemente con l'encoder immagine (flipbook) | 157.713 |
| **Payload balzar (delta, 10 step)** | **766** |

766 byte per l'intera sequenza, in un solo QR con ampio margine (limite
2.953). Il confronto che conta di più: **75× più piccolo della somma dei
10 PNG indipendenti**, **206× più piccolo del flipbook con lo stesso
nostro encoder** — la differenza è quasi interamente dovuta al fatto che
la BOM e il disegno **si accumulano** invece di essere ridisegnati da
zero ogni step (lo stesso principio del modello differenziale, applicato
non solo ai pixel ma anche al testo).

**Navigazione avanti/indietro**: gratuita in un senso preciso — dopo il
render, `RenderResult.frames` è già una lista ad accesso casuale, non uno
stream sequenziale; "indietro" non è un problema di decodifica, è solo
un cambio di indice. Prima di questa sessione la GUI desktop faceva però
**solo auto-play in loop**, senza controlli manuali: aggiunti pulsanti
◀ Indietro / ⏸ Pausa/▶ Play / Avanti ▶ + etichetta "Step N/M" in
`balzar/gui.py`, verificati sotto Xvfb (navigazione manuale, toggle
play/pausa, indice modulo corretto in entrambe le direzioni).

### 2.4 Supporto fisico (serie di QR)

`chunk_payload` / `assemble_chunks` in `balzar/payload.py`: un payload più
grande di un QR si spezza in capitoli autodescrittivi —

```
"BZC1" | u16 indice | u16 totale | u32 CRC-32 del payload intero | dati
```

Ogni capitolo sta in un QR v40 (~2953 byte), porta con sé posizione e
checksum dell'insieme. I capitoli si riassemblano **in qualsiasi ordine**
(testato con shuffle) e la corruzione/mancanza viene rilevata.

**Integrato nel codice** (`balzar/qr.py`, richiede `qrcode` + `pyzbar`,
dipendenze opzionali non nel motore core): `payload_to_qr_image` genera
un'immagine singola se il payload sta in un QR, altrimenti spezza in
capitoli e li dispone in una **griglia auto-dimensionata** nella stessa
immagine; `scan_image_bytes`/`scan_image_file` fanno il percorso inverso,
decodificando con ZBar tutti i QR trovati in una foto e riassemblando
in qualsiasi ordine. Esposto come `balzar chunks --qr` / `balzar scan` in
CLI e come pulsanti "Esporta QR (immagine)" / "Scansiona foto QR" in GUI.
Verificato end-to-end (`tests/test_qr.py` + test manuali in sessione):
payload piccolo → 1 QR → scansionato → bit-identico; payload video da
8.144 byte → griglia 2×2 (4 capitoli) → fotografata in un colpo solo →
riassemblata → video di 30 frame rigenerato correttamente, anche con i
capitoli letti fuori ordine.

Due dettagli tecnici emersi costruendolo, da ricordare:
- **I byte grezzi non sopravvivono al giro libreria-QR→ZBar**: un test con
  2.953 byte binari (incluso `0x00` e tutti i valori 0-255) è tornato
  corrotto (4.370 byte invece di 2.953). I capitoli vanno quindi
  **sempre** codificati in base64 prima di finire in un QR (come già fa
  `encode --base64`), mai come byte grezzi.
- **Il livello di correzione errori conta per la capacità**: usare
  `ERROR_CORRECT_M` invece di `ERROR_CORRECT_L` fa scendere la capacità
  di un QR v40 da 2.953 a 2.334 byte, causando un errore "Invalid version
  41" su payload che in teoria ci starebbero — `balzar/qr.py` usa L per
  restare coerente con `QR_V40_BINARY_CAPACITY`, scambiando robustezza
  fisica extra (che L comunque non ha, 7% di recovery) con più byte per
  QR; la corruzione è comunque rilevata dal CRC di `BZC1` al riassemblaggio.
- `cv2.QRCodeDetector().detectAndDecodeMulti` (OpenCV nativo, senza
  dipendenze extra) ha letto solo 5 QR su 15 nello stesso scatto in un
  test precedente — la sua multi-decodifica è inaffidabile oltre pochi
  codici. **ZBar (`pyzbar`) li legge tutti**: usare quello, non il
  detector nativo di OpenCV.

### 2.5 Export SVG (vettoriale reale, non raster incapsulato)

`balzar/svg.py` — un secondo target di rendering per lo stesso DSL, non
un'estensione dell'encoder. PNG (`png.py`) rasterizza **qualunque**
programma sempre; SVG no, e lo dichiara: solo il sottoinsieme di
operazioni con un equivalente vettoriale diretto è supportato —
`CANVAS`, `PALETTE`, `REGION`, `LOOP`, `RECT`, `LINE`, `CIRCLE`, `TEXT`,
`FILL`, `COPY`, `TILE`, e **al massimo un `FRAME`** (video/animazioni
restano dominio di PNG/GIF). Ops senza un significato vettoriale pulito
(`SHIFT`, `ROTATE`, `MIRROR`, `SCALE`, `SWAP`, `MAP`, `INVERT`, `NOISE`,
`SCATTER`, `FRACTAL`, `SETPIX`, o un programma multi-frame) fanno
sollevare `UnsupportedForSVG` con il nome esatto dell'istruzione
incompatibile, invece di rasterizzare silenziosamente una toppa o
produrre un file che sembra vettoriale ma non lo è.

Dettagli tecnici non ovvi:
- `TILE` diventa un vero `<pattern>` SVG (riempimento scalabile nativo,
  non una copia raster ripetuta) — corrispondenza quasi perfetta con la
  semantica dell'istruzione.
- `COPY` duplica gli elementi vettoriali già emessi nella regione
  sorgente dentro un `<g transform="translate(...)">` alla destinazione:
  un cerchio copiato resta un cerchio vero, non una toppa raster.
- `TEXT` diventa `<text>` reale/editabile (font generico monospace), **non**
  una riproduzione pixel-perfect del font bitmap 5×7 — scelta deliberata:
  testo vettoriale modificabile in Illustrator/Inkscape vale più di un
  match esatto del glifo che nessuno può selezionare o restilizzare.

Verificato su tutti gli esempi (`tests/test_svg.py` + rendering reale in
browser via Playwright): `etichetta_bom.bzr` e `schema_tecnico.bzr`
esportano puliti (COPY per i bulloni → cerchi vettoriali reali, non
pixel); `pattern_tile.bzr` (SHIFT/NOISE), `frattale.bzr` (FRACTAL),
`animazione.bzr`/`esploso_industriale.bzr` (multi-frame) vengono
onestamente rifiutati con il motivo esatto.

### 2.6 Ingestione vettoriale (SVG/DXF → DSL, no raster)

`balzar/vectorio.py` — **fatto**, era il punto 1 di §5 nella versione
precedente di questo documento. Motivazione diretta: un utente ha notato
che il testo/le forme "fotografate" (screenshot → encoder raster)
degradano vistosamente, mentre il testo generato direttamente con `TEXT`
(es. `etichetta_bom.bzr`) resta perfetto — perché non passa mai per
quantizzazione colore né per la copertura a rettangoli, che è dove si
perde tutto. `vectorio.py` estende quella stessa esattezza ai file
vettoriali esterni: un `<circle>` SVG o un'entità `CIRCLE` DXF hanno già
centro e raggio espliciti, si mappano 1:1 su `CIRCLE` senza rasterizzare
né dedurre nulla da pixel.

Due parser scritti da zero, **zero dipendenze nuove** (coerente col
motore core): SVG via `xml.etree.ElementTree` (stdlib), DXF con un lettore
di coppie codice/valore ASCII scritto a mano (il formato è testuale e
semplice da leggere per le entità comuni, non serve una libreria CAD).

Supportato: `RECT`/`CIRCLE`/`LINE` (anche da `<polyline>`/`<polygon>`/
`<path>` con solo comandi `M`/`L`/`Z`, e da `LWPOLYLINE` in DXF), `TEXT`
(da `<text>` SVG e da entità `TEXT`/`MTEXT` DXF — **la stessa `TEXT`
esatta usata a mano**, non testo rasterizzato), gruppi `<g
transform="translate(...)">` in SVG, colori ACI 1-9 in DXF (la tabella
completa a 256 voci non è verificabile senza accesso a rete in questo
ambiente — onestamente non hardcodata a rischio di sbagliarla).

Non supportato — **saltato con il motivo esatto**, mai in silenzio (stesso
principio di `svg.py` ma best-effort invece di tutto-o-niente, perché qui
non c'è un secondo target di rendering dello stesso DSL da cui aspettarsi
un supporto completo, ma un formato esterno arbitrario): curve SVG
(`C`/`S`/`Q`/`T`/`A`), trasformazioni diverse da `translate`, archi/spline
DXF, colori ACI fuori dalla tabella nota (resi in grigio neutro,
dichiarato in `skipped`).

Due bug reali trovati testando prima di dichiarare la funzione pronta:
- **Sfondo bianco non garantito**: il primo tentativo assumeva che
  l'indice di palette 1 fosse sempre bianco (convenzione degli esempi
  scritti a mano), ma la palette qui si costruisce dinamicamente dai
  colori del file sorgente — è finito per diventare rosso per coincidenza
  d'ordine. Fix: il bianco viene sempre riservato esplicitamente come
  indice 0 prima di processare qualunque elemento.
- **Convenzione baseline testo**: la `y` di `<text>` SVG e delle entità
  `TEXT` DXF è la *baseline* (base del testo), mentre la nostra `TEXT`
  interpreta `y` come il *top* del glifo — senza correzione il testo
  risultava tagliato dal bordo del canvas. Corretto sottraendo/sommando
  l'altezza del font in base alla convenzione dell'asse Y di ciascun
  formato (SVG y giù, DXF y su — direzioni opposte).

Verificato end-to-end (`tests/test_vectorio.py` + rendering reale in
sessione): `examples/flangia_sorgente.svg`/`.dxf` (flangia con fori
imbullonati + etichetta di testo, lo stesso soggetto di
`schema_tecnico.bzr` ma come sorgente vettoriale esterna) convertiti con
**zero elementi saltati**, payload 230 B (SVG) / 255 B (DXF), entrambi in
un singolo QR con ampio margine. Il risultato SVG è a sua volta
ri-esportabile come SVG vettoriale reale via `svg.py` (usa solo
`CIRCLE`/`LINE`/`TEXT`), chiudendo il cerchio SVG→balzar→SVG senza mai
passare per un pixel.

### 2.7 App desktop (il prodotto)

`balzar/gui.py` + `balzar-app.py` — Tkinter (stdlib) + Pillow. Apri
immagine/GIF/payload → encoding in thread separato (la finestra non si
blocca) → anteprima animata fianco a fianco originale/rigenerato →
statistiche oneste → salva `.bzp`/`.bzr`, esporta PNG/GIF, esporta QR come
**immagine reale** (singola o griglia auto-dimensionata, `balzar/qr.py`),
pulsante "Scansiona foto QR" per il percorso inverso. Impacchettabile in
un eseguibile singolo con PyInstaller
(`pyinstaller --onefile --windowed --name balzar balzar-app.py`) —
**il packaging PyInstaller non è stato ancora eseguito/testato in questa
sessione**, solo documentato; da verificare che includa anche la libreria
nativa `libzbar` richiesta da `pyzbar`, non solo codice Python.

Verificato con screenshot reale sotto Xvfb: apertura GIF, encoding video
delta, anteprima animata, pannello statistiche, bottoni attivi, ciclo
completo esporta-QR→scansiona-foto→payload bit-identico.

### 2.8 Demo web (solo vetrina, non il prodotto)

`index.html` + `app.js` + `style.css` + due funzioni serverless Vercel
(`api/encode.py`, `api/render.py`) + `balzar/webapi.py` (logica condivisa
con profili di limiti espliciti: `VERCEL_LIMITS` vs `LOCAL_LIMITS`,
quest'ultimo non ancora agganciato a un vero deployment). Due tab nella
pagina: **"Comprimi immagine"** (il flusso originale, `api/encode.py`) e
**"Apri programma (.bzr/.bzp)"** (`api/render.py` + `handle_render` in
`webapi.py`) — quest'ultima chiude il caso d'uso "ho scaricato un `.bzr`
da qui e non ho un terminale": carica il file, viene decodificato e
rigenerato, scarichi PNG (e GIF se multi-frame, e SVG se il programma è
vettoriale — §2.5) senza installare nulla. Verificato end-to-end in
sessione (Playwright): `.bzr` e `.bzp` entrambi riconosciuti, bottone SVG
che appare/sparisce correttamente in base al programma, video multi-frame
→ bottone GIF, file non valido → errore 400 con messaggio chiaro.

Vercel impone limiti reali (~3,3MB upload utile, ~4,5MB risposta, timeout)
gestiti esplicitamente con messaggi chiari invece di errori criptici —
vedi `MAX_PREVIEW_DIM`, `MAX_PROGRAM_CHARS`, `MAX_PAYLOAD_B64_BYTES` in
`balzar/webapi.py`. **Questi limiti non esistono nell'app desktop**, che
è il prodotto vero.

### 2.9 CLI

`balzar render|encode|encode-image|encode-video|decode|info|chunks|scan|assemble|gui`
— vedi `balzar/cli.py` per l'elenco completo con esempi in `README.md`.

### 2.10 Test

85 test, tutti verdi (`python3 -m unittest discover -s tests`):
`test_determinism.py`, `test_ops.py`, `test_expansion.py`, `test_encoder.py`,
`test_qr.py` (skippato automaticamente se `qrcode`/`pyzbar` non sono
installati — dipendenze opzionali, non nel motore core),
`test_video.py`, `test_svg.py`, `test_vectorio.py`. Copertura: round-trip
bit-identico, corruzione rilevata,
correttezza delle singole operazioni, fattori di espansione sugli esempi,
encoder lossless su contenuto strutturato e onesto su rumore, video delta
vs flipbook, capitoli in ordine sparso/mancanti/corrotti.

## 3. Numeri misurati (non stimati) fin qui

| Caso | Payload | Output | Fattore |
|---|---|---|---|
| `examples/pattern_tile.bzr` (autore umano) | 276 B | 1024×1024 | ~11.400× |
| `examples/animazione.bzr` (autore umano, 24 frame) | 210 B | 4,7 MB RGB | ~22.500× |
| Icona geometrica sintetica (encoder auto) | — | — | peggio del PNG (bordi non assiali) |
| Scacchiera 256×256, tiling 32×32 (encoder auto) | 168 B | 196.608 B | 1.170× |
| Schema tecnico ripetuto 1600×1600→800×800 (encoder auto, NEAREST) | 1.585 B | 1,92 MB | 1.211× |
| Rumore puro 800×800 (encoder auto) | 2,73 MB | 1,92 MB | **0,7×, nessun guadagno** (dichiarato) |
| GIF palla+griglia 320×240×30 frame (video encoder) | 8.144 B | 6,91 MB | 849× |
| Confronto onesto vs JPEG/PNG/ZIP/DEFLATE su vista esplosa 5 frame | 424 B | 7,2 MB | 40×–17.000× a seconda della baseline |
| Screenshot UI sintetico anti-aliased, 455 colori esatti (encoder auto) | 18.751 B (passo 4, vs 7.949 B col vecchio fallback fisso) | 384.000 B | 20,5× (qualità visibilmente migliore: ombra/pattern di sfondo preservati) |
| `examples/flangia_sorgente.svg` (ingestione vettoriale, 0 elementi saltati) | 230 B | 800×800 | in un solo QR, margine ampio |
| `examples/flangia_sorgente.dxf` (stesso soggetto, ingestione DXF, 0 saltati) | 255 B | 789×800 | in un solo QR, margine ampio |

## 4. Criticità note (non nascoste, da affrontare quando serve)

1. **Niente rilevamento linee/cerchi/curve nell'encoder *raster*.** La
   copertura a rettangoli va in crisi su contenuto rasterizzato con bordi
   non assiali (diagonali, cerchi): un'icona con una linea diagonale e
   un'ellisse è risultata **peggiore del PNG** (4.216 B vs 1.900 B) perché
   ogni pixel di bordo diventa la propria istruzione. Servirebbe un
   fitting tipo Hough transform per linee/cerchi — non implementato,
   resta una lacuna dell'encoder raster v1. **Aggirata, non risolta, per
   il caso con sorgente vettoriale disponibile**: `vectorio.py` (§2.6)
   ingerisce SVG/DXF direttamente, quindi un cerchio/una linea con quella
   sorgente non passa mai dal problema (niente pixel da cui dedurre
   nulla). Resta valida per contenuto che arriva *solo* rasterizzato
   (screenshot, scansioni) senza una sorgente vettoriale disponibile.
2. **Quantizzazione lossy oltre 256 colori — migliorata ma non risolta
   del tutto.** Non è più il fallback fisso 3-3-2 (passi ±16/±32 sempre,
   anche quando servirebbe pochissimo): ora `_quantize` in `encoder.py`
   prova passi di arrotondamento crescenti (2,4,8,...,64 per canale) e usa
   il più fine che riporta il conteggio colori ≤256. Caso reale misurato
   in sessione (screenshot UI con testo anti-aliased, icone arrotondate,
   ombra sfumata, sfondo a puntini — 455 colori esatti): passo 4 basta
   (palette 217 colori) invece del vecchio passo fisso ~32, con il pattern
   di sfondo e l'ombra visibili invece di scomparire nel banding. Costo
   onesto: payload più grande (18.751 B contro 7.949 B col vecchio
   fallback) — più fedeltà costa più byte, dichiarato via il nuovo campo
   `EncodeResult.color_step` e `fidelity_label()` (non più un booleano
   lossless/lossy piatto). Il fallback fisso 3-3-2 è stato rimosso perché
   con passo 64 restano al più 4×4×4=64 colori possibili per pigeonhole —
   quindi era già codice morto, mai raggiunto. Resta vero che un vero
   quantizzatore percettivo (median-cut, o clustering nello spazio colore)
   darebbe risultati ancora migliori sulle foto vere, senza cambiare
   l'esito di fondo (le foto restano il caso a basso/nessun guadagno).
3. **`png.py` non usa filtri di scanline adattivi** (Sub/Up/Paeth): un
   secondo passaggio DEFLATE sui PNG generati li comprime ulteriormente del
   ~25-30%. Non è un bug bloccante (i confronti onesti fatti nella
   conversazione ne hanno tenuto conto), ma un PNG "vero" di libreria
   sarebbe più piccolo di quello scritto da `balzar.png`.
4. **Il flusso "capitoli QR" non genera/legge QR reali nel codice**: produce
   testo base64 da incollare in un generatore esterno, e non c'è un comando
   di lettura. L'esperimento di questa sessione (generazione con `qrcode`,
   lettura multi-QR con `pyzbar`/ZBar) ha provato che il concetto regge, ma
   va portato dentro il progetto (nuova dipendenza opzionale, nuovo comando
   CLI/GUI) — vedi Sviluppi §5.
5. **PyInstaller non testato**: il packaging in eseguibile singolo è
   documentato ma non verificato in questa sessione (nessun ambiente
   Windows/macOS disponibile qui). Da testare prima di distribuire.
6. **Vercel: `vercel.json` non testato con un deploy reale** in questa
   sessione (nessun deploy effettuato, solo simulato con un server locale
   equivalente). Verificare `maxDuration`/`memory` reggono sul piano
   effettivamente usato.
7. **Limite architetturale di fondo, non un bug**: qualunque incremento
   dell'encoder resta vincolato alla complessità di Kolmogorov del
   contenuto. Non esiste un encoder che comprima bene contenuto genuinamente
   casuale — non è un obiettivo raggiungibile, è escluso per definizione.

## 5. Sviluppi possibili (ordinati per valore/sforzo stimato)

1. ~~Ingestione diretta di formati vettoriali (SVG/DXF)~~ — **fatto**
   (`balzar/vectorio.py`, comando `balzar encode-vector`): vedi §2.6.
2. ~~Comando `balzar scan` + generazione QR reale~~ — **fatto** (`balzar/qr.py`,
   `balzar chunks --qr`, `balzar scan`, pulsanti GUI): vedi §2.4.
3. **Supporto hardware dedicato: lettore QR + schermo.** Idea proposta in
   sessione per l'adozione reale in officina/ONG (applicazioni §6.1 e
   §6.3): un dispositivo fisico che fotografa QR (singoli o griglia,
   `balzar/qr.py` già lo fa) ed espande il contenuto (esploso CAD, BOM,
   schema) su schermo, senza rete, senza PC. **Fase 1, prototipo**: uno
   smartphone Android vecchio/dismesso — ha già fotocamera + schermo +
   batteria, quindi zero costo hardware aggiuntivo, solo software. Il
   percorso più realistico non è "installare Tkinter su Android" (non
   funziona, vedi discussione sessione su iOS/Android: Tkinter non gira
   su mobile) ma impacchettare il *solo motore* (stdlib pura, già
   portabile) con un layer UI minimale mobile-native — Kivy o BeeWare/
   Briefcase (già valutati come le due strade realistiche per
   Android/iOS) — oppure, ancora più semplice per un vero prototipo
   rapido, una web-app locale (HTML+JS che chiama un piccolo server
   Python locale sul telefono stesso, es. via Termux) che riusa
   `balzar/qr.py` + `interpreter.py` così come sono. Il valore del
   prototipo "vecchio smartphone" non è il prodotto finale (l'app dedicata
   verrebbe dopo, magari su un device più economico/robusto tipo un
   pannello industriale con Android embedded) ma la dimostrazione a costo
   zero: fotografa un'etichetta reale, vedi l'esploso apparire su uno
   schermo vero, senza PC, senza rete — l'argomento più concreto possibile
   per convincere un'officina o un'ONG a investire nell'adozione.
   **Non ancora iniziato**: nessun lavoro di packaging mobile nel codice
   oggi.
4. **Rilevamento linee/cerchi (Hough) sul raster**: utile solo per
   contenuto che arriva *già rasterizzato* senza sorgente vettoriale
   disponibile (screenshot, scansioni). Se il punto 1 copre il caso reale
   più comune (CAD/schemi hanno quasi sempre una sorgente vettoriale),
   questo scende in priorità — è uno sforzo maggiore (fitting reale, non
   solo lettura) per una porzione più piccola di casi.
5. **Packaging e distribuzione reale**: build PyInstaller testate su
   Windows/macOS/Linux, eventualmente firma del codice, installer.
6. **Filtri PNG adattivi** in `png.py` per output competitivo con encoder
   PNG di libreria (criticità #3) — minore, ma facile.
7. **Generazione diretta del QR dal payload** (già in parte coperta dal
   punto 2).
8. **Pre-rendering di stati UI/HMI finiti** (versione ridimensionata e
   costruibile dell'idea "gemello UI runtime" — vedi §7.2 per il perché la
   versione ambiziosa non è realistica): se un pannello industriale ha un
   numero finito di stati visivi noti (idle/loading/alarm/errore), ognuno
   si pre-renderizza offline col motore video esistente (`video.py`, stessa
   tecnica del delta tra frame) in un unico payload compatto; un wrapper
   esterno piccolissimo sceglie quale frame mostrare in base allo stato live
   letto altrove. Zero nuove primitive nel motore — è un caso d'uso di
   `encode_video`, non un'estensione.
9. **Scene 3D** con lo stesso modello stato+trasformazioni (estensione
   dichiarata fin dalla visione originale, non ancora iniziata). Il
   candidato più lontano di tutti: servirebbe un parser di un formato CAD
   reale (es. STEP, geometria B-rep con vincoli/simmetrie) *e* primitive 3D
   nel DSL — nessuna delle due esiste oggi. Vedi §7.3 per l'analisi
   dettagliata di perché non è "il prossimo passo facile" nonostante sembri
   il caso ideale sulla carta.
10. **Quantizzatore percettivo migliore** per il fallback lossy (criticità #2).
11. **Encoder per dati strutturati non-immagine** (JSON/XML ripetitivi):
    problema diverso dalla compressione di immagini — "template + diff dei
    parametri" invece di "rettangoli di pixel". Concettualmente vicino al
    modello LOOP+espressioni del DSL, ma richiederebbe un encoder
    interamente nuovo, non un'estensione di `encoder.py`. Speculativo,
    nessun lavoro iniziato.

## 6. Applicazioni target (valutate, non solo elencate)

Sei direzioni d'uso concrete, ordinate dalla più B2B/tecnica alla più
consumer. Per ognuna: perché balzar specificamente (con un numero reale
dietro, non una stima), e la precondizione che la rende vera.

1. **Manuali tecnici, ricambi ed esplosi/BOM per officina e manutenzione
   sul campo.** Il caso guida del progetto: reparti produttivi spesso non
   hanno viewer 3D/licenze CAD accanto alla macchina, e la manutenzione
   sul campo (stabulari sotterranei, navi, cantieri) spesso non ha rete.
   Un'etichetta/QR rigenera schema esploso *e* distinta base (BOM) — testo
   incluso, vedi `balzar/font5x7.py` e l'operazione `TEXT` — senza viewer
   3D, senza licenza CAD, senza connessione: sostituisce la pila di PDF
   disordinati. Esempio completo: `examples/etichetta_bom.bzr` (esploso +
   tabella part number/descrizione/quantità in un payload di 559 byte,
   entra in un singolo QR). Numeri più forti del progetto sui soli disegni
   (`schema_tecnico.bzr`, `esploso_industriale.bzr`): 2.900×–17.000× a
   seconda della baseline — vedi §9 per il confronto quantitativo
   completo con l'alternativa reale (PDF su chiavetta/stampato).
   Precondizione: il disegno va esportato pulito (CAD/vettoriale), non
   fotografato — **ora ancora più diretto**: `balzar encode-vector` (§2.6)
   ingerisce l'SVG/DXF esportato dal CAD senza passare da uno screenshot.
   Per portare questo in officina/ONG senza un PC vicino alla macchina,
   vedi l'idea di supporto hardware dedicato al punto 3 di §5.
2. **Asset per firmware/embedded**: icone, boot animation, sprite UI come
   programma invece di bitmap in flash — il decoder è stdlib pura apposta
   per questo. Coerente con la visione originale (sez. 10 della spec).
3. **Distribuzione offline di contenuti tecnici/didattici** in zone a bassa
   connettività: una pagina di QR fotografata in un colpo solo (provato:
   15 QR, ZBar, riassemblaggio bit-identico — vedi §2.4) consegna
   diagrammi/animazioni senza rete dati.
4. **Asset procedurali per videogiochi/app**: tileset, pattern UI, sprite
   animati generati a runtime da un seed invece che scaricati come bitmap.
   Non è una novità (procedural generation esiste da decenni nei motori di
   gioco), ma balzar offre un formato portabile e interpretabile invece di
   codice ad-hoc per motore.
5. **Marketing generativo/branding fisico**: QR su packaging che
   rigenerano un pattern di brand animato. Il valore è il gesto ("appare
   dal nulla" da un'etichetta minuscola), non la percentuale di
   compressione — e funziona perché il pattern è *disegnato* per essere
   strutturato, va comunicato così o sembra una promessa che non regge
   sulla prima foto di un cliente.
6. **Musica: notazione/MIDI strutturato, non audio.** Vedi §7.4: idea
   valida solo se si resta su rappresentazione simbolica (spartito, MIDI,
   pattern ritmici/melodici), MAI su audio campionato (dove balzar non ha
   nulla da offrire: un MP3/WAV già usa una compressione percettiva
   ottimizzata da decenni di ricerca, un secondo passaggio non fa che
   peggiorare — stesso principio per cui in tabella MP3/AAC/MP4 sono
   segnati come "peggiora sempre").

## 7. Idee esterne valutate (per non ridiscuterle da zero)

Registro delle proposte esterne (consulenze, brainstorm) con verdetto
esplicito: cosa è balzar-oggi, cosa è "stessa filosofia ma prodotto
diverso", cosa è semplicemente non fattibile con l'architettura attuale.

### 7.1 Formati vettoriali/CAD (SVG, DXF, STEP, G-code, GLTF, STL, OBJ)

Un consulente ha proposto una classifica di "efficacia Balzar" per ~25
formati di file. Il principio qualitativo è corretto e coincide col
nostro (strutturato/vettoriale comprime, percettivo/già-compresso no), ma
**tutti i numeri della tabella sono aspirazionali**: balzar oggi ingerisce
solo immagini raster via Pillow, zero parsing di STEP/SVG/DXF/G-code/
GLTF/STL/OBJ/XML/JSON. Nessuno di quei formati è supportato nel codice.
Vedi §5.1 per l'estensione reale più vicina (SVG/DXF) e §7.3 per il caso
STEP nel dettaglio.

### 7.2 "Gemello digitale" di una UI industriale runtime

Proposta: serializzare un pannello HMI (component library + layout rules
+ state machine + binding logici tipo `if machine.status == alarm →
AlarmWidget.visible = true`) come "UI execution graph" eseguibile da
balzar. **Non è un'estensione di balzar**: il DSL attuale non ha
condizionali (solo aritmetica totale su variabili di loop, per design —
vedi `dsl.py`), non legge stato esterno a runtime (il seed è cotto nel
payload), non ha un modello a componenti/oggetti. Servirebbe un
linguaggio nuovo con condizionali, input live, binding reattivi — un
prodotto fratello che condivide la filosofia (determinismo, niente
storage di dati grezzi) ma non l'architettura (griglia di pixel +
trasformazioni geometriche). La proposta contiene anche una
contraddizione interna: il suo stesso piano B per il caso realistico
("nessun accesso al codice") è un modello *probabilistico* ricostruito
dai log — che contraddice il punto 7.1 della visione originale
(determinismo totale, zero probabilità). Versione ridimensionata e
realmente costruibile con l'architettura attuale: punto 7 di §5
(pre-rendering di un numero finito di stati UI noti via `encode_video`,
scelta del frame delegata a un wrapper esterno).

### 7.3 Perché STEP non è il prossimo passo, nonostante sembri il caso ideale

STEP descrive geometria B-rep con primitive parametriche vere (cilindro,
foro, raggio, vincoli, simmetrie dichiarate) — sulla carta è esattamente
il tipo di struttura che il modello di balzar ama. Il problema non è il
principio, è che servono **due cose che non esistono, non una**:

1. **Un parser STEP reale.** STEP (ISO 10303) non è un formato semplice
   da leggere a mano: è un linguaggio di scambio dati completo (EXPRESS),
   normalmente letto con librerie CAD pesanti (OpenCascade e simili, non
   pure-Python, non piccole). Scriverne uno da zero è un progetto a sé,
   ordini di grandezza più grande di `imageio.py` (che delega tutto il
   parsing pesante a Pillow, una libreria matura da vent'anni — non
   esiste un equivalente leggero per STEP).
2. **Primitive 3D nel DSL, che oggi non esistono.** Tutto il motore
   (`grid.py`, `ops.py`) lavora su una griglia 2D di indici di palette.
   Non c'è un concetto di solido, mesh, vincolo geometrico o proiezione
   3D→2D da nessuna parte. Anche con un parser STEP perfetto in mano, non
   ci sarebbe dove appoggiare l'informazione estratta.

Il confronto onesto con SVG/DXF (§5.1) rende il divario evidente: lì i
parser sono semplici (path/circle/linea in un file di testo strutturato,
gestibili con poche centinaia di righe pure-Python) e le primitive di
destinazione (`LINE`, `CIRCLE`) **esistono già**. Per STEP mancano
entrambi i lati del ponte. Resta il candidato più interessante per il
*lungo termine* (punto 8 di §5, insieme alle scene 3D), non per il
prossimo incremento.

### 7.4 Musica: dove potrebbe avere senso, dove no

Distinzione netta, stesso principio di PNG-tecnico-vs-fotografico:

- **Audio campionato (MP3/WAV/FLAC di una registrazione reale)**: zero
  guadagno per definizione. Un campione audio è denso di micro-variazioni
  che i codec audio già comprimono sfruttando decenni di modelli
  percettivi (mascheramento uditivo, ecc.) — è la stessa categoria di
  JPEG/H.265 in tabella, "già ottimizzato, un secondo passaggio peggiora".
  Balzar non ha né l'obiettivo né gli strumenti per competere qui, e
  dichiararlo sarebbe l'esatto errore di onestà che il progetto vuole
  evitare.
- **Notazione simbolica (spartito, MIDI, pattern ritmici/melodici
  generativi)**: territorio potenzialmente valido, perché è già
  discreto e strutturato, non un segnale continuo. Un rullante ripetuto
  ogni 4 battute, un arpeggio con trasposizioni regolari, una sequenza
  MIDI con pattern ricorrenti: sono l'equivalente musicale del tiling e
  delle trasformazioni geometriche (SHIFT diventa trasposizione, LOOP
  diventa ripetizione di battute, un ipotetico `TRANSPOSE`/`SEQUENCE`
  sostituirebbe RECT/CIRCLE). Ma è **un dominio nuovo**, non un'estensione
  dell'encoder immagini: servirebbe uno stato (griglia note/tempo invece
  di griglia pixel) e operazioni proprie. Zero lavoro iniziato, nessuna
  garanzia che il guadagno sarebbe comparabile ai numeri visti su
  immagini/video — da trattare come ipotesi da testare, non da vendere
  con un moltiplicatore inventato.

## 8. Confronto quantitativo con lo stato dell'arte (regola del progetto)

Ogni volta che si decide una direzione, va misurato il guadagno concreto
contro l'alternativa reale — non solo "funziona", ma "quanto in meno, e
sta in un QR o no". Caso guida: `examples/etichetta_bom.bzr` (esploso +
distinta base, applicazione §6.1), numeri reali misurati in sessione:

| Rappresentazione | Byte | Sta in un QR (limite 2.953 B)? |
|---|---|---|
| RGB grezzo (640×520, non compresso) | 998.400 | no (339× oltre) |
| PNG dello stesso identico contenuto (encoder nostro, non ottimizzato) | 5.496 | **no** (1,9× oltre) |
| PNG ri-compresso (stima con encoder a filtri adattivi) | 4.617 | **no** (1,6× oltre) |
| ZIP del PNG | 4.969 | **no** (1,7× oltre — lo ZIP non trova altro da comprimere, il PNG è già DEFLATE) |
| **Payload balzar (`.bzp`)** | **559** | **sì**, con margine (usa solo il 19% della capacità) |

Il punto non è solo "559 è più piccolo di 5.496" (9,8× contro il PNG
equivalente): è che **il PNG della stessa identica immagine non entra in
un QR, il payload balzar sì, con margine per aggiungere altre righe di
BOM**. Questo è l'unico numero che conta per l'applicazione "etichetta
fisica": non il rapporto di compressione in astratto, ma se il contenuto
sta o non sta nel supporto fisico scelto.

Per un vero export PDF/CAD (SolidWorks, AutoCAD) dello stesso disegno +
BOM — font incorporati, overhead del formato, spesso un'anteprima raster
in pancia — l'ordine di grandezza tipico è 100KB–qualche MB anche per un
disegno semplice: **non è una misura fatta in sessione** (non abbiamo
generato un PDF reale per confronto), va trattata come stima qualitativa
nota nel settore, non come dato verificato — a differenza delle righe
sopra, che sono tutte misurate su file reali prodotti in questa sessione.

### Perché non è "ZIP più aggressivo" né "JPEG migliore"

- **ZIP/DEFLATE comprimono byte esistenti** cercando ripetizioni locali in
  ciò che già c'è. Il PNG sopra è già passato da un DEFLATE (`png.py`):
  ricomprimerlo con ZIP guadagna ~10% (5.496→4.969) perché non c'è molto
  altro da trovare — la tabella sopra lo mostra: lo ZIP non fa la
  differenza tra "entra" e "non entra" nel QR.
- **JPEG è peggio, non meglio, su questo contenuto**: è ottimizzato per
  gradienti fotografici (DCT + quantizzazione percettiva), non per bordi
  netti e testo — su un'etichetta con linee nette e caratteri a 5×7 pixel
  introduce artefatti di blocking proprio sui bordi delle lettere e in
  genere pesa più del PNG equivalente, non meno.
- **balzar non comprime il PNG**: non lo genera nemmeno come passo
  intermedio. Il payload da 559 byte non è "l'immagine compressa più
  aggressivamente" — è la lista di istruzioni (`CIRCLE cx=170 cy=150
  r=110`, `TEXT x=90 y=400 text="B-4471-A"`, ecc.) che, eseguita, produce
  i 998.400 byte di RGB. I pixel del cerchio o della lettera "Q" non sono
  mai stati salvati da nessuna parte per essere poi riletti: vengono
  calcolati al volo da `CIRCLE`/`TEXT` ogni volta che il payload viene
  aperto. È la differenza tra "un file audio compresso" e "uno spartito":
  lo spartito non contiene il suono, contiene le istruzioni per produrlo.

## 9. Comandi utili per riprendere il lavoro

```bash
python3 -m unittest discover -s tests        # 85 test (3 opzionali su qrcode/pyzbar), deve restare verde
python3 -m balzar gui                        # app desktop
python3 -m balzar encode-image foto.png -o f.bzp
python3 -m balzar encode-video anim.gif -o v.bzp
python3 -m balzar chunks v.bzp -o qr/ --qr       # immagine QR reale (1 o griglia)
python3 -m balzar scan qr/v_qr.png -o ricostruito.bzp --render out/
```

Ambiente di sviluppo: Python 3.11 di sistema **non ha Tk** (pacchetto
`python3.11-tk` non installabile qui per un blocco del proxy apt); la GUI è
stata sviluppata e testata con **python3.12**, che ha Tk 8.6 disponibile.
Pillow va installato su entrambe le versioni se si passa dall'una all'altra
(`pip install pillow` / `python3.12 -m pip install --break-system-packages pillow`).
Stesso discorso per `qrcode`/`pyzbar` (usati da `balzar/qr.py`, opzionali):
`pyzbar` richiede anche `libzbar0` di sistema (`apt-get install libzbar0`),
non solo il pacchetto pip.
