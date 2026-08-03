"use strict";
// Trasporto QR di byte arbitrari — nessun motore balzar coinvolto.
// Encoding: /api/qr (server, riusa balzar/qr.py) su byte grezzi invece che
// su un payload balzar (l'endpoint non distingue i due casi, §2.4c).
// Decoding: interamente client-side, via il motore condiviso in
// qr-transport-core.js (CRC32/LiveScanner/tileBoxes/decodeAllInImage,
// estratto da qui senza cambi di comportamento in una sessione
// successiva -- CLAUDE.md §2.4e) e qr-camera-scanner.js (acquisizione
// continua da fotocamera, §2.4g/§2.4h) — nessun file lascia il browser.
//
// Due scelte esplicite, entrambe con pro/con dichiarati nell'interfaccia
// invece che imposti in silenzio: in generazione, pagine da fotografare
// a mano (qualunque griglia) contro una GIF per acquisizione continua
// (sempre griglia 1×1 -- l'unica che una fotocamera live legge in modo
// affidabile, §2.4g); in lettura, foto multiple a comando dell'operatore
// (qualunque griglia) contro acquisizione continua da fotocamera (solo
// griglia 1×1). Le griglie dense restano interamente disponibili in
// entrambe le direzioni, semplicemente non abbinate all'acquisizione
// continua -- coerente con la misura, non con una preferenza.

function downloadBlob(bytes, filename, mime) {
  const blob = new Blob([bytes], { type: mime || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// -------------------------------------------------------------- encode

const encDrop = document.getElementById("enc-drop");
const encFileInput = document.getElementById("enc-file-input");
const encBrowseBtn = document.getElementById("enc-browse-btn");
const encGridDim = document.getElementById("enc-grid-dim");
const encGridDimRow = document.getElementById("enc-grid-dim-row");
const encGridDimFixed = document.getElementById("enc-grid-dim-fixed");
const encStatus = document.getElementById("enc-status");
const encPages = document.getElementById("enc-pages");
const encGifResult = document.getElementById("enc-gif-result");
const encGifImg = document.getElementById("enc-gif-img");
const encGifDownloadBtn = document.getElementById("enc-gif-download-btn");
const encModeRadios = document.querySelectorAll('input[name="enc-mode"]');

function encMode() {
  return document.querySelector('input[name="enc-mode"]:checked').value;
}

function updateEncModeUI() {
  const gif = encMode() === "gif";
  encGridDimRow.hidden = gif;
  encGridDimFixed.hidden = !gif;
}
encModeRadios.forEach(r => r.addEventListener("change", updateEncModeUI));
updateEncModeUI();

encBrowseBtn.addEventListener("click", () => encFileInput.click());
encFileInput.addEventListener("change", () => {
  if (encFileInput.files[0]) encodeFile(encFileInput.files[0]);
});
["dragover", "dragleave", "drop"].forEach(evt => {
  encDrop.addEventListener(evt, e => {
    e.preventDefault();
    encDrop.classList.toggle("dragover", evt === "dragover");
  });
});
encDrop.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  if (file) encodeFile(file);
});

async function encodeFile(file) {
  encStatus.classList.remove("error");
  encStatus.textContent = `Lettura di ${file.name} (${file.size.toLocaleString("it-IT")} byte)...`;
  encPages.innerHTML = "";
  encGifResult.hidden = true;
  const mode = encMode();
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    const payloadB64 = bytesToB64(buf);
    // GIF per acquisizione continua: SEMPRE griglia 1x1, non quella
    // scelta nel picker (nascosto in questa modalità) -- è l'unica che
    // una fotocamera live legge in modo affidabile, §2.4g/§2.4h.
    const gridDim = mode === "gif" ? 1 : parseInt(encGridDim.value, 10);
    encStatus.textContent = "Generazione QR in corso...";
    const res = await fetch("/api/qr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload_base64: payloadB64, mode, grid_dim: gridDim }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");

    if (mode === "gif") {
      if (json.gif_omitted) {
        encStatus.classList.add("error");
        encStatus.textContent = `Sequenza generata (${json.n_frames} fotogrammi) ma la GIF risultante è troppo grande per essere restituita da questo deployment.`;
        return;
      }
      const scanRange = `${json.estimated_scan_seconds_low}-${json.estimated_scan_seconds_high}s`;
      encStatus.textContent = `${file.name}: ${buf.length.toLocaleString("it-IT")} byte -> ` +
        `GIF di ${json.n_frames} fotogrammi (un QR ciascuno). Nessuna compressione: sono gli ` +
        `stessi byte del file originale, solo spezzettati per il trasporto fisico. Riproducila a ` +
        `schermo intero e leggila con "Acquisizione continua" nella sezione 2 (stima di lettura: ` +
        `~${scanRange}, dipende da fotocamera/luce/mano).`;
      encGifImg.src = "data:image/gif;base64," + json.qr_gif_base64;
      encGifDownloadBtn.onclick = () =>
        downloadBlob(b64ToBytes(json.qr_gif_base64), `${file.name}_acquisizione_continua.gif`, "image/gif");
      encGifResult.hidden = false;
      return;
    }

    if (json.pages_omitted) {
      encStatus.classList.add("error");
      encStatus.textContent = `Sequenza generata (${json.n_frames} pagine) ma troppo grande per essere restituita da questo deployment.`;
      return;
    }
    encStatus.textContent = `${file.name}: ${buf.length.toLocaleString("it-IT")} byte -> ` +
      `${json.n_frames} pagina/e da ${json.grid_dim}×${json.grid_dim} QR l'una. Nessuna compressione: ` +
      `sono gli stessi byte del file originale, solo spezzettati per il trasporto fisico. ` +
      `Leggile qui sotto nella sezione 2, o stampale/salvale e leggile in un secondo momento.`;
    json.pages.forEach((page, i) => {
      const item = document.createElement("div");
      item.className = "qr-page-item";
      const img = document.createElement("img");
      img.src = "data:image/png;base64," + page.png_base64;
      img.alt = `Pagina ${i + 1} di ${json.n_frames}`;
      const dlBtn = document.createElement("button");
      dlBtn.type = "button";
      dlBtn.textContent = `Scarica pagina ${i + 1}/${json.n_frames}`;
      dlBtn.addEventListener("click", () =>
        downloadBlob(b64ToBytes(page.png_base64), `${file.name}_qr_${i + 1}_di_${json.n_frames}.png`, "image/png"));
      item.appendChild(img);
      item.appendChild(dlBtn);
      encPages.appendChild(item);
    });
  } catch (e) {
    encStatus.classList.add("error");
    encStatus.textContent = "Errore: " + e.message;
  }
}

// -------------------------------------------------------------- decode

const decModeRadios = document.querySelectorAll('input[name="dec-mode"]');
const decManualSection = document.getElementById("dec-manual-section");
const decContinuousSection = document.getElementById("dec-continuous-section");
const decDrop = document.getElementById("dec-drop");
const decFileInput = document.getElementById("dec-file-input");
const decBrowseBtn = document.getElementById("dec-browse-btn");
const decFileList = document.getElementById("dec-file-list");
const decStatus = document.getElementById("dec-status");
const decDownloadBtn = document.getElementById("dec-download-btn");
const decResetBtn = document.getElementById("dec-reset-btn");
const decFilenameRow = document.getElementById("dec-filename-row");
const decFilename = document.getElementById("dec-filename");
const decCameraVideo = document.getElementById("dec-camera-video");
const decCameraProgress = document.getElementById("dec-camera-progress");
const decCameraStartBtn = document.getElementById("dec-camera-start-btn");
const decCameraStopBtn = document.getElementById("dec-camera-stop-btn");

// Una sola LiveScanner condivisa tra foto manuali e acquisizione
// continua: un capitolo che la fotocamera non legge si può coprire con
// una foto manuale, e viceversa, senza perdere ciò che l'altra via ha
// già trovato -- stesso principio di accumulo già alla base del formato.
let scanner = new LiveScanner();
let decodedImageNames = [];
let camScanner = null;

function decMode() {
  return document.querySelector('input[name="dec-mode"]:checked').value;
}

function stopCamera() {
  if (camScanner) {
    camScanner.stop();
    camScanner = null;
  }
  decCameraStartBtn.hidden = false;
  decCameraStopBtn.hidden = true;
}

function updateDecModeUI() {
  const continuous = decMode() === "continuous";
  decManualSection.hidden = continuous;
  decContinuousSection.hidden = !continuous;
  if (!continuous) stopCamera();
}
decModeRadios.forEach(r => r.addEventListener("change", updateDecModeUI));
updateDecModeUI();

decBrowseBtn.addEventListener("click", () => decFileInput.click());
decFileInput.addEventListener("change", () => {
  addDecodeImages(Array.from(decFileInput.files));
  decFileInput.value = "";
});
["dragover", "dragleave", "drop"].forEach(evt => {
  decDrop.addEventListener(evt, e => {
    e.preventDefault();
    decDrop.classList.toggle("dragover", evt === "dragover");
  });
});
decDrop.addEventListener("drop", e => addDecodeImages(Array.from(e.dataTransfer.files)));

decResetBtn.addEventListener("click", () => {
  stopCamera();
  scanner = new LiveScanner();
  decodedImageNames = [];
  decFileList.innerHTML = "";
  decStatus.classList.remove("error");
  decStatus.textContent = "";
  decDownloadBtn.hidden = true;
  decResetBtn.hidden = true;
  decFilenameRow.hidden = true;
  decCameraProgress.classList.remove("active");
  decCameraProgress.textContent = "Fotocamera non avviata.";
});

decDownloadBtn.addEventListener("click", () => {
  try {
    const bytes = scanner.result();
    downloadBlob(bytes, decFilename.value || "file_ricostruito.bin");
  } catch (e) {
    decStatus.classList.add("error");
    decStatus.textContent = "Errore: " + e.message;
  }
});

// Aggiorna lo stato/i pulsanti condivisi (download/reset/nome file) in
// base alla LiveScanner condivisa -- usato sia dal percorso manuale sia
// da quello a fotocamera continua, cosicché l'esperienza di completamento
// sia identica indipendentemente da come sono arrivati i capitoli.
function renderSharedStatus() {
  const st = scanner.status();
  if (st.total === null) {
    decStatus.textContent = "Nessun capitolo BZC1 riconosciuto finora.";
  } else if (st.complete) {
    decStatus.classList.remove("error");
    decStatus.textContent = `Completo: ${st.total}/${st.total} capitoli. Pronto per il download ` +
      `(integrità verificata via CRC32 al momento del download).`;
    decDownloadBtn.hidden = false;
    decResetBtn.hidden = false;
    decFilenameRow.hidden = false;
  } else {
    decStatus.classList.remove("error");
    decStatus.textContent = `${st.have}/${st.total} capitoli letti — mancano ${st.missing.length} ` +
      `(indici: ${st.missing.slice(0, 12).join(", ")}${st.missing.length > 12 ? "..." : ""}). ` +
      `Aggiungi altre foto/pagine.`;
  }
  return st;
}

async function addDecodeImages(files) {
  for (const file of files) {
    const li = document.createElement("li");
    const nameSpan = document.createElement("span");
    nameSpan.className = "file-name";
    nameSpan.textContent = file.name;
    li.appendChild(nameSpan);
    decFileList.appendChild(li);
    decodedImageNames.push(file.name);

    try {
      const imgData = await loadImageData(file);
      const texts = decodeAllInImage(imgData);  // grid_dim auto-rilevato
      let addedHere = 0;
      for (const text of texts) {
        const r = scanner.addDecodedText(text);
        if (r.added) addedHere++;
      }
      nameSpan.textContent = `${file.name} — ${texts.length} QR trovati, ${addedHere} nuovi capitoli`;
      if (texts.length === 0) {
        li.classList.add("status");
        nameSpan.textContent += " (nessun QR riconosciuto in questa immagine)";
      }
    } catch (e) {
      nameSpan.textContent = `${file.name} — errore: ${e.message}`;
      decStatus.classList.add("error");
      decStatus.textContent = "Errore leggendo " + file.name + ": " + e.message;
      continue;
    }

    renderSharedStatus();
  }
}

// -------------------------------------------------------- acquisizione continua

decCameraStartBtn.addEventListener("click", async () => {
  decCameraStartBtn.hidden = true;
  decCameraProgress.classList.add("active");
  decCameraProgress.textContent = "Richiesta permesso fotocamera...";
  // onFrameSample fires once per decode attempt, BEFORE onProgress in
  // the same tick (qr-camera-scanner.js) -- captured here so onProgress
  // can fold it into the same message instead of appending to
  // already-stale text that onProgress is about to overwrite anyway.
  let lastFrameSampleCount = null;
  camScanner = new ContinuousQrScanner({
    video: decCameraVideo,
    gridDim: 1,
    scanner, // condivisa col percorso manuale, vedi sopra
    onProgress: (st) => {
      renderSharedStatus();
      if (st.complete) {
        decCameraProgress.textContent = `Completo: ${st.total}/${st.total} capitoli letti.`;
        return;
      }
      const missingTxt = st.missing ? `mancano ${st.missing.length}` : "in attesa del primo QR";
      const hint = lastFrameSampleCount === 0
        ? " (nessun QR in questa inquadratura, avvicina/allontana la fotocamera)" : "";
      decCameraProgress.textContent =
        `${st.have}/${st.total || "?"} capitoli letti — ${missingTxt}.${hint}`;
    },
    onFrameSample: (n) => { lastFrameSampleCount = n; },
    onError: (e) => {
      decStatus.classList.add("error");
      decStatus.textContent = "Errore fotocamera: " + (e && e.message ? e.message : String(e));
    },
    onComplete: () => {
      renderSharedStatus();
      decCameraStopBtn.hidden = true;
      decCameraStartBtn.hidden = false;
    },
  });
  try {
    await camScanner.start();
    decCameraStopBtn.hidden = false;
  } catch (e) {
    decCameraStartBtn.hidden = false;
    decCameraProgress.classList.remove("active");
    decCameraProgress.textContent = "Fotocamera non avviata: " + (e && e.message ? e.message : String(e));
  }
});

decCameraStopBtn.addEventListener("click", () => {
  stopCamera();
  decCameraProgress.textContent = "Fotocamera fermata manualmente.";
});
