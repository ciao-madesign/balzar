# balzar — visione, scopo, idee

Questo documento raccoglie in forma leggibile la visione del progetto, le
applicazioni a cui punta, il posizionamento dei suoi prodotti e le idee
esterne valutate nel tempo. È un documento di **sintesi**, pensato per chi
vuole capire cosa è balzar e dove può andare senza leggere il log tecnico
completo di sessione. Le stesse informazioni, con più dettaglio tecnico e
i numeri misurati che le supportano, vivono in `CLAUDE.md` — i due file
sono tenuti allineati a mano, `CLAUDE.md` resta la fonte di verità tecnica.

---

## 1. Visione

balzar non comprime dati: **genera** contenuto (immagini, sequenze di
frame, assiemi 3D) a partire da una descrizione minima (seed + programma
di regole). Non è un codec — è "compressione algoritmica basata su
descrizione" (program-based generation). Il dato diventa minimo, la
descrizione diventa il contenuto, la complessità si sposta dal file al
processo generativo.

Il limite teorico è sempre presente in ogni decisione di design: la
**complessità di Kolmogorov**. Contenuto strutturato (CAD, pattern, icone,
UI, frattali) si comprime di ordini di grandezza — nei casi misurati, da
qualche centinaio a molte migliaia di volte più piccolo dell'equivalente
raster. Contenuto casuale (foto, rumore, video da fotocamera, audio
campionato) non dà guadagno, e il sistema **lo deve dichiarare
onestamente** invece di fingere una compressione che non c'è. Questa
onestà è un requisito di prodotto, non un dettaglio tecnico: è quello che
distingue balzar da un tool di compressione bugiardo.

L'analogia più diretta: un file audio compresso contiene il suono; uno
spartito no, contiene le istruzioni per produrlo. Un payload balzar è uno
spartito — i pixel di un cerchio o le lettere di un'etichetta non sono
mai salvati da nessuna parte, vengono ricalcolati ogni volta che il
payload viene aperto.

---

## 2. Ecosistema di prodotto

La piattaforma balzar si pensa oggi come due prodotti, con un terzo
valutato ma non costruito:

```
BALZAR STUDIO                          BALZAR LIVE
creazione dei contenuti                utilizzo/consultazione

CLI + GUI desktop + demo web           viewer 2D/3D (desktop + web)
  |                                      |
motore core (encoder/decoder)          ricostruzione da QR/file/bundle
  |                                      |
payload compatto (QR o file)  ────────▶  click-to-select, ricerca,
                                          BOM, documenti, allarmi
```

**Balzar Studio** è tutto ciò che oggi produce un payload: il motore
deterministico stdlib-pura (griglia+trasformazioni, `balzar/grid.py`/
`ops.py`/`dsl.py`/`interpreter.py`), gli encoder (raster, vettoriale
SVG/DXF, video, sequenze CAD, esploso automatico, scene 3D da 3DXML), la
CLI, la GUI desktop e la demo web. Usato da chi crea contenuto tecnico:
uffici tecnici, disegnatori CAD, chi prepara manuali/etichette.

**Balzar Live** è il lato di consultazione: apri un payload/QR/bundle e
lo vedi — rotazione, zoom, esploso, click-to-select con isolamento
(§9.11 di CLAUDE.md), ricerca per nome componente o codice allarme
(§9.15), distinta base, indice di documenti collegati (§9.17), tavole 2D
rigenerate al volo (§9.18). **Questo esiste già**, funziona
completamente offline, non richiede licenza CAD né rete. È il prodotto
che un tecnico/operatore usa davvero in officina o sul campo.

**Un'estensione valutata ma non costruita**: collegare Balzar Live allo
stato reale di una macchina (allarme attivo, letto via OPC UA/Modbus/
MQTT/REST) per far scattare automaticamente l'evidenziazione del
componente e l'apertura della procedura collegata, invece che l'operatore
la cerchi a mano. Il dettaglio tecnico completo — cosa esiste già, cosa
servirebbe scrivere, perché non tocca il motore deterministico di
balzar — è in `CLAUDE.md` §9.19. In sintesi: **il viewer resta quello di
oggi, statico e senza animazioni**; l'unica estensione concessa è una
colonna in più nella tabella allarmi con un riferimento a un documento di
procedura, e un piccolo orchestratore esterno (mai il motore balzar) che
traduce un evento macchina in una chiamata alla ricerca già esistente.
Non è un impegno di roadmap, è una direzione plausibile.

**Forma del prodotto finale**: un'app **installabile e usabile come qualsiasi
programma** (tipo Microsoft Word) — installazione banale, doppio clic, lavora
come un normale programma locale, offline. Tecnicamente, Studio e Live vivono
in **un'unica interfaccia** (quella della demo web) dentro un **guscio nativo**
(finestra pywebview su desktop, WebView su mobile; nessun browser visibile,
nessuna rete) — lo stesso schema di app come VS Code/Slack, non "un sito". La
CLI e la GUI Tkinter restano come strumenti di sviluppo/fallback. Il dettaglio
tecnico della decisione (perché guscio WebView invece di Tkinter, cosa richiede
l'esperienza "come Word": installer + firma) è in `CLAUDE.md` §12.5 e nella
`ROADMAP.md`.

---

## 3. Applicazioni target

Sei direzioni d'uso concrete, dalla più B2B/tecnica alla più consumer.

1. **Manuali tecnici, ricambi ed esplosi/BOM per officina e manutenzione
   sul campo** — il caso guida del progetto. Reparti produttivi spesso
   non hanno viewer 3D/licenze CAD accanto alla macchina, e la
   manutenzione sul campo spesso non ha rete. Un'etichetta/QR rigenera
   schema esploso e distinta base senza viewer 3D, senza licenza CAD,
   senza connessione — sostituisce la pila di PDF disordinati.
   Precondizione: il disegno va esportato pulito da CAD, non fotografato.
2. **Asset per firmware/embedded**: icone, boot animation, sprite UI come
   programma invece di bitmap in flash — il decoder è stdlib pura apposta
   per questo.
3. **Distribuzione offline di contenuti tecnici/didattici** in zone a
   bassa connettività: una pagina di QR fotografata in un colpo solo
   consegna diagrammi/animazioni senza rete dati.
4. **Asset procedurali per videogiochi/app**: tileset, pattern UI, sprite
   animati generati a runtime da un seed invece che scaricati come bitmap.
5. **Marketing generativo/branding fisico**: QR su packaging che
   rigenerano un pattern di brand animato — il valore è il gesto ("appare
   dal nulla"), non la percentuale di compressione.
6. **Musica: notazione/MIDI strutturato, non audio campionato** — idea
   valida solo su rappresentazione simbolica (spartito, MIDI, pattern
   ritmici/melodici), mai su audio campionato, dove balzar non ha nulla
   da offrire contro decenni di compressione percettiva già ottimizzata.

Il filo comune: balzar vince dove il contenuto è **strutturato e
ripetitivo** (CAD, UI, pattern, procedure), non dove è percettivo o
già ottimizzato da un codec maturo (foto, audio, video reale, JPEG/MP3).

---

## 4. Idee esterne valutate (per non ridiscuterle da zero)

Registro delle proposte ricevute in sessione, con verdetto esplicito:
cosa è balzar-oggi, cosa è "stessa filosofia ma prodotto diverso", cosa
non è fattibile con l'architettura attuale.

- **Formati vettoriali/CAD (SVG, DXF, STEP, G-code, GLTF, STL, OBJ)** —
  SVG/DXF sono **fatti** (`balzar/vectorio.py`): parser semplici, testo
  strutturato, primitive di destinazione (`LINE`/`CIRCLE`/`TEXT`) già
  esistenti nel DSL. STEP resta il candidato più lontano: servirebbe un
  parser EXPRESS reale (progetto a sé, ordini di grandezza più grande di
  qualunque altro parser nel progetto) *e* primitive 3D nel DSL, nessuna
  delle due esiste. Un ponte più realistico (non ancora costruito):
  delegare la lettura STEP a FreeCAD/`pythonocc` (OpenCASCADE) e
  costruire uno `Scene3D` direttamente dal loro albero documento, senza
  scrivere un parser B-rep proprio.
- **"Gemello digitale" di una UI industriale runtime** — non è
  un'estensione di balzar: richiederebbe condizionali, lettura di stato
  esterno a runtime, un modello a componenti/oggetti, tutte cose che il
  DSL non ha per scelta di design (determinismo totale, seed cotto nel
  payload). Sarebbe un prodotto fratello con la stessa filosofia ma
  un'architettura diversa. Versione ridimensionata e realmente
  costruibile con l'architettura attuale: pre-rendering di un numero
  finito di stati UI noti via il motore video esistente, con la scelta
  del frame delegata a un wrapper esterno.
- **Balzar Live con integrazione macchina in tempo reale** — vedi §2
  sopra e `CLAUDE.md` §9.19: a differenza del "gemello digitale UI",
  questa proposta **non** chiede al motore di leggere stato live, lo
  stato resta fuori da balzar in un orchestratore esterno che richiama
  un'API di visualizzazione già esistente. Più vicino al fattibile, ma
  il connettore ai protocolli industriali (OPC UA/Modbus/MQTT) è lavoro
  nuovo vero, non ancora iniziato.
- **Musica: dove potrebbe avere senso, dove no** — audio campionato
  (MP3/WAV/FLAC di una registrazione reale): zero guadagno per
  definizione, stessa categoria di JPEG/H.265, un secondo passaggio di
  compressione peggiora soltanto. Notazione simbolica (spartito, MIDI,
  pattern ritmici/melodici generativi): territorio potenzialmente
  valido, perché già discreto e strutturato — ma è un dominio nuovo,
  servirebbe uno stato e operazioni proprie (griglia note/tempo invece
  di griglia pixel), zero lavoro iniziato, nessuna garanzia di guadagno
  comparabile a quanto visto su immagini/video.
- **HTML/XML come sorgente** — oggi nessun modulo ingerisce markup
  generico. Il modello sarebbe "template + diff dei parametri", non
  "copertura a rettangoli di pixel": un encoder nuovo da zero, non
  un'estensione di uno esistente. Misurato su due casi sintetici: markup
  fortemente templato batte già gzip di poco margine sopra i 25× gratis
  che gzip offre da solo (un encoder dedicato dovrebbe superare quel
  numero, non solo eguagliarlo); prosa che varia genuinamente non ha
  scorciatoia generativa possibile, stesso limite di Kolmogorov già
  visto su rumore/foto/audio.
- **Convertitore STEP → 3DXML (o diretto a `Scene3D`) per allargare
  l'input 3D** — idea coerente con la filosofia del progetto (delegare
  un problema difficile a uno strumento maturo, come già fa Pillow per
  JPEG/PNG). Scartato l'uso di un servizio di conversione web di terzi
  (caricare un assieme CAD proprietario su un server esterno viola sia
  il caso d'uso "manutenzione senza rete" sia la riservatezza del
  disegno). Alternativa realistica identificata ma non implementata:
  FreeCAD/`pythonocc`, entrambi offline e scriptabili.
- **"3D filtered mode"** (nascondere sotto-assiemi riservati) — proposta
  e discussa, non iniziata. Punto tecnico da tenere presente quando si
  riprende: nascondere solo nella UI del viewer non basta, il `.glb`
  scaricabile contiene comunque nomi e gerarchia completi — una vera
  riservatezza richiederebbe unire la geometria sotto il livello scelto
  già in fase di export, col costo esplicito di perdere il
  click-to-select per quelle sotto-parti.

---

## 5. Perché questo documento esiste separato da CLAUDE.md

`CLAUDE.md` è un log tecnico di sessione: cosa è stato costruito, come è
stato verificato, quali numeri sono stati misurati, quali bug sono stati
trovati e corretti. Utilissimo per riprendere il lavoro, denso per chi
vuole solo capire "cosa fa balzar e dove potrebbe andare". Questo file è
quella seconda lettura — aggiornarlo quando cambia la sostanza della
visione, delle applicazioni target o del posizionamento di prodotto, non
per ogni dettaglio implementativo (quello resta in `CLAUDE.md`).
