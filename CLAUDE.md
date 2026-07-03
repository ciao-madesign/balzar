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
| `balzar/ops.py` | Motore di trasformazioni: registry dichiarativo tipizzato (`@op(...)`). Geometriche (SHIFT/ROTATE/MIRROR/SCALE), strutturali (COPY/SWAP/TILE), differenziali (SETPIX/FILL/MAP/INVERT/FRAME), generative (RECT/LINE/CIRCLE/NOISE/SCATTER/FRACTAL) |
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
   (icone, screenshot, export CAD, pixel art); altrimenti quantizzazione
   fissa 3-3-2 bit (256 colori), dichiarata come lossy;
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

### 2.4 Supporto fisico (serie di QR)

`chunk_payload` / `assemble_chunks` in `balzar/payload.py`: un payload più
grande di un QR si spezza in capitoli autodescrittivi —

```
"BZC1" | u16 indice | u16 totale | u32 CRC-32 del payload intero | dati
```

Ogni capitolo sta in un QR v40 (~2953 byte), porta con sé posizione e
checksum dell'insieme. I capitoli si riassemblano **in qualsiasi ordine**
(testato con shuffle) e la corruzione/mancanza viene rilevata.

**Provato end-to-end in questa sessione** (non solo in teoria): generati 15
QR reali (libreria `qrcode`) da un payload video da 8.144 byte, disposti in
una griglia fisica 4×4 (immagine PNG), **fotografati/letti in un solo
scatto** con ZBar (`pyzbar`), riassemblati in ordine casuale → payload
bit-identico all'originale → video di 30 frame rigenerato correttamente.
Questo dimostra il concetto "supporto fisico con moltitudine di QR letti in
un solo gesto" come reale, non ipotetico. **Non ancora integrato nel
codice**: l'esperimento è stato fatto ad-hoc, `qrcode`/`pyzbar` non sono
dipendenze del progetto e non c'è ancora un comando `balzar scan` — vedi §5.

Nota tecnica emersa dall'esperimento: `cv2.QRCodeDetector().detectAndDecodeMulti`
(OpenCV nativo, senza dipendenze extra) ha letto solo 5 QR su 15 nello
stesso scatto — la sua multi-decodifica è inaffidabile oltre pochi codici.
**ZBar (`pyzbar`) li ha letti tutti e 15**. Se si implementa lo scan reale,
usare ZBar, non il detector nativo di OpenCV.

### 2.5 App desktop (il prodotto)

`balzar/gui.py` + `balzar-app.py` — Tkinter (stdlib) + Pillow. Apri
immagine/GIF/payload → encoding in thread separato (la finestra non si
blocca) → anteprima animata fianco a fianco originale/rigenerato →
statistiche oneste → salva `.bzp`/`.bzr`, esporta PNG/GIF, esporta capitoli
QR (come file di testo base64, **non ancora come immagini QR reali** — vedi
criticità). Impacchettabile in un eseguibile singolo con PyInstaller
(`pyinstaller --onefile --windowed --name balzar balzar-app.py`) —
**il packaging PyInstaller non è stato ancora eseguito/testato in questa
sessione**, solo documentato.

Verificato con screenshot reale sotto Xvfb: apertura GIF, encoding video
delta, anteprima animata, pannello statistiche, bottoni attivi.

### 2.6 Demo web (solo vetrina, non il prodotto)

`index.html` + `app.js` + `style.css` + `api/encode.py` (funzione serverless
Vercel) + `balzar/webapi.py` (logica condivisa con profili di limiti
espliciti: `VERCEL_LIMITS` vs `LOCAL_LIMITS`, quest'ultimo per uso futuro non
vincolato da piattaforma). Vercel impone limiti reali (~3,3MB upload utile,
~4,5MB risposta, timeout) gestiti esplicitamente con messaggi chiari invece
di errori criptici — vedi `MAX_PREVIEW_DIM`, `MAX_PROGRAM_CHARS`,
`MAX_PAYLOAD_B64_BYTES` in `api/encode.py`. **Questi limiti non esistono
nell'app desktop**, che è il prodotto vero.

### 2.7 CLI

`balzar render|encode|encode-image|encode-video|decode|info|chunks|assemble|gui`
— vedi `balzar/cli.py` per l'elenco completo con esempi in `README.md`.

### 2.8 Test

48 test, tutti verdi (`python3 -m unittest discover -s tests`):
`test_determinism.py`, `test_ops.py`, `test_expansion.py`, `test_encoder.py`,
`test_video.py`. Copertura: round-trip bit-identico, corruzione rilevata,
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

## 4. Criticità note (non nascoste, da affrontare quando serve)

1. **Niente rilevamento linee/cerchi/curve nell'encoder**. La copertura a
   rettangoli va in crisi su contenuto vettoriale con bordi non assiali
   (diagonali, cerchi): un'icona con una linea diagonale e un'ellisse è
   risultata **peggiore del PNG** (4.216 B vs 1.900 B) perché ogni pixel di
   bordo diventa la propria istruzione. Servirebbe un fitting tipo Hough
   transform per linee/cerchi. Non implementato: è la lacuna più seria
   dell'encoder v1.
2. **Quantizzazione lossy 3-3-2 grezza** oltre 256 colori. Funziona ma è
   rozza (banding visibile su gradienti). Un quantizzatore migliore
   (median-cut vero, o merge percettivo) darebbe risultati più puliti sulle
   foto, senza però cambiare l'esito di fondo (le foto restano il caso a
   basso/nessun guadagno).
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

1. **Comando `balzar scan`**: fotografa/carica un'immagine di una griglia di
   QR, li decodifica con ZBar in un colpo solo, riassembla, renderizza.
   Chiude il cerchio "supporto fisico → contenuto rigenerato" che oggi è
   solo per metà nel codice (si genera testo capitoli, non si legge indietro
   da foto). Aggiungere anche generazione QR reale (`qrcode`) al posto del
   solo testo base64 in `export_chunks`/`cmd_chunks`.
2. **Rilevamento linee/cerchi (Hough)** nell'encoder immagine: risolverebbe
   la criticità #1, la più seria. Estende il guadagno dal "flat + tiling" al
   vero contenuto vettoriale/tecnico (icone, schemi con curve).
3. **Packaging e distribuzione reale**: build PyInstaller testate su
   Windows/macOS/Linux, eventualmente firma del codice, installer.
4. **Filtri PNG adattivi** in `png.py` per output competitivo con encoder
   PNG di libreria (criticità #3) — minore, ma facile.
5. **Generazione diretta del QR dal payload** (già in parte coperta dal
   punto 1).
6. **Scene 3D** con lo stesso modello stato+trasformazioni (estensione
   dichiarata fin dalla visione originale, non ancora iniziata).
7. **Quantizzatore percettivo migliore** per il fallback lossy (criticità #2).

## 6. Comandi utili per riprendere il lavoro

```bash
python3 -m unittest discover -s tests        # 48 test, deve restare verde
python3 -m balzar gui                        # app desktop
python3 -m balzar encode-image foto.png -o f.bzp
python3 -m balzar encode-video anim.gif -o v.bzp
python3 -m balzar chunks v.bzp -o capitoli/
python3 -m balzar assemble capitoli/ -o ricostruito.bzp
```

Ambiente di sviluppo: Python 3.11 di sistema **non ha Tk** (pacchetto
`python3.11-tk` non installabile qui per un blocco del proxy apt); la GUI è
stata sviluppata e testata con **python3.12**, che ha Tk 8.6 disponibile.
Pillow va installato su entrambe le versioni se si passa dall'una all'altra
(`pip install pillow` / `python3.12 -m pip install --break-system-packages pillow`).
