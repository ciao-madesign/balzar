"""Transformation engine: the deterministic operations of the DSL.

Each operation is registered with a typed parameter spec; the interpreter
evaluates raw DSL arguments (which may be arithmetic expressions over loop
variables) against that spec and invokes the function. All operations are
exact integer transformations on the palette grid.

Categories (see README §4):
  geometric     SHIFT, ROTATE, MIRROR, SCALE
  structural    COPY, SWAP, TILE, REGION
  differential  SETPIX, FILL, MAP, INVERT, FRAME
  generative    RECT, LINE, CIRCLE, NOISE, SCATTER, FRACTAL (seed-driven)
"""

from __future__ import annotations

from .grid import DEFAULT_PALETTE, Grid, Region
from .rng import DetRNG

# name -> (func, {param: (type, default | REQUIRED)})
REGISTRY: dict[str, tuple] = {}

_REQUIRED = object()

# param types understood by the interpreter:
#   int    arithmetic expression -> int
#   float  arithmetic expression -> float
#   region region name -> Region (FULL = whole canvas)
#   name   bare identifier, kept as string
#   rgb    #RRGGBB literal -> (r, g, b)


def op(name: str, /, **params):
    def deco(func):
        spec = {}
        for pname, p in params.items():
            if isinstance(p, tuple):
                spec[pname] = (p[0], p[1])
            else:
                spec[pname] = (p, _REQUIRED)
        REGISTRY[name] = (func, spec)
        return func

    return deco


def is_required(default) -> bool:
    return default is _REQUIRED


class State:
    """Everything the program mutates while it runs."""

    def __init__(self) -> None:
        self.grid: Grid | None = None
        self.regions: dict[str, Region] = {}
        self.palette: dict[int, tuple[int, int, int]] = dict(DEFAULT_PALETTE)
        self.rng = DetRNG(0)
        self.frames: list[bytes] = []

    def require_grid(self) -> Grid:
        if self.grid is None:
            raise ValueError("CANVAS must be declared before drawing operations")
        return self.grid

    def region(self, name: str) -> Region:
        if name == "FULL":
            g = self.require_grid()
            return Region(0, 0, g.width, g.height)
        try:
            return self.regions[name]
        except KeyError:
            raise ValueError(f"undefined region '{name}'") from None


# ---------------------------------------------------------------- directives


@op("CANVAS", w="int", h="int", bg=("int", 0))
def op_canvas(state: State, w: int, h: int, bg: int) -> None:
    if state.grid is not None:
        raise ValueError("CANVAS declared twice")
    state.grid = Grid(w, h, bg)


@op("PALETTE", i="int", rgb="rgb")
def op_palette(state: State, i: int, rgb: tuple[int, int, int]) -> None:
    if not 0 <= i <= 255:
        raise ValueError(f"palette index {i} out of range 0-255")
    state.palette[i] = rgb


@op("SEED", value="int")
def op_seed(state: State, value: int) -> None:
    state.rng = DetRNG(value)


@op("REGION", name="name", x="int", y="int", w="int", h="int")
def op_region(state: State, name: str, x: int, y: int, w: int, h: int) -> None:
    g = state.require_grid()
    r = Region(x, y, w, h)
    r.validate(g.width, g.height, name)
    state.regions[name] = r


@op("FRAME")
def op_frame(state: State) -> None:
    state.frames.append(state.require_grid().snapshot())


# ---------------------------------------------------------------- geometric


@op("SHIFT", region="region", dx="int", dy="int", wrap=("int", 1), fill=("int", 0))
def op_shift(state: State, region: Region, dx: int, dy: int, wrap: int, fill: int) -> None:
    """Translate the region content by (dx, dy); wrap=1 rolls around."""
    g = state.require_grid()
    src = g.extract(region)
    out = [bytearray([fill & 0xFF]) * region.w for _ in range(region.h)]
    for j in range(region.h):
        for i in range(region.w):
            ni, nj = i + dx, j + dy
            if wrap:
                ni %= region.w
                nj %= region.h
            elif not (0 <= ni < region.w and 0 <= nj < region.h):
                continue
            out[nj][ni] = src[j][i]
    g.blit(region, out)


@op("ROTATE", region="region", angle="int")
def op_rotate(state: State, region: Region, angle: int) -> None:
    """Rotate region content by 90/180/270 degrees clockwise, in place.

    Only right angles are allowed: arbitrary angles would need resampling
    and break the exact-integer contract. 90/270 require a square region.
    """
    g = state.require_grid()
    angle %= 360
    if angle == 0:
        return
    if angle not in (90, 180, 270):
        raise ValueError(f"ROTATE supports only 90/180/270, got {angle}")
    if angle in (90, 270) and region.w != region.h:
        raise ValueError("ROTATE 90/270 requires a square region")
    src = g.extract(region)
    n, m = region.w, region.h
    out = [bytearray(n) for _ in range(m)]
    for j in range(m):
        for i in range(n):
            if angle == 90:
                out[j][i] = src[n - 1 - i][j]
            elif angle == 180:
                out[j][i] = src[m - 1 - j][n - 1 - i]
            else:  # 270
                out[j][i] = src[i][m - 1 - j]
    g.blit(region, out)


@op("MIRROR", region="region", axis="name")
def op_mirror(state: State, region: Region, axis: str) -> None:
    """axis=x flips horizontally, axis=y flips vertically."""
    g = state.require_grid()
    src = g.extract(region)
    if axis == "x":
        out = [bytearray(reversed(row)) for row in src]
    elif axis == "y":
        out = list(reversed(src))
    else:
        raise ValueError(f"MIRROR axis must be x or y, got '{axis}'")
    g.blit(region, out)


@op("SCALE", src="region", dst="region")
def op_scale(state: State, src: Region, dst: Region) -> None:
    """Nearest-neighbour resample of src content into dst (exact integers)."""
    g = state.require_grid()
    block = g.extract(src)
    out = []
    for j in range(dst.h):
        sj = j * src.h // dst.h
        row = bytearray(dst.w)
        for i in range(dst.w):
            row[i] = block[sj][i * src.w // dst.w]
        out.append(row)
    g.blit(dst, out)


# ---------------------------------------------------------------- structural


@op("COPY", src="region", dst="region")
def op_copy(state: State, src: Region, dst: Region) -> None:
    if (src.w, src.h) != (dst.w, dst.h):
        raise ValueError("COPY requires src and dst of identical size")
    g = state.require_grid()
    g.blit(dst, g.extract(src))


@op("SWAP", a="region", b="region")
def op_swap(state: State, a: Region, b: Region) -> None:
    if (a.w, a.h) != (b.w, b.h):
        raise ValueError("SWAP requires regions of identical size")
    g = state.require_grid()
    block_a, block_b = g.extract(a), g.extract(b)
    g.blit(a, block_b)
    g.blit(b, block_a)


@op("TILE", src="region", dst="region")
def op_tile(state: State, src: Region, dst: Region) -> None:
    """Repeat the src pattern across the whole dst region."""
    g = state.require_grid()
    block = g.extract(src)
    out = []
    for j in range(dst.h):
        srow = block[j % src.h]
        row = bytearray(dst.w)
        for i in range(dst.w):
            row[i] = srow[i % src.w]
        out.append(row)
    g.blit(dst, out)


# -------------------------------------------------------------- differential


@op("SETPIX", x="int", y="int", color="int")
def op_setpix(state: State, x: int, y: int, color: int) -> None:
    state.require_grid().set(x, y, color)


@op("FILL", region="region", color="int")
def op_fill(state: State, region: Region, color: int) -> None:
    g = state.require_grid()
    row = bytearray([color & 0xFF]) * region.w
    g.blit(region, [bytearray(row) for _ in range(region.h)])


@op("MAP", region="region", src="int", dst="int")
def op_map(state: State, region: Region, src: int, dst: int) -> None:
    """Recolor: every pixel of value src becomes dst inside the region."""
    g = state.require_grid()
    block = g.extract(region)
    s, d = src & 0xFF, dst & 0xFF
    for row in block:
        for i, v in enumerate(row):
            if v == s:
                row[i] = d
    g.blit(region, block)


@op("INVERT", region="region", ncolors=("int", 16))
def op_invert(state: State, region: Region, ncolors: int) -> None:
    """color -> ncolors-1-color, the palette-space complement."""
    g = state.require_grid()
    block = g.extract(region)
    top = ncolors - 1
    for row in block:
        for i, v in enumerate(row):
            row[i] = (top - v) % 256
    g.blit(region, block)


# ---------------------------------------------------------------- generative


@op("RECT", x="int", y="int", w="int", h="int", color="int", fill=("int", 1))
def op_rect(state: State, x: int, y: int, w: int, h: int, color: int, fill: int) -> None:
    g = state.require_grid()
    if fill:
        for j in range(y, y + h):
            for i in range(x, x + w):
                g.set(i, j, color)
    else:
        for i in range(x, x + w):
            g.set(i, y, color)
            g.set(i, y + h - 1, color)
        for j in range(y, y + h):
            g.set(x, j, color)
            g.set(x + w - 1, j, color)


@op("LINE", x1="int", y1="int", x2="int", y2="int", color="int")
def op_line(state: State, x1: int, y1: int, x2: int, y2: int, color: int) -> None:
    """Bresenham line: exact integer rasterization."""
    g = state.require_grid()
    dx, dy = abs(x2 - x1), -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    x, y = x1, y1
    while True:
        g.set(x, y, color)
        if x == x2 and y == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


@op("CIRCLE", cx="int", cy="int", r="int", color="int", fill=("int", 0))
def op_circle(state: State, cx: int, cy: int, r: int, color: int, fill: int) -> None:
    """Midpoint circle; fill=1 draws horizontal spans instead of the outline."""
    g = state.require_grid()
    x, y, err = r, 0, 1 - r
    while x >= y:
        if fill:
            for px in range(cx - x, cx + x + 1):
                g.set(px, cy + y, color)
                g.set(px, cy - y, color)
            for px in range(cx - y, cx + y + 1):
                g.set(px, cy + x, color)
                g.set(px, cy - x, color)
        else:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y),
                           (-x, -y), (-y, -x), (y, -x), (x, -y)):
                g.set(cx + px, cy + py, color)
        y += 1
        if err < 0:
            err += 2 * y + 1
        else:
            x -= 1
            err += 2 * (y - x) + 1


@op("NOISE", region="region", color="int", density="float")
def op_noise(state: State, region: Region, color: int, density: float) -> None:
    """Seed-driven speckle: each pixel turns `color` with probability density."""
    g = state.require_grid()
    for j in range(region.y, region.y + region.h):
        for i in range(region.x, region.x + region.w):
            if state.rng.next_float() < density:
                g.set(i, j, color)


@op("SCATTER", region="region", color="int", count="int")
def op_scatter(state: State, region: Region, color: int, count: int) -> None:
    """Seed-driven placement of `count` points inside the region."""
    g = state.require_grid()
    for _ in range(count):
        i = region.x + state.rng.randint(region.w)
        j = region.y + state.rng.randint(region.h)
        g.set(i, j, color)


@op("FRACTAL", type="name", region="region", color=("int", 1),
    depth=("int", 5), cx=("float", -0.5), cy=("float", 0.0),
    scale=("float", 1.3), iter=("int", 32))
def op_fractal(state: State, type: str, region: Region, color: int,
               depth: int, cx: float, cy: float, scale: float, iter: int) -> None:
    g = state.require_grid()
    if type == "sierpinski":
        # Sierpinski carpet: mark cells whose base-3 coordinates never
        # share a middle digit. Region coords are mapped onto a 3^depth grid.
        size = 3 ** depth
        for j in range(region.h):
            rj = j * size // region.h
            for i in range(region.w):
                ri = i * size // region.w
                a, b, hole = ri, rj, False
                while a or b:
                    if a % 3 == 1 and b % 3 == 1:
                        hole = True
                        break
                    a //= 3
                    b //= 3
                if not hole:
                    g.set(region.x + i, region.y + j, color)
    elif type == "triangle":
        # Sierpinski triangle via the bitwise-AND rule on a 2^depth grid.
        size = 2 ** depth
        for j in range(region.h):
            rj = j * size // region.h
            for i in range(region.w):
                ri = i * size // region.w
                if (ri & rj) == 0:
                    g.set(region.x + i, region.y + j, color)
    elif type == "mandelbrot":
        # Escape-time Mandelbrot; IEEE-754 doubles make this reproducible
        # bit-for-bit across CPython builds.
        ncol = max(2, len(state.palette))
        for j in range(region.h):
            ci = cy + (j - region.h / 2) * (2.0 * scale / region.h)
            for i in range(region.w):
                cr = cx + (i - region.w / 2) * (2.0 * scale / region.w)
                zr = zi = 0.0
                k = 0
                while k < iter and zr * zr + zi * zi <= 4.0:
                    zr, zi = zr * zr - zi * zi + cr, 2.0 * zr * zi + ci
                    k += 1
                if k < iter:
                    g.set(region.x + i, region.y + j, k % ncol)
                else:
                    g.set(region.x + i, region.y + j, color)
    else:
        raise ValueError(f"unknown fractal type '{type}'")
