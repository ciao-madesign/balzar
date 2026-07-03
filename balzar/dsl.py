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
over loop variables (no spaces inside an expression).
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


def _strip_comment(raw: str) -> str:
    # '#' starts a comment only at line start or after whitespace, so
    # #RRGGBB color literals (as in rgb=#FF0000) are left intact
    for i, ch in enumerate(raw):
        if ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            return raw[:i]
    return raw


def _split_line(raw: str) -> list[str]:
    # strip comments, treat ( ) , as whitespace so both syntaxes work
    code = _strip_comment(raw)
    for ch in "(),":
        code = code.replace(ch, " ")
    return code.split()


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
        lines.append(" ".join([head] + tokens[1:]))
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
