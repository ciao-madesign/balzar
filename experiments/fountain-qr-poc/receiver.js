"use strict";
// Ricevitore: fotocamera -> jsQR -> decoder fountain -> file. Adattato da
// https://github.com/bashalarmistalt/decimen-optical-transfer (MIT),
// receive/main.ts, semplificato (un solo thread di decodifica, niente
// worker pool -- sufficiente per un primo test con hardware reale).

const OVERHEAD_EST = 1.18;

const startBtn = document.getElementById("start");
const video = document.getElementById("video");
const stats = document.getElementById("stats");
const barBg = document.getElementById("bar-bg");
const bar = document.getElementById("bar");
const result = document.getElementById("result");

let stream = null;
let decoder = null;
let sessionId = 0;
let startTs = 0;
let done = false;
let framesCaptured = 0;
let framesDecoded = 0;

startBtn.onclick = () => void start();

async function start() {
  if (!navigator.mediaDevices?.getUserMedia) {
    stats.textContent = "Errore: serve https (o localhost) per usare la fotocamera.";
    return;
  }
  startBtn.style.display = "none";
  video.style.display = "block";
  barBg.style.display = "block";
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "environment", width: { ideal: 1280 } },
    });
  } catch (err) {
    stats.textContent = "Errore fotocamera: " + err.message;
    return;
  }
  video.srcObject = stream;
  await video.play().catch(() => undefined);
  stats.textContent = "in ricerca del flusso...";

  const grab = document.createElement("canvas");
  const loop = () => {
    if (done) return;
    const vw = video.videoWidth, vh = video.videoHeight;
    if (vw && vh) {
      if (grab.width !== vw) { grab.width = vw; grab.height = vh; }
      const ctx = grab.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(video, 0, 0);
      const img = ctx.getImageData(0, 0, vw, vh);
      framesCaptured++;
      const res = jsQR(img.data, vw, vh);
      if (res) {
        framesDecoded++;
        onDecoded(res.binaryData ? new Uint8Array(res.binaryData) : textToBytes(res.data));
      }
    }
    if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(loop);
    else requestAnimationFrame(loop);
  };
  loop();
  setInterval(updateStats, 500);
}

// jsQR gives back a JS string for byte-mode QR content by default in some
// builds; when it exposes binaryData use that directly (exact bytes), else
// fall back to reinterpreting the string as latin1 bytes (loss-free for
// byte-mode QR, since each char code is one original byte 0-255).
function textToBytes(s) {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
  return out;
}

function onDecoded(bytes) {
  const parsed = parseFrame(bytes);
  if (!parsed || done) return;
  const { header, block } = parsed;
  if (!decoder || sessionId !== header.sessionId) {
    decoder = new LTDecoder(header.k, header.blockLen, header.sessionId, header.totalLen);
    sessionId = header.sessionId;
    startTs = performance.now();
  }
  decoder.addFrame(header.seq, block);
  const progress = Math.min(0.99, decoder.framesNew / (decoder.k * OVERHEAD_EST));
  bar.style.width = `${(progress * 100).toFixed(1)}%`;

  if (decoder.isComplete) {
    const payload = decoder.assemble();
    const seconds = (performance.now() - startTs) / 1000;
    const ok = fnv1a(payload) === header.payloadFnv;
    finish(payload, ok, seconds, header.totalLen);
  }
}

function finish(payload, hashOk, seconds, totalLen) {
  done = true;
  stream?.getTracks().forEach((t) => t.stop());
  video.style.display = "none";
  bar.style.width = "100%";
  const kb = Math.round(totalLen / 1024);
  const rate = (totalLen / 1024 / seconds).toFixed(1);
  stats.textContent = `${kb} KB in ${seconds.toFixed(1)} s · ${rate} KB/s · ` +
    `${framesDecoded}/${framesCaptured} fotogrammi letti · hash ${hashOk ? "verificato ✓" : "NON CORRISPONDE ✗"}`;
  const heading = document.createElement("div");
  heading.className = "done";
  heading.textContent = "Trasferimento completo!";
  result.appendChild(heading);
  const dlBtn = document.createElement("button");
  dlBtn.textContent = "Scarica il file ricevuto";
  dlBtn.onclick = () => {
    const blob = new Blob([payload]);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "ricevuto.bin";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  result.appendChild(dlBtn);
}

function updateStats() {
  if (done || !decoder) return;
  const elapsed = (performance.now() - startTs) / 1000;
  const kbs = (decoder.framesNew * decoder.blockLen) / OVERHEAD_EST / 1024 / Math.max(0.1, elapsed);
  stats.textContent = `K=${decoder.k} · fotogrammi nuovi/duplicati: ${decoder.framesNew}/${decoder.framesDup} · ` +
    `~${kbs.toFixed(1)} KB/s · ${elapsed.toFixed(0)} s`;
}
