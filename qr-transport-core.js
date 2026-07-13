"use strict";
// Shared QR transport core -- decoding side of the BZC1 chunk format
// (balzar/payload.py) plus the grid-crop geometry (_tile_boxes in
// balzar/qr.py), ported to JS once and reused by every page/component
// that needs to turn a QR image (or a live camera frame) into balzar
// chunk bytes: trasporto-qr.js (arbitrary raw-file transport, byte
// download) and the continuous-camera scanner (feeds Balzar Live's
// magic-byte dispatch instead). Extracted unchanged from trasporto-qr.js
// (CLAUDE.md §2.4d) -- no behavior change, just a shared home so a third
// copy of this logic is never written.
//
// Decoding is via jsQR (vendorized, jsQR.min.js), chosen over the
// actively-maintained-looking @paulmillr/qr after a real, reproducible
// measurement: on a real generated 2x2 grid (24 chunks, PDF payload),
// @paulmillr/qr's decodeQR threw an internal error on 3/24 otherwise-
// valid QR crops (a bug in its finder-pattern transform -- the same
// crop decodes fine via pyzbar/ZBar, so not a crop-geometry issue).
// jsQR decoded all 24/24 on the same images. See CLAUDE.md §2.4d for
// the full writeup.

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
//
// `rows` is NOT assumed equal to gridDim (real bug, found and fixed on
// the Python side in the same session, CLAUDE.md §2.4f): _compose_grid's
// `top` is a fixed constant (26 with a frame label, 0 without), never
// derived from row count -- a partial LAST frame almost always has
// fewer rows than a full one even when its column count still equals
// gridDim (e.g. 12 remaining codes at gridDim=4 lays out as 4 cols x 3
// rows). `rows` is instead solved from the known image height, trying
// both possible `top` values and keeping whichever one reconstructs the
// given height exactly.
//
// `cols` is NOT assumed equal to gridDim either (a second real bug,
// found from a user report of total non-detection on a partial matrix,
// not a hypothetical): a last frame with few enough remaining chunks
// lays out at cols=ceil(sqrt(n)), which drops BELOW gridDim once
// n <= (gridDim-1)**2 (e.g. 8 codes at gridDim=4 is 3x3, not 4x4). The
// old code assumed cols=gridDim unconditionally and correctly failed
// closed (0 boxes) when no `top` could reconstruct the height under
// that wrong assumption -- but decodeAllViaMasking (this file's
// whole-image fallback) ALSO came up empty on that same dense grid, so
// the failure was total, not just a lost speedup. Fixed by also
// searching `cols` from gridDim down to 1 (gridDim tried first, by far
// the common case), keeping the first (cols, top) whose solved
// cell/pad/rows reconstructs both width and height.

function tileBoxes(width, height, gridDim) {
  const labelH = 22;
  const slack = 3;

  for (let cols = gridDim; cols >= 1; cols--) {
    let cell = (width * 15) / (16 * cols + 1);
    let pad = 12;
    for (let i = 0; i < 4; i++) {
      pad = Math.max(12, Math.floor(Math.floor(cell) / 15));
      cell = (width - pad * (cols + 1)) / cols;
    }
    cell = Math.round(cell);
    pad = Math.max(12, Math.floor(cell / 15));
    if (cell < 20) continue;

    const rowH = cell + pad + labelH;
    for (const top of [26, 0]) {
      const rows = Math.round((height - top - pad) / rowH);
      if (rows < 1) continue;
      // Real bug found and fixed on the Python side too (balzar/qr.py's
      // _tile_boxes): this used to compare against rowH/2 (hundreds of
      // pixels), so the WRONG top=26 hypothesis (tried first) could get
      // accepted whenever it happened to reconstruct the height within
      // that huge margin -- e.g. a real single-frame 2x2 grid (top=0)
      // whose height was only 26px off under the top=26 guess. Every crop
      // then landed ~26px off from the real cells, and jsQR's whole-image
      // fallback isn't reliable on a multi-code grid either, so decode
      // failed completely, not just slower. When the hypothesis is
      // actually right this reconstructs EXACTLY (cell/pad/rows are the
      // same integers _compose_grid itself used) -- a couple of pixels of
      // slack covers any real rounding, nothing close to rowH/2.
      if (Math.abs(top + rows * rowH + pad - height) > 2) continue;
      const boxes = [];
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const x = pad + c * (cell + pad);
          const y = top + pad + r * rowH;
          const left = Math.max(0, x - slack), upper = Math.max(0, y - slack);
          const right = Math.min(width, x + cell + slack), lower = Math.min(height, y + cell + slack);
          if (left < right && upper < lower) boxes.push([left, upper, right, lower]);
        }
      }
      if (boxes.length) return boxes;
    }
  }
  return [];
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

// decode EVERY QR code in one image, regardless of layout. jsQR finds
// only one code per call (unlike ZBar's native multi-decode, which the
// Python side's whole-image fallback relies on) -- decodeAllViaMasking
// below is the JS equivalent: decode, white out the found code's
// bounding box in a working copy, repeat until nothing more is found.
//
// gridDim tiling (tileBoxes) is tried FIRST as a speed hint, same
// principle as balzar/qr.py's own grid_dim hint on the Python side.
// tileBoxes now solves the REAL row count for the frame's actual layout
// (see tileBoxes for the bug this fixed -- a partial last frame used to
// silently decode 0 codes).
//
// gridDim is optional: omitted (or falsy), it defaults to 8 (the same
// _AUTO_GRID_DIM_CEILING as the Python side) -- tileBoxes already
// searches cols downward from whatever ceiling it's given until the
// real layout reconstructs exactly, so passing the maximum plausible
// ceiling auto-detects the true layout regardless of what gridDim the
// sequence was actually generated with. The caller no longer needs to
// know or match it. Pass gridDim=1 explicitly to skip the tiling
// attempt entirely (used by ContinuousQrScanner's tight capture loop,
// which already knows every frame is a single ungridded code -- no
// benefit from searching for an answer it already has).
//
// Unlike the Python side, an incomplete tiled result here is NOT
// discarded: jsQR occasionally fails to decode one otherwise-valid crop
// even with correct geometry (measured: 11/12 on a real partial-frame
// image, a per-crop jsQR reliability gap, not a positioning bug -- and
// jsQR alone finds 0/16 codes scanning the same image whole, so a
// Python-style "discard everything unless perfect, then fall back to a
// whole-image scan" would throw away 11 good decodes to gain nothing).
// The tiled texts found are always kept; decodeAllViaMasking is run in
// addition (not instead) whenever the tiled pass wasn't 100% complete,
// to recover anything tiling missed -- this is also what keeps a wrong
// geometry guess (the auto-ceiling search hitting a coincidental match,
// same real risk already found and handled on the Python side) safe:
// a mis-cropped region essentially never contains a real QR finder
// pattern, so tiledTexts stays empty/incomplete and the whole-image
// fallback still runs. Chunk identity is self-describing (BZC1's own
// index/crc, see LiveScanner), so accumulating a genuinely partial
// result from one image and completing it from a later photo/frame is
// already how this format is meant to be used -- one frame missing a
// single code is not a failure, it's the same "add another photo" flow
// already exposed to the operator elsewhere.
function decodeAllInImage(imgData, gridDim) {
  const effectiveGridDim = gridDim || 8;
  const seen = new Set();
  const texts = [];
  const addUnique = (found) => {
    for (const t of found) {
      if (!seen.has(t)) { seen.add(t); texts.push(t); }
    }
  };

  let tiledComplete = false;
  if (effectiveGridDim > 1) {
    const boxes = tileBoxes(imgData.width, imgData.height, effectiveGridDim);
    const tiledTexts = [];
    for (const box of boxes) {
      const region = cropImageData(imgData, box);
      const result = jsQR(region.data, region.width, region.height);
      if (result) tiledTexts.push(result.data);
    }
    addUnique(tiledTexts);
    tiledComplete = boxes.length > 0 && tiledTexts.length === boxes.length;
  }
  if (!tiledComplete) addUnique(decodeAllViaMasking(imgData));
  return texts;
}

function decodeAllViaMasking(imgData) {
  // working copy -- never mutate the caller's ImageData
  const data = new Uint8ClampedArray(imgData.data);
  const width = imgData.width, height = imgData.height;
  const texts = [];
  const MAX_CODES = 256; // hard guard: never loop forever if a decode's
                        // bounding box somehow fails to mask out the code
  for (let i = 0; i < MAX_CODES; i++) {
    const result = jsQR(data, width, height);
    if (!result) break;
    texts.push(result.data);
    const corners = [result.location.topLeftCorner, result.location.topRightCorner,
                     result.location.bottomLeftCorner, result.location.bottomRightCorner];
    const margin = 4;
    const left = Math.max(0, Math.floor(Math.min(...corners.map(c => c.x))) - margin);
    const right = Math.min(width, Math.ceil(Math.max(...corners.map(c => c.x))) + margin);
    const top = Math.max(0, Math.floor(Math.min(...corners.map(c => c.y))) - margin);
    const bottom = Math.min(height, Math.ceil(Math.max(...corners.map(c => c.y))) + margin);
    // white-out (not black): matches the pure-white background between
    // QR codes in a balzar-generated grid image (_compose_grid uses a
    // white canvas), so the masked region reads as empty space rather
    // than risking a dark rectangle that could itself look like part of
    // a finder pattern to the next decode attempt
    for (let y = top; y < bottom; y++) {
      const rowStart = (y * width + left) * 4;
      data.fill(255, rowStart, rowStart + (right - left) * 4);
    }
  }
  return texts;
}
