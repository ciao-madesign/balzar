# Prototipo: trasferimento "a fontana" (schermo → fotocamera)

**Non fa parte del prodotto balzar.** Nessun file esistente del progetto è
stato toccato per costruire questo prototipo — vive isolato in questa
cartella, per verificare con hardware reale se la codifica "a fontana"
(fountain coding, presa da
[decimen-optical-transfer](https://github.com/bashalarmistalt/decimen-optical-transfer),
licenza MIT) è davvero più affidabile del metodo a QR sequenziali che
balzar usa oggi, prima di decidere se integrarla.

## Cosa fa

- `sender.html` — apri su uno schermo (PC, tablet, o il monitor di una
  macchina): mostra un flusso continuo di QR che cambiano rapidamente.
  Puoi caricare un file qualunque, oppure parte da un payload di prova
  (50 KB) se non ne scegli uno.
- `receiver.html` — apri sul telefono: punta la fotocamera verso lo
  schermo del mittente, e ricostruisce il file mano a mano che legge i
  fotogrammi — non importa quali riesce a leggere né in che ordine, gli
  basta raccoglierne "abbastanza".

## Già verificato (senza hardware reale, solo in automatico)

- Il mittente genera QR validi e correttamente formati (controllato
  catturando un fotogramma vero e decodificandolo).
- Il ricevitore ricostruisce il file **byte per byte identico**
  all'originale, partendo da 60 fotogrammi veri catturati dal mittente
  in esecuzione in un browser reale (non una simulazione teorica) — hash
  verificato.

**Non ancora verificato**: una vera fotocamera di telefono, un vero
schermo, condizioni di luce/messa a fuoco reali. È il passo successivo,
e serve hardware vero — da qui in sessione non è possibile.

## Come provarlo con hardware vero

Serve un computer (farà da mittente) e un telefono (farà da ricevitore),
sulla stessa rete Wi-Fi.

1. Sul computer, dentro questa cartella:
   ```bash
   python3 serve_https.py
   ```
   (genera al volo un certificato locale autofirmato — la fotocamera
   funziona solo su una pagina "sicura", https o `localhost`; un
   telefono che apre l'IP del computer in http semplice non avrà
   accesso alla fotocamera, è una regola dei browser).
2. Sul computer, apri `https://localhost:8899/sender.html`.
3. Trova l'indirizzo IP del computer sulla rete locale (es. `192.168.1.x`).
4. Sul telefono, apri `https://192.168.1.x:8899/receiver.html` — il
   browser avviserà che il certificato non è riconosciuto (è
   autofirmato, non emesso da un'autorità pubblica): accetta comunque,
   la connessione resta cifrata, è solo un avviso di fiducia.

## Esito che conta

Alla fine del trasferimento, il ricevitore mostra: tempo impiegato,
KB/s, quanti fotogrammi ha letto su quanti catturati, e se l'hash
combacia. Quel numero (KB/s reale, non teorico) è quello da confrontare
con i tempi già misurati per il metodo QR attuale di balzar sugli
stessi assiemi 3D (CLAUDE.md §9.10/§9.24/§9.25).
