"""Physical QR carrier: payload <-> printable image(s), one photo either way.

Skipped entirely if qrcode/pyzbar (+ system libzbar) aren't installed —
these are optional, desktop-only dependencies, not part of the core
engine (see balzar/qr.py docstring for why raw bytes aren't used).
"""

import io
import unittest

try:
    import qrcode  # noqa: F401
    from pyzbar.pyzbar import decode as _zbar_decode  # noqa: F401
    HAVE_QR_DEPS = True
except ImportError:
    HAVE_QR_DEPS = False

from balzar.payload import encode_payload


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestQRCarrier(unittest.TestCase):
    def test_small_payload_single_qr_roundtrip(self):
        from balzar.qr import payload_to_qr_image, scan_image_bytes
        payload = encode_payload("CANVAS w=32 h=32 bg=0\nFILL region=FULL color=3")
        img = payload_to_qr_image(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.assertEqual(scan_image_bytes(buf.getvalue()), payload)

    def test_large_payload_becomes_grid_and_roundtrips(self):
        from balzar.qr import CHUNK_RAW_BYTES, payload_to_qr_image, scan_image_bytes
        # force a payload bigger than one chunk: several KB of distinct
        # instructions defeat deflate, so the encoded payload stays large
        lines = ["CANVAS w=64 h=64 bg=0"]
        for i in range(2000):
            lines.append(f"SETPIX x={i % 64} y={(i * 7) % 64} color={i % 251}")
        payload = encode_payload("\n".join(lines))
        self.assertGreater(len(payload), CHUNK_RAW_BYTES)

        img = payload_to_qr_image(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.assertEqual(scan_image_bytes(buf.getvalue()), payload)

    def test_scan_rejects_image_with_no_qr(self):
        from PIL import Image

        from balzar.qr import scan_image_bytes
        blank = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        blank.save(buf, format="PNG")
        with self.assertRaises(ValueError):
            scan_image_bytes(buf.getvalue())

    def test_partial_last_chunk_stays_pure_black_and_white_in_the_grid(self):
        # regression test for a real bug: _compose_grid resized every QR
        # image up to the frame's cell size with the default (bicubic)
        # filter. A payload whose last chunk is shorter than the rest
        # produces a smaller QR (fewer modules), which is exactly the
        # one that then gets resized -- bicubic interpolation blurs the
        # sharp module edges into ~256 distinct gray levels instead of
        # the 2 (pure black/white) a QR code should have, which is
        # harder to binarize under non-ideal scanning conditions.
        from balzar.qr import CHUNK_RAW_BYTES, _compose_grid, _qr_image
        from balzar.payload import to_base64

        small = _qr_image(to_base64(b"x" * 50))
        big = _qr_image(to_base64(b"x" * (CHUNK_RAW_BYTES - 8)))
        self.assertLess(small.size[0], big.size[0])

        grid = _compose_grid([big, small], labels=["1/2", "2/2"])
        cell = big.size[0]
        pad = max(12, cell // 15)
        # the second image (small) lands at column 1, row 0 in a 2-wide grid
        x = pad + 1 * (cell + pad)
        resized_region = grid.crop((x, pad, x + cell, pad + cell))
        colors = resized_region.getcolors(maxcolors=100000)
        self.assertIsNotNone(colors)
        self.assertLessEqual(len(colors), 2,
                             "resized QR must stay pure black/white (NEAREST), not "
                             "gain interpolation gray levels (bicubic)")


def _big_payload(n_lines=28000):
    lines = ["CANVAS w=64 h=64 bg=0"]
    for i in range(n_lines):
        lines.append(f"SETPIX x={i % 64} y={(i * 7) % 64} color={i % 251}")
    return encode_payload("\n".join(lines))


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestQRFrameSequence(unittest.TestCase):
    def test_small_payload_is_a_single_frame_no_label(self):
        from balzar.qr import payload_to_qr_frames
        payload = encode_payload("CANVAS w=32 h=32 bg=0\nFILL region=FULL color=3")
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertEqual(len(frames), 1)

    def test_grid_dim_caps_codes_per_frame(self):
        from balzar.qr import CHUNK_RAW_BYTES, payload_to_qr_frames
        payload = _big_payload()
        chunk_count = -(-len(payload) // CHUNK_RAW_BYTES)  # rough lower bound
        self.assertGreater(chunk_count, 16)

        frames_4 = payload_to_qr_frames(payload, grid_dim=4)
        frames_8 = payload_to_qr_frames(payload, grid_dim=8)
        # a tighter cap can only mean the same or more frames, never fewer
        self.assertGreaterEqual(len(frames_4), len(frames_8))
        self.assertGreater(len(frames_4), 1)

    def test_frame_sequence_roundtrips_via_live_scanner(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        scanner = LiveScanner()
        done = False
        for frame in frames:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            done, missing = scanner.add(buf.getvalue())
        self.assertTrue(done)
        self.assertEqual(scanner.result(), payload)

    def test_grid_dim_hint_gives_bit_identical_result(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        scanner = LiveScanner()
        for frame in frames:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            scanner.add(buf.getvalue(), grid_dim=4)
        self.assertEqual(scanner.result(), payload)

    def test_grid_dim_hint_falls_back_when_tiling_is_a_mismatch(self):
        # a single, un-gridded QR: the grid_dim=4 hint cannot possibly
        # apply (there's only one code, not 16) -- must still work via
        # the whole-image fallback, not silently find nothing
        from balzar.qr import LiveScanner, payload_to_qr_image
        payload = encode_payload("CANVAS w=16 h=16 bg=0\nFILL region=FULL color=2")
        img = payload_to_qr_image(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        scanner = LiveScanner()
        done, missing = scanner.add(buf.getvalue(), grid_dim=4)
        self.assertTrue(done)
        self.assertEqual(scanner.result(), payload)

    def test_scan_image_bytes_grid_dim_hint_matches_default(self):
        import math

        from balzar.payload import chunk_payload
        from balzar.qr import CHUNK_RAW_BYTES, payload_to_qr_image, scan_image_bytes
        payload = _big_payload()
        img = payload_to_qr_image(payload)  # one auto-sized grid, all chunks
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        n_chunks = len(chunk_payload(payload, chunk_size=CHUNK_RAW_BYTES))
        grid_dim_hint = math.ceil(math.sqrt(n_chunks))

        assembled_default = scan_image_bytes(buf.getvalue())
        assembled_hinted = scan_image_bytes(buf.getvalue(), grid_dim=grid_dim_hint)
        self.assertEqual(assembled_default, payload)
        self.assertEqual(assembled_hinted, payload)

    def test_live_scanner_accepts_frames_out_of_order_and_repeated(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 2)

        order = list(reversed(frames)) + [frames[0]]  # reversed, plus a repeat
        scanner = LiveScanner()
        for frame in order:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            scanner.add(buf.getvalue())
        self.assertEqual(scanner.result(), payload)

    def test_live_scanner_reports_missing_chunks_before_done(self):
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        scanner = LiveScanner()
        buf = io.BytesIO()
        frames[0].save(buf, format="PNG")
        done, missing = scanner.add(buf.getvalue())
        self.assertFalse(done)
        self.assertTrue(missing)
        with self.assertRaises(ValueError):
            scanner.result()

    def test_gif_bundle_roundtrips_through_live_scanner(self):
        from balzar.qr import (LiveScanner, frames_to_gif, gif_to_frames,
                               payload_to_qr_frames)
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        gif_bytes = frames_to_gif(frames, duration_ms=200)
        replayed = gif_to_frames(gif_bytes)
        self.assertEqual(len(replayed), len(frames))

        scanner = LiveScanner()
        for frame in replayed:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            scanner.add(buf.getvalue())
        self.assertEqual(scanner.result(), payload)

    def test_file_bundle_roundtrips_through_live_scanner(self):
        import shutil
        import tempfile

        from balzar.qr import LiveScanner, frames_to_files, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertGreater(len(frames), 1)

        out_dir = tempfile.mkdtemp()
        try:
            paths = frames_to_files(frames, out_dir)
            self.assertEqual(len(paths), len(frames))
            scanner = LiveScanner()
            for path in paths:
                with open(path, "rb") as fh:
                    scanner.add(fh.read())
            self.assertEqual(scanner.result(), payload)
        finally:
            shutil.rmtree(out_dir)


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestParallelQRGeneration(unittest.TestCase):
    """balzar/qr.py's _generate_qr_images: QR encoding at near-max
    capacity is CPU-bound and proportional to total data regardless of
    grid_dim (measured in session: ~0.06ms per base64 char at every QR
    version tried, 10 through 40) -- every chunk's encoding is
    independent of every other's, so this parallelizes across a process
    pool for a real wall-clock win (measured 3.84x on a 4-core machine
    for 64 codes) with zero change to the output bytes. These tests
    check correctness of that parallel path and its fallback, not the
    speedup itself (timing assertions would be flaky across CI
    hardware)."""

    def test_parallel_path_matches_sequential_byte_for_byte(self):
        from balzar.qr import _PARALLEL_MIN_IMAGES, _generate_qr_images, _qr_image

        texts = [f"payload-chunk-{i}" for i in range(_PARALLEL_MIN_IMAGES + 4)]
        sequential = [_qr_image(t) for t in texts]
        parallel = _generate_qr_images(texts)
        self.assertEqual(len(parallel), len(sequential))
        for seq_img, par_img in zip(sequential, parallel):
            buf_seq, buf_par = io.BytesIO(), io.BytesIO()
            seq_img.save(buf_seq, format="PNG")
            par_img.save(buf_par, format="PNG")
            self.assertEqual(buf_seq.getvalue(), buf_par.getvalue())

    def test_below_threshold_stays_sequential_even_if_pool_is_broken(self):
        import concurrent.futures

        import balzar.qr as qr_mod

        texts = ["only-one-chunk"]
        self.assertLess(len(texts), qr_mod._PARALLEL_MIN_IMAGES)

        class _BoomPool:
            def __init__(self, *a, **k):
                raise RuntimeError("process pool should never be created below threshold")

        original = concurrent.futures.ProcessPoolExecutor
        concurrent.futures.ProcessPoolExecutor = _BoomPool
        try:
            images = qr_mod._generate_qr_images(texts)
        finally:
            concurrent.futures.ProcessPoolExecutor = original
        self.assertEqual(len(images), 1)

    def test_falls_back_to_sequential_when_the_process_pool_fails(self):
        # a sandboxed environment without process-spawn support, or any
        # other platform quirk not seen in this session's testing --
        # this must never crash the whole encode, only forgo the speedup
        import concurrent.futures

        import balzar.qr as qr_mod

        class _BoomPool:
            def __init__(self, *a, **k):
                raise RuntimeError("simulated: this platform can't spawn a process pool")

        original = concurrent.futures.ProcessPoolExecutor
        concurrent.futures.ProcessPoolExecutor = _BoomPool
        try:
            texts = [f"payload-chunk-{i}" for i in range(qr_mod._PARALLEL_MIN_IMAGES + 2)]
            images = qr_mod._generate_qr_images(texts)
        finally:
            concurrent.futures.ProcessPoolExecutor = original
        self.assertEqual(len(images), len(texts))
        expected = [qr_mod._qr_image(t) for t in texts]
        for img, exp in zip(images, expected):
            buf_img, buf_exp = io.BytesIO(), io.BytesIO()
            img.save(buf_img, format="PNG")
            exp.save(buf_exp, format="PNG")
            self.assertEqual(buf_img.getvalue(), buf_exp.getvalue())


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestParallelTileDecoding(unittest.TestCase):
    """balzar/qr.py's _decode_crops: pyzbar calls into native libzbar via
    ctypes, which releases the GIL for the call -- verified (not
    assumed) to give a real wall-clock win from plain threads (measured
    3.72x on a real 16-tile frame), cheaper than the process pool
    generation needs since pure-Python QR encoding never releases the
    GIL. These tests check correctness of the parallel path and its
    fallback, not the speedup itself (timing assertions would be flaky
    across CI hardware)."""

    def _real_crops(self, grid_dim=4):
        from balzar.qr import _tile_boxes, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=grid_dim)
        img = frames[0]
        boxes = _tile_boxes(img.size[0], img.size[1], grid_dim)
        return [img.crop(box) for box in boxes]

    def test_parallel_path_matches_sequential_decoded_data(self):
        from pyzbar.pyzbar import decode as zbar_decode

        import balzar.qr as qr_mod

        crops = self._real_crops()
        self.assertGreaterEqual(len(crops), qr_mod._PARALLEL_MIN_IMAGES)
        sequential = [{r.data for r in zbar_decode(c)} for c in crops]
        parallel = [{r.data for r in results} for results in qr_mod._decode_crops(crops)]
        self.assertEqual(parallel, sequential)

    def test_below_threshold_stays_sequential_even_if_pool_is_broken(self):
        import concurrent.futures

        import balzar.qr as qr_mod

        crops = self._real_crops()[:qr_mod._PARALLEL_MIN_IMAGES - 1]

        class _BoomPool:
            def __init__(self, *a, **k):
                raise RuntimeError("thread pool should never be created below threshold")

        original = concurrent.futures.ThreadPoolExecutor
        concurrent.futures.ThreadPoolExecutor = _BoomPool
        try:
            results = qr_mod._decode_crops(crops)
        finally:
            concurrent.futures.ThreadPoolExecutor = original
        self.assertEqual(len(results), len(crops))

    def test_falls_back_to_sequential_when_the_thread_pool_fails(self):
        import concurrent.futures

        from pyzbar.pyzbar import decode as zbar_decode

        import balzar.qr as qr_mod

        crops = self._real_crops()
        self.assertGreaterEqual(len(crops), qr_mod._PARALLEL_MIN_IMAGES)

        class _BoomPool:
            def __init__(self, *a, **k):
                raise RuntimeError("simulated: this platform can't spawn a thread pool")

        original = concurrent.futures.ThreadPoolExecutor
        concurrent.futures.ThreadPoolExecutor = _BoomPool
        try:
            results = qr_mod._decode_crops(crops)
        finally:
            concurrent.futures.ThreadPoolExecutor = original
        expected = [{r.data for r in zbar_decode(c)} for c in crops]
        self.assertEqual([{r.data for r in res} for res in results], expected)

    def test_tile_boxes_uses_the_correct_top_on_a_full_single_frame_grid(self):
        # Real bug, found via the trasporto-qr.html UI (not hypothetical):
        # _tile_boxes tries top=26 before top=0, and used to accept
        # top=26 whenever it reconstructed the image height within
        # row_h/2 (hundreds of px) -- far too loose. A genuinely full
        # single-frame grid (no frame_label, so the real top is 0) with a
        # smaller last chunk (a shorter QR upscaled to `cell`, changing
        # cell/pad just enough) reconstructed within that old margin
        # under the WRONG top=26 hypothesis, shifting every crop ~26px
        # and making both pyzbar and jsQR fail on all of them even though
        # the whole image decodes fine. Payload sized to force exactly 4
        # chunks (3 full + 1 short) at grid_dim=2 -- one full 2x2 grid,
        # single frame, no frame_label.
        from balzar.qr import CHUNK_RAW_BYTES, _tile_boxes, payload_to_qr_frames
        from pyzbar.pyzbar import decode as zbar_decode

        payload = b"x" * (CHUNK_RAW_BYTES * 3 + 100)
        frames = payload_to_qr_frames(payload, grid_dim=2)
        self.assertEqual(len(frames), 1)
        img = frames[0]
        boxes = _tile_boxes(img.size[0], img.size[1], 2)
        self.assertEqual(len(boxes), 4)
        found = sum(1 for box in boxes if zbar_decode(img.crop(box)))
        self.assertEqual(found, 4, "every crop should decode; a top-hypothesis "
                          "geometry bug shifts all of them out of alignment")

    def test_tile_boxes_solves_fewer_columns_for_a_sparse_partial_frame(self):
        # Real bug, found from a user report of total non-detection on a
        # partial matrix, not a hypothetical: _tile_boxes used to
        # hardcode cols=grid_dim, but _compose_grid actually lays out
        # len(images) images at cols=ceil(sqrt(len(images))), which
        # drops BELOW grid_dim once there are few enough images left
        # (n <= (grid_dim-1)**2). 8 codes at grid_dim=4 is a real 3x3
        # layout (9 cells), not 4x4 (16) -- assuming 16 made every top
        # hypothesis fail to reconstruct the real (smaller) image
        # height, so _tile_boxes returned zero boxes and the caller fell
        # through to a whole-image scan that, for jsQR in the browser
        # (no reliable whole-image multi-decode fallback, unlike ZBar),
        # meant total failure instead of just a lost speedup.
        from balzar.qr import CHUNK_RAW_BYTES, _tile_boxes, payload_to_qr_frames
        from pyzbar.pyzbar import decode as zbar_decode

        payload = b"x" * (CHUNK_RAW_BYTES * 7 + 100)  # exactly 8 chunks
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertEqual(len(frames), 1)
        img = frames[0]
        boxes = _tile_boxes(img.size[0], img.size[1], 4)
        self.assertEqual(len(boxes), 9, "8 codes lay out as a real 3x3 grid, "
                          "not a 4x4 one -- cols must be solved, not assumed")
        found = sum(1 for box in boxes if zbar_decode(img.crop(box)))
        self.assertEqual(found, 8)

    def test_decode_tiled_recovers_a_partial_frame_with_a_blank_tail(self):
        # Real bug, found from the same user report ("10 of 16 slots
        # filled" produced total non-detection): _decode_tiled's old
        # completeness check required literally EVERY cell -- including
        # the genuinely blank ones past the real image count -- to
        # produce a decode result. This is structurally impossible for
        # any partial frame with a blank tail (10 real images at
        # grid_dim=4 lays out as a real 4x3 grid, 12 cells, the last 2
        # of which are blank white space with no QR at all), so the old
        # check discarded an otherwise fully-correct tiled decode every
        # single time. Fixed by accepting a hit/miss pattern that is a
        # PREFIX of the cells (real hits, then a blank tail), rejecting
        # only a hit appearing after a miss (a real geometry error).
        from balzar.payload import CHUNK_MAGIC, from_base64
        from balzar.qr import (CHUNK_RAW_BYTES, _decode_tiled, _tile_boxes,
                                payload_to_qr_frames)

        payload = b"x" * (CHUNK_RAW_BYTES * 9 + 100)  # exactly 10 chunks
        frames = payload_to_qr_frames(payload, grid_dim=4)
        self.assertEqual(len(frames), 1)
        img = frames[0]
        boxes = _tile_boxes(img.size[0], img.size[1], 4)
        self.assertEqual(len(boxes), 12, "10 codes lay out as a real 4x3 "
                          "grid (12 cells, 2 genuinely blank), not 4x4")
        results = _decode_tiled(img, grid_dim=4)
        real_chunks = [r for r in results
                      if from_base64(r.data.decode("ascii"))[:4] == CHUNK_MAGIC]
        self.assertEqual(len(real_chunks), 10)

    def test_decode_tiled_drops_spurious_non_qr_symbology_matches(self):
        # Real regression, introduced by the blank-tail fix above and
        # found by re-running the existing test suite, not anticipated:
        # ZBar can occasionally misdetect an unrelated barcode symbology
        # (e.g. DATABAR) inside a real cell's cropped region, alongside
        # the genuine QRCODE match -- previously invisible because the
        # old all-cells-must-hit check always failed for any partial
        # frame anyway, forcing a whole-image fallback that doesn't
        # exhibit this crop-boundary artifact. Once partial frames
        # started succeeding via the tiled path, the collection loop
        # picked up the spurious non-QR result alongside the real one,
        # which then failed assemble_chunks's magic-byte check
        # downstream. _decode_tiled must filter to r.type == "QRCODE"
        # before collecting -- this reproduces the exact frame where the
        # spurious DATABAR match was observed (a 34-chunk payload tiled
        # at grid_dim=6, cell index 4).
        from balzar.qr import _decode_tiled, payload_to_qr_frames
        from balzar.payload import assemble_chunks, from_base64

        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=6)
        results = _decode_tiled(frames[0], grid_dim=6)
        self.assertTrue(all(r.type == "QRCODE" for r in results))
        chunks = [from_base64(r.data.decode("ascii")) for r in results]
        self.assertEqual(assemble_chunks(chunks), payload)

    def test_decode_tiled_end_to_end_still_recovers_full_frame(self):
        # only count actual BZC1 chunks -- zbar can occasionally
        # misdetect an unrelated barcode symbology in the label-text
        # region of the image (observed: a spurious non-QR read), which
        # is harmless because LiveScanner/scan_image_bytes already
        # filter for the CHUNK_MAGIC prefix downstream; this test cares
        # that every real chunk is still recovered, not that nothing
        # else was ever (spuriously) read
        from balzar.payload import CHUNK_MAGIC, from_base64
        from balzar.qr import _decode_tiled, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=4)
        results = _decode_tiled(frames[0], grid_dim=4)
        real_chunks = [r for r in results
                      if from_base64(r.data.decode("ascii"))[:4] == CHUNK_MAGIC]
        self.assertEqual(len(real_chunks), 16)


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestAutoGridDimDetection(unittest.TestCase):
    """grid_dim on the reading side (LiveScanner.add/scan_image_bytes) is
    now an optional override, not something the operator needs to know
    or match at scan time: omitting it auto-detects via a fixed ceiling
    search (_AUTO_GRID_DIM_CEILING) instead of requiring the exact value
    used at generation. Verified safe (not just assumed) across every
    grid_dim/chunk-count combination the system's own generator can
    produce -- 136 real frames swept manually in session, reproduced
    here as focused regression cases for the 4 that hit a genuine
    geometry coincidence (a WRONG (cols, top) hypothesis also satisfying
    the tight reconstruction tolerance before the true one is reached)."""

    def test_scan_image_bytes_without_grid_dim_reads_a_grid_dim_2_sequence(self):
        # the auto-detect ceiling (8) must still find a sequence
        # generated with a SMALLER grid_dim -- not just the ceiling
        # itself -- proving this is real detection, not a fluke that
        # only works when generation happened to also use 8
        from balzar.qr import LiveScanner, payload_to_qr_frames
        payload = _big_payload()
        frames = payload_to_qr_frames(payload, grid_dim=2)
        self.assertGreater(len(frames), 2)

        scanner = LiveScanner()
        for frame in frames:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            scanner.add(buf.getvalue())  # no grid_dim: must auto-detect
        self.assertEqual(scanner.result(), payload)

    def test_auto_ceiling_never_returns_wrong_chunks_on_known_geometry_coincidences(self):
        # Real cases found by an exhaustive sweep (not hypothetical):
        # searching cols from the ceiling (8) downward can hit a WRONG
        # (cols, top) hypothesis that also satisfies the tight
        # reconstruction tolerance before reaching the true, smaller
        # cols -- e.g. a tiny single-QR frame (real cols=1) where cols=8
        # also happens to reconstruct the image's small width/height
        # exactly. This must NEVER translate into wrong decoded data:
        # the mis-cropped regions must fail to decode (empty result),
        # correctly falling through to the safe whole-image scan.
        from balzar.qr import CHUNK_RAW_BYTES, _AUTO_GRID_DIM_CEILING, _decode_tiled, payload_to_qr_frames
        from balzar.payload import CHUNK_MAGIC, chunk_payload, from_base64

        cases = [(1, 2, 1), (1, 3, 2), (1, 5, 4), (2, 5, 1)]  # (real_grid_dim, n_chunks, frame_index)
        for real_grid_dim, n_chunks, frame_index in cases:
            with self.subTest(real_grid_dim=real_grid_dim, n_chunks=n_chunks):
                payload = b"x" * (CHUNK_RAW_BYTES * (n_chunks - 1) + 100)
                expected = {bytes(c) for c in chunk_payload(payload, chunk_size=CHUNK_RAW_BYTES)}
                frames = payload_to_qr_frames(payload, grid_dim=real_grid_dim)
                img = frames[frame_index]

                result = _decode_tiled(img, grid_dim=_AUTO_GRID_DIM_CEILING)
                got = {from_base64(r.data.decode("ascii")) for r in result
                      if from_base64(r.data.decode("ascii"))[:4] == CHUNK_MAGIC}
                self.assertTrue(got.issubset(expected),
                                "auto-ceiling geometry mismatch must never fabricate wrong chunks")

    def test_scan_image_bytes_default_still_recovers_the_known_coincidence_frame(self):
        # end-to-end version of the case above: even where the tiled
        # ceiling search hits the geometry coincidence and comes back
        # empty, scan_image_bytes's whole-image fallback must still
        # recover the correct payload with zero operator input
        from balzar.qr import CHUNK_RAW_BYTES, payload_to_qr_frames, scan_image_bytes

        payload = b"x" * (CHUNK_RAW_BYTES * 1 + 100)  # exactly 2 chunks, grid_dim=1
        frames = payload_to_qr_frames(payload, grid_dim=1)
        self.assertEqual(len(frames), 2)
        buf = io.BytesIO()
        frames[1].save(buf, format="PNG")  # the frame with the known geometry coincidence

        from balzar.qr import LiveScanner
        scanner = LiveScanner()
        scanner.add(buf.getvalue())
        buf0 = io.BytesIO()
        frames[0].save(buf0, format="PNG")
        scanner.add(buf0.getvalue())
        self.assertEqual(scanner.result(), payload)


@unittest.skipUnless(HAVE_QR_DEPS, "requires qrcode + pyzbar (+ system libzbar)")
class TestEstimateScanSeconds(unittest.TestCase):
    """estimate_scan_seconds: an honestly-labelled ballpark, calibrated
    from a real decode benchmark (CLAUDE.md §9.24), not a promise."""

    def test_scales_with_frame_count(self):
        from balzar.qr import estimate_scan_seconds
        low1, high1 = estimate_scan_seconds(1)
        low10, high10 = estimate_scan_seconds(10)
        self.assertLess(low1, low10)
        self.assertLess(high1, high10)

    def test_high_is_at_least_low(self):
        from balzar.qr import estimate_scan_seconds
        for n in (0, 1, 7, 50):
            low, high = estimate_scan_seconds(n)
            self.assertLessEqual(low, high)

    def test_zero_frames_is_zero(self):
        from balzar.qr import estimate_scan_seconds
        self.assertEqual(estimate_scan_seconds(0), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
