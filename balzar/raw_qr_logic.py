"""Raw byte QR transport — the logic behind `balzar chunks --raw`/`scan
--raw` (CLI, CLAUDE.md §2.4c) and the "Trasporto file (QR)" desktop
window (balzar/raw_qr_gui.py) and web page (trasporto-qr.html/.js). Zero
Tkinter here on purpose: this module stays importable without Tk (the
Python running the test suite doesn't have it — see CLAUDE.md §10),
unlike balzar/raw_qr_gui.py which needs it for the widgets.

Pure slicing/reassembly via balzar/qr.py's BZC1 chunking, agnostic to
content — never touches balzar's generative engine, no compression
attempted, the reconstructed file is bit-identical to the original.
"""

from __future__ import annotations

import os


def encode_file_to_qr_frames(path: str, grid_dim: int, out_dir: str) -> tuple[int, int]:
    """Read raw bytes from `path`, write a QR frame sequence (PNGs) into
    `out_dir`. Returns (n_frames, n_bytes)."""
    from .qr import payload_to_qr_frames

    with open(path, "rb") as fh:
        data = fh.read()
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    frames = payload_to_qr_frames(data, grid_dim=grid_dim)
    for i, img in enumerate(frames):
        img.save(os.path.join(out_dir, f"{stem}_qr_frame_{i + 1:03d}.png"))
    return len(frames), len(data)


class RawQrAssembler:
    """Thin stateful wrapper around balzar.qr.LiveScanner: remembers
    which image paths have already been fed in, so re-running over a
    file list that only grew (the common case: user adds more photos
    after seeing "capitoli mancanti") doesn't re-decode images whose
    chunks are already accounted for."""

    def __init__(self):
        from .qr import LiveScanner
        self._scanner = LiveScanner()
        self._done_paths: set[str] = set()
        self._last_status: tuple[bool, list[int] | None] = (False, None)

    def add_image(self, path: str, grid_dim: int | None = None):
        """Returns (complete, missing) — same shape as LiveScanner.add.
        A path already processed is a no-op (not re-read, not an error):
        returns the last known status instead of decoding it again."""
        if path in self._done_paths:
            return self._last_status
        with open(path, "rb") as fh:
            data = fh.read()
        self._last_status = self._scanner.add(data, grid_dim=grid_dim)
        self._done_paths.add(path)
        return self._last_status

    def result(self) -> bytes:
        return self._scanner.result()
