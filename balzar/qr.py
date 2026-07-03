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
"""

from __future__ import annotations

import math

from .payload import (QR_V40_BINARY_CAPACITY, assemble_chunks, chunk_payload,
                      from_base64, to_base64)

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


def payload_to_qr_image(payload: bytes):
    """One payload -> one printable image (single QR, or an auto-sized
    grid of QR codes if the payload doesn't fit in one)."""
    from PIL import Image

    chunks = chunk_payload(payload, chunk_size=CHUNK_RAW_BYTES)
    images = [_qr_image(to_base64(c)) for c in chunks]
    if len(images) == 1:
        return images[0]

    cols = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / cols)
    cell = max(im.size[0] for im in images)
    pad = max(12, cell // 15)
    label_h = 22
    grid = Image.new(
        "RGB",
        (cols * (cell + pad) + pad, rows * (cell + pad + label_h) + pad),
        "white",
    )
    from PIL import ImageDraw
    draw = ImageDraw.Draw(grid)
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        x, y = pad + c * (cell + pad), pad + r * (cell + pad + label_h)
        grid.paste(im.resize((cell, cell)), (x, y))
        draw.text((x, y + cell + 2), f"{i + 1}/{len(images)}", fill="black")
    return grid


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
