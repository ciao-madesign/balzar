"use strict";
// Continuous camera-based QR capture -- points a live getUserMedia stream
// at decodeAllInImage/LiveScanner (qr-transport-core.js) so an operator
// can scan a multi-frame QR sequence (printed pages panned in front of
// the camera, or a screen auto-cycling through frames) with ZERO per-photo
// taps. Explicitly chosen over "tap to advance/capture" after discussion:
// with frames cycling every ~1.5s on an auto-playing screen, expecting an
// operator to tap in sync is bad UX -- continuous acquisition means the
// operator just holds the camera roughly steady/pointed at the source and
// the scan completes itself the moment every chunk has been seen in ANY
// frame, in any order, from any single decode attempt.
//
// Reuses qr-transport-core.js's LiveScanner/decodeAllInImage UNCHANGED --
// this file only adds the camera plumbing (getUserMedia, a throttled
// capture loop, progress reporting). No chunk parsing or QR decoding is
// reimplemented here.
//
// Throttling, not a fixed frame rate: jsQR decode (tiled crops +
// decodeAllViaMasking fallback) is synchronous JS work that can take
// anywhere from a few ms to a few hundred ms depending on image size and
// how many codes are found. Driving decode attempts off requestAnimationFrame
// directly (as fast as the display refreshes, ~60/s) would pile up
// overlapping decode calls and starve the browser's render/input loop. A
// `busy` guard plus a minimum-interval throttle (default 350ms, i.e. up to
// ~2.9 decode attempts/s) keeps the UI responsive while still sampling
// often enough to catch a screen cycling frames every ~1.5s multiple times.
//
// gridDim=1 is the ONLY grid_dim this class is realistically usable with,
// and callers generating the QR sequence for continuous camera capture
// MUST use grid_dim=1 -- this is a real, measured constraint, not a
// conservative default. jsQR needs roughly 700-1100px of PIXEL WIDTH per
// individual QR code to decode reliably (non-monotonic near ~800-1000px:
// specific sizes in that band can fail while both smaller and larger
// sizes succeed, a resize/antialiasing artifact, not a smooth falloff).
// A live camera pointed at a screen/page from a normal working distance
// delivers nowhere near the ~3800-4700px of frame width a grid_dim=4
// grid needs for every one of its 16 codes to individually clear that
// threshold (that number IS achievable for a static, deliberately-framed
// PHOTO -- see balzar/qr.py's own grid_dim=4 default for the desktop
// photo-scan/print use case -- but not for a live, continuously-panning
// capture). Measured end to end with a real getUserMedia() call (fed by
// Chromium's fake-video-capture device pointed at a real grid_dim=1 QR
// sequence, not a mock): a screen cycling 5 pages at 1.5s/page, 1920x1080
// capture, scanned start to finish in ~6.3s across 3 repeated runs, every
// one of 20 decode attempts finding exactly 1 QR code, zero errors,
// bit-identical reassembly. See CLAUDE.md §2.4g for the full calibration
// writeup, including the grid_dim=2/4 resolution sweeps that ruled them
// out for this delivery mode.

class ContinuousQrScanner {
  constructor(opts) {
    if (!opts || !opts.video) throw new Error("ContinuousQrScanner: opts.video (un elemento <video>) e' obbligatorio");
    if (!opts.gridDim) throw new Error("ContinuousQrScanner: opts.gridDim e' obbligatorio");
    if (typeof opts.onComplete !== "function") throw new Error("ContinuousQrScanner: opts.onComplete e' obbligatorio");

    this.video = opts.video;
    this.gridDim = opts.gridDim;
    this.intervalMs = opts.intervalMs || 350;
    this.onProgress = opts.onProgress || (() => {});
    this.onComplete = opts.onComplete;
    this.onError = opts.onError || (() => {});
    this.onFrameSample = opts.onFrameSample || (() => {}); // (foundCount) => void, for a "codici visti in quest'inquadratura" indicator

    this.scanner = new LiveScanner();
    this._stream = null;
    this._canvas = document.createElement("canvas");
    this._ctx = this._canvas.getContext("2d", { willReadFrequently: true });
    this._busy = false;
    this._lastAttemptAt = 0;
    this._rafId = null;
    this._running = false;
    this._completed = false;
  }

  // status of the underlying LiveScanner, for a caller that wants to
  // render progress before the first onProgress callback fires (e.g. to
  // show "0/N" immediately after start() resolves, before any frame has
  // been decoded yet -- total is unknown until the first chunk is seen).
  status() {
    return this.scanner.status();
  }

  async start() {
    if (this._running) return;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      });
    } catch (e) {
      // Surface the real browser error (permission denied, no camera
      // device, constraints not satisfiable) instead of a generic
      // message -- each has a different fix on the operator's side.
      this.onError(e);
      throw e;
    }
    this._stream = stream;
    this.video.srcObject = stream;
    await this.video.play();
    this._running = true;
    this._loop();
  }

  stop() {
    this._running = false;
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    if (this._stream) {
      for (const track of this._stream.getTracks()) track.stop();
      this._stream = null;
    }
    if (this.video) this.video.srcObject = null;
  }

  _loop() {
    if (!this._running) return;
    this._rafId = requestAnimationFrame(() => this._tick());
  }

  _tick() {
    if (!this._running) return;
    const now = performance.now();
    if (!this._busy && now - this._lastAttemptAt >= this.intervalMs
        && this.video.videoWidth > 0 && this.video.videoHeight > 0) {
      this._lastAttemptAt = now;
      this._attemptDecode();
    }
    this._loop();
  }

  _attemptDecode() {
    this._busy = true;
    try {
      const w = this.video.videoWidth, h = this.video.videoHeight;
      this._canvas.width = w;
      this._canvas.height = h;
      this._ctx.drawImage(this.video, 0, 0, w, h);
      const imgData = this._ctx.getImageData(0, 0, w, h);
      const texts = decodeAllInImage(imgData, this.gridDim);
      this.onFrameSample(texts.length);

      let anyAdded = false;
      for (const t of texts) {
        const res = this.scanner.addDecodedText(t);
        if (res.added) anyAdded = true;
      }
      const st = this.scanner.status();
      this.onProgress({ ...st, added: anyAdded });
      if (st.complete && !this._completed) {
        this._completed = true;
        const bytes = this.scanner.result();
        this.stop();
        this.onComplete(bytes);
      }
    } catch (e) {
      // A single bad frame (blur, motion, camera glitch, or a genuinely
      // mismatched CRC/total from pointing at the wrong sequence) must
      // never kill the scan -- report it and keep sampling. Only a
      // hard camera/permission failure (handled in start()) stops the
      // loop.
      this.onError(e);
    } finally {
      this._busy = false;
    }
  }
}
