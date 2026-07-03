"""Deterministic interpreter: program text -> rendered frames.

The interpreter walks the parsed instruction tree, evaluates each argument
against the typed spec declared by the operation, and mutates the State.
Identical program + identical seed => bit-identical frames, always.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ops
from .dsl import Instr, Loop, eval_expr, eval_int, parse
from .grid import Region


@dataclass
class RenderResult:
    width: int
    height: int
    frames: list[bytes]                       # palette-indexed, row-major
    palette: dict[int, tuple[int, int, int]]

    @property
    def raw_rgb_size(self) -> int:
        """Size in bytes of the fully materialized RGB output."""
        return self.width * self.height * 3 * len(self.frames)

    def frame_rgb(self, index: int) -> bytes:
        lut = bytearray(256 * 3)
        for idx, (r, g, b) in self.palette.items():
            lut[idx * 3:idx * 3 + 3] = bytes((r, g, b))
        frame = self.frames[index]
        out = bytearray(len(frame) * 3)
        for i, v in enumerate(frame):
            out[i * 3:i * 3 + 3] = lut[v * 3:v * 3 + 3]
        return bytes(out)


def _parse_rgb(value: str) -> tuple[int, int, int]:
    if not value.startswith("#") or len(value) != 7:
        raise ValueError(f"expected #RRGGBB color, got '{value}'")
    n = int(value[1:], 16)
    return (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF


class Interpreter:
    MAX_STEPS = 50_000_000  # safety valve against runaway loops

    def __init__(self) -> None:
        self.state = ops.State()
        self._steps = 0

    def run(self, text: str) -> RenderResult:
        tree = parse(text)
        self._exec_block(tree, {})
        grid = self.state.require_grid()
        frames = self.state.frames
        if not frames:
            # no explicit FRAME: the final state is the single output frame
            frames = [grid.snapshot()]
        return RenderResult(
            width=grid.width,
            height=grid.height,
            frames=frames,
            palette=dict(self.state.palette),
        )

    # ------------------------------------------------------------ execution

    def _exec_block(self, block: list, env: dict[str, float]) -> None:
        for node in block:
            if isinstance(node, Loop):
                count = eval_int(node.count_expr, env)
                if count < 0:
                    raise ValueError(f"line {node.line}: negative LOOP count {count}")
                for k in range(count):
                    inner = dict(env)
                    inner[node.var] = k
                    self._exec_block(node.body, inner)
            else:
                self._exec_instr(node, env)

    def _exec_instr(self, instr: Instr, env: dict[str, float]) -> None:
        self._steps += 1
        if self._steps > self.MAX_STEPS:
            raise RuntimeError("instruction budget exceeded")
        entry = ops.REGISTRY.get(instr.name)
        if entry is None:
            raise ValueError(f"line {instr.line}: unknown instruction '{instr.name}'")
        func, spec = entry

        kwargs = {}
        try:
            for pname, (ptype, default) in spec.items():
                if pname in instr.args:
                    kwargs[pname] = self._coerce(instr.args[pname], ptype, env)
                elif ops.is_required(default):
                    raise ValueError(f"missing required argument '{pname}'")
                else:
                    kwargs[pname] = default
            for extra in instr.args:
                if extra not in spec:
                    raise ValueError(f"unknown argument '{extra}'")
            func(self.state, **kwargs)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"line {instr.line}: {instr.name}: {exc}") from None

    def _coerce(self, raw: str, ptype: str, env: dict[str, float]):
        if ptype == "int":
            return eval_int(raw, env)
        if ptype == "float":
            return float(eval_expr(raw, env))
        if ptype == "region":
            return self.state.region(raw)
        if ptype == "name":
            return raw
        if ptype == "str":
            return raw
        if ptype == "rgb":
            return _parse_rgb(raw)
        raise ValueError(f"internal: unknown param type '{ptype}'")


def render(text: str) -> RenderResult:
    """One-shot convenience: program text in, frames out."""
    return Interpreter().run(text)
