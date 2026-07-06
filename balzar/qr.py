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
"""

from __future__ import annotations

import math
import struct

from .payload import (CHUNK_MAGIC, QR_V40_BINARY_CAPACITY, _CHUNK_HEADER,
                      assemble_chunks, chunk_payload, from_base64, to_base64)

# base64 expands 3 raw bytes -> 4 text chars; leave a small safety margin
# under the QR's binary capacity for the text-mode overhead
CHUNK_RAW_BYTES = (QR_V40_BINARY_CAPACITY * 3 // 4) - 8


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
        grid.paste(im.resize((cell, cell)), (x, y))
        draw.text((x, y + cell + 2), labels[i], fill="black")
    return grid


def payload_to_qr_image(payload: bytes):
    """One payload -> one printable image (single QR, or an auto-sized
    grid of QR codes if the payload doesn't fit in one)."""
    chunks = chunk_payload(payload, chunk_size=CHUNK_RAW_BYTES)
    images = [_qr_image(to_base64(c)) for c in chunks]
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
    frames = []
    for f in range(n_frames):
        batch = chunks[f * per_frame:(f + 1) * per_frame]
        images = [_qr_image(to_base64(c)) for c in batch]
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

    def add(self, image_bytes: bytes):
        """Decode every QR in one photo/frame, merge into the running
        set. Returns (done, missing): missing is the sorted list of
        chunk indices not seen yet (None once done)."""
        import io as _io

        from PIL import Image
        from pyzbar.pyzbar import decode as zbar_decode

        img = Image.open(_io.BytesIO(image_bytes)).convert("RGB")
        for result in zbar_decode(img):
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


def scan_image_bytes(data: bytes) -> bytes:
    """One photo (of a single QR or a grid of many) -> reassembled payload.

    Uses ZBar (pyzbar), not OpenCV's native detector: verified far more
    reliable at reading many QR codes from one shot.
    """
    import io as _io

    from PIL import Image
    from pyzbar.pyzbar import decode as zbar_decode

    img = Image.open(_io.BytesIO(data)).convert("RGB")
    results = zbar_decode(img)
    if not results:
        raise ValueError("nessun QR code trovato nell'immagine")
    chunks = [from_base64(r.data.decode("ascii")) for r in results]
    return assemble_chunks(chunks)


def scan_image_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return scan_image_bytes(fh.read())
