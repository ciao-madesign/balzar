"""Bridge between arbitrary uploaded image files and raw RGB pixel buffers.

This is the ONLY module in balzar allowed to depend on Pillow. Decoding
arbitrary PNG/JPEG/etc. containers from scratch is out of scope (JPEG alone
needs a full DCT/Huffman/chroma pipeline); reading them is a solved problem
we should not reinvent. The deterministic generation engine itself
(grid/rng/dsl/ops/interpreter/payload) stays pure stdlib.
"""

from __future__ import annotations

import io

from PIL import Image


def load_rgb(data: bytes, max_dim: int = 400) -> tuple[int, int, bytes]:
    """Decode arbitrary image bytes to (width, height, row-major RGB8).

    Images larger than `max_dim` on either side are downscaled (Lanczos)
    so the pure-Python encoder stays fast and predictable; this is
    disclosed to the caller via the returned dimensions.
    """
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    w, h = img.size
    if w > max_dim or h > max_dim:
        scale = max_dim / max(w, h)
        w, h = max(1, round(w * scale)), max(1, round(h * scale))
        img = img.resize((w, h), Image.LANCZOS)
    return w, h, img.tobytes()
