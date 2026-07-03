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
    ["fedeltà", r.lossless ? "esatta (lossless)" : "quantizzata a 256 colori fissi (lossy)"],
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

dlPayloadBtn.addEventListener("click", () => {
  if (!lastResult) return;
  downloadBlob(base64ToBytes(lastResult.payload_base64), "output.bzp", "application/octet-stream");
});

dlProgramBtn.addEventListener("click", () => {
  if (!lastResult) return;
  downloadBlob(new TextEncoder().encode(lastResult.program_text), "output.bzr", "text/plain");
});

// ---------------------------------------------------------- tabs

const tabEncode = document.getElementById("tab-encode");
const tabOpen = document.getElementById("tab-open");
const panelEncode = document.getElementById("panel-encode");
const panelOpen = document.getElementById("panel-open");

function activateTab(tab) {
  const isEncode = tab === "encode";
  tabEncode.classList.toggle("active", isEncode);
  tabOpen.classList.toggle("active", !isEncode);
  panelEncode.hidden = !isEncode;
  panelOpen.hidden = isEncode;
}
tabEncode.addEventListener("click", () => activateTab("encode"));
tabOpen.addEventListener("click", () => activateTab("open"));

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
openDlSvg.addEventListener("click", () => {
  if (!lastOpenResult || !lastOpenResult.svg_text) return;
  downloadBlob(new TextEncoder().encode(lastOpenResult.svg_text), "rigenerato.svg", "image/svg+xml");
});
