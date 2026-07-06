const dropzone = document.getElementById("drop");
const fileInput = document.getElementById("file-input");
const browseBtn = document.getElementById("browse-btn");
const maxDimSelect = document.getElementById("max-dim");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const imgOriginal = document.getElementById("img-original");
const imgRendered = document.getElementById("img-rendered");
const statsTable = document.getElementById("stats-table");
const programText = document.getElementById("program-text");
const dlPayloadBtn = document.getElementById("dl-payload");
const dlProgramBtn = document.getElementById("dl-program");

let lastResult = null;

browseBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

["dragenter", "dragover"].forEach(evt =>
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach(evt =>
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});

function setStatus(msg, isError) {
  statusEl.hidden = false;
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", !!isError);
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",", 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// Vercel rifiuta body oltre ~4.5MB; il base64 aggiunge ~33%, quindi il
// file originale deve stare sotto ~3.3MB. Meglio dirlo subito e chiaro.
const MAX_FILE_BYTES = 3.3 * 1024 * 1024;

async function handleFile(file) {
  resultEl.hidden = true;
  if (file.size > MAX_FILE_BYTES) {
    setStatus(
      `File troppo grande (${(file.size / 1048576).toFixed(1)} MB): il limite di upload è ~3.3 MB. ` +
      `Nota: il peso del file non conta per il test — l'analisi lavora sui pixel dopo il ` +
      `ridimensionamento alla risoluzione scelta. Riduci il file (es. riesporta come JPEG) e riprova.`,
      true
    );
    return;
  }
  const maxDimVal = parseInt(maxDimSelect.value, 10);
  setStatus(
    `Analisi di "${file.name}" in corso a ${maxDimVal}px… ` +
    (maxDimVal >= 600 ? "alle risoluzioni alte può servire più di un minuto." : "")
  );
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(file);
    });
    imgOriginal.src = dataUrl;

    const data = dataUrl.split(",", 2)[1];

    const res = await fetch("/api/encode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data, max_dim: maxDimVal }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");

    lastResult = json;
    render(json, file);
    setStatus(`Fatto: ${file.name}`);
  } catch (err) {
    setStatus("Errore: " + err.message, true);
  }
}

function fmtBytes(n) {
  return n.toLocaleString("it-IT") + " B";
}

function render(r, file) {
  imgRendered.src = "data:image/png;base64," + r.preview_png_base64;
  programText.textContent = r.program_text;

  dlPayloadBtn.disabled = !!r.payload_omitted;
  dlPayloadBtn.title = r.payload_omitted
    ? "payload più grande del limite di risposta del server (caso senza guadagno): usa la CLI in locale"
    : "";
  dlProgramBtn.disabled = !!r.program_truncated;
  dlProgramBtn.title = r.program_truncated
    ? "programma troncato nella risposta: ricavalo dal payload con 'python -m balzar decode'"
    : "";

  const gain = r.expansion_vs_raw >= 1;
  const rows = [
    ["dimensioni analizzate", `${r.width}×${r.height} px`],
    ["file caricato", fmtBytes(r.upload_bytes)],
    ["colori (palette)", r.palette_size + (r.lossless ? "" : " (quantizzati, non esatti)")],
    ["fedeltà", r.fidelity_label],
    ["tiling rilevato", r.tile ? `sì, ${r.tile[0]}×${r.tile[1]} px` : "no"],
    ["istruzioni generate", r.instruction_count],
    ["RGB grezzo equivalente", fmtBytes(r.raw_rgb_bytes)],
    ["payload balzar", fmtBytes(r.payload_bytes)],
    [
      "fattore vs RGB grezzo",
      `<span class="${gain ? "stat-good" : "stat-bad"}">${gain ? "" : "nessun guadagno — "}${r.expansion_vs_raw.toFixed(1)}×</span>`,
    ],
    ["entra in un QR code", r.fits_qr ? "sì" : "no"],
  ];
  if (r.preview_scaled) {
    rows.push(["anteprima", "ridotta per la visualizzazione (il payload genera la risoluzione piena)"]);
  }
  statsTable.innerHTML = rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join("");

  resultEl.hidden = false;
}

function downloadBlob(bytes, filename, mime) {
  const blob = new Blob([bytes], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

// ---------------------------------------------------------- generatore QR
//
// Condiviso da tutti i tab che producono un payload: prende il
// payload_base64 gia' nel risultato (mai ricalcolato lato client) e lo
// manda a /api/qr, che riusa balzar/qr.py cosi' com'e' (nessuna
// dipendenza nativa: qrcode e' puro Python, a differenza di pyzbar che
// serve solo per *leggere* un QR da una foto).
function setupQrButton(prefix, getPayloadBase64) {
  const btn = document.getElementById(`${prefix}-gen-qr-btn`);
  const modeSelect = document.getElementById(`${prefix}-qr-mode`);
  const resultEl = document.getElementById(`${prefix}-qr-result`);
  const imgEl = document.getElementById(`${prefix}-qr-img`);
  const pagesEl = document.getElementById(`${prefix}-qr-pages`);
  const noteEl = document.getElementById(`${prefix}-qr-note`);
  const dlBtn = document.getElementById(`${prefix}-qr-dl-btn`);
  let lastDownload = null; // { bytes, filename, mime } for single/gif mode

  btn.addEventListener("click", async () => {
    const payloadB64 = getPayloadBase64();
    if (!payloadB64) {
      resultEl.hidden = false;
      noteEl.textContent = "Payload non disponibile per generare un QR (omesso perché troppo grande).";
      noteEl.classList.add("error");
      return;
    }
    const mode = modeSelect.value;
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Genero…";
    imgEl.hidden = true;
    pagesEl.innerHTML = "";
    dlBtn.hidden = (mode === "pages");
    lastDownload = null;
    try {
      const res = await fetch("/api/qr", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload_base64: payloadB64, mode, grid_dim: 4 }),
      });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error || "errore sconosciuto");

      noteEl.classList.remove("error");
      if (json.mode === "single") {
        imgEl.src = "data:image/png;base64," + json.qr_png_base64;
        imgEl.hidden = false;
        lastDownload = { bytes: base64ToBytes(json.qr_png_base64), filename: "payload_qr.png", mime: "image/png" };
        noteEl.textContent = json.single_qr
          ? "QR singolo — scansionalo con qualunque lettore o con 'balzar scan'."
          : "Il payload non entra in un solo QR: griglia auto-dimensionata in una sola immagine — utile come file, ma non pensata per essere fotografata/stampata a dimensione leggibile se la griglia è grande. Prova 'Sequenza QR' per una serie di frame più piccoli.";
      } else if (json.mode === "gif") {
        if (json.gif_omitted) {
          noteEl.classList.add("error");
          noteEl.textContent = `Sequenza generata (${json.n_frames} frame da ${json.grid_dim}×${json.grid_dim} QR) ma la GIF risultante è troppo grande per essere restituita da questo deployment.`;
        } else {
          imgEl.src = "data:image/gif;base64," + json.qr_gif_base64;
          imgEl.hidden = false;
          lastDownload = { bytes: base64ToBytes(json.qr_gif_base64), filename: "payload_qr.gif", mime: "image/gif" };
          noteEl.textContent = `Sequenza di ${json.n_frames} frame (${json.grid_dim}×${json.grid_dim} QR ciascuno) in una GIF animata — riassembla ogni frame con la classe LiveScanner di balzar/qr.py (non ancora un comando CLI dedicato), in qualsiasi ordine e con ripetizioni tollerate.`;
        }
      } else { // pages
        if (json.pages_omitted) {
          noteEl.classList.add("error");
          noteEl.textContent = `Sequenza generata (${json.n_frames} pagine da ${json.grid_dim}×${json.grid_dim} QR) ma troppo grande per essere restituita in un colpo solo da questo deployment.`;
        } else {
          json.pages.forEach((page, i) => {
            const item = document.createElement("div");
            item.className = "qr-page-item";
            const pageImg = document.createElement("img");
            pageImg.src = "data:image/png;base64," + page.png_base64;
            pageImg.alt = `Pagina ${i + 1} di ${json.n_frames}`;
            const pageDlBtn = document.createElement("button");
            pageDlBtn.type = "button";
            pageDlBtn.textContent = `Scarica pagina ${i + 1}/${json.n_frames}`;
            pageDlBtn.addEventListener("click", () =>
              downloadBlob(base64ToBytes(page.png_base64), `payload_qr_page_${i + 1}_of_${json.n_frames}.png`, "image/png"));
            item.appendChild(pageImg);
            item.appendChild(pageDlBtn);
            pagesEl.appendChild(item);
          });
          noteEl.textContent = `Sequenza di ${json.n_frames} pagine (${json.grid_dim}×${json.grid_dim} QR ciascuna) — stampa/fotografa una pagina alla volta, in qualsiasi ordine, poi riassembla con la classe LiveScanner di balzar/qr.py (non ancora un comando CLI dedicato).`;
        }
      }
      resultEl.hidden = false;
    } catch (err) {
      noteEl.classList.add("error");
      noteEl.textContent = "Errore: " + err.message;
      resultEl.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });

  dlBtn.addEventListener("click", () => {
    if (!lastDownload) return;
    downloadBlob(lastDownload.bytes, lastDownload.filename, lastDownload.mime);
  });
}

dlPayloadBtn.addEventListener("click", () => {
  if (!lastResult) return;
  downloadBlob(base64ToBytes(lastResult.payload_base64), "output.bzp", "application/octet-stream");
});

dlProgramBtn.addEventListener("click", () => {
  if (!lastResult) return;
  downloadBlob(new TextEncoder().encode(lastResult.program_text), "output.bzr", "text/plain");
});

setupQrButton("encode", () => (lastResult && !lastResult.payload_omitted) ? lastResult.payload_base64 : null);

// ---------------------------------------------------------- tabs

const TAB_NAMES = ["encode", "vector", "video", "sequence", "3d", "open"];
const tabButtons = Object.fromEntries(TAB_NAMES.map(n => [n, document.getElementById(`tab-${n}`)]));
const tabPanels = Object.fromEntries(TAB_NAMES.map(n => [n, document.getElementById(`panel-${n}`)]));

function activateTab(tab) {
  for (const name of TAB_NAMES) {
    const active = name === tab;
    tabButtons[name].classList.toggle("active", active);
    tabPanels[name].hidden = !active;
  }
}
for (const name of TAB_NAMES) {
  tabButtons[name].addEventListener("click", () => activateTab(name));
}

// -------------------------------------------------------- ingestione vettoriale (SVG/DXF)

const vectorDrop = document.getElementById("vector-drop");
const vectorFileInput = document.getElementById("vector-file-input");
const vectorBrowseBtn = document.getElementById("vector-browse-btn");
const vectorMaxDim = document.getElementById("vector-max-dim");
const vectorStatusEl = document.getElementById("vector-status");
const vectorResultEl = document.getElementById("vector-result");
const vectorOriginalFigure = document.getElementById("vector-original-figure");
const vectorImgOriginal = document.getElementById("vector-img-original");
const vectorImgRendered = document.getElementById("vector-img-rendered");
const vectorStatsTable = document.getElementById("vector-stats-table");
const vectorSkippedEl = document.getElementById("vector-skipped");
const vectorProgramText = document.getElementById("vector-program-text");
const vectorDlPayload = document.getElementById("vector-dl-payload");
const vectorDlProgram = document.getElementById("vector-dl-program");
const vectorDlSvg = document.getElementById("vector-dl-svg");

let lastVectorResult = null;

vectorBrowseBtn.addEventListener("click", () => vectorFileInput.click());
vectorFileInput.addEventListener("change", () => {
  if (vectorFileInput.files[0]) handleVectorFile(vectorFileInput.files[0]);
});
["dragenter", "dragover"].forEach(evt =>
  vectorDrop.addEventListener(evt, e => { e.preventDefault(); vectorDrop.classList.add("dragover"); })
);
["dragleave", "drop"].forEach(evt =>
  vectorDrop.addEventListener(evt, e => { e.preventDefault(); vectorDrop.classList.remove("dragover"); })
);
vectorDrop.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  if (file) handleVectorFile(file);
});

function setVectorStatus(msg, isError) {
  vectorStatusEl.hidden = false;
  vectorStatusEl.textContent = msg;
  vectorStatusEl.classList.toggle("error", !!isError);
}

async function handleVectorFile(file) {
  vectorResultEl.hidden = true;
  const lower = file.name.toLowerCase();
  if (!lower.endsWith(".svg") && !lower.endsWith(".dxf")) {
    setVectorStatus(`Estensione non riconosciuta: atteso .svg o .dxf`, true);
    return;
  }
  if (file.size > MAX_FILE_BYTES) {
    setVectorStatus(`File troppo grande (${(file.size / 1048576).toFixed(1)} MB): il limite è ~3.3 MB.`, true);
    return;
  }
  setVectorStatus(`Ingestione di "${file.name}" in corso…`);
  try {
    const data = await fileToBase64(file);
    const res = await fetch("/api/encode_vector", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data, filename: file.name, max_dim: parseInt(vectorMaxDim.value, 10) }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");

    lastVectorResult = json;
    if (lower.endsWith(".svg")) {
      vectorOriginalFigure.hidden = false;
      vectorImgOriginal.src = "data:image/svg+xml;base64," + data;
    } else {
      vectorOriginalFigure.hidden = true;
    }
    renderVectorResult(json);
    setVectorStatus(`Fatto: ${file.name}`);
  } catch (err) {
    setVectorStatus("Errore: " + err.message, true);
  }
}

function renderVectorResult(r) {
  vectorImgRendered.src = "data:image/png;base64," + r.preview_png_base64;
  vectorProgramText.textContent = r.program_text;

  const rows = [
    ["formato sorgente", r.source_format.toUpperCase()],
    ["dimensioni", `${r.width}×${r.height} px`],
    ["elementi convertiti", r.element_count !== undefined ? r.element_count : "—"],
    ["elementi saltati", r.skipped.length],
    ["istruzioni generate", r.instruction_count],
    ["RGB grezzo equivalente", fmtBytes(r.raw_rgb_bytes)],
    ["payload balzar", fmtBytes(r.payload_bytes)],
    ["fattore vs RGB grezzo", `<span class="stat-good">${r.expansion_vs_raw.toFixed(1)}×</span>`],
    ["entra in un QR code", r.fits_qr ? "sì" : "no"],
  ];
  vectorStatsTable.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");

  vectorSkippedEl.hidden = r.skipped.length === 0;
  if (r.skipped.length) {
    vectorSkippedEl.innerHTML = "saltato: " + r.skipped.map(s => `<br>&nbsp;&nbsp;• ${s}`).join("");
  }

  vectorDlPayload.disabled = !!r.payload_omitted;
  vectorDlProgram.disabled = !!r.program_truncated;
  vectorDlSvg.hidden = !r.svg_available;

  vectorResultEl.hidden = false;
}

vectorDlPayload.addEventListener("click", () => {
  if (!lastVectorResult) return;
  downloadBlob(base64ToBytes(lastVectorResult.payload_base64), "output.bzp", "application/octet-stream");
});
vectorDlProgram.addEventListener("click", () => {
  if (!lastVectorResult) return;
  downloadBlob(new TextEncoder().encode(lastVectorResult.program_text), "output.bzr", "text/plain");
});
vectorDlSvg.addEventListener("click", () => {
  if (!lastVectorResult || !lastVectorResult.svg_text) return;
  downloadBlob(new TextEncoder().encode(lastVectorResult.svg_text), "rigenerato.svg", "image/svg+xml");
});

setupQrButton("vector", () => (lastVectorResult && !lastVectorResult.payload_omitted) ? lastVectorResult.payload_base64 : null);

// ------------------------------------------------------------ video (GIF animata)

const videoDrop = document.getElementById("video-drop");
const videoFileInput = document.getElementById("video-file-input");
const videoBrowseBtn = document.getElementById("video-browse-btn");
const videoMaxDim = document.getElementById("video-max-dim");
const videoStatusEl = document.getElementById("video-status");
const videoResultEl = document.getElementById("video-result");
const videoImgOriginal = document.getElementById("video-img-original");
const videoImgRendered = document.getElementById("video-img-rendered");
const videoStatsTable = document.getElementById("video-stats-table");
const videoProgramText = document.getElementById("video-program-text");
const videoDlPayload = document.getElementById("video-dl-payload");
const videoDlProgram = document.getElementById("video-dl-program");

let lastVideoResult = null;

videoBrowseBtn.addEventListener("click", () => videoFileInput.click());
videoFileInput.addEventListener("change", () => {
  if (videoFileInput.files[0]) handleVideoFile(videoFileInput.files[0]);
});
["dragenter", "dragover"].forEach(evt =>
  videoDrop.addEventListener(evt, e => { e.preventDefault(); videoDrop.classList.add("dragover"); })
);
["dragleave", "drop"].forEach(evt =>
  videoDrop.addEventListener(evt, e => { e.preventDefault(); videoDrop.classList.remove("dragover"); })
);
videoDrop.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  if (file) handleVideoFile(file);
});

function setVideoStatus(msg, isError) {
  videoStatusEl.hidden = false;
  videoStatusEl.textContent = msg;
  videoStatusEl.classList.toggle("error", !!isError);
}

async function handleVideoFile(file) {
  videoResultEl.hidden = true;
  if (file.size > MAX_FILE_BYTES) {
    setVideoStatus(`File troppo grande (${(file.size / 1048576).toFixed(1)} MB): il limite è ~3.3 MB.`, true);
    return;
  }
  setVideoStatus(`Codifica di "${file.name}" in corso…`);
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(file);
    });
    videoImgOriginal.src = dataUrl;
    const data = dataUrl.split(",", 2)[1];

    const res = await fetch("/api/encode_video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data, max_dim: parseInt(videoMaxDim.value, 10) }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");

    lastVideoResult = json;
    renderVideoResult(json);
    setVideoStatus(`Fatto: ${file.name}`);
  } catch (err) {
    setVideoStatus("Errore: " + err.message, true);
  }
}

function renderVideoResult(r) {
  videoImgRendered.src = "data:image/gif;base64," + r.preview_gif_base64;

  const rows = [
    ["dimensioni", `${r.width}×${r.height} px`],
    ["frame", r.frame_count],
    ["colori (palette)", r.palette_size + (r.lossless ? "" : ` (median-cut, errore medio colore ${r.mean_color_error})`)],
    ["pixel cambiati dopo il frame 0", r.delta_pixels_total.toLocaleString("it-IT")],
    ["istruzioni generate", r.instruction_count],
    ["RGB grezzo equivalente", fmtBytes(r.raw_rgb_bytes)],
    ["payload balzar", fmtBytes(r.payload_bytes)],
    ["fattore vs RGB grezzo", `<span class="stat-good">${r.expansion_vs_raw.toFixed(1)}×</span>`],
    ["entra in un QR code", r.fits_qr ? "sì" : "no"],
  ];
  if (r.preview_scaled) {
    rows.push(["anteprima", "ridotta per la visualizzazione (il payload genera la risoluzione piena)"]);
  }
  videoStatsTable.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");

  videoDlPayload.disabled = !!r.payload_omitted;
  videoDlProgram.disabled = !!r.program_truncated;
  videoProgramText.textContent = r.program_text;

  videoResultEl.hidden = false;
}

videoDlPayload.addEventListener("click", () => {
  if (!lastVideoResult) return;
  downloadBlob(base64ToBytes(lastVideoResult.payload_base64), "output.bzp", "application/octet-stream");
});
videoDlProgram.addEventListener("click", () => {
  if (!lastVideoResult) return;
  downloadBlob(new TextEncoder().encode(lastVideoResult.program_text), "output.bzr", "text/plain");
});

setupQrButton("video", () => (lastVideoResult && !lastVideoResult.payload_omitted) ? lastVideoResult.payload_base64 : null);

// -------------------------------------------------------- sequenza multi-file

const sequenceDrop = document.getElementById("sequence-drop");
const sequenceFileInput = document.getElementById("sequence-file-input");
const sequenceBrowseBtn = document.getElementById("sequence-browse-btn");
const sequenceMaxDim = document.getElementById("sequence-max-dim");
const sequenceFileList = document.getElementById("sequence-file-list");
const sequenceEncodeBtn = document.getElementById("sequence-encode-btn");
const sequenceClearBtn = document.getElementById("sequence-clear-btn");
const sequenceStatusEl = document.getElementById("sequence-status");
const sequenceResultEl = document.getElementById("sequence-result");
const sequenceImgRendered = document.getElementById("sequence-img-rendered");
const sequenceStatsTable = document.getElementById("sequence-stats-table");
const sequenceProgramText = document.getElementById("sequence-program-text");
const sequenceDlPayload = document.getElementById("sequence-dl-payload");
const sequenceDlProgram = document.getElementById("sequence-dl-program");
const sequencePrevBtn = document.getElementById("sequence-prev");
const sequenceNextBtn = document.getElementById("sequence-next");
const sequenceFrameLabel = document.getElementById("sequence-frame-label");
const independentResultEl = document.getElementById("independent-result");
const independentSummaryEl = document.getElementById("independent-summary");
const independentItemsEl = document.getElementById("independent-items");

let pendingSequenceFiles = []; // File objects, in the order to encode
let lastSequenceResult = null;
let sequenceFrameIndex = 0;

function currentSequenceMode() {
  return document.querySelector('input[name="sequence-mode"]:checked').value;
}

sequenceBrowseBtn.addEventListener("click", () => sequenceFileInput.click());
sequenceFileInput.addEventListener("change", () => {
  addSequenceFiles(Array.from(sequenceFileInput.files));
  sequenceFileInput.value = "";
});
["dragenter", "dragover"].forEach(evt =>
  sequenceDrop.addEventListener(evt, e => { e.preventDefault(); sequenceDrop.classList.add("dragover"); })
);
["dragleave", "drop"].forEach(evt =>
  sequenceDrop.addEventListener(evt, e => { e.preventDefault(); sequenceDrop.classList.remove("dragover"); })
);
sequenceDrop.addEventListener("drop", e => {
  addSequenceFiles(Array.from(e.dataTransfer.files));
});

function setSequenceStatus(msg, isError) {
  sequenceStatusEl.hidden = false;
  sequenceStatusEl.textContent = msg;
  sequenceStatusEl.classList.toggle("error", !!isError);
}

function addSequenceFiles(files) {
  pendingSequenceFiles.push(...files);
  renderSequenceFileList();
}

function renderSequenceFileList() {
  sequenceFileList.innerHTML = "";
  pendingSequenceFiles.forEach((file, i) => {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="file-order">${i + 1}.</span>
      <span class="file-name">${file.name}</span>
      <button type="button" data-action="up" ${i === 0 ? "disabled" : ""}>▲</button>
      <button type="button" data-action="down" ${i === pendingSequenceFiles.length - 1 ? "disabled" : ""}>▼</button>
      <button type="button" data-action="remove">✕</button>
    `;
    li.querySelector('[data-action="up"]').addEventListener("click", () => moveSequenceFile(i, -1));
    li.querySelector('[data-action="down"]').addEventListener("click", () => moveSequenceFile(i, 1));
    li.querySelector('[data-action="remove"]').addEventListener("click", () => removeSequenceFile(i));
    sequenceFileList.appendChild(li);
  });
  const minFiles = currentSequenceMode() === "independent" ? 1 : 2;
  sequenceEncodeBtn.hidden = pendingSequenceFiles.length < minFiles;
  sequenceClearBtn.hidden = pendingSequenceFiles.length === 0;
}

document.querySelectorAll('input[name="sequence-mode"]').forEach(radio => {
  radio.addEventListener("change", () => {
    sequenceEncodeBtn.textContent = currentSequenceMode() === "independent"
      ? "Codifica file indipendenti" : "Codifica sequenza";
    renderSequenceFileList();
  });
});

sequenceClearBtn.addEventListener("click", () => {
  pendingSequenceFiles = [];
  lastSequenceResult = null;
  sequenceResultEl.hidden = true;
  independentResultEl.hidden = true;
  sequenceStatusEl.hidden = true;
  renderSequenceFileList();
});

function moveSequenceFile(i, delta) {
  const j = i + delta;
  if (j < 0 || j >= pendingSequenceFiles.length) return;
  [pendingSequenceFiles[i], pendingSequenceFiles[j]] = [pendingSequenceFiles[j], pendingSequenceFiles[i]];
  renderSequenceFileList();
}

function removeSequenceFile(i) {
  pendingSequenceFiles.splice(i, 1);
  renderSequenceFileList();
}

sequenceEncodeBtn.addEventListener("click", async () => {
  const mode = currentSequenceMode();
  const minFiles = mode === "independent" ? 1 : 2;
  sequenceResultEl.hidden = true;
  independentResultEl.hidden = true;
  if (pendingSequenceFiles.length < minFiles) return;
  const totalBytes = pendingSequenceFiles.reduce((sum, f) => sum + f.size, 0);
  if (totalBytes > MAX_FILE_BYTES) {
    setSequenceStatus(
      `File totali troppo grandi (${(totalBytes / 1048576).toFixed(1)} MB): il limite combinato è ~3.3 MB.`,
      true
    );
    return;
  }
  setSequenceStatus(`Codifica di ${pendingSequenceFiles.length} file in corso…`);
  try {
    const files = await Promise.all(pendingSequenceFiles.map(async f => ({
      filename: f.name,
      data: await fileToBase64(f),
    })));
    const res = await fetch("/api/encode_sequence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files, mode, max_dim: parseInt(sequenceMaxDim.value, 10) }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");

    if (mode === "independent") {
      renderIndependentResults(json);
      setSequenceStatus(`Fatto: ${json.success_count}/${json.file_count} file codificati`);
    } else {
      lastSequenceResult = json;
      sequenceFrameIndex = 0;
      renderSequenceResult(json);
      setSequenceStatus(`Fatto: ${pendingSequenceFiles.length} file → ${json.frame_count} frame`);
    }
  } catch (err) {
    setSequenceStatus("Errore: " + err.message, true);
  }
});

function renderIndependentResults(resp) {
  independentSummaryEl.textContent =
    `${resp.success_count} di ${resp.file_count} file codificati con successo.`;
  independentItemsEl.innerHTML = "";

  resp.items.forEach((item, i) => {
    const card = document.createElement("div");
    card.className = "independent-item" + (item.ok ? "" : " failed");

    if (!item.ok) {
      card.innerHTML = `
        <div class="independent-item-header">
          <span>${item.filename}</span>
          <span class="badge-fail">✕ errore</span>
        </div>
        <p class="honesty">${item.error}</p>
      `;
      independentItemsEl.appendChild(card);
      return;
    }

    const idBase = `indep-${i}`;
    card.innerHTML = `
      <div class="independent-item-header">
        <span>${item.filename}</span>
        <span class="badge-ok">✓ ${item.source_format.toUpperCase()}</span>
      </div>
      <div class="item-body">
        <img src="data:image/png;base64,${item.preview_png_base64}" alt="rigenerato: ${item.filename}">
        <table class="stats">
          <tr><td>dimensioni</td><td>${item.width}×${item.height} px</td></tr>
          <tr><td>istruzioni</td><td>${item.instruction_count}</td></tr>
          <tr><td>payload</td><td>${fmtBytes(item.payload_bytes)}</td></tr>
          <tr><td>entra in un QR code</td><td>${item.fits_qr ? "sì" : "no"}</td></tr>
          ${item.skipped && item.skipped.length ? `<tr><td>elementi saltati</td><td>${item.skipped.length}</td></tr>` : ""}
        </table>
      </div>
      <div class="downloads">
        <button type="button" data-action="dl-payload">scarica payload (.bzp)</button>
        <button type="button" data-action="dl-program">scarica programma (.bzr)</button>
        <button type="button" data-action="gen-qr">genera QR</button>
      </div>
      <div class="qr-block" data-role="qr-result" hidden>
        <img class="qr-image" alt="QR del payload">
        <p class="honesty"></p>
        <button type="button" data-action="dl-qr">scarica QR (PNG)</button>
      </div>
    `;

    card.querySelector('[data-action="dl-payload"]').addEventListener("click", () => {
      downloadBlob(base64ToBytes(item.payload_base64), `${item.filename}.bzp`, "application/octet-stream");
    });
    card.querySelector('[data-action="dl-program"]').addEventListener("click", () => {
      downloadBlob(new TextEncoder().encode(item.program_text), `${item.filename}.bzr`, "text/plain");
    });

    const qrBlock = card.querySelector('[data-role="qr-result"]');
    const qrImg = qrBlock.querySelector("img");
    const qrNote = qrBlock.querySelector("p");
    let lastQrB64 = null;
    card.querySelector('[data-action="gen-qr"]').addEventListener("click", async (e) => {
      if (item.payload_omitted) {
        qrBlock.hidden = false;
        qrNote.textContent = "Payload omesso (troppo grande per questa risposta).";
        return;
      }
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const res = await fetch("/api/qr", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ payload_base64: item.payload_base64 }),
        });
        const qrJson = await res.json();
        if (!qrJson.ok) throw new Error(qrJson.error || "errore sconosciuto");
        lastQrB64 = qrJson.qr_png_base64;
        qrImg.src = "data:image/png;base64," + lastQrB64;
        qrNote.textContent = qrJson.single_qr
          ? "QR singolo — scansionalo con qualunque lettore o con 'balzar scan'."
          : "Il payload non entra in un solo QR: griglia auto-dimensionata.";
        qrBlock.hidden = false;
      } catch (err) {
        qrNote.textContent = "Errore: " + err.message;
        qrBlock.hidden = false;
      } finally {
        btn.disabled = false;
      }
    });
    card.querySelector('[data-action="dl-qr"]').addEventListener("click", () => {
      if (!lastQrB64) return;
      downloadBlob(base64ToBytes(lastQrB64), `${item.filename}_qr.png`, "image/png");
    });

    independentItemsEl.appendChild(card);
  });

  independentResultEl.hidden = false;
}

function sequenceFrames(r) {
  return r.preview_frames_png_base64 || (r.preview_png_base64 ? [r.preview_png_base64] : []);
}

function showSequenceFrame() {
  const frames = sequenceFrames(lastSequenceResult);
  if (!frames.length) return;
  sequenceImgRendered.src = "data:image/png;base64," + frames[sequenceFrameIndex];
  sequenceFrameLabel.textContent = `Step ${sequenceFrameIndex + 1}/${frames.length}`;
}

sequencePrevBtn.addEventListener("click", () => {
  const frames = sequenceFrames(lastSequenceResult);
  if (!frames.length) return;
  sequenceFrameIndex = (sequenceFrameIndex - 1 + frames.length) % frames.length;
  showSequenceFrame();
});
sequenceNextBtn.addEventListener("click", () => {
  const frames = sequenceFrames(lastSequenceResult);
  if (!frames.length) return;
  sequenceFrameIndex = (sequenceFrameIndex + 1) % frames.length;
  showSequenceFrame();
});

function renderSequenceResult(r) {
  showSequenceFrame();

  const rows = [
    ["formato sorgente", (r.source_format || "raster").toUpperCase()],
    ["file → frame", r.frame_count],
    ["dimensioni", `${r.width}×${r.height} px`],
    ["istruzioni generate", r.instruction_count],
    ["RGB grezzo equivalente", fmtBytes(r.raw_rgb_bytes)],
    ["payload balzar", fmtBytes(r.payload_bytes)],
    ["fattore vs RGB grezzo", `<span class="stat-good">${r.expansion_vs_raw.toFixed(1)}×</span>`],
    ["entra in un QR code", r.fits_qr ? "sì" : "no"],
  ];
  if (r.skipped && r.skipped.length) {
    rows.push(["elementi saltati", r.skipped.length]);
  }
  sequenceStatsTable.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");

  sequenceDlPayload.disabled = !!r.payload_omitted;
  sequenceDlProgram.disabled = !!r.program_truncated;
  sequenceProgramText.textContent = r.program_text;

  sequenceResultEl.hidden = false;
}

sequenceDlPayload.addEventListener("click", () => {
  if (!lastSequenceResult) return;
  downloadBlob(base64ToBytes(lastSequenceResult.payload_base64), "sequenza.bzp", "application/octet-stream");
});
sequenceDlProgram.addEventListener("click", () => {
  if (!lastSequenceResult) return;
  downloadBlob(new TextEncoder().encode(lastSequenceResult.program_text), "sequenza.bzr", "text/plain");
});

setupQrButton("sequence", () => (lastSequenceResult && !lastSequenceResult.payload_omitted) ? lastSequenceResult.payload_base64 : null);

// -------------------------------------------------------- assiemi 3D (3DXML)
//
// Niente immagine da renderizzare qui: la "preview" è un vero .glb
// (balzar/gltf.py) mostrato dal web component <model-viewer> (vendorizzato
// in model-viewer.min.js, nessuna dipendenza da CDN — stesso principio
// offline-first del resto del progetto). Il payload BZM1 resta il formato
// di trasporto compatto; il GLB è solo per questa vista, esattamente come
// PNG non è mai il formato che viaggia nel QR.

const threedDrop = document.getElementById("threed-drop");
const threedFileInput = document.getElementById("threed-file-input");
const threedBrowseBtn = document.getElementById("threed-browse-btn");
const threedStatusEl = document.getElementById("threed-status");
const threedResultEl = document.getElementById("threed-result");
const threedViewer = document.getElementById("threed-viewer");
const threedStatsTable = document.getElementById("threed-stats-table");
const threedBomTable = document.getElementById("threed-bom-table");
const threedDlPayload = document.getElementById("threed-dl-payload");
const threedDlGlb = document.getElementById("threed-dl-glb");
const threedGlbOmittedEl = document.getElementById("threed-glb-omitted");
const threedResetBtn = document.getElementById("threed-reset-btn");
const threedExportBtn = document.getElementById("threed-export-btn");

let lastThreedResult = null;
let lastThreedGlbUrl = null;
let threedOriginalColors = null; // Map<Material, [r,g,b,a]>, cached on model load
let threedSelectedName = null;
let threedSelectedCount = null;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const THREED_HIGHLIGHT = [1.0, 0.55, 0.05, 1.0];
const THREED_DIM_ALPHA = 0.12;

function threedCacheColors() {
  threedOriginalColors = new Map();
  threedViewer.model.materials.forEach(m => {
    threedOriginalColors.set(m, m.pbrMetallicRoughness.baseColorFactor.slice());
  });
}

function threedResetSelection() {
  if (!threedOriginalColors) return;
  threedViewer.model.materials.forEach(m => {
    m.pbrMetallicRoughness.setBaseColorFactor(threedOriginalColors.get(m));
  });
  threedSetBomSelection(null);
}

function threedSelectMaterial(material) {
  if (!threedOriginalColors) return;
  threedViewer.model.materials.forEach(m => {
    const orig = threedOriginalColors.get(m);
    if (m === material) m.pbrMetallicRoughness.setBaseColorFactor(THREED_HIGHLIGHT);
    else m.pbrMetallicRoughness.setBaseColorFactor([orig[0], orig[1], orig[2], THREED_DIM_ALPHA]);
  });
  threedSetBomSelection(material.name);
}

function threedSelectByName(name) {
  if (!threedOriginalColors) return;
  threedViewer.model.materials.forEach(m => {
    const orig = threedOriginalColors.get(m);
    if (m.name === name) m.pbrMetallicRoughness.setBaseColorFactor(THREED_HIGHLIGHT);
    else m.pbrMetallicRoughness.setBaseColorFactor([orig[0], orig[1], orig[2], THREED_DIM_ALPHA]);
  });
  threedSetBomSelection(name);
}

function threedSetBomSelection(name) {
  threedBomTable.querySelectorAll("tr.part").forEach(row => {
    row.classList.toggle("selected", name !== null && row.dataset.partName === name);
  });
  threedSelectedName = name;
  if (name !== null) {
    const row = threedBomTable.querySelector(`tr.part[data-part-name="${CSS.escape(name)}"]`);
    threedSelectedCount = row ? row.dataset.partCount : null;
  } else {
    threedSelectedCount = null;
  }
  threedExportBtn.disabled = (threedSelectedName === null);
}

async function threedExportPartSheet() {
  // threedViewer.toDataURL() (no options, straight to
  // displayCanvas().toDataURL()) instead of toBlob({idealAspect:true}):
  // the latter routes through an internal offscreen-canvas resize+crop
  // step that was measured to return a fully transparent capture in this
  // exact layout -- consistent, same byte size every time, so not a
  // timing race (no amount of waiting or retrying fixed it). Losing the
  // idealAspect crop is a cosmetic trade for a capture that actually
  // contains the model.
  if (!threedSelectedName) return;
  const dataUrl = threedViewer.toDataURL("image/png");
  const img = new Image();
  await new Promise(resolve => { img.onload = resolve; img.src = dataUrl; });

  const headerH = 64;
  const canvas = document.createElement("canvas");
  canvas.width = img.width;
  canvas.height = img.height + headerH;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, headerH);
  ctx.fillStyle = "#000000";
  ctx.font = "bold 22px sans-serif";
  ctx.fillText(threedSelectedName, 12, 28);
  ctx.font = "16px sans-serif";
  ctx.fillText(`Quantita' nell'assieme: ${threedSelectedCount ?? "?"}`, 12, 50);

  canvas.toBlob(sheetBlob => {
    downloadBlob(sheetBlob, `scheda_${threedSelectedName.replace(/[^a-z0-9]+/gi, "_")}.png`, "image/png");
  }, "image/png");
}

threedViewer.addEventListener("load", threedCacheColors);
threedViewer.addEventListener("click", (ev) => {
  const material = threedViewer.materialFromPoint(ev.clientX, ev.clientY);
  if (material) threedSelectMaterial(material); else threedResetSelection();
});
threedResetBtn.addEventListener("click", threedResetSelection);
threedExportBtn.addEventListener("click", threedExportPartSheet);

threedBrowseBtn.addEventListener("click", () => threedFileInput.click());
threedFileInput.addEventListener("change", () => {
  if (threedFileInput.files[0]) handleThreedFile(threedFileInput.files[0]);
});
["dragenter", "dragover"].forEach(evt =>
  threedDrop.addEventListener(evt, e => { e.preventDefault(); threedDrop.classList.add("dragover"); })
);
["dragleave", "drop"].forEach(evt =>
  threedDrop.addEventListener(evt, e => { e.preventDefault(); threedDrop.classList.remove("dragover"); })
);
threedDrop.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  if (file) handleThreedFile(file);
});

function setThreedStatus(msg, isError) {
  threedStatusEl.hidden = false;
  threedStatusEl.textContent = msg;
  threedStatusEl.classList.toggle("error", !!isError);
}

async function handleThreedFile(file) {
  threedResultEl.hidden = true;
  if (file.size > MAX_FILE_BYTES) {
    setThreedStatus(
      `File troppo grande (${(file.size / 1048576).toFixed(1)} MB): il limite di upload è ~3.3 MB. ` +
      `Usa la CLI in locale ('balzar encode-3d') per assiemi più pesanti.`,
      true
    );
    return;
  }
  setThreedStatus(`Analisi di "${file.name}" in corso…`);
  try {
    const data = await fileToBase64(file);
    const res = await fetch("/api/encode_3d", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");

    lastThreedResult = json;
    renderThreedResult(json);
    setThreedStatus(`Fatto: ${file.name}`);
  } catch (err) {
    setThreedStatus("Errore: " + err.message, true);
  }
}

function renderThreedResult(r) {
  // Un-hide the container BEFORE setting .src: model-viewer measures its
  // parent's size when it starts loading, and doing this the other way
  // around (as a first version did) meant it could initialize against a
  // still-hidden (display:none, zero-size) container -- toBlob() then
  // produced a real-looking-dimensions-but-blank capture (a small,
  // consistent byte size every time) even though the on-screen render
  // itself looked completely correct, since normal rendering re-measures
  // on becoming visible but toBlob()'s internal snapshot canvas did not.
  threedResultEl.hidden = false;

  if (lastThreedGlbUrl) URL.revokeObjectURL(lastThreedGlbUrl);
  threedGlbOmittedEl.hidden = !r.glb_omitted;
  if (!r.glb_omitted) {
    const blob = new Blob([base64ToBytes(r.glb_base64)], { type: "model/gltf-binary" });
    lastThreedGlbUrl = URL.createObjectURL(blob);
    threedViewer.src = lastThreedGlbUrl;
  }

  threedDlPayload.disabled = !!r.payload_omitted;
  threedDlPayload.title = r.payload_omitted
    ? "payload più grande del limite di risposta del server: usa la CLI in locale"
    : "";
  threedDlGlb.disabled = !!r.glb_omitted;

  const rows = [
    ["forme uniche", r.shape_count],
    ["riferimenti", r.reference_count],
    ["istanze (posizionamenti)", r.instance_count],
    ["vertici", r.vertex_count.toLocaleString("it-IT")],
    ["errore medio vertici (quantizzazione int16)", r.mean_vertex_error],
    ["payload (BZM1)", fmtBytes(r.payload_bytes)],
    ["entra in un QR code", r.fits_qr ? "sì" : "no"],
  ];
  threedStatsTable.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");

  threedBomTable.innerHTML = r.bom.length
    ? r.bom.map(e =>
        `<tr class="part" data-part-name="${escapeHtml(e.name)}" data-part-count="${e.count}">` +
        `<td>${escapeHtml(e.name)}</td><td>x${e.count}</td></tr>`
      ).join("")
    : "<tr><td>(nessuna parte)</td></tr>";
  threedBomTable.querySelectorAll("tr.part").forEach(row => {
    row.addEventListener("click", () => threedSelectByName(row.dataset.partName));
  });

  threedOriginalColors = null; // new model: cached again on its own 'load' event
  threedSelectedName = null;
  threedSelectedCount = null;
  threedExportBtn.disabled = true;
}

threedDlPayload.addEventListener("click", () => {
  if (!lastThreedResult) return;
  downloadBlob(base64ToBytes(lastThreedResult.payload_base64), "output.b3d", "application/octet-stream");
});

threedDlGlb.addEventListener("click", () => {
  if (!lastThreedResult || lastThreedResult.glb_omitted) return;
  downloadBlob(base64ToBytes(lastThreedResult.glb_base64), "output.glb", "model/gltf-binary");
});

setupQrButton("threed", () => (lastThreedResult && !lastThreedResult.payload_omitted) ? lastThreedResult.payload_base64 : null);

// ------------------------------------------------- apri programma (.bzr/.bzp)

const openDrop = document.getElementById("open-drop");
const openFileInput = document.getElementById("open-file-input");
const openBrowseBtn = document.getElementById("open-browse-btn");
const openStatusEl = document.getElementById("open-status");
const openResultEl = document.getElementById("open-result");
const openImgRendered = document.getElementById("open-img-rendered");
const openStatsTable = document.getElementById("open-stats-table");
const openProgramText = document.getElementById("open-program-text");
const openDlPng = document.getElementById("open-dl-png");
const openDlGif = document.getElementById("open-dl-gif");
const openDlSvg = document.getElementById("open-dl-svg");
const openDlPayload = document.getElementById("open-dl-payload");
const openSvgReason = document.getElementById("open-svg-reason");

let lastOpenResult = null;

openBrowseBtn.addEventListener("click", () => openFileInput.click());
openFileInput.addEventListener("change", () => {
  if (openFileInput.files[0]) handleOpenFile(openFileInput.files[0]);
});
["dragenter", "dragover"].forEach(evt =>
  openDrop.addEventListener(evt, e => { e.preventDefault(); openDrop.classList.add("dragover"); })
);
["dragleave", "drop"].forEach(evt =>
  openDrop.addEventListener(evt, e => { e.preventDefault(); openDrop.classList.remove("dragover"); })
);
openDrop.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  if (file) handleOpenFile(file);
});

function setOpenStatus(msg, isError) {
  openStatusEl.hidden = false;
  openStatusEl.textContent = msg;
  openStatusEl.classList.toggle("error", !!isError);
}

async function handleOpenFile(file) {
  openResultEl.hidden = true;
  setOpenStatus(`Apertura di "${file.name}" in corso…`);
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(file);
    });
    const data = dataUrl.split(",", 2)[1];

    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data }),
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "errore sconosciuto");

    lastOpenResult = json;
    renderOpenResult(json);
    setOpenStatus(`Fatto: ${file.name}`);
  } catch (err) {
    setOpenStatus("Errore: " + err.message, true);
  }
}

function renderOpenResult(r) {
  openImgRendered.src = "data:image/png;base64," + r.preview_png_base64;
  openProgramText.textContent = r.program_text;

  const rows = [
    ["dimensioni", `${r.width}×${r.height} px`],
    ["frame", r.frame_count],
    ["RGB grezzo equivalente", fmtBytes(r.raw_rgb_bytes)],
  ];
  if (r.preview_scaled) rows.push(["anteprima", "ridotta per la visualizzazione"]);
  openStatsTable.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");

  openDlPng.disabled = !!r.png_omitted;
  openDlPng.title = r.png_omitted ? "PNG oltre il limite di risposta del server" : "";

  openDlGif.hidden = r.frame_count <= 1;
  if (r.frame_count > 1) {
    openDlGif.disabled = !!r.gif_omitted;
    openDlGif.title = r.gif_omitted ? "GIF oltre il limite di risposta del server" : "";
  }

  openDlSvg.hidden = !r.svg_available;
  openSvgReason.hidden = r.svg_available;
  if (!r.svg_available) {
    openSvgReason.textContent = "SVG non disponibile: " + r.svg_reason;
  }

  openDlPayload.disabled = !!r.payload_omitted;
  openDlPayload.title = r.payload_omitted
    ? "payload più grande del limite di risposta del server: usa la CLI in locale"
    : "";

  openResultEl.hidden = false;
}

openDlPng.addEventListener("click", () => {
  if (!lastOpenResult || !lastOpenResult.png_base64) return;
  downloadBlob(base64ToBytes(lastOpenResult.png_base64), "rigenerato.png", "image/png");
});
openDlGif.addEventListener("click", () => {
  if (!lastOpenResult || !lastOpenResult.gif_base64) return;
  downloadBlob(base64ToBytes(lastOpenResult.gif_base64), "rigenerato.gif", "image/gif");
});
openDlPayload.addEventListener("click", () => {
  if (!lastOpenResult || !lastOpenResult.payload_base64) return;
  downloadBlob(base64ToBytes(lastOpenResult.payload_base64), "rigenerato.bzp", "application/octet-stream");
});
openDlSvg.addEventListener("click", () => {
  if (!lastOpenResult || !lastOpenResult.svg_text) return;
  downloadBlob(new TextEncoder().encode(lastOpenResult.svg_text), "rigenerato.svg", "image/svg+xml");
});

setupQrButton("open", () => (lastOpenResult && !lastOpenResult.payload_omitted) ? lastOpenResult.payload_base64 : null);
