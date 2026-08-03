"use strict";
// Trasporto QR "a fontana" — secondo canale sperimentale, additivo e
// indipendente dal trasporto QR classico (trasporto-qr.html/
// qr-transport-core.js). Nessun file esistente di balzar viene toccato:
// questa pagina usa solo fountain-protocol.js (porting fedele da
// decimen-optical-transfer, MIT) + qrcode.min.js (generazione, client-side)
// + jsQR.min.js (lettura, già vendorizzato altrove nel progetto). Vedi
// CLAUDE.md §13 per la valutazione completa e i numeri misurati.
//
// Adattato da experiments/fountain-qr-poc/sender.js + receiver.js,
// stessa logica, solo rilegato a questa pagina (nuovi id DOM, un pulsante
// di stop/reset in più per il ricevitore).

function downloadBlob(bytes, filename) {
  const blob = new Blob([bytes], { type: "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// -------------------------------------------------------------- mittente

const MARGIN = 4;
const LOOKAHEAD = 3;
const TX_FPS = 20;
const FRAME_BYTES = 1465; // ~QR v27, margine di sicurezza per schermi comuni

const sendCanvas = document.getElementById("send-canvas");
const sendSpecs = document.getElementById("send-specs");
const sendFileInput = document.getElementById("send-file-input");
const sendBrowseBtn = document.getElementById("send-browse-btn");
const sendFileLabel = document.getElementById("send-file-label");

let sendGeneration = 0;

function demoPayload() {
  // payload di prova deterministico, 50 KB, se l'utente non sceglie un file
  const buf = new Uint8Array(50 * 1024);
  for (let i = 0; i < buf.length; i++) buf[i] = i & 0xff;
  return buf;
}

sendBrowseBtn.addEventListener("click", () => sendFileInput.click());
sendFileInput.addEventListener("change", () => {
  const f = sendFileInput.files[0];
  if (!f) return;
  sendFileLabel.textContent = `${f.name} (${f.size.toLocaleString("it-IT")} byte)`;
  f.arrayBuffer().then((buf) => startStream(new Uint8Array(buf), f.name));
});

function startStream(payload, label) {
  const gen = ++sendGeneration;
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
    const cssBudget = Math.min(0.9 * Math.min(window.innerWidth, 480), 480);
    scale = Math.max(1, Math.floor((cssBudget * dpr) / total));
    staging.width = total;
    staging.height = total;
    sendCanvas.width = total * scale;
    sendCanvas.height = total * scale;
    sendCanvas.style.width = `${(total * scale) / dpr}px`;
    sendCanvas.style.height = `${(total * scale) / dpr}px`;
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
      sendSpecs.textContent = `sorgente: ${label || "payload di prova"} · ${payload.length.toLocaleString("it-IT")} B · ` +
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
    if (gen !== sendGeneration) return;
    while (queue.length < LOOKAHEAD) queue.push(makeFrame());
    setTimeout(pump, 0);
  };
  pump();

  const interval = 1000 / TX_FPS;
  let nextAt = performance.now();
  const tick = (now) => {
    if (gen !== sendGeneration) return;
    requestAnimationFrame(tick);
    if (now < nextAt) return;
    const img = queue.shift();
    if (!img) { nextAt = now + interval; return; }
    staging.getContext("2d").putImageData(img, 0, 0);
    const ctx = sendCanvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(staging, 0, 0, sendCanvas.width, sendCanvas.height);
    nextAt += interval;
    if (now - nextAt > 3 * interval) nextAt = now + interval;
  };
  requestAnimationFrame(tick);
}

startStream(demoPayload(), null);

// -------------------------------------------------------------- ricevitore

const OVERHEAD_EST = 1.18;

const recvStartBtn = document.getElementById("recv-start-btn");
const recvStopBtn = document.getElementById("recv-stop-btn");
const recvResetBtn = document.getElementById("recv-reset-btn");
const recvVideo = document.getElementById("recv-video");
const recvStatus = document.getElementById("recv-status");
const recvBarBg = document.getElementById("recv-bar-bg");
const recvBar = document.getElementById("recv-bar");
const recvResult = document.getElementById("recv-result");

let recvStream = null;
let recvDecoder = null;
let recvSessionId = 0;
let recvStartTs = 0;
let recvDone = false;
let recvFramesCaptured = 0;
let recvFramesDecoded = 0;
let recvStatsTimer = null;

recvStartBtn.addEventListener("click", () => void startReceiving());
recvStopBtn.addEventListener("click", () => stopReceiving("Fotocamera fermata manualmente."));
recvResetBtn.addEventListener("click", resetReceiver);

function resetReceiver() {
  stopReceiving(null);
  recvDone = false;
  recvDecoder = null;
  recvSessionId = 0;
  recvFramesCaptured = 0;
  recvFramesDecoded = 0;
  recvResult.innerHTML = "";
  recvBar.style.width = "0%";
  recvBarBg.hidden = true;
  recvStatus.classList.remove("active");
  recvStatus.textContent = "pronto.";
  recvStartBtn.hidden = false;
  recvStopBtn.hidden = true;
  recvResetBtn.hidden = true;
}

function stopReceiving(message) {
  if (recvStatsTimer) { clearInterval(recvStatsTimer); recvStatsTimer = null; }
  recvStream?.getTracks().forEach((t) => t.stop());
  recvStream = null;
  recvVideo.hidden = true;
  recvStartBtn.hidden = false;
  recvStopBtn.hidden = true;
  if (message) recvStatus.textContent = message;
}

async function startReceiving() {
  if (!navigator.mediaDevices?.getUserMedia) {
    recvStatus.textContent = "Errore: serve https (o localhost) per usare la fotocamera.";
    return;
  }
  recvStartBtn.hidden = true;
  recvVideo.hidden = false;
  recvBarBg.hidden = false;
  recvStatus.classList.add("active");
  try {
    recvStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "environment", width: { ideal: 1280 } },
    });
  } catch (err) {
    recvStatus.textContent = "Errore fotocamera: " + err.message;
    recvStartBtn.hidden = false;
    recvVideo.hidden = true;
    return;
  }
  recvVideo.srcObject = recvStream;
  await recvVideo.play().catch(() => undefined);
  recvStopBtn.hidden = false;
  recvStatus.textContent = "in ricerca del flusso...";

  const grab = document.createElement("canvas");
  const loop = () => {
    if (recvDone || !recvStream) return;
    const vw = recvVideo.videoWidth, vh = recvVideo.videoHeight;
    if (vw && vh) {
      if (grab.width !== vw) { grab.width = vw; grab.height = vh; }
      const ctx = grab.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(recvVideo, 0, 0);
      const img = ctx.getImageData(0, 0, vw, vh);
      recvFramesCaptured++;
      const res = jsQR(img.data, vw, vh);
      if (res) {
        recvFramesDecoded++;
        onDecoded(res.binaryData ? new Uint8Array(res.binaryData) : textToBytes(res.data));
      }
    }
    if (recvVideo.requestVideoFrameCallback) recvVideo.requestVideoFrameCallback(loop);
    else requestAnimationFrame(loop);
  };
  loop();
  recvStatsTimer = setInterval(updateRecvStats, 500);
}

// jsQR restituisce binaryData quando possibile (byte esatti); altrimenti
// reinterpreta la stringa come latin1 (senza perdita per QR in byte-mode,
// un char code = un byte originale 0-255).
function textToBytes(s) {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
  return out;
}

function onDecoded(bytes) {
  const parsed = parseFrame(bytes);
  if (!parsed || recvDone) return;
  const { header, block } = parsed;
  if (!recvDecoder || recvSessionId !== header.sessionId) {
    recvDecoder = new LTDecoder(header.k, header.blockLen, header.sessionId, header.totalLen);
    recvSessionId = header.sessionId;
    recvStartTs = performance.now();
  }
  recvDecoder.addFrame(header.seq, block);
  const progress = Math.min(0.99, recvDecoder.framesNew / (recvDecoder.k * OVERHEAD_EST));
  recvBar.style.width = `${(progress * 100).toFixed(1)}%`;

  if (recvDecoder.isComplete) {
    const payload = recvDecoder.assemble();
    const seconds = (performance.now() - recvStartTs) / 1000;
    const ok = fnv1a(payload) === header.payloadFnv;
    finishReceiving(payload, ok, seconds, header.totalLen);
  }
}

function finishReceiving(payload, hashOk, seconds, totalLen) {
  recvDone = true;
  if (recvStatsTimer) { clearInterval(recvStatsTimer); recvStatsTimer = null; }
  recvStream?.getTracks().forEach((t) => t.stop());
  recvStream = null;
  recvVideo.hidden = true;
  recvStopBtn.hidden = true;
  recvResetBtn.hidden = false;
  recvBar.style.width = "100%";
  const kb = Math.round(totalLen / 1024);
  const rate = (totalLen / 1024 / seconds).toFixed(1);
  recvStatus.textContent = `${kb} KB in ${seconds.toFixed(1)} s · ${rate} KB/s · ` +
    `${recvFramesDecoded}/${recvFramesCaptured} fotogrammi letti · hash ${hashOk ? "verificato ✓" : "NON CORRISPONDE ✗"}`;
  const heading = document.createElement("div");
  heading.className = "done";
  heading.textContent = "Trasferimento completo!";
  recvResult.appendChild(heading);
  const dlBtn = document.createElement("button");
  dlBtn.type = "button";
  dlBtn.textContent = "Scarica il file ricevuto";
  dlBtn.addEventListener("click", () => downloadBlob(payload, "ricevuto.bin"));
  recvResult.appendChild(dlBtn);
}

function updateRecvStats() {
  if (recvDone || !recvDecoder) return;
  const elapsed = (performance.now() - recvStartTs) / 1000;
  const kbs = (recvDecoder.framesNew * recvDecoder.blockLen) / OVERHEAD_EST / 1024 / Math.max(0.1, elapsed);
  recvStatus.textContent = `K=${recvDecoder.k} · fotogrammi nuovi/duplicati: ${recvDecoder.framesNew}/${recvDecoder.framesDup} · ` +
    `~${kbs.toFixed(1)} KB/s · ${elapsed.toFixed(0)} s`;
}
