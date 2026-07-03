"""DSL parser and deterministic expression evaluator.

Program syntax — one instruction per line:

    # comment
    CANVAS w=256 h=256
    REGION name=A x=0 y=0 w=32 h=32
    LOOP var=i count=8
      SHIFT(region=A, dx=i*4, dy=1)
    ENDLOOP

Arguments are key=value pairs; parentheses and commas are optional sugar,
so the spec form `SHIFT(region=A, dx=2, dy=1)` parses identically to
`SHIFT region=A dx=2 dy=1`. Numeric values may be arithmetic expressions
over loop variables (no spaces inside an expression). A value may be
double-quoted to contain spaces/parentheses/commas verbatim (needed for
TEXT content), e.g. `TEXT x=1 y=1 text="QTY 12" color=0`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class Instr:
    name: str
    args: dict[str, str]
    line: int


@dataclass
class Loop:
    var: str
    count_expr: str
    body: list = field(default_factory=list)
    line: int = 0


def _split_line(raw: str) -> list[str]:
    """Tokenize one line: whitespace/`(),` separated, '#' comments, and
    "..." strings that keep everything (spaces, #, parens) verbatim.

    '#' only starts a comment when it begins a fresh token — mid-token it
    is data (rgb=#FF0000); quotes are stripped from the emitted token.
    """
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    for ch in raw:
        if in_quotes:
            if ch == '"':
                in_quotes = False
            else:
                current.append(ch)
            continue
        if ch == '"':
            in_quotes = True
            continue
        if ch == "#" and not current:
            break
        if ch in " \t(),":
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(ch)
    if in_quotes:
        raise SyntaxError(f"unterminated quoted string in line: {raw!r}")
    if current:
        tokens.append("".join(current))
    return tokens


def parse(text: str) -> list:
    """Parse program text into a tree of Instr / Loop nodes."""
    root: list = []
    stack: list[list] = [root]
    loop_stack: list[Loop] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        tokens = _split_line(raw)
        if not tokens:
            continue
        name = tokens[0].upper()
        args: dict[str, str] = {}
        for tok in tokens[1:]:
            if "=" not in tok:
                raise SyntaxError(f"line {lineno}: expected key=value, got '{tok}'")
            key, value = tok.split("=", 1)
            if not key or not value:
                raise SyntaxError(f"line {lineno}: malformed argument '{tok}'")
            args[key.lower()] = value

        if name == "LOOP":
            if "var" not in args or "count" not in args:
                raise SyntaxError(f"line {lineno}: LOOP requires var= and count=")
            loop = Loop(var=args["var"], count_expr=args["count"], line=lineno)
            stack[-1].append(loop)
            stack.append(loop.body)
            loop_stack.append(loop)
        elif name == "ENDLOOP":
            if not loop_stack:
                raise SyntaxError(f"line {lineno}: ENDLOOP without LOOP")
            loop_stack.pop()
            stack.pop()
        else:
            stack[-1].append(Instr(name=name, args=args, line=lineno))

    if loop_stack:
        raise SyntaxError(f"line {loop_stack[-1].line}: LOOP without ENDLOOP")
    return root


_NEEDS_QUOTING = set(' \t#(),')


def _requote(tok: str) -> str:
    """Re-wrap a value in quotes if it contains characters that would
    otherwise be split/mangled on the next parse (round-trip safety)."""
    if "=" not in tok:
        return tok
    key, value = tok.split("=", 1)
    if any(c in _NEEDS_QUOTING for c in value):
        return f'{key}="{value}"'
    return tok


def canonical(text: str) -> str:
    """Normalized program form: what actually gets encoded in the payload.

    Comments and blank lines are dropped and whitespace collapsed, so two
    cosmetically different sources produce byte-identical payloads.
    """
    lines = []
    for raw in text.splitlines():
        tokens = _split_line(raw)
        if not tokens:
            continue
        head = tokens[0].upper()
        lines.append(" ".join([head] + [_requote(t) for t in tokens[1:]]))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------- expressions

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                   ast.Mod, ast.Pow)


def _eval_node(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"unknown variable '{node.id}'")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval_node(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        a = _eval_node(node.left, env)
        b = _eval_node(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        if isinstance(node.op, ast.FloorDiv):
            return a // b
        if isinstance(node.op, ast.Mod):
            return a % b
        return a ** b
    raise ValueError(f"disallowed expression element: {ast.dump(node)}")


def eval_expr(expr: str, env: dict[str, float]) -> float:
    """Evaluate an arithmetic expression over loop variables.

    Only literals, variables and + - * / // % ** are accepted: the
    expression language is total and side-effect free by construction.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression '{expr}': {exc}") from None
    return _eval_node(tree, env)


def eval_int(expr: str, env: dict[str, float]) -> int:
    v = eval_expr(expr, env)
    # floor(v + 0.5): explicit, platform-independent rounding
    import math
    return int(math.floor(v + 0.5))
