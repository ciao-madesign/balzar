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

~~Non ancora fatto: nessuna integrazione CLI/GUI/demo web~~ —
**integrato nella demo web in una sessione successiva** (non ancora in
CLI/GUI desktop), vedi §2.9 per i dettagli: il bottone "genera QR" di
ogni tab ora espone `payload_to_qr_frames`/`frames_to_gif`/
`frames_to_files` con una scelta esplicita, non solo `payload_to_qr_image`.

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

### 2.4c Trasporto QR di byte arbitrari (`chunks --raw`/`scan --raw`), non passa mai dal motore balzar

Domanda diretta di sessione, nata da un caso concreto: un PDF (dichiarazione
di conformità, 51.318 B, testo nativo non scansionato) passato per davvero
attraverso l'encoder raster di balzar (§4.1) risultava **peggio**
dell'originale — 313.927 B a risoluzione leggibile, 6,1× più grande del
PDF, per lo stesso motivo già noto (bordi non assiali dei glifi, nessun
rilevamento linee/curve). Domanda successiva: e se invece si spezzasse il
PDF **grezzo** su una sequenza di QR, senza nessun tentativo di
compressione generativa?

**Funziona, ed è un uso legittimo di un meccanismo già esistente, non una
funzionalità nuova nel senso pieno.** `chunk_payload`/`assemble_chunks`
(`balzar/payload.py`) e `payload_to_qr_frames`/`LiveScanner`
(`balzar/qr.py`) sono **agnostici al contenuto**: trattano qualunque
sequenza di byte come dati opachi con un header `BZC1` (indice/totale/
CRC32) — lo stesso principio già sfruttato per i bundle multi-documento
(§9.16: "il livello QR/chunking tratta già qualunque payload come byte
opachi"). Prima di questa sessione questo non era raggiungibile da CLI:
`cmd_chunks` forzava `_load_program()` (valida come programma/payload
balzar), e non esisteva alcun modo di generare o leggere una **sequenza**
multi-fotogramma (solo la libreria `payload_to_qr_frames`, mai wired a un
comando — gap già annotato in §2.9).

**Cosa distingue questo path da tutto il resto del progetto**: qui non
c'è generazione. Il motore (griglia/DSL/interprete) non viene mai
toccato — è puro slicing e ricomposizione di byte, ogni passaggio
(`chunk_payload` taglia, `to_base64`/`from_base64` codifica/decodifica
testo in modo reversibile, ZBar legge, `assemble_chunks` concatena e
verifica il CRC32) è una trasformazione **reversibile senza perdita**,
mai un'interpretazione. Conseguenza diretta, verificata non solo per
ragionamento ma con un test concreto: **firme digitali e cifratura
embedded nel file originale sopravvivono intatte**, perché sono funzioni
matematiche calcolate sugli stessi byte, e i byte in uscita sono
byte-identici a quelli in ingresso (dimostrato con un HMAC-SHA256
simulato calcolato prima e dopo il giro QR: stesso hash).

**Implementazione**: nuovi flag su comandi già esistenti, non nuovi
comandi — `chunks --raw` legge `INPUT` come byte grezzi arbitrari invece
di richiedere `_load_program()`; `chunks --qr --grid-dim N` (funziona
anche senza `--raw`, colma il gap generale già annotato in §2.9) genera
una sequenza di fotogrammi via `payload_to_qr_frames` invece dell'unica
griglia auto-dimensionata; `scan` accetta ora **più immagini**
(`nargs="+"`, prima una sola) e usa `LiveScanner` internamente anche per
il caso a una sola foto, così un'unica implementazione copre singolo QR,
griglia singola e sequenza multi-fotogramma; `scan --raw` scrive i byte
ricostruiti così come sono (richiede `-o` esplicito: nessuna estensione
sensata da indovinare per contenuto arbitrario) invece di interpretarli
come payload balzar — incompatibile con `--render` per lo stesso motivo.

**Verificato end-to-end sul PDF reale** (non solo sulla libreria):

| Passo | Byte/QR | Note |
|---|---|---|
| PDF originale | 51.318 | testo nativo, non scansionato |
| Capacità reale per capitolo QR | 2.206 | byte grezzi, al netto dell'espansione base64 (~33%) — non i 2.953 usati per i payload già base64 |
| Capitoli necessari | **24** | non 18 come una prima stima aveva calcolato ignorando l'espansione base64 — errore corretto in sessione |
| Fotogrammi (griglia 2×2, `--grid-dim 2`) | **6** | `balzar chunks file.pdf --raw --qr --grid-dim 2 -o qr/` |
| Round-trip (`balzar scan qr/*.png --raw -o rebuilt.pdf`) | — | **bit-identico** all'originale (SHA256 verificato, `cmp` pulito) |

Confronto onesto con l'"encoding balzar" tentato prima sullo stesso file:
questo percorso non promette né tenta compressione — il PDF nativo
(51.318 B, già efficiente per il suo contenuto) resta l'unità di
trasporto, balzar fa solo da corriere a pacchetti fisici per superare il
limite di un singolo QR (2.953 B), esattamente come già fa per i propri
payload.

**Non ancora fatto**: nessuna interfaccia per leggere una sequenza così
generata **dal telefono** — `LiveScanner`/il riassemblaggio esistono solo
come libreria Python (CLI) al momento della scrittura di questa nota —
**integrato in una sessione successiva**, sia come finestra dedicata
nella GUI desktop sia come pagina web a sé (JS lato client, nessun
round-trip al server per la lettura): vedi §2.4d. Test aggiunti:
`tests/test_cli.py` (5 nuovi: round-trip raw con sequenza multi-
fotogramma su byte arbitrari, errore pulito `--grid-dim` senza `--qr`,
errore pulito `--raw` senza `-o`, errore pulito `--raw`+`--render`
insieme, controprova che senza `--raw` un file non-UTF8/non-balzar
continua a essere rifiutato onestamente) — 269 test totali. Verificata
anche l'assenza di regressioni sul flusso `chunks`/`scan` esistente
(payload balzar, singola griglia, con `--render`).

### 2.4d Trasporto QR come "app nell'app": finestra desktop dedicata + pagina web dedicata

Richiesta diretta di sessione, seguito naturale di §2.4c: portare il
trasporto QR di byte grezzi fuori dal solo terminale — una finestra
dedicata nella GUI desktop e una pagina a sé nella demo web, entrambe
esplicitamente **separate** dal flusso Balzar Studio/Balzar Live (questa
funzionalità non tocca mai il motore generativo, quindi non appartiene
a nessuno dei due gruppi).

**Desktop — `balzar/raw_qr_gui.py` + `balzar/raw_qr_logic.py`.** Split
in due moduli deliberato, non incidentale: `raw_qr_logic.py` non importa
mai `tkinter` (funzioni pure — `encode_file_to_qr_frames`,
`RawQrAssembler`, un thin wrapper stateful su `LiveScanner` che ignora
un path immagine già processato invece di ridecodificarlo), quindi resta
importabile e testabile sotto `unittest` anche nell'ambiente Python
3.11 di sviluppo che **non ha Tk** (§10) — lo stesso vincolo già
documentato per `balzar/gui.py`, qui risolto separando la logica dai
widget invece di rinunciare alla copertura di test. `raw_qr_gui.py`
resta solo il layer widget (`RawQrTransportWindow(tk.Toplevel)`, due tab
Codifica/Leggi, stesso pattern coda+`after(100, ...)` già usato da
`BalzarApp`/`_poll_queue` per non bloccare il mainloop durante
encode/decode). Nuovo bottone "Trasporto file (QR)…" nella finestra
principale (`balzar/gui.py`), stesso principio di deduplica finestra già
usato per "Libreria…" (`_raw_qr_window`, riusa/porta in primo piano
un'istanza già aperta invece di aprirne una seconda).

**Strutturato per poter diventare standalone in futuro, senza esserlo
ancora** (scelta esplicita di sessione): `RawQrTransportWindow` prende
un qualunque master Tk-compatibile a cui agganciare un `Toplevel`;
`main()` in fondo al modulo crea invece una propria root ed esegue la
stessa finestra come programma a sé (`python3 -m balzar.raw_qr_gui`) —
zero refactoring necessario se in futuro si deciderà di impacchettarla
separatamente con PyInstaller, oggi raggiungibile solo dal bottone nella
GUI principale.

**Web — `trasporto-qr.html`/`trasporto-qr.js`, pagina statica separata**
(stesso principio di `come-funziona.html`: nessuna funzione serverless
nuova per la lettura, linkata dall'header di `index.html`), non una
scheda dentro l'app a tab esistente — coerente con l'essere esplicitamente
fuori dal raggruppamento Balzar Studio/Balzar Live.
- **Codifica**: riusa l'endpoint `/api/qr`/`handle_qr` **esistente**
  senza modificarlo — quella funzione tratta già `payload_base64` come
  byte opachi (nessuna validazione di formato balzar al suo interno),
  quindi caricare un file arbitrario e generare la sequenza di pagine QR
  non richiede nessun codice server nuovo, solo un frontend diverso che
  ci carica byte grezzi invece del payload di una scheda encoder.
- **Lettura**: **interamente client-side**, nessun file lascia il
  browser — la ragione di essere di questa pagina, dato che leggere QR
  richiederebbe altrimenti `pyzbar`/`libzbar` nativo, mai esposto sul
  web demo (§2.9). Port JS a mano del formato `BZC1`
  (`balzar/payload.py`: parsing header, CRC32 — tabella IEEE 802.3
  scritta da zero, stesso polinomio di `zlib.crc32`) e della geometria
  di ritaglio a griglia (`_tile_boxes` in `balzar/qr.py`, porta fedele
  della stessa formula a punto fisso cell/pad, non una riapprossimazione
  — necessario perché, a differenza di ZBar, la libreria di decodifica
  QR lato browser trova **un solo codice per chiamata**, non una lista;
  senza ritaglio una griglia N×N leggerebbe sempre e solo il primo QR
  trovato).

**Libreria di decodifica QR lato browser: bug reale trovato con una
misura, non assunto.** Prima scelta `@paulmillr/qr` (doppia licenza
Apache-2.0/MIT, mantenuta attivamente, encode+decode in un solo pacchetto
zero-dipendenze) — **scartata dopo aver isolato un bug reale**: su una
griglia 2×2 generata dal payload PDF reale di §2.4c (24 capitoli, 6
fotogrammi), la sua `decodeQR` falliva con un errore interno
(`Cannot read properties of undefined`) su 3/24 QR altrimenti
perfettamente validi — isolato passo-passo fino a `detect()`→`transform()`
nel codice della libreria stessa, non un problema di ritaglio (lo stesso
identico crop, passato a ZBar via `pyzbar`, decodifica correttamente;
nessuna combinazione di margine/padding/opzioni ha risolto il fallimento,
segno di un bug data-dipendente nella libreria, non un errore di
geometria). Provata **jsQR** (Apache-2.0, non più mantenuta da anni, ma
matura/battle-tested) sugli stessi 24 QR: **24/24**, zero fallimenti.
Scelta jsQR nonostante la minore freschezza di manutenzione — la
correttezza misurata vince sulla frequenza di aggiornamento per una
libreria che fa solo una cosa (decodifica QR, algoritmo stabile da anni)
e il cui bug nell'alternativa "mantenuta" era già isolato e riproducibile,
non ipotetico. Vendorizzata come `jsQR.min.js` (build UMD ufficiale del
pacchetto npm `jsqr@1.4.0`, 257 KB, nessuna ricompilazione necessaria —
già pronta per un `<script>` diretto, a differenza di `@paulmillr/qr` che
avrebbe comunque richiesto un bundle con `esbuild` essendo distribuita
solo come moduli ESM/CJS).

**Verificato end-to-end con Playwright, non solo scritto**: upload del
PDF reale di §2.4c (51.318 B) nella sezione Codifica → 6 pagine QR
generate via `/api/qr` (server) → le stesse 6 immagini ripassate alla
sezione Leggi **in ordine invertito** (prova diretta dell'indipendenza
dall'ordine) → riassemblaggio client-side completo, **bit-identico**
all'originale (SHA256 confrontato, non solo la dimensione). Verificato
anche lato desktop (screenshot reali sotto Xvfb dei due tab, e un
round-trip completo pilotando direttamente i worker thread della
finestra con un file arbitrario non-balzar da 7.680 B — stato dei label
e del bottone "Salva" verificati, byte ricostruiti bit-identici).

Test aggiunti: `tests/test_raw_qr_logic.py` (3 test: round-trip
encode→assemble su byte arbitrari via `payload_to_qr_frames`/
`LiveScanner`, un path immagine già processato viene ignorato non
riletto, uno scan parziale segnala correttamente i capitoli mancanti e
`result()` solleva `ValueError` se richiamato prima del completamento) —
skippato se `qrcode`/`pyzbar` non installati, stesso principio di
`test_qr.py`. Nessun test Python per `trasporto-qr.js` (comportamento
client-side puro, stesso principio già seguito per il resto della UI 3D:
verifica Playwright manuale in sessione, non nella suite automatica).

**Non ancora fatto**: nessun test automatico Playwright committato nel
repository per questa pagina (verifica manuale one-off in sessione,
stesso principio già seguito altrove per JS); nessuna opzione di
rilevamento automatico di `grid_dim` lato lettura (l'utente deve saperlo
e impostarlo uguale a come è stato generato — un valore sbagliato non
corrompe nulla, semplicemente non trova QR, dichiarato esplicitamente
nell'interfaccia).

### 2.4f Allineamento pre-acquisizione-continua: motore JS condiviso + bug reale di geometria trovato portandolo

Richiesta diretta di sessione, prima di iniziare la parte più ambiziosa
("acquisizione continua" via fotocamera, vedi il piano a valle): questa
sessione aveva proposto in autonomia `@undecaf/zbar-wasm` per la
decodifica lato browser, basandosi solo su una ricerca sullo stato di
manutenzione delle librerie — **senza** testarla contro le griglie QR
reali di balzar. L'utente ha segnalato che un'altra sessione aveva già
affrontato esattamente questo problema per il trasporto QR di byte
grezzi (§2.4c/§2.4d): testato `@paulmillr/qr` (libreria "attivamente
mantenuta", la stessa categoria di scelta che questa sessione stava per
rifare), trovato un bug reale e riproducibile (3/24 QR falliti con un
errore interno su una griglia reale), scartata in favore di **jsQR**
(non più mantenuta ma provata 24/24 sugli stessi QR). Raccomandazione
`zbar-wasm` **ritirata esplicitamente**: stesso errore metodologico che
il principio guida del progetto vuole evitare ("misura, non stimare") —
corretto riusando jsQR, già vendorizzata e già provata, invece di
introdurre una seconda dipendenza di decodifica QR non misurata.

**Estrazione, non riscrittura**: `qr-transport-core.js` (nuovo file)
contiene ora CRC32/BZC1/`LiveScanner`/`tileBoxes`/`decodeAllInImage`,
estratti **senza modifiche di comportamento** da `trasporto-qr.js`
(che si accorcia da ~280 a ~100 righe, mantenendo solo il wiring DOM
specifico della pagina) — così un secondo consumatore (l'acquisizione
continua via fotocamera, il prossimo passo del piano) può riusare lo
stesso motore invece di scriverne una terza copia. Verificato con un
round-trip reale contro un devserver che instrada `/api/qr` al vero
`handle_qr` (non un mock): upload di un file arbitrario da 52.944 B
attraverso la UI reale di `trasporto-qr.html` (non uno script isolato),
grid_dim=2, 7 pagine generate, rilette **in ordine invertito** — SHA256
bit-identico all'originale, zero regressioni dall'estrazione.

**Bug reale trovato portando `_tile_boxes` in JS**, non nella semplice
traduzione ma verificandola con Playwright su una vera griglia
grid_dim=4 (16 QR/frame, il default di balzar — il test precedente in
§2.4d aveva esercitato solo grid_dim=2): sia `_tile_boxes` (Python) sia
il suo porting JS assumevano `rows = grid_dim` incondizionatamente,
ma il `top` di `_compose_grid` è una **costante fissa** (26 con
un'etichetta "Frame i/N", 0 senza), mai derivata dal numero di righe —
un frame parziale finale ha quasi sempre meno righe di `grid_dim` anche
a parità di colonne (es. 12 codici residui a `grid_dim=4` sono 4
colonne × 3 righe, non 4×4). Misurato prima del fix: `_decode_tiled`
recuperava **1/16** invece dei 12 reali su un frame di questo tipo — un
crollo quasi totale, non un errore marginale. **Il bug esisteva già nel
codice Python originale**, ma era mascherato in silenzio dal fallback
whole-image di ZBar (mai una perdita di correttezza lato Python, solo
di velocità — l'euristica del tiling è sempre stata "solo un
suggerimento, mai un requisito", §2.4b punto 6): la mascheratura non
regge per jsQR, che non ha un fallback whole-image multi-decode
altrettanto affidabile (`decodeAllViaMasking` è il tentativo JS più
vicino, ma misurato meno affidabile — vedi sotto). Fix, in entrambi i
linguaggi: `rows` non più assunto ma **derivato algebricamente
dall'altezza nota dell'immagine**, provando ciascuno dei due valori
possibili di `top` (26, 0) e tenendo quello che ricostruisce
esattamente l'altezza data.

**Un secondo bug, introdotto e corretto nella stessa sessione mentre si
sistemava il primo**: spostando il controllo di completamento dentro
`_decode_tiled` stesso, la prima versione confrontava il conteggio
totale piatto (`len(results) == len(boxes)`) — che ha rotto un test
preesistente (`test_decode_tiled_end_to_end_still_recovers_full_frame`)
perché ZBar può legittimamente produrre **due** risultati per una sola
cella (una lettura spuria di un'altra simbologia di codice a barre nel
margine di testo dell'etichetta, comportamento innocuo già documentato
altrove — filtrato a valle dal prefisso `CHUNK_MAGIC`), facendo
apparire `len(results) > len(boxes)` e quindi far scartare un decode
altrimenti perfettamente riuscito. Corretto controllando il
completamento **per cella** (ogni cella tentata ha prodotto almeno un
risultato, `cells_with_a_result == len(boxes)`) invece che sul
conteggio piatto.

**Limite di affidabilità di jsQR per singolo crop, reale e accettato,
non un bug da inseguire**: anche con la geometria corretta, su un
frame parziale reale da 12 QR, jsQR ne manca costantemente **1 su 12**
(lo stesso identico crop, passato a ZBar/Python, decodifica senza
problemi) — misurato ripetutamente (3 run consecutivi, stesso esito).
`decodeAllInImage` (JS) gestisce questo diversamente da `_decode_tiled`
(Python): **non scarta** i risultati parziali del tiling quando non è
completo al 100% (a differenza del comportamento tutto-o-niente di
Python), perché il fallback whole-image di jsQR
(`decodeAllViaMasking`) è esso stesso inaffidabile su un'immagine
piena — scartare 11 decodifiche buone per guadagnare 0 sarebbe una
perdita netta. La dichiarazione di identità di ogni chunk è comunque
autodescrittiva (indice/CRC in BZC1, via `LiveScanner`), quindi
accumulare un risultato genuinamente parziale da un'immagine e
completarlo da una foto/frame successivo è già il modello d'uso
previsto per questo formato — un frame a cui manca un solo codice non
è un fallimento, è lo stesso flusso "aggiungi un'altra foto" già
esposto altrove nel progetto, e la ragione diretta per cui
l'acquisizione continua (molti tentativi nel tempo, non una singola
foto statica perfetta) è il passo naturale successivo.

Verificato: suite Python invariata (309 test, tutti verdi,
`tests/test_qr.py` incluso — 24/24), sintassi JS controllata
(`node --check` su entrambi i file), tre run consecutivi del test
grid_dim=4 reale (16/16 sul frame pieno in ogni run, confermando il fix
di geometria; 11/12 costante sul frame parziale, confermando il limite
di affidabilità jsQR come caratteristica stabile e non rumore).

### 2.4g Componente di cattura fotocamera continua (`qr-camera-scanner.js`) — nessun tocco, e un vincolo di risoluzione reale scoperto misurando

`qr-camera-scanner.js` (nuovo file) — `class ContinuousQrScanner`,
il pezzo di libreria per l'"acquisizione continua" decisa in sessione:
punta un vero stream `getUserMedia()` a `decodeAllInImage`/`LiveScanner`
(§2.4f) con un loop a intervallo minimo (default 350ms, guardia `busy`
contro decodifiche sovrapposte — `requestAnimationFrame` diretto a
~60/s accoderebbe chiamate jsQR sincrone che possono costare centinaia
di ms), accumula i capitoli via `LiveScanner` esattamente come il flusso
foto-singola già esistente, e chiama `onComplete` da solo appena
l'ultimo capitolo arriva — **zero tocchi dell'operatore**, il requisito
esplicito della sessione (rifiutato "avanza al tocco": con frame che
cambiano ogni ~1,5s su uno schermo che cicla da solo, sincronizzare un
tocco umano è cattiva UX). Riusa `qr-transport-core.js` **senza
modifiche**: nessuna logica di parsing chunk o decodifica QR
reimplementata qui, solo la plumbing della fotocamera.

**Vincolo reale scoperto misurando, non assunto**: la prima verifica
end-to-end (fotocamera fittizia via Chromium `--use-file-for-fake-video-
capture`, vedi sotto) con una griglia `grid_dim=4` reale (lo stesso
default usato per lo scan-foto desktop) non ha mai trovato un solo QR
— `count: 0` a ogni risoluzione di camera realistica (1920×1080 e
sotto). Isolato il motivo con un vero sweep di risoluzione (non
ipotizzato): jsQR ha bisogno di circa **700-1100px di larghezza per
singolo codice QR** per decodificare in modo affidabile — un requisito
enormemente più alto del sweet spot ZBar già noto per lo scan-foto
desktop (1700-2400px per l'**intera griglia** 4×4, §9.10/§2.4b). Una
griglia `grid_dim=4` da fotocamera live avrebbe bisogno di ~3800-4700px
di larghezza inquadratura per tenere ognuno dei 16 codici sopra quella
soglia — irrealistico per una fotocamera puntata a distanza normale;
`grid_dim=2` (4 codici) resta comunque sopra soglia solo a ~1900px+,
marginale. **Solo `grid_dim=1` (un codice QR per pagina generata)** si
è dimostrato affidabile a ogni risoluzione testata, da quella nativa
fino a 640px — confermato con un vero sweep (`decodeAllInImage`
chiamato su PNG ridimensionati con Pillow/LANCZOS a 1920/1600/1280/
1080/960/800/640px, 1/1 trovato a ogni passo). Nota anche una
sensibilità di jsQR **non monotona** al ridimensionamento: in un test
isolato su un singolo crop, 1100px e 700px decodificavano correttamente
ma 900px falliva — un artefatto di resample/antialiasing, non un
degrado uniforme; qualunque margine di inquadratura scelto per la
generazione delle pagine deve restare ben lontano da quella fascia
intermedia, non solo "abbastanza grande".

`/api/qr` (`handle_qr`) clampa `grid_dim` a `[2, 8]` (§2.9) — una
policy pensata per lo scan-foto desktop, non un limite di libreria:
`payload_to_qr_frames(payload, grid_dim=1)` resta chiamabile
direttamente. La generazione della sequenza QR per l'acquisizione
continua (lato encoding, task successivo) dovrà quindi usare un
percorso diverso da quello che serve già gli altri tab, o un parametro
dedicato — non ancora deciso, rimandato all'integrazione UI.

**Verificato end-to-end con una fotocamera reale, non un mock**:
Chromium lanciato con `--use-fake-device-for-media-stream
--use-file-for-fake-video-capture=<file>.y4m`, un vero video Y4M
scritto a mano (nessun encoder ffmpeg disponibile in questo sandbox con
supporto Y4M/MJPEG — verificato con `ffmpeg -version`, solo encoder
PNG/VP8 abilitati — quindi scritto direttamente via la conversione
YCbCr già disponibile in Pillow) che simula uno schermo che cicla 5
pagine QR reali (payload casuale 10.000 B, `grid_dim=1`) a 1,5s/pagina,
1920×1080, letterbox con margine ridotto (0,95×, non 0,8× — un primo
tentativo con margine 0,8× ha spinto il codice della pagina 5 esattamente
nella fascia 800-900px non affidabile scoperta sopra, causando un
capitolo mai trovato in 8 cicli di loop consecutivi — bug del test,
non del componente, isolato confrontando byte per byte il frame Y4M
sorgente [corretto, letto e verificato "Frame 5/5" visivamente] contro
lo stesso identico PNG prima/dopo il roundtrip YUV420, che falliva
identico anche SENZA alcun coinvolgimento della fotocamera o del video).
Con il margine corretto: **scansione completa in ~6,3s, stabile su 3
run consecutivi**, tutti e 20 i tentativi di decodifica hanno trovato
esattamente 1 QR (zero tentativi a vuoto), zero errori, riassemblaggio
**bit-identico** (SHA256 verificato) — zero tocchi dell'operatore dal
primo all'ultimo fotogramma, esattamente il modello richiesto.

**Non ancora fatto**: nessuna integrazione UI (`trasporto-qr.html`,
Balzar Live, desktop) — questo è solo il componente di libreria,
verificato in isolamento con una pagina di test minimale non
committata nel repository (`getUserMedia` richiede un contesto sicuro:
`http://127.0.0.1`/`localhost` sì, `about:blank` di `page.set_content()`
no — verificato anche questo nel processo). Nessuna gestione UI di
`onError` (permesso negato, nessuna fotocamera, vincoli non
soddisfatti) oltre al callback stesso. Nessuna decisione ancora presa
su come la generazione della sequenza QR lato encoding debba esporre
`grid_dim=1` per questo caso d'uso specifico (endpoint dedicato?
parametro esplicito sull'esistente? nuovo default solo per questo
flusso?).

### 2.4h Frequenza di acquisizione: da 6,3s a ~1,7-2,3s per la stessa sequenza, misurando due leve indipendenti

Domanda diretta di sessione, seguito naturale di §2.4g: dato che
`grid_dim=1` è obbligatorio per la fotocamera (una sola pagina per QR,
molte più pagine della griglia desktop), si può almeno alzare la
frequenza di acquisizione per accorciare il tempo totale? Risposta
misurata, non stimata: **sì, e il collo di bottiglia non era
l'intervallo di polling** (`intervalMs`, già a 350ms) ma la
**risoluzione di cattura richiesta** — due leve distinte, entrambe
misurate separatamente prima di combinarle.

**Leva 1 — risoluzione di cattura**: `ContinuousQrScanner` chiedeva
1920×1080 di default. Misurato il costo reale di `decodeAllInImage` su
un singolo QR a diverse risoluzioni (stesso codice, stesso contenuto):

| Larghezza richiesta | Latenza decodifica (mediana) |
|---|---|
| 1920px | ~660ms |
| 1600px | ~460ms |
| 1280px | ~260ms |
| 1080px | ~200ms |
| 800px | ~135ms |

Il costo di jsQR scala con il numero totale di pixel scansionati, non
solo con la dimensione del singolo codice — richiedere una risoluzione
inferiore (ma sempre sopra la soglia di affidabilità, §2.4g) accelera
la decodifica **prima ancora** di toccare l'intervallo di polling.

**Leva 2 — aspect ratio della richiesta camera**: qui un bug reale,
non solo un'ottimizzazione. Il primo tentativo ha richiesto 1280×960
(il classico 4:3) — sembrava ragionevole, ma le pagine `grid_dim=1` di
balzar sono quasi quadrate (es. 1230×1278): adattarle con margine
(0,95×) dentro un'altezza di soli 960px comprime il codice a
~880-920px, **esattamente nella fascia inaffidabile** già documentata
in §2.4g. Scoperto testando **tutte e 5 le pagine** di un payload reale
(non solo la pagina 0, che per caso aveva una dimensione che a 960px
decodificava comunque bene, mascherando il problema in uno smoke test
a una sola pagina): 4 pagine su 5 fallivano sistematicamente a 1280×960.
Corretto passando a **1280×1152** (quasi quadrato, come il contenuto):
le stesse pagine finiscono a ~1050-1170px, **5/5 affidabile**, e persino
più veloce (meno pixel totali di 1280×1280). Nuovo default:
`idealWidth=1280, idealHeight=1152` (parametrizzabile via
`opts.idealWidth`/`opts.idealHeight`).

**Effetto combinato su `intervalMs`**: con la decodifica a ~200-260ms
invece di ~660ms, il vero limitatore di cadenza diventa la latenza di
decodifica stessa (la guardia `busy` impedisce comunque sovrapposizioni)
— l'intervallo minimo non serve più a rallentare deliberatamente, serve
solo da pavimento di sicurezza. Default abbassato da 350ms a **60ms**.

**Misurato end-to-end con una fotocamera reale** (stessa metodologia di
§2.4g, Chromium `--use-file-for-fake-video-capture` su un video Y4M che
cicla pagine QR reali), sullo stesso payload di test a 5 pagine:

| Durata per pagina | Tempo totale di scansione |
|---|---|
| 1,5s (originale) | ~6,3s |
| 1,0s | ~4,45s |
| 0,75s | ~3,23s |
| 0,5s | ~2,33s |
| 0,25s (pavimento del banco di test) | ~1,7-1,8s |

**~3,6× più veloce** passando da 1,5s/pagina (l'originale) a 0,5s/pagina,
con margine per 2+ tentativi di decodifica reali dentro la finestra di
ogni pagina — la raccomandazione per chi genera la sequenza a
ciclo-automatico (GIF/slideshow JS) è **0,5s/pagina**, non 0,25s: quel
pavimento è un artefatto della granularità a 4fps del banco di prova
Y4M di questa sessione (Chromium non onora in modo affidabile un F più
alto nell'header Y4M — misurato direttamente: la stessa struttura a 6
frame/pagina dichiarata a F20:1 invece di F4:1 si è bloccata a metà
sequenza per 8+ secondi reali, un artefatto del dispositivo/banco di
prova, non una velocità raggiungibile davvero), non un limite del
componente stesso — su un display reale 0,5s ha comunque margine di
sicurezza contro il jitter di temporizzazione che un test sintetico non
ha.

**Onestà sul confronto con `grid_dim=4` ("con le matrici era molto più
veloce")**: vero, e resta vero anche dopo questa ottimizzazione — 16
codici per foto contro 1 è una differenza strutturale di un ordine di
grandezza che nessuna ottimizzazione di frequenza cancella. Quello che
questa sessione ha fatto è **restringere il divario**, non eliminarlo:
un payload da 109 capitoli (lo stesso benchmark di §9.10) richiederebbe
109 pagine invece di 7 fotogrammi da 16 — ma a 0,5s/pagina invece di
1,5s, il tempo di sola visualizzazione scende da ~164s a ~55s, lo stesso
ordine di grandezza della pipeline `grid_dim=4` completa (foto+lettura,
~29-44s misurati in §2.4b/§9.10) invece di 3× più lento. Il vantaggio
reale di `grid_dim=1` non è la velocità (che resta strutturalmente
inferiore) ma **zero tocchi dell'operatore e nessuna necessità di
inquadrare l'intera griglia a distanza fissa** — un compromesso
esplicito, non un pareggio.

**Un miss deterministico trovato ripetendo il test con un payload più
grande (27 capitoli, seed fisso)**: la scansione si è fermata a 27/28
capitoli, sempre sullo stesso capitolo, riproducibile su 3 run
consecutivi. **Non è un bug nuovo**: è lo stesso limite di affidabilità
per-crop di jsQR già documentato in §2.4f/§2.4g (jsQR manca
occasionalmente un crop altrimenti valido), reso deterministico solo
dal fatto che il video di test sintetico ripete fotogrammi
bit-identici a ogni giro di loop — una fotocamera reale ha invece
micro-variazioni naturali (autofocus, tremore della mano, luce) che
danno un "secondo tiro di dadi" a ogni tentativo, esattamente il
meccanismo per cui l'acquisizione continua (molti tentativi nel tempo)
è più robusta di una singola foto statica, non riproducibile in un
banco di prova a fotogrammi identici.

Nessuna modifica a `qr-transport-core.js` in questa sessione — solo
`qr-camera-scanner.js` (nuovi default `idealWidth`/`idealHeight`/
`intervalMs`, documentati nel commento di testata del file con gli
stessi numeri sopra). Nessun test Python coinvolto (comportamento
client-side puro). Verificato: sintassi JS (`node --check`), nessuna
regressione sui test già passati con i vecchi default.

### 2.4i Integrazione in `trasporto-qr.html`: scelta esplicita generazione/lettura, e un bug reale di geometria trovato dall'integrazione stessa

Richiesta diretta di sessione: lasciare all'utente la scelta esplicita,
con pro/con dichiarati in interfaccia, tra le due modalità già esistenti
(pagine da fotografare a mano, qualunque griglia — contro GIF per
acquisizione continua, sempre griglia 1×1) sia in **generazione** sia in
**lettura**, mantenendo le griglie dense interamente disponibili per chi
non usa l'acquisizione continua.

**Generazione** (`trasporto-qr.html` sezione 1): un nuovo `<fieldset>`
con due opzioni radio (`enc-mode`), ciascuna con un pro/con di una riga.
Selezionando "GIF per acquisizione continua" il selettore di griglia
esistente si nasconde (rimane invariato e disponibile per l'altra
modalità) e la richiesta a `/api/qr` forza `grid_dim=1` internamente,
indipendentemente da cosa fosse impostato prima — non un valore
suggerito, imposto lato client perché è l'unico che l'acquisizione
continua legge in modo affidabile (§2.4g). Sblocco necessario lato
server: `handle_qr` clampava `grid_dim` a `[2, 8]` (una policy per lo
scan-foto desktop, mai un limite di libreria), che rendeva `grid_dim=1`
irraggiungibile dall'endpoint pubblico — cambiato a `[1, 8]`, con test
espliciti sia per il nuovo valore ammesso sia per il vecchio
comportamento di clamp dal basso (ora a 1, non più a 2).

**Lettura** (`trasporto-qr.html` sezione 2): stesso principio, un
`<fieldset>` (`dec-mode`) tra "Foto multiple (comando dell'operatore)"
(il flusso già esistente, qualunque griglia) e "Acquisizione continua
(fotocamera)" (nuovo, `ContinuousQrScanner` da `qr-camera-scanner.js`,
un `<video>` live + pulsanti avvia/ferma + testo di progresso). Le due
modalità **condividono la stessa `LiveScanner`** (nuovo parametro
opzionale `opts.scanner` su `ContinuousQrScanner`, che di default ne
crea una propria se non passata): un capitolo che la fotocamera non
riesce a leggere si può coprire con una foto manuale, e viceversa, senza
perdere ciò che l'altra via ha già trovato — stesso principio di
accumulo già alla base del formato, ora esteso a due meccanismi di
acquisizione invece di uno.

**Bug reale trovato dall'integrazione stessa, non dalla libreria in
isolamento**: il test di regressione della modalità "foto multiple"
falliva in modo deterministico (stesso payload, stesso seed, sempre lo
stesso esito) — **sia jsQR sia pyzbar** non trovavano nessun QR in
un'immagine che, scansionata per intero senza ritaglio, decodificava
perfettamente. Isolato passo-passo: `_tile_boxes`/`tileBoxes` provano
`top=26` prima di `top=0`, accettando la prima ipotesi che "ricostruisce
l'altezza abbastanza bene" — ma la tolleranza usata per "abbastanza
bene" era `row_h/2` (centinaia di pixel), enormemente più larga del
necessario. Su una griglia 2×2 reale a frame singolo (`top` vero = 0,
nessuna etichetta "Frame i/N"), l'ipotesi SBAGLIATA `top=26` ricostruiva
l'altezza con un errore di soli 26px — comodamente dentro quella
tolleranza troppo larga — e veniva accettata per prima, spostando ogni
ritaglio di ~26px rispetto alla posizione reale dei QR. Il risultato non
era un rallentamento (il fallback whole-image di ZBar avrebbe comunque
salvato la correttezza in Python) ma un **fallimento totale**: jsQR non
ha un fallback whole-image affidabile su una griglia multi-codice (limite
già documentato, non nuovo), quindi sia il tentativo di tiling (mal
posizionato) sia il fallback (whole-image, jsQR intrinsecamente debole su
più codici in un canvas) fallivano insieme.

Verificato con `pyzbar` **prima** di incolpare jsQR: gli stessi identici
ritagli mal posizionati, passati a ZBar invece che a jsQR, fallivano
anch'essi — la prova diretta che non era un limite di jsQR ma un errore
di geometria a monte, condiviso da entrambi i linguaggi (`_tile_boxes` in
Python e `tileBoxes` in JS hanno esattamente lo stesso bug, stessa
tolleranza `row_h/2` in entrambi). Fix in entrambi: tolleranza stretta
(2px, non `row_h/2`) — quando l'ipotesi è davvero corretta la formula
ricostruisce l'altezza **esattamente** (cell/pad/rows sono gli stessi
interi che `_compose_grid` ha usato), qualunque errore ben oltre un paio
di pixel di arrotondamento significa che l'ipotesi è sbagliata, non che
serve più margine.

Verificato: tutti e 4 i ritagli della griglia del test decodificano
correttamente dopo il fix (prima: 0/4 sia con ZBar sia con jsQR); nuovo
test di regressione in `tests/test_qr.py`
(`test_tile_boxes_uses_the_correct_top_on_a_full_single_frame_grid`,
payload dimensionato per forzare esattamente 4 capitoli — un frame
singolo, griglia 2×2 piena, senza etichetta "Frame i/N", esattamente lo
scenario del bug); suite Python 312 test, tutti verdi; verifica end-to-end
completa con Playwright contro un devserver reale che instrada `/api/qr`
al vero `handle_qr` — non solo le due modalità nuove, ma un round-trip
reale con fotocamera fittizia (`--use-file-for-fake-video-capture`,
stessa metodologia di §2.4g/§2.4h) attraverso l'interfaccia reale di
`trasporto-qr.html` (non uno script isolato): scelta modalità GIF in
generazione → GIF reale prodotta e verificata (magic bytes `GIF8`) →
scelta modalità "Acquisizione continua" in lettura → scansione completa
tramite un vero pulsante "Avvia fotocamera" → download tramite il vero
pulsante "Scarica file ricostruito" → bit-identico (SHA256), stabile su
run ripetuti.

**Bug di CSS trovato durante la verifica, stessa causa già nota di
§9.9**: sia `#enc-grid-dim-row` (classe `.dim-picker`) sia
`#enc-gif-result`/`.qr-page-item` sia `#dec-continuous-section`/
`.camera-view` hanno una regola di classe incondizionata con `display:
... `, che — per la stessa collisione di specificità già trovata e
corretta per `.qr-block` — vince sulla regola nativa `[hidden] {
display: none }`. Corretto con la stessa tecnica (`.dim-picker[hidden]`,
`.qr-page-item[hidden]`, `.camera-view[hidden]`, tutte `{ display: none;
}`), trovato scrivendo il test Playwright (`is_visible()` restituiva
`true` nonostante l'attributo `hidden` fosse impostato), non a occhio.

**Collisione di nome CSS evitata prima che causasse un bug**: il nome
ovvio per il nuovo contenitore delle due opzioni radio,
`.mode-picker`, **era già usato** da `index.html` (il toggle "Sequenza
navigabile / File indipendenti" del tab Sequenza, §2.9) con regole
incompatibili — riusarlo avrebbe silenziosamente cambiato l'aspetto di
quel toggle non correlato. Rinominato in `.qr-mode-picker`, verificato
con `grep` che nessun'altra collisione di nome esiste per le classi
nuove (`.mode-option`, `.mode-pro-con`).

### 2.4j Acquisizione continua estesa a Balzar Live (tab "Apri programma")

Seguito diretto di §2.4i: `trasporto-qr.html` (trasporto di byte
grezzi arbitrari) aveva già la scelta esplicita generazione/lettura
tra griglie dense e GIF per acquisizione continua; Balzar Live (il tab
"Apri programma" di `index.html`, quello che apre `.bzr`/`.b3d`/`.bzx`
tramite `/api/render`) no — un file scaricato da lì poteva essere
riaperto solo caricandolo di nuovo da disco, mai ricostruendolo da una
sequenza QR fotografata/ripresa dalla fotocamera. Stesso motore
(`jsQR.min.js`/`qr-transport-core.js`/`qr-camera-scanner.js`, già
vendorizzati e provati in §2.4d-§2.4h), nessun codice di decodifica
nuovo — solo wiring DOM e refactoring per riusarlo su una terza
pagina.

**Generazione**: ogni bottone "genera QR" già esistente su Balzar Live
(i tre blocchi `open`/`open-3d`/`open-docs` in `index.html`) guadagna
lo stesso checkbox "ottimizza per acquisizione continua" già visto in
`trasporto-qr.html` — se spuntato, `setupQrButton` (`app.js`) forza
`mode="gif"` e `grid_dim=1` indipendentemente da cosa mostri il
`<select>` esistente (che resta invariato e disponibile per l'altra
modalità, esattamente come in §2.4i).

**Lettura**: nuova sezione "Carica un file / Scansiona una sequenza
QR" prima della dropzone esistente, con lo stesso doppio livello di
scelta di `trasporto-qr.html` (foto multiple vs fotocamera continua).
Refactoring necessario per riusarla pulitamente: `handleOpenFile`
(che faceva sia la lettura del File sia la POST a `/api/render`) è
stato diviso in `handleOpenData(dataB64, label)` — la parte condivisa,
POST + dispatch su `json.kind` (2d/3d/bundle) — e un `handleOpenFile`
ridotto a un thin wrapper FileReader-based. Una nuova
`handleOpenScanBytes(bytes, label)` riusa `handleOpenData` esattamente
allo stesso modo per i byte ricostruiti da una sequenza QR: `/api/render`
tratta i byte come byte, indipendentemente da come sono arrivati.
`LiveScanner` è condivisa tra le due modalità di lettura (foto manuali
e fotocamera continua, tramite `opts.scanner` su `ContinuousQrScanner`,
già supportato da §2.4i) — un capitolo mancato dalla fotocamera si può
coprire con una foto manuale e viceversa.

**Verificato con Playwright contro un devserver locale reale** (stessa
metodologia già nota, non contro Vercel): toggle mostra/nasconde le
sezioni giuste; upload normale di file (nessuna regressione); checkbox
"acquisizione continua" forza davvero `mode=gif`/`grid_dim=1` nella
richiesta reale a `/api/qr` (intercettata e verificata, non assunta);
lettura manuale — payload aperto → pagine QR `grid_dim=2` generate →
le stesse pagine ricaricate **in ordine invertito** tramite il file
picker reale → file riaperto automaticamente, stessa identica
`stats` di prima; lettura continua — sequenza `grid_dim=1` reale
(5 pagine) mostrata a una fotocamera fittizia (`--use-file-for-fake-
video-capture`, stessa tecnica di §2.4g/§2.4h) → file aperto **con
zero tocchi dell'operatore**, testo del programma decodificato
verificato carattere per carattere (non solo la dimensione).

Bug reali trovati e corretti, non nel codice del progetto ma nello
script di verifica stesso — entrambi bug della sintassi Playwright/DSL
dello script, non del prodotto: sintassi DSL non valida nel fixture di
test (`PALETTE 0=0,0,0 ...` invece del vero `PALETTE i=0 rgb=#...`, un
formato chiave=valore per riga); `Request.post_data` è una proprietà
in questa versione di Playwright, non un metodo (`req.post_data`, non
`req.post_data()`). Nessun bug trovato nel codice di produzione durante
questa verifica.

Suite Python invariata (nessuna riga JS è testata da `unittest`, per
costruzione — stesso principio già seguito per il resto della UI QR/3D
di questo progetto): 315 test, tutti verdi.

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
**eseguito e verificato per la prima volta in una sessione successiva**
(vedi §9.13 per il numero reale e la conferma che `libzbar` nativo
viene incluso automaticamente, non solo il codice Python di `pyzbar`).

Verificato con screenshot reale sotto Xvfb: apertura GIF, encoding video
delta, anteprima animata, pannello statistiche, bottoni attivi, ciclo
completo esporta-QR→scansiona-foto→payload bit-identico.

Un secondo pulsante, "Scansiona con fotocamera (browser)…"
(`balzar/live_scan_server.py`, sessione successiva — vedi §9.27), copre
il caso "acquisizione continua" (zero tocchi dell'operatore, fotocamera
live) che "Scansiona foto QR" non copre (foto singole scattate a
parte): apre una pagina locale nel browser di sistema che riusa lo
stesso motore jsQR/`ContinuousQrScanner` già vendorizzato per la demo
web, e i byte ricostruiti tornano al processo desktop via un endpoint
`POST /submit` sullo stesso server HTTP effimero — nessuna dipendenza
nativa di cattura video (OpenCV o simili) aggiunta al progetto.

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
sei i tab dove esiste un payload: riusa `balzar/qr.py` esattamente
com'è. A differenza della *lettura* di un QR (`pyzbar`/`libzbar0`,
nativa, mai esposta sul web demo — serve un ambiente con quella
libreria di sistema), la *generazione* usa solo `qrcode`, puro Python +
Pillow: nessuna nuova dipendenza di sistema, sicuro da aggiungere a
`requirements.txt` per Vercel. Verificato non solo visivamente ma con
un vero round-trip ZBar in sessione: screenshot del QR generato dalla
pagina → `pyzbar.decode` → `assemble_chunks`/`decode_payload` →
programma bit-identico all'originale caricato.

**Tre modalità di export, scelta esplicita dell'utente (sessione
successiva)** — domanda diretta: "la demo web può produrre anche le
sequenze QR e le matrici? l'utente può scegliere?". Risposta onesta al
momento della domanda: **no**, `handle_qr` chiamava solo
`payload_to_qr_image` — singola griglia auto-dimensionata, nessun tetto
sul numero di codici per immagine (una griglia 14×14 per il payload 3D
reale da 178 capitoli, §2.4b/§9.10 — "inutile e inutilizzabile", parole
dell'utente, correttamente: mai pensata per essere fotografata o
proiettata a dimensione leggibile). `payload_to_qr_frames`/
`frames_to_gif`/`frames_to_files` esistevano già, verificate da test,
ma erano **solo funzioni di libreria**, mai raggiungibili da un
bottone. Fix, nel bottone "genera QR" di ogni tab, ora con un
`<select>` a monte:
- **"Immagine singola (griglia unica)"** — comportamento originale
  invariato, default per compatibilità;
- **"Sequenza QR — GIF animata"** — `payload_to_qr_frames(grid_dim=4)`
  + `frames_to_gif`, una GIF che cicla i frame per uno schermo che la
  riproduce da solo;
- **"Sequenza QR — pagine PNG"** — stesso split, restituito come lista
  di PNG separati (un `<div class="qr-page-item">` per pagina, ognuno
  col proprio bottone di download), per la stampa su carta dove
  "auto-play" non ha senso.

`handle_qr` guadagna i parametri `mode` (`single`/`gif`/`pages`,
default `single`) e `grid_dim` (default 4, **clampato lato server a
[2, 8]**: un valore assurdo come 100 da un endpoint pubblico produrrebbe
un'immagine composta enorme, va sempre validato anche se il valore
tecnico non ha limiti nel codice di libreria). Stesso principio
`_omitted` già usato altrove nel modulo (PNG/GLB) applicato all'output
GIF/pagine: la dimensione del payload **sorgente** non basta a limitare
l'output (misurato: un payload di 500KB può gonfiarsi a una GIF di 9MB
per 7 frame, §9.10), quindi `gif_omitted`/`pages_omitted` ricontrollano
la dimensione reale della risposta contro `max_payload_b64_bytes` prima
di restituirla, evitando di sforare il limite di risposta di Vercel
(~4.5MB) con un payload sorgente che di per sé passava il controllo.

**Numero reale misurato** (payload sintetico di test, 41.143 B, 19
capitoli): la vecchia modalità unica produce un'immagine da **5862×4792
px** (19 QR in una sola griglia, illeggibile a qualunque dimensione
fisica ragionevole); la nuova modalità sequenza (`grid_dim=4`) la
spezza in **2 frame da 4704×4818 px** ciascuno (16+3 QR), la stessa
dimensione già misurata come "piena risoluzione, affidabile" nel
benchmark di §2.4b/§9.10 — non una stima, lo stesso frame è stato
generato e verificato.

Verificato end-to-end con Playwright contro un server locale che
instrada `/api/qr` al vero `handle_qr` (stessa metodologia, non un
mock): upload di un programma sintetico che produce quel payload da
41KB tramite il tab "Apri programma" → modalità "singola" invariata
(sanity check) → modalità GIF: risposta reale con `n_frames=2`,
`qr_gif_base64` scaricato e riaperto con Pillow, **confermato
2 frame reali nel file GIF** (non una singola griglia travestita) →
modalità pagine: galleria con 2 elementi, ciascuno scaricabile
singolarmente, prima pagina riaperta e verificata (4704×4818 px, PNG
reale). Nessun bug trovato in questa verifica (a differenza della
scoperta `toBlob`/`toDataURL` di §9.14). Aggiunti 7 test in
`tests/test_webapi.py::TestHandleQr` (modalità gif/pages, split
multi-frame per un payload grande, `grid_dim` fuori range clampato,
`mode` sconosciuto rifiutato con 400, roundtrip reale via ZBar +
`LiveScanner` attraverso la modalità pagine, omissione della risposta
oltre il limite) — 223 test totali.

**Non ancora fatto**: CLI e GUI desktop non espongono ancora le tre
modalità (solo la demo web); `LiveScanner`/la lettura multi-frame
restano solo funzioni di libreria senza un comando `balzar scan`
dedicato per leggerle indietro (nessun flag `--live`, verificato nel
codice di `cli.py` prima di scriverlo in questa nota — non inventato).

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

### 2.9b `landing.html` — pagina marketing separata dalla demo funzionante

Richiesta diretta di sessione: `index.html` è la demo funzionante a sei
schede (§2.9), non pensata per convertire un visitatore che arriva da un
link esterno e non sa ancora cosa sia balzar — nessuna pagina del
progetto raccontava in un colpo solo il caso d'uso guida (§6 punto 1,
manutenzione industriale) con un messaggio, una prova visiva e una CTA.
Decisione di scope confermata esplicitamente con l'utente prima di
costruire (via `AskUserQuestion`): pagina **separata** (`landing.html`,
non diventa la root del sito, `index.html`/`vercel.json` **invariati** —
zero rischio sul flusso esistente), messaggio guidato dal caso
manutenzione/CAD, CTA primaria "Prova la demo" verso `index.html`.

**Nuovi file**: `landing.html` + `landing.css` (foglio di stile
dedicato, **non** `style.css` — deliberato: `style.css` è già stato
sorgente di bug di specificità CSS ripetuti in questo progetto, es. §9.9/
§9.20/§9.29, tutti dovuti a regole condivise tra pagine diverse che
collidevano; una pagina di marketing con un linguaggio visivo diverso
[hero, badge statistici, grid di card] non condivide componenti con la
UI applicativa, quindi non ha motivo di condividerne il CSS) +
`landing-img/` (PNG **reali**, non mockup: renderizzati con
`balzar render`/`balzar chunks --qr` dagli stessi file in `examples/`
già usati altrove nel progetto — `schema-tecnico.png` per l'hero,
`etichetta-bom.png` + `etichetta-bom-qr.png` come prova appaiata
immagine/QR nella sezione numeri, `viewer-3d-search.png` per la sezione
Balzar Live, vedi sotto). Nessuna dipendenza nuova: le uniche librerie
usate per generare gli asset (`qrcode`, Pillow) servivano solo in fase
di build degli asset statici, non sono richieste a runtime dalla pagina.

**Bug reale di deformazione dell'immagine, trovato e corretto dopo il
primo feedback dell'utente**: il QR nella sezione "prove" appariva
allungato in verticale, non quadrato. Causa isolata misurando il DOM,
non indovinata: la regola globale `img { max-width: 100%; }` non aveva
`height: auto`, quindi quando il layout a flexbox della riga
immagine+QR restringeva la larghezza dell'elemento sotto l'attributo
HTML `width="290"`, l'altezza restava fissa a `height="290"` (letta
dall'attributo) mentre la larghezza si riduceva — la stessa distorsione
si sarebbe potuta ripresentare su qualunque immagine futura stretta in
un contenitore più piccolo dei suoi attributi HTML. Fix in due parti:
`height: auto` aggiunto alla regola globale `img` (corregge la classe
di bug, non solo il QR), e `.proof-visual` passato da `flex` a
`display: grid` con colonne proporzionali esplicite (`1.2fr 1fr`) per
un dimensionamento più prevedibile del riquadro QR, con l'immagine del
QR stessa vincolata a `aspect-ratio: 1/1` come ulteriore garanzia
indipendente dagli attributi HTML. Verificato leggendo
`getBoundingClientRect()` prima/dopo il fix (123×290px deformato →
123×123px quadrato) e visivamente su desktop/mobile.

**Sezione "pattern band" rimossa su richiesta esplicita**: il testo
("ogni piastrella di questo sfondo è calcolata al volo...") accompagnava
uno sfondo decorativo a bassa opacità di `pattern_tile.bzr`, ma preso
fuori contesto — senza aver appena visto il resto della pagina — non
comunicava nulla di comprensibile. Rimossi la sezione HTML, le regole
CSS `.pattern-band*` e il PNG `landing-img/pattern-tile.png` (diventato
inutilizzato), invece di lasciare CSS/asset morti nel repository.

**Contenuto**: hero con CTA verso `index.html`; fascia statistiche;
sezione "problema" (officina senza rete/licenza CAD); "come funziona" a
3 step con l'analogia spartito già usata in `come-funziona.html`;
sezione prova con la stessa tabella RGB/PNG/ZIP/payload di §8 (559 B
contro 998.400 B RGB, unico che entra in un QR) accompagnata dal QR
reale generato dallo stesso payload; riquadro onestà/Kolmogorov (stesso
principio "dichiara invece di nascondere" del resto del progetto, con
l'esempio reale del rumore 0,7× che non comprime); due card Balzar
Studio/Balzar Live (stesso contenuto di `VISIONE.md` §2); griglia
applicazioni (5 delle 6 di `VISIONE.md` §3 — la musica/notazione
simbolica omessa perché lì stessa esplicitamente "zero lavoro
iniziato", non coerente con il tono "solo capacità reali" scelto per
questa pagina); CTA finale; footer con link a demo/come-funziona/
trasporto-qr/repository GitHub (lo stesso URL pubblico già linkato da
`come-funziona.html`, non un link nuovo).

Verificato con Playwright contro un server locale (`http.server`, non
Vercel — stessa limitazione di rete già nota, §2.9): desktop/dark-mode/
mobile (390px) senza overflow orizzontale, tutti i link interni
risolvono 200, gli anchor `#come-funziona`/`#prove`/`#onesta`/
`#applicazioni`/`#balzar-live` scrollano al target giusto, nessun errore
console. Un bug reale trovato e corretto durante la verifica: il badge
statistico sovrapposto all'immagine hero (posizionato in basso a
sinistra) copriva la seconda riga della didascalia sottostante —
spostato in alto a destra, dove non collide con nessun testo.

**Sezione "Balzar Live in azione" (3D + ricerca allarmi), aggiunta dopo
un secondo giro di feedback**: la prima bozza copriva il viewer 3D solo
di striscio (un elenco puntato dentro la card "Balzar Live" del
confronto Studio/Live). L'utente ha chiesto di dargli più peso — è la
funzionalità con la demo visiva più forte del progetto (click-to-select,
ricerca libera, BOM collegata, §9.11/§9.15/§9.29) e non aveva ancora
una prova visiva reale sulla landing. Aggiunta una sezione dedicata
subito dopo la fascia statistiche (prima di "In officina...", quindi la
seconda cosa che un visitatore vede dopo l'hero), con uno screenshot
reale del viewer, non un mockup disegnato a mano.

**Prima versione**: un assieme 3DXML sintetico costruito ad hoc (una
flangia + 4 bulloni, geometria a scatole scritta a mano) più un CSV
allarmi a 4 colonne, impacchettati in un vero bundle `.bzx` e aperti con
la vera `balzar.viewer3d.open_bundle_in_browser`. **Sostituita su
richiesta esplicita dell'utente** con uno screenshot dello stesso
assieme 3DXML industriale reale già usato per la verifica end-to-end di
§9.10/§9.12/§9.21/§9.30 (skid con vasche di accumulo/riscaldo, 88 forme
uniche, 245 posizionamenti-foglia — fornito di nuovo in sessione,
**non incluso nel repository** per lo stesso motivo di copyright già
seguito per gli altri assiemi reali di quelle sezioni): risultato
nettamente più credibile di una geometria a scatole disegnata a mano —
lista BOM lunga e realistica visibile nel pannello, silhouette
riconoscibile di un impianto vero. CSV allarmi con nomi di componenti
reali estratti dalla BOM (`RESISTENZA_DU_SCATOLA`, `VASCA_RISCALDO`,
`QUADRO_EL_DU` — nomi di parte generici, non part number proprietari)
invece di quelli inventati della flangia sintetica. Nessuna delle
interazioni mostrate nello screenshot è finta: pilotata con Playwright
(ricerca reale digitata in `#search-input`) contro il server locale che
`open_bundle_in_browser` avvia per davvero. `landing-img/
viewer-3d-search.png` cattura lo stato dopo la ricerca del codice
`E102`: il quadro elettrico evidenziato in arancione nel modello
(silhouette dell'intero skid attenuata sullo sfondo), riga
`QUADRO_EL_DU ×1` selezionata nella distinta base — visibile
contemporaneamente nello stesso screenshot, prova diretta del
collegamento 3D↔BOM↔ricerca — tabella dei risultati con le 4 colonne
del CSV sotto. Card presentata come un finto "browser frame" (barra con
tre pallini + pillola URL, solo CSS — `.browser-frame`/`.browser-chrome`
in `landing.css`) per segnalare visivamente che è un'interfaccia reale
in un browser, non un'illustrazione.

**Sezione "intro" aggiunta prima dell'hero, su richiesta esplicita**:
l'hero originale apriva già con un esempio concreto (schema tecnico +
QR), ma non c'era nessuna riga che spiegasse **cos'è balzar** a un
livello più alto, senza tecnicismi, prima di mostrare la prova. Nuova
`<section class="intro">` in cima a `<main>` — solo testo centrato,
niente immagine/card/CTA, massima sobrietà ("Non salviamo i tuoi
disegni. Li rigeneriamo." + una riga di spiegazione senza menzionare
QR/byte/payload, lasciati al resto della pagina) — seguita da un
divisorio (`border-top` su `.hero`) per segnalare visivamente il
passaggio da "concetto" a "esempio concreto".

**Correzione di gerarchia semantica, non solo estetica**: l'intro
diventa l'unico `<h1>` della pagina (era l'hero prima); il titolo
dell'hero scende a `<h2 class="hero-title">` con le stesse identiche
regole tipografiche di prima (spostate dal selettore `h1` a
`h1, .hero-title` in `landing.css`, incluso `em` per la parola
evidenziata in accento) — nessuna regressione visiva, solo un
documento con una struttura di intestazioni corretta (un solo h1, h2
per le sezioni principali, h3 per le sotto-sezioni, invariato altrove).

### 2.10 CLI

`balzar render|encode|encode-image|encode-video|decode|info|chunks|scan|assemble|gui`
— vedi `balzar/cli.py` per l'elenco completo con esempi in `README.md`.

### 2.11 Test

342 test, tutti verdi (`python3 -m unittest discover -s tests`):
`test_determinism.py`, `test_ops.py`, `test_expansion.py`, `test_encoder.py`,
`test_qr.py` (skippato automaticamente se `qrcode`/`pyzbar` non sono
installati — dipendenze opzionali, non nel motore core),
`test_video.py`, `test_svg.py`, `test_vectorio.py`, `test_sequence.py`,
`test_explode.py`, `test_webapi.py`, `test_png.py`, `test_cli.py`,
`test_scene3d.py` (parser 3DXML, formato binario `BZM1`, export glTF —
vedi §9.5), `test_viewer3d.py` (`parse_alarm_csv` per la barra di
ricerca/tabella allarmi del viewer 3D — vedi §9.15), `test_bundle.py`
(formato `BZX1`, dispatch per estensione, transito byte-identico
attraverso il chunking QR — vedi §9.16), `test_library.py` (libreria
locale persistente di Balzar Live: logica pura file/JSON, isolata via
`BALZAR_LIBRARY_DIR` — vedi §9.22/§9.23), `test_raw_qr_logic.py`
(trasporto QR di byte arbitrari, nessun motore balzar — vedi §2.4d),
`test_live_scan_server.py` (protocollo HTTP puro del ponte browser→
desktop per l'acquisizione continua fotocamera, nessun Tkinter/browser
reale — vedi §9.27).
Copertura: round-trip
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
5. ~~PyInstaller non testato~~ — **fatto in una sessione successiva**
   (§9.13): build Linux reale, 23.325.664 B, GUI lanciata sotto Xvfb con
   screenshot reale, `libzbar` nativo incluso automaticamente. Resta da
   ripetere la build su Windows/macOS reali (non disponibili in questo
   sandbox) per confermare le stesse dimensioni/comportamento — quella
   parte resta non verificata.
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
13. **"3D filtered mode"** (nome scelto in sessione): mostrare solo gli
    assiemi di primo livello nominati dal disegnatore, nascondendo
    sotto-codici/sotto-assiemi che possono essere informazione
    riservata (part number proprietari, dettagli costruttivi interni).
    Proposto e discusso, esplicitamente **rimandato** a valutazione
    futura, non ancora iniziato. Punto tecnico chiave emerso nella
    discussione, da tenere presente quando si riprende: **nascondere
    solo nella UI del viewer non basta** — il `.glb` scaricabile
    contiene comunque nomi e gerarchia completi di ogni sotto-parte,
    ispezionabili da chiunque con un viewer glTF generico o un editor
    di testo (è JSON+binario). Una vera riservatezza richiederebbe
    unire la geometria sotto il livello scelto già in fase di export
    (`scene3d.py`/`gltf.py`, non un filtro lato client) — le sotto-parti
    nascoste diventerebbero una singola mesh anonima, senza nomi né
    materiali distinti, con il costo esplicito di perdere il
    click-to-select per quelle sotto-parti specifiche (un compromesso
    riservatezza-vs-interattività, non un dettaglio implementativo
    gratuito).

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

### 9.12 Nomi generici "Object N" nella BOM: confermato, corretto

Domanda diretta di sessione: l'utente ha notato che la BOM del test
mostra "Object 1, Object 2, Object 3..." mentre il file originale (nel
suo CAD) mostra un involucro con un codice reale e "Object X" solo
all'interno. Verificato sul file XML grezzo, non a memoria: sì,
confermato esattamente. Estratto un esempio concreto dal documento
principale del `.3dxml`:

```
Reference3D id="4" name="VASCA_ACCUMULO_SUB009"   (nessuna forma propria)
  -> Instance3D id="11" (senza nome)
    -> IsInstanceOf -> Reference3D id="6" name="Object 13"  (qui la geometria)
```

Un `Reference3D` "prodotto" con un nome reale e leggibile
(`VASCA_ACCUMULO_SUB009`) avvolge — tramite un singolo `Instance3D`
senza nome proprio — il `Reference3D` che porta davvero la geometria,
etichettato genericamente dall'esportatore CAD ("Object 13", assegnato
dal software, non dall'ingegnere). `generate_bom`/`gltf.py` prendevano
il nome della foglia (quello con la geometria), non quello
dell'involucro — da cui "Object N" invece del codice reale.

**Verificato sistematicamente su tutto il file**, non solo
sull'esempio: **tutti i 245 posizionamenti-foglia reali** seguono
esattamente questo schema (un involucro con un solo figlio che è esso
stesso una foglia), e **nessuna delle 88 forme uniche sottostanti** è
mai raggiunta da due involucri con nomi diversi — quindi preferire il
nome dell'involucro qui è una correzione univoca, non un'euristica
rischiosa.

**Fix**: nuova funzione `effective_display_name(parent, ref)` in
`scene3d.py`, condivisa da `generate_bom` e da `gltf.py` (stessa fonte
di verità già usata per la sincronizzazione BOM↔click di §9.11) — **non**
"preferisci sempre l'involucro quando esiste un solo figlio": la prima
versione faceva così ed è stata trovata rotta dagli stessi test
automatici già esistenti, prima ancora di toccare un file reale.
Un involucro a singolo figlio è un pattern comune anche per parti già
correttamente nominate (es. `SubGroup` che avvolge `PartB`, dal fixture
sintetico di `tests/test_scene3d.py`) — sovrascrivere sempre avrebbe
perso un nome già buono in favore di uno meno specifico, e avrebbe
persino sovrascritto il placeholder esplicito "(senza nome, ...)" di
una foglia genuinamente senza nome con il nome di un antenato non
correlato. Corretto restringendo il trigger al pattern esatto osservato
(`re.fullmatch(r"Object \d+", ref.name)`): scatta solo quando il nome
della foglia è **esattamente** quello che lo strumento CAD genera in
automatico, mai quando la foglia ha già un nome vero (assegnato da un
umano) o nessun nome affatto.

Verificato: rieseguendo `generate_bom` sul file reale, le prime righe
della BOM sono ora `VASCA_ACCUMULO_SUB009`, `VASCA_ACCUMULO_SUB004`,
`VASCA_ACCUMULO_SUB008`, ... — codici reali, non più "Object N". Due
nuovi test in `tests/test_scene3d.py`
(`test_auto_generated_object_n_name_prefers_wrapper_name`,
`test_already_meaningful_leaf_name_is_not_overridden_by_wrapper`) — 216
test totali.

### 9.13 Licenze, commercializzazione, dimensione reale dell'eseguibile desktop

Tre domande dirette di sessione, verificate con dati reali invece che a
memoria.

**Licenza di `model-viewer` e rischio legale**: il file vendorizzato
(`model-viewer.min.js`, build UMD 4.3.1) contiene commenti `@license`
con `SPDX-License-Identifier: BSD-3-Clause`/`MIT` per alcuni file
interni (probabilmente utility matematiche prese da three.js con la
loro attribuzione originale preservata) — a prima vista in contraddizione
con quanto già scritto in §9.4 ("Apache 2.0"). Verificato alla fonte per
non fidarsi della sola grep sul minificato: il pacchetto npm
`@google/model-viewer` cache-ato in questa sessione da un test
precedente ha `package.json` con `"license": "Apache-2.0"` e un vero
file `LICENSE` con il testo della Apache License 2.0. Conclusione
corretta: la licenza del **pacchetto nel suo complesso** (quella che
conta per l'uso/redistribuzione) è Apache-2.0; i commenti BSD/MIT
interni sono attribuzioni preservate per singoli file presi in prestito,
non licenze in conflitto. **Apache-2.0 è permissiva**: uso commerciale
libero, nessun copyleft, richiede solo di mantenere l'avviso di
copyright/licenza — nessun rischio legale noto a includerlo in un
prodotto commerciale.

**Le altre dipendenze**, verificate via metadata pip reali (non a
memoria):

| Dipendenza | Licenza | Nota |
|---|---|---|
| Pillow | MIT-CMU (verificato nel file LICENSE installato) | permissiva |
| qrcode | BSD | permissiva |
| pyzbar (wrapper Python) | MIT | permissiva |
| **libzbar (libreria C nativa)** | **LGPL 2.1** | vedi sotto |

`libzbar` è l'unica dipendenza non permissiva-pura del progetto, e solo
per la funzionalità QR opzionale (non nel motore core). LGPL 2.1
**permette l'uso commerciale/proprietario**: l'obbligo riguarda solo la
libreria LGPL stessa (renderla sostituibile/rilinkabile, fornirne
licenza e sorgente), non il codice proprio che la usa. `pyzbar` la
carica dinamicamente via `ctypes` (mai linkata staticamente) — la via
più semplice per restare in regola con LGPL, verificato che è
esattamente questo il meccanismo che l'eseguibile PyInstaller reale usa
(vedi sotto: `libzbar.so.0` bundlata come file binario separato, non
fusa nel codice).

**`balzar` stesso non ha un file LICENSE** nel repository: nessun
vincolo esterno sulla commercializzazione del codice scritto in questo
progetto — è dell'utente, la scelta di licenza/termini è sua. La
domanda "posso commercializzare il prodotto" ha quindi risposta onesta
in due parti: il codice proprio, sì, nessun vincolo trovato; le
dipendenze vendorizzate/usate, sì, tutte permissive o LGPL-con-linking-
dinamico-già-rispettato.

**Dimensione reale dell'eseguibile desktop — build vera eseguita in
sessione, non stimata** (mai fatto prima in questo progetto, criticità
§4.5): `pyinstaller --onefile --windowed --name balzar balzar-app.py`
su Linux (nessun ambiente Windows/macOS disponibile in questo sandbox,
quindi non lo stesso binario che si otterrebbe lì, ma un proxy reale
dello stesso ordine di grandezza) —

| Metrica | Valore |
|---|---|
| Dimensione eseguibile | **23.325.664 B (~22,2 MiB)** |
| `libzbar.so.0` nativa inclusa? | **sì**, confermato in `PKG-00.toc`: `('libzbar.so.0', '/lib/x86_64-linux-gnu/libzbar.so.0', 'BINARY')` — bundlata come file binario separato (coerente con l'uso LGPL sopra), non solo il wrapper Python |
| Lancio reale sotto Xvfb | riuscito, screenshot reale con GUI completa (tutti i pulsanti visibili: Apri file, Scansiona foto QR, Salva payload, Esporta QR, Visualizza in 3D) |

Risponde a un dubbio esplicito lasciato aperto da mesi in questo
documento (§4.5, prima di questa verifica: "da verificare che includa
anche la libreria nativa libzbar"). **Non ancora fatto**: build reali
su Windows/macOS (richiedono quegli ambienti, non disponibili qui) —
il numero sopra è un proxy Linux, non una garanzia di dimensione
identica sugli altri sistemi operativi.

**App Android: nessun numero reale possibile**, e onestamente
nessuno stimabile. A differenza del desktop, il packaging mobile non è
mai stato iniziato (§5 punto 3: "nessun lavoro di packaging mobile nel
codice oggi") — nessun prototipo Kivy/BeeWare, nessuna build APK
tentata. Qualunque cifra data ora sarebbe inventata, non misurata:
dichiarato onestamente come "non esiste ancora", non stimato a caso.

### 9.14 "Esporta scheda ricambio": isolare una parte, stamparne una vista + codice

Domanda diretta di sessione, seguito naturale del click-to-select di
§9.11: una volta isolato un componente difettoso, il tecnico può
"stampare" (scaricare) una vista di quel componente più il suo
identificativo, per richiedere il ricambio? Verificato prima il punto
sui metadati: il file 3DXML reale non porta altri campi utili oltre al
nome (`V_discipline`/`V_usage`/`V_nature` sono boilerplate costante
identico sulle 88 parti, `Author`/`Created`/`Title` sono solo a livello
documento, non per-parte) — quindi la scheda esportabile è
deliberatamente minima: un'immagine e un codice, non un generatore di
report con campi che il formato sorgente non fornisce.

Implementazione, nel modo più semplice possibile come richiesto: con
una parte selezionata (isolata via `materialFromPoint`, §9.11), un
bottone "Esporta scheda ricambio" cattura la vista corrente del
`<model-viewer>` (già isolata/attenuata così com'è mostrata), la
disegna su un `<canvas>` con un header bianco che riporta il nome
della parte e "Quantità nell'assieme: N" (letto dallo stesso
`data-part-count` già presente sulle righe della BOM), e scarica il
risultato come PNG (`scheda_<nome>.png`) — stessa idea di
`exportPartSheet`/`threedExportPartSheet` in entrambe le interfacce
(`balzar/viewer3d.py` per la GUI desktop, `app.js` per la demo web),
duplicata invece che condivisa per lo stesso motivo già documentato in
§9.11 (una è incorporata in un f-string Python, l'altra è uno script
statico).

**Bug reale trovato durante la verifica, non nella logica del bottone
ma nell'API di cattura scelta.** Il primo tentativo ha usato
`model-viewer.toBlob({idealAspect:true})` (asincrona, con ritaglio
all'aspect ratio ideale) — sembrava la scelta giusta perché è l'API
pensata apposta per l'export ("idealAspect" è un'opzione documentata).
Misurato invece: **cattura sempre completamente trasparente/vuota**,
esattamente **1.699 byte ogni singola volta**, indipendentemente da
attese fino a 5+ secondi, retry, ordine di scroll-into-view prima/dopo
il caricamento del modello, o CSS (`border-radius` rimosso per
escludere un clipping). La consistenza esatta del numero di byte è
stata la prova che non fosse una race condition di timing (che avrebbe
dato risultati variabili) ma un problema strutturale della
funzione stessa. Ispezionato il sorgente minificato di
`model-viewer.min.js` per capire perché: `toBlob()` internamente
disegna su un **canvas offscreen separato** con un calcolo di ritaglio
e poi un blocco `finally` che richiama un resize interno — uno di
questi passaggi produce un buffer vuoto in questo layout specifico.
`toDataURL('image/png')` (sincrona, va dritta a
`displayCanvas().toDataURL()`, nessun canvas offscreen, nessun
ritaglio) sullo **stesso elemento, nello stesso momento**, ha prodotto
contenuto reale in modo affidabile: 36.671–73.165 byte a seconda della
scena, verificato ripetutamente. Fix: sostituita `toBlob` con
`toDataURL` in entrambe le implementazioni — si perde il ritaglio
automatico all'aspect ratio ideale (costo estetico, l'immagine include
lo sfondo scuro intero del viewer invece di un ritaglio stretto attorno
al modello), guadagno di correttezza netto: una cattura che contiene
davvero il modello invece di un rettangolo vuoto scaricato con successo
ma inutile.

Verificato end-to-end con Playwright, non solo scritto: click su una
parte reale (fixture 3DXML sintetica, stessa usata da
`tests/test_scene3d.py`) → bottone abilitato solo dopo una selezione →
download → PNG riaperto e ricontrollato per contenuto non-vuoto (conteggio
di colori distinti campionati sull'immagine, non solo la dimensione in
byte). Ripetuto due volte, stessa metodologia già consolidata nel
progetto: una sulla GUI desktop (`viewer3d.py`, servito in locale via
`http.server`) — cattura **27.677 B**, 9 colori distinti campionati; e
una **end-to-end reale sulla demo web**, upload vero attraverso un
devserver locale che instrada `/api/encode_3d` al vero `handle_encode_3d`
(non un mock) — cattura **17.011 B**, 14 colori distinti campionati.
Nessun test Python automatico aggiunto (comportamento client-side puro,
stesso principio già seguito per il resto della UI 3D in §9.11: verifica
Playwright manuale in sessione, non nella suite automatica). Suite
Python invariata a 216 test, tutti verdi.

### 9.15 Ricerca componente + tabella allarmi (aiuto alla manutenzione)

Proposta diretta di sessione, seguito naturale del click-to-select
(§9.11): un operatore che legge un codice di allarme sulla macchina
oggi deve sapere già come si chiama il componente CAD coinvolto per
poterlo cliccare/cercare nella BOM. Aggiunta una barra di ricerca al
viewer 3D (desktop e demo web) che chiude quel salto: digitando un
nome componente **o** un codice di allarme (se è stata caricata una
tabella di corrispondenza), il componente giusto si isola ed evidenzia
esattamente come un click diretto — stessa logica, un ingresso in più.

**Meccanismo, riusa quanto già esisteva invece di duplicarlo**: il
click su una riga BOM isolava già un nome; generalizzata quella logica
da "un nome" a "un insieme di nomi" (`highlightNames(names[])` in
`viewer3d.py`, `threedHighlightNames` in `app.js`) — un singolo
click/ricerca-per-nome passa un insieme con un solo elemento, un
allarme che coinvolge più parti ne passa diversi, stesso codice in
entrambi i casi. Il pulsante "Esporta scheda ricambio" (§9.14) resta
disabilitato quando la selezione contiene zero o più di un nome: una
scheda ricambio è la foto di UNA parte, un allarme con più parti
coinvolte non ha "la" parte su cui stampare una scheda.

**Tabella allarmi**: CSV a due colonne (`codice_allarme,nome_componente`,
un allarme può comparire su più righe se coinvolge più parti).
Caricabile in due modi, entrambi supportati, per due esigenze diverse:
- **upload manuale nel browser** (client-side, nessun round-trip
  server — un parser CSV plain-text scritto a mano in JS, dichiarato
  onestamente senza supporto per virgole tra virgolette: una tabella a
  due campi non giustifica un parser RFC4180 completo) — comodo per
  provare subito una tabella senza rigenerare nulla;
- **incorporata alla generazione della pagina** (solo GUI desktop,
  `open_glb_in_browser(..., alarm_rows=...)`, nuova funzione
  `parse_alarm_csv()` con lo stesso parser ma via `csv` di stdlib,
  quindi con supporto vero per virgole tra virgolette) — questa è la
  via che rende possibile l'automazione (sotto): una pagina generata
  una volta con la tabella già incorporata può essere riaperta con un
  parametro URL, senza upload manuale.

**Automazione — `?q=<codice>` nell'URL**: al caricamento del modello,
la pagina legge `?q=` dalla propria URL e lancia la ricerca da sola,
zero interazione umana. Combinato con l'incorporazione della tabella
alla generazione (sopra), questo è il pezzo che chiude il flusso
descritto in sessione: "operatore legge un codice sulla macchina, lo
digita, vede il componente" diventa "qualunque cosa sappia costruire
un URL apre la pagina già con quel codice e il componente si evidenzia
da solo". Implementato e verificato (sotto) sia su desktop sia sulla
demo web; sulla demo web la stessa automazione ha un limite onesto:
ogni sessione richiede comunque un upload fresco del file 3D prima che
un modello sia caricato, quindi un `?q=` in arrivo su una pagina vuota
non ha nulla su cui agire — l'incorporazione alla generazione (e quindi
l'automazione a zero-click vera) resta specifica del viewer desktop,
dove la pagina esiste già pre-generata e può essere solo riaperta.

**Meccanismi di automazione proposti, non implementati in questa
sessione** (per tenere lo scope al meccanismo di ricerca in sé, che è
quello richiesto):
1. **Endpoint locale sul server già presente**: `open_glb_in_browser`
   avvia già un `http.server.HTTPServer` locale per servire la pagina
   (§9.9) — estendere quell'handler con una piccola route (es. `POST
   /set_alarm?code=E100`) e far sì che la pagina già aperta la legga
   (polling breve o Server-Sent Events, nessuna libreria nuova, tutto
   stdlib) aggiornerebbe la vista **senza ricaricare la pagina** — più
   fluido di un redirect a `?q=` su un monitor a muro sempre acceso,
   ma richiede uno stato condiviso client/server che oggi non esiste.
2. **Watcher di un sistema PLC/SCADA reale**: un piccolo processo che
   osserva il tag/registro di allarme attivo della macchina (via
   OPC-UA, un log, un file condiviso — dipende dall'impianto specifico,
   informazione che non abbiamo per questo progetto) e traduce il
   nuovo codice in una chiamata all'endpoint del punto 1, o in una
   navigazione del browser a `?q=<codice>`. Questo è il pezzo che
   servirebbe per il flusso "zero digitazione" completo descritto in
   sessione — non iniziato, richiede di sapere con quale sistema reale
   si integra prima di poter scrivere codice sensato (non un dettaglio
   da indovinare).
3. **QR fisico sul quadro allarmi**: un QR che codifica direttamente
   l'URL `.../viewer.html?q=<codice>` (non un payload `BZC1` — un
   normale URL in un QR, uso diverso della stessa libreria `qrcode`
   già nel progetto) affisso vicino al pannello comandi della macchina:
   l'operatore fotografa il codice invece di leggerlo e digitarlo.
   Economico da provare (nessun codice nuovo, solo generare N QR con
   `qrcode.make(url)` per gli N codici di allarme noti), non
   implementato perché richiede l'elenco reale degli allarmi di un
   impianto specifico per avere senso.

Verificato con Playwright, non solo scritto, su entrambe le interfacce
con una fixture 3DXML sintetica a due parti distinte (stesso principio
già seguito per le altre feature del viewer 3D — nessun file CAD reale
nel repository): ricerca per nome esatto/parziale, ricerca per codice
allarme con corrispondenza a una sola parte e a più parti (evidenzia
tutte, pulsante "esporta scheda" correttamente disabilitato),
corrispondenza case-insensitive dei codici, `?q=` nell'URL che lancia
la ricerca da solo al caricamento (sia con la tabella incorporata sul
desktop, sia dopo un upload+CSV manuale sulla demo web), upload di una
CSV che sostituisce la tabella precedente (non la unisce — comportamento
dichiarato, non nascosto), messaggio onesto quando niente corrisponde.
Nessun bug trovato in questa verifica. Aggiunto anche un vero test
Python (`tests/test_viewer3d.py`, 7 test) per `parse_alarm_csv` — la
sola parte di questa feature che non è JS lato client, quindi l'unica
testabile senza Playwright (righe vuote/corte scartate, riga di
intestazione riconosciuta ed esclusa solo quando è davvero
un'intestazione, un codice con più righe/componenti, nomi con virgole
tra virgolette preservati correttamente dal parser Python — a
differenza del parser JS lato client, che dichiara esplicitamente di
non supportarlo).

### 9.16 Bundle multi-documento: 3D + tabella allarmi in un solo QR/file

Domanda diretta di sessione: si possono codificare più documenti
insieme (esempio posto dall'utente: 3D + CSV allarmi + 2 tavole PDF) in
un solo giro, un solo QR/sequenza, con dispatch automatico a più
viewer alla scansione? Risposta separata in due parti perché il
meccanismo e i PDF hanno risposte molto diverse — vedi la valutazione
di sessione precedente per il perché i PDF restano **esplicitamente
fuori scope** (nessun encoder PDF nel progetto, e un vero disegno
tecnico pesa abbastanza da vanificare il "sta in pochi QR" che rende
utile il supporto fisico). Questa sezione copre solo il meccanismo di
bundle + dispatch multi-viewer per 3D+CSV, costruito per davvero.

**Formato `BZX1` (`balzar/bundle.py`, nuovo modulo)**: diversi
sotto-documenti tipizzati concatenati in un solo blob —

```
b"BZX1" | u16 versione | u16 n.elementi | u32 lunghezza-corpo
        | u32 crc32(corpo) | deflate(corpo)

corpo = concatenazione di elementi, ciascuno:
  u8 lunghezza-kind | kind ascii       ("3d" o "csv")
  u8 lunghezza-label | label utf-8     (es. nome file originale)
  u32 lunghezza-dati | dati            (bytes nativi dell'elemento:
                                         BZM1 già codificato per "3d",
                                         testo UTF-8 per "csv")
```

**L'intuizione architetturale che rende questo pulito**: il livello
QR/chunking (`chunk_payload`/`payload_to_qr_frames`/`LiveScanner` in
`payload.py`/`qr.py`) tratta già qualunque payload come byte opachi
con un CRC — non serve **nessuna modifica** a `qr.py` per farci
transitare un bundle invece di un `BZM1`/`BZR1` nudo. Verificato non
solo a parole: `tests/test_bundle.py::TestBundleThroughQrCarrier`
spacchetta un bundle in `chunk_payload`, lo rimescola, lo riassembla
con `assemble_chunks`, e conferma byte-identico all'originale.

**Un solo passaggio di compressione sul corpo intero**, non uno per
elemento: ogni formato nativo già si autoverifica al proprio decode
(`BZM1` ha già la propria lunghezza+CRC), e comprimere una volta la
concatenazione intera sfrutta meglio la ridondanza tra elementi di N
passaggi separati — stesso principio già usato da `BZM1`/`BZR1` per i
propri corpi.

**Scoperta reale, non ottimistica, sul guadagno di dimensione**:
misurato su una fixture di test reale (assieme sintetico a 2 parti +
CSV a 3 righe), il bundle **non comprime — pesa più della somma delle
parti separate**: BZM1 da solo 162 B, CSV da solo 64 B (somma 226 B),
bundle risultante **290 B** (+28%). Scomponendo il perché: il corpo
grezzo con framing pesa già 282 B (56 B in più della somma — quasi
tutto per i due nomi-file usati come `label`, non per l'intestazione
BZX1 stessa, che è fissa a 16 B), e la compressione lo riduce solo a
274 B (+16 B di intestazione = 290) perché **il BZM1 al suo interno è
già compresso** — comprimere di nuovo byte quasi-incomprimibili non
guadagna quasi nulla, lo stesso principio già noto per cui ri-zippare
un PNG guadagna solo il 10% (criticità §8). **Il valore del bundle non
è la dimensione, è la convenienza**: un solo file/QR/scan invece di
due, con il viewer già wired — dichiarato onestamente invece di
vendere una compressione che non esiste. Il gap percentuale si riduce
comunque all'aumentare della dimensione reale dell'assieme 3D (l'unico
costo fisso è il framing per elemento, dell'ordine delle decine di
byte per label — trascurabile su un payload di centinaia di KB), ma
non è stato rimisurato su un assieme reale grande in questa sessione:
dichiarato come ragionamento qualitativo, non una nuova misura.

**Dispatch multi-viewer alla "scansione"**: `open_bundle_in_browser`
(nuova funzione in `balzar/viewer3d.py`, GUI desktop) spacchetta il
bundle, decodifica l'elemento "3d" in GLB+BOM con lo stesso percorso
già esistente (`scene3d.decode_payload` + `gltf.scene3d_to_glb` +
`generate_bom`), e passa l'elemento "csv" (se presente) come
`alarm_rows` a `open_glb_in_browser` — la stessa pagina/ricerca di
§9.15, ora popolata **senza upload manuale**: chi apre il bundle vede
subito il viewer 3D con la ricerca per codice allarme già pronta.
Nessuna nuova UI: è la stessa pagina di sempre, solo alimentata da un
bundle invece che da un GLB nudo. Un bundle con più di un elemento "3d"
è valido per il formato ma il viewer ne mostra solo il primo — dichiarato
esplicitamente nell'errore, non ignorato in silenzio; un bundle senza
nessun elemento "3d" è rifiutato con un messaggio chiaro (il viewer 3D
non ha senso senza un 3D da mostrare).

**Superfici collegate**:
- **CLI**: `balzar encode-bundle assembly.3dxml alarms.csv -o out.bzx`
  (dispatch per estensione: `.3dxml`/`.b3d` -> elemento 3D, `.csv` ->
  elemento CSV; qualunque altra estensione, incluso `.pdf`, viene
  rifiutata con il nome del file e il motivo esatto — mai saltata in
  silenzio, a differenza di `encode_independent` in `sequence.py`, che
  qui non si applica: un bundle è un piccolo insieme deliberato, non
  un mucchio scorrelato).
- **GUI desktop**: nuovo bottone "Crea bundle (3D + CSV)…" (due
  dialog di selezione file, il secondo — il CSV — annullabile per un
  bundle solo-3D), e riconoscimento del magic `BZX1`/estensione `.bzx`
  nel flusso "Apri file" esistente (`_worker`), cosicché un bundle
  salvato si riapra esattamente come un file `.b3d`, con
  `job.alarm_rows` popolato dal bundle e usato in "Visualizza in 3D"
  con priorità sulla tabella caricata manualmente via "Carica tabella
  allarmi" (quest'ultima resta come fallback, non sovrascritta se il
  bundle non porta un proprio CSV).
- **Demo web**: il tab "Assemblee 3D" guadagna un campo file opzionale
  "Tabella allarmi da includere nel bundle" **prima** della dropzone
  3D — se compilato al momento dell'upload, `handle_encode_3d` (esteso,
  non un endpoint nuovo) impacchetta i due in un `BZX1` e restituisce
  `bundled: true` + `alarm_rows` già estratti; il frontend popola la
  ricerca **senza un secondo upload CSV lato client** (a differenza
  del percorso manuale di §9.15, che resta disponibile invariato per
  chi vuole provare una tabella diversa senza rigenerare il payload).
  Lo stesso bottone "genera QR" già esistente funziona senza modifiche
  (conferma diretta della tesi architetturale sopra): il payload
  restituito è semplicemente più grande e contiene un bundle invece di
  un BZM1 nudo, invisibile al generatore di QR.

**Verificato con Playwright, non solo scritto**, su entrambe le
interfacce con la stessa fixture sintetica a due parti (nessun file
CAD reale, stesso principio di §9.11/§9.15): bundle generato e aperto
→ ricerca per codice allarme funziona **al primo tentativo**, zero
upload manuale, un allarme a più componenti evidenzia entrambi e
disabilita "esporta scheda ricambio" (coerente con §9.15); verificato
anche il percorso di **non-regressione** — un upload senza CSV
selezionato produce `bundled: false` e il vecchio upload manuale del
tab funziona esattamente come prima. Aggiunti: `tests/test_bundle.py`
(11 test: round-trip, corruzione rilevata, dispatch per estensione con
errore che nomina il file, transito byte-identico attraverso
chunk/riassemblaggio), 2 test in `tests/test_cli.py`, 4 test in
`tests/test_webapi.py::TestHandleEncode3D` per il nuovo campo
`alarm_csv` (non-bundling di default, bundling, decodifica coerente
della scena, base64 malformato onestamente rifiutato con 400) — 247
test totali.

**Non fatto in questa sessione, dichiarato esplicitamente**: nessun
supporto PDF (per scelta, vedi sopra); nessun modo di aprire/dispacciare
un `.bzx` dalla demo web se non passando dal tab "Assemblee 3D" al
momento della codifica — il tab generico "Apri programma" resta
specifico per `BZR1` 2D, non esteso a `BZX1` (avrebbe richiesto
insegnargli a mostrare GLB+BOM, fuori dallo scope di questa richiesta);
nessuna UI per un bundle con più di un elemento "3d" o con più CSV
combinati in modi diversi dal semplice "unisci tutte le righe" già
implementato in `open_bundle_in_browser`. **Molti di questi limiti sono
stati poi superati** in una sessione successiva — vedi §9.17 (documenti
generici consultabili, bundle senza 3D, indice navigabile).

### 9.17 Documenti generici + indice navigabile (bundle come insieme di documenti)

Domanda diretta di sessione, generalizzazione di §9.16: il CSV (o altri
formati) di un bundle può NON essere una tabella allarmi ma un semplice
documento contestuale, consultabile ma non collegato al 3D? E si può
avere un indice navigabile dei documenti estratti dal QR? Sì. Due
decisioni di scope confermate con l'utente prima di costruire (via
`AskUserQuestion`): (1) **il 3D diventa opzionale** — un bundle di soli
documenti, senza 3D, è valido e apre una pagina indice-only; (2)
**consultazione inline per i formati semplici** (testo txt/md/log, CSV
come tabella, immagini png/gif/svg/jpg/webp/bmp), **download per gli
strutturati** (html/xml/json/pdf/dxf/binari) — nessuna anteprima finta,
stessa onestà di `svg.py` che rifiuta ciò che non sa rappresentare.

**Il modello del bundle passa da "3D + tabella allarmi" a "insieme di
documenti con ruoli"** (`balzar/bundle.py`). Il `kind` è un RUOLO, non
un tipo di file:
- `KIND_3D` (`"3d"`) → il viewer + BOM;
- `KIND_ALARM` (`"alarm"`, prima `"csv"`; `is_alarm_kind()` accetta
  ancora il vecchio tag per retro-compatibilità) → cablato alla ricerca;
- `KIND_DOC` (`"doc"`, nuovo) → documento generico consultabile, nel
  solo indice navigabile, **non** collegato al 3D.

**Il ruolo è sempre esplicito, mai indovinato dall'estensione**: un
`.csv` è una tabella allarmi solo se marcato tale (`encode_bundle_files(
paths, alarm_paths=...)`, flag `--alarm` in CLI, campo dedicato in
GUI/web); un `.csv` non marcato è un semplice documento. Il tipo di
contenuto di un doc è dedotto dall'estensione della sua label **a
tempo di visualizzazione** (client-side), non memorizzato nel formato —
i byte del doc sono trasportati grezzi, nessun parsing che possa fallire.
Il formato binario `BZX1` **non cambia** (stessi campi kind/label/dati);
cambia solo l'insieme dei valori di `kind` ammessi e il fatto che un 3D
non è più obbligatorio.

**Indice navigabile + rendering inline** (`_DOC_JS` in `viewer3d.py`
per il desktop, logica gemella in `app.js` per la demo web — duplicata
per lo stesso motivo già documentato di `_SELECT_JS`): ogni elemento
alarm/doc diventa una voce cliccabile con un badge di ruolo; il click
apre il contenuto inline (testo in `<pre>`, CSV come `<table>` con lo
stesso parser split-semplice dichiarato altrove, immagini come `<img>`
data-URI) oppure, per un formato strutturato, lo scarica onestamente
invece di mostrare un'anteprima vuota. La pagina è **unica e
parametrica** (`_render_viewer_page`): la sezione 3D (model-viewer +
controlli + BOM + ricerca) è presente solo se c'è un GLB, l'indice solo
se ci sono documenti — così un bundle di soli documenti rende una
pagina indice-only e un assieme puro rende esattamente la vecchia
pagina 3D (verificato che il percorso non-bundle è invariato).

**Superfici**: `open_bundle_in_browser` gestisce tutti i casi (3D+docs,
solo-3D, solo-docs) da un unico ingresso; la GUI desktop apre `.bzx` di
soli documenti (bottone "Visualizza documenti", canvas placeholder
"bundle di documenti") e "Crea bundle" ora prende 3D (opzionale) +
allarmi (opzionale) + documenti multipli (opzionale); CLI
`encode-bundle ... --alarm FILE` marca la tabella allarmi, ogni altro
non-3D è un documento, nessun 3D richiesto; demo web con un campo
"documenti aggiuntivi" a selezione multipla nel tab 3D, `handle_encode_3d`
esteso per impacchettarli e restituire `documents` (base64) +
`alarm_rows`, il frontend costruisce lo stesso indice.

**Verificato con Playwright, non solo scritto**, su fixture sintetiche
(3DXML a 2 parti + txt + csv + png + pdf): sul viewer desktop sia
**3D+documenti** (model-viewer presente, indice a 5 voci incl. allarmi,
click su txt→testo inline, csv→tabella a 3 righe, png→`<img>`) sia
**solo-documenti** (nessun model-viewer, indice a 4 voci, stessa
consultazione inline); sulla demo web reale (upload vero attraverso il
devserver che instrada al vero `handle_encode_3d`): `bundled: true` con
5 documenti, indice reso, anteprime inline testo/tabella/immagine, e la
ricerca allarmi ancora cablata dal bundle; sulla GUI desktop sotto Xvfb
un `.bzx` di soli documenti riconosciuto (`is_bundle` true, `is_3d`
false, bottone e etichette corretti). Il formato strutturato (pdf) cade
sul download in tutti i casi, come dichiarato.

Test: `tests/test_bundle.py` aggiornato al nuovo dispatch (alarm
marcato vs doc generico, formato arbitrario come doc, bundle di soli
documenti, alarm non-UTF8 rifiutato col nome file); `tests/test_cli.py`
(alarm marcato, bundle di soli documenti con formato arbitrario, errore
pulito se `--alarm` non è tra gli input); `tests/test_webapi.py`
invariato per il campo `alarm_csv`, il campo `documents` verificato via
Playwright (comportamento client-side per il rendering, backend coperto
dal round-trip del bundle). PDF resta fuori scope come encoder (solo
trasporto grezzo, §9.16).

### 9.18 Tavole 2D nel bundle: `KIND_2D`, rigenerate al volo (non salvate come pixel)

Seguito diretto di sessione: nel bundle, un file `.bzr`/`.bzp` (un
programma/payload balzar 2D) può essere una "sottoapplicazione" a sé,
con un visualizzatore dedicato invece di finire nel generico `KIND_DOC`
(che offrirebbe solo il download, dato che `.bzr`/`.bzp` non sono
formati che un browser sa mostrare). Nuovo ruolo `KIND_2D`
(`balzar/bundle.py`): il file viene riconosciuto per estensione
(`.3dxml`/`.b3d` restano 3D, `.bzr`/`.bzp` sono 2D, nessuna ambiguità —
diversamente dal CSV che richiede la marcatura esplicita `--alarm`
perché un CSV può essere sia tabella allarmi sia documento generico).

**Il punto architetturale centrale, coerente con tutto il resto del
progetto**: il bundle porta il *programma* (bytes `BZR1`), non
un'immagine. La rigenerazione in PNG/GIF/SVG avviene al **momento
dell'apertura del viewer** (`viewer3d._render_2d_item`, sia sulla GUI
desktop sia — al momento della codifica — sulla demo web in
`handle_encode_3d`), non viene mai salvata nel bundle stesso. Stesso
principio "descrivi, non memorizzare i pixel" già alla base di tutto
balzar, applicato qui a un documento dentro un bundle invece che al
payload principale.

**Riuso totale del codice client-side esistente, zero JavaScript
nuovo**: gli item PNG/GIF/SVG generati ricevono un'estensione reale
come label (`tavola.png`, `tavola.gif`, `tavola.svg`) — lo stesso
percorso di anteprima-immagine già scritto per l'indice documenti
(§9.17) li riconosce e mostra automaticamente, senza che `_DOC_JS`/
`app.js` debbano sapere che quell'immagine viene da un programma balzar
invece che da una foto. Un programma a un solo frame produce un PNG
(più un SVG se il programma sta nel sottoinsieme vettoriale-sicuro di
`svg.py` — `UnsupportedForSVG` viene silenziata: niente SVG non è un
errore, è onestamente dichiarato omettendo semplicemente quella voce
dall'indice); un programma multi-frame produce un GIF animato (nessun
SVG in quel caso, `svg.py` rifiuta esplicitamente i programmi
multi-frame).

**Validazione reale, non solo tokenizzazione**: il primo tentativo
validava un `.bzr` solo con `canonical()` (usato da `encode_payload`)
— scoperto **insufficiente** scrivendo il test dell'errore: un
programma con un'istruzione inesistente (`BOGUS x=1`) veniva accettato
silenziosamente, perché `canonical()` tokenizza `key=value` ma non
verifica che l'istruzione sia registrata in `ops.py` — quel controllo
avviene solo eseguendo davvero il programma. Fix: `encode_bundle_files`
ora chiama `interpreter.render()` per davvero prima di accettare un
`.bzr`, catturando `SyntaxError`/`ValueError`/`RuntimeError` con il nome
del file — stesso livello di rigore già usato per la validazione
`.3dxml` (parsing completo, non un controllo superficiale). Un `.bzp`
(payload già codificato) resta invece validato solo sul magic byte
`BZR1`, coerente con lo stesso livello di fiducia già dato a un `.b3d`
già codificato (l'eventuale corruzione più fine emerge comunque al
momento della vista, con un errore chiaro, non un crash).

**Superfici**: CLI (`encode-bundle assembly.3dxml tavola.bzr -o
out.bzx`, nessun flag nuovo, dispatch automatico per estensione); GUI
desktop (il picker "documenti aggiuntivi" già generico in `create_bundle`
accetta `.bzr`/`.bzp` senza alcuna modifica al codice — l'unico punto
toccato è `bundle.py`); demo web (lo stesso campo "documenti aggiuntivi"
del tab 3D, `handle_encode_3d` esteso per riconoscere `.bzr`/`.bzp` tra
i documenti caricati e chiamare `viewer3d._render_2d_item` lato server
prima di rispondere, così il frontend riceve PNG/GIF/SVG già pronti
senza dover eseguire l'interprete lato browser — cosa che non esiste,
essendo l'interprete scritto in Python).

Verificato con Playwright, non solo scritto, su entrambe le interfacce
con tavole sintetiche (un rettangolo/cerchio a un frame, un programma
a due frame con `FRAME`): indice con `tavola.png`+`tavola.svg` per il
caso a singolo frame, `tavola.gif` (niente `.svg`) per il multi-frame,
contenuto reale non vuoto in tutti e tre i formati (`data:image/...`
con byte reali). Aggiunti 8 test in `tests/test_bundle.py`
(`TestKind2D` + `TestRender2DItem`: dispatch per estensione, bundle
3D+2D, payload `.bzp` portato verbatim, istruzione sconosciuta
rifiutata col nome file, PNG+SVG per un frame singolo, GIF senza SVG
per multi-frame, solo PNG per un programma fuori dal sottoinsieme
vettoriale come `NOISE` — 22 test totali nel file), 2 in
`tests/test_cli.py`, 3 in `tests/test_webapi.py::TestHandleEncode3D`.

### 9.19 "Balzar Live" — valutato, non ancora implementato, ma meno lontano di quanto sembri

Proposta esterna ricevuta in sessione (due documenti di specifica, tecnico e
prodotto): un "sotto-prodotto" **Balzar Live** che collega il contenuto
statico generato da balzar (modello/esploso/BOM/documenti) allo **stato
reale della macchina**, letto in tempo reale via protocollo industriale
(OPC UA, Modbus TCP, MQTT, REST), per far scattare automaticamente
highlight/ricerca quando arriva un codice di allarme — invece che
l'operatore lo digiti a mano nella barra di ricerca (§9.15).

**Perché non è la stessa proposta già scartata in §7.2.** Il "gemello
digitale UI runtime" era stato respinto perché richiedeva che il *motore*
di balzar (DSL/interprete) leggesse stato esterno a runtime — cosa che
l'architettura non ammette per costruzione (niente condizionali, seed
cotto nel payload, determinismo totale, vedi `dsl.py`). Balzar Live non
tocca quel confine: lo stato live resta interamente **fuori** dal motore
balzar, in un orchestratore esterno; balzar continua a generare solo
contenuto statico deterministico, esattamente come oggi. L'orchestratore
si limita a richiamare un'API di visualizzazione già esistente
(`highlightNames()` in `viewer3d.py`/`app.js`, §9.11/§9.15) al posto di un
click o di una digitazione umana.

**Scope deliberatamente ridotto rispetto ai documenti originali, deciso
in sessione**: **niente animazione, niente movimento assi nel viewer** —
la proposta originale includeva `viewer.moveAxis(...)`/
`viewer.playAnimation(...)`, scartati esplicitamente per non introdurre
uno stato/motore di animazione che oggi non esiste in `viewer3d.py` (il
viewer mostra una scena statica + eventuale navigazione frame-per-frame
già presente per le sequenze, §2.3/§2.9 — non un player di animazioni
guidato da eventi live). La visualizzazione resta quella di oggi
(rotazione/zoom/isolamento/ricerca/BOM/indice documenti, §9.9-§9.17),
**invariata**. L'unica estensione concessa: **colonne aggiuntive nella
tabella allarmi con un riferimento a un documento-procedura**, così un
evento allarme può sia evidenziare il componente (già esistente) sia
aprire automaticamente il documento di procedura collegato (già
esistente come voce `KIND_DOC` nell'indice bundle, §9.17) — nessuna
primitiva nuova nel viewer, solo un secondo campo opzionale nel CSV già
letto da `parse_alarm_csv()` e un secondo trigger sulla stessa
infrastruttura di ricerca-e-apertura già scritta.

**Mappatura onesta tra i nomi proposti e ciò che esiste già nel
codice**, perché i due documenti originali usano una terminologia
(Balzar Studio/Core/Live/Runtime/Bridge) che non collima con niente di
scritto finora e rischierebbe di far sembrare tutto da rifare da zero:

| Nome nella proposta | Cosa è davvero, oggi |
|---|---|
| Balzar Studio (encoder/decoder, creazione contenuti) | l'insieme già esistente: CLI (`balzar/cli.py`), GUI desktop (`balzar/gui.py`), demo web (§2.9) — nessun nome nuovo necessario nel codice, è un'etichetta di prodotto/marketing sopra ciò che c'è |
| Balzar Core | il motore deterministico (`grid.py`/`ops.py`/`dsl.py`/`interpreter.py`) + i vari encoder (`encoder.py`/`vectorio.py`/`video.py`/`scene3d.py`) — già così chiamato implicitamente in questo documento |
| Balzar Live Runtime (ricostruzione + viewer offline) | **esiste già**: `balzar/viewer3d.py` (desktop) + `index.html`/`app.js` (demo web), con click-to-select (§9.11), ricerca allarmi (§9.15), indice documenti (§9.17), tavole 2D (§9.18) |
| Balzar Bridge (connessione PLC/SCADA, driver protocollari) | **l'unico pezzo genuinamente nuovo** — zero righe di codice oggi. Corrisponde esattamente al punto 2 già annotato in §9.15 ("watcher di un sistema PLC/SCADA reale... non iniziato, richiede di sapere con quale sistema reale si integra") |

**Cosa servirebbe davvero per costruire il solo Bridge** (non
implementato, elenco di scoping onesto):
1. Estendere `parse_alarm_csv`/il formato CSV con colonne opzionali
   aggiuntive (es. `codice,componente,documento_procedura`) — piccola
   estensione retrocompatibile, non un nuovo formato.
2. Un endpoint locale sul server già avviato da `open_glb_in_browser`
   (già `http.server`, stdlib) che riceva un evento (`POST
   /set_alarm?code=...`) e lo giri alla pagina già aperta — nessuna
   libreria nuova per questa parte.
3. Un driver per protocollo (OPC UA/Modbus/MQTT/REST), ciascuno una
   **nuova dipendenza opzionale** (es. `asyncua`, `pymodbus`,
   `paho-mqtt`) — esplicitamente **fuori** dal vincolo "stdlib pura" che
   vale per il motore core (§1): quel vincolo riguarda `balzar/` come
   motore di generazione, non un eventuale layer Bridge, che è un
   prodotto satellite e può avere dipendenze proprie, dichiarate come
   tali e mai infiltrate nel motore.
4. **Read-only imposto architetturalmente, non solo dichiarato**: ogni
   driver dovrebbe esporre solo metodi di lettura nell'interfaccia verso
   il Runtime (nessun metodo di scrittura raggiungibile), stessa
   disciplina già seguita altrove nel progetto per fallire esplicito
   invece che implicito (es. `_tile_boxes` in `qr.py`, §2.4b).

**Cosa di questo documento resta volutamente fuori scope, marcato
speculativo e non una roadmap impegnata** (stesso trattamento già dato
ad altre idee esterne in §7): app Android dedicata, modalità AR, "gemello
digitale leggero", plugin SCADA/MES, supporto Ethernet/IP e Profinet.
Nessuna di queste ha un percorso di implementazione concreto oggi, e
elencarle come fasi 2-4 di una roadmap tecnica (come nei documenti
originali) darebbe un'impressione di pianificazione che non esiste —
restano idee valutate, non impegni, esattamente come STEP in §7.3.

**Stato**: valutata, non implementata. Nessun modulo `bridge.py` nel
repository, nessuna dipendenza a protocolli industriali installata,
nessun formato CSV allarmi esteso. Vedi anche il documento di visione
separato (vedi §11) per il posizionamento di prodotto (Balzar Studio /
Balzar Live) — questa sezione resta il riferimento tecnico su cosa
esiste davvero e cosa mancherebbe.

### 9.20 Demo web riorganizzata in Balzar Studio / Balzar Live; "Apri programma" diventa un apritore generico

Seguito diretto di §9.19: una volta stabilita la coppia di prodotto
Balzar Studio (crea) / Balzar Live (consulta), la demo web (`index.html`)
aveva ancora sei schede appiattite senza quella distinzione visibile, e
la sesta scheda ("Apri programma") apriva **solo** `BZR1` — un payload
`.b3d` (`BZM1`, §9.5) o un bundle `.bzx` (`BZX1`, §9.16) scaricati dalle
altre schede non potevano essere riaperti da lì, l'unico modo per
rivederli era rigenerarli da capo con lo stesso upload sorgente. Due
cambi, entrambi nella stessa direzione (rendere esplicito ciò che era
già vero nell'architettura):

**1) Riorganizzazione visiva, nessun cambio di funzionalità.** Le
cinque schede di codifica (Comprimi immagine/Vettoriale/Video/Sequenza/
Assemblee 3D) sono ora raggruppate sotto un'etichetta "Balzar Studio ·
crea", la sesta sotto "Balzar Live · consulta" (`.tab-groups`/
`.tab-group-name` in `style.css`) — stesso principio di `VISIONE.md` §2,
reso visibile nella UI invece di restare solo nel documento di
posizionamento.

**2) `handle_render` (in `balzar/webapi.py`) diventa l'apritore
generico di Balzar Live**: dispatch sui **magic byte** del payload
decodificato (`BZR1`→2D, `BZM1`→3D, `BZX1`→bundle), non
sull'estensione del file caricato — stesso principio già usato da
`chunk_payload`/`qr.py` per trattare i byte come autodescrittivi. Tre
funzioni interne nuove:
- `_handle_render_2d` — il vecchio corpo di `handle_render`, invariato
  a parte un campo `"kind": "2d"` aggiunto per uniformità con gli altri
  due casi;
- `_handle_render_3d` — un `.b3d` isolato: `scene3d.decode_payload` +
  `gltf.scene3d_to_glb` + `generate_bom`, stessa forma di risposta di
  `handle_encode_3d` (`shape_count`/`reference_count`/`instance_count`/
  `vertex_count`/`bom`/`glb_base64`) **tranne** `mean_vertex_error`: quel
  campo confronta contro la geometria originale non quantizzata, che un
  payload già codificato non porta più con sé — omesso onestamente
  invece di inventato, nuovo helper condiviso `_scene3d_stats(scene)`
  usato da entrambi i path 3D/bundle;
- `_handle_render_bundle` — stesso dispatch che `open_bundle_in_browser`
  già fa per il viewer desktop (§9.16), riletto qui per produrre JSON
  invece di HTML: al più un elemento 3D mostrato (il primo, bundle con
  più di un 3D restano validi ma il viewer ne mostra uno), ogni
  elemento allarme alimenta `alarm_rows`, `_documents_from_items`
  (già scritta per la scheda "Assemblee 3D", §9.17/§9.18) produce
  l'indice documenti — **zero codice nuovo per il rendering dei
  documenti**, riusato esattamente com'è. Un bundle senza alcun 3D
  (`has_3d: false`) è valido, risposta con solo `alarm_rows`/
  `documents`.

**Frontend (`app.js`): stesso principio, refactoring invece di
duplicazione.** La scheda "Assemblee 3D" aveva ~250 righe di JS legate
a doppio filo a id DOM fissi (`threedViewer`, `threedBomTable`, ecc.) —
copiarle per la nuova sotto-vista 3D di "Apri programma" avrebbe
significato una seconda copia quasi identica nello stesso file (a
differenza della duplicazione JS server-HTML-vs-statico già accettata
altrove nel progetto per motivi di ambiente diverso, qui l'ambiente è
lo stesso file: nessuna scusa per duplicare). Estratta una fabbrica
`createSceneViewerController(ids)` — click-to-select/isolamento,
ricerca nome/allarme, export scheda ricambio, indice documenti — e una
funzione `renderScenePanel(ctrl, r)` che popola tabella statistiche/BOM/
ricerca/indice da una risposta `r` **il cui formato è già condiviso**
tra `handle_encode_3d` e `_handle_render_3d`/`_handle_render_bundle`
(stessa scelta di campo deliberata sopra). La scheda "Assemblee 3D" ora
istanzia un controller (`threedCtrl`) sugli stessi id di sempre, "Apri
programma" ne istanzia un secondo (`open3dCtrl`) sui nuovi id
`open-3d-*` — stessa logica, zero copie. Tre sezioni di risultato ora
mutuamente esclusive nel tab "Apri programma"
(`open-result`/`open-3d-result`/`open-docs-result`), scelte da
`json.kind`/`json.has_3d`.

**Verificato con Playwright contro un devserver locale reale** (stessa
metodologia già nota, route `handle_*` dirette non un mock — vedi nota
di sessione su perché non contro Vercel): upload di un `.b3d` nudo →
viewer 3D con BOM corretta; upload di un `.bzx` con 3D+tabella
allarmi+documento → viewer 3D + indice documenti (`alarms.csv`,
`nota.txt`) + ricerca per codice allarme funzionante
("E100" → "Bullone-M6"); upload di un `.bzx` di soli documenti → solo
il pannello indice, nessun viewer 3D montato; upload di un `.bzr`
testuale semplice → percorso 2D invariato. **Verifica di
non-regressione esplicita sulla scheda "Assemblee 3D"** dopo il
refactoring del suo JS: stesso file 3DXML sintetico codificato da capo
→ statistiche con `mean_vertex_error` (assente invece nel path
"Apri programma", come atteso), click su una riga BOM → scheda ricambio
abilitata, reset → di nuovo disabilitata, ricerca per nome → trovato.
Generazione QR verificata anche sulla nuova sezione `open-3d-result`
(stesso `setupQrButton` riusato con un terzo/quarto prefisso
`open-3d`/`open-docs`). Nessuna regressione trovata. Suite Python
invariata (nessuna riga JS è testata da `unittest`, per costruzione —
stesso principio già seguito per il resto della UI 3D): 7 nuovi test in
`tests/test_webapi.py::TestHandleRender` (discriminatore `kind` sul
path 2D, apertura di un `.b3d` nudo, `.b3d` corrotto → 400 pulito,
bundle con 3D+allarme → entrambi presenti, bundle di soli documenti →
`has_3d: false`, bundle corrotto → 400 pulito).

**Non ancora fatto**: CLI/GUI desktop non hanno bisogno di questo
cambio (già aprono `.b3d`/`.bzx` nativamente in `balzar/gui.py`/
`cli.py` da sessioni precedenti, §9.9/§9.16) — questo lavoro riguardava
solo la demo web, che era rimasta indietro sull'unico punto (l'apritore
generico) non ancora allineato.

### 9.21 BOM collassata alla granularità del file allarmi (`generate_bom` collapse_names) + fix CSV a 3 colonne

Seguito diretto di sessione: un file 3DXML reale caricato dall'utente
(un impianto industriale, non incluso nel repository per lo stesso
motivo di copyright già visto per gli altri assiemi reali) e una
tabella allarmi CSV con nomi di **sotto-assiemi** (`HEATER1`,
`RESERVOIR1`, `UV DEVICE`, ecc.) invece di parti singole hanno
esposto due problemi reali, verificati sul file prima di scrivere
codice, non ipotizzati:

1. **Il meccanismo di evidenziazione oggi lavora solo a livello di
   parte foglia**: `generate_bom`/`highlightNames` non hanno mai
   avuto un concetto di "sotto-assieme" — cercare "HEATER1" (che
   esiste nell'albero solo come nodo di raggruppamento, senza
   geometria propria) restituiva "nessun componente trovato".
2. **Rischio di sovra-evidenziazione se risolto ingenuamente per
   nome**: analizzando il file reale, i nomi delle parti foglia sotto
   `RESERVOIR1`/`RESERVOIR2`/`UV DEVICE`/`FLITRO CARBONE` si
   sovrappongono per **7-12 nomi ciascuno** con parti fuori dal
   gruppo (tutti nel pattern placeholder auto-generato `"Object N"`,
   §2.6/§9.12) — evidenziare "RESERVOIR1" per solo nome testuale
   avrebbe acceso anche pezzi di `RESERVOIR2`.

**Fix, in `balzar/scene3d.py`**: `generate_bom(scene, collapse_names=None)`
guadagna un parametro opzionale — un insieme di nomi di `Reference3D`
(tipicamente le colonne `nome_componente` di una tabella allarmi) che,
se corrispondono a un nodo di **gruppo** (non foglia) nella scena,
fermano la ricorsione lì: quel sotto-assieme diventa **una singola
riga di BOM** invece di espandersi in ogni parte sottostante. Un nome
che corrisponde già a una parte foglia ordinaria non viene toccato
(niente da collassare, è già atomico). Senza `collapse_names`
(default), il comportamento è identico a prima — verificato dai 23
test preesistenti, tutti verdi senza modifiche.

**`BomEntry` guadagna `material_names: list[str]`** (e `shape_index`
diventa `int | None`): per una riga ordinaria è un elenco di un solo
elemento uguale al nome; per una riga collassata è l'insieme esatto
dei nomi materiale glTF delle sue parti foglia discendenti, **con un
suffisso** (`COLLAPSE_SEPARATOR = "§"`, mai presente in un nome CAD
reale) che scopa il nome alla sola istanza di quel gruppo specifico —
`_collect_leaf_material_names` cammina l'albero sotto il gruppo
producendo `"{nome_foglia}§{nome_gruppo}"` per ognuna. Questo è
esattamente ciò che elimina il rischio di sovra-evidenziazione:
`"Object 112§RESERVOIR1"` e `"Object 112§RESERVOIR2"` non sono mai lo
stesso materiale, anche se il nome-foglia grezzo coincide.

**`balzar/gltf.py`**: `scene3d_to_glb(scene, collapse_names=None)`
applica **esattamente la stessa regola** durante l'export (un
`collapse_context` filettato nella ricorsione di
`_build_reference_node`, impostato al primo nodo di gruppo il cui nome
è in `collapse_names`): ogni materiale/mesh foglia sotto quel gruppo
riceve lo stesso suffisso `§{nome_gruppo}`, garantendo che `generate_bom`
e l'esportazione GLB restino sempre coerenti — verificato con un test
che decodifica davvero il GLB prodotto e confronta i nomi materiale
con `material_names` della BOM, non solo fidandosi che le due
implementazioni concordino "a vista".

**Frontend (entrambe le copie, `app.js` e `viewer3d.py`'s `_SELECT_JS`)**:
`highlightNames(labels)` ora espande ogni etichetta (nome riga BOM)
nel suo insieme di nomi materiale reali tramite una mappa
`labelToMaterialNames` costruita dalla BOM stessa (dal campo
`material_names`, o dagli attributi `data-material-names` nella
pagina generata dal desktop), prima di toccare i materiali del
modello — `setSelection`/il conteggio per la scheda ricambio
continuano a lavorare sulle etichette di visualizzazione, invariati.
Il click diretto sul modello 3D risolve il materiale cliccato alla sua
etichetta proprietaria (`materialNameToLabel`, la mappa inversa) prima
di evidenziare, cosicché cliccare una singola vite dentro un gruppo
collassato seleziona l'intero gruppo (non ha più senso un'unità più
piccola del gruppo, una volta collassato) e la riga BOM corretta si
marca come selezionata.

**Bug reale trovato scrivendo i test prima di dichiarare la funzione
pronta** (non nell'implementazione principale, in un caso limite):
`effective_display_name` preferisce il nome del wrapper quando la
foglia ha il pattern auto-generato "Object N" **e** il wrapper ha
esattamente un figlio — un gruppo collassato con un solo figlio
placeholder produce quindi un nome materiale come `"HEATER1§HEATER1"`
(il nome del gruppo usato sia come nome-foglia-preferito sia come
suffisso) — ridondante ma **non un bug**: resta comunque univoco,
nessuna collisione. Scoperto scrivendo un test con un gruppo a un solo
figlio placeholder e correggendo l'aspettativa del test (non il
codice, che si comporta correttamente), non il contrario.

**Fix separato, stessa sessione — CSV a 3 colonne corrompeva il nome
componente**: la tabella allarmi reale caricata dall'utente aveva
anche una terza colonna (`documento_procedura`, la stessa idea già
proposta in §9.19 per il Bridge). `parse_alarm_csv_text` (e i due
parser JS gemelli in `app.js`/`_SELECT_JS`) costruivano il nome
componente con `",".join(cells[1:])` — pensato per tollerare una
virgola non quotata nel nome, ma che in presenza di una terza colonna
**incolla il testo della procedura al nome del componente**
(`"HEATER1,procedura_heater"` invece di `"HEATER1"`), rompendo
silenziosamente ogni corrispondenza. Fix: `name = cells[1]` da solo
(o `parts[1]` lato JS) — una terza colonna è ora accettata e ignorata
correttamente, non più incollata. Un nome con una virgola reale deve
essere tra virgolette nel CSV sorgente (`csv.reader` lo gestisce già
bene); il side-effect è che la variante JS (senza supporto quoting,
dichiarato esplicitamente da tempo) non tollera più neanche una
virgola grezza non quotata nel nome — untrade-off onesto, non prima
possibile avere entrambe le cose con un parser così semplice.

**Template CSV corretto fornito in sessione** (non nel repository,
consegnato all'utente): virgola come separatore (non punto e virgola
— con `;` ogni riga diventa una singola cella e viene scartata in
silenzio, zero righe caricate, nessun errore visibile), una riga per
ogni singolo componente (i multi-componente vanno ripetuti su righe
separate, mai con `/` in una cella), nomi verificati contro l'albero
3DXML reale prima di consegnarli (trovato un refuso: `POMPA1` nel CSV
originale contro `POMPA 1`, con spazio, nel file CAD).

Verificato con Playwright contro un devserver locale reale (fixture
sintetica: sotto-assieme `HEATER1` con due bulloni `BoltA`/`BoltB`,
stesso principio di fixture minime già usato altrove — nessun file
CAD reale nel repository): upload 3DXML + CSV con `HEATER1` come
componente → BOM mostra una sola riga `HEATER1` (non `BoltA`/`BoltB`
separate) → click sulla riga seleziona il gruppo (scheda ricambio
abilitata) → ricerca per codice allarme (`A06`) evidenzia lo stesso
gruppo → click diretto sul modello non va in crash. Test aggiunti:
6 in `tests/test_scene3d.py` (`TestBomCollapse` — collasso a una riga,
nome-già-foglia lasciato intatto, comportamento invariato senza
collapse_names, conteggio corretto su un gruppo ripetuto, **il test
di regressione diretto** sul bug reale di sovrapposizione tra due
gruppi con nomi placeholder ambigui, coerenza BOM↔GLB via decodifica
reale del GLB prodotto), 1 in `tests/test_viewer3d.py` (colonna
extra non corrompe più il nome), 2 in `tests/test_webapi.py`
(collasso end-to-end tramite `handle_encode_3d`, nessuna corrispondenza
→ BOM resta espansa, onesto invece di un comportamento silenzioso
diverso) — 280 test totali.

### 9.22 Libreria locale persistente per Balzar Live (app desktop) + fix di un bug reale nello scan

Seguito diretto di sessione: valutato dove finiscono i file dopo una
scansione/apertura in Balzar Live (scenario concreto discusso: 3
macchine, 3 QR scansionati, bisogno di scegliere quale visualizzare,
chiuderlo, aprirne un altro). Decisione presa in sessione: la demo web
resta a sola memoria di sessione (non serve altro, è solo vetrina); per
l'app desktop ha senso salvare in memoria fisica del dispositivo di
lettura — non un registro solo in RAM per la durata del processo.

**Bug reale trovato progettando la feature, non ipotizzato**: `_scan_worker`
instradava **sempre** il payload scansionato a `_job_from_payload`, che
capisce solo `BZR1`/testo — scansionare un QR che porta un assieme 3D o
un bundle **andava in crash** con un `UnicodeDecodeError` grezzo tentando
di decodificare come UTF-8 dei byte binari `BZM1`/`BZX1`. Riprodotto
prima di correggere (non solo letto il codice): payload BZM1 reale,
`data.decode('utf-8')` solleva `'utf-8' codec can't decode byte 0x8c...`.
Fix: nuovo `_dispatch_payload_bytes(job, data)` in `balzar/gui.py`, lo
stesso controllo a tre vie sui magic byte (`BZX1`→bundle, `BZM1`→3D,
altrimenti `BZR1`/testo) già usato da `_worker` per un file su disco, ma
senza il fallback sull'estensione del percorso (una foto scansionata non
ne ha uno) — stesso principio di `chunk_payload`/`qr.py`: il payload è
autodescrivente, non serve un'estensione. `_worker` resta invariato (il
suo fallback su estensione per un caso di magic corrotto è una difesa
in più, non ridondante da rimuovere).

**`balzar/library.py` (nuovo modulo)**: un registro locale persistente
di ciò che è stato decodificato/scansionato — `save_to_library`/
`list_library`/`load_library_payload`/`delete_from_library`, appoggiati
a una cartella (`~/.balzar/library/`, sovrascrivibile con
`BALZAR_LIBRARY_DIR` per i test) con un `manifest.json` e un file per
voce (stessa estensione già usata ovunque nel progetto: `.bzp`/`.b3d`/
`.bzx`). Dichiarato onestamente cosa **non** fa: "cloud" qui significa
solo una cartella normale su disco — se l'utente la punta a una cartella
sincronizzata da Dropbox/OneDrive/iCloud, è il sistema operativo del
dispositivo a fare la parte "cloud", balzar non integra nessun
provider specifico (sarebbe una feature diversa, autenticazione e
gestione errori di rete incluse, non tentata qui).

**Salvataggio automatico, non su richiesta**: `Job` guadagna
`is_live_artifact` (vero solo per un job che ha decodificato/scansionato
un artefatto **esistente** — aprire un `.b3d`/`.bzx`/`.bzp` o scansionare
un QR — mai per un encode fresco lato Balzar Studio, che l'utente salva
già esplicitamente se vuole tenerlo). `_poll_queue` chiama
`_save_job_to_library` solo quando questo flag è vero, subito dopo aver
mostrato il job — nessun blocco, nessuna domanda, ogni scansione si
accumula e basta; l'operatore pota le voci vecchie dal pannello quando
vuole, non ad ogni scansione.

**Pannello "Libreria…"** (nuovo bottone in toolbar): una `Toplevel` con
una lista di tutte le voci (etichetta, tipo, timestamp), doppio click o
bottone "Apri" per riaprire una voce esattamente con lo stesso percorso
di codice di un'apertura normale (`_dispatch_payload_bytes` via un nuovo
`_open_library_worker`, senza fissare di nuovo `is_live_artifact` —
altrimenti riaprire una voce già in libreria ne creerebbe una copia
duplicata ogni volta). "Elimina dalla libreria" rimuove voce e file.

**Bug di risorsa trovato e corretto nello stesso lavoro**: `view_3d()`
apriva sempre un **nuovo** `http.server.HTTPServer` su una porta effimera
ad ogni click, anche per lo stesso identico job — passare avanti e
indietro tra 3 voci di libreria avrebbe lasciato un server in background
per ogni click, per sempre, fino alla chiusura dell'app. Fix:
`_render_viewer_page`/`open_glb_in_browser`/`open_bundle_in_browser`
ora restituiscono il server (non più `None`) — `view_3d()` tiene un
registro `entry_id -> server` (`self._open_viewers`) e, se un server per
quella voce è già attivo, riapre solo una scheda del browser sulla stessa
porta invece di crearne uno nuovo; "Chiudi visualizzazione" nel pannello
chiama `server.shutdown()` + `server.server_close()` e libera la voce dal
registro. Nessun cambiamento all'API pubblica per chi non ha bisogno del
valore di ritorno (nessun altro chiamante nel progetto lo usava).

Verificato con uno smoke test Xvfb dedicato (stessa tecnica già
consolidata nel progetto: `filedialog`/`messagebox` monkeypatchati, nessun
vero file picker), non solo scritto: scansione di un QR con payload 3D
→ nessun crash (bug confermato risolto), auto-salvataggio in libreria
confermato; apertura di 2 file `.b3d` aggiuntivi → 3 voci in libreria
(lo scenario "3 macchine" esatto); pannello popolato con 3 righe; apertura
di una voce dalla libreria → `job.library_entry_id` impostato; **due**
click consecutivi su "Visualizza in 3D" sulla stessa voce → **una sola**
`HTTPServer` nel registro, stessa porta entrambe le volte (bug di risorsa
confermato risolto); chiusura della visualizzazione → registro svuotato;
eliminazione di una voce → libreria passa da 3 a 2. Nessun bug rimasto.

Test aggiunti: `tests/test_library.py` (8 test, logica pura file/JSON,
isolata via `BALZAR_LIBRARY_DIR` — nessun Tkinter richiesto, coerente col
principio già seguito nel progetto: l'interazione Tkinter/browser si
verifica manualmente sotto Xvfb, non nella suite `unittest`) — 288 test
totali.

### 9.23 Audit del codice della libreria (§9.22): 10 bug reali trovati e corretti

Subito dopo aver pushato la feature libreria (§9.22, commit `6d21920`),
eseguito un audit dedicato (`code-review`, 8 angoli di ricerca in
parallelo — correttezza riga-per-riga, comportamento rimosso, tracciante
cross-file, riuso, semplificazione, efficienza, altitudine, convenzioni
CLAUDE.md) sul diff di quel solo commit. Nessuna violazione di
convenzioni trovata; tutti gli altri 7 angoli hanno prodotto candidati
reali, verificati leggendo il codice attuale (non solo fidandosi del
giudizio dei finder) prima di correggerli. Dieci bug/problemi confermati,
corretti uno alla volta con test/verifica Xvfb dopo ciascuno, commit e
push separati per ognuno:

1. **Bug di selezione nel pannello libreria**: `_refresh_library_panel`
   riassegnava `self._library_entries` alla lista appena ricaricata
   *prima* di leggere l'indice di selezione corrente della listbox —
   un salvataggio automatico in background mentre il pannello era
   aperto poteva far evidenziare silenziosamente la voce sbagliata dopo
   il refresh. Fix: catturare id della voce selezionata contro la
   lista ancora a schermo, poi ricaricare.
2. **Il fix anti-leak non copriva gli encode freschi di Balzar
   Studio** — il più significativo dei dieci. `view_3d()` deduplicava
   i server solo per `job.library_entry_id`, che resta `None` per un
   job appena codificato (`.3dxml` fresco o un bundle appena creato,
   mai salvato in libreria per design — `is_live_artifact` resta
   `False`). Cliccare ripetutamente "Visualizza in 3D" su un file
   appena codificato — un flusso comune quanto (o più di) riaprire
   dalla libreria — perdeva ancora un `HTTPServer` per click, esatto
   bug che il fix originale dell sessione precedente doveva chiudere,
   solo sull'altro percorso. Fix: `Job` porta ora un id proprio stabile
   (`uuid4` all'istanziazione, indipendente dalla libreria);
   `view_3d()` usa `job.library_entry_id or job.id` come chiave — un
   job mai salvato in libreria deduplica comunque i propri click
   ripetuti, mentre codificare un file davvero nuovo (un nuovo `Job`,
   una nuova chiave) ottiene correttamente un proprio server.
3. **`_save_job_to_library` catturava solo `OSError`**: un
   `ValueError` (kind sconosciuto, o `json.JSONDecodeError` da un
   manifest corrotto — sottoclasse di `ValueError`) sfuggiva non
   catturato attraverso `_poll_queue`, il cui riarmo ricorrente
   `root.after(100, self._poll_queue)` sta subito dopo questa
   chiamata — fermando permanentemente e silenziosamente il polling
   della coda dei job finché l'app non veniva riavviata. Fix: catturare
   `(OSError, ValueError)`.
4. **Scrittura del manifest non atomica**: `_write_manifest` scriveva
   `manifest.json` direttamente, quindi un crash/disco pieno a metà
   scrittura lasciava un file troncato/corrotto per ogni sessione
   futura. Fix: scrivere su un file temporaneo nella stessa cartella e
   `os.replace()` atomico sopra il manifest reale.
5. **Voce di libreria orfana dopo elimina-poi-visualizza**: una voce
   salvata automaticamente ma mai visualizzata (mai in
   `_open_viewers`) poteva essere eliminata dal pannello mentre il suo
   `Job` restava quello mostrato nella finestra principale; un
   successivo primo click su "Visualizza in 3D" per quel job ancora
   visualizzato resuscitava l'id ormai eliminato dentro
   `_open_viewers` — e poiché quell'id non poteva più comparire nella
   listbox (più corta), quel server non poteva più essere chiuso dal
   pannello. Fix: `_delete_library_selected` azzera
   `self.job.library_entry_id` se coincide con la voce eliminata, così
   una vista successiva ricade sull'id proprio del job (punto 2) invece
   di resuscitare quello eliminato.
6. **Ordinamento "più recente in cima" non garantito per salvataggi
   nello stesso secondo**: `list_library()` ordinava per `saved_at`
   (risoluzione 1 secondo) con `reverse=True`, ma l'ordinamento di
   Python è stabile anche in modalità inversa (le chiavi uguali
   mantengono il loro ordine relativo originale, non vengono
   invertite) — due scansioni completate nello stesso secondo
   mostravano quindi la più vecchia delle due in cima, contraddicendo
   l'ordine documentato/atteso. Fix: spareggio per posizione originale
   di append nel manifest, discendente.
7. **Logica di dispatch per magic byte duplicata**: `_dispatch_payload_bytes`
   (usata da scansione QR e riapertura da libreria) e il dispatch
   inline di `_worker` (usato aprendo un file da disco) erano due copie
   quasi identiche dello stesso controllo a tre vie BZX1/BZM1/BZR1, con
   piccole varianti (fallback su estensione, branch immagine) —
   rischio di divergenza silenziosa se una viene aggiornata e l'altra
   no. Fix: unificate in un solo metodo con un parametro `path`
   opzionale, che restituisce quale ramo è stato preso (usato da
   `_worker` per derivare `job.is_live_artifact` invece di impostarlo
   inline per ramo).
8. **Sequenza di chiusura server duplicata**: `_close_library_viewer_selected`
   e `_delete_library_selected` ripetevano lo stesso pop+shutdown+
   server_close identico. Fix: estratto un helper condiviso
   `_shutdown_viewer`.
9. **Cartella temporanea del viewer mai ripulita**: il `work_dir` di
   `tempfile.mkdtemp()` creato da `view_3d()` (copia di
   `model-viewer.min.js` + `model.glb`, ~1 MB) non veniva mai rimosso
   nemmeno dopo aver chiuso esplicitamente quel visualizzatore —
   accumulo su disco in una sessione lunga. Fix: `_open_viewers` ora
   traccia `(server, work_dir)`; `_shutdown_viewer` chiama
   `shutil.rmtree` sul work_dir dopo aver fermato il server.
10. **`server.shutdown()` bloccava il thread principale di Tkinter**:
    `http.server`'s `shutdown()` attende che il loop `serve_forever()`
    (in un altro thread) se ne accorga al prossimo tick del suo
    poll-interval (~0,5s di default) — chiamarlo direttamente sul
    thread principale congelava l'intera GUI per quel tempo ad ogni
    click su "Chiudi visualizzazione"/"Elimina dalla libreria". Fix:
    lo shutdown/rmtree ora girano in un thread di background; il pop
    dal registro resta sincrono (così l'UI riflette subito la
    chiusura).

**Metodologia di verifica, non solo lettura del diff**: per ogni bug
strutturale (2, 3, 5, 9, 10) scritto uno smoke test Xvfb dedicato che
riproduce concretamente lo scenario prima del fix (dove tecnicamente
possibile senza modificare il codice — es. forzare `save_to_library` a
sollevare un `ValueError` per il punto 3) e ne conferma la risoluzione
dopo; per i bug di logica pura (1, 4, 6) letto il codice sorgente
attuale riga per riga per confermare ogni candidato del finder prima di
correggere, non fidandosi del solo giudizio dell'agente di revisione.
Ogni fix è stato verificato con l'intera suite `unittest` **e** con
tutti gli smoke test Xvfb rilevanti prima di committare, uno alla
volta, con push separato dopo ciascuno (10 commit, ognuno seguito da
`git push` sia sul branch di feature sia su `main`).

Test aggiunti in questo audit: 2 in `tests/test_library.py`
(`test_newest_first_breaks_same_second_ties_by_append_order`,
`test_write_failure_mid_manifest_write_leaves_old_manifest_intact`) —
290 test totali. Gli altri fix (2, 3, 5, 9, 10) riguardano interazione
Tkinter/thread/browser pura — verificati con smoke test Xvfb one-off,
non aggiunti alla suite automatica, stesso principio già seguito per il
resto della UI del progetto (l'interazione Tkinter si verifica
manualmente sotto Xvfb, non in `unittest`).

### 9.24 "Punto 3" ripreso: tempo di generazione QR parallelizzato per assiemi 3D grandi

Ripresa la richiesta rimandata a inizio sessione (ridurre tempo di
scansione/dimensione payload per assiemi 3D reali grandi — vedi §9.10
per la pipeline già misurata, e la libreria di §9.22 che risolve già
metà del problema: una volta scansionato, un assieme resta in cache
locale, niente riscansione ad ogni rivisita). Prima di scrivere
codice, misurato — non stimato — dove sta davvero il collo di
bottiglia per un assieme *più grande* di quello già documentato:
costruita una `Scene3D` sintetica apposta (150 forme uniche × 600
vertici, nessun file 3DXML reale coinvolto — stessa ragione di
copyright già vista per gli assiemi reali in §9.2/§9.10), **2,3× più
grande** del payload più grande già misurato (555.922 B contro
239.491 B), fatta passare per l'intera pipeline reale (encode →
`payload_to_qr_frames` → `LiveScanner` → decode+GLB) con le stesse
funzioni di produzione, non un mock.

**Risultato prima del fix**: 137,29 s totali, ben oltre l'obiettivo di
~60 s (§9.3) — e la sorpresa, verificata non assunta: **la generazione
dei QR (79,9 s) pesa più della lettura (56,9 s)**, il passo su cui
tutte le ottimizzazioni precedenti (§2.4b, tiled-crop) si erano
concentrate. Isolata la causa esatta con un microbenchmark mirato:
codificare un QR versione 40 vicino alla capacità massima (il caso
comune, dato che `chunk_payload` dimensiona i pezzi apposta per
riempirne uno) costa **~0,06 ms per carattere base64** nella libreria
`qrcode` (puro Python) — misurato identico alle versioni 10, 20, 30 e
40: il costo è proporzionale ai dati totali, **non** riducibile
scegliendo un `grid_dim`/dimensione di chunk diversa (un chunk più
piccolo richiede solo più QR, stesso totale di lavoro).

**Fix**: la codifica di ogni chunk in un QR è indipendente da tutte le
altre, quindi parallelizzabile sui core della CPU senza toccare un solo
byte di output. Nuova `_generate_qr_images` in `balzar/qr.py`: usa
`concurrent.futures.ProcessPoolExecutor`, con i worker che restituiscono
byte PNG (non oggetti `PIL.Image` — evita di dipendere dal fatto che
`Image` sia pickle-abile, mai verificato esplicitamente). Misurato:
**3,84×** di accelerazione su 4 core per 64 codici (14,34 s → 3,74 s),
byte PNG **identici** byte-per-byte rispetto al percorso sequenziale
(verificato, non assunto). Sotto `_PARALLEL_MIN_IMAGES = 4` codici resta
sequenziale (l'overhead di avvio di un process pool non conviene per una
manciata di QR); qualunque fallimento del pool (un ambiente sandboxato
senza spawn di processi, un limite di piattaforma non visto in questo
ambiente) ricade **sempre** sul percorso sequenziale — un'ottimizzazione
di velocità, mai un requisito di correttezza, stesso principio già
seguito per l'hint `grid_dim` in lettura (§2.4b).

**Risultato dopo il fix**, stessa scena sintetica: generazione
79,9 s → **25,3 s** (3,16× reale, coerente col 3,84× isolato — la
differenza è rumore di sistema in questo sandbox condiviso), totale
pipeline 137,29 s → **67,62 s** (2,03× complessivo) — vicino
all'obiettivo di 60 s ma ancora leggermente oltre. **Nessun'altra leva
facile identificata per chiudere il gap residuo** senza toccare la
dimensione del payload/fedeltà geometrica (una scelta di prodotto
genuinamente lossy, deliberatamente non presa qui) o senza aggiungere
una libreria di codifica QR non-Python (dipendenza nuova, fuori scope):
la generazione è già parallelizzata al massimo dei core disponibili,
la lettura non ha un'analoga leva di parallelizzazione nell'uso reale
(scansione live foto-per-foto, non un lotto di immagini già pronte).

**Onestà sul numero residuo**: il gap (~7,6 s, ~13%) è misurato su un
sandbox condiviso con variabilità di sistema osservata direttamente
(la sola lettura è passata da 56,9 s a 41,9 s tra due run identiche,
nessun codice toccato in mezzo) — su una macchina reale dedicata
questo stesso assieme sintetico potrebbe già stare nel budget. Se gli
assiemi reali dell'utente sono ancora più grandi di questo benchmark
(2,3× il caso più grande già documentato), la richiesta originale di
punto 3 — semplificazione geometrica per le parti non legate ad
allarmi — resta la prossima leva da valutare, ora con un punto di
riferimento reale invece che solo teorico.

Test aggiunti: `tests/test_qr.py::TestParallelQRGeneration` (3 test:
percorso parallelo identico byte-per-byte al sequenziale, sotto soglia
resta sequenziale anche con un pool rotto, fallback a sequenziale
corretto quando il pool fallisce) — 293 test totali.

### 9.25 Revisione di una specifica esterna sulle sequenze QR: 3 fix reali, uno scartato

Un consulente ha fornito una specifica tecnica per "ottimizzare" il
processo di generazione/lettura di sequenze QR (matrici dinamiche per
dimensione del supporto, frequenza frame fissa, acquisizione continua
con selezione del frame migliore, decodifica in pipeline, nessun
ridimensionamento lossy). Invece di implementarla alla cieca, ogni
proposta è stata confrontata con il codice reale e, dove possibile,
misurata — non solo letta.

**Scartato, contraddetto dai dati reali**: la specifica raccomanda
matrici più dense (6×6, 8×8) per supporti fisici più grandi, stimando
"~4× più throughput" per l'8×8 senza aver mai testato una griglia 8×8
reale. Il benchmark reale già in questo documento (§2.4b/§9.10) dice
l'opposto: un'8×8 vera ha un'unica finestra di lettura affidabile,
esattamente alla risoluzione già nota come "lenta senza guadagno" per
il 4×4, con un tempo di decodifica per singolo QR **15-18× peggiore**.
`grid_dim=4` resta il default corretto indipendentemente dalla
dimensione del supporto.

**Confermato, già implementato**: "non cercare QR nell'immagine intera,
ritaglia per posizione nota" (§9.1 della specifica) è esattamente
`_tile_boxes`/`_decode_tiled`, già costruito e ottimizzato in una
sessione precedente (§2.4b punto 6). "Verifica integrità per QR"
(sequenza/frame/posizione/checksum) è ridondante rispetto a BZC1, che
già basta da solo (frame/posizione sono solo un'etichetta umana, per
design — vedi il docstring di `payload_to_qr_frames`).

**Tre fix reali implementati, verificati e misurati**:

1. **Resize bicubico che sfoca l'ultimo chunk di quasi ogni frame —
   bug reale, non solo ipotetico.** La specifica dice "evitare
   ridimensionamento automatico, nero/bianco puro". Verificato: `_compose_grid`
   ridimensiona ogni QR alla cella più grande della griglia col filtro
   di default di Pillow (bicubico). L'ultimo chunk di un payload (a
   meno che la dimensione non sia un multiplo esatto di
   `CHUNK_RAW_BYTES`, il caso comune) produce un QR più piccolo — che è
   esattamente quello che poi viene ingrandito. Misurato: il resize di
   default introduce **256 livelli di grigio distinti** da un QR puro
   bianco/nero; `Image.NEAREST` (esplicito, non più il default) ne
   preserva **2**. Un QR sfumato è oggettivamente più difficile da
   binarizzare per un decoder sotto le condizioni non ideali (sfocatura,
   autofocus, luce reale) che il formato deve tollerare. Test scritto
   e verificato per davvero (non solo aggiunto): rimosso temporaneamente
   il fix, confermato che il test fallisce con 256 colori, ripristinato,
   confermato verde.

2. **Decodifica dei tile in parallelo — completamento naturale della
   parallelizzazione già fatta per la generazione (§9.24).** La
   specifica chiede di decodificare i QR di una matrice in parallelo
   invece che in sequenza (§9.2). Verificato che `pyzbar` chiama
   `libzbar` nativa via `ctypes`, che rilascia il GIL durante la
   chiamata — quindi, a differenza della generazione (calcolo puro
   Python, serviva un process pool), qui bastano i **thread**, più
   economici (nessun pickling, nessun costo di avvio processo).
   Misurato: **3,72×** più veloce su una griglia 4×4 reale (2146ms →
   577ms). Applicato al benchmark del §9.24 (67,6s totali, di cui
   41,9s di lettura): **lettura 41,9s → 17,1s**, **pipeline totale
   67,6s → 43,66s — ora entro il budget di 60s**, con margine, contro
   i 137,3s di partenza prima di entrambi i fix. Un problema emerso
   scrivendo i test, non nel codice: un frame reale può contenere una
   lettura spuria di un altro formato di codice a barre nella zona
   dell'etichetta testuale (comportamento preesistente di zbar, non
   una regressione — già innocuo perché `LiveScanner`/`scan_image_bytes`
   filtrano già per il prefisso `CHUNK_MAGIC` a valle) — corretta
   l'assunzione del test (contare solo i chunk BZC1 reali), non il
   codice.

3. **Stima onesta del tempo di lettura, mostrata all'operatore.**
   La specifica chiede che il generatore restituisca "tempo stimato
   acquisizione, livello affidabilità previsto" (§12). Nuova
   `estimate_scan_seconds(n_frames)` in `balzar/qr.py`, calibrata sul
   benchmark reale appena misurato (~1,1s/frame di sola decodifica a
   piena risoluzione con il percorso parallelo) — **non un numero
   inventato**, ma dichiarata esplicitamente come stima: un intervallo
   (basso, alto) dove l'alto raddoppia il basso come margine per il
   tempo reale di scatto/messa a fuoco che un benchmark di sola
   decodifica non può includere. Esposta in `handle_qr` (modalità
   gif/pages) come `estimated_scan_seconds_low`/`_high`, mostrata
   nella demo web accanto al risultato. **Non ancora esposta in
   CLI/GUI desktop**: quei percorsi generano solo `payload_to_qr_image`
   (una singola griglia auto-dimensionata, mai la sequenza a frame
   `grid_dim`), per cui la stima calibrata sul percorso a frame non si
   applicherebbe onestamente — nessuna finta precisione aggiunta dove
   non è calibrata.

Test aggiunti: 1 in `tests/test_qr.py::TestQRCarrier` (resize
NEAREST), 4 in `tests/test_qr.py::TestParallelTileDecoding` (decodifica
parallela identica alla sequenziale, fallback sotto soglia e su pool
rotto, `_decode_tiled` end-to-end), 3 in
`tests/test_qr.py::TestEstimateScanSeconds`, 3 in
`tests/test_webapi.py::TestHandleQr` (nuovi campi nella risposta) —
301 test totali.

### 9.26 Matrici non complete: due bug reali distinti da una segnalazione utente, più una regressione auto-inflitta corretta nella stessa sessione

Segnalazione diretta e concreta dell'utente, testata di persona sia
sulla demo web sia sul solo trasporto QR (`trasporto-qr.html`): "le
matrici non complete (esempio 10 code su 16 slot) provocano la non
rilevazione dei qr code". Investigato prima di scrivere qualunque fix
— si sono rivelati **due bug distinti**, entrambi reali, non uno solo,
più una regressione che il fix del secondo ha introdotto e che è stata
trovata e corretta nella stessa sessione.

**Bug A — colonne assunte uguali a `grid_dim`, mai risolte.**
`_tile_boxes`/`tileBoxes` assumevano `cols = grid_dim` incondizio-
natamente, ma `_compose_grid` dispone davvero `len(images)` immagini a
`cols = ceil(sqrt(len(images)))` — che scende SOTTO `grid_dim` non
appena `n <= (grid_dim-1)**2` (es. 8 codici a `grid_dim=4` è un vero
3×3, non 4×4). Con l'assunzione sbagliata, nessuna delle due ipotesi
`top` riusciva a ricostruire l'altezza reale dell'immagine (più bassa
di quella di una griglia 4×4 piena), quindi `_tile_boxes` falliva
correttamente in modo esplicito (0 box) — ma il fallback whole-image
di jsQR (`decodeAllViaMasking`, senza il multi-decode nativo di ZBar)
falliva **anch'esso** su una griglia densa del genere: fallimento
totale nel browser, non solo perdita dello speedup. Fix: `_tile_boxes`/
`tileBoxes` ora cercano `cols` da `grid_dim` in giù fino a 1 (`grid_dim`
tentato per primo, il caso comune), tenendo la prima combinazione
`(cols, top)` che ricostruisce esattamente sia larghezza sia altezza.
Verificato con misura diretta: payload forzato a esattamente 8 chunk,
`_tile_boxes(..., grid_dim=4)` produce ora **9 box** (griglia 3×3
reale, non 16), tutti e 8 i codici reali decodificati individualmente.

**Bug B — coda vuota scartata per intero, non solo ignorata.**
`_decode_tiled` richiedeva che **ogni singola cella**, incluse quelle
genuinamente vuote oltre il numero reale di immagini, producesse un
risultato. Per una segnalazione esatta come "10 di 16 slot" (10 codici
reali a `grid_dim=4`: layout vero 4 colonne × 3 righe = 12 celle, le
ultime 2 bianche senza alcun QR), questo è strutturalmente impossibile
— la vecchia verifica di completezza scartava quindi SEMPRE un
risultato tiled altrimenti perfetto, per qualunque frame parziale con
una coda vuota. Fix: nuova verifica basata su un PREFISSO di hit/miss
per cella (nello stesso ordine riga-maggiore in cui `_compose_grid`
piazza le immagini) — una sequenza di successi reali seguita da una
coda vuota è accettata (la coda vuota è la forma NORMALE di un ultimo
frame parziale), mentre un successo che compare DOPO un fallimento fa
scartare tutto il risultato (segno che la geometria è sbagliata, non
solo che una cella è genuinamente vuota). Verificato: payload forzato
a esattamente 10 chunk, `_tile_boxes(..., grid_dim=4)` produce **12
box** (4×3 reale), `_decode_tiled` recupera ora tutti e 10 i chunk
reali (prima: lista vuota, fallimento totale).

**Regressione, auto-inflitta dal fix del Bug B, trovata ri-eseguendo la
suite esistente — non ipotizzata.** `test_scan_image_bytes_grid_dim_hint_matches_default`
ha iniziato a fallire con `PayloadError: not a balzar chunk (bad
magic)`. Causa isolata con certezza, non solo sospettata: su un payload
di test da 34 chunk decodificato a `grid_dim=6` (36 celle, 2 vuote in
coda), la cella di indice 4 — una cella reale, non vuota — produce
**due** risultati ZBar: uno `QRCODE` vero e uno spurio `DATABAR`
(`b'0152941528732321'`), un artefatto di misdetection già documentato
(ZBar può leggere per sbaglio un'altra simbologia di codice a barre nel
margine di un ritaglio). Prima del fix del Bug B questo era invisibile:
la vecchia verifica "tutte le celle devono avere un hit" falliva
comunque sempre per qualunque frame parziale, quindi il chiamante
ricadeva sempre sulla scansione whole-image (che non esibisce questo
artefatto specifico del bordo di ritaglio). Una volta che `_decode_tiled`
ha iniziato a riuscire anche su frame parziali, il suo loop di raccolta
prendeva alla cieca **entrambi** i risultati di quella cella —
compreso quello spurio non-QR — che poi falliva la verifica del magic
byte di `assemble_chunks` a valle. Fix: `_decode_tiled` filtra ora
`r.type == "QRCODE"` prima di qualunque cosa (sia il controllo
hit/miss sia la raccolta finale) — un risultato non-QR non conta né
come hit né come chunk valido. Applicato per coerenza/difesa anche al
percorso whole-image di `scan_image_bytes` (stesso principio già usato
da `LiveScanner.add`, che tollera già un chunk con magic byte sbagliato
scartandolo silenziosamente) — verificato empiricamente che il
percorso whole-image su questo stesso payload di test **non** riproduce
lo spurio DATABAR (la scansione dell'immagine intera, senza ritaglio,
resta a 34/34 `QRCODE` puliti), quindi il filtro lì è difensivo,
non la correzione di un fallimento osservato.

**Lato JS**: nessun fix equivalente necessario per il filtro di tipo —
`jsQR` decodifica solo QR code, non può mai restituire un risultato di
un'altra simbologia come DATABAR (a differenza di ZBar, multi-
simbologia nativa). `tileBoxes` aveva già la stessa ricerca di `cols`
del Bug A applicata in questa sessione; `decodeAllInImage` non ha mai
avuto bisogno del fix del Bug B: il suo design "tieni sempre il
risultato tiled parziale, non scartarlo" (già esistente per il limite
di affidabilità per-crop di jsQR, §2.4f/§2.4i) gestiva già la coda
vuota correttamente per costruzione — confermato con misura diretta
(10/10 e 8/8 trovati via browser reale con fotocamera fittizia).

Test aggiunti in `tests/test_qr.py::TestParallelTileDecoding`: 3 nuovi
(`test_tile_boxes_solves_fewer_columns_for_a_sparse_partial_frame`,
`test_decode_tiled_recovers_a_partial_frame_with_a_blank_tail`,
`test_decode_tiled_drops_spurious_non_qr_symbology_matches` — quest'ultimo
riproduce esattamente il payload/`grid_dim` della regressione, non un
caso generico) — 315 test totali.

### 9.27 Acquisizione continua estesa alla GUI desktop: un ponte browser locale, non una nuova dipendenza nativa

Ultimo tassello del percorso "acquisizione continua" iniziato in §2.4f:
Balzar Studio/Live sulla demo web (§2.4i/§2.4j) avevano già la
fotocamera continua, l'app desktop (`balzar/gui.py`) no — "Scansiona
foto QR" resta un flusso a foto singole scattate a parte (via
`filedialog`, `pyzbar` nativo, nessuna fotocamera live), perché Tkinter
non ha un'API fotocamera propria.

**Decisione architetturale, coerente con il resto del progetto**: non
aggiungere OpenCV (o un'altra libreria di cattura video nativa) come
nuova dipendenza — mai usata altrove in balzar, e ridondante rispetto a
un motore (`jsQR`/`qr-transport-core.js`/`qr-camera-scanner.js`) già
vendorizzato, già provato su tre superfici diverse (trasporto-qr.html,
Balzar Live, e ora questa). Stesso principio già seguito da
`viewer3d.py` per il 3D (nessun rasterizzatore scritto in casa, delega
a `model-viewer` in una pagina locale): qui si delega la cattura
fotocamera al browser di sistema, in una pagina locale minimale, invece
di reimplementarla in Python.

**`balzar/live_scan_server.py` (nuovo modulo)** — il pezzo che il tab
web non aveva bisogno di avere: un modo di far tornare il risultato
DAL browser AL processo desktop. `start_live_scan_server(work_dir)`
scrive una paginetta HTML (video + `ContinuousQrScanner`, `gridDim=1`
fisso — l'unico valore realisticamente affidabile per la cattura live,
§2.4g) + le tre copie dei JS vendorizzati in `work_dir`, la serve su
una porta effimera locale (stesso `http.server.HTTPServer` +
thread daemon di `viewer3d.py`), apre il browser di sistema, e
restituisce `(server, result_queue)`. L'unica novità rispetto al
pattern di `viewer3d.py`: l'handler accetta anche un `POST /submit` (i
byte ricostruiti, base64) e li mette su una `queue.Queue` — nessun
altro modo di far arrivare il risultato dal thread del server HTTP al
thread principale di Tkinter senza bloccarlo.

**`balzar/gui.py`**: nuovo bottone "Scansiona con fotocamera
(browser)…", toggle (un secondo click annulla una scansione in corso
invece di aprirne una seconda — l'etichetta del bottone stesso è lo
stato, nessun indicatore separato). `toggle_camera_scan` avvia il
server in una `tempfile.TemporaryDirectory`; `_poll_camera_scan`
(stesso pattern non bloccante di `_poll_queue`, `root.after(200, ...)`)
controlla la coda senza mai bloccare il thread principale; alla
ricezione, `_camera_scan_worker` riusa **esattamente** lo stesso
`_dispatch_payload_bytes` già usato da `_scan_worker` per una foto
scansionata da file (`job.is_live_artifact = True`, quindi salvataggio
automatico in libreria, §9.22, identico a una scansione da foto).
Teardown del server (`_stop_camera_scan`) in un thread di background,
stessa ragione già documentata per `_shutdown_viewer` (§9.23 punto 10):
`server.shutdown()` blocca finché l'altro thread non se ne accorge al
prossimo tick di poll (~0,5s), farlo sul thread principale di Tkinter
congelerebbe la GUI per quel tempo ad ogni annullamento/completamento.

**Verificato end-to-end sotto Xvfb con una fotocamera fittizia reale**
(stessa metodologia di §2.4g/§2.4h/§2.4i, non un mock): click sul
bottone (chiamata diretta a `toggle_camera_scan`, `webbrowser.open`
catturato invece di lanciato — nessun browser di default configurato in
questo sandbox) → server avviato, URL catturato; un secondo click
annulla la scansione, il bottone torna all'etichetta originale; un
terzo avvio, stavolta guidato da un vero Chromium
(`--use-file-for-fake-video-capture`, stesso video Y4M scritto a mano
già usato altrove) che naviga all'URL catturato, clicca "Avvia
fotocamera", lascia che `ContinuousQrScanner` completi la scansione di
una sequenza `grid_dim=1` reale e la invii a `/submit` — il job arriva
nella coda di Tkinter (`root.after` pompato con `root.update()`, stesso
principio già consolidato in questo progetto per i test GUI sotto
Xvfb) con il testo del programma **verificato carattere per carattere**
(non solo la dimensione), `is_live_artifact=True`, e il server chiuso
correttamente al completamento (`_camera_scan_server is None`). Zero
tocchi dell'operatore dopo l'avvio della fotocamera, esattamente il
modello già stabilito per l'acquisizione continua sulle altre due
superfici.

**Nessun numero di prestazioni nuovo da misurare**: il motore di
decodifica è bit-per-bit lo stesso già calibrato in §2.4g/§2.4h (stessa
risoluzione di cattura, stesso intervallo minimo, stesso limite
`gridDim=1`) — il ponte desktop aggiunge solo un `POST /submit` finale
(un singolo round-trip HTTP locale su `127.0.0.1`, trascurabile rispetto
al tempo di scansione stesso) e non introduce alcuna caratteristica di
prestazioni propria da ricalibrare.

Test aggiunti: `tests/test_live_scan_server.py` (6 test, protocollo
HTTP puro via socket reali — nessun Tkinter, nessun browser, nessuna
fotocamera vera: apertura del browser catturata, pagina + i tre JS
vendorizzati serviti correttamente, `/submit` valido mette i byte
sulla coda, `/submit` con corpo non valido o base64 non valido
risponde 400 invece di andare in crash, percorso sconosciuto risponde
404) — 321 test totali. Nessun test Python per l'interazione
Tkinter/browser/fotocamera stessa (comportamento verificato manualmente
sotto Xvfb in sessione, stesso principio già seguito per il resto della
UI browser-based di questo progetto, non nella suite `unittest`
automatica).

### 9.28 Rilevamento automatico di grid_dim in lettura: l'operatore non deve più saperlo né impostarlo

Richiesta diretta di sessione, seguito del lavoro §9.26: ovunque nel
progetto la lettura di una sequenza QR richiedeva che l'operatore
sapesse e impostasse lo stesso `grid_dim` usato in generazione (un
`<select>` 1/2/4/8 sia in `trasporto-qr.html` sia nella sezione di
lettura manuale di Balzar Live), nonostante `grid_dim` fosse già
dichiarato ovunque nel codice "solo un suggerimento di velocità, mai un
requisito di correttezza" — un requisito manuale che l'utente doveva
comunque rispettare per ottenere lo speedup, o la lettura ricadeva
(più lenta ma corretta) sulla scansione whole-image.

**L'intuizione che rende il rilevamento automatico possibile quasi
gratis**: `_tile_boxes`/`tileBoxes` già cercano `cols` da `grid_dim` in
giù fino a 1 (§2.4b/§9.26) — il valore di `grid_dim` passato non è mai
stato "il valore vero", è sempre stato solo il **tetto** da cui iniziare
la ricerca. Passare sempre il tetto massimo che qualunque generatore di
questo progetto usa (8) invece di un valore scelto dall'utente fa
trovare lo stesso `cols` reale a prescindere da come la sequenza è stata
generata — l'utente non deve più saperlo.

**Verificato che questo è sicuro, non solo assunto — e la verifica ha
trovato un rischio reale non ovvio.** Uno sweep esaustivo (136 frame
reali, ogni `grid_dim` di generazione supportato × un ampio ventaglio
di conteggi di capitoli, incluse code piccole/parziali) confronta
`_tile_boxes(width, height, grid_dim_vero)` con
`_tile_boxes(width, height, 8)`: **5 casi su 136 non trovano la stessa
geometria** — una vera coincidenza aritmetica dove un'ipotesi `(cols,
top)` SBAGLIATA soddisfa comunque la tolleranza stretta di 2px prima
che la ricerca raggiunga quella vera (misurato su un frame minuscolo a
360×408px, un singolo QR piccolo con etichetta: `cols=1,top=26` è
l'ipotesi vera con errore 0, ma `cols=8,top=0` — provata per prima
nell'ordine di ricerca dall'alto — ha ANCH'ESSA errore 0). Prima di
concludere che il tetto=8 fosse sicuro da usare come default, verificato
il comportamento di `_decode_tiled` su tutti e 136 i casi, non solo la
geometria: **zero esiti scorretti**. Nei 5 casi di geometria sbagliata,
i ritagli mal posizionati non contengono mai un QR reale decodificabile,
quindi `_decode_tiled` torna vuoto (nessun hit) e il chiamante ricade
correttamente sulla scansione whole-image già esistente — mai dati
sbagliati, solo lo speedup perso in quei 5 casi su 136. Lato JS,
`decodeAllInImage` ha una rete di sicurezza equivalente per costruzione
(non modificata in questa sessione): un risultato tiled incompleto non
viene mai scartato ma la scansione whole-image viene comunque eseguita
in aggiunta finché il tiled non è sicuro sia caso: un ritaglio mal
posizionato quasi certamente non contiene un vero pattern finder QR,
quindi il pass tiled resta vuoto/incompleto e il fallback whole-image
scatta comunque.

**Implementazione**: nuova costante `_AUTO_GRID_DIM_CEILING = 8` in
`balzar/qr.py`. `LiveScanner.add`/`scan_image_bytes`/`scan_image_file`
ora tentano **sempre** il percorso tiled veloce (prima: solo se
`grid_dim` era esplicitamente passato) — `grid_dim=None` (il default)
usa il tetto automatico invece di saltare direttamente alla scansione
whole-image. `grid_dim` resta un parametro accettato solo per forzare
un valore diverso dal tetto di default (es. un deployment non standard
con `grid_dim>8`) — nessun caso d'uso reale nel progetto lo richiede
oggi. Lato JS, `decodeAllInImage(imgData, gridDim)` — `gridDim` diventa
opzionale, `gridDim || 8` di default; `gridDim=1` esplicito resta
intatto per `ContinuousQrScanner` (che già sa che ogni fotogramma è un
singolo QR non in griglia, nessun beneficio a cercare una risposta già
nota nel suo loop di cattura ravvicinato).

**Superfici aggiornate**: rimosso il `<select>` "QR per immagine" dalla
sezione di lettura manuale sia di `trasporto-qr.html` (`#dec-grid-dim`)
sia di Balzar Live (`#open-scan-grid-dim`, tab "Apri programma") —
`decodeAllInImage(imgData)` chiamato senza secondo argomento in
entrambi i punti di chiamata (`trasporto-qr.js`, `app.js`). La CLI
(`balzar scan --grid-dim`) e la GUI desktop ("Scansiona foto QR",
`gui.py`'s `_scan_worker` → `scan_image_file(path)`) **non hanno
richiesto alcuna modifica di codice**: chiamavano già `grid_dim=None`
di default, quindi ereditano il rilevamento automatico gratis dal
cambio di semantica in `qr.py` — solo il testo di help di `--grid-dim`
è stato aggiornato per riflettere che ora è un override opzionale, non
un suggerimento da passare di norma. Il selettore `grid_dim` in
**generazione** (`enc-grid-dim` in `trasporto-qr.html`, e ogni altro
punto che genera una sequenza) resta invariato e obbligatorio — è una
proprietà del supporto fisico/dello schermo scelto dall'utente, non
qualcosa che si può auto-rilevare a monte, e questa richiesta riguardava
solo la lettura.

Verificato con Playwright contro un devserver locale reale (stessa
metodologia già nota): payload da 76.800 byte grezzi (trasporto QR di
byte arbitrari) codificato a `grid_dim=4` → 3 pagine reali con più QR
per pagina ciascuna → lette in `trasporto-qr.html` **senza alcun
selettore `grid_dim` visibile nella pagina** → byte ricostruiti
bit-identici; un programma DSL codificato a `grid_dim=2` → aperto in
Balzar Live allo stesso modo, senza selettore, testo del programma
verificato carattere per carattere. Nessuna regressione: la suite
Python esistente già copriva involontariamente questo percorso
(`tests/test_cli.py::test_chunks_raw_qr_grid_dim_and_scan_raw_roundtrip`
genera con `--grid-dim 2` e legge con `balzar scan` **senza**
`--grid-dim`, un roundtrip bit-identico già verde prima di questa
sessione).

Test aggiunti in `tests/test_qr.py::TestAutoGridDimDetection` (3 test):
lettura senza `grid_dim` di una sequenza generata a `grid_dim=2` (non
solo al tetto stesso); i 4 casi reali di coincidenza geometrica trovati
dallo sweep, verificando che `_decode_tiled` al tetto automatico non
fabbrichi mai un capitolo sbagliato; il roundtrip end-to-end sullo
stesso frame di coincidenza, a conferma che il fallback whole-image
recupera comunque il payload corretto. 324 test totali.

### 9.29 Tabella componenti a contenuto libero: colonna "componente" auto-rilevata, ricerca su tutta la riga

Richiesta diretta di sessione: la tabella allarmi (§9.15/§9.21) aveva
uno schema fisso a 2-3 colonne (`codice_allarme,nome_componente[,documento_procedura]`).
Nella realtà il file può avere colonne arbitrarie in ordine arbitrario
(`nome componente,codice,funzione,allarme,procedure,ricambio,info` — dove
`info` può essere ore di utilizzo dal contaore o una nota di manutenzione
programmata), e la ricerca deve funzionare per qualunque colonna, non
solo allarme/componente, mostrando **sempre la riga intera trovata**, non
solo un'evidenziazione. Quattro decisioni di design proposte e confermate
esplicitamente dall'utente prima di scrivere codice (tutte le opzioni
"Consigliato"):

1. **Colonna componente auto-rilevata per contenuto, non per
   intestazione**: dopo il caricamento, si conta per ogni colonna quanti
   valori (trim + lowercase) corrispondono esattamente a un nome reale
   nella distinta base 3D — la colonna con più corrispondenze vince,
   nessuna corrispondenza → nessuna colonna component-driving (le righe
   restano comunque cercabili/visualizzabili, solo senza evidenziazione
   3D). Zero configurazione, funziona con qualunque intestazione/ordine.
2. **Ricerca su tutta la riga, mostra tutte le righe corrispondenti**:
   cercare un valore presente in una qualunque cella di una qualunque
   colonna mostra tutte le righe che lo contengono, con l'unione dei
   valori della colonna-componente di quelle righe evidenziata insieme
   sul modello (stesso principio già in uso per un allarme
   multi-componente, generalizzato a "qualunque ricerca
   multi-riga"). Una riga puramente informativa (nessun valore
   riconosciuto in colonna-componente) non è una ricerca fallita: si
   mostra comunque, semplicemente senza toccare la vista 3D, invece di
   attenuare tutto il modello senza motivo.
3. **Colonna "procedure" resta solo testo mostrato nella riga**: nessuna
   apertura automatica di un documento collegato — più semplice, non
   presuppone che il valore corrisponda esattamente al nome di un
   documento nel bundle (l'idea di apertura automatica, mai
   implementata, resta così, non riesumata).
4. **Riga di intestazione obbligatoria**: niente più euristica che
   indovina se la prima riga è intestazione o dato (il vecchio parser
   guardava parole chiave tipo "codice"/"allarme" nella prima riga) — con
   colonne a piacere non c'è modo di sapere cosa significhi una colonna
   senza un'intestazione dichiarata. Un'intestazione mancante/vuota
   solleva un errore chiaro invece di indovinare.

**`ComponentTable` (nuova classe, `balzar/viewer3d.py`)**: modello
tabellare generico — `.headers: list[str]`, `.rows: list[list[str]]`,
`.all_values() -> set[str]` (ogni cella non vuota, usata come candidati
per `collapse_names`), `.to_json_dict()`. `parse_component_table_text`/
`parse_component_table` sostituiscono `parse_alarm_csv_text`/
`parse_alarm_csv`: riga 0 è **sempre** l'intestazione (mai indovinata),
un'intestazione vuota solleva `ValueError` con "intestazione" nel
messaggio; righe corte vengono riempite con celle vuote, righe lunghe
troncate alla larghezza dell'intestazione; righe vuote saltate.

**Il problema uovo-e-gallina con `collapse_names`**: `generate_bom`/
`scene3d_to_glb` hanno bisogno di `collapse_names` come **input** prima
che BOM/GLB esistano, ma l'auto-rilevamento della colonna componente ha
bisogno della BOM già pronta (per sapere i nomi reali da confrontare).
Risolto usando `ComponentTable.all_values()` — letteralmente ogni
valore non vuoto di ogni cella dell'intera tabella, indipendentemente
dalla colonna — come insieme di candidati per `collapse_names`: un
candidato che non corrisponde a un vero nome di gruppo `Reference3D`
viene silenziosamente ignorato a valle (comportamento già esistente e
documentato in `scene3d.py`/`gltf.py`), quindi passare ogni cella è
sicuro, non solo comodo — aggira il bisogno di sapere "quale colonna è
il componente" prima che la BOM esista.

**Solo il primo CSV marcato allarme in un bundle diventa la tabella
informazioni** (comportamento cambiato deliberatamente rispetto al
design precedente, che concatenava le righe di TUTTI i CSV marcati
allarme perché condividevano per costruzione lo stesso schema a 2
colonne): con schemi arbitrari, concatenare righe tra CSV con colonne
diverse non generalizza in modo sicuro, quindi vince il primo (stesso
principio "il primo vince" già usato altrove nella stessa funzione per
il primo elemento 3D).

**Rinominato**: `window.__BALZAR_ALARM_ROWS__` →
`window.__BALZAR_INFO_TABLE__` (forma `{headers:[...], rows:[[...]]}`,
non più `[[codice,nome],...]`); il campo di risposta web `alarm_rows` →
`info_table` in `handle_encode_3d`/`_handle_render_3d`/
`_handle_render_bundle` (`balzar/webapi.py`) — il campo di **input**
`alarm_csv` (il base64 del CSV da marcare come tabella informazioni)
resta invariato, solo l'output analizzato è stato rinominato, dato che
il concetto "marca questo CSV come tabella allarmi/informazioni" non è
cambiato, è cambiato solo il modello del suo contenuto.

**UI**: nuovo pannello `#search-panel`/`.search-results-table`
(`style.css`, e l'equivalente incorporato in `viewer3d.py`) — una
tabella di risultati sotto la nota di ricerca esistente, aperta/chiusa
con la classe `.open` (non l'attributo `[hidden]`, per evitare
esattamente la collisione di specificità CSS già documentata più volte
in questo progetto). Stessa logica duplicata (non condivisibile come
file, una è incorporata in un f-string Python, l'altra è `app.js`
statico) in `_SELECT_JS`/`app.js`: `detectComponentColumn`,
`loadInfoTable`/`setInfoTable`, `renderResultsTable`, `runSearch`
riscritta per cercare ogni cella e mostrare le righe intere, con
fallback alla vecchia ricerca per nome BOM se non c'è tabella o niente
corrisponde in essa.

**Bug di parità trovato durante la verifica Playwright del percorso
desktop, non nel percorso web**: aprendo un bundle con una tabella già
incorporata (senza upload manuale), il percorso web
(`renderScenePanel` in `app.js`) mostra subito una nota "Tabella
disponibile (N righe, M colonne: ...)" al caricamento, ma il percorso
desktop (`cacheColors()` in `_SELECT_JS`, `balzar/viewer3d.py`) chiamava
`loadInfoTable(window.__BALZAR_INFO_TABLE__)` senza impostare alcuna
nota — zero feedback che una tabella fosse pronta finché l'utente non
eseguiva una ricerca. Corretto specchiando lo stesso testo del percorso
web in `cacheColors()`.

Verificato con Playwright su entrambi i percorsi, contro server reali
(non mock): **web** (devserver locale che instrada `/api/encode_3d` al
vero `handle_encode_3d`) — upload 3DXML sintetico (due istanze di
"Bullone-M6") + CSV a 7 colonne con "componente" come **seconda**
colonna e intestazione non convenzionale (non "nome_componente") →
tabella caricata (nota corretta) → ricerca per codice allarme mostra la
riga intera (tutte e 7 le colonne) → ricerca per un valore presente solo
nella colonna libera "info" trova comunque la riga → ricerca diretta per
nome componente evidenzia la riga BOM (prova diretta dell'auto-
rilevamento content-based, non per posizione/intestazione) → ricerca
senza corrispondenza chiude il pannello onestamente. **Desktop**
(`open_bundle_in_browser` con lo stesso bundle sintetico, servito
realmente, driver Playwright sotto Xvfb) — stessi 4 scenari, più
`?q=<codice>` nell'URL che lancia la ricerca da solo al caricamento
(automazione zero-click, invariata dalla generalizzazione). Nessun bug
di prodotto trovato oltre alla lacuna di parità sopra, corretta durante
la stessa verifica.

Test riscritti: `tests/test_viewer3d.py` (12 test, `TestParseComponentTable`
— colonne arbitrarie in qualunque ordine, intestazione sempre riga 0 senza
euristica, intestazione mancante solleva errore chiaro, righe corte
riempite/lunghe troncate, un valore su più righe, virgola nel nome
preservata dal parser CSV, righe vuote saltate, file vuoto non è un
errore, `all_values()`/`to_json_dict()`). `tests/test_webapi.py`
aggiornato (3 test: forma `info_table` invece di `alarm_rows`, CSV di
test aggiornato con intestazione dato che l'header è ora obbligatorio).
Suite completa: 328 test, tutti verdi.

### 9.30 Limite reale a 65.535 vertici/strip trovato su un vero assieme pesante — corretto, non solo dichiarato

L'utente ha fornito due assiemi 3DXML reali di scala/densità diverse per
misurare dove si muove davvero la pipeline QR 3D (seguito diretto di
§9.24/§9.25, priorità dichiarata di sessione). Il primo (500.756 B) è
**esattamente** il secondo assieme reale già misurato in §9.10 (stessi
88 forme/360 riferimenti/245 istanze) — nessuna nuova misura necessaria
lì. Il secondo (9.219.625 B, "zephyr_h_230v") è territorio nuovo: **316
forme uniche, 2.166 riferimenti, 3.520 istanze, 1.009.940 vertici totali**
— quasi il doppio dei vertici totali della prima assieme più i suoi
344 mesh, un ordine di grandezza sopra qualunque fixture sintetica usata
finora per i benchmark di §9.24.

**Bug reale trovato, non un limite teorico**: `encode_3dxml_file` sul
secondo file si rifiutava con `Scene3DError: forma 'None' con 290192
vertici: supera il limite di 65535 per gli indici a 16 bit` — un limite
già dichiarato esplicitamente in §9.5 ("non ancora visto nella realtà,
ma dichiarato esplicitamente") e **ora visto per davvero**. Investigando
prima di correggere (misura, non stimare): **due** forme del file
superano 65.535 vertici (290.192 e 153.500), e rappresentano **43,9%**
di tutti i vertici dell'assieme (443.692 su 1.009.940) — non un caso
limite trascurabile, quasi metà del contenuto reale. Controllando più a
fondo è emerso un **secondo** bug distinto, mascherato dal primo (il
controllo sui vertici falliva per primo): il conteggio delle strisce di
triangoli per forma (`len(shape.strips)`) era anch'esso impacchettato
come `<H>` (uint16, limite 65.535) — la stessa forma da 290.192 vertici
ha **80.535 strisce**, oltre anche questo limite, un problema di
*conteggio* non solo di *valore indice*, che sarebbe scattato comunque
anche per una forma con meno di 65.535 vertici ma più di 65.535 strisce.

**Fix in `balzar/scene3d.py`** (`_serialize`/`_deserialize`), niente
troncamento silenzioso né semplificazione geometrica — solo allargare i
campi che si sono rivelati troppo stretti:
- `n_strips` (conteggio strisce per forma) passa da `<H>` a `<I>`
  **incondizionatamente** — costo trascurabile (2 byte in più per forma,
  prima della compressione) per ogni forma, anche le 314 forme normali
  del file che non ne avrebbero bisogno.
- I **valori** degli indici dentro ogni striscia restano `<H>` (uint16)
  per il caso comune, e diventano `<I>` (uint32) **solo** per la forma
  che ne ha davvero bisogno — derivato dal conteggio vertici già
  memorizzato (`n_verts > 65535`), non un nuovo flag per-forma: zero
  byte aggiuntivi per le forme che restano sotto il limite.
- Versione del payload alzata da 1 a 2 (il campo esiste già nell'header
  proprio per questo). **Mantenuta la lettura della versione 1**: la
  libreria locale desktop (`balzar/library.py`, §9.22) persiste file
  `.b3d` sul disco tra un aggiornamento di balzar e l'altro — un
  payload versione 1 già salvato da un utente reale è una preoccupazione
  concreta, non ipotetica, quindi `_deserialize` resta in grado di
  leggerlo con la larghezza fissa originale (un payload versione 1, per
  costruzione, non ha mai potuto contenere una forma fuori limite, visto
  che `encode_payload` sollevava un errore invece di scriverla).

**Costo reale misurato sul file già documentato** (§9.10, nessuna forma
oltre il limite): payload 239.491 B → **239.546 B** (+55 B, +0,02%) —
esattamente l'ordine di grandezza atteso per 88 forme × 2 byte extra su
`n_strips`, quasi azzerato dalla compressione deflate. Nessun costo
percepibile per l'assieme comune.

**Numeri reali sul nuovo assieme, ora che l'encoding non si rifiuta
più**: payload **5.215.937 B** in 11,7 s, **1,77×** rispetto al 3DXML
sorgente (9.219.625 B) — un rapporto molto più basso del 2,09× già
misurato sull'assieme più piccolo, e la ragione è chiara guardando i
dati, non un mistero: il rapporto di instancing di questo assieme è
più debole (3.520 istanze / 316 forme uniche ≈ 11×, contro un rapporto
più alto sull'altro assieme), e il 43,9% dei vertici vive in due
sole superfici tessellate non ripetute — geometria che il modello di
deduplicazione di balzar (il cuore del suo guadagno, §9.2) non ha modo
di comprimere, perché non si ripete da nessuna parte. Errore di
quantizzazione medio: 0,000507 mm, ben dentro tolleranza CAD — la
fedeltà non è in discussione, solo la dimensione.

**Conclusione onesta, la parte che conta per la priorità 1 di
sessione**: a 5,2 MB questo payload richiederebbe **1.774 capitoli QR,
111 fotogrammi** a `grid_dim=4` (misurato con `chunk_payload` reale, non
stimato) — ben oltre la sofferenza già nota per code di dozzine di
fotogrammi (§9.10/§9.24), un ordine di grandezza sopra qualunque caso
finora reso pratico. Il collo di bottiglia per un assieme di questa
natura **non è la velocità di generazione/lettura QR** (il fronte già
ottimizzato in §9.24/§9.25) — è che il payload stesso, anche dopo la
deduplicazione reale di balzar, resta troppo grande per il trasporto
fisico via QR quando la geometria non si presta alla deduplicazione
(poche forme uniche molto dense, invece di molte istanze di forme
piccole). Nessuna quantità di parallelizzazione nella generazione/
lettura QR risolve un problema di dimensione del contenuto sorgente.
La leva utile per *questa* classe di assieme è diversa da quella già
esplorata: una **semplificazione/decimazione geometrica** delle forme
uniche di grandi dimensioni con poco riuso (non la stessa cosa del
punto 6 di sessione, che riguarda il nascondere sotto-assiemi per
riservatezza, non ridurre la densità della mesh) — non ancora
implementata, richiede una decisione di prodotto esplicita (è
un'ulteriore perdita di fedeltà geometrica, oltre alla quantizzazione
int16 già in uso, e va misurata/dichiarata con lo stesso principio di
onestà già seguito per `mean_vertex_error`).

Verificato: suite completa 330 test (2 nuovi + 1 riscritto in
`tests/test_scene3d.py::TestQuantizationAndCompactTransforms` — round-trip
di una forma sopra 65.535 vertici con indici larghi, round-trip di una
forma sopra 65.535 strisce, lettura di un payload versione 1 genuino
costruito a mano byte-per-byte per fissare esattamente il layout
pre-fix), tutti verdi. Nessun file 3DXML reale committato nel repository
(stesso motivo di copyright già visto per gli altri assiemi reali,
§9.2/§9.10) — solo la fixture sintetica nei test.

### 9.31 Scala target realistica misurata (~14 KB sorgente) + `merge_names`, strumento di riserva opzionale

Seguito diretto di sessione a §9.30: l'utente ha chiarito che la
semplificazione principale di un assieme pesante **avviene fuori da
balzar**, in una fase preliminare CAD (unendo in singoli oggetti le
istanze e i sotto-assiemi che non servono mostrare individualmente,
prima ancora di esportare il 3DXML) — balzar riceve già un file
tipicamente nell'ordine di **10-100 KB**, non i 9,2 MB dell'assieme
zephyr_h_230v misurato in §9.30. Due filoni di lavoro distinti, in
sequenza, come richiesto esplicitamente ("entrambi").

**1) Validazione reale sulla scala target.** Costruito un 3DXML
sintetico rappresentativo (12 parti uniche piccole — bulloni,
rondelle, staffe, non superfici dense — con ripetizione realistica:
viti/rondelle/dadi ripetuti fino a 24 volte, parti strutturali 1-4
volte, **111 istanze totali**, nessun file CAD reale coinvolto, stesso
principio di copyright già seguito altrove) e misurata l'intera
pipeline reale, non stimata:

| Passo | Tempo | Risultato |
|---|---|---|
| Sorgente 3DXML | — | 14.011 B |
| Encode (`encode_3dxml_file`) | 0,009 s | payload 5.339 B (2,62× vs sorgente) |
| Generazione QR (`payload_to_qr_frames`, grid_dim=4) | 0,541 s | **1 solo fotogramma** (2 capitoli) |
| Lettura (`LiveScanner`) | 0,257 s | bit-identico |
| Decodifica + export GLB | 0,003 s | 62.240 B, 12 forme/12 parti BOM |

**Tempo totale pipeline (esclusa l'acquisizione fisica): meno di un
secondo** — non i 43-92 secondi già misurati per l'assieme da 9,2 MB in
§9.24/§9.25/§9.30, e ben sotto la stima "ordine dei secondi, non
minuti" fatta prima di misurare. Conferma diretta, con numeri reali,
che per la scala che l'utente prevede di usare in produzione (post-
semplificazione esterna) la pipeline QR è già pienamente pratica senza
alcuna ulteriore ottimizzazione — il lavoro di parallelizzazione già
fatto in §9.24/§9.25 resta rilevante solo per il caso limite (assiemi
grandi non ancora semplificati), non per il flusso di produzione atteso.

**2) `merge_named_groups` — strumento di riserva opzionale, NON il
percorso principale.** Anche se la semplificazione principale avviene
altrove, l'utente ha chiesto uno strumento equivalente dentro balzar
per i casi non coperti da quel processo esterno. Decisioni tecniche
confermate esplicitamente prima di scrivere codice:
1. **Parametro indipendente** da `collapse_names` (generate_bom/
   scene3d_to_glb, §9.21) — quel meccanismo raggruppa solo la vista
   (BOM/evidenziazione), la geometria nel payload resta quella
   originale; `merge_names` invece **concatena davvero** vertici e
   triangoli in un'unica `Shape`, eliminando le voci Reference/
   Instance3D separate — due liste distinte, tipicamente uguali in
   pratica ma non vincolate a esserlo.
2. **Solo concatenazione, zero perdita aggiuntiva** — nessuna
   decimazione/riduzione poligoni. Le parti mantengono la propria
   posizione reale (le RelativeMatrix vengono composte dal gruppo verso
   ogni foglia e applicate ai vertici), zero perdita oltre la
   quantizzazione int16 già esistente nell'encoder.

**Implementazione** (`balzar/scene3d.py`): `_compose_matrices`/
`_apply_matrix` (composizione affine standard, stessa convenzione
riga-maggiore già verificata algebricamente per `gltf.py` in §9.7,
non una nuova convenzione) + `merge_named_groups(scene, merge_names)`
— per ogni `Reference` di gruppo (ha figli, non è già una foglia) il
cui nome è in `merge_names`, cammina l'albero sotto di essa componendo
i trasformi, concatena la geometria di ogni foglia raggiunta in
**un'unica** `Shape` nel sistema di coordinate proprio del gruppo (così
il risultato si muove correttamente ovunque il gruppo stesso sia
istanziato), e sostituisce il `Reference` del gruppo con una foglia
pura (`shape_index` impostato, `children=[]`). Un nome che non
corrisponde a nulla, o che corrisponde a una foglia già atomica (senza
figli), viene **ignorato silenziosamente** — stessa convenzione già
usata da `collapse_names`. Nuova `_prune_unreachable(scene)`:
indispensabile, non opzionale — senza di essa i vecchi Reference/Shape
resi orfani dalla fusione resterebbero comunque serializzati nel
payload, vanificando il motivo stesso della fusione (i loro byte non
spariscono da soli). Mai forzato: `merge_names=None` (il default)
restituisce la scena **invariata** (stesso oggetto), zero costo/rischio
per chi non lo usa.

**Scoperta reale, non assunta, e opposta all'intuizione iniziale**:
misurato l'effetto della fusione su due scenari sintetici distinti,
non uno solo, perché il primo tentativo di test ha rivelato che
l'ipotesi di partenza era sbagliata:
- **Molte parti DISTINTE usate una sola volta ciascuna** (es. 50
  staffe/coperchi unici sotto un sotto-assieme, mai ripetuti): la
  fusione **aiuta davvero** — 1.319 B → 650 B, **2,03×** più piccolo,
  perché rimuove l'overhead per-parte (nome, struttura Reference/
  ReferenceRep/InstanceRep/Instance3D) senza perdere alcun beneficio di
  deduplicazione, dato che non ce n'era nessuno da perdere (ogni forma
  era già usata una sola volta).
- **Molte istanze RIPETUTE della stessa forma** (es. 200 bulloni
  identici, il caso "viti" che sembrava il bersaglio naturale): la
  fusione **peggiora**, non migliora — misurato direttamente (non
  assunto): payload sale da 1.512 B a 2.346 B. Il motivo è chiaro
  misurando, non ipotizzando: la rappresentazione non fusa sfrutta già
  al massimo la deduplicazione di balzar (1 sola forma memorizzata +
  200 trasformi economici, quasi identici byte-per-byte e quindi
  compressi benissimo da DEFLATE); fondere **duplica** i dati di
  vertice già deduplicati in 600 posizioni uniche quantizzate — dati ad
  alta entropia che DEFLATE comprime molto peggio di 200 record quasi
  identici. **Il caso in cui questo strumento aiuta davvero è quindi
  l'opposto di quello intuitivo**: parti uniche non ripetute, non parti
  ripetute come viti/bulloni (quelle, balzar le comprime già meglio da
  solo).

**Superfici collegate, tutte opzionali, mai forzate**:
`encode_3dxml_file(path, merge_names=None)`; CLI
`balzar encode-3d file.3dxml --merge-names "Nome1,Nome2"`; web API
`handle_encode_3d` accetta un campo `merge_names` (stringa separata da
virgole) opzionale; GUI desktop — `open_file()` chiede (solo per un
`.3dxml`, un `simpledialog.askstring` sul thread principale, **prima**
di avviare il thread di encoding in background, dato che i dialog
Tkinter non possono girare dal worker thread) un elenco opzionale di
nomi da fondere, lasciato vuoto per il comportamento di sempre.
**Non wired sulla demo web** (`index.html`/`app.js`): nessun campo
frontend per digitare `merge_names` — scelta deliberata, non
dimenticanza, dato che è uno strumento di riserva secondario e la demo
web è dichiaratamente "solo vetrina, non il prodotto" (§1); il campo
backend esiste ed è testato, raggiungibile da chi chiama l'API
direttamente.

Verificato: 8 nuovi test in `tests/test_scene3d.py::TestMergeNamedGroups`
(fusione con posizioni mondo corrette, round-trip attraverso il payload
— confrontato contro la scena già quantizzata, stesso principio del
self-check di `encode_3dxml_file`, non contro l'originale a piena
precisione — riduzione di dimensione per parti distinte, **aumento** di
dimensione per parti ripetute misurato esplicitamente non solo
menzionato, nome non corrispondente ignorato, nome su una foglia già
atomica è un no-op, `encode_3dxml_file` accetta il parametro opzionale),
2 in `tests/test_cli.py`, 2 in
`tests/test_webapi.py::TestHandleEncode3D`. Smoke test manuale sotto
Xvfb per il dialog GUI (dialog monkeypatchato, mainloop reale pompato):
prompt mostrato solo per `.3dxml`, nomi passati correttamente fino a
`encode_3dxml_file`, job completato senza errori. 342 test totali.

## 10. Comandi utili per riprendere il lavoro

```bash
python3 -m unittest discover -s tests        # 342 test (alcuni opzionali su qrcode/pyzbar), deve restare verde
python3 -m balzar chunks any_file.pdf --raw --qr --grid-dim 2 -o qr/  # trasporto QR di byte grezzi (§2.4c)
python3 -m balzar scan qr/*_qr_frame_*.png --raw -o rebuilt.pdf
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

## 11. Documento di visione/scopo

`VISIONE.md` (radice del repository) raccoglie in forma leggibile, senza i
dettagli tecnici di sessione, la visione del progetto (§1), le applicazioni
target (§6), le idee esterne valutate (§7) e il posizionamento di prodotto
Balzar Studio/Balzar Live (§9.19). È un documento **duplicato**, non
sostitutivo: le sezioni corrispondenti restano qui, questo file resta la
fonte tecnica di verità; `VISIONE.md` è la vista di sintesi condivisibile
con chi non ha bisogno del log di sessione completo. Tenerli allineati a
mano quando cambia la sostanza di una delle sezioni duplicate.
