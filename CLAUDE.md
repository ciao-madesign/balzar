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
ambiente — onestamente non hardcodata a rischio di sbagliarla), **entità
`SPLINE` DXF** (curve NURBS, vedi sotto).

Non supportato — **saltato con il motivo esatto**, mai in silenzio (stesso
principio di `svg.py` ma best-effort invece di tutto-o-niente, perché qui
non c'è un secondo target di rendering dello stesso DSL da cui aspettarsi
un supporto completo, ma un formato esterno arbitrario): curve SVG
(`C`/`S`/`Q`/`T`/`A`), trasformazioni diverse da `translate`, archi DXF
(`ARC`/`ELLIPSE`), SPLINE definite solo da fit point senza punti di
controllo espliciti (variante rara), colori ACI fuori dalla tabella nota
(resi in grigio neutro, dichiarato in `skipped`).

**Curve SPLINE (DXF), aggiunte in una sessione successiva**: il DSL non
ha una primitiva curva, quindi una `SPLINE` viene approssimata con lo
stesso principio già usato per `LWPOLYLINE` — campionarla ed emettere
segmenti `LINE` connessi — invece di richiedere una nuova primitiva
nell'interprete. Serve però un vero valutatore di curve B-spline (non
solo "connetti i punti", quelli qui sono punti di controllo e nodi, non
punti sulla curva): implementato l'algoritmo di De Boor in coordinate
omogenee (funziona sia per B-spline normali sia per NURBS pesate) in
`_bspline_de_boor`/`_sample_bspline`, nessuna dipendenza nuova. Ogni
`SPLINE` è campionata a un numero **fisso** di punti (`SPLINE_SAMPLES =
32`, non adattivo alla curvatura) — una tolleranza dichiarata ed esplicita,
non una precisione nascosta; conta come **1 entità** in `element_count`
anche se diventa 32 segmenti `LINE`, stessa convenzione di `LWPOLYLINE`.
Varianti DXF non supportate: SPLINE definite solo da fit point (senza
punti di controllo/nodi espliciti, rara nei file esportati da CAD reali).

Verificato con un file reale fornito dall'utente durante la sessione (non
incluso nel repository per motivi di copyright — logo aquila/ali
Harley-Davidson): 382.000 B di DXF, **118 entità, tutte SPLINE** su un
solo layer — prima di questo lavoro sarebbe stato un fallimento totale
(0 entità convertibili). Con SPLINE supportata: 118/118 convertite, 0
saltate (a parte gli avvisi di colore ACI non in tabella), payload
32.172 B (a `SPLINE_SAMPLES=64`, vedi sotto). Punto di misura onesto e
utile: **né il sorgente né il payload entrano in un solo QR** (sorgente
330.991 B → 151 QR necessari; payload 32.172 B → 15 QR) — ma il rapporto
10,3× in meno byte (17,4× contro l'RGB equivalente) è la differenza reale
tra stampare/laminare 151 QR o 15. Nuovo esempio incluso nel repository
(soggetto generico, non coperto da copyright): `examples/curva_spline.dxf`
(2 onde SPLINE + testo, 0 saltati, payload 1.380 B, singolo QR).

**Fedeltà visiva, verificata sullo stesso file**: 32 campioni per SPLINE
lasciava sfaccettature visibili sui dettagli fini (bordi delle piume);
alzato a **64** dopo aver isolato che pesa quanto la mancanza di
anti-aliasing nel nostro `png.py` (Bresenham puro, nessuna sfumatura sui
bordi). Prova diretta: lo stesso output a 64 campioni, esportato come SVG
(`svg.py`) e renderizzato da un browser (anti-aliasing nativo, gratis),
è visivamente più pulito del PNG a 256 campioni — quasi tutta l'asprezza
percepita viene dal renderer raster proprio, non dalla densità di
campionamento. Conclusione onesta: **per contenuto ricco di curve, l'export
SVG è la resa fedele consigliata, il PNG resta esatto ma esteticamente più
grezzo** — nessun cambiamento al renderer PNG (richiederebbe ripensare il
modello a palette indicizzata per ammettere colori sfumati sui bordi, un
lavoro architetturale a parte, non fatto in questa sessione).

Bug reale trovato **grazie a questo test**, corretto nella stessa
sessione: quando *tutte* le entità di un file sono di un tipo non
supportato, `_parse_dxf` collezionava correttamente i motivi in
`skipped`, ma `ingest_dxf` sollevava un `VectorIngestError` generico
("nessuna entità convertibile trovata") **senza includere quei motivi**
— l'informazione più utile proprio nel caso di fallimento totale veniva
scartata. Corretto: il messaggio d'errore ora include un riepilogo
deduplicato dei motivi di scarto (es. "ARC: entità non supportata
(×45)").

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
**zero elementi saltati**, payload 230 B (SVG, 9 elementi) / 249 B (DXF,
6 entità), entrambi in un singolo QR con ampio margine. Il risultato SVG è
a sua volta ri-esportabile come SVG vettoriale reale via `svg.py` (usa solo
`CIRCLE`/`LINE`/`TEXT`), chiudendo il cerchio SVG→balzar→SVG senza mai
passare per un pixel.

Nota di correzione: `element_count` per DXF conta **entità sorgente**, non
istruzioni DSL emesse — una `LWPOLYLINE` chiusa a 4 punti è 1 entità ma
diventa 4 segmenti `LINE` (il rettangolo non ha un op dedicato per un
poligono arbitrario). Il primo tentativo contava le righe emesse, gonfiando
il numero (7 invece di 4 sull'esempio di test); corretto contando le
entità effettivamente processate in un contatore separato in `_parse_dxf`.

### 2.7 Sequenze multi-file ed esploso automatico (CAD)

`balzar/sequence.py` e `balzar/explode.py` — risposta diretta alla
richiesta di validare l'ingestione su multi-file e su esploso automatico.
Prerequisito: `vectorio.py` è stato ristrutturato separando il parsing
(`_parse_svg`/`_parse_dxf` → lista di `_Shape` in coordinate sorgente,
esposta anche come `parse_vector_file`) dalla trasformazione+emissione
(`_emit_shapes`), cosa che permette a più file di condividere **una sola**
trasformazione/palette invece che una a testa (altrimenti ogni file avrebbe
la propria scala e i pezzi non si allineerebbero tra un frame e l'altro).

**`encode_vector_sequence(paths, max_dim=800)`** — più file **dello stesso
formato** (solo `.svg` o solo `.dxf`, misto rifiutato esplicitamente) →
un payload multi-`FRAME`. Il delta tra step è un dedup testuale esatto:
una riga DSL già emessa in uno step precedente (match esatto) non viene
riemessa in quello successivo. Questo è **corretto solo per contenuto
puramente additivo** (pezzi che compaiono, mai che si spostano o
scompaiono) — esattamente il modello di `examples/sequenza_montaggio.bzr`,
qui applicato a file CAD reali invece che a un programma scritto a mano.
Misurato su `examples/sequenza_flangia_cad/` (3 file DXF: carcassa →
+flangia → +4 bulloni): 800×800, 3 frame, 9 istruzioni totali, **169 byte**
contro 5.760.000 byte di RGB grezzo equivalente (34.083×), zero elementi
saltati.

**`encode_raster_sequence(paths, max_dim=400)`** — più file immagine
indipendenti (non un GIF animato) forzati su **una** dimensione condivisa
(quella del primo file dopo lo scaling; i successivi vengono
ridimensionati con NEAREST se non coincidono) e passati a
`video.encode_video`, che fa il vero delta a livello di pixel. In pratica
"più foto separate" diventano lo stesso oggetto di un video con un frame
per foto. Misurato su 3 PNG sintetici 100×80 con un blocco rosso che si
sposta: 12 istruzioni, **166 byte** contro 72.000 byte RGB grezzo (434×),
lossless.

**`encode_independent(paths, max_dim=800)`** — terza modalità, aggiunta
in risposta diretta alla richiesta di poter trattare più file come un
**mucchio non organizzato** invece che come una sequenza/video: ogni file
è codificato **per conto suo** (dispatch per estensione, stessa logica di
`encode-vector`/`encode-image` chiamati uno alla volta), nessuna
trasformazione condivisa, nessun vincolo di formato — un batch può
mescolare liberamente `.svg`/`.dxf`/raster, cosa che le altre due funzioni
rifiutano esplicitamente. Restituisce una lista di `IndependentFileResult`
(uno per file, con `ok`/`error` propri) invece di un singolo payload
multi-frame. Differenza di comportamento deliberata rispetto alle altre
due: un file rotto **non fa fallire il batch intero** — viene registrato
come voce singola con `ok=False`, gli altri file proseguono. Questo è
esattamente il punto della modalità "indipendente": è un mucchio di file
scorrelati, non un tutto navigabile che deve restare coerente. Esposta
come `balzar encode-sequence ... --mode independent` in CLI (scrive un
`.bzp` per file, accanto al sorgente o nella directory data con `-o`) e
come toggle "Sequenza navigabile" / "File indipendenti" nel tab
"Sequenza" della demo web (`handle_encode_independent` in `webapi.py`,
`mode: "independent"` nel corpo della richiesta).

**`balzar/explode.py`: `explode_vector_file(path, steps=6, spacing=0.6,
max_dim=800)`** — un solo file CAD/SVG con **più di un layer/gruppo**
(layer DXF, codice gruppo 8 / `<g id>` SVG — la stessa chiave di
raggruppamento già presente su ogni `_Shape`) → payload con `steps+1`
frame: frame 0 assemblato, ogni frame successivo sposta ogni gruppo
radialmente verso l'esterno, lungo il vettore dal baricentro **del
disegno intero** al baricentro **del proprio gruppo** (un gruppo che si
trova già sul baricentro non si sposta: non c'è nulla da esplodere via da
se stesso). Un file con un solo layer viene **rifiutato con il motivo
esatto**, non silenziosamente processato come se non ci fosse nulla da
esplodere.

Punto tecnico non ovvio, diverso dal delta di `sequence.py`: qui **non si
riusa il dedup testuale**. Il canvas del motore è cumulativo (`FRAME` fa
uno snapshot, non pulisce mai nulla) — se un gruppo si sposta e la riga
DSL della sua vecchia posizione venisse saltata perché "già vista", la
vecchia posizione resterebbe visibile per sempre (un fantasma). La
correttezza richiede un repaint completo per frame: un `FILL` su una
`REGION` grande quanto l'intero canvas riporta tutto a sfondo, poi si
ridisegnano tutte le forme nella posizione corrente. Costa di più per
frame di un delta puro, **ma è l'unico modello corretto per contenuto che
si muove**, a differenza del contenuto puramente additivo di
`sequence.py`. La rotazione (2D o 3D) è esplicitamente fuori scope per
questo modulo — solo esplosione radiale in linea retta.

Misurato su `examples/flangia_esploso.dxf` (6 layer: carcassa, flangia
interna, 4 bulloni): 800×800, 7 frame (`--steps 6`), 57 istruzioni,
**303 byte**, entra in un singolo QR con ampio margine, 44.356× rispetto
all'RGB grezzo equivalente (13,44 MB). Verificato visivamente (render PNG
per frame): i bulloni si allontanano radialmente dal centro senza artefatti
di "fantasma" nelle posizioni precedenti e senza clipping ai bordi del
canvas anche nell'ultimo frame.

Comandi CLI: `balzar encode-sequence file1 file2 ... -o out.bzp
[--max-dim N]` (dispatch automatico vettoriale/raster in base
all'estensione), `balzar explode-vector file.dxf -o out.bzp [--steps N]
[--spacing N]`. Test: `tests/test_sequence.py` (8 test),
`tests/test_explode.py` (6 test).

### 2.8 App desktop (il prodotto)

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

### 2.9 Demo web (solo vetrina, non il prodotto)

`index.html` + `app.js` + `style.css` + sei funzioni serverless Vercel
(`api/encode.py`, `api/encode_vector.py`, `api/encode_video.py`,
`api/encode_sequence.py`, `api/qr.py`, `api/render.py`) +
`balzar/webapi.py` (logica condivisa con profili di limiti espliciti:
`VERCEL_LIMITS` vs `LOCAL_LIMITS`, quest'ultimo non ancora agganciato a
un vero deployment). Cinque tab nella pagina, ognuno con un badge
"Codifica"/"Consumo" esplicito in cima al pannello che ne dichiara lo
scopo (nessuna spiegazione implicita lasciata all'utente):

1. **"Comprimi immagine"** (il flusso originale, `api/encode.py`) — encoder
   raster, guarda solo il primo frame di un file multi-frame.
2. **"Vettoriale (SVG/DXF)"** (`api/encode_vector.py` + `handle_encode_vector`)
   — ingestione diretta via `vectorio.py`, nessuna rasterizzazione. L'SVG
   originale viene mostrato nel browser nativamente (`<img>` renderizza SVG
   senza bisogno del backend) accanto al risultato rigenerato da balzar;
   per DXF (che il browser non sa renderizzare) si mostra solo il
   rigenerato. Offre anche il download SVG (sempre disponibile: l'output
   di `vectorio.py` usa solo il sottoinsieme vettoriale-sicuro, mai
   rifiutato da `svg.py`).
3. **"Video (GIF animata)"** (`api/encode_video.py` + `handle_encode_video`)
   — a differenza del tab 1, guarda **tutti** i frame e usa il vero delta
   di `video.py`; una GIF con un solo frame viene rifiutata con un
   messaggio che rimanda al tab 1.
4. **"Sequenza (multi-file)"** (`api/encode_sequence.py` +
   `handle_encode_sequence`) — due modalità scelte con un toggle
   (`input[name=sequence-mode]`): **"Sequenza navigabile"** (default), 2+
   file in ordine scelto dall'utente (frecce ▲/▼ per riordinare, niente
   drag-and-drop per affidabilità) diventano un payload multi-frame,
   navigabile avanti/indietro con gli stessi controlli `◀ Indietro`/
   `Avanti ▶` della GUI desktop (dispatch automatico vettoriale — solo
   `.svg` o solo `.dxf`, mai misti — vs raster, stessa regola della CLI);
   **"File indipendenti"** (`mode: "independent"`,
   `handle_encode_independent`), aggiunta su richiesta esplicita per
   trattare più file come un mucchio non organizzato invece che come una
   sequenza — ogni file diventa una card separata con la propria
   anteprima/statistiche/download/QR, nessun vincolo di formato (un batch
   può mescolare `.svg`+`.dxf`+raster), un file rotto non blocca gli
   altri (mostrato come card d'errore isolata, non un 400 per l'intera
   richiesta).
5. **"Apri programma (.bzr/.bzp)"** (`api/render.py` + `handle_render`) —
   chiude il caso d'uso "ho scaricato un `.bzr` da qui e non ho un
   terminale": carica il file, viene decodificato e rigenerato, scarichi
   PNG (e GIF se multi-frame, e SVG se il programma è vettoriale — §2.5),
   e — novità di questa sessione — anche il payload (`.bzp`) stesso,
   ri-codificato canonicamente dal programma decodificato così il bottone
   "genera QR" (vedi sotto) funziona anche quando l'upload originale era
   un `.bzr` testuale, non un `.bzp` già pronto.

**Generatore QR** (`api/qr.py` + `handle_qr`), disponibile su tutti e
cinque i tab dove esiste un payload: riusa `balzar/qr.py` esattamente
com'è (singolo codice o griglia auto-dimensionata). A differenza della
*lettura* di un QR (`pyzbar`/`libzbar0`, nativa, mai esposta sul web
demo — serve un ambiente con quella libreria di sistema), la
*generazione* usa solo `qrcode`, puro Python + Pillow: nessuna nuova
dipendenza di sistema, sicuro da aggiungere a `requirements.txt` per
Vercel. Verificato non solo visivamente ma con un vero round-trip ZBar
in sessione: screenshot del QR generato dalla pagina → `pyzbar.decode`
→ `assemble_chunks`/`decode_payload` → programma bit-identico
all'originale caricato.

Tutti e cinque i tab (più il generatore QR) verificati end-to-end in
sessione (Playwright contro un server locale che espone le stesse
funzioni `handle_*` — vedi nota sotto sul perché non contro il deploy
reale): upload → risultato coerente con gli stessi numeri misurati dalla
CLI sugli stessi file (es. la sequenza CAD a 3 step: 169 B, 34.083×
identico a `sequenza_flangia_cad/`).

**Bug reale trovato e corretto durante la verifica**: la lista file del
tab "Sequenza" si accumula (permette di aggiungere file in più batch),
ma non si svuotava mai da sola — codificare una prima sequenza e poi
caricarne una seconda di tipo diverso (es. DXF poi PNG) mischiava i file
vecchi con quelli nuovi, il dispatch vettoriale/raster sceglieva raster
per la presenza di estensioni miste, e il tentativo di aprire un `.dxf`
con Pillow falliva con un'eccezione non gestita (500 invece di un errore
onesto). Fix in due parti: aggiunto un bottone "Svuota elenco" esplicito
in `app.js`, e resa `handle_encode_sequence` robusta anche lato server
(cattura `VectorIngestError`/`OSError` invece di lasciarli propagare come
500) — stesso principio applicato a `handle_encode_video` per un file non
immagine. Nessuna delle due funzioni nuove crasha più su input scorretto,
entrambe rispondono 400 con un messaggio chiaro.

**Due bug reali trovati e corretti in una sessione di irrobustimento
mirata a "perfezionare i flussi di compressione e ri-espansione"**:
1. **Ogni `base64.b64decode()` in `webapi.py` era sguarnito** (7 punti,
   su tutti e sei gli handler): un base64 malformato (padding errato,
   caso limite ma reale — upload troncato, bug del client) faceva
   crashare con un 500 non gestito invece del 400 onesto che il resto
   del codice applica ovunque. Riprodotto e verificato prima del fix:
   `handle_render({"data": "not-valid-base64!!!"}, ...)` sollevava
   `binascii.Error: Incorrect padding` fino in cima. Fix: helper
   condiviso `_b64decode()` che cattura l'errore e lo trasforma in un
   400 con messaggio chiaro, usato da tutti e sette i punti di chiamata.
   In modalità "file indipendenti" il fix è più di una semplice guardia:
   il decode avviene ora *prima* di scrivere il file su disco e *prima*
   di chiamare `encode_independent`, con un file dal base64 corrotto
   registrato come proprio item fallito (stesso principio di isolamento
   guasto già documentato sopra) invece di far fallire l'intera
   richiesta — altrimenti un solo file corrotto in un batch avrebbe
   vanificato esattamente la garanzia di isolamento che questa modalità
   promette.
2. **`handle_encode` (tab 1, "Comprimi immagine" — il flusso più
   vecchio della demo) non catturava affatto gli errori di decodifica
   immagine**, a differenza del suo gemello `handle_encode_video` che
   già cattura `OSError`. Un file non-immagine caricato su quel tab
   crashava con `PIL.UnidentifiedImageError` (sottoclasse di `OSError`)
   non gestita. Trovato scrivendo un test di regressione per
   `handle_encode` (che non aveva **nessuna** copertura in
   `test_webapi.py` prima di questa sessione, né lui né `handle_render`)
   e osservandolo fallire subito. Fix: stesso pattern `try/except
   OSError` già usato da `handle_encode_video`.

Test aggiunti: `TestHandleEncode` e `TestHandleRender` (prima assenti
del tutto), più un test di base64 malformato per ciascuno dei sei
handler e un test di ordine/isolamento su un batch "indipendente" da 3
file con quello centrale corrotto (`tests/test_webapi.py`, ora 155 test
totali).

**Audit esteso a tutta la superficie (CLI/GUI/qr.py), stessa sessione,
per "finire tutti gli audit" richiesto esplicitamente**: verificato ogni
altro punto d'ingresso dello stesso tipo di errore (crash non gestito
invece di messaggio onesto). Risultato onesto, non uniforme — un solo
altro problema reale, di copertura non di codice:
- **`balzar/cli.py` (574 righe, l'interfaccia principale del progetto)
  non aveva `tests/test_cli.py` — zero copertura automatica**, solo
  verifica manuale per sessione. Il codice stesso si è rivelato già
  robusto: `main()` cattura un singolo `except (ValueError, SyntaxError,
  OSError)` attorno a `args.func(args)`, e **tutte** le eccezioni
  custom del progetto (`PayloadError`, `VectorIngestError`,
  `SequenceError`, `ExplodeError`) sono già sottoclassi di `ValueError`
  — quindi ogni comando arriva già a un `errore: ...` pulito e
  `exit code 1`, mai un traceback grezzo, senza bisogno di alcun fix.
  Aggiunto `tests/test_cli.py` (20 test): round-trip di ognuno degli 11
  sottocomandi (`render`/`encode`/`encode-image`/`encode-vector`/
  `encode-video`/`encode-sequence` nei due modi/`explode-vector`/
  `decode`/`info`/`chunks`+`--qr`/`scan`/`assemble`), più verifica
  esplicita che input mancante/non valido produca `errore:` e mai
  `Traceback` nello stderr.
- **`balzar/gui.py`**: già corretto. I due worker thread (`_worker`,
  `_scan_worker`) catturano `Exception` in modo ampio e deliberato e
  instradano il messaggio a `messagebox.showerror` via una coda
  thread-safe — nessun crash silenzioso, nessun hang. Non modificato.
- **`balzar/qr.py`**: già corretto, nessuna eccezione non gestita nei
  suoi 95 righe; gli errori che può sollevare (`ValueError`/
  `PayloadError`/eccezioni PIL/pyzbar) sono già intercettati a monte da
  CLI (`main()`) o GUI (worker `except Exception`).

Con questo, i quattro livelli della pila (motore -> encoder -> CLI/GUI
-> demo web) hanno tutti una copertura di test esplicita sul
comportamento in caso di errore, non solo sul percorso di successo —
non solo "funziona", ma "fallisce onestamente quando deve fallire".
Test totali: 175.

Vercel impone limiti reali (~3,3MB upload utile, ~4,5MB risposta, timeout)
gestiti esplicitamente con messaggi chiari invece di errori criptici —
vedi `MAX_PREVIEW_DIM`, `MAX_PROGRAM_CHARS`, `MAX_PAYLOAD_B64_BYTES` in
`balzar/webapi.py`. **Questi limiti non esistono nell'app desktop**, che
è il prodotto vero.

**Nota sull'ambiente di sviluppo di questa sessione**: `balzar-eight.vercel.app`
non è raggiungibile da questo sandbox (proxy di rete con policy
organizzativa che nega l'host, confermato dallo stato del proxy — non un
problema del sito). La verifica end-to-end sopra è quindi contro un
server locale (`http.server` + le stesse funzioni `handle_encode*` di
`webapi.py`, non contro `api/*.py`/Vercel), non contro il deploy reale —
stessa limitazione già nota per `VERCEL_LIMITS` (criticità §4.6): il
deploy reale va controllato da un ambiente con accesso di rete.

**`come-funziona.html`**: pagina statica separata (nessuna funzione
serverless, nessun JS oltre l'HTML), linkata dall'header di `index.html`.
Spiega il modello (seed+programma→interprete→pixel, l'analogia
spartito/registrazione), il limite di Kolmogorov, e una tabella di
confronto per tipo di contenuto (icone/pattern, CAD/vettoriale, sequenze
multi-step, video/animazioni UI, screenshot, foto, audio, dati
strutturati) contro il sistema che si userebbe oggi — con i numeri già
misurati altrove in questo documento (§3, §8), non nuovi né stimati.
Dichiara onestamente le tre righe a guadagno nullo (foto, audio, dati
strutturati non ancora implementati) invece di ometterle.

### 2.10 CLI

`balzar render|encode|encode-image|encode-video|decode|info|chunks|scan|assemble|gui`
— vedi `balzar/cli.py` per l'elenco completo con esempi in `README.md`.

### 2.11 Test

194 test, tutti verdi (`python3 -m unittest discover -s tests`):
`test_determinism.py`, `test_ops.py`, `test_expansion.py`, `test_encoder.py`,
`test_qr.py` (skippato automaticamente se `qrcode`/`pyzbar` non sono
installati — dipendenze opzionali, non nel motore core),
`test_video.py`, `test_svg.py`, `test_vectorio.py`, `test_sequence.py`,
`test_explode.py`, `test_webapi.py`, `test_png.py`, `test_cli.py`,
`test_scene3d.py` (parser 3DXML, formato binario `BZM1`, export glTF —
vedi §9.5). Copertura: round-trip
bit-identico, corruzione rilevata,
correttezza delle singole operazioni, fattori di espansione sugli esempi,
encoder lossless su contenuto strutturato e onesto su rumore, video delta
vs flipbook, capitoli in ordine sparso/mancanti/corrotti, sequenze
vettoriali/raster multi-file, esploso automatico per layer, curve SPLINE
DXF (campionamento B-spline/NURBS, entità con nodi/gradi incoerenti o
solo fit-point scartate senza crash), tutti e cinque i flussi della demo
web incluso il tab 1 "Comprimi immagine" e il tab 5 "Apri programma"
(prima privi di copertura in `test_webapi.py` — vedi il bug reale
trovato proprio scrivendola, sopra) — successo, errori onesti invece di
crash (incluso base64 malformato su tutti e sei gli handler), troncamento
in base ai limiti — più il generatore QR (incluso un round-trip reale
via ZBar in `test_webapi.py`, skippato se `pyzbar` non è installato), e
la modalità "file indipendenti" (formati misti, isolamento del
fallimento per singolo file incluso un base64 corrotto, con verifica che
l'ordine dei file superstiti nella risposta resti quello originale, sia
in `sequence.py` che nel suo dispatch in `webapi.py`), più il writer PNG
con filtri adattivi (round-trip pixel-esatto via Pillow e guardia
esplicita di non-regressione contro il vecchio writer solo-None,
`test_png.py`).

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
| Screenshot UI sintetico anti-aliased, 279 colori esatti (encoder auto, median-cut) | 22.996 B, errore medio colore 0.0 | 442.368 B | 19,2× (256 scatole per 279 colori reali, quasi esatta) |
| `examples/flangia_sorgente.svg` (ingestione vettoriale, 0 elementi saltati) | 230 B | 800×800 | in un solo QR, margine ampio |
| `examples/flangia_sorgente.dxf` (stesso soggetto, ingestione DXF, 0 saltati) | 249 B | 800×800 | in un solo QR, margine ampio |
| `examples/sequenza_flangia_cad/` (sequenza vettoriale, 3 file DXF: carcassa→+flangia→+bulloni) | 169 B | 800×800×3 frame = 5,76 MB RGB | 34.083× |
| 3 PNG sintetici 100×80 indipendenti (sequenza raster, encode_raster_sequence) | 166 B | 72.000 B RGB | 434× |
| `examples/flangia_esploso.dxf` (esploso automatico, 6 layer, 6 step) | 303 B | 800×800×7 frame = 13,44 MB RGB | 44.356×, un solo QR |
| `examples/curva_spline.dxf` (curve SPLINE reali, 2 onde + testo, 0 saltati) | 1.380 B | 753×800 | in un solo QR, margine ampio |
| Logo reale multi-spline (118 entità SPLINE, file di terzi non incluso per copyright) | 32.172 B | 800×233 | 10,3× vs DXF grezzo (330.991 B), 17,4× vs RGB — **né sorgente né payload entrano in un solo QR** (151 QR vs 15 QR necessari: il numero che conta davvero qui) |

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
2. **Quantizzazione lossy oltre 256 colori — ora un vero quantizzatore
   percettivo (median-cut), non più arrotondamento a griglia fissa.**
   Prima passava per passi di arrotondamento crescenti (2,4,8,...,64 per
   canale, una griglia uniforme sull'intero spazio colore); ora
   `_median_cut_quantize` in `encoder.py` divide lo spazio colore in
   ≤256 "scatole" tagliando ripetutamente quella con il range più ampio
   (pesato per numero di pixel) lungo il canale più largo, poi rappresenta
   ogni scatola con la media pesata dei colori che contiene — si adatta
   alla distribuzione reale invece di imporre una griglia fissa. Caso
   reale misurato in sessione (screenshot sintetico con icone
   anti-aliased, ombra sfumata, sfondo a puntini, 279 colori esatti):
   errore medio colore **0.0** (256 scatole per 279 colori reali, quasi
   tutti isolati) — il vecchio sistema a griglia fissa non poteva adattarsi
   così alla distribuzione reale. Il campo `EncodeResult.color_step`
   (l'ampiezza del passo di arrotondamento) è stato sostituito da
   `mean_color_error` (distanza RGB media per pixel introdotta, 0.0 se
   esatta) — una metrica di fedeltà reale, non un parametro interno
   dell'algoritmo precedente. **Criticità di performance trovata e
   corretta durante l'implementazione**: il median-cut richiede ordinare
   ripetutamente le "scatole" da tagliare, e su un'immagine ad alta
   entropia (rumore, foto) il numero di colori distinti può arrivare a
   centinaia di migliaia — misurato 26 secondi su un rumore 400×400 prima
   della correzione. Fix: sopra 4.096 colori distinti (`_pre_bucket`),
   i colori vengono raggruppati con lo stesso raddoppio di passo usato
   dal vecchio sistema **solo per limitare l'input al median-cut**, non
   come quantizzazione finale — tocca solo contenuto a bassa struttura
   (foto/rumore, che non guadagna comunque nulla), il caso reale
   (poche centinaia/migliaia di sfumature da anti-aliasing) non lo
   raggiunge mai. Con la correzione, 800×800 di rumore puro passa da
   tempo impraticabile a **~30 secondi** (ancora lento ma completabile,
   coerente con l'essere un caso a guadagno nullo dichiarato, non un
   caso d'uso reale da ottimizzare oltre). Stesso quantizzatore riusato
   in `video.py` (`_quantize_frames`), che aveva la stessa vecchia
   posterizzazione fissa 3-3-2 per il fallback lossy multi-frame —
   `VideoEncodeResult` guadagna lo stesso campo `mean_color_error`.
3. **`png.py` ora usa filtri di scanline adattivi (Sub/Up/Average/Paeth),
   non solo None.** Per ogni riga si sceglie il filtro che minimizza la
   somma dei valori assoluti con segno (l'euristica MSAD standard degli
   encoder PNG di riferimento). **Non basta da sola**: misurato in
   sessione che l'euristica per-riga, presa da sola, **peggiora** il
   contenuto tipico di balzar — `examples/pattern_tile.bzr` (1024×1024,
   righe ripetute identiche) passava da 30.501 B (solo None) a 43.035 B
   (+41%) perché filtrare rompe l'identità di byte riga-su-riga che
   DEFLATE stava sfruttando per trovare match lunghissimi. Fix: `png_bytes`
   ora comprime **entrambe** le varianti (tutta None, e adattiva per riga)
   e tiene quella più piccola — mai peggio del vecchio writer per
   costruzione, con guadagno reale dove i filtri aiutano davvero
   (contenuto con variazione liscia pixel-su-pixel: un gradiente
   sintetico 256×256 passa da 186.695 B a 575 B, 325× più piccolo).
   Numeri reali misurati sul contenuto che balzar genera per davvero:
   `pattern_tile.bzr` 30.501→30.501 B (0%, vince None), `schema_tecnico`
   800×600 10.062→9.951 B (−1,1%), `etichetta_bom.bzr` 640×520
   5.496→5.496 B (0%, vince None) — **il guadagno stimato in precedenza
   (~25-30%) non si materializza sul contenuto reale di balzar**, fatto
   quasi tutto di rettangoli/testo a bordi netti dove il filtro None +
   ripetizione di righe è già quasi ottimale per DEFLATE; il guadagno
   vero è sui casi limite (gradienti, frattali, contenuto fotografico),
   non sul caso d'uso principale. Costo: `png_bytes` ora comprime due
   volte invece di una (~1-2,5s invece di ~0.01-0.1s sulle dimensioni
   sopra) — accettabile, nessun timeout su CLI/GUI/desktop. Test:
   `tests/test_png.py` (round-trip pixel-esatto via Pillow, guardia di
   non-regressione esplicita sul caso che ha regredito).
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
   effettivamente usato. **Confermato di nuovo in questa sessione**:
   `balzar-eight.vercel.app` non è raggiungibile dall'ambiente di sviluppo
   usato (policy di rete organizzativa, non un problema del sito) — ogni
   verifica end-to-end della demo web resta contro un server locale
   equivalente finché qualcuno con accesso di rete non controlla il
   deploy reale dopo il push.
7. **Limite architetturale di fondo, non un bug**: qualunque incremento
   dell'encoder resta vincolato alla complessità di Kolmogorov del
   contenuto. Non esiste un encoder che comprima bene contenuto genuinamente
   casuale — non è un obiettivo raggiungibile, è escluso per definizione.
8. **Nessun round-trip verso DXF**: `vectorio.py` ingerisce DXF ma non
   esiste un writer che rigeneri un `.dxf` dal payload — la ricostruzione
   di un DXF ingerito produce solo PNG/SVG (§2.6), mai lo stesso formato
   dell'originale. Segnalato esplicitamente dall'utente come lavoro da
   fare **quando si sarà pronti**, non ora — vedi Sviluppi §5 punto 12.
   Stesso discorso, meno prioritario perché fuori dall'obiettivo dichiarato
   del progetto, per JPEG (l'encoder raster produce sempre PNG in uscita).

## 5. Sviluppi possibili (ordinati per valore/sforzo stimato)

1. ~~Ingestione diretta di formati vettoriali (SVG/DXF)~~ — **fatto**
   (`balzar/vectorio.py`, comando `balzar encode-vector`): vedi §2.6.
2. ~~Comando `balzar scan` + generazione QR reale~~ — **fatto** (`balzar/qr.py`,
   `balzar chunks --qr`, `balzar scan`, pulsanti GUI): vedi §2.4.
2b. ~~Ingestione multi-file (sequenze CAD/immagini) ed esploso automatico
   per layer~~ — **fatto** (`balzar/sequence.py`, `balzar/explode.py`,
   comandi `balzar encode-sequence`/`balzar explode-vector`): vedi §2.7.
   La **rotazione** (2D o 3D) resta esplicitamente rimandata — l'esploso
   automatico oggi è solo traslazione radiale, per scelta discussa in
   sessione, non per limite tecnico non affrontato.
2c. ~~Demo web: tab vettoriale/video/sequenza~~ — **fatto** (`api/encode_vector.py`,
   `api/encode_video.py`, `api/encode_sequence.py`, `handle_encode_vector`/
   `handle_encode_video`/`handle_encode_sequence` in `webapi.py`): vedi
   §2.9. Decisione esplicita di sessione: **prima chiudere il ciclo
   encoding→QR→demo web sui formati già supportati (PNG/SVG/DXF)**,
   rimandando STEP e un encoder per XML/JSON (proposti nella stessa
   discussione) a una sessione di scoping separata — vedi §7.1/§7.3 per
   perché STEP in particolare non è "il prossimo incremento facile"
   (serve un parser EXPRESS *e* primitive 3D nel DSL, nessuna delle due
   esiste oggi).
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
6. ~~Filtri PNG adattivi in `png.py`~~ — **fatto** (Sub/Up/Average/Paeth
   con euristica MSAD + confronto contro None, mai peggio del vecchio
   writer): vedi criticità §4.3.
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
10. ~~Quantizzatore percettivo migliore per il fallback lossy~~ — **fatto**
    (median-cut, `_median_cut_quantize` in `encoder.py`): vedi criticità §4.2.
11. **Encoder per dati strutturati non-immagine** (JSON/XML ripetitivi):
    problema diverso dalla compressione di immagini — "template + diff dei
    parametri" invece di "rettangoli di pixel". Concettualmente vicino al
    modello LOOP+espressioni del DSL, ma richiederebbe un encoder
    interamente nuovo, non un'estensione di `encoder.py`. Speculativo,
    nessun lavoro iniziato. Esplicitamente rimandato in una sessione
    recente insieme a STEP (§7.1/§7.3), a favore di chiudere prima i
    flussi sui formati già supportati.
12. **Round-trip completo verso DXF** (e, minore, verso JPEG): oggi
    ricostruire un DXF ingerito produce solo PNG/SVG, mai un `.dxf`
    rigenerato — non esiste un writer DXF. Segnalato esplicitamente
    dall'utente come lavoro utile ma non prioritario ora ("quando saremo
    pronti") — vedi criticità §4.8. Servirebbe un serializzatore delle
    `_Shape` di `vectorio.py` (già strutturate per kind/geom/layer) nel
    formato a coppie codice/valore DXF — probabilmente il pezzo più
    semplice di questa lista, perché il modello dati esiste già.

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
| PNG dello stesso identico contenuto (`balzar.png`, **ora** con filtri adattivi, §4.3) | 5.496 | **no** (1,9× oltre) |
| ZIP del PNG | 4.969 | **no** (1,7× oltre — lo ZIP non trova altro da comprimere, il PNG è già DEFLATE) |
| **Payload balzar (`.bzp`)** | **559** | **sì**, con margine (usa solo il 19% della capacità) |

Riga aggiornata dopo l'implementazione reale dei filtri adattivi
(§4.3): il vecchio confronto aveva una riga "PNG ri-compresso (stima con
encoder a filtri adattivi) — 4.617 B", una stima mai verificata. Con
`balzar.png` che ora prova davvero Sub/Up/Average/Paeth e sceglie il più
piccolo, il numero reale misurato su questa immagine è **identico**
(5.496 B): per questo contenuto specifico (rettangoli/cerchi/testo a
bordi netti) il filtro None vince comunque, la stima era ottimistica.
Non cambia la conclusione dell'applicazione (il PNG non entra in un QR
in ogni caso), ma è il numero vero, non un'ipotesi.

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

## 9. 3D parametrico — prima versione funzionante

Estensione del progetto per la codifica/decodifica di file 3D
parametrici pesanti (assiemi CAD), con lo stesso principio del resto di
balzar (deduplicazione strutturale + descrizione generativa) e lo stesso
supporto fisico QR già esistente. §9.1-9.3 sono il risultato di
un'analisi approfondita su file reali forniti dall'utente in sessione —
non teoria, misure vere. §9.4-9.6 documentano la prima versione
implementata, costruita esattamente sulle decisioni prese in quello
scoping: gerarchia/nomi dei sotto-assiemi preservati (non appiattiti),
formato payload binario dedicato (non un'estensione del DSL testuale
2D), visualizzazione delegata a `model-viewer`/glTF invece di un
motore di rendering 3D scritto da zero.

### 9.1 Perché non STEP, non `.smg` — il formato giusto è 3DXML

Analizzato un file `.smg` reale (67.000 KB di STEP originale, esportato
come `.smg` da SOLIDWORKS Composer/Seemage): contenitore ZIP con prefisso
"SMG", XML dell'assembly (`product.smgXml`) + geometria tassellata in un
blob binario gzippato proprietario (`product.smgGeom`, float32 grezzi,
comprime solo ~2,4× con deflate perché è già binario denso). Trovato
845 posizionamenti di parti ma solo 143 geometrie uniche (`IdentGeom`
condiviso) — conferma che l'instancing è già presente nel formato
sorgente, ma il blob geometrico è binario proprietario da reverse-
engineerare.

Confrontato con lo stesso assembly esportato in **3DXML** (formato
Dassault pubblicato, non proprietario-binario): nettamente superiore per
i nostri scopi —
- schema documentato, XML puro (anche la geometria: `<Positions>`/
  `<Normals>`/`<Faces strips="...">` sono testo ASCII, non binario —
  nessun reverse-engineering necessario, un parser XML + `float()` basta;
- **geometria esternalizzata per forma unica** in file `.3DRep` separati,
  referenziati per nome (`associatedFile="urn:3DXML:<hash>.3DRep"`) — la
  deduplicazione è già la struttura del formato, non va rilevata a
  posteriori;
- albero annidato vero (`Reference3D`/`Instance3D` con
  `IsAggregatedBy`/`IsInstanceOf` + `RelativeMatrix`, un trasformo affine
  3×4 completo, gestisce anche gli specchiati — trovato un determinante
  −1 reale nel file) — un sotto-assieme ripetuto moltiplica automaticamente
  tutto ciò che contiene, esattamente come una chiamata a funzione/loop nel
  codice, non un elenco piatto da enumerare.

**Verificato dall'utente esplicitamente**: il file di test (staffe, viti,
barre, lamiere, poche superfici curve — un rack di acciaio) è
rappresentativo della tipologia di forme reale con cui si lavorerebbe
(con più oggetti del normale, ma la "forma" delle geometrie è quella
giusta), non un caso peggiore scelto per prudenza.

### 9.2 Numeri reali misurati sul file di test (non stimati)

Percorrendo davvero l'albero 3862 `Instance3D` fino alle foglie con
geometria:

| Metrica | Valore |
|---|---|
| Posizionamenti-foglia (con moltiplicità da annidamento) | 1.623 |
| Geometrie uniche (`*.3DRep`) | 78 |
| Rapporto di instancing | ~20,8× |
| Colori distinti (uno per forma, non per vertice) | 3 |
| Vertici totali (nelle 78 forme uniche) | 75.752 |
| Voci di indice nelle strisce di triangoli | 107.041 |
| Trasformi-foglia allineati agli assi (rotazione solo 0/±1) | 1.623/1.623 (100%) |

**Guadagno di deduplicazione** (pesato per uso reale di ogni forma, non
una media semplice): flattening ingenuo (una copia di geometria per
posizionamento, quello che daresti per scontato con un OBJ/STL unico)
130.711.307 B raw / 21.620.221 B compressi vs deduplicazione reale
(78 forme uniche + trasformi) 4.905.126 B raw / 672.722 B compressi —
**26,6× raw, 32,1× compresso**, prima di qualunque ricodifica binaria.

**Ricodifica binaria** (posizioni float32 senza normali per vertice —
si ricalcolano come flat-shading dalla faccia a rendering, scelta
dichiarata non nascosta — indici uint16, header per forma):
geometria 438.830 B + istanze 908 B = **439.738 B** dopo deflate.
Con quantizzazione int16 per-forma (~0,03 mm di precisione, dentro
tolleranza CAD tipica): geometria 389.923 B + istanze 908 B =
**390.831 B** — guadagno reale ma modesto dalla quantizzazione (~11%,
deflate su float32 IEEE-754 lascia poco sul tavolo).

A 2.194 B/QR (capacità già usata da `balzar/qr.py`): **178-201 QR code**
a seconda della variante.

### 9.3 Benchmark reali: decodifica QR e pipeline software

Generata una griglia 4×4 vera (16 QR) con `balzar/qr.py` e cronometrata
la decodifica con la stessa libreria che balzar già usa (pyzbar/ZBar), a
diverse risoluzioni — risultato controintuitivo: **risoluzione massima
non è né più veloce né più affidabile**.

| Larghezza immagine | QR decodificati | Tempo |
|---|---|---|
| 4704 px (piena, default `balzar/qr.py`) | 16/16 | 4,2 s (**più lento** del budget EPD ipotizzato) |
| 1700–2400 px | 16/16 | 0,26–0,48 s |
| ≤1600 px | 14/16 o 0/16 (fallisce) | — |

Oltre una soglia, più pixel aggiungono solo tempo di scansione ZBar senza
guadagno di affidabilità — la griglia va renderizzata nella fascia
1700-2400px, non alla risoluzione più alta possibile "per sicurezza".

Pipeline software misurata sul payload quantizzato reale (178 capitoli):
`chunk_payload` 0,29 ms, `assemble_chunks` 0,46 ms, `zlib.decompress`
3,92 ms, parsing delle 78 forme da struct binari 5,35 ms — **tutte e
quattro insieme sotto i 10 ms**, rumore statistico rispetto alla
scansione.

**Tempo totale stimato** (scansione di 15 frame a griglia 4×4 con un
supporto LCD economico invece di EPD — l'idea di un display che
riproduce una sequenza di QR nel tempo, non solo nello spazio di una
griglia singola, resta valida e discussa in sessione — + decodifica +
assemblaggio + decompressione + parsing, **esclusa** l'acquisizione
fisica reale — motion/focus/fotocamera non misurabili in questo
ambiente): **~4-7 secondi**, di cui il 99%+ speso nella sola
scansione+decodifica dei 15 frame. Il render finale (~2,94 milioni di
triangoli-istanza da disegnare, contando ogni posizionamento non solo le
78 forme uniche) **non è misurabile** — balzar non ha ancora un motore
di rendering 3D — ma qualunque GPU degli ultimi 10 anni gestisce quel
carico in tempo reale (aspettativa basata su capacità hardware tipiche,
dichiarata esplicitamente come stima e non come misura, a differenza dei
numeri sopra).

**Obiettivo di prodotto fissato in sessione**: tempo totale tra
scansione e visualizzazione del render **sotto i 6-7 secondi**. Se il
numero reale (una volta costruita la pipeline vera) lo sfora, la prima
leva di ottimizzazione è la **decodifica in pipeline invece che
sequenziale** (decodificare il frame N mentre il display mostra già il
frame N+1, invece di scansionare tutti i 15 frame e poi decodificarli in
serie) — non prima ottimizzazione tentata finché non risulta necessaria.

### 9.4 Decisioni prese: input 3DXML, output binario dedicato, vista via glTF

Tre decisioni esplicite prese in sessione, prima di scrivere codice:

1. **Gerarchia preservata**: `parse_3dxml` mantiene l'albero
   `Reference3D`/`Instance3D` con nomi e raggruppamenti (non appiattisce
   a una lista di posizionamenti-foglia in coordinate mondo). È anche
   un DAG, non un albero — un `Reference3D` (es. un sotto-assieme
   ripetuto) viene interpretato **una sola volta** indipendentemente da
   quanti `Instance3D` lo bersagliano, perché è lì che vive il grosso
   della compressione (~20,8× misurato in §9.2): appiattirlo a monte
   avrebbe buttato via esattamente quel guadagno.
2. **Formato payload binario dedicato** (`BZM1`, non un'estensione del
   DSL testuale 2D): confrontato empiricamente contro l'alternativa
   testuale prima di scegliere — su dati reali, DSL-ASCII + deflate dà
   465.474 B contro 438.830 B (float32 diretto) / 389.923 B (quantizzato
   int16): **differenza 6-19%, non un ordine di grandezza** come nel 2D.
   La scelta è stata quindi guidata dall'architettura (nessun rischio
   per il parser/interprete 2D esistente, self-check numerico invece di
   render-e-confronta-pixel) non dalla dimensione, che è quasi un
   pareggio. Costo esplicito accettato: un payload `BZM1` non è testo
   ispezionabile a mano come un `.bzr` — coerente con la filosofia del
   progetto solo in parte, dichiarato apertamente come compromesso.
3. **Visualizzazione delegata, non un rasterizzatore 3D nostro**: stessa
   filosofia di `svg.py` per il 2D. Confrontati tre progetti reali:
   `alonrubintec/3DViewer` scartato subito (nessuna licenza dichiarata,
   8 commit totali, abbandonato dal 2023); `Online3DViewer` (MIT, molto
   maturo, supporta STEP/IFC/decine di formati) tenuto da parte per un
   uso futuro laterale (mostrare il file *sorgente* non convertito,
   come già fa il tab "Vettoriale" con l'SVG originale); **`model-viewer`
   di Google** (Apache 2.0, web component, client-side puro, attivamente
   mantenuto) scelto come target — prende solo glTF/GLB, che però ha
   già nativamente lo stesso modello dati di 3DXML (nodi con nome, mesh
   riferite per istanza, gerarchia) — un piccolo esportatore basta,
   nessun motore di rendering da scrivere.

### 9.5 Cosa esiste ora: `balzar/scene3d.py` + `balzar/gltf.py`

**`balzar/scene3d.py`** — `parse_3dxml` (percorre `Manifest.xml` →
documento radice → albero `Reference3D`/`Instance3D`/`ReferenceRep` →
un `Scene3D` con `Shape` uniche + `Reference` con nomi/figli/trasformi),
formato binario `BZM1` (`encode_payload`/`decode_payload`, stesso schema
di `BZR1`: magic+versione+lunghezza+CRC32+deflate del corpo binario),
self-check obbligatorio (`encode_3dxml_file` decodifica il payload appena
prodotto e lo confronta per uguaglianza esatta contro la scena — vedi
sotto per quale scena esattamente, dopo le ottimizzazioni).

**Ottimizzazioni di dimensione applicate** (le stesse già misurate nello
scoping §9.2, ora nel codice invece che solo prototipate): vertici
quantizzati int16 per-forma (bounding box propria di ogni forma come
scala/offset — più precisione dei 16 bit su una parte piccola che una
scala unica condivisa su tutto l'assieme), indici delle strisce a 16 bit
invece di 32 (`_serialize` solleva `Scene3DError` se una forma supera
65.535 vertici invece di troncare in silenzio — non ancora visto nella
realtà, ma dichiarato esplicitamente), e una codifica compatta a 2 byte
per le rotazioni allineate agli assi (permutazioni pure con valori
-1/0/1, il caso comune misurato al 100% sull'istanza reale — fallback a
9 float per una rotazione ad angolo arbitrario genuino).

La quantizzazione è **realmente lossy** (a differenza del solo
arrotondamento float32 di prima), quindi il self-check è stato
ridisegnato con lo stesso principio già usato per `mean_color_error` nel
2D: confronta il payload decodificato contro la scena **già quantizzata**
(non contro l'originale a piena precisione), e `Scene3DEncodeResult`
guadagna il campo `mean_vertex_error` — la distanza media introdotta,
dichiarata onestamente invece di nascosta. Misurato sull'assembly reale:
**0,000776 mm** di errore medio, ben dentro qualunque tolleranza CAD.

**`balzar/gltf.py`** — `scene3d_to_glb` esporta una `Scene3D` in un
file `.glb` valido (verificato non solo con controlli propri ma
**caricato con successo da `pygltflib`**, una libreria glTF indipendente,
sull'assembly reale usato per lo scoping). Asimmetria dichiarata, non un
bug: il grafo di nodi di glTF è un **albero**, non un DAG — supporta il
riuso di **mesh** tra più nodi (usato: le 78 forme uniche restano
uniche nel buffer binario) ma non il riuso di **sotto-alberi interi**
(non esiste un equivalente glTF del "sotto-assieme ripetuto" di 3DXML).
L'esportatore quindi duplica i nodi per ogni istanza (necessario, non
un errore), ma i dati di geometria restano deduplicati. Le strisce di
triangoli vengono appiattite a liste di triangoli semplici (mode 4) per
compatibilità massima con i viewer, invece di contare sul supporto del
mode 5 (TRIANGLE_STRIP).

Verificato sul file reale usato per lo scoping (78 forme, 3.862 istanze,
75.752 vertici): `encode_3dxml_file` — payload **394.021 B** in 0,85s
(era 455.369 B prima delle ottimizzazioni — **13,5% in meno**, coerente
con la stima 390-440 KB fatta nello scoping), **180 QR** a 2.194 B/QR
(era 208); `scene3d_to_glb` — 2,13 MB in 0,08s, 78 mesh / 7.725 nodi /
3 materiali / 1.623 nodi con mesh (i posizionamenti-foglia reali) —
dimensione del GLB invariata rispetto a prima: usa comunque float32 al
suo interno, le ottimizzazioni riguardano solo il payload `BZM1`.

Comandi CLI: `balzar encode-3d assembly.3dxml -o out.b3d`,
`balzar render-3d out.b3d -o out.glb`. Test: `tests/test_scene3d.py`
(16 test, fixture 3DXML sintetica costruita in memoria — nessun file
CAD reale nel repository) + 4 test in `tests/test_cli.py` — 194 test
totali.

### 9.6 Cosa manca ancora (esplicitamente non fatto in questa sessione)

- ~~Ottimizzazioni di dimensione (quantizzazione int16, indici a 16
  bit, rotazioni compatte)~~ — **fatto**, vedi §9.5.
- **Integrazione GUI/demo web**: nessun tab "Assemblee 3D" nella demo
  web, nessun pulsante nella GUI desktop. La pagina che ospiterebbe
  `<model-viewer>` non è stata scritta.
- **Nessuna distinta base (BOM) generata**: `Scene3D` porta già tutti i
  nomi (forme, riferimenti, istanze), ma non esiste ancora una funzione
  che li aggreghi in una tabella "nome parte → quantità" — l'informazione
  c'è, l'estrazione no. Segnalato esplicitamente durante la discussione
  di visione generale (l'obiettivo finale è "scansiona un codice, vedi
  esploso 3D **e** distinta base", non solo la geometria).
- **Nessun test con un file 3DXML reale nel repository** (per gli
  stessi motivi di copyright già visti per il logo Harley-Davidson in
  §2.6): la fixture di test è sintetica, verificata a mano contro il
  file reale dell'utente in sessione ma non committata.
- **Nessuna verifica visiva**: il GLB è stato validato strutturalmente
  (header, chunk, parsing con `pygltflib`) ma mai aperto in un browser
  con `<model-viewer>` — non è stato controllato che l'orientamento
  reale dei pezzi sia corretto (la conversione riga-maggiore→colonna-
  maggiore della matrice in `gltf.py` è un'assunzione dichiarata, non
  verificata visivamente).

## 10. Comandi utili per riprendere il lavoro

```bash
python3 -m unittest discover -s tests        # 194 test (alcuni opzionali su qrcode/pyzbar), deve restare verde
python3 -m balzar encode-3d assembly.3dxml -o out.b3d
python3 -m balzar render-3d out.b3d -o out.glb
python3 -m balzar gui                        # app desktop
python3 -m balzar encode-image foto.png -o f.bzp
python3 -m balzar encode-vector drawing.svg -o f.bzp
python3 -m balzar encode-video anim.gif -o v.bzp
python3 -m balzar encode-sequence step1.dxf step2.dxf step3.dxf -o seq.bzp
python3 -m balzar explode-vector drawing.dxf -o esploso.bzp --steps 6
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
