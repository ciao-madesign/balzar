"""Command-line interface.

    python -m balzar render  program.bzr  -o out/
    python -m balzar encode  program.bzr  -o payload.bzp [--base64]
    python -m balzar decode  payload.bzp  -o program.bzr
    python -m balzar info    payload.bzp

`render` accepts either DSL source (.bzr) or a binary payload (.bzp,
detected via magic) and reports the expansion factor: bytes of generated
content per byte of payload.
"""

from __future__ import annotations

import argparse
import os
import sys

from .interpreter import render
from .payload import (MAGIC, QR_V40_BINARY_CAPACITY, decode_payload,
                      encode_payload, fits_in_qr, from_base64, to_base64)
from .png import write_png


def _fmt(n: float) -> str:
    """Thousands separator in Italian style (1.234.567)."""
    return f"{n:,.0f}".replace(",", ".")


def _load_program(path: str) -> tuple[str, bytes]:
    """Return (program_text, payload_bytes) from a .bzr or .bzp file."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] == MAGIC:
        return decode_payload(data), data
    text = data.decode("utf-8")
    stripped = text.strip()
    # a base64-encoded payload saved as text starts with b64(b"BZR1"...) = "QlpSM..."
    if stripped.startswith("QlpSM") and "\n" not in stripped:
        payload = from_base64(stripped)
        return decode_payload(payload), payload
    return text, encode_payload(text)


def cmd_render(args: argparse.Namespace) -> int:
    program, payload = _load_program(args.input)
    result = render(program)
    os.makedirs(args.output, exist_ok=True)

    stem = os.path.splitext(os.path.basename(args.input))[0]
    png_total = 0
    for i in range(len(result.frames)):
        if len(result.frames) == 1:
            name = f"{stem}.png"
        else:
            name = f"{stem}_{i:04d}.png"
        path = os.path.join(args.output, name)
        png_total += write_png(path, result.width, result.height,
                               result.frame_rgb(i))

    raw = result.raw_rgb_size
    factor = raw / len(payload)
    print(f"payload:     {len(payload)} byte "
          f"({'entra' if fits_in_qr(payload) else 'NON entra'} in un QR code, "
          f"limite {QR_V40_BINARY_CAPACITY} byte)")
    print(f"output:      {len(result.frames)} frame "
          f"{result.width}x{result.height} = {raw} byte RGB "
          f"({png_total} byte PNG)")
    print(f"espansione:  {_fmt(factor)}x (RGB generato / payload)")
    print(f"scritto in:  {args.output}/")
    return 0


def cmd_encode(args: argparse.Namespace) -> int:
    with open(args.input, "r", encoding="utf-8") as fh:
        program = fh.read()
    # validate before encoding: a payload must always be renderable
    render(program)
    payload = encode_payload(program)
    if args.base64:
        out = args.output or (os.path.splitext(args.input)[0] + ".bzp.b64")
        with open(out, "w", encoding="ascii") as fh:
            fh.write(to_base64(payload) + "\n")
    else:
        out = args.output or (os.path.splitext(args.input)[0] + ".bzp")
        with open(out, "wb") as fh:
            fh.write(payload)
    qr = "sì" if fits_in_qr(payload) else "no"
    print(f"{out}: {len(payload)} byte (QR singolo: {qr})")
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    program, _ = _load_program(args.input)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(program)
        print(f"scritto {args.output}")
    else:
        sys.stdout.write(program)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    program, payload = _load_program(args.input)
    result = render(program)
    raw = result.raw_rgb_size
    print(f"payload:      {len(payload)} byte")
    print(f"programma:    {len(program.encode('utf-8'))} byte canonici, "
          f"{len(program.splitlines())} istruzioni")
    print(f"canvas:       {result.width}x{result.height}, "
          f"{len(result.frames)} frame")
    print(f"output RGB:   {_fmt(raw)} byte")
    print(f"espansione:   {_fmt(raw / len(payload))}x")
    print(f"QR singolo:   {'sì' if fits_in_qr(payload) else 'no'} "
          f"(limite {QR_V40_BINARY_CAPACITY} byte)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="balzar",
        description="Generazione deterministica di contenuti da descrizioni minime",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("render", help="rigenera il contenuto da .bzr o payload")
    p.add_argument("input")
    p.add_argument("-o", "--output", default="out", help="directory di output")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("encode", help="programma DSL -> payload binario")
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--base64", action="store_true",
                   help="emette il payload in base64 (testo pronto per QR)")
    p.set_defaults(func=cmd_encode)

    p = sub.add_parser("decode", help="payload -> programma DSL canonico")
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None)
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("info", help="statistiche payload/espansione")
    p.add_argument("input")
    p.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, SyntaxError, OSError) as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
