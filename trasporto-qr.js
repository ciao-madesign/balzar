"use strict";
// Trasporto QR di byte arbitrari — nessun motore balzar coinvolto.
// Encoding: /api/qr (server, riusa balzar/qr.py) su byte grezzi invece che
// su un payload balzar (l'endpoint non distingue i due casi, §2.4c).
// Decoding: interamente client-side, via il motore condiviso in
// qr-transport-core.js (CRC32/LiveScanner/tileBoxes/decodeAllInImage,
// estratto da qui senza cambi di comportamento in una sessione
// successiva -- CLAUDE.md §2.4e -- così un futuro terzo consumatore
// dello stesso formato BZC1 lato browser non ne scrive una terza copia)
// — nessun file lascia il browser.

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
const encStatus = document.getElementById("enc-status");
const encPages = document.getElementById("enc-pages");

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
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    const payloadB64 = bytesToB64(buf);
    const gridDim = parseInt(encGridDim.value, 10);
    encStatus.textContent = "Generazione QR in corso...";
    const res = await fetch("/api/qr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload_base64: payloadB64, mode: "pages", grid_dim: gridDim }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");
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

const decDrop = document.getElementById("dec-drop");
const decFileInput = document.getElementById("dec-file-input");
const decBrowseBtn = document.getElementById("dec-browse-btn");
const decGridDim = document.getElementById("dec-grid-dim");
const decFileList = document.getElementById("dec-file-list");
const decStatus = document.getElementById("dec-status");
const decDownloadBtn = document.getElementById("dec-download-btn");
const decResetBtn = document.getElementById("dec-reset-btn");
const decFilenameRow = document.getElementById("dec-filename-row");
const decFilename = document.getElementById("dec-filename");

let scanner = new LiveScanner();
let decodedImageNames = [];

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
  scanner = new LiveScanner();
  decodedImageNames = [];
  decFileList.innerHTML = "";
  decStatus.classList.remove("error");
  decStatus.textContent = "";
  decDownloadBtn.hidden = true;
  decResetBtn.hidden = true;
  decFilenameRow.hidden = true;
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

async function addDecodeImages(files) {
  const gridDim = parseInt(decGridDim.value, 10);
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
      const texts = decodeAllInImage(imgData, gridDim);
      let addedHere = 0;
      for (const text of texts) {
        const r = scanner.addDecodedText(text);
        if (r.added) addedHere++;
      }
      nameSpan.textContent = `${file.name} — ${texts.length} QR trovati, ${addedHere} nuovi capitoli`;
      if (texts.length === 0) {
        li.classList.add("status");
        nameSpan.textContent += " (nessun QR riconosciuto: controlla il numero QR/griglia impostato sopra)";
      }
    } catch (e) {
      nameSpan.textContent = `${file.name} — errore: ${e.message}`;
      decStatus.classList.add("error");
      decStatus.textContent = "Errore leggendo " + file.name + ": " + e.message;
      continue;
    }

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
  }
}
