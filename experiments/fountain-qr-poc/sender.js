"use strict";
// Mittente: legge un file (o usa un payload di prova) e lo trasmette come
// flusso infinito di QR fountain-coded. Adattato da
// https://github.com/bashalarmistalt/decimen-optical-transfer (MIT),
// send/main.ts -> JS puro, stessa logica.

const MARGIN = 4;
const LOOKAHEAD = 3;
const TX_FPS = 20;
const FRAME_BYTES = 1465; // ~QR v27, margine di sicurezza per schermi comuni

const canvas = document.getElementById("canvas");
const specs = document.getElementById("specs");
const fileInput = document.getElementById("file-input");
const fileLabel = document.getElementById("file-label");

let generation = 0;

function demoPayload() {
  // payload di prova deterministico, 50 KB, se l'utente non sceglie un file
  const buf = new Uint8Array(50 * 1024);
  for (let i = 0; i < buf.length; i++) buf[i] = i & 0xff;
  return buf;
}

fileInput.addEventListener("change", () => {
  const f = fileInput.files[0];
  if (!f) return;
  fileLabel.textContent = `${f.name} (${f.size.toLocaleString("it-IT")} byte)`;
  f.arrayBuffer().then((buf) => startStream(new Uint8Array(buf), f.name));
});

function startStream(payload, label) {
  const gen = ++generation;
  const sessionId = (Math.floor(Math.random() * 0xffff) + 1) & 0xffff;
  const blockLen = FRAME_BYTES - HEADER_LEN;
  const encoder = new LTEncoder(payload, blockLen, sessionId);
  const header = {
    sessionId,
    seq: 0,
    k: encoder.k,
    blockLen,
    totalLen: payload.length,
    payloadFnv: fnv1a(payload),
  };

  let version;
  let modules = 0;
  let scale = 1;
  const staging = document.createElement("canvas");
  const queue = [];
  let nextSeq = 0;

  const sizeCanvas = () => {
    const dpr = window.devicePixelRatio || 1;
    const total = modules + 2 * MARGIN;
    const cssBudget = Math.min(0.9 * Math.min(window.innerWidth, window.innerHeight), 640);
    scale = Math.max(1, Math.floor((cssBudget * dpr) / total));
    staging.width = total;
    staging.height = total;
    canvas.width = total * scale;
    canvas.height = total * scale;
    canvas.style.width = `${(total * scale) / dpr}px`;
    canvas.style.height = `${(total * scale) / dpr}px`;
  };

  const makeFrame = () => {
    const bytes = packFrame({ ...header, seq: nextSeq }, encoder.encode(nextSeq));
    nextSeq++;
    const qr = QRCode.create([{ data: bytes, mode: "byte" }], {
      errorCorrectionLevel: "L",
      version,
      maskPattern: 4,
    });
    if (version === undefined) {
      version = qr.version;
      modules = qr.modules.size;
      sizeCanvas();
      specs.textContent = `sorgente: ${label || "payload di prova"} · ${payload.length.toLocaleString("it-IT")} B · ` +
        `${TX_FPS} fps · ${FRAME_BYTES} B/fotogramma · QR v${version} · K=${encoder.k} capitoli`;
    }
    const size = qr.modules.size;
    const data = qr.modules.data;
    const total = size + 2 * MARGIN;
    const img = new ImageData(total, total);
    const px = new Uint32Array(img.data.buffer);
    px.fill(0xffffffff);
    for (let y = 0; y < size; y++) {
      const row = (y + MARGIN) * total + MARGIN;
      const src = y * size;
      for (let x = 0; x < size; x++) {
        if (data[src + x]) px[row + x] = 0xff000000;
      }
    }
    return img;
  };

  const pump = () => {
    if (gen !== generation) return;
    while (queue.length < LOOKAHEAD) queue.push(makeFrame());
    setTimeout(pump, 0);
  };
  pump();

  const interval = 1000 / TX_FPS;
  let nextAt = performance.now();
  const tick = (now) => {
    if (gen !== generation) return;
    requestAnimationFrame(tick);
    if (now < nextAt) return;
    const img = queue.shift();
    if (!img) { nextAt = now + interval; return; }
    staging.getContext("2d").putImageData(img, 0, 0);
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(staging, 0, 0, canvas.width, canvas.height);
    nextAt += interval;
    if (now - nextAt > 3 * interval) nextAt = now + interval;
  };
  requestAnimationFrame(tick);
}

startStream(demoPayload(), null);
