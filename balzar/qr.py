"""Physical QR carrier: payload <-> printable QR image(s), one gesture.

If the payload fits one QR, this generates a single QR image. If it
doesn't, it splits the payload into self-describing chunks (BZC1, see
payload.py) and lays the QR codes out in a grid in a single image — the
user experience stays "scan this image" either way, 1 code or many.

Reading is symmetric: decode every QR found in one photo (ZBar, via
pyzbar — verified far more reliable than OpenCV's native multi-decoder
for many codes in one shot: 15/15 vs 5/15 in testing), reassemble
regardless of scan order, done.

Raw bytes do NOT round-trip reliably through QR generation + ZBar
decoding (verified empirically: corrupted on binary payloads) — chunks
are base64-encoded before being put in a QR, same as the CLI's
`encode --base64`.

Beyond one grid: `payload_to_qr_image` above auto-sizes a single grid to
however many chunks the payload needs, with no cap -- fine as a file, but
not necessarily readable if displayed/printed at a fixed physical size
(a 14x14 grid of tiny QR codes in one frame is not the same problem as
a 4x4 one). `payload_to_qr_frames` below caps each grid at grid_dim**2
codes and returns a *sequence* of grids ("frames") instead. This is
purely a presentation concern: BZC1 chunks carry their own index/total/
crc regardless of which frame they end up in, so frame order carries no
data-level meaning -- LiveScanner accumulates chunks from any number of
photos, in any order, of any subset of frames (including the same frame
re-shot twice), and knows it's done the moment every index has been seen
at least once. The "Frame i/N" label baked into each grid is a human
affordance only, so a person knows how many photos are left to take.

`frames_to_gif`/`frames_to_files` are the two ways to bundle a frame
sequence into one deliverable: an auto-playing GIF (for a screen that
cycles frames on its own -- lossless here, unlike on a photo, because a
QR code is pure black/white and GIF's 256-colour palette is not a
constraint on 2-colour content) or one PNG file per frame (for printing
on paper, where "animated" has no meaning). Both are read the same way,
through LiveScanner -- the bundle format is a write-side choice only.

Reading speed: LiveScanner.add/scan_image_bytes hand the whole image to
ZBar by default, which has to search the entire canvas for finder
patterns -- measured 5.84s to decode a real 16-code grid this way.
Passing grid_dim (when the caller already knows the image is a
payload_to_qr_frames(grid_dim=N) grid) tries _decode_tiled first:
cropping into grid_dim*grid_dim regions, by SOLVING _compose_grid's own
layout formula for the real cell/pad rather than guessing a uniform
division, measured 3.03s for the same frame, all 16 codes recovered.
This is a speed optimization only, never a correctness one: it's used
solely when tiling recovers a complete grid_dim*grid_dim frame, and
falls back to the exact same whole-image scan otherwise (a partial last
frame with too few remaining codes, or any image that isn't actually a
matching grid) -- so a wrong or absent hint never loses a code, it only
forgoes the speedup. A first attempt at this used a uniform grid_dim
division with a 15% safety margin instead of solving the real formula;
it only recovered 11-14 of 16 codes per frame, which meant the
whole-image fallback fired almost every time *on top of* the tiling
attempt -- measured 66.5s vs the 39.7s baseline on a real file, a
genuine regression later found and fixed by solving the exact geometry
instead of approximating it (see _tile_boxes).
"""

from __future__ import annotations

import io
import math
import struct

from .payload import (CHUNK_MAGIC, QR_V40_BINARY_CAPACITY, _CHUNK_HEADER,
                      assemble_chunks, chunk_payload, from_base64, to_base64)

# base64 expands 3 raw bytes -> 4 text chars; leave a small safety margin
# under the QR's binary capacity for the text-mode overhead
CHUNK_RAW_BYTES = (QR_V40_BINARY_CAPACITY * 3 // 4) - 8

# Below this many codes, don't bother spinning up a process pool -- its
# startup cost (cheap on Linux's fork, measured non-trivial on Windows/
# macOS's spawn, which re-imports the whole module tree per worker,
# not verified in this environment) isn't worth it for a handful of QR
# codes that would encode in well under a second anyway.
_PARALLEL_MIN_IMAGES = 4


def _qr_image(text: str, box_size: int = 6, border: int = 2):
    import qrcode
    # level L (7% recovery, highest capacity): matches QR_V40_BINARY_CAPACITY
    # in payload.py. Corruption is already caught by the BZC1 CRC at
    # assemble time (re-scan on failure), so we trade physical robustness
    # for fewer codes per payload rather than duplicating that safety net.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L,
                       border=border, box_size=box_size)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _qr_image_png_bytes(text: str) -> bytes:
    """_qr_image, serialized to PNG bytes -- the picklable unit of work
    for _generate_qr_images's process pool (a PIL Image itself round-
    trips through pickle on most Pillow versions, but going through PNG
    bytes explicitly sidesteps ever depending on that, and is lossless
    for this content, same reasoning as frames_to_gif above)."""
    buf = io.BytesIO()
    _qr_image(text).save(buf, format="PNG")
    return buf.getvalue()


def _generate_qr_images(texts: list[str]):
    """Encode each text into its own QR image. Measured (not assumed):
    generating a near-max-capacity version-40 QR (the common case here,
    since chunk_payload sizes chunks to fill one) costs ~0.06ms per
    base64 character regardless of QR version -- i.e. proportional to
    total data, not shrinkable by picking a different grid_dim/chunk
    size. On a real 555,922 B payload (190 chunks) this dominated the
    whole pipeline: 79.9s of QR generation alone, more than the 56.9s
    spent reading the same frames back. But every chunk's QR encoding is
    completely independent of every other's, so it parallelizes across
    CPU cores for a real wall-clock win with zero change to the output
    bytes: measured 3.84x on a 4-core machine for 64 codes (14.34s ->
    3.74s), identical PNG bytes confirmed byte-for-byte against the
    sequential path.

    Falls back to sequential unconditionally on any error (a sandboxed
    environment without process-spawn support, a platform quirk not
    seen here) -- this is a speed optimization only, and a payload with
    too few codes to justify pool startup overhead (_PARALLEL_MIN_IMAGES)
    always stays sequential."""
    if len(texts) < _PARALLEL_MIN_IMAGES:
        return [_qr_image(t) for t in texts]
    try:
        import concurrent.futures
        from PIL import Image
        with concurrent.futures.ProcessPoolExecutor() as ex:
            png_bytes = list(ex.map(_qr_image_png_bytes, texts))
        return [Image.open(io.BytesIO(b)).convert("RGB") for b in png_bytes]
    except Exception:
        return [_qr_image(t) for t in texts]


def _compose_grid(images, labels, frame_label=None):
    """Lay QR images out in a roughly-square grid, each cell captioned
    with its own label, plus an optional label for the whole frame."""
    from PIL import Image, ImageDraw

    if len(images) == 1 and frame_label is None:
        return images[0]

    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)
    cell = max(im.size[0] for im in images)
    pad = max(12, cell // 15)
    label_h = 22
    top = 26 if frame_label else 0
    grid = Image.new(
        "RGB",
        (cols * (cell + pad) + pad, top + rows * (cell + pad + label_h) + pad),
        "white",
    )
    draw = ImageDraw.Draw(grid)
    if frame_label:
        draw.text((pad, 4), frame_label, fill="black")
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        x, y = pad + c * (cell + pad), top + pad + r * (cell + pad + label_h)
        # NEAREST, not the default (bicubic): a shorter final chunk
        # produces a smaller QR (fewer modules -> a lower QR version),
        # so it's the one usually needing this resize up to `cell` --
        # bicubic interpolation blurs the sharp module edges into ~256
        # distinct gray levels (measured), turning a pure black/white
        # code into a noisier one to binarize under exactly the
        # non-ideal conditions (handheld camera, autofocus, real
        # lighting) this format is meant to tolerate. NEAREST preserves
        # the original 2 colors exactly, just at a different pixel size.
        grid.paste(im.resize((cell, cell), Image.NEAREST), (x, y))
        draw.text((x, y + cell + 2), labels[i], fill="black")
    return grid


def _tile_boxes(width: int, height: int, grid_dim: int):
    """grid_dim*grid_dim cell regions covering a _compose_grid image,
    derived by INVERTING _compose_grid's own layout formula (cols=rows=
    grid_dim, cell+pad tiling, optional top label band) instead of
    guessing a uniform division with a safety margin.

    A first version used width/grid_dim slices with a 15% margin -- that
    measured WORSE than no tiling at all (66.5s vs 39.7s baseline on a
    real file's frames): the margin wasn't tight enough to reliably
    catch every code (11-14 of 16 found), so the "did tiling recover
    everything" check in _decode_tiled's caller almost always failed and
    paid for the whole-image fallback *on top of* the tiling attempt --
    strictly more work than doing nothing. Solving for the real cell/pad
    _compose_grid used (pad = max(12, cell//15), a few fixed-point
    iterations since pad depends weakly on cell) instead of guessing
    fixes this: measured 16/16 recovered, 3.03s vs 5.84s whole-image on
    the same real frame -- an actual net win, not just a smaller crop.

    Only exact when the frame's actual cols/rows equal grid_dim, which
    holds for every full frame from payload_to_qr_frames and for a last
    (partial) frame as long as it holds more than (grid_dim-1)**2 codes
    -- true in the real file tested (13 codes > 9). A caller whose frame
    doesn't fit this always falls back to the whole-image scan anyway
    (see _decode_tiled's caller), so a wrong assumption here costs
    speed, never correctness.
    """
    cols = rows = grid_dim
    cell = width * 15 / (16 * cols + 1)
    pad = 12
    for _ in range(4):
        pad = max(12, int(cell) // 15)
        cell = (width - pad * (cols + 1)) / cols
    cell = round(cell)
    pad = max(12, cell // 15)
    label_h = 22
    top = max(0, height - (rows * (cell + pad + label_h) + pad))

    # The formula above assumes the image really IS a grid_dim x
    # grid_dim _compose_grid layout (e.g. a bare single QR -- no grid at
    # all, from payload_to_qr_image's len(images)==1 shortcut -- isn't).
    # A wrong assumption can solve to a nonsensical (tiny or negative)
    # cell; fail closed with no boxes rather than handing crop() an
    # inverted box, letting the caller's completeness check naturally
    # fall back to the whole-image scan.
    if cell < 20:
        return []

    boxes = []
    slack = 3  # only for integer-rounding error, not layout uncertainty
    for r in range(rows):
        for c in range(cols):
            x = pad + c * (cell + pad)
            y = top + pad + r * (cell + pad + label_h)
            left = max(0, x - slack)
            upper = max(0, y - slack)
            right = min(width, x + cell + slack)
            lower = min(height, y + cell + slack)
            # same fail-closed principle as the cell<20 guard above: a
            # grid_dim that doesn't match this image's real layout can
            # still push a cell entirely outside the image bounds after
            # clamping; skip it rather than hand crop() an inverted box
            if left < right and upper < lower:
                boxes.append((left, upper, right, lower))
    return boxes


def _decode_tiled(img, grid_dim: int):
    """Decode a _compose_grid image by cropping it into grid_dim*grid_dim
    regions (see _tile_boxes) and running ZBar on each small crop
    instead of the whole canvas -- measured 3.03s vs 5.84s whole-image
    for a real 16-code frame, all 16 recovered. This is a speed
    optimization only, never a correctness requirement: callers must
    still fall back to a whole-image decode when this doesn't recover a
    full grid_dim*grid_dim frame (see LiveScanner.add/scan_image_bytes:
    only used when the caller already knows grid_dim because they
    generated the grid themselves)."""
    from pyzbar.pyzbar import decode as zbar_decode

    results = []
    seen_data = set()
    for box in _tile_boxes(img.size[0], img.size[1], grid_dim):
        crop = img.crop(box)
        for r in zbar_decode(crop):
            # de-dup: overlapping tile margins can find the same
            # physical code in two adjacent crops
            if r.data not in seen_data:
                seen_data.add(r.data)
                results.append(r)
    return results


def payload_to_qr_image(payload: bytes):
    """One payload -> one printable image (single QR, or an auto-sized
    grid of QR codes if the payload doesn't fit in one)."""
    chunks = chunk_payload(payload, chunk_size=CHUNK_RAW_BYTES)
    images = _generate_qr_images([to_base64(c) for c in chunks])
    labels = [f"{i + 1}/{len(images)}" for i in range(len(images))]
    return _compose_grid(images, labels)


def payload_to_qr_frames(payload: bytes, grid_dim: int = 4) -> list:
    """One payload -> a sequence of grid images, each holding at most
    grid_dim*grid_dim QR codes.

    Use this instead of payload_to_qr_image when the grid must stay
    readable at a fixed physical output size (screen/print): capping
    codes-per-frame keeps each QR module above whatever pixel budget the
    output medium can give it, at the cost of needing more than one
    photo. grid_dim is a property of the physical medium, not of the
    payload -- pick it from real decode tests at the actual output
    resolution (see tests/test_qr.py's grid-size benchmark), not by
    assumption.
    """
    chunks = chunk_payload(payload, chunk_size=CHUNK_RAW_BYTES)
    total = len(chunks)
    per_frame = grid_dim * grid_dim
    n_frames = math.ceil(total / per_frame)
    # generate every QR image across ALL frames in one pass, not one
    # process pool per frame -- pool startup only happens once instead
    # of once per frame, and there's more work to spread across cores
    all_images = _generate_qr_images([to_base64(c) for c in chunks])
    frames = []
    for f in range(n_frames):
        images = all_images[f * per_frame:(f + 1) * per_frame]
        start = f * per_frame
        labels = [f"{start + i + 1}/{total}" for i in range(len(images))]
        frame_label = f"Frame {f + 1}/{n_frames}" if n_frames > 1 else None
        frames.append(_compose_grid(images, labels, frame_label))
    return frames


def frames_to_gif(frames: list, duration_ms: int = 1500, loop: int = 0) -> bytes:
    """Bundle a frame sequence into one auto-playing GIF (for a screen
    that cycles frames on its own). Lossless for this content: a QR
    code is pure black/white, so GIF's 256-colour palette limit -- which
    would matter for a photo -- costs nothing here."""
    import io as _io

    buf = _io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=duration_ms, loop=loop)
    return buf.getvalue()


def gif_to_frames(data: bytes) -> list:
    """Split an animated GIF back into its frames. Used to test the
    read path (LiveScanner) without a camera: decode straight from the
    bundle instead of photographing a screen playing it."""
    import io as _io

    from PIL import Image, ImageSequence

    img = Image.open(_io.BytesIO(data))
    return [frame.convert("RGB").copy() for frame in ImageSequence.Iterator(img)]


def frames_to_files(frames: list, out_dir: str, prefix: str = "frame") -> list:
    """Bundle a frame sequence into one PNG file per frame (for
    printing on paper, where "auto-playing" has no meaning)."""
    import os

    n = len(frames)
    width = len(str(n))
    paths = []
    for i, im in enumerate(frames):
        path = os.path.join(out_dir, f"{prefix}_{i + 1:0{width}d}_of_{n}.png")
        im.save(path, format="PNG")
        paths.append(path)
    return paths


class LiveScanner:
    """Accumulates BZC1 chunks across any number of photos taken over
    time -- a camera pointed at a screen playing frames_to_gif, or
    sequential photos of frames_to_files pages.

    Chunk identity lives inside the QR payload itself (BZC1's own
    index/total/crc), not in which frame or photo it came from, so
    add() tolerates any order, any subset of frames per call, and the
    same frame photographed more than once (a duplicate chunk is just
    ignored, not an error) -- the same order-independence
    scan_image_bytes already had for a single photo, extended across
    multiple photos instead of requiring completeness in one shot.
    """

    def __init__(self):
        self._parts: dict = {}
        self._total = None
        self._crc = None

    def add(self, image_bytes: bytes, grid_dim: int | None = None):
        """Decode every QR in one photo/frame, merge into the running
        set. Returns (done, missing): missing is the sorted list of
        chunk indices not seen yet (None once done).

        grid_dim is an optional speed hint, not a correctness
        requirement: pass it ONLY when you already know this image came
        from payload_to_qr_frames(grid_dim=N) (e.g. reading back your
        own generated frames, not an arbitrary photo) -- it tries the
        much faster tiled decode (_decode_tiled) first, and only falls
        back to the full whole-image scan when tiling didn't recover a
        complete grid_dim*grid_dim frame (the common case for every
        frame except a partial last one). Omit it (default) for the
        general case -- an actual photograph, or any image whose layout
        isn't known in advance -- which always uses the original
        whole-image scan."""
        import io as _io

        from PIL import Image
        from pyzbar.pyzbar import decode as zbar_decode

        img = Image.open(_io.BytesIO(image_bytes)).convert("RGB")
        results = None
        if grid_dim is not None:
            tiled = _decode_tiled(img, grid_dim)
            if len(tiled) == grid_dim * grid_dim:
                results = tiled
        if results is None:
            results = zbar_decode(img)
        for result in results:
            chunk = from_base64(result.data.decode("ascii"))
            if len(chunk) < _CHUNK_HEADER or chunk[:4] != CHUNK_MAGIC:
                continue
            i, t, c = struct.unpack(">HHI", chunk[4:_CHUNK_HEADER])
            if self._total is None:
                self._total, self._crc = t, c
            elif (t, c) != (self._total, self._crc):
                raise ValueError("i QR trovati appartengono a payload diversi")
            self._parts[i] = chunk
        if self._total is None:
            return False, None
        missing = sorted(set(range(self._total)) - set(self._parts))
        return (not missing), (missing or None)

    def result(self) -> bytes:
        """Assemble the payload once add() has reported done=True."""
        missing = sorted(set(range(self._total or 0)) - set(self._parts))
        if missing or self._total is None:
            raise ValueError(f"scansione incompleta, mancano i capitoli: {missing}")
        return assemble_chunks(list(self._parts.values()))


def scan_image_bytes(data: bytes, grid_dim: int | None = None) -> bytes:
    """One photo (of a single QR or a grid of many) -> reassembled payload.

    Uses ZBar (pyzbar), not OpenCV's native detector: verified far more
    reliable at reading many QR codes from one shot.

    grid_dim is the same optional speed hint as LiveScanner.add: pass it
    only when this image is known to be a full payload_to_qr_image-style
    grid of exactly grid_dim*grid_dim codes (tiled decode, falls back to
    the whole-image scan otherwise -- never less correct, just slower
    when the hint doesn't fit).
    """
    import io as _io

    from PIL import Image
    from pyzbar.pyzbar import decode as zbar_decode

    img = Image.open(_io.BytesIO(data)).convert("RGB")
    results = None
    if grid_dim is not None:
        tiled = _decode_tiled(img, grid_dim)
        if len(tiled) == grid_dim * grid_dim:
            results = tiled
    if results is None:
        results = zbar_decode(img)
    if not results:
        raise ValueError("nessun QR code trovato nell'immagine")
    chunks = [from_base64(r.data.decode("ascii")) for r in results]
    return assemble_chunks(chunks)


def scan_image_file(path: str, grid_dim: int | None = None) -> bytes:
    with open(path, "rb") as fh:
        return scan_image_bytes(fh.read(), grid_dim=grid_dim)
