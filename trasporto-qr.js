"use strict";
// Trasporto QR di byte arbitrari — nessun motore balzar coinvolto.
// Encoding: /api/qr (server, riusa balzar/qr.py) su byte grezzi invece che
// su un payload balzar (l'endpoint non distingue i due casi, §2.4c).
// Decoding: interamente client-side, port JS del formato BZC1
// (balzar/payload.py) + della geometria di ritaglio a griglia
// (_tile_boxes in balzar/qr.py) + jsQR (vendorizzato) per la decodifica
// del singolo QR — nessun file lascia il browser.

// ---------------------------------------------------------- BZC1 / CRC32

const CHUNK_MAGIC = [0x42, 0x5a, 0x43, 0x31]; // "BZC1"
const CHUNK_HEADER = 12;

let CRC_TABLE = null;
function crc32(bytes) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64(bytes) {
  // chunked to avoid blowing the call stack with String.fromCharCode(...bytes)
  // on a large file (spread of a big typed array), same concern noted for
  // base64ToBytes elsewhere in app.js.
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function parseChunk(bytes) {
  if (bytes.length < CHUNK_HEADER) return null;
  for (let i = 0; i < 4; i++) if (bytes[i] !== CHUNK_MAGIC[i]) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return {
    index: view.getUint16(4, false),
    total: view.getUint16(6, false),
    crc: view.getUint32(8, false),
    data: bytes.slice(CHUNK_HEADER),
  };
}

class LiveScanner {
  constructor() {
    this.parts = new Map();
    this.total = null;
    this.crc = null;
  }
  // returns {added: bool, complete, missing} -- added=false means this
  // text wasn't a recognizable BZC1 chunk (not an error: could be a QR
  // decoded from something else entirely) or a duplicate already seen.
  addDecodedText(text) {
    let bytes;
    try { bytes = b64ToBytes(text); } catch (e) { return { added: false, ...this._status() }; }
    const chunk = parseChunk(bytes);
    if (!chunk) return { added: false, ...this._status() };
    if (this.total === null) { this.total = chunk.total; this.crc = chunk.crc; }
    else if (chunk.total !== this.total || chunk.crc !== this.crc) {
      throw new Error("i QR trovati appartengono a payload diversi (CRC/totale non coincidono)");
    }
    const already = this.parts.has(chunk.index);
    this.parts.set(chunk.index, chunk.data);
    return { added: !already, ...this._status() };
  }
  _status() {
    if (this.total === null) return { complete: false, missing: null, total: null, have: 0 };
    const missing = [];
    for (let i = 0; i < this.total; i++) if (!this.parts.has(i)) missing.push(i);
    return { complete: missing.length === 0, missing: missing.length ? missing : null,
             total: this.total, have: this.parts.size };
  }
  status() { return this._status(); }
  result() {
    const st = this._status();
    if (!st.complete) throw new Error("scansione incompleta, mancano i capitoli: " + JSON.stringify(st.missing));
    let totalLen = 0;
    for (let i = 0; i < this.total; i++) totalLen += this.parts.get(i).length;
    const out = new Uint8Array(totalLen);
    let off = 0;
    for (let i = 0; i < this.total; i++) { out.set(this.parts.get(i), off); off += this.parts.get(i).length; }
    if (crc32(out) !== this.crc) throw new Error("integrita' fallita: il CRC32 non corrisponde dopo il riassemblaggio");
    return out;
  }
}

// --------------------------------------------------- grid crop geometry
// Faithful port of balzar/qr.py:_tile_boxes -- inverts the same
// _compose_grid layout formula (cell/pad solved by a few fixed-point
// iterations) instead of guessing a uniform division. See CLAUDE.md
// §2.4b for why a guessed margin measured WORSE than no tiling.

function tileBoxes(width, height, gridDim) {
  const cols = gridDim, rows = gridDim;
  let cell = (width * 15) / (16 * cols + 1);
  let pad = 12;
  for (let i = 0; i < 4; i++) {
    pad = Math.max(12, Math.floor(Math.floor(cell) / 15));
    cell = (width - pad * (cols + 1)) / cols;
  }
  cell = Math.round(cell);
  pad = Math.max(12, Math.floor(cell / 15));
  const labelH = 22;
  const top = Math.max(0, height - (rows * (cell + pad + labelH) + pad));
  if (cell < 20) return [];
  const boxes = [];
  const slack = 3;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = pad + c * (cell + pad);
      const y = top + pad + r * (cell + pad + labelH);
      const left = Math.max(0, x - slack), upper = Math.max(0, y - slack);
      const right = Math.min(width, x + cell + slack), lower = Math.min(height, y + cell + slack);
      if (left < right && upper < lower) boxes.push([left, upper, right, lower]);
    }
  }
  return boxes;
}

function cropImageData(full, box) {
  const [left, upper, right, lower] = box;
  const w = right - left, h = lower - upper;
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    const srcStart = ((upper + y) * full.width + left) * 4;
    out.set(full.data.subarray(srcStart, srcStart + w * 4), y * w * 4);
  }
  return { width: w, height: h, data: out };
}

function loadImageData(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0);
      try {
        resolve(ctx.getImageData(0, 0, canvas.width, canvas.height));
      } catch (e) {
        reject(e);
      } finally {
        URL.revokeObjectURL(img.src);
      }
    };
    img.onerror = () => reject(new Error("immagine non leggibile"));
    img.src = URL.createObjectURL(file);
  });
}

// decode every QR candidate region in one image; gridDim=1 means "no
// grid, the whole image is one QR". Decode failures on individual cells
// are expected (e.g. an empty cell in a partial last frame) and simply
// skipped, never fatal.
//
// Uses jsQR, not @paulmillr/qr: measured on a real generated grid (24
// chunks, grid_dim=2, PDF payload) @paulmillr/qr's decodeQR threw an
// internal error on 3/24 otherwise-perfectly-valid QR crops (a bug in
// its finder-pattern transform, confirmed by isolating the exact
// failing step -- not a crop-geometry issue: the same crop decodes fine
// via pyzbar/ZBar, and no amount of extra margin/padding fixed it).
// jsQR decoded all 24/24 on the same images. Correctness beats
// "actively maintained" here -- jsQR is unmaintained but battle-tested;
// @paulmillr/qr is newer and, on this measurement, less reliable.
function decodeAllInImage(imgData, gridDim) {
  const texts = [];
  const regions = gridDim <= 1 ? [imgData] : tileBoxes(imgData.width, imgData.height, gridDim).map(b => cropImageData(imgData, b));
  for (const region of regions) {
    const result = jsQR(region.data, region.width, region.height);
    if (result) texts.push(result.data);
  }
  return texts;
}

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
