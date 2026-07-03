"""Bridge between arbitrary uploaded image files and raw RGB pixel buffers.

This is the ONLY module in balzar allowed to depend on Pillow. Decoding
arbitrary PNG/JPEG/etc. containers from scratch is out of scope (JPEG alone
needs a full DCT/Huffman/chroma pipeline); reading them is a solved problem
we should not reinvent. The deterministic generation engine itself
(grid/rng/dsl/ops/interpreter/payload) stays pure stdlib.
"""

from __future__ import annotations

import io
import os

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
        # NEAREST, not Lanczos: smooth resampling would blend edge pixels
        # into hundreds of new intermediate colors, destroying exactly the
        # flat-region/tiling structure the encoder feeds on. Structured
        # content survives NEAREST intact; photos are the no-gain case
        # either way, so nothing of value is lost.
        img = img.resize((w, h), Image.NEAREST)
    return w, h, img.tobytes()


def load_frames(data: bytes, max_dim: int = 400,
                max_frames: int = 120) -> tuple[int, int, list[bytes]]:
    """Decode an animated image (GIF/APNG/...) to (w, h, [RGB frames]).

    Single-frame images yield a one-element list. All frames share the
    first frame's dimensions. `max_frames` caps runaway inputs.
    """
    from PIL import ImageSequence
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    if w > max_dim or h > max_dim:
        scale = max_dim / max(w, h)
        w, h = max(1, round(w * scale)), max(1, round(h * scale))

    frames: list[bytes] = []
    for frame in ImageSequence.Iterator(img):
        rgb = frame.convert("RGB")
        if rgb.size != (w, h):
            rgb = rgb.resize((w, h), Image.NEAREST)
        frames.append(rgb.tobytes())
        if len(frames) >= max_frames:
            break
    return w, h, frames


def save_gif(path: str, width: int, height: int,
             rgb_frames: list[bytes], fps: int = 12) -> int:
    """Write frames as an animated GIF (lossless for <=256-color content)."""
    images = [Image.frombytes("RGB", (width, height), f) for f in rgb_frames]
    images[0].save(
        path, save_all=True, append_images=images[1:],
        duration=max(20, round(1000 / fps)), loop=0, optimize=False,
    )
    return os.path.getsize(path)
