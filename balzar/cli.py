"""Command-line interface.

    python -m balzar render        program.bzr   -o out/
    python -m balzar encode        program.bzr   -o payload.bzp [--base64]
    python -m balzar encode-image  photo.png      -o payload.bzp [--max-dim N]
    python -m balzar encode-vector drawing.svg    -o payload.bzp [--max-dim N]
    python -m balzar encode-sequence step1.dxf step2.dxf ... -o payload.bzp
    python -m balzar encode-sequence a.svg b.dxf c.png --mode independent -o outdir/
    python -m balzar explode-vector drawing.dxf   -o payload.bzp [--steps N]
    python -m balzar decode        payload.bzp    -o program.bzr
    python -m balzar info          payload.bzp

`render` accepts either DSL source (.bzr) or a binary payload (.bzp,
detected via magic) and reports the expansion factor: bytes of generated
content per byte of payload. `encode-image` runs the best-effort automatic
raster encoder (balzar/encoder.py) on an arbitrary image file (requires
Pillow). `encode-vector` ingests an SVG/DXF file directly (balzar/vectorio.py,
stdlib only) — no rasterization, no quantization, exact geometry.
`encode-sequence` combines several files (homogeneous SVG/DXF, or raster
images) into one multi-frame payload (balzar/sequence.py), or with
`--mode independent` encodes each file on its own (no format restriction,
one broken file doesn't sink the batch). `explode-vector`
auto-generates an exploded-view animation from a single multi-layer CAD/SVG
file, grouping by layer/`<g>` (balzar/explode.py).
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

    if args.gif and len(result.frames) > 1:
        from .imageio import save_gif
        gif_path = os.path.join(args.output, f"{stem}.gif")
        frames = [result.frame_rgb(i) for i in range(len(result.frames))]
        size = save_gif(gif_path, result.width, result.height, frames,
                        fps=args.fps)
        print(f"gif:         {gif_path} ({size} byte)")

    if args.svg:
        from .svg import UnsupportedForSVG, render_svg
        try:
            svg_text = render_svg(program)
            svg_path = os.path.join(args.output, f"{stem}.svg")
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(svg_text)
            print(f"svg:         {svg_path} ({len(svg_text)} byte, vettoriale reale)")
        except UnsupportedForSVG as exc:
            print(f"svg:         non disponibile — {exc}", file=sys.stderr)

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


def cmd_encode_image(args: argparse.Namespace) -> int:
    try:
        from .imageio import load_rgb
    except ImportError:
        print("errore: encode-image richiede Pillow (pip install pillow)",
              file=sys.stderr)
        return 1
    from .encoder import encode_image

    with open(args.input, "rb") as fh:
        data = fh.read()
    w, h, rgb = load_rgb(data, max_dim=args.max_dim)
    result = encode_image(w, h, rgb)

    out = args.output or (os.path.splitext(args.input)[0] + ".bzp")
    with open(out, "wb") as fh:
        fh.write(result.payload)

    raw = w * h * 3
    fedelta = result.fidelity_label()
    print(f"immagine:     {w}x{h} ({args.max_dim}px lato massimo), "
          f"{result.palette_size} colori, fedelta' {fedelta}")
    print(f"tiling:       {'trovato ' + str(result.tile) if result.tile else 'non trovato'}")
    print(f"istruzioni:   {result.instruction_count}")
    print(f"payload:      {out}: {len(result.payload)} byte "
          f"(QR singolo: {'si' if fits_in_qr(result.payload) else 'no'})")
    if len(result.payload) < raw:
        print(f"guadagno:     {_fmt(raw / len(result.payload))}x rispetto al raw RGB ({raw} byte)")
    else:
        print(f"guadagno:     NESSUNO - payload piu' grande del raw RGB ({raw} byte). "
              f"Contenuto poco strutturato per questo encoder.")
    return 0


def cmd_encode_vector(args: argparse.Namespace) -> int:
    from .vectorio import VectorIngestError, ingest_vector_file

    try:
        result = ingest_vector_file(args.input, max_dim=args.max_dim)
    except VectorIngestError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1

    out = args.output or (os.path.splitext(args.input)[0] + ".bzp")
    with open(out, "wb") as fh:
        fh.write(result.payload)

    print(f"sorgente:     {args.input} ({result.source_format.upper()}), "
          f"{result.width}x{result.height}")
    print(f"elementi:     {result.element_count} convertiti, "
          f"{len(result.skipped)} saltati")
    for reason in result.skipped:
        print(f"  saltato:    {reason}")
    print(f"istruzioni:   {result.instruction_count}")
    print(f"payload:      {out}: {len(result.payload)} byte "
          f"(QR singolo: {'si' if fits_in_qr(result.payload) else 'no'})")
    return 0


def cmd_encode_3d(args: argparse.Namespace) -> int:
    """3DXML -> payload binario BZM1 (balzar/scene3d.py): quantizzazione
    int16 dei vertici, indici a 16 bit, codifica compatta delle rotazioni
    allineate agli assi -- vedi CLAUDE.md SS9 per le misure reali."""
    from .scene3d import Scene3DError, encode_3dxml_file

    try:
        result = encode_3dxml_file(args.input)
    except Scene3DError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1

    out = args.output or (os.path.splitext(args.input)[0] + ".b3d")
    with open(out, "wb") as fh:
        fh.write(result.payload)

    print(f"sorgente:     {args.input}")
    print(f"forme uniche: {result.shape_count}")
    print(f"riferimenti:  {_fmt(result.reference_count)}")
    print(f"istanze:      {_fmt(result.instance_count)}")
    print(f"errore medio vertici (quantizzazione int16): {result.mean_vertex_error}")
    print(f"vertici:      {_fmt(result.vertex_count)}")
    print(f"payload:      {out}: {_fmt(len(result.payload))} byte "
          f"(QR singolo: {'si' if fits_in_qr(result.payload) else 'no'})")
    return 0


def cmd_render_3d(args: argparse.Namespace) -> int:
    """Payload BZM1 -> file .glb (balzar/gltf.py), visualizzabile con
    qualunque viewer glTF (es. <model-viewer> nel browser)."""
    from .gltf import scene3d_to_glb
    from .scene3d import Scene3DError, decode_payload

    with open(args.input, "rb") as fh:
        data = fh.read()
    try:
        scene = decode_payload(data)
    except Scene3DError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1

    glb = scene3d_to_glb(scene)
    out = args.output or (os.path.splitext(args.input)[0] + ".glb")
    with open(out, "wb") as fh:
        fh.write(glb)
    print(f"glb:          {out} ({_fmt(len(glb))} byte)")
    return 0


def cmd_encode_video(args: argparse.Namespace) -> int:
    try:
        from .imageio import load_frames
    except ImportError:
        print("errore: encode-video richiede Pillow (pip install pillow)",
              file=sys.stderr)
        return 1
    from .video import encode_video

    with open(args.input, "rb") as fh:
        data = fh.read()
    w, h, frames = load_frames(data, max_dim=args.max_dim,
                               max_frames=args.max_frames)
    result = encode_video(w, h, frames)

    out = args.output or (os.path.splitext(args.input)[0] + ".bzp")
    with open(out, "wb") as fh:
        fh.write(result.payload)

    raw = w * h * 3 * result.frame_count
    fedelta = "esatta (lossless)" if result.lossless else "quantizzata (lossy)"
    print(f"video:        {w}x{h}, {result.frame_count} frame, "
          f"{result.palette_size} colori, fedelta' {fedelta}")
    print(f"delta:        {_fmt(result.delta_pixels_total)} pixel cambiati "
          f"dopo il frame iniziale")
    print(f"istruzioni:   {_fmt(result.instruction_count)}")
    print(f"payload:      {out}: {_fmt(len(result.payload))} byte")
    if len(result.payload) < raw:
        print(f"guadagno:     {_fmt(raw / len(result.payload))}x "
              f"rispetto al raw RGB ({_fmt(raw)} byte)")
    else:
        print(f"guadagno:     NESSUNO - payload piu' grande del raw RGB "
              f"({_fmt(raw)} byte)")
    return 0


def cmd_encode_sequence(args: argparse.Namespace) -> int:
    from .sequence import SequenceError

    if args.mode == "independent":
        return _cmd_encode_independent(args)

    exts = {os.path.splitext(p)[1].lower() for p in args.inputs}
    is_vector = exts <= {".svg", ".dxf"}

    try:
        if is_vector:
            from .sequence import encode_vector_sequence
            result = encode_vector_sequence(args.inputs, max_dim=args.max_dim)
            raw = None
        else:
            try:
                from .sequence import encode_raster_sequence
            except ImportError:
                print("errore: encode-sequence su immagini raster richiede Pillow "
                      "(pip install pillow)", file=sys.stderr)
                return 1
            result = encode_raster_sequence(args.inputs, max_dim=args.max_dim)
            raw = result.width * result.height * 3 * result.frame_count
    except SequenceError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1

    out = args.output or (os.path.splitext(args.inputs[0])[0] + "_sequenza.bzp")
    with open(out, "wb") as fh:
        fh.write(result.payload)

    print(f"sequenza:     {len(args.inputs)} file -> {result.frame_count} frame, "
          f"{result.width}x{result.height}")
    if hasattr(result, "skipped"):
        print(f"elementi:     {len(result.skipped)} saltati")
        for reason in result.skipped:
            print(f"  saltato:    {reason}")
    print(f"istruzioni:   {result.instruction_count}")
    print(f"payload:      {out}: {_fmt(len(result.payload))} byte "
          f"(QR singolo: {'si' if fits_in_qr(result.payload) else 'no'})")
    if raw is not None:
        if len(result.payload) < raw:
            print(f"guadagno:     {_fmt(raw / len(result.payload))}x "
                  f"rispetto al raw RGB ({_fmt(raw)} byte)")
        else:
            print(f"guadagno:     NESSUNO - payload piu' grande del raw RGB "
                  f"({_fmt(raw)} byte)")
    return 0


def _cmd_encode_independent(args: argparse.Namespace) -> int:
    """--mode independent: every file gets its own payload, written next
    to the source (or into -o if given as a directory) — no shared
    canvas, no delta, files don't need to share a format."""
    from .sequence import SequenceError, encode_independent

    try:
        results = encode_independent(args.inputs, max_dim=args.max_dim)
    except SequenceError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1

    out_dir = None
    if args.output:
        out_dir = args.output
        os.makedirs(out_dir, exist_ok=True)

    n_ok = sum(1 for r in results if r.ok)
    print(f"batch indipendente: {len(results)} file, {n_ok} codificati, "
          f"{len(results) - n_ok} falliti")
    for path, result in zip(args.inputs, results):
        stem = os.path.splitext(os.path.basename(path))[0]
        if not result.ok:
            print(f"  {result.filename}: ERRORE - {result.error}")
            continue
        target_dir = out_dir or os.path.dirname(path) or "."
        out_path = os.path.join(target_dir, f"{stem}.bzp")
        with open(out_path, "wb") as fh:
            fh.write(result.payload)
        print(f"  {result.filename} ({result.source_format}): "
              f"{result.instruction_count} istruzioni, {_fmt(len(result.payload))} byte "
              f"(QR singolo: {'si' if fits_in_qr(result.payload) else 'no'}) -> {out_path}")
    return 0


def cmd_explode_vector(args: argparse.Namespace) -> int:
    from .explode import ExplodeError, explode_vector_file

    try:
        result = explode_vector_file(args.input, steps=args.steps,
                                     spacing=args.spacing, max_dim=args.max_dim)
    except ExplodeError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1

    out = args.output or (os.path.splitext(args.input)[0] + "_esploso.bzp")
    with open(out, "wb") as fh:
        fh.write(result.payload)

    print(f"sorgente:     {args.input} ({result.source_format.upper()}), "
          f"{result.width}x{result.height}")
    print(f"gruppi:       {result.group_count} layer/gruppi esplosi in "
          f"{result.frame_count} frame")
    if result.skipped:
        print(f"elementi:     {len(result.skipped)} saltati")
        for reason in result.skipped:
            print(f"  saltato:    {reason}")
    print(f"istruzioni:   {result.instruction_count}")
    print(f"payload:      {out}: {_fmt(len(result.payload))} byte "
          f"(QR singolo: {'si' if fits_in_qr(result.payload) else 'no'})")
    return 0


def cmd_chunks(args: argparse.Namespace) -> int:
    _, payload = _load_program(args.input)
    os.makedirs(args.output, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.input))[0]

    if args.qr:
        try:
            from .qr import payload_to_qr_image
        except ImportError:
            print("errore: --qr richiede i pacchetti 'qrcode' e 'Pillow' "
                  "(pip install qrcode pillow)", file=sys.stderr)
            return 1
        img = payload_to_qr_image(payload)
        path = os.path.join(args.output, f"{stem}_qr.png")
        img.save(path)
        print(f"immagine QR scritta in {path} ({img.size[0]}x{img.size[1]}px) "
              f"— singolo QR o griglia auto-dimensionata a seconda della "
              f"dimensione del payload; scansionala con 'balzar scan'")
        return 0

    from .payload import chunk_payload
    chunks = chunk_payload(payload, chunk_size=args.size)
    for i, chunk in enumerate(chunks):
        name = os.path.join(args.output, f"{stem}_qr_{i + 1:03d}.txt")
        with open(name, "w", encoding="ascii") as fh:
            fh.write(to_base64(chunk) + "\n")
    print(f"{len(chunks)} capitoli in {args.output}/ "
          f"(max {args.size} byte l'uno: stampabili come un QR ciascuno; "
          f"usa --qr per generare l'immagine QR direttamente)")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    try:
        from .qr import scan_image_file
    except ImportError:
        print("errore: scan richiede i pacchetti 'pyzbar' (+ libzbar0 di "
              "sistema) e 'Pillow' (pip install pyzbar pillow)", file=sys.stderr)
        return 1
    from .payload import PayloadError

    try:
        payload = scan_image_file(args.input)
    except PayloadError as exc:
        print(f"errore: {exc} — riprova la scansione", file=sys.stderr)
        return 1

    out = args.output or (os.path.splitext(args.input)[0] + ".bzp")
    with open(out, "wb") as fh:
        fh.write(payload)
    print(f"payload ricostruito: {out} ({_fmt(len(payload))} byte)")

    if args.render:
        program = decode_payload(payload)
        result = render(program)
        os.makedirs(args.render, exist_ok=True)
        stem = os.path.splitext(os.path.basename(args.input))[0]
        for i in range(len(result.frames)):
            name = f"{stem}.png" if len(result.frames) == 1 else f"{stem}_{i:04d}.png"
            write_png(os.path.join(args.render, name), result.width,
                     result.height, result.frame_rgb(i))
        print(f"rigenerato in {args.render}/ "
              f"({len(result.frames)} frame {result.width}x{result.height})")
    return 0


def cmd_assemble(args: argparse.Namespace) -> int:
    from .payload import assemble_chunks
    chunks = []
    for name in sorted(os.listdir(args.input)):
        path = os.path.join(args.input, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        if data[:4] == b"BZC1":
            chunks.append(data)
        else:
            chunks.append(from_base64(data.decode("ascii")))
    payload = assemble_chunks(chunks)
    with open(args.output, "wb") as fh:
        fh.write(payload)
    print(f"riassemblato {args.output}: {_fmt(len(payload))} byte "
          f"da {len(chunks)} capitoli (integrita' verificata)")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import main as gui_main
    gui_main()
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
    p.add_argument("--gif", action="store_true",
                   help="scrive anche una GIF animata (richiede Pillow)")
    p.add_argument("--fps", type=int, default=12)
    p.add_argument("--svg", action="store_true",
                   help="prova anche l'export SVG vettoriale (funziona solo "
                        "per il sottoinsieme di op vettoriali, vedi balzar/svg.py)")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("encode", help="programma DSL -> payload binario")
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--base64", action="store_true",
                   help="emette il payload in base64 (testo pronto per QR)")
    p.set_defaults(func=cmd_encode)

    p = sub.add_parser("encode-image",
                       help="immagine arbitraria -> payload (encoder automatico best-effort)")
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--max-dim", type=int, default=400,
                   help="lato massimo dopo il ridimensionamento (default 400)")
    p.set_defaults(func=cmd_encode_image)

    p = sub.add_parser("encode-vector",
                       help="SVG/DXF -> payload (ingestione diretta, no raster)")
    p.add_argument("input", help="file .svg o .dxf")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--max-dim", type=int, default=800,
                   help="lato massimo del canvas generato (default 800)")
    p.set_defaults(func=cmd_encode_vector)

    p = sub.add_parser("encode-3d",
                       help="3DXML -> payload binario BZM1 (assiemi CAD parametrici)")
    p.add_argument("input", help="file .3dxml")
    p.add_argument("-o", "--output", default=None)
    p.set_defaults(func=cmd_encode_3d)

    p = sub.add_parser("render-3d",
                       help="payload BZM1 -> file .glb visualizzabile (balzar/gltf.py)")
    p.add_argument("input", help="file .b3d (payload BZM1)")
    p.add_argument("-o", "--output", default=None)
    p.set_defaults(func=cmd_render_3d)

    p = sub.add_parser("encode-video",
                       help="GIF animata/sequenza -> payload (delta tra frame)")
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--max-dim", type=int, default=400)
    p.add_argument("--max-frames", type=int, default=120)
    p.set_defaults(func=cmd_encode_video)

    p = sub.add_parser("encode-sequence",
                       help="piu' file -> un payload multi-frame, o file indipendenti "
                            "(--mode independent)")
    p.add_argument("inputs", nargs="+", help="2+ file .svg/.dxf (stesso formato) o immagini raster")
    p.add_argument("-o", "--output", default=None,
                   help="file di output (modo sequence) o directory (modo independent)")
    p.add_argument("--max-dim", type=int, default=800,
                   help="lato massimo del canvas generato (default 800)")
    p.add_argument("--mode", choices=["sequence", "independent"], default="sequence",
                   help="'sequence' (default): un payload multi-frame navigabile, i file "
                        "devono condividere il formato. 'independent': un payload per "
                        "file, nessun vincolo di formato, un file rotto non blocca gli altri")
    p.set_defaults(func=cmd_encode_sequence)

    p = sub.add_parser("explode-vector",
                       help="CAD/SVG a piu' layer -> esploso automatico multi-frame")
    p.add_argument("input", help="file .svg o .dxf con piu' di un layer/gruppo")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--steps", type=int, default=6,
                   help="frame di esplosione dopo quello assemblato (default 6)")
    p.add_argument("--spacing", type=float, default=0.6,
                   help="frazione della distanza dal centro aggiunta per step (default 0.6)")
    p.add_argument("--max-dim", type=int, default=800)
    p.set_defaults(func=cmd_explode_vector)

    p = sub.add_parser("decode", help="payload -> programma DSL canonico")
    p.add_argument("input")
    p.add_argument("-o", "--output", default=None)
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("chunks",
                       help="payload -> capitoli QR-sized (supporto fisico)")
    p.add_argument("input")
    p.add_argument("-o", "--output", default="chunks")
    p.add_argument("--size", type=int, default=2953,
                   help="byte per capitolo (default: capacita' QR v40)")
    p.add_argument("--qr", action="store_true",
                   help="genera l'immagine QR reale (1 codice o griglia "
                        "auto-dimensionata) invece del testo base64")
    p.set_defaults(func=cmd_chunks)

    p = sub.add_parser("scan",
                       help="foto di 1 QR o una griglia -> payload ricostruito")
    p.add_argument("input", help="immagine (foto/screenshot) con 1 o piu' QR")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--render", default=None, metavar="DIR",
                   help="rigenera subito il contenuto in questa directory")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("assemble",
                       help="cartella di capitoli -> payload ricostruito")
    p.add_argument("input", help="directory con i file capitolo")
    p.add_argument("-o", "--output", default="assembled.bzp")
    p.set_defaults(func=cmd_assemble)

    p = sub.add_parser("gui", help="apre l'applicazione desktop")
    p.set_defaults(func=cmd_gui)

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
