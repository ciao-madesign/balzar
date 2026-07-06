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

### 2.4b Sequenze multi-frame di QR: meccanismo, bundle, lettura live

Domanda diretta di sessione: `payload_to_qr_image` mette **tutti** i
capitoli in un'unica griglia auto-dimensionata (`cols = ceil(sqrt(n))`,
nessun tetto) — per il payload 3D reale (178 capitoli) diventa una
griglia 14×14 in un solo file, mai pensata per essere fotografata o
proiettata a dimensione leggibile (l'unico caso misurato finora, §9.3,
è 4×4=16). Quattro decisioni prese in sessione, in ordine, più un
benchmark reale per la quinta (4×4 vs 8×8):

**1) Meccanismo di spezzettamento in frame.** Il numero di frame non
dipende dalla dimensione del payload ma da un tetto esplicito di QR per
frame (`grid_dim`, es. 4→16 o 8→64), scelto in base al vincolo fisico
(schermo/stampa), non calcolato a piacere di `sqrt(n)`. Nuova funzione
`payload_to_qr_frames(payload, grid_dim=4) -> list[Image]` in
`balzar/qr.py`: raggruppa i capitoli già prodotti da `chunk_payload` in
blocchi da `grid_dim²` e produce **una lista di immagini griglia**
invece di una sola. `payload_to_qr_image` resta invariata (caso
`n_frame == 1` implicito, griglia singola non limitata) — nessuna
modifica al comportamento esistente, verificato dagli stessi test di
prima ancora verdi.

**2) Sequenza dei frame.** Distinzione netta tra cosa già garantisce il
formato e cosa serve solo all'utente umano: l'header `BZC1` (indice/
totale/CRC del payload intero) è dentro ogni singolo QR, indipendente
dal frame che lo contiene — `assemble_chunks` già accetta capitoli in
qualsiasi ordine. **Non serve nessun nuovo campo dati per l'ordine dei
frame**: l'unica cosa nuova è un'etichetta testuale "Frame i/N" stampata
su ogni griglia (stesso principio della label "i+1/totale" già su ogni
singolo QR), pura affordance per l'utente/fotocamera — sapere quante
foto mancano, non un requisito di correttezza.

**3) Bundle.** Scartato MP4/video: servirebbe un encoder nuovo (dipendenza
pesante, contro "stdlib pura" del motore core) ed è lossy per default,
stesso problema già noto per JPEG su bordi netti (§8) — un QR è
contenuto ad altissimo contrasto, un codec con perdita rischia di
sfumare i moduli. Scelti invece, dalla stessa lista di frame, **due
esportatori leggeri**, zero dipendenze nuove (Pillow è già usato in
`qr.py`):
- `frames_to_gif(frames, duration_ms=1500, loop=0) -> bytes` — GIF
  animata per il caso "schermo che mostra i frame in sequenza da solo".
  **Senza perdita per questo contenuto specifico**: un QR è puro
  bianco/nero, quindi il limite di palette a 256 colori della GIF (che
  conterebbe su una foto) qui non costa nulla.
- `frames_to_files(frames, out_dir) -> list[str]` — un PNG per frame,
  per il caso "stampa su carta" (§6.1), dove "auto-play" non ha senso.

**4) Lettura.** Le due modalità di bundle si riducono allo **stesso
algoritmo di lettura** — cambia solo la sorgente dei fotogrammi (foto
sequenziali di pagine stampate, o foto/frame video di uno schermo che
riproduce la GIF), non la logica di riassemblaggio. Nuova classe
`LiveScanner` in `balzar/qr.py`: accumula `{indice: capitolo}` su
chiamate ripetute di `.add(foto)`, tollera **qualsiasi ordine, qualsiasi
sottoinsieme di frame per chiamata, e la stessa foto ripetuta più volte**
(un capitolo duplicato viene semplicemente ignorato, non è un errore) —
stessa indipendenza dall'ordine che `scan_image_bytes` aveva già per una
singola foto, estesa su più foto invece di richiedere completezza in
uno scatto solo. `.add()` ritorna `(completo, mancanti)` riusando
esattamente il calcolo `missing` già presente in `assemble_chunks`;
`.result()` assembla il payload quando `completo` è vero. Per i test
automatici, `gif_to_frames(data) -> list[Image]` (via
`PIL.ImageSequence.Iterator`) splitta una GIF già scritta nei suoi
frame senza bisogno di una fotocamera reale — stessa metodologia già
usata altrove nel progetto (verifica by codice, fotografia reale solo
come test manuale one-off).

Verificato in `tests/test_qr.py` (6 nuovi test, `TestQRFrameSequence`):
tetto sul numero di codici per frame rispettato, roundtrip completo
frame-per-frame via `LiveScanner`, frame scansionati fuori ordine e con
ripetizioni, progresso `missing` corretto prima del completamento,
roundtrip completo attraverso bundle GIF e attraverso bundle a file
separati.

**5) 4×4 contro 8×8 — benchmark reale, non stimato.** Prima di questa
misura esistevano dati solo su 4×4 (§9.3: sweet spot 1700–2400px, piena
risoluzione 4704px **più lenta senza guadagno di affidabilità**).
Generata una vera griglia 8×8 (64 QR, primo frame pieno — il caso
peggiore, non una griglia a metà) dallo stesso payload di test (183.280
byte, 84 capitoli) e scansionata alle stesse risoluzioni del benchmark
4×4:

| Griglia | Larghezza immagine | QR decodificati | Tempo |
|---|---|---|---|
| 4×4 (16 QR) | 4704px (piena) | 16/16 | 3,19 s |
| 4×4 (16 QR) | 2400–1700px (sweet spot noto) | 16/16 | 0,23–0,45 s |
| 4×4 (16 QR) | 1600px | 14/16 (degrada) | — |
| 4×4 (16 QR) | 1200px | 0/16 (fallisce) | — |
| 8×8 (64 QR) | 9336px (piena) | 64/64 | 16,35 s |
| **8×8 (64 QR)** | **4704px** | **64/64** | **4,16 s** |
| 8×8 (64 QR) | 3400px | 9/64 (crollo) | 1,17 s |
| 8×8 (64 QR) | 2400px e sotto | 0–1/64 (fallisce) | — |

Risultato netto, non ambiguo: l'8×8 ha **un'unica finestra di lettura
affidabile**, esattamente alla risoluzione (4704px) che il benchmark
4×4 aveva già misurato come "piena, lenta, senza guadagno" — sotto
quella soglia il crollo è a picco (64/64 → 9/64 tra 4704 e 3400px), non
graduale. E a quella risoluzione il tempo di decodifica di un singolo
frame 8×8 (4,16 s) è **~15–18× più lento** dello sweet spot 4×4
(0,23–0,29 s) per 4× i codici — un rapporto tempo/codice peggiore, non
migliore: quadruplicare i codici per frame *non* dimezza il numero di
acquisizioni a parità di tempo totale, lo aumenta. Conferma diretta,
con dati reali, del sospetto di design: per mantenere la stessa nitidezza
per-modulo, una griglia 8×8 nella stessa area fisica richiede circa il
doppio della risoluzione lineare del sweet spot 4×4, e quella
risoluzione è già il regime "lento senza guadagno" scoperto sul 4×4.

**Decisione**: `grid_dim=4` resta il default e il tetto consigliato.
Un payload grande accetta **più frame da 16 QR** (sequenza più lunga,
tempo di decodifica per frame che resta nello sweet spot misurato),
non frame più densi — esattamente il fallback già previsto in sessione
se il test fosse andato male. `grid_dim=8` resta disponibile come
parametro esplicito (nessun limite hardcoded nel codice) per chi
controlla un supporto fisico/schermo diverso e vuole ripetere questo
stesso benchmark sulle proprie condizioni reali — non è consigliato
come default.

**Non ancora fatto**: nessuna integrazione CLI/GUI/demo web per
`payload_to_qr_frames`/`frames_to_gif`/`frames_to_files`/`LiveScanner`
— oggi sono solo funzioni di libreria, verificate da test, non ancora
esposte come comando `balzar` o pulsante. Prossimo passo naturale se
si vuole portare questo in campo, non fatto in questa sessione per
tenere lo scope alla sola domanda posta (meccanismo + benchmark).

**6) Ottimizzazione della lettura: ritaglio per-cella invece di ZBar
sull'immagine intera — un primo tentativo ha peggiorato le cose, non
migliorate.** Verifica end-to-end su un vero assieme 3DXML (§9.10) ha
mostrato che il collo di bottiglia reale è la scansione: ZBar impiega
5,84s per decodificare una griglia reale da 16 QR perché cerca i
pattern finder sull'intera tela. Un primo tentativo di ottimizzazione
ha ritagliato l'immagine in `grid_dim × grid_dim` regioni assumendo una
divisione uniforme con un margine di sicurezza del 15% — **misurato
peggio, non meglio**: il margine non era abbastanza preciso da
catturare sempre tutti e 16 i codici (ne trovava 11-14/16), quindi il
controllo "la griglia ritagliata ha recuperato tutto?" falliva quasi
sempre e il codice pagava la scansione whole-image di riserva **in più
del** tentativo di ritaglio, non al suo posto — 66,5s misurati contro
i 39,7s di partenza, una regressione reale, scoperta solo ri-misurando
end-to-end e non fidandosi del microbenchmark isolato (un singolo
ritaglio decodificato in 0,118s contro 4,226s per l'immagine intera,
che sembrava promettente ma non teneva conto del costo aggregato di 16
chiamate ZBar separate né del tasso di mancata cattura).

Fix: invece di indovinare una divisione uniforme, `_tile_boxes` ora
**inverte la formula di layout che `_compose_grid` usa davvero**
(`cell`/`pad` risolti per punto fisso, dato che `pad = max(12, cell //
15)` dipende debolmente da `cell`), recuperando la geometria esatta
invece di una approssimazione. Misurato sulla stessa griglia reale:
**16/16 codici recuperati, 3,03s contro 5,84s** dell'immagine intera —
un guadagno vero, non solo un ritaglio più piccolo. Guardia di
sicurezza aggiunta: se la geometria risolta produce un `cell`
implausibile (es. un singolo QR non in griglia, dove l'assunzione
`grid_dim × grid_dim` non si applica affatto), `_tile_boxes` fallisce
in modo esplicito restituendo nessun box invece di passare coordinate
invertite a `Image.crop` — il chiamante nota semplicemente che il
ritaglio non ha recuperato una griglia completa e passa alla scansione
whole-image, mai un crash.

Esposto come parametro opzionale `grid_dim` su `LiveScanner.add()` e
`scan_image_bytes()` — **solo un suggerimento di velocità, mai un
requisito di correttezza**: usato esclusivamente quando il ritaglio
recupera una griglia `grid_dim²` completa, altrimenti ricade
esattamente sulla stessa scansione whole-image di sempre (un frame
finale parziale, o un'immagine che non è davvero una griglia). Un hint
sbagliato o assente non perde mai un codice, costa solo la velocità
extra.

**Ri-misurato sulla pipeline reale** (§9.10, stessi 7 frame del secondo
assieme 3DXML): lettura totale **44,62s → 28,65s** (~1,56×), tutti i
capitoli recuperati, **bit-identico** in entrambi i casi. I 6 frame
pieni scendono da ~6-7,5s a ~3,4-3,6s ciascuno; il 7° frame (parziale,
13 codici) ricade sul fallback com'era prima. Verificato con
`tests/test_qr.py` (3 nuovi test: hint bit-identico, fallback esplicito
su un'immagine non a griglia, corrispondenza tra `_decode_tiled` e la
scansione whole-image su una griglia completa) — 212 test totali.

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

214 test, tutti verdi (`python3 -m unittest discover -s tests`):
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
`test_png.py`) — più le sequenze multi-frame di QR (`TestQRFrameSequence`
in `test_qr.py`: tetto sul numero di codici per frame, roundtrip
completo via `LiveScanner` frame per frame, frame fuori ordine e
ripetuti, progresso "mancanti" corretto prima del completamento,
roundtrip attraverso bundle GIF e attraverso bundle a file separati,
l'hint `grid_dim` di velocità (risultato bit-identico con e senza,
fallback esplicito su un'immagine a QR singolo dove l'assunzione a
griglia non si applica affatto, corrispondenza tra `_decode_tiled` e la
scansione whole-image su una griglia completa) — vedi §2.4b).

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

### 7.5 Convertitore STEP → 3DXML (o altro) per allargare l'input di scene3d.py

Proposta: dato che STEP è il vero formato di interscambio standard
(3DXML è nativo solo dell'ecosistema Dassault/SolidWorks), integrare un
convertitore STEP→3DXML a monte di `scene3d.py`, così l'ingestione 3D
accetta il formato che un utente ha davvero, non quello che il nostro
parser preferisce. L'idea in sé è coerente con la filosofia già usata
nel progetto (delegare un problema difficile a uno strumento maturo
invece di scriverlo da zero — Pillow per JPEG/PNG, `model-viewer` per il
rendering) e **non equivale a scrivere un parser STEP nostro** (§7.3
resta valida per quello scenario specifico).

**Scartato l'esempio concreto proposto** (`3dencoder.com`, un servizio
web di conversione): è un servizio di terzi, e usarlo — anche solo come
passaggio manuale prima di dare il file a balzar — richiederebbe
caricare l'assieme CAD su un server esterno. Va contro il requisito
guida del caso d'uso §6.1 (manutenzione sul campo, spesso senza rete) e
contro la privacy di un disegno CAD proprietario, che un contesto
industriale reale normalmente non accetta di caricare altrove. (Il
fetch automatico del link è stato bloccato con un 403, quindi non è
stato verificato nemmeno se il servizio esponga un'API scriptabile —
scartato comunque a prescindere per il problema di principio sopra.)

**Alternativa realistica identificata, non ancora implementata**:
FreeCAD o `pythonocc` (binding Python di OpenCASCADE, il kernel CAD
open-source che FreeCAD stesso usa) — entrambi open-source, scriptabili,
**offline**, con lettura STEP nativa. Non è garantito che sappiano
scrivere 3DXML in uscita, ma non è necessario: un adattatore potrebbe
leggere l'albero documento di FreeCAD/OCCT (parti/nomi/trasformi, stessa
idea concettuale di `Reference3D`/`Instance3D`) e costruire direttamente
un `Scene3D`, saltando 3DXML come formato intermedio — stesso principio
di `vectorio.py` per SVG/DXF: nessun parser B-rep scritto da noi, solo
un ponte verso una libreria che lo sa già fare.

**Stato**: valutata, non implementata. Nuova dipendenza opzionale, nuovo
modulo, nuova superficie di test — non avviata senza una decisione
esplicita di procedere, dato lo scope non piccolo.

### 7.6 HTML/XML come sorgente — valutata, non implementata

Domanda diretta di sessione: balzar può codificare HTML/XML? **Oggi
no** — nessun modulo del progetto ingerisce markup generico. Gli
encoder esistenti sono tutti per contenuto diverso: raster
(`encoder.py`), grafica vettoriale SVG/DXF (`vectorio.py` — ingerisce
solo primitive geometriche di *disegno*, `<circle>`/`<path>`/`TEXT`,
non il DOM/markup di una pagina), video (`video.py`), CAD 3D
(`scene3d.py`). Nessuno di questi tratta HTML/XML come testo/markup
strutturato da comprimere.

Il modello sarebbe diverso da tutti gli encoder attuali: non "copertura
a rettangoli di pixel" ma "template + diff dei parametri" (già annotato
come idea speculativa in §5 punto 11, qui valutata con numeri reali
invece che solo ipotizzata) — un albero di tag che si ripete con solo
alcuni campi che cambiano (righe di una tabella, blocchi di componente
in un catalogo) diventa un LOOP-equivalente con i valori variabili
estratti, invece di essere ricompresso byte per byte da un compressore
generico. Servirebbe: un parser (stdlib pura, `xml.etree.ElementTree`
per XML/XHTML ben formato, `html.parser` per HTML reale — zero nuove
dipendenze, stesso principio di `vectorio.py`) **più** un algoritmo di
estrazione di pattern strutturali che oggi non esiste in nessuna forma
nel progetto — non un'estensione di un encoder esistente, un encoder
nuovo da zero.

**Guadagno per un manuale da 12MB — dipende interamente dalla
composizione, misurato su due casi sintetici rappresentativi invece che
stimato a caso**:

| Contenuto sintetico | Byte grezzi | gzip -9 | Rapporto |
|---|---|---|---|
| Markup templato (400 blocchi "componente" con tabella specifiche ripetuta + boilerplate + prosa ripetuta) | 142.807 | 5.672 | **25,2×** |
| Prosa che varia genuinamente (900 paragrafi, nessuna struttura ripetuta, vocabolario ridotto — quindi ottimistico rispetto a prosa reale) | 504.299 | 72.458 | **7,0×** (prosa reale tipica: ~2,5-4× con gzip, dato noto in letteratura, non rimisurato qui) |

Il punto onesto: **gzip da solo prende già 25× sul caso fortemente
templato** — un encoder balzar dedicato dovrebbe battere quel numero
per giustificare il lavoro, non solo eguagliarlo, perché gzip è già
gratis e non richiede nessuna estrazione di pattern (DEFLATE trova da
solo la ripetizione byte-a-byte della stessa tabella HTML ripetuta 400
volte). Un vero encoder "template+diff" potrebbe spingersi oltre
(memorizzare solo i 3 campi che cambiano per blocco invece dell'intera
struttura HTML circostante, anche compressa) — ma questo è speculativo,
nessun prototipo scritto, nessuna misura reale di quanto in più
otterrebbe rispetto ai 25× già gratuiti di gzip.

Sul secondo caso (prosa) il limite è strutturale, non implementativo:
il testo naturale ha una complessità di Kolmogorov vicina alla sua
entropia — non esiste una scorciatoia "generativa" per prosa unica,
stesso principio già applicato a rumore/foto (§4.7) e già dichiarato
per audio campionato (§7.4). Un manuale tecnico reale da 12MB è quasi
certamente un misto: markup/boilerplate ripetuto (il caso dove balzar
potrebbe guadagnare, se e quando si scrivesse l'estrattore), prosa
(nessun guadagno oltre gzip, per nessun encoder possibile), e
probabilmente immagini/diagrammi incorporati — questi ultimi **già
gestiti oggi**, ma da un encoder diverso e già esistente: raster via
`encoder.py`/`imageio.py` se rasterizzate, oppure direttamente
`vectorio.py`/`svg.py` se il manuale incorpora SVG vettoriale reale
(caso comune per diagrammi tecnici esportati da CAD). Senza un file
reale da 12MB da analizzare, qualunque numero complessivo per "il
manuale" sarebbe inventato — la tabella sopra è la misura vera dei due
estremi che lo compongono, non una stima del tutto.

**Stato**: valutata, non implementata. Nessun lavoro iniziato oltre
questa valutazione: nuovo modulo, nuovo algoritmo di estrazione
pattern, nuova superficie di test — scope paragonabile a un encoder
esistente da zero, non una piccola estensione.

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
(19 test, fixture 3DXML sintetica costruita in memoria — nessun file
CAD reale nel repository) + 4 test in `tests/test_cli.py` — 202 test
totali.

### 9.6 Cosa manca ancora (esplicitamente non fatto in questa sessione)

- ~~Ottimizzazioni di dimensione (quantizzazione int16, indici a 16
  bit, rotazioni compatte)~~ — **fatto**, vedi §9.5.
- ~~Integrazione GUI/demo web~~ — **fatto**, vedi §9.9.
- ~~Nessuna distinta base (BOM) generata~~ — **fatto**, vedi §9.8.
- **Nessun test con un file 3DXML reale nel repository** (per gli
  stessi motivi di copyright già visti per il logo Harley-Davidson in
  §2.6): la fixture di test è sintetica, verificata a mano contro il
  file reale dell'utente in sessione ma non committata.
- ~~Nessuna verifica visiva~~ — **fatto**, vedi §9.7.

### 9.7 Verifica visiva reale: `<model-viewer>` + Playwright/Chromium

L'ambiente di sviluppo di questa sessione ha Chromium e Playwright
preinstallati (per altri scopi), quindi invece di lasciare la
conversione riga-maggiore→colonna-maggiore della matrice come
un'assunzione dichiarata ma non controllata, è stato possibile
verificarla per davvero:

1. **Prova algebrica** (non solo visiva): applicando `_matrix_to_gltf`
   a una rotazione nota di +90° attorno a Z in senso antiorario
   (`r=(0,-1,0, 1,0,0, 0,0,1)`, convenzione riga-maggiore) e calcolando
   a mano `M·(1,0,0,1)` con la matrice colonna-maggiore risultante, il
   punto (1,0,0) si trasforma esattamente in (0,1,0) — il risultato
   atteso per quella rotazione. Conferma che la trasposizione riga→
   colonna in `gltf.py` è corretta, non solo "sembra funzionare".
2. **Prova visiva indipendente**: costruita una `Scene3D` sintetica con
   tre triangoli asimmetrici (per rendere una rotazione visivamente
   riconoscibile, a differenza di un quadrato) — rosso all'origine,
   verde traslato (stessa rotazione identità), blu ruotato di 90° attorno
   a Z e traslato. Esportato in GLB, servito via `http.server` locale
   (necessario: `file://` blocca i moduli ES per CORS), caricato in
   Chromium headless con `@google/model-viewer` (build UMD, non il
   modulo ES — quello richiede risoluzione di specifier bare tipo
   "three" che un browser semplice non sa risolvere) via Playwright,
   screenshot reale. Risultato: rosso e verde hanno la stessa forma/
   orientamento (conferma traslazione), il blu ha una forma visibilmente
   diversa (conferma che la rotazione viene applicata, non ignorata né
   corrotta). Verificato anche sul GLB dell'assembly reale (78 mesh,
   1.623 nodi con mesh): renderizza senza errori, nessun artefatto di
   geometria degenere.

Non ripetuto nei test automatici (richiederebbe Chromium+Playwright+
model-viewer come dipendenze di test, non solo di sviluppo) — verifica
manuale one-off, come già fatto altrove nel progetto per la GUI
desktop sotto Xvfb.

### 9.8 Distinta base (BOM): `generate_bom`

Risposta diretta alla domanda di visione generale ("scansiona un
codice, vedi l'esploso 3D **e** la distinta base") — `Scene3D` portava
già tutti i nomi ma non c'era una funzione che li aggregasse. `Scene3D
scene3d.generate_bom(scene)` percorre l'albero con la stessa logica di
raggiungibilità già usata per `instance_count`/`mean_vertex_error`
(non conta le definizioni `Reference3D` foglia, conta i posizionamenti
reali con la moltiplicità dei sotto-assiemi ripetuti — lo stesso motivo
per cui una geometria nell'assembly reale risulta usata 360 volte pur
essendo una sola definizione). Le voci sono raggruppate per
`(nome, indice_forma)`: due riferimenti con nomi diversi che condividono
la stessa geometria restano due righe di BOM distinte (una vite e un
rivetto possono avere la stessa forma ed essere pezzi diversi); un
riferimento senza nome riceve un'etichetta placeholder esplicita invece
di essere confuso con un'altra forma.

`Scene3DEncodeResult` guadagna il campo `bom: list[BomEntry]`, calcolato
automaticamente da `encode_3dxml_file`. CLI: `balzar encode-3d ... --bom`
stampa la tabella completa (senza il flag, solo il riepilogo "N parti
uniche, M posizionamenti totali"). Verificato sull'assembly reale: 78
parti uniche, 1.623 posizionamenti totali, il pezzo più riusato compare
360 volte — numeri identici a quelli già misurati a mano nello scoping
(§9.2), stavolta calcolati dal codice invece che da uno script usa e
getta.

Non ancora fatto al momento della scrittura di questa sezione: nessuna
vista/esportazione della BOM in un formato diverso dalla stampa a
schermo — risolto subito dopo, vedi §9.9 (sovrapposta al viewer nella
GUI desktop e nella demo web, entrambe come tabella HTML/Tk, non ancora
come CSV o testo inciso nel GLB stesso).

### 9.9 Integrazione GUI desktop e demo web

**Vendorizzato `model-viewer.min.js`** (build UMD di `@google/model-viewer`
4.3.1, Apache-2.0, ~1 MB) alla radice del repository — non da CDN, stesso
principio offline-first del resto del progetto: la build UMD è stata
scelta apposta invece della build a modulo ES (`model-viewer.min.js`
upstream), che usa `export`/specifier bare come `"three"` e non si carica
con un semplice `<script>` in una pagina senza bundler.

**`balzar/viewer3d.py`** (nuovo, solo per la GUI desktop): scrive
`model.glb` + una paginetta HTML (`<model-viewer>` + una tabella BOM
sovrapposta in overlay) + una copia di `model-viewer.min.js` in una
directory temporanea, avvia un `http.server` locale su una porta
effimera e apre il browser di sistema. **`file://` non basta**: Chrome
blocca il fetch/XHR che `<model-viewer>` usa per caricare il GLB quando
l'origine è `file://` ("CORS policy: cross origin requests only
supported for http/https"), anche se il GLB sta nella stessa cartella
dell'HTML — scoperto producendo gli screenshot diagnostici di §9.7,
stessa soluzione riusata qui (servire su `localhost` invece).

**GUI desktop (`balzar/gui.py`)**: `Job` guadagna `is_3d`/`glb`/`bom_lines`.
Un file `.3dxml` (encoding nuovo) o `.b3d` (riapertura di un payload già
codificato, magic `BZM1` controllato prima del vecchio magic `BZR1`)
vengono riconosciuti in `_worker` e instradati a `_job_from_3dxml`/
`_job_from_3d_payload`. Nessuna anteprima 2D esiste per un assieme 3D —
i due canvas mostrano un testo placeholder ("assieme 3D" / "usa
'Visualizza in 3D'") invece di fingere un'immagine — e i pulsanti
inapplicabili (Salva programma, Esporta PNG/GIF, Esporta SVG) restano
disabilitati, mentre Salva payload cambia effettivamente estensione
(`.b3d`, non `.bzp` — è un formato binario genuinamente diverso da
`BZR1`) e un nuovo pulsante "Visualizza in 3D (browser)" chiama
`viewer3d.open_glb_in_browser`. Verificato sotto Xvfb con un vero
`root.mainloop()` (non polling manuale — il primo tentativo di test con
polling manuale ha prodotto `RuntimeError: main thread is not in main
loop`, un artefatto del metodo di test, non un bug in `gui.py`: con un
mainloop reale sia il flusso 2D esistente sia i due flussi 3D nuovi
funzionano senza errori).

**Demo web**: sesto tab "Assemblee 3D" (`api/encode_3d.py` +
`handle_encode_3d` in `webapi.py`). Diversamente dagli altri tab non
c'è un PNG da mostrare: la risposta include il GLB in base64, il
frontend lo trasforma in un Blob URL e lo assegna a `<model-viewer
src="...">` lato client — lo stesso principio "il payload compatto e
il formato di visualizzazione sono cose diverse" di `gltf.py`, solo
applicato al browser invece che al filesystem. La distinta base arriva
come JSON e diventa una tabella HTML.

**Bug reale trovato testando il nuovo tab, preesistente su tutti e
cinque i tab originali**: `style.css` aveva `.qr-block { display: flex;
... }` senza guardia — specificità CSS pari a `[hidden] { display:
none }` della regola nativa del browser, e la regola d'autore vince
perché arriva dopo nel cascade. Risultato: il blocco QR (che parte
`hidden` in ogni tab, pensato per apparire solo dopo aver cliccato
"genera QR") **si mostrava comunque** appena la sezione risultato
principale del tab diventava visibile — mai notato prima perché mascherato:
finché la sezione risultato è `hidden`, anche il blocco QR al suo interno
resta invisibile "per procura", quindi il problema si vede solo dopo un
encode riuscito, controllando lo stato del singolo elemento (non solo
guardando lo screenshot a occhio). Trovato con un controllo Playwright
mirato (`element.hidden === true` ma `is_visible() === true`, la
contraddizione che ha rivelato il problema), corretto con una singola
regola `.qr-block[hidden] { display: none; }` (specificità più alta,
vince per costruzione) che risolve tutti e sei i tab in un colpo solo.

Verificato end-to-end con Playwright contro un server locale
(`http.server` + le funzioni `handle_*` dirette, stessa metodologia già
nota — non contro il deploy Vercel reale, non raggiungibile da questo
sandbox): upload `.3dxml` → stats/BOM popolate correttamente → modello
caricato in `<model-viewer>` (`loaded === true`) → download payload/GLB
→ generazione QR reale (screenshot con QR code vero). Test aggiunti:
`TestHandleEncode3D` in `tests/test_webapi.py` (5 test: successo,
dati mancanti, base64 malformato, 3DXML non valido, GLB omesso oltre
il limite di risposta) — 202 test totali.

### 9.10 Verifica end-to-end reale: secondo assieme 3DXML, pipeline completa QR

Sessione successiva: l'utente ha fornito un **secondo** assieme 3DXML
reale (skid industriale con serbatoi/telaio/pompa, non incluso nel
repository per lo stesso motivo di copyright già visto per il logo
Harley-Davidson §2.6 e il primo assieme §9.2) con la richiesta esplicita
di eseguire l'intera pipeline — codifica → QR multi-frame (§2.4b) →
lettura → rigenerazione 3D — e misurare ogni passo, non solo confermare
che "funziona". Numeri reali (nessuno stimato):

| Passo | Tempo | Note |
|---|---|---|
| Parse 3DXML originale | 0,095 s | 88 forme, 360 riferimenti, 516 istanze (archi DAG totali) |
| **1. Codifica** (`encode_3dxml_file`) | 0,457 s | 500.756 B → **239.491 B payload**, **2,09×** vs il `.3dxml` sorgente, **8,60×** vs flattening ingenuo senza dedup (2.060.324 B) |
| **2. Generazione QR** (`payload_to_qr_frames`, grid_dim=4) | 22,9 s | 109 capitoli → **7 frame** (4704×4818 px, piena risoluzione) |
| bundle GIF (`frames_to_gif`) | 15,7 s | 8.999.976 B (9 MB — pesante, per il caso "schermo che cicla da solo") |
| bundle PNG (`frames_to_files`) | 3,0 s | 2.339.416 B totali, 7 file |
| **3. Lettura** (`LiveScanner`, risoluzione piena) | 28,6–60,4 s (varianza tra run, vedi sotto) | tutti e 109 i capitoli recuperati, **bit-identico** al payload originale sia dal bundle PNG sia dal bundle GIF ri-letto |
| **4. Decodifica + export GLB** | 0,045 s + 0,033 s | 88 mesh / 1.033 nodi / 245 nodi-con-mesh (confermato **indipendentemente** da `pygltflib`, non dal nostro stesso codice) |

**Fedeltà (passo 4), misurata contro l'originale vero, non contro la
copia già quantizzata che `encode_3dxml_file` usa per il proprio
self-check interno**: errore medio per vertice **0,00079 mm**, massimo
**0,0074 mm** — un ordine di grandezza sotto la tolleranza CAD tipica.
Conteggi forme/riferimenti/BOM **tutti coincidenti** con l'originale.
Verifica visiva indipendente (Playwright + `<model-viewer>`, stessa
metodologia §9.7): modello caricato (`loaded === true`), screenshot
reale — un assieme industriale riconoscibile (skid con due serbatoi,
telaio tubolare, gruppo valvole/pompa separato), nessun artefatto di
geometria degenere o "fantasma".

**Zero bug funzionali trovati**: nessun crash, nessuna corruzione,
nessun conteggio disallineato, nessuna eccezione non gestita in tutta
la pipeline. Un apparente problema si è rivelato **non essere un bug**
dopo verifica diretta: il render appare monocromatico (un solo
materiale/colore su tutte le 88 forme, `(204,204,230)`) — controllato
alla fonte (`scene.shapes` prima di qualunque nostra elaborazione) e
confermato che è una proprietà genuina del file 3DXML sorgente (nessun
colore per-parte impostato in origine), non una perdita introdotta da
`scene3d.py`/`gltf.py`.

**Due criticità reali trovate, non di correttezza ma di prestazioni e
di validità delle assunzioni precedenti**:

1. **La generazione (22,9 s) e soprattutto la lettura a piena
   risoluzione (28,6–60,4 s, varianza tra esecuzioni identiche — rumore
   di scheduling della CPU condivisa in questo sandbox, non determinismo
   del codice) dominano il tempo totale della pipeline** (~52–92 s),
   ben oltre l'obiettivo di prodotto "<6-7 s" fissato in §9.3. Quella
   stima presupponeva la scansione allo sweet spot 1700-2400px note lì
   misurato, non alla risoluzione piena.
2. **Lo sweet spot 1700–2400px misurato in §9.3/§2.4b su contenuto
   sintetico NON si trasferisce a questo contenuto reale** — correzione
   onesta a un'assunzione implicita precedente. Uno sweep di risoluzione
   sugli stessi 7 frame reali:

   | Larghezza | Esito |
   |---|---|
   | 4704px (piena) | tutti i capitoli letti |
   | 3800px | tutti i capitoli letti |
   | 3400px | **incompleto** (un frame è sceso a 4/16 codici) |
   | 3000–2800px | **incompleto** (un frame sceso a 3/16) |
   | 2400–2000px | **incompleto** (mancano capitoli sparsi) |

   Il crollo è a picco, non graduale (stesso pattern già visto per la
   griglia 8×8 in §2.4b), ma la soglia esatta dipende dal contenuto
   reale del singolo QR (lunghezza dei dati base64 per capitolo, quindi
   versione QR effettiva), non è una costante universale. **Conclusione
   corretta**: la risoluzione di lettura va sempre riverificata sul
   payload reale che si intende scansionare, non assunta dal benchmark
   di un altro contenuto — `payload_to_qr_frames`/`LiveScanner` restano
   corretti, è la scelta della risoluzione di acquisizione a valle
   (fuori dal codice di libreria) a richiedere una verifica caso per
   caso, non ancora automatizzata.

Nessuna modifica al codice da questa verifica: nessun bug da correggere,
solo due correzioni oneste alle aspettative di prestazioni documentate
in §9.3/§2.4b.

**Seguito, stessa sessione**: la prima criticità (tempo dominato dalla
lettura) è stata affrontata con l'hint `grid_dim` su
`LiveScanner.add()`/`scan_image_bytes()` — vedi §2.4b punto 6 per la
storia completa (incluso un primo tentativo che ha *peggiorato* i
tempi, scoperto solo ri-misurando end-to-end su questi stessi 7 frame
invece di fidarsi di un microbenchmark isolato). Ri-misurato su questa
stessa pipeline reale dopo il fix: lettura totale **44,62s → 28,65s**
(~1,56×), bit-identico in entrambi i casi.

### 9.11 Clicca una parte per evidenziarla/isolarla: model-viewer scene-graph API

Domanda diretta di sessione, risposta al punto lasciato aperto in §9.9
(nessuna esplorazione per sotto-parte, solo orbita dell'intero
assieme). Verificato prima di scrivere codice quale parte dell'API
scene-graph di `model-viewer` è davvero **pubblica** nel build
vendorizzato (4.3.1 UMD) invece di fidarsi della memoria: `grep` sul
file minificato mostra che `nodeFromPoint` è un `Symbol` interno (non
richiamabile dall'esterno), mentre `materialFromPoint(x, y)` e
`positionAndNormalFromPoint(x, y)` sono metodi pubblici veri, e ogni
`Material` espone sia `get name()` sia
`pbrMetallicRoughness.setBaseColorFactor(...)` — inclusa una vera
`get baseColorFactor()` per leggere il colore attuale, e `setAlphaMode`
per il blending. Solo API pubblica e documentata usata, nessun hack su
proprietà interne.

**Il vincolo architetturale reale**: `gltf.py` deduplicava i materiali
per colore (§9.5), quindi in un file reale con un solo colore condiviso
da tutte le 88 forme (§9.10) `materialFromPoint` avrebbe restituito
**lo stesso oggetto Material per qualunque parte cliccata** — impossibile
distinguere un posizionamento dall'altro. Fix: ogni **istanza-foglia**
(non più ogni forma unica) riceve ora il proprio mesh+materiale nel GLB
esportato — stesso principio di deduplicazione geometrica di sempre
(gli accessor POSITION/indices restano condivisi per forma, il costo
aggiuntivo è solo JSON), ma materiali/mesh non più deduplicati per
colore. Ogni materiale porta `alphaMode: "BLEND"` fin dall'export, così
un click può attenuare via alpha (isolamento vero) non solo ricolorare.

**Costo reale misurato** sull'assieme del secondo file 3DXML (§9.10):
GLB **1.107.300 B → 1.154.652 B (+47.352 B, +4,3%)**, tempo di export
invariato (0,055s). `meshes`/`materials` passano da 88 (una per forma
unica) a 245 (una per posizionamento-foglia reale) — la geometria nel
buffer binario resta però identica: gli accessor sono ancora condivisi,
solo l'involucro JSON per-istanza si moltiplica.

**Interazione**: click sul modello (`materialFromPoint`) seleziona
**l'esatto oggetto Material cliccato** (un singolo posizionamento,
distinto anche da un fratello dello stesso tipo di parte) — colore
acceso su quello, alpha abbassato (0,12) su tutti gli altri. Click su
una riga della distinta base seleziona invece **tutti** i materiali con
quel nome (una riga BOM è un tipo di parte, non un singolo
posizionamento) — nome condiviso via nuovo helper `bom_display_name()`
in `scene3d.py`, usato sia da `generate_bom` sia da `gltf.py` per
garantire che il nome del materiale e il nome della riga BOM coincidano
esattamente. Pulsante "Mostra tutto" e click su sfondo vuoto
(`materialFromPoint` restituisce `null`) ripristinano i colori
originali (cache-ati una volta sola all'evento `load` del
model-viewer).

Implementato in entrambe le interfacce che già mostravano il 3D
(`balzar/viewer3d.py` per la GUI desktop, `index.html`/`app.js` per la
demo web) con la stessa logica JS duplicata (non condivisibile come
file: una è incorporata in un f-string Python, l'altra è uno script
statico) — nessuna terza implementazione, nessuna nuova dipendenza.

**Verificato con Playwright, non solo scritto**: sul GLB reale del
secondo assieme 3DXML — click su una parte visibile → 1 materiale
acceso, 244 attenuati, riga BOM corretta evidenziata (`Object 15`/
`Object 235` a seconda del punto cliccato), pulsante "Mostra tutto"
ripristina tutti e 245 i materiali originali. Ripetuto **due volte**:
una sulla pagina che apre la GUI desktop (`viewer3d.py`, HTML+GLB
serviti in locale) e una **end-to-end reale sulla demo web** (upload
vero del file attraverso un devserver locale che instrada
`/api/encode_3d` a `handle_encode_3d`, non un mock — stessa metodologia
già nota, non contro Vercel). Un problema emerso e risolto durante
questa seconda verifica, non nel codice ma nel test stesso: il primo
tentativo calcolava il punto di click con `getBoundingClientRect()`
**prima** di scorrere l'elemento nella viewport, ottenendo coordinate
sotto il fold — `materialFromPoint` le accetta comunque (non controlla
la visibilità reale), ma un click fisico lì non intercetta nulla;
corretto scorrendo l'elemento in vista prima di calcolare il punto.

Test automatici: `tests/test_scene3d.py` aggiunge
`test_each_instance_gets_its_own_named_material_with_alpha_blend` e
`test_instance_meshes_share_the_same_geometry_accessors` (le due
istanze dello stesso pezzo condividono gli stessi accessor di geometria
ma hanno materiali distinti) — 214 test totali. Nessun test Python per
il click stesso (comportamento client-side, stesso principio già
seguito per il resto della UI 3D: verifica Playwright manuale in
sessione, non nella suite automatica).

## 10. Comandi utili per riprendere il lavoro

```bash
python3 -m unittest discover -s tests        # 214 test (alcuni opzionali su qrcode/pyzbar), deve restare verde
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
