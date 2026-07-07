# balzar

**Generazione deterministica di contenuti ad alta complessità da descrizioni minime.**

Un contenuto digitale (immagine o sequenza di frame) non viene memorizzato:
viene **rigenerato deterministicamente** a partire da un payload compatto —
seme + programma di regole — che entra in un singolo QR code. Il dato diventa
minimo, la descrizione diventa il contenuto.

```
INPUT MINIMO (seed + istruzioni)     payload binario, ~200-500 byte
        ↓
INTERPRETE DETERMINISTICO            balzar/interpreter.py
        ↓
APPLICAZIONE DI REGOLE SUCCESSIVE    balzar/ops.py
        ↓
RICOSTRUZIONE OUTPUT COMPLETO        PNG / sequenze di frame, MB di pixel
```

Implementazione in **puro Python, zero dipendenze** (solo stdlib): il
determinismo è totale per costruzione e il decoder gira ovunque, inclusi
sistemi embedded.

## Risultati misurati (esempi inclusi)

| Programma | Payload | Output | Espansione |
|---|---|---|---|
| `examples/animazione.bzr` | 210 byte | 24 frame 256×256 (4,7 MB RGB) | ~22.500× |
| `examples/pattern_tile.bzr` | 276 byte | 1024×1024 (3,1 MB RGB) | ~11.400× |
| `examples/frattale.bzr` | 230 byte | 768×512 (1,2 MB RGB) | ~5.100× |
| `examples/schema_tecnico.bzr` | ~490 byte | 800×600 (1,4 MB RGB) | ~2.900× |
| `examples/etichetta_bom.bzr` (esploso + testo BOM) | 559 byte | 640×520 (998 KB RGB) | ~1.786× |
| `examples/sequenza_montaggio.bzr` (10 step navigabili + BOM crescente) | 766 byte | 10 frame 760×520 (11,9 MB RGB) | ~15.478× |
| `examples/sequenza_flangia_cad/` (3 file DXF, ingeriti come sequenza) | 169 byte | 3 frame 800×800 (5,76 MB RGB) | ~34.083× |
| `examples/flangia_esploso.dxf` (esploso automatico, 6 layer, 6 step) | 303 byte | 7 frame 800×800 (13,44 MB RGB) | ~44.356× |

Tutti i payload entrano in un QR code versione 40 (capacità ~2953 byte). Per
`etichetta_bom.bzr` vale la pena notare il confronto diretto: il PNG della
stessa identica immagine pesa 5.496 byte e **non** entra in un QR — il
payload balzar (559 byte) sì, con ampio margine (dettagli in `CLAUDE.md` §8).
Per `sequenza_montaggio.bzr` il confronto più parlante non è contro il raw
RGB: è che i 10 frame codificati **indipendentemente** (10 PNG separati)
pesano 75× di più (57.810 byte) — l'intera sequenza sta comunque in un solo
QR, i 10 PNG no.

## Uso rapido

```bash
# rigenera il contenuto da un programma DSL
python3 -m balzar render examples/frattale.bzr -o out/

# programma -> payload binario compatto (o base64, testo pronto per QR)
python3 -m balzar encode examples/pattern_tile.bzr -o pattern.bzp
python3 -m balzar encode examples/pattern_tile.bzr --base64 -o pattern.b64

# il render accetta indifferentemente sorgente, payload binario o base64:
# l'output è bit-identico in tutti e tre i casi
python3 -m balzar render pattern.bzp -o out/

# payload -> programma canonico; statistiche di espansione
python3 -m balzar decode pattern.bzp
python3 -m balzar info pattern.bzp

# encoder automatico: immagine arbitraria -> payload (richiede Pillow)
pip install pillow
python3 -m balzar encode-image foto.png -o foto.bzp

# video: GIF animata -> payload delta-based; render con anteprima GIF
python3 -m balzar encode-video animazione.gif -o video.bzp --max-dim 400
python3 -m balzar render video.bzp -o out/ --gif

# vettoriale diretto: SVG/DXF -> payload, nessun raster in mezzo
python3 -m balzar encode-vector disegno.svg -o disegno.bzp

# sequenza multi-file (vettoriali omogenei o immagini raster) -> un payload
python3 -m balzar encode-sequence step1.dxf step2.dxf step3.dxf -o sequenza.bzp

# esploso automatico: un CAD/SVG a piu' layer -> animazione di esploso
python3 -m balzar explode-vector disegno.dxf -o esploso.bzp --steps 6

# supporto fisico: payload -> immagine QR (1 codice o griglia auto) e ritorno
pip install qrcode pyzbar pillow   # pyzbar richiede anche libzbar0 di sistema
python3 -m balzar chunks video.bzp -o qr/ --qr
python3 -m balzar scan qr/video_qr.png -o ricostruito.bzp --render out/

# capitoli come solo testo base64 (senza generare l'immagine QR)
python3 -m balzar chunks video.bzp -o capitoli/
python3 -m balzar assemble capitoli/ -o ricostruito.bzp

# applicazione desktop (il prodotto)
python3 -m balzar gui

# test
python3 -m unittest discover -s tests
```

## App desktop (il prodotto)

Il prodotto finale è un **programma offline tipo zipper**: apri un file,
lo comprimi in payload generativo, lo salvi; apri un `.bzp` e lo
"decomprimi" rigenerandolo. `balzar/gui.py` è l'app (Tkinter, quindi
stdlib pura + Pillow), con anteprima fianco a fianco originale/rigenerato
(animata per i video, con controlli ◀ Indietro / Pausa/Play / Avanti ▶ e
indicatore "Step N/M" per navigare manualmente una sequenza multi-frame —
utile per procedure di montaggio step-by-step, non solo per la riproduzione
automatica), statistiche di guadagno oneste, salvataggio `.bzp`/`.bzr`,
export del contenuto rigenerato (PNG/GIF) e dei capitoli QR.

**Libreria locale** (`balzar/library.py`): ogni apertura di un file
esistente o scansione di un QR (il lato Balzar Live, non un encode
fresco) viene salvata automaticamente in `~/.balzar/library/` — utile
per lo scenario "3 macchine, 3 QR scansionati": il bottone "Libreria…"
elenca ogni voce salvata, permette di riaprirne una senza riscansionare
(anche dopo aver chiuso e riaperto l'app), e di chiudere/eliminare
quelle non più utili. Riaprire due volte la stessa voce riusa lo stesso
server locale invece di aprirne uno nuovo ogni volta — la stessa
deduplica copre anche un file appena codificato da Balzar Studio, mai
salvato in libreria (CLAUDE.md §9.23, punto 2). Vedi CLAUDE.md §9.22
per il bug di crash risolto nel percorso di scansione (un QR con un
assieme 3D/bundle andava in crash prima di questa sessione) e per il
bug di risorsa risolto nel visualizzatore, e §9.23 per un audit
successivo che ha trovato e corretto altri 10 problemi (selezione nel
pannello, scrittura del manifest non atomica, gestione eccezioni,
pulizia dei file temporanei, blocco della UI in chiusura, e altro).

Per distribuirla come eseguibile singolo senza Python installato:

```bash
pip install pyinstaller pillow
pyinstaller --onefile --windowed --name balzar balzar-app.py
# risultato in dist/balzar (.exe su Windows, binario su Linux/macOS)
```

## Video (sequenze di frame)

`balzar/video.py` implementa il modello differenziale della sezione 4.3
sul caso reale: una GIF animata (o sequenza di immagini) diventa **un solo
programma** in cui il frame 0 è codificato per intero e ogni frame
successivo costa solo i pixel che cambiano (coperti a rettangoli, con
`FRAME` a separare gli stati). Non è il "flipbook" di frame indipendenti:
la ridondanza temporale — che è dove vive quasi tutta la comprimibilità di
un video — viene sfruttata, non buttata.

Misurato sul test incluso (palla che attraversa una griglia tecnica,
320×240, 30 frame): payload 8.1KB contro 6,9MB di RGB grezzo (**849×**),
lossless, e più del doppio più compatto della somma dei 30 frame
codificati indipendentemente. Come sempre: contenuto sintetico/strutturato
sì, video da fotocamera no (ogni frame è "rumore" per l'encoder).

## Supporto fisico (serie di QR)

Un payload più grande di un QR si spezza in **capitoli autodescrittivi**
(`chunk_payload` / `assemble_chunks` in `balzar/payload.py`):

```
"BZC1" | u16 indice | u16 totale | u32 CRC-32 del payload intero | dati
```

Ogni capitolo sta in un QR v40 (~2953 byte) e porta con sé posizione e
checksum dell'insieme: i codici si possono stampare in serie e scansionare
**in qualsiasi ordine**; il riassemblaggio verifica l'integrità end-to-end.

`balzar/qr.py` (richiede `qrcode` + `pyzbar`, non nel motore core) chiude
il cerchio: se il payload sta in un QR genera **un'immagine**, altrimenti
lo spezza in capitoli e li dispone in **una griglia auto-dimensionata**
nella stessa immagine — l'esperienza resta "scansiona questa foto" sia per
1 QR sia per 15. La lettura usa ZBar (`pyzbar`), non il detector nativo di
OpenCV: verificato che legge in modo affidabile molti più QR in un solo
scatto (15/15 contro 5/15 in un test con `cv2.QRCodeDetector`). I byte
grezzi non sopravvivono al giro stampa/foto/lettura (verificato: si
corrompono) — i capitoli passano per base64, come `encode --base64`.
`balzar scan foto.jpg` (CLI) e il pulsante "Scansiona foto QR" (GUI)
fanno il percorso inverso in un colpo solo. Una pagina di QR diventa così
il supporto fisico di un contenuto che viene
rigenerato, non letto: il volume informativo del supporto è il volume
dell'output generato, non dei byte stampati.

**Sequenze multi-frame** (`payload_to_qr_frames`/`frames_to_gif`/
`frames_to_files`/`LiveScanner` in `balzar/qr.py`, non ancora esposte in
CLI/GUI): un tetto esplicito di QR per frame (`grid_dim`, default 4→16)
invece della griglia unica illimitata sopra, per restare leggibile a
dimensione fisica fissa — payload grandi diventano una **sequenza** di
griglie, bundlabile come GIF animata (schermo che cicla i frame da solo,
senza perdita per contenuto bianco/nero) o come PNG separati (stampa).
`LiveScanner` accumula i capitoli su più foto nel tempo, in qualsiasi
ordine, tollerando ripetizioni. Benchmark reale su una griglia 8×8 (64
QR): affidabile solo in una finestra stretta attorno alla stessa
risoluzione già nota come "lenta, senza guadagno" per il 4×4, con un
tempo di decodifica per frame ~15-18× peggiore a parità di codice —
`grid_dim=4` resta il default consigliato, un payload grande accetta
più frame invece di frame più densi (dettagli e numeri in `CLAUDE.md`
§2.4b).

## Il linguaggio (DSL)

Un'istruzione per riga, argomenti `chiave=valore`; parentesi e virgole sono
zucchero sintattico, quindi `SHIFT(region=A, dx=2, dy=1)` e
`SHIFT region=A dx=2 dy=1` sono equivalenti. I commenti iniziano con `#`.
Un valore tra virgolette (`text="QTY 12"`) mantiene spazi/parentesi/virgole
alla lettera — serve per il contenuto di `TEXT`.

```text
CANVAS w=256 h=256 bg=0          # stato base (A)
SEED value=42                    # seme deterministico (S)
PALETTE i=1 rgb=#F5F0E8          # colori indicizzati
REGION name=A x=0 y=0 w=32 h=32  # mappa di indicizzazione spaziale (I)

# --- trasformazioni (T) ---
SHIFT region=A dx=2 dy=1 wrap=1      # traslazione (con o senza wrap)
ROTATE region=A angle=90             # rotazione esatta 90/180/270
MIRROR region=A axis=x               # riflessione
SCALE src=A dst=B                    # riscalatura nearest-neighbour
COPY src=A dst=C                     # copia di regioni
SWAP a=A b=B                         # scambio blocchi
TILE src=A dst=FULL                  # pattern repetition / tiling
FILL region=A color=5                # riempimento
MAP region=A src=3 dst=6             # ricolorazione selettiva
INVERT region=A ncolors=16           # complemento in spazio palette
SETPIX x=3 y=4 color=2               # modifica locale (differenziale)
TEXT x=10 y=10 text="QTY 12" color=0 scale=2   # font bitmap 5x7 incorporato

# --- primitive generative ---
RECT x=2 y=2 w=28 h=28 color=6 fill=0
LINE x1=0 y1=0 x2=31 y2=31 color=9   # Bresenham, rasterizzazione esatta
CIRCLE cx=16 cy=16 r=10 color=5 fill=1
NOISE region=FULL color=1 density=0.01   # rumore guidato dal seme
SCATTER region=A color=7 count=30        # punti deterministici dal seme
FRACTAL type=mandelbrot region=A cx=-0.6 cy=0 scale=1.4 iter=48
FRACTAL type=sierpinski region=B color=5 depth=5
FRACTAL type=triangle region=C color=6 depth=8

# --- struttura ---
LOOP var=i count=32                  # catena generativa composta
  REGION name=ROW x=0 y=i*32 w=1024 h=32
  SHIFT region=ROW dx=i*8 dy=0      # gli argomenti sono espressioni su i
ENDLOOP
FRAME                                # emette lo stato corrente come frame
```

La regione predefinita `FULL` è l'intero canvas. Ogni argomento numerico può
essere un'espressione aritmetica (`+ - * / // % **`) sulle variabili di loop:
è ciò che permette a poche istruzioni di descrivere strutture grandi e
regolari. Se il programma non contiene `FRAME`, lo stato finale è l'unico
frame di output.

## Formato payload

```
"BZR1" | uint32 lunghezza | uint32 CRC-32 | deflate(programma canonico)
```

Il payload codifica la **forma canonica** del programma (commenti rimossi,
spazi normalizzati): sorgenti cosmeticamente diversi producono payload
byte-identici, e il CRC rileva payload corrotti. La codifica base64
(`encode --base64`) produce il testo da inserire direttamente in un QR code.

## Garanzie di determinismo

Stesso payload ⇒ stessi pixel, sempre, su ogni piattaforma:

- **niente floating point dove conta**: la griglia è a indici di palette e
  tutte le trasformazioni sono operazioni intere esatte (rotazioni solo ad
  angoli retti, scaling nearest-neighbour, Bresenham per le linee);
- **PRNG proprio** (`xorshift64*` + `splitmix64`, `balzar/rng.py`): la
  sequenza pseudo-casuale fa parte del contratto di formato, nessuna
  dipendenza da `random` o dalla versione di Python;
- **espressioni totali**: il valutatore accetta solo aritmetica su interi e
  variabili di loop — niente chiamate, niente stato, niente I/O;
- il frattale di Mandelbrot usa double IEEE-754 con operazioni elementari,
  riproducibili bit-a-bit tra build di CPython.

I test in `tests/test_determinism.py` verificano il contratto: doppio render
identico, render da payload identico al render da sorgente, sequenza PRNG
bloccata per regressione.

## Encoder automatico (immagine -> programma)

`balzar/encoder.py` implementa il pezzo mancante della sezione 5.1: prende
pixel RGB arbitrari (via `balzar/imageio.py`, l'unico modulo che dipende da
Pillow — decodificare PNG/JPEG da zero è fuori scope) e li riduce a un
programma DSL, best-effort:

1. **quantizzazione palette**: esatta e lossless se l'immagine ha già
   <=256 colori (icone, screenshot, pixel art, export CAD); altrimenti
   quantizzatore percettivo median-cut (<=256 colori adattati alla
   distribuzione reale, non una griglia fissa), con l'errore medio colore
   introdotto sempre dichiarato (`EncodeResult.mean_color_error`, 0.0 se
   esatta) — mai un booleano lossless/lossy piatto;
2. **rilevamento tiling**: se l'intero canvas è periodico, viene codificata
   una sola piastrella + `TILE`;
3. **copertura greedy a rettangoli**: scansione riga per riga che
   massimizza ogni blocco di colore uniforme (`RECT ... fill=1`), pixel
   isolati come `SETPIX`;
4. **auto-verifica**: il programma generato viene renderizzato e confrontato
   pixel-per-pixel con la sorgente quantizzata prima di essere restituito —
   non si dichiara mai "lossless" senza averlo controllato.

Il risultato è onesto per costruzione: su contenuti a blocchi piatti o
pattern ripetuti il guadagno è enorme (nei test, migliaia di×); su rumore
o foto il payload può risultare **più grande** del raw RGB, e il tool lo
segnala invece di nasconderlo — è il limite di Kolmogorov della sezione 8
reso misurabile. Il rilevamento di linee/cerchi (per contenuti vettoriali
con bordi non assiali, es. icone con curve) non è ancora implementato: è
il limite noto di questa v1, richiederebbe un fitting tipo Hough.

## Ingestione vettoriale (SVG/DXF → DSL, no raster)

`balzar/vectorio.py` ingerisce direttamente file SVG e DXF — nessuna
rasterizzazione, nessuna quantizzazione: un cerchio nel file sorgente è
già un cerchio con centro e raggio espliciti, si mappa 1:1 su `CIRCLE`
esistente. Zero dipendenze nuove (SVG via `xml.etree` stdlib, DXF con un
parser di gruppi ASCII scritto da zero).

```bash
python3 -m balzar encode-vector examples/flangia_sorgente.svg -o f.bzp
python3 -m balzar encode-vector examples/flangia_sorgente.dxf -o f.bzp
```

Risolve esattamente il problema del testo/cerchi "fotografati": uno
screenshot passa per quantizzazione colore + copertura a rettangoli
(bordi curvi = tante micro-istruzioni, testo anti-aliased = centinaia di
colori). Un `<circle>` SVG o un'entità `CIRCLE` DXF non ha bisogno di
nessuna delle due cose. Stesso discorso per il testo: `<text>` SVG e
`TEXT` DXF diventano direttamente la nostra operazione `TEXT` — lo stesso
font bitmap esatto usato in `etichetta_bom.bzr`, non pixel quantizzati.

Supportate: `RECT`/`CIRCLE`/`LINE` (anche da `polyline`/`polygon`/`path`
con solo comandi `M`/`L`/`Z`), `TEXT`, gruppi con `translate` (SVG),
colori ACI comuni 1-9 (DXF), **curve `SPLINE` DXF** (NURBS/B-spline,
campionate e convogliate in segmenti `LINE`: il DSL non ha una
primitiva curva, quindi si approssima la curva invece di aggiungerne
una — stesso principio già usato per `LWPOLYLINE`). Non supportate —
**saltate con il motivo esatto**, mai ignorate in silenzio: curve
(`C`/`Q`/`A` negli SVG path), trasformazioni diverse da `translate`,
archi DXF (`ARC`/`ELLIPSE`), SPLINE definite solo da fit point senza
punti di controllo, colori ACI fuori dalla tabella nota (resi in grigio
neutro, dichiarato). A differenza dell'encoder raster non c'è un
originale rasterizzato da cui verificare un lossless bit-a-bit: la
garanzia qui è "ogni elemento convertito è geometricamente esatto" (per
le SPLINE, "approssimato con una tolleranza fissa dichiarata" —
`SPLINE_SAMPLES = 64`), non "pixel-perfect rispetto a un render di
riferimento" (per cui servirebbe un motore di rendering SVG/DXF esterno,
fuori scope). **Per contenuto ricco di curve, l'export SVG è la resa
fedele consigliata**: il nostro `png.py` non fa anti-aliasing (linee
Bresenham pure), quindi anche una curva ben campionata resta a scalini
nel PNG — lo stesso output esportato come SVG e aperto in un browser è
visibilmente più pulito, gratis, grazie all'anti-aliasing nativo.

Testato su un logo CAD reale multi-spline (118 entità `SPLINE`, file di
terzi non incluso nel repository per copyright): 0 entità saltate,
payload 32.172 B contro 330.991 B del DXF grezzo — **il punto che conta
è che né l'uno né l'altro entrano in un solo QR**, ma il payload ne
richiede 15 contro i 151 del sorgente grezzo. Esempio incluso (soggetto
generico): `examples/curva_spline.dxf`.

## Sequenze multi-file ed esploso automatico (CAD)

Due strumenti costruiti sopra `vectorio.py`, per due problemi diversi:

**`balzar encode-sequence`** — più file separati (invece di un video/GIF
già montato) diventano un solo payload multi-frame. Vettoriali: solo
SVG **oppure** solo DXF, mai misti; il delta tra step è un dedup
testuale (una riga già disegnata in uno step precedente non costa nulla
in quello dopo) — corretto per contenuto che si accumula (pezzi che
compaiono), non per contenuto che si sposta. Raster: ogni file è un
fotogramma indipendente, forzato su una dimensione condivisa e passato
al vero delta a pixel di `video.py`.

```bash
python3 -m balzar encode-sequence \
    examples/sequenza_flangia_cad/step1_carcassa.dxf \
    examples/sequenza_flangia_cad/step2_flangia.dxf \
    examples/sequenza_flangia_cad/step3_bulloni.dxf \
    -o sequenza.bzp
# 3 file DXF -> 3 frame, 800x800, 169 byte (contro 5,76 MB RGB equivalente)
```

**`--mode independent`** — stesso comando, ma per quando i file **non**
sono una sequenza: un mucchio di file scorrelati che vuoi solo codificare
in un colpo solo. Ogni file diventa un payload a sé (nessuna trasformazione
condivisa, nessun vincolo di formato — un batch può mescolare `.svg` +
`.dxf` + immagini raster), scritto come `<nome>.bzp` accanto al sorgente
(o nella directory passata a `-o`). Un file rotto non blocca gli altri:
viene segnalato come voce singola in errore, il resto del batch prosegue.

```bash
python3 -m balzar encode-sequence logo.svg schema.dxf foto.png --mode independent -o out/
# 3 file, formati diversi -> 3 payload separati in out/, ciascuno indipendente
```

**`balzar explode-vector`** — un solo file CAD/SVG con più layer/gruppi
diventa un esploso automatico: ogni layer si allontana radialmente dal
baricentro del disegno, un passo alla volta. A differenza di
`encode-sequence`, qui ogni frame è un repaint completo (`FILL` su tutto
il canvas + ridisegno), non un delta — necessario perché il motore non
cancella mai nulla da solo (`FRAME` fa uno snapshot cumulativo) e un
pezzo che si sposta lascerebbe un "fantasma" nella vecchia posizione se
si riusasse il dedup. Rotazione 2D/3D non è supportata: solo traslazione
radiale in linea retta.

```bash
python3 -m balzar explode-vector examples/flangia_esploso.dxf -o esploso.bzp --steps 6
# 6 layer (carcassa, flangia interna, 4 bulloni) -> 7 frame, 303 byte,
# 44.356x rispetto all'RGB grezzo equivalente, entra in un solo QR
```

Un file con un solo layer (nessun raggruppamento possibile) viene
rifiutato con il motivo esatto, non silenziosamente accettato come "niente
da esplodere".

## Export SVG (vettoriale reale)

`balzar/svg.py` è un secondo target di rendering per lo stesso DSL —
PNG rasterizza sempre, SVG solo per il sottoinsieme di operazioni con un
equivalente vettoriale diretto (`RECT`, `LINE`, `CIRCLE`, `TEXT`, `FILL`,
`COPY`, `TILE`, un solo `FRAME`). Se il programma usa operazioni senza
un significato vettoriale pulito (`SHIFT`, `NOISE`, `FRACTAL`, più di un
`FRAME`, ecc.), l'export fallisce con un errore che nomina l'istruzione
incompatibile — mai un raster silenziosamente incapsulato in un tag SVG.

```bash
python3 -m balzar render examples/etichetta_bom.bzr -o out/ --svg
# -> out/etichetta_bom.svg: vettoriale reale, apribile/modificabile in
#    Illustrator o Inkscape (i cerchi restano cerchi, non pixel)

python3 -m balzar render examples/frattale.bzr -o out/ --svg
# -> svg: non disponibile — 'FRACTAL' non ha un equivalente vettoriale diretto
```

`TILE` diventa un vero `<pattern>` SVG (riempimento scalabile nativo);
`COPY` duplica gli elementi vettoriali già emessi in un `<g
transform="translate(...)">` alla destinazione — un cerchio copiato resta
un cerchio, non una toppa raster. `TEXT` diventa `<text>` reale/editabile
con un font generico monospace, non una riproduzione pixel-perfect del
font bitmap 5×7 di `font5x7.py`: scelta deliberata, testo modificabile
vale più di un match esatto del glifo.

## Demo web (solo vetrina online)

La demo su Vercel serve unicamente a far provare l'encoder dal browser;
il prodotto è l'app desktop qui sopra, che non ha i limiti di upload,
risposta e timeout della piattaforma. Interfaccia statica (`index.html` +
`app.js` + `style.css`) con sei tab, sei funzioni serverless Python:

- **"Comprimi immagine"** (`api/encode.py`): carica una foto, la analizza
  lato server con l'encoder reale (stesso codice della CLI), mostra
  fianco a fianco l'originale e l'immagine **rigenerata dall'interprete**
  a partire dal payload — mai l'upload originale ridisegnato. Scarichi il
  payload binario (`.bzp`) o il programma DSL (`.bzr`). Guarda solo il
  primo frame di un file multi-frame.
- **"Vettoriale (SVG/DXF)"** (`api/encode_vector.py`): ingestione diretta
  via `vectorio.py`, nessuna rasterizzazione. Un SVG caricato viene
  mostrato anche nella sua forma originale (il browser renderizza SVG
  nativamente, nessun round-trip col server necessario per quello);
  offre anche il download di un SVG vettoriale vero, sempre disponibile
  perché `vectorio.py` emette solo il sottoinsieme di op vettoriale-sicure.
- **"Video (GIF animata)"** (`api/encode_video.py`): a differenza di
  "Comprimi immagine", guarda **tutti** i frame e usa il vero delta di
  `video.py` invece di codificare solo il primo.
- **"Sequenza (multi-file)"** (`api/encode_sequence.py`): due modalità con
  un toggle. **"Sequenza navigabile"**: carica 2+ file dello stesso tipo
  (solo `.svg`, solo `.dxf`, oppure immagini raster), mettili nell'ordine
  giusto con le frecce ▲/▼, ottieni un payload multi-frame navigabile
  avanti/indietro — stesso dispatch automatico della CLI
  (`balzar encode-sequence`). **"File indipendenti"**: stessi file, ma
  trattati come un mucchio scorrelato — ogni file diventa una card a sé
  (anteprima, statistiche, download, QR propri), nessun vincolo di
  formato, un file rotto non blocca gli altri.
- **"Assemblee 3D"** (`api/encode_3d.py`): carica un file `.3dxml`
  (`balzar/scene3d.py`, non STEP — vedi CLAUDE.md §9.1 per il perché).
  Niente anteprima raster qui: il "risultato" è un vero file `.glb`
  mostrato dal web component `<model-viewer>` (vendorizzato in
  `model-viewer.min.js`, nessuna dipendenza da CDN), più la distinta
  base estratta dalla struttura dell'assieme. Scarichi il payload
  binario (`.b3d`, formato `BZM1`) o il `.glb` stesso. **Clicca una
  parte per evidenziarla/isolarla** (usa l'API scene-graph pubblica di
  model-viewer, `materialFromPoint` — ogni posizionamento ha un proprio
  materiale, non condiviso per colore come prima, così un click sa
  distinguere due istanze della stessa parte): l'attenuazione delle
  altre parti usa alpha (`alphaMode: BLEND` impostato all'export), non
  un semplice ricolorare. Click su una riga della BOM evidenzia invece
  tutte le istanze di quel tipo di parte. Costo reale misurato: +4,3%
  sul GLB (dettagli in CLAUDE.md §9.11), stessa geometria deduplicata
  di sempre. Con una parte selezionata, **"Esporta scheda ricambio"**
  scarica un PNG con la vista isolata più il nome della parte e la
  quantità nell'assieme — un'immagine e un codice, pensata per un
  tecnico che ha trovato un pezzo difettoso e deve richiedere il
  ricambio (vedi CLAUDE.md §9.14 per la scelta API dietro la cattura).
  Stessa funzione nella GUI desktop (`balzar/viewer3d.py`). Una **barra
  di ricerca** cerca per nome componente o, se carichi una tabella
  allarmi (CSV a due colonne `codice_allarme,nome_componente` — una
  terza colonna, es. un documento di procedura, è accettata e
  ignorata, mai incollata al nome), per codice allarme — un operatore
  che legge un codice sulla macchina lo digita qui e vede subito il
  componente coinvolto, senza conoscerne il nome CAD (un allarme può
  coinvolgere più componenti: tutti si evidenziano insieme, "esporta
  scheda ricambio" resta disabilitato in quel caso — una scheda è la
  foto di una parte sola). Se `nome_componente` è il nome di un intero
  **sotto-assieme** (es. `HEATER1`) invece di una parte singola, la BOM
  lo mostra come **una sola riga collassata** invece di espanderlo in
  ogni parte sottostante, ed evidenziarlo accende esattamente e solo le
  sue parti (mai quelle di un sotto-assieme diverso che condivide per
  caso un nome placeholder generico — verificato su un assieme reale,
  vedi CLAUDE.md §9.21 per il bug di sovrapposizione trovato e
  corretto). La ricerca
  supporta anche `?q=<codice>` nell'URL: sulla GUI desktop, dove la
  tabella allarmi può essere incorporata alla generazione della pagina
  (`open_glb_in_browser(..., alarm_rows=...)`), questo permette di
  aprire il componente giusto con zero digitazione — vedi CLAUDE.md
  §9.15 per i meccanismi di automazione proposti su questa base
  (endpoint locale, integrazione PLC/SCADA, QR fisico sul quadro
  allarmi). Puoi anche **caricare la tabella allarmi insieme al file
  3D** (campo file prima della dropzone, sia qui sia nella GUI
  desktop): i due vengono impacchettati in un bundle `BZX1`
  (`balzar/bundle.py`) che viaggia in un solo QR/file e apre il viewer
  con la ricerca già pronta, zero upload separati — il bottone "genera
  QR" funziona senza modifiche, perché il livello QR tratta qualunque
  payload (bundle incluso) come byte opachi. Vedi CLAUDE.md §9.16 per
  il formato e una scoperta onesta: per un assieme piccolo il bundle
  *pesa di più* della somma delle parti separate (il payload 3D è già
  compresso, comprimerlo di nuovo non aiuta) — il vantaggio è la
  convenienza di un solo scan, non la dimensione. Oltre alla tabella
  allarmi puoi includere **documenti aggiuntivi consultabili** (campo
  a selezione multipla): non collegati al 3D, appaiono in un **indice
  navigabile** e si aprono inline se sono formati semplici
  (testo/CSV/immagini) o si scaricano se strutturati (pdf/html/xml/…).
  Il 3D è opzionale lato **creazione**: costruire un nuovo bundle da
  questo tab richiede sempre un file `.3dxml` (è la scheda "Assemblee
  3D", non un creatore di bundle generico); un bundle di **soli
  documenti** si crea invece dalla CLI/GUI desktop. Vedi CLAUDE.md §9.17.
  Un documento `.bzr`/`.bzp` (un programma/payload balzar 2D — una
  tavola tecnica) è un caso speciale: viene **rigenerato al volo** in
  PNG/SVG (o GIF se multi-frame) al momento dell'apertura, non salvato
  come immagine — stesso principio "descrivi, non memorizzare i pixel"
  di tutto balzar, applicato a un documento dentro il bundle. Vedi
  CLAUDE.md §9.18.
- **"Apri programma"** (`api/render.py`): il lato **Balzar Live** della
  demo — l'unico tab di consumo, non di codifica. Hai già un file
  generato altrove (dalla CLI, dall'app desktop, o scaricato da qui in
  una sessione precedente)? Caricalo, qualunque dei tre formati balzar
  sia: **`.bzr`/`.bzp`** (un programma 2D, magic `BZR1`) viene
  decodificato e rigenerato, scarichi PNG/GIF/SVG o il payload; **`.b3d`**
  (un assieme 3D, magic `BZM1`) apre lo stesso viewer 3D con
  click-to-select/ricerca/BOM della scheda "Assemblee 3D" (stesso
  codice JS riusato, non duplicato); **`.bzx`** (un bundle, magic
  `BZX1`) apre il 3D (se presente) **più** ricerca allarmi **più**
  indice documenti tutto insieme, oppure — se il bundle non contiene
  nessun 3D — solo l'indice documenti. Non serve scegliere il tipo:
  `handle_render` legge i magic byte del file e decide da solo (vedi
  CLAUDE.md §9.20). Le tre viste sono mutuamente esclusive nella
  stessa pagina.

Ogni tab mostra in cima un badge esplicito ("Codifica" o "Consumo") con
lo scopo di quel flusso specifico, e — dove esiste un payload — un
bottone **"genera QR"** (`api/qr.py` + `handle_qr`) con una scelta
esplicita di tre modalità: **"Immagine singola"** (griglia
auto-dimensionata unica, comportamento originale — utile come file, ma
per un payload grande diventa una griglia enorme e illeggibile a
qualunque dimensione fisica, es. 14×14 codici in un colpo solo per un
assieme 3D reale), **"Sequenza QR — GIF animata"** e **"Sequenza QR —
pagine PNG"** (`payload_to_qr_frames`, tetto di 16 codici per
frame/pagina — la stessa dimensione già misurata come affidabile in
CLAUDE.md §2.4b — invece di un'unica griglia senza limite). Genera
un'immagine, non la legge: usa solo `qrcode` (puro Python + Pillow),
**nessuna dipendenza nativa** — a differenza di `pyzbar`/`libzbar0` che
serve solo per leggere un QR da una foto e non è mai stato esposto
sulla demo web (la sequenza generata si rilegge con la classe
`LiveScanner` di `balzar/qr.py`, non ancora un comando CLI dedicato).

```bash
# deploy
vercel deploy   # legge vercel.json + requirements.txt (Pillow, qrcode)
```

`api/encode.py` importa `balzar` direttamente dalla cartella del progetto
(inclusa di default nel bundle Python di Vercel); il payload di richiesta è
JSON con l'immagine in base64, per evitare di dover fare parsing
multipart lato server.

## Architettura

```
balzar/
  grid.py         stato: griglia a indici di palette + regioni
  rng.py          PRNG deterministico (xorshift64*)
  dsl.py          parser + valutatore di espressioni (AST whitelistato)
  ops.py          motore di trasformazioni (registry dichiarativo tipizzato)
  interpreter.py  interprete deterministico: programma -> frame
  payload.py      encoder/decoder payload binario (QR-ready)
  png.py          writer PNG RGB8 in puro Python
  svg.py          export SVG vettoriale reale (sottoinsieme vector-safe)
  vectorio.py     ingestione SVG/DXF -> DSL diretta (no raster, stdlib)
  encoder.py      encoder automatico immagine -> DSL (best-effort)
  video.py        encoder sequenze di frame (delta tra frame, sez. 4.3)
  imageio.py      lettura immagini/GIF animate (unico modulo con Pillow)
  qr.py           payload <-> immagine QR (singola, griglia, o sequenza
                  multi-frame GIF/PNG); lettura ZBar (foto singola o
                  accumulo live su più foto, LiveScanner)
  gui.py          applicazione desktop (Tkinter)
  library.py      libreria locale persistente per Balzar Live (scan/apri -> ~/.balzar/library/)
  sequence.py     sequenze multi-file (vettoriali dedup, raster delta)
  explode.py      esploso automatico per layer/gruppo (CAD/SVG)
  scene3d.py      ingestione 3DXML -> payload binario BZM1 (assiemi CAD, dettagli in CLAUDE.md §9)
  gltf.py         export payload BZM1 -> glTF/GLB (materiali/mesh per-istanza,
                  non deduplicati per colore, per rendere ogni posizionamento
                  cliccabile/distinguibile via materialFromPoint)
  viewer3d.py     GLB + BOM -> pagina locale (model-viewer) aperta nel browser di
                  sistema (solo GUI desktop); clicca una parte per evidenziarla/
                  isolarla, click sulla BOM per selezionare tutte le istanze di un tipo;
                  barra di ricerca per nome/codice allarme, apre anche bundle BZX1
  bundle.py       formato BZX1: più documenti con ruoli (3D / tavole 2D / allarmi /
                  doc generici) in un solo blob con indice navigabile; una tavola 2D
                  (.bzr/.bzp) viene rigenerata al volo in PNG/GIF/SVG, mai salvata
                  come pixel; transita nel livello QR/chunking senza modifiche,
                  3D opzionale (vedi CLAUDE.md §9.16-9.18)
  webapi.py       logica dell'API web con profili di limiti
  cli.py          render / encode / encode-image / encode-vector / encode-3d /
                  encode-video / encode-sequence / explode-vector / render-3d /
                  encode-bundle / decode / info / chunks / scan / assemble / gui
balzar-app.py     entry point per PyInstaller
examples/         programmi dimostrativi (.bzr) + sorgenti vettoriali (.svg/.dxf)
tests/            determinismo, round-trip, op, espansione, encoder, video,
                  qr, svg, vectorio, sequence, explode, webapi, cli, png, scene3d
api/encode.py           funzione serverless Vercel: comprimi immagine -> payload
api/encode_vector.py    funzione serverless Vercel: SVG/DXF -> payload
api/encode_video.py     funzione serverless Vercel: GIF animata -> payload (delta)
api/encode_sequence.py  funzione serverless Vercel: 2+ file -> payload multi-frame
api/encode_3d.py        funzione serverless Vercel: 3DXML -> payload BZM1 + GLB + BOM
api/qr.py               funzione serverless Vercel: payload -> immagine QR (genera, non legge)
api/render.py           funzione serverless Vercel: apri .bzr/.bzp -> PNG/GIF/SVG
index.html, app.js, style.css   frontend statico della demo (6 tab)
model-viewer.min.js     web component vendorizzato (Apache-2.0), nessuna dipendenza da CDN
```

Per aggiungere un'operazione basta registrarla in `ops.py` con la sua firma
tipizzata:

```python
@op("GRADIENT", region="region", c1="int", c2="int")
def op_gradient(state, region, c1, c2): ...
```

L'interprete valuta automaticamente gli argomenti DSL (incluse le espressioni
sulle variabili di loop) contro la firma dichiarata.

## Limite teorico e posizionamento

Il sistema non è compressione tradizionale ma **compressione algoritmica
basata su descrizione** (program-based generation). Il limite è la
complessità di Kolmogorov del contenuto: contenuti strutturati (schemi CAD,
pattern, frattali, UI) si comprimono di ordini di grandezza; contenuti
casuali non danno alcun guadagno. A differenza dei codec (JPEG, H.265) il
contenuto non viene ricostruito da dati, ma **derivato da regole**; a
differenza della compressione neurale la ricostruzione è deterministica pura
e il formato è interpretabile come regole discrete.

## Estensioni previste

- ~~ingestione diretta di formati vettoriali (SVG/DXF)~~ — **fatto**
  (`balzar/vectorio.py`, comando `encode-vector`);
- ~~comando `balzar scan` + generazione QR reale~~ — **fatto**
  (`balzar/qr.py`, comandi `chunks --qr`/`scan`);
- ~~sequenze multi-file ed esploso automatico per layer~~ — **fatto**
  (`balzar/sequence.py`, `balzar/explode.py`, comandi
  `encode-sequence`/`explode-vector`); la **rotazione** (2D o 3D)
  dell'esploso resta rimandata per scelta, non per limite tecnico;
- ~~demo web: tab vettoriale/video/sequenza~~ — **fatto**
  (`api/encode_vector.py`/`api/encode_video.py`/`api/encode_sequence.py`);
  STEP e un encoder per XML/JSON, proposti nella stessa discussione, sono
  stati esplicitamente rimandati a una sessione di scoping separata;
- supporto hardware dedicato (lettore QR + schermo, prototipo su
  smartphone Android riadattato) per adozione in officina/ONG — non
  ancora iniziato, vedi `CLAUDE.md` §5 punto 3;
- round-trip completo verso DXF (oggi la ricostruzione di un DXF ingerito
  produce solo PNG/SVG, mai un `.dxf` rigenerato) — segnalato dall'utente
  come utile ma non prioritario ora;
- rilevamento di linee/cerchi (Hough) sul raster, per contenuto senza
  sorgente vettoriale disponibile (screenshot, scansioni);
- assiemi 3D parametrici (`balzar/scene3d.py`, ingestione 3DXML —
  **prima versione funzionante**, non più solo teoria: dettagli, numeri
  reali e cosa manca ancora in `CLAUDE.md` §9). Diverso dall'idea
  originale "parser STEP + primitive 3D nel DSL" (§7.3, ancora valida
  come analisi di perché STEP specificamente resta fuori scope): qui si
  parte da 3DXML (già tassellato, schema pubblicato) invece che da un
  parser EXPRESS/B-rep da scrivere da zero.

Per un registro di idee esterne valutate (comprese quelle scartate o
ridimensionate, con il perché) vedi `CLAUDE.md` §7.
