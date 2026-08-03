// Fountain (Luby Transform) coding + self-describing frame header.
//
// Ported to plain JS from https://github.com/bashalarmistalt/decimen-optical-transfer
// (MIT license), protocol.ts + fountain.ts, with only the TypeScript type
// annotations removed — the algorithm is unchanged. See that repo's
// README for the "why" behind fountain coding on a one-way optical
// channel (screen -> camera, no back-channel, no retransmission).
//
// This file has NO dependency on the balzar codebase and does not touch
// any existing balzar file — it is a self-contained prototype living in
// experiments/fountain-qr-poc/.

// ---------------------------------------------------------------- frame header
// 20-byte little-endian header, followed by blockLen payload bytes:
//   0  u8   magic 0xD1
//   1  u8   magic 0x0C
//   2  u16  sessionId
//   4  u32  seq
//   8  u16  k            source block count
//  10  u16  blockLen
//  12  u32  totalLen     file length in bytes
//  16  u32  payloadFnv   FNV-1a of the whole file

const HEADER_LEN = 20;
const MAGIC0 = 0xd1;
const MAGIC1 = 0x0c;

function packFrame(h, block) {
  const out = new Uint8Array(HEADER_LEN + block.length);
  const dv = new DataView(out.buffer);
  dv.setUint8(0, MAGIC0);
  dv.setUint8(1, MAGIC1);
  dv.setUint16(2, h.sessionId, true);
  dv.setUint32(4, h.seq, true);
  dv.setUint16(8, h.k, true);
  dv.setUint16(10, h.blockLen, true);
  dv.setUint32(12, h.totalLen, true);
  dv.setUint32(16, h.payloadFnv, true);
  out.set(block, HEADER_LEN);
  return out;
}

function parseFrame(bytes) {
  if (bytes.length <= HEADER_LEN) return null;
  if (bytes[0] !== MAGIC0 || bytes[1] !== MAGIC1) return null;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const header = {
    sessionId: dv.getUint16(2, true),
    seq: dv.getUint32(4, true),
    k: dv.getUint16(8, true),
    blockLen: dv.getUint16(10, true),
    totalLen: dv.getUint32(12, true),
    payloadFnv: dv.getUint32(16, true),
  };
  if (header.k === 0 || header.blockLen === 0 || header.totalLen === 0) return null;
  if (bytes.length !== HEADER_LEN + header.blockLen) return null;
  return { header, block: bytes.subarray(HEADER_LEN) };
}

function fnv1a(bytes) {
  let h = 0x811c9dc5;
  for (let i = 0; i < bytes.length; i++) {
    h ^= bytes[i];
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** splitmix32 -- deterministic across JS engines (integer ops only). */
function splitmix32(seed) {
  let s = seed | 0;
  return () => {
    s = (s + 0x9e3779b9) | 0;
    let t = s ^ (s >>> 16);
    t = Math.imul(t, 0x21f0aaad);
    t ^= t >>> 15;
    t = Math.imul(t, 0x735a2d97);
    t ^= t >>> 15;
    return t >>> 0;
  };
}

// ---------------------------------------------------------------- LT fountain code

const LN2 = 0.6931471805599453;

/** Deterministic natural log: exact-ops range reduction + atanh series --
 * Math.log is implementation-approximated and can desync sender/receiver
 * degree distributions across JS engines (V8 vs JavaScriptCore). */
function dlog(x) {
  let e = 0;
  let m = x;
  while (m >= 1.5) { m /= 2; e++; }
  while (m < 0.75) { m *= 2; e--; }
  const z = (m - 1) / (m + 1);
  const z2 = z * z;
  let term = z;
  let sum = 0;
  for (let n = 1; n <= 21; n += 2) {
    sum += term / n;
    term *= z2;
  }
  return e * LN2 + 2 * sum;
}

const SOLITON_C = 0.1;
const SOLITON_DELTA = 0.5;

/** Robust-soliton degree CDF for k source blocks. */
function solitonCdf(k) {
  const cdf = new Float64Array(k);
  if (k === 1) { cdf[0] = 1; return cdf; }
  const R = Math.max(1, SOLITON_C * dlog(k / SOLITON_DELTA) * Math.sqrt(k));
  const spike = Math.min(k, Math.ceil(k / R));
  let total = 0;
  for (let d = 1; d <= k; d++) {
    const rho = d === 1 ? 1 / k : 1 / (d * (d - 1));
    let tau = 0;
    if (d < spike) tau = R / (d * k);
    else if (d === spike) tau = (R * Math.max(0, dlog(R / SOLITON_DELTA))) / k;
    total += rho + tau;
    cdf[d - 1] = total;
  }
  for (let i = 0; i < k; i++) cdf[i] = cdf[i] / total;
  cdf[k - 1] = 1;
  return cdf;
}

function frameSeed(sessionId, seq) {
  let h = (Math.imul(sessionId + 1, 0x9e3779b1) ^ (seq + 0x85ebca6b)) | 0;
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return (h ^ (h >>> 16)) | 0;
}

/** The block indices XORed into frame `seq` -- identical on both ends. */
function frameIndices(k, cdf, sessionId, seq) {
  const rnd = splitmix32(frameSeed(sessionId, seq));
  const u = rnd() * 2 ** -32;
  let lo = 0;
  let hi = k - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (cdf[mid] >= u) hi = mid;
    else lo = mid + 1;
  }
  const d = Math.min(k, lo + 1);
  if (d > k >> 3) {
    const scratch = new Uint32Array(k);
    for (let i = 0; i < k; i++) scratch[i] = i;
    const out = new Array(d);
    for (let i = 0; i < d; i++) {
      const j = i + (rnd() % (k - i));
      const t = scratch[i];
      scratch[i] = scratch[j];
      scratch[j] = t;
      out[i] = scratch[i];
    }
    return out;
  }
  const set = new Set();
  while (set.size < d) set.add(rnd() % k);
  return [...set];
}

function xorInto(dst, src) {
  for (let i = 0; i < dst.length; i++) dst[i] = (dst[i] ^ src[i]) >>> 0;
}

class LTEncoder {
  constructor(payload, blockLen, sessionId) {
    this.blockLen = blockLen;
    this.sessionId = sessionId;
    this.k = Math.max(1, Math.ceil(payload.length / blockLen));
    this.words = Math.ceil(blockLen / 4);
    this.blocks = new Uint32Array(this.k * this.words);
    const bytes = new Uint8Array(this.blocks.buffer);
    for (let b = 0; b < this.k; b++) {
      const src = payload.subarray(b * blockLen, Math.min((b + 1) * blockLen, payload.length));
      bytes.set(src, b * this.words * 4);
    }
    this.cdf = solitonCdf(this.k);
  }

  encode(seq) {
    const idx = frameIndices(this.k, this.cdf, this.sessionId, seq);
    const out = new Uint32Array(this.words);
    for (const b of idx) {
      const off = b * this.words;
      for (let w = 0; w < this.words; w++) out[w] = (out[w] ^ this.blocks[off + w]) >>> 0;
    }
    return new Uint8Array(out.buffer, 0, this.blockLen);
  }
}

class LTDecoder {
  constructor(k, blockLen, sessionId, totalLen) {
    this.k = k;
    this.blockLen = blockLen;
    this.sessionId = sessionId;
    this.totalLen = totalLen;
    this.words = Math.ceil(blockLen / 4);
    this.cdf = solitonCdf(k);
    this.solved = new Array(k).fill(null);
    this.byBlock = new Map();
    this.seen = new Set();
    this.solvedCount = 0;
    this.framesNew = 0;
    this.framesDup = 0;
  }

  get isComplete() {
    return this.solvedCount >= this.k;
  }

  addFrame(seq, block) {
    if (this.seen.has(seq)) { this.framesDup++; return; }
    this.seen.add(seq);
    this.framesNew++;
    if (this.isComplete) return;

    const idx = new Set(frameIndices(this.k, this.cdf, this.sessionId, seq));
    const words = new Uint32Array(this.words);
    new Uint8Array(words.buffer).set(block.subarray(0, this.blockLen));
    for (const b of [...idx]) {
      const s = this.solved[b];
      if (s) { xorInto(words, s); idx.delete(b); }
    }
    if (idx.size === 0) return;
    if (idx.size === 1) { this.resolve(idx.values().next().value, words); return; }
    const pf = { idx, words };
    for (const b of idx) {
      let set = this.byBlock.get(b);
      if (!set) { set = new Set(); this.byBlock.set(b, set); }
      set.add(pf);
    }
  }

  resolve(b0, w0) {
    const queue = [[b0, w0]];
    while (queue.length > 0) {
      const [b, w] = queue.pop();
      if (this.solved[b]) continue;
      this.solved[b] = w;
      this.solvedCount++;
      const waiting = this.byBlock.get(b);
      if (!waiting) continue;
      this.byBlock.delete(b);
      for (const pf of waiting) {
        xorInto(pf.words, w);
        pf.idx.delete(b);
        if (pf.idx.size === 1) {
          const r = pf.idx.values().next().value;
          this.byBlock.get(r)?.delete(pf);
          if (!this.solved[r]) queue.push([r, pf.words]);
        }
      }
    }
  }

  assemble() {
    if (!this.isComplete) return null;
    const out = new Uint8Array(this.totalLen);
    for (let b = 0; b < this.k; b++) {
      const start = b * this.blockLen;
      const len = Math.min(this.blockLen, this.totalLen - start);
      if (len > 0) out.set(new Uint8Array(this.solved[b].buffer, 0, len), start);
    }
    return out;
  }
}
