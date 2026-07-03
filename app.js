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

async function handleFile(file) {
  resultEl.hidden = true;
  setStatus(`Analisi di "${file.name}" in corso…`);
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = reject;
      r.readAsDataURL(file);
    });
    imgOriginal.src = dataUrl;

    const data = dataUrl.split(",", 2)[1];
    const maxDim = parseInt(maxDimSelect.value, 10);

    const res = await fetch("/api/encode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data, max_dim: maxDim }),
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
