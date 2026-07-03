"""Palette-indexed pixel grid: the mutable state the interpreter acts on.

Every pixel is a palette index (0-255) stored in a flat bytearray. Keeping
the state index-based (instead of RGB) makes every transformation an exact
integer operation — no rounding, no interpolation, total determinism.
"""

from __future__ import annotations

# Classic 16-color default palette; programs override entries with PALETTE.
DEFAULT_PALETTE = {
    0: (0x00, 0x00, 0x00),
    1: (0xFF, 0xFF, 0xFF),
    2: (0xFF, 0x00, 0x00),
    3: (0x00, 0xFF, 0x00),
    4: (0x00, 0x00, 0xFF),
    5: (0xFF, 0xFF, 0x00),
    6: (0x00, 0xFF, 0xFF),
    7: (0xFF, 0x00, 0xFF),
    8: (0x80, 0x80, 0x80),
    9: (0xC0, 0xC0, 0xC0),
    10: (0x80, 0x00, 0x00),
    11: (0x00, 0x80, 0x00),
    12: (0x00, 0x00, 0x80),
    13: (0x80, 0x80, 0x00),
    14: (0x00, 0x80, 0x80),
    15: (0x80, 0x00, 0x80),
}


class Region:
    """Axis-aligned rectangle, always fully inside its canvas."""

    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h

    def validate(self, width: int, height: int, name: str) -> None:
        if self.w <= 0 or self.h <= 0:
            raise ValueError(f"region '{name}' has non-positive size {self.w}x{self.h}")
        if self.x < 0 or self.y < 0 or self.x + self.w > width or self.y + self.h > height:
            raise ValueError(
                f"region '{name}' ({self.x},{self.y},{self.w},{self.h}) "
                f"exceeds canvas {width}x{height}"
            )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Region({self.x},{self.y},{self.w},{self.h})"


class Grid:
    """Flat bytearray of palette indices, row-major."""

    def __init__(self, width: int, height: int, fill: int = 0) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid canvas size {width}x{height}")
        self.width = width
        self.height = height
        self.data = bytearray([fill & 0xFF]) * (width * height)

    def get(self, x: int, y: int) -> int:
        return self.data[y * self.width + x]

    def set(self, x: int, y: int, color: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.data[y * self.width + x] = color & 0xFF

    def extract(self, r: Region) -> list[bytearray]:
        """Snapshot a region as a list of rows (top to bottom)."""
        rows = []
        for j in range(r.h):
            start = (r.y + j) * self.width + r.x
            rows.append(bytearray(self.data[start:start + r.w]))
        return rows

    def blit(self, r: Region, rows: list[bytearray]) -> None:
        """Write a row-block back; the block must match the region size."""
        for j in range(r.h):
            start = (r.y + j) * self.width + r.x
            self.data[start:start + r.w] = rows[j]

    def snapshot(self) -> bytes:
        return bytes(self.data)

    def to_rgb(self, palette: dict[int, tuple[int, int, int]]) -> bytes:
        """Flatten to raw RGB8 bytes; unknown indices render as black."""
        lut = bytearray(256 * 3)
        for idx, (rr, gg, bb) in palette.items():
            lut[idx * 3:idx * 3 + 3] = bytes((rr, gg, bb))
        out = bytearray(self.width * self.height * 3)
        for i, v in enumerate(self.data):
            out[i * 3:i * 3 + 3] = lut[v * 3:v * 3 + 3]
        return bytes(out)
