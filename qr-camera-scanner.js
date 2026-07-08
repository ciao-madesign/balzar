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
// decodeAllViaMasking fallback) is synchronous JS work. Driving decode
// attempts off requestAnimationFrame directly (as fast as the display
// refreshes, ~60/s) would pile up overlapping decode calls and starve the
// browser's render/input loop. A `busy` guard plus a minimum-interval
// throttle (default 60ms, effectively a floor -- see below, decode
// latency itself ends up setting the real pace) keeps the UI responsive
// while sampling as often as the decode cost allows.
//
// gridDim=1 is the ONLY grid_dim this class is realistically usable with,
// and callers generating the QR sequence for continuous camera capture
// MUST use grid_dim=1 -- this is a real, measured constraint, not a
// conservative default. jsQR needs roughly 1000-1200px of PIXEL WIDTH per
// individual QR code to decode reliably, with a content-dependent
// unreliable band somewhere around 700-1000px (which specific pixel
// widths fail is NOT a fixed rule -- it shifts with the QR's own data,
// confirmed by testing the identical resize sweep against two different
// payloads and getting different failure points each time; treat
// anything under ~1050px as unreliable, not just a documented magic
// range). A live camera pointed at a screen/page from a normal working
// distance delivers nowhere near the ~3800-4700px of frame width a
// grid_dim=4 grid needs for every one of its 16 codes to individually
// clear that threshold (that number IS achievable for a static,
// deliberately-framed PHOTO -- see balzar/qr.py's own grid_dim=4 default
// for the desktop photo-scan/print use case -- but not for a live,
// continuously-panning capture).
//
// Default capture resolution (idealWidth/idealHeight = 1280x1152, not
// 1920x1080) and default intervalMs (60, not 350) were both revised after
// measuring, not guessed:
//  - jsQR decode latency scales with total pixels scanned, not just
//    per-code size: the SAME single-QR decode took ~660ms median at
//    1920x1080 vs ~200ms median at 1280x1152 -- a >3x speed difference
//    from capture resolution alone, before touching intervalMs at all.
//  - 1280x960 (the "obvious" 4:3 choice) was tried first and measured
//    WORSE than the square-ish 1280x1152: balzar's grid_dim=1 pages are
//    close to square, so fitting them into a 960px-tall frame with
//    reasonable margin (0.95x) pushed the code down to ~880-920px --
//    squarely in the unreliable band above -- while the SAME pages fit
//    into 1280x1152 land at ~1050-1170px, comfortably clear of it. This
//    was found by testing all 5 pages of a real payload, not just page 0
//    (which happened to be a size that decoded fine at 960px, masking
//    the problem on a single-page smoke test).
//  - intervalMs's job changes once decode is this fast: at ~200ms/decode
//    and a `busy` guard, the decode latency itself becomes the real pace
//    -setter, not the interval gate. 60ms is a floor against a
//    hypothetically instant decode, not the expected cadence.
//
// Net effect, measured end to end with a real getUserMedia() call (fed
// by Chromium's fake-video-capture device pointed at a real grid_dim=1
// QR sequence, not a mock): a 5-page sequence scans in ~6.3s at the
// original 1.5s/page display rate, but the SAME 5 pages scan in ~1.7-1.8s
// (~3.6x faster) once the page-display duration is also lowered to match
// the faster decode -- confirmed reliable at 0.5s/page (~2.3s total) down
// to a 0.25s/page floor (limited by this session's Y4M test-harness frame
// granularity, not by the scanner itself). Recommended page-display
// duration for whatever generates the auto-cycling sequence (GIF/JS
// slideshow) is ~0.5s: fast, with margin for 2+ real decode attempts to
// land inside each page's window (0.25s leaves only one attempt's worth
// of margin, fine in a synthetic test with zero timing jitter, riskier
// on a real display + camera pair). See CLAUDE.md §2.4g/§2.4h for the
// full calibration writeup and the grid_dim=2/4 resolution sweeps that
// ruled those out for this delivery mode entirely.

class ContinuousQrScanner {
  constructor(opts) {
    if (!opts || !opts.video) throw new Error("ContinuousQrScanner: opts.video (un elemento <video>) e' obbligatorio");
    if (!opts.gridDim) throw new Error("ContinuousQrScanner: opts.gridDim e' obbligatorio");
    if (typeof opts.onComplete !== "function") throw new Error("ContinuousQrScanner: opts.onComplete e' obbligatorio");

    this.video = opts.video;
    this.gridDim = opts.gridDim;
    this.intervalMs = opts.intervalMs !== undefined ? opts.intervalMs : 60;
    this.idealWidth = opts.idealWidth || 1280;
    this.idealHeight = opts.idealHeight || 1152;
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
          width: { ideal: this.idealWidth },
          height: { ideal: this.idealHeight },
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
