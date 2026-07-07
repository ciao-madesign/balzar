"""Desktop app: the offline product. Open a file, compress it, save it —
like a zipper, but what you save is a generative program, not data.

    python3 -m balzar gui

Pure stdlib Tkinter + Pillow (already required by the encoder), so the
whole thing packages into a single executable with PyInstaller:

    pyinstaller --onefile --windowed --name balzar balzar-app.py

Accepts images (PNG/JPEG/BMP/...), animated GIFs (encoded as a delta
video, README §4.3), .bzp payloads (decoded and re-rendered), 3DXML CAD
assemblies (encoded to the BZM1 payload, balzar/scene3d.py — no 2D
preview exists for these, see Job.is_3d; "Visualizza in 3D" opens the
regenerated assembly + bill of materials in the system browser via
balzar/viewer3d.py) and .b3d payloads. Encoding runs in a worker thread
so the window never freezes.
"""

from __future__ import annotations

import base64
import os
import queue
import threading
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk

from .imageio import load_frames, save_gif
from .interpreter import render as render_program
from .payload import (MAGIC, QR_V40_BINARY_CAPACITY, chunk_payload,
                      decode_payload, fits_in_qr)
from .png import png_bytes

PREVIEW_MAX = 380  # px, per pane


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", ".")


class Job:
    """Everything produced by one encode/decode, consumed by the UI."""

    def __init__(self):
        # stable per-instance id, independent of the library (see
        # library_entry_id below) -- lets view_3d() dedup repeat clicks
        # on a job that was never saved to the library at all (a fresh
        # Balzar Studio encode), not only on library-backed ones
        self.id = uuid.uuid4().hex[:12]
        self.source_name = ""
        self.width = 0
        self.height = 0
        self.frames_rgb: list[bytes] = []      # regenerated frames (RGB)
        self.original_frames_rgb: list[bytes] = []
        self.payload = b""
        self.program_text = ""
        self.stats: list[tuple[str, str]] = []
        self.error: str | None = None
        # 3D assemblies (scene3d.py) have no 2D preview at all -- Tkinter
        # can't show a GLB, so the canvases show a text hint instead and
        # "Visualizza in 3D" opens the system browser (balzar/viewer3d.py)
        self.is_3d = False
        self.glb = b""
        self.bom_lines: list[tuple[str, int, list[str]]] = []
        # a multi-document bundle (balzar/bundle.py) is still a 3D job
        # (is_3d=True) but saves as .bzx instead of .b3d, and carries its
        # own alarm table -- distinct from BalzarApp.alarm_rows, which is
        # loaded manually via "Carica tabella allarmi" and applies across
        # whichever job is open
        self.is_bundle = False
        self.alarm_rows: list[tuple[str, str]] = []
        # True only for a job that decoded/scanned an EXISTING artifact
        # (Balzar Live's consumption side) -- opening a .b3d/.bzx/.bzp or
        # scanning a QR photo -- never for a fresh encode (Balzar Studio:
        # a .3dxml or raster image), which the user already saves
        # explicitly if they want to keep it. Gates the library
        # auto-save in _poll_queue.
        self.is_live_artifact = False
        # set once this job has a library entry (auto-saved, or opened
        # FROM the library panel) -- lets view_3d() reuse an already-
        # running viewer server for the same entry instead of leaking a
        # new one on every click
        self.library_entry_id: str | None = None


class BalzarApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("balzar — compressione generativa deterministica")
        root.minsize(900, 640)

        self.queue: queue.Queue[Job] = queue.Queue()
        self.job: Job | None = None
        self._anim_after: str | None = None
        self._anim_index = 0
        self._frame_count = 1
        self._playing = True
        self._photo_refs: list = []  # keep PhotoImage references alive
        # alarm code -> component name table for the 3D viewer's search bar
        # (balzar/viewer3d.py); independent of which job is loaded -- a
        # technician can load this once and reuse it across several 3D
        # files, so it lives on the app, not on Job.
        self.alarm_rows: list[tuple[str, str]] = []
        # library entry id (or a job's own fallback id) -> (the running
        # http.server.HTTPServer already serving it, its temp work_dir)
        # (balzar/library.py) -- avoids spawning a second
        # HTTPServer+browser-tab pair for content already open when the
        # operator switches away and back via the library panel, and
        # lets "Chiudi visualizzazione" shut the right one down and
        # remove its temp directory
        self._open_viewers: dict = {}  # key -> (HTTPServer, work_dir)
        self._library_window: tk.Toplevel | None = None
        self._library_listbox: tk.Listbox | None = None
        self._library_entries: list = []  # parallel to _library_listbox rows
        self._raw_qr_window: tk.Toplevel | None = None

        self._build_ui()
        root.after(100, self._poll_queue)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Apri file…", command=self.open_file).pack(side="left")
        ttk.Button(top, text="Scansiona foto QR…",
                  command=self.open_qr_photo).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Libreria…",
                  command=self.open_library).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Trasporto file (QR)…",
                  command=self.open_raw_qr_transport).pack(side="left", padx=(6, 0))
        ttk.Label(top, text="  risoluzione di analisi:").pack(side="left")
        self.max_dim = tk.StringVar(value="400")
        ttk.Combobox(top, textvariable=self.max_dim, width=6, state="readonly",
                     values=["200", "300", "400", "600", "800", "1200"]).pack(side="left")
        ttk.Label(top, text="  max frame:").pack(side="left")
        self.max_frames = tk.StringVar(value="60")
        ttk.Combobox(top, textvariable=self.max_frames, width=5, state="readonly",
                     values=["10", "30", "60", "120"]).pack(side="left")

        self.status = tk.StringVar(value="Apri un'immagine, una GIF animata o un payload .bzp")
        ttk.Label(top, textvariable=self.status).pack(side="left", padx=16)

        panes = ttk.Frame(self.root, padding=8)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(1, weight=1)

        ttk.Label(panes, text="originale").grid(row=0, column=0)
        ttk.Label(panes, text="rigenerato dal payload (interprete)").grid(row=0, column=1)
        self.canvas_orig = tk.Canvas(panes, width=PREVIEW_MAX, height=PREVIEW_MAX,
                                     highlightthickness=1, highlightbackground="#888")
        self.canvas_orig.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        self.canvas_regen = tk.Canvas(panes, width=PREVIEW_MAX, height=PREVIEW_MAX,
                                      highlightthickness=1, highlightbackground="#888")
        self.canvas_regen.grid(row=1, column=1, sticky="nsew", padx=(4, 0))

        nav = ttk.Frame(panes)
        nav.grid(row=2, column=0, columnspan=2, pady=(6, 0))
        self.btn_prev = ttk.Button(nav, text="◀ Indietro",
                                   command=self.prev_frame, state="disabled")
        self.btn_prev.pack(side="left", padx=4)
        self.btn_play = ttk.Button(nav, text="⏸ Pausa",
                                   command=self.toggle_play, state="disabled")
        self.btn_play.pack(side="left", padx=4)
        self.btn_next = ttk.Button(nav, text="Avanti ▶",
                                   command=self.next_frame, state="disabled")
        self.btn_next.pack(side="left", padx=4)
        self.frame_label = ttk.Label(nav, text="")
        self.frame_label.pack(side="left", padx=12)

        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(fill="x")

        self.stats_text = tk.Text(bottom, height=8, width=60, state="disabled",
                                  font=("TkFixedFont", 9))
        self.stats_text.pack(side="left", fill="both", expand=True)

        btns = ttk.Frame(bottom)
        btns.pack(side="left", padx=(12, 0))
        self.btn_payload = ttk.Button(btns, text="Salva payload (.bzp)",
                                      command=self.save_payload, state="disabled")
        self.btn_payload.pack(fill="x", pady=2)
        self.btn_program = ttk.Button(btns, text="Salva programma (.bzr)",
                                      command=self.save_program, state="disabled")
        self.btn_program.pack(fill="x", pady=2)
        self.btn_export = ttk.Button(btns, text="Esporta rigenerato (PNG/GIF)",
                                     command=self.export_rendered, state="disabled")
        self.btn_export.pack(fill="x", pady=2)
        self.btn_svg = ttk.Button(btns, text="Esporta SVG (vettoriale)",
                                  command=self.export_svg, state="disabled")
        self.btn_svg.pack(fill="x", pady=2)
        self.btn_chunks = ttk.Button(btns, text="Esporta QR (immagine)",
                                     command=self.export_chunks, state="disabled")
        self.btn_chunks.pack(fill="x", pady=2)
        self.btn_view3d = ttk.Button(btns, text="Visualizza in 3D (browser)",
                                     command=self.view_3d, state="disabled")
        self.btn_view3d.pack(fill="x", pady=2)
        # not tied to job state: the alarm table is independent of which
        # 3D file is open, can be loaded before or after opening one, and
        # is reused across files until replaced by loading another CSV
        self.btn_load_alarms = ttk.Button(
            btns, text="Carica tabella allarmi (CSV)", command=self.load_alarm_csv)
        self.btn_load_alarms.pack(fill="x", pady=2)
        # also independent of the currently open job -- picks its own
        # files rather than combining whatever is already loaded
        self.btn_create_bundle = ttk.Button(
            btns, text="Crea bundle (3D + CSV)…", command=self.create_bundle)
        self.btn_create_bundle.pack(fill="x", pady=2)

    # ------------------------------------------------------------- actions

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Apri immagine, GIF animata, assieme 3D (.3dxml), bundle o payload balzar",
            filetypes=[("Immagini, 3D, bundle e payload",
                        "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.3dxml *.bzp *.b3d *.bzx *.bzr"),
                       ("Tutti i file", "*.*")])
        if not path:
            return
        self.status.set(f"Elaborazione di {os.path.basename(path)}…")
        self._set_buttons(False)
        threading.Thread(target=self._worker, args=(path,), daemon=True).start()

    def open_qr_photo(self) -> None:
        """Scan a photo of one QR code or a printed grid of many: same
        action either way, per the physical-carrier design (balzar/qr.py)."""
        path = filedialog.askopenfilename(
            title="Apri foto di un QR o di una griglia di QR",
            filetypes=[("Immagini", "*.png *.jpg *.jpeg *.bmp *.webp"),
                       ("Tutti i file", "*.*")])
        if not path:
            return
        self.status.set(f"Scansione di {os.path.basename(path)}…")
        self._set_buttons(False)
        threading.Thread(target=self._scan_worker, args=(path,), daemon=True).start()

    def _scan_worker(self, path: str) -> None:
        job = Job()
        job.source_name = os.path.basename(path)
        try:
            from .qr import scan_image_file
            payload = scan_image_file(path)
            self._dispatch_payload_bytes(job, payload)
            job.is_live_artifact = True  # a scan is always a Live consumption action
            job.stats.insert(0, ("scansionato da", os.path.basename(path)))
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
        self.queue.put(job)

    def _dispatch_payload_bytes(self, job: Job, data: bytes, path: str | None = None) -> str:
        """Dispatch a decoded/reassembled payload by its own magic header
        -- BZX1 bundle, BZM1 3D scene, or BZR1/plain-text 2D program --
        the single shared implementation for both a file already on disk
        (`path` given, so a mismatched/missing magic still gets an
        extension-based fallback, and a completely unrecognized file
        falls back to a fresh raster encode via _job_from_image) and a
        QR scan or library reopen (`path=None`: no filename to fall back
        on, the bytes are all there is -- same principle
        chunk_payload/qr.py already use, the payload is self-describing,
        so an unrecognized-but-not-BZR1 scan still tries _job_from_payload
        as a last resort rather than misreading it as a raster image).

        Returns which kind of EXISTING artifact was recognized ("bundle"/
        "3d"/"2d"), or "image" for the image-fallback branch (a FRESH
        raster encode, not an existing artifact) -- callers use this to
        decide job.is_live_artifact.

        Fixes a real bug found while designing the library feature:
        scanning a QR that carries a 3D assembly or a bundle used to
        always go through _job_from_payload, which only understands
        BZR1/text, so it crashed with a raw UnicodeDecodeError trying to
        utf-8-decode binary BZM1/BZX1 bytes instead of failing (or
        succeeding) honestly. Previously duplicated (with slight
        variations) between this method and _worker; unified so the
        two entry points can't silently drift apart."""
        from .bundle import MAGIC as BZX1_MAGIC
        from .scene3d import MAGIC as BZM1_MAGIC
        if data[:4] == BZX1_MAGIC or (path is not None and path.endswith(".bzx")):
            self._job_from_bundle(job, data)
            return "bundle"
        if data[:4] == BZM1_MAGIC or (path is not None and path.endswith(".b3d")):
            self._job_from_3d_payload(job, data)
            return "3d"
        if data[:4] == MAGIC or (path is not None and path.endswith(".bzr")):
            self._job_from_payload(job, path or job.source_name, data)
            return "2d"
        if path is not None:
            self._job_from_image(job, data, len(data))
            return "image"
        self._job_from_payload(job, job.source_name, data)
        return "2d"

    def _worker(self, path: str) -> None:
        job = Job()
        job.source_name = os.path.basename(path)
        try:
            if path.endswith(".3dxml"):
                self._job_from_3dxml(job, path)
            else:
                with open(path, "rb") as fh:
                    data = fh.read()
                kind = self._dispatch_payload_bytes(job, data, path=path)
                job.is_live_artifact = kind != "image"  # opening an EXISTING artifact, not creating one
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
        self.queue.put(job)

    def _job_from_payload(self, job: Job, path: str, data: bytes) -> None:
        """Decode + re-render an existing payload or program: the 'unzip'."""
        if data[:4] == MAGIC:
            program = decode_payload(data)
            job.payload = data
        else:
            program = data.decode("utf-8")
            from .payload import encode_payload
            job.payload = encode_payload(program)
        result = render_program(program)
        job.width, job.height = result.width, result.height
        job.frames_rgb = [result.frame_rgb(i) for i in range(len(result.frames))]
        job.original_frames_rgb = []  # no original: the payload IS the source
        job.program_text = program
        raw = result.raw_rgb_size
        job.stats = [
            ("sorgente", job.source_name),
            ("payload", f"{_fmt(len(job.payload))} B"),
            ("output", f"{len(job.frames_rgb)} frame {job.width}x{job.height}"),
            ("RGB generato", f"{_fmt(raw)} B"),
            ("espansione", f"{_fmt(raw / len(job.payload))}x"),
            ("QR singolo", "sì" if fits_in_qr(job.payload) else
             f"no ({_fmt(len(chunk_payload(job.payload)))} capitoli)"),
        ]

    def _job_from_3dxml(self, job: Job, path: str) -> None:
        """3DXML -> BZM1 payload (balzar/scene3d.py): the 3D 'zip'. No 2D
        preview exists for this at all (see Job.is_3d) -- stats + BOM are
        the whole picture until 'Visualizza in 3D' opens a real one."""
        from .scene3d import encode_3dxml_file

        result = encode_3dxml_file(path)
        job.payload = result.payload
        self._finish_3d_job(job, result.payload, result.bom, extra_stats=[
            ("forme uniche", _fmt(result.shape_count)),
            ("riferimenti", _fmt(result.reference_count)),
            ("istanze", _fmt(result.instance_count)),
            ("vertici", _fmt(result.vertex_count)),
            ("errore medio vertici (quantizzazione int16)", str(result.mean_vertex_error)),
        ])

    def _job_from_3d_payload(self, job: Job, data: bytes) -> None:
        """Re-open an already-encoded .b3d payload: decode + rebuild the
        GLB/BOM for viewing, same 'unzip' role as _job_from_payload but
        for the 3D format."""
        from .scene3d import decode_payload, generate_bom

        scene = decode_payload(data)
        job.payload = data
        self._finish_3d_job(job, data, generate_bom(scene), extra_stats=[
            ("forme uniche", _fmt(len(scene.shapes))),
            ("riferimenti", _fmt(len(scene.references))),
        ])

    def _finish_3d_job(self, job: Job, payload: bytes, bom, extra_stats,
                       stats_payload: bytes | None = None,
                       collapse_names: set[str] | None = None) -> None:
        """stats_payload overrides which bytes the 'payload size'/'QR
        singolo' stats describe -- needed for a bundle job (_job_from_bundle),
        where the glb is built from the 3D sub-item's own BZM1 bytes but
        the size/QR-fit that matters to the user is the whole bundle
        that actually gets saved/scanned, not just the sub-item.

        `collapse_names`, if given, must be the same set `bom` was
        already computed with (scene3d.generate_bom's collapse_names) --
        threaded to scene3d_to_glb here so the GLB's material names stay
        consistent with the BOM's own material_names."""
        from .gltf import scene3d_to_glb
        from .scene3d import decode_payload

        scene = decode_payload(payload)
        job.is_3d = True
        job.glb = scene3d_to_glb(scene, collapse_names=collapse_names)
        job.bom_lines = [(e.name, e.count, e.material_names)
                        for e in sorted(bom, key=lambda e: -e.count)]
        size_payload = payload if stats_payload is None else stats_payload
        job.stats = [
            ("sorgente", job.source_name),
            *extra_stats,
            ("distinta base", f"{_fmt(len(bom))} parti uniche, "
             f"{_fmt(sum(e.count for e in bom))} posizionamenti totali"),
            ("payload", f"{_fmt(len(size_payload))} B"),
            ("QR singolo", "sì" if fits_in_qr(size_payload) else
             f"no ({_fmt(len(chunk_payload(size_payload)))} capitoli)"),
        ]

    def _job_from_bundle(self, job: Job, data: bytes) -> None:
        """Re-open (or receive freshly built by create_bundle) a
        multi-document bundle (.bzx, balzar/bundle.py): unpack the 3D
        item (if any) into the usual glb/BOM view, any alarm item into
        job.alarm_rows, and every alarm/doc item into job.documents for
        the navigable index. A bundle with NO 3D item is valid -- it
        stays is_bundle but not is_3d, and 'Visualizza documenti' opens
        an index-only page."""
        from .bundle import KIND_3D, decode_bundle, is_alarm_kind
        from .scene3d import decode_payload, generate_bom
        from .viewer3d import parse_alarm_csv_text

        items = decode_bundle(data)
        job.is_bundle = True
        job.payload = data
        job.alarm_rows = []
        for it in items:
            if is_alarm_kind(it.kind):
                job.alarm_rows.extend(parse_alarm_csv_text(it.data.decode("utf-8")))
        n_docs = sum(1 for it in items if it.kind != KIND_3D)
        bundle_stat = ("bundle", f"{len(items)} elementi ({', '.join(it.kind for it in items)})")
        # an alarm component name collapses its own BOM/GLB entry into a
        # single row/highlight group instead of expanding to every leaf
        # part underneath -- see scene3d.generate_bom's collapse_names
        collapse_names = {name for _code, name in job.alarm_rows} or None

        three_d_items = [it for it in items if it.kind == KIND_3D]
        if three_d_items:
            scene = decode_payload(three_d_items[0].data)
            self._finish_3d_job(job, three_d_items[0].data,
                               generate_bom(scene, collapse_names), extra_stats=[
                ("forme uniche", _fmt(len(scene.shapes))),
                ("riferimenti", _fmt(len(scene.references))),
                bundle_stat,
            ], stats_payload=data, collapse_names=collapse_names)
        else:
            # document-only bundle: no 3D scene to render, just an index
            job.is_3d = False
            job.stats = [
                ("sorgente", job.source_name),
                bundle_stat,
                ("documenti", _fmt(n_docs)),
                ("payload", f"{_fmt(len(data))} B"),
                ("QR singolo", "sì" if fits_in_qr(data) else
                 f"no ({_fmt(len(chunk_payload(data)))} capitoli)"),
            ]

    def _job_from_image(self, job: Job, data: bytes, upload_size: int) -> None:
        """Encode an image or animation: the 'zip'."""
        max_dim = int(self.max_dim.get())
        max_frames = int(self.max_frames.get())
        w, h, frames = load_frames(data, max_dim=max_dim, max_frames=max_frames)
        job.width, job.height = w, h
        job.original_frames_rgb = frames

        if len(frames) == 1:
            from .encoder import encode_image
            result = encode_image(w, h, frames[0])
            frame_info = "1 frame"
            fedelta = result.fidelity_label()
            extra = [("tiling", f"{result.tile[0]}x{result.tile[1]} px"
                      if result.tile else "non trovato")]
        else:
            from .video import encode_video
            result = encode_video(w, h, frames)
            frame_info = f"{result.frame_count} frame (delta-encoding)"
            fedelta = "esatta (lossless)" if result.lossless else "quantizzata (lossy)"
            extra = [("pixel cambiati totali", _fmt(result.delta_pixels_total))]

        job.payload = result.payload
        job.program_text = result.program_text
        rendered = render_program(result.program_text)
        job.frames_rgb = [rendered.frame_rgb(i) for i in range(len(rendered.frames))]

        raw = w * h * 3 * len(frames)
        ratio = raw / len(result.payload)
        job.stats = [
            ("sorgente", f"{job.source_name} ({_fmt(upload_size)} B)"),
            ("analisi", f"{w}x{h}, {frame_info}"),
            ("colori", f"{result.palette_size} ({fedelta})"),
            *extra,
            ("istruzioni", _fmt(result.instruction_count)),
            ("RGB grezzo", f"{_fmt(raw)} B"),
            ("payload", f"{_fmt(len(result.payload))} B"),
            ("fattore vs RGB", (f"{ratio:,.1f}x" if ratio >= 1
                                else f"NESSUN GUADAGNO ({ratio:.2f}x)")),
            ("QR", "1 codice" if fits_in_qr(result.payload)
             else f"{_fmt(len(chunk_payload(result.payload)))} capitoli QR"),
        ]

    # -------------------------------------------------------------- saving

    def _ask_save(self, defname: str, ext: str, types) -> str:
        return filedialog.asksaveasfilename(
            initialfile=defname, defaultextension=ext, filetypes=types)

    def save_payload(self) -> None:
        if not self.job:
            return
        # each format gets its own extension so it's never confused with
        # (or opened as) another: a bundle (.bzx) is still is_3d=True but
        # is a genuinely different container than a bare 3D payload
        if self.job.is_bundle:
            ext, label = ".bzx", "bundle balzar (3D + allarmi)"
        elif self.job.is_3d:
            ext, label = ".b3d", "payload 3D balzar"
        else:
            ext, label = ".bzp", "payload balzar"
        path = self._ask_save(self._stem() + ext, ext, [(label, f"*{ext}")])
        if path:
            with open(path, "wb") as fh:
                fh.write(self.job.payload)
            self.status.set(f"Salvato {os.path.basename(path)} "
                            f"({_fmt(len(self.job.payload))} B)")

    def view_3d(self) -> None:
        """Open the regenerated assembly and/or document index in the
        system's default browser via balzar/viewer3d.py -- Tkinter cannot
        show a GLB, so this delegates to model-viewer the same way
        gltf.py delegates rendering instead of writing a 3D engine here.

        A bundle goes through open_bundle_in_browser straight from its
        raw payload (which builds the 3D view, wires the alarm search,
        AND the document index in one place, including the document-only
        case). A plain 3D job uses open_glb_in_browser as before.

        If this job has a library entry (balzar/library.py) already
        being served from a previous "Visualizza in 3D" click, reopens
        a browser tab at that same port instead of spawning a second
        HTTPServer+thread for content that's already up -- otherwise
        clicking back and forth between library entries would leak one
        background server per click, forever, until the app closes.

        The same caching applies to a job that was never saved to the
        library at all (a fresh Balzar Studio encode, which never gets
        a library_entry_id -- see Job.is_live_artifact): job.id is a
        stable per-instance fallback key, so repeatedly clicking
        'Visualizza in 3D' on the very same just-encoded job also
        reuses one server instead of leaking one per click. Encoding a
        genuinely new file creates a new Job (a new key), which
        correctly gets its own server."""
        job = self.job
        if not job or not (job.is_3d or job.is_bundle):
            return
        key = job.library_entry_id or job.id
        if key in self._open_viewers:
            import webbrowser
            server, _work_dir = self._open_viewers[key]
            webbrowser.open(f"http://127.0.0.1:{server.server_address[1]}/viewer.html")
            self.status.set("Riaperto nel browser (visualizzatore già attivo)")
            return
        import tempfile
        work_dir = tempfile.mkdtemp(prefix="balzar_view3d_")
        if job.is_bundle:
            from .viewer3d import open_bundle_in_browser
            server = open_bundle_in_browser(job.payload, work_dir)
        else:
            from .viewer3d import open_glb_in_browser
            server = open_glb_in_browser(job.glb, job.bom_lines, work_dir,
                                         alarm_rows=self.alarm_rows or None)
        self._open_viewers[key] = (server, work_dir)
        self.status.set("Aperto nel browser predefinito")

    # --------------------------------------------------------------- library

    def open_raw_qr_transport(self) -> None:
        """Opens the "app within the app" for raw byte QR transport
        (balzar/raw_qr_gui.py) -- a file that never touches the balzar
        engine at all, kept in its own module/window so it stays that
        way (see CLAUDE.md §2.4d)."""
        if self._raw_qr_window is not None and self._raw_qr_window.winfo_exists():
            self._raw_qr_window.deiconify()
            self._raw_qr_window.lift()
            return
        from .raw_qr_gui import open_raw_qr_window
        self._raw_qr_window = open_raw_qr_window(self.root)

    def open_library(self) -> None:
        """Open (or raise + refresh) the library panel: every artifact
        decoded/scanned this run AND in past runs (balzar/library.py
        persists to disk on this device, not just in-memory for the
        current session) -- the concrete need behind this panel: scan 3
        machines' QR codes one after another, then pick which of the 3
        to look at, close it, look at another, all without rescanning."""
        if self._library_window is not None and self._library_window.winfo_exists():
            self._refresh_library_panel()
            self._library_window.deiconify()
            self._library_window.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Libreria — file decodificati/scansionati")
        win.geometry("620x360")
        self._library_window = win

        ttk.Label(win, text="Doppio click per aprire una voce.",
                 padding=(8, 6, 8, 0)).pack(anchor="w")
        self._library_listbox = tk.Listbox(win, font=("TkFixedFont", 9))
        self._library_listbox.pack(fill="both", expand=True, padx=8, pady=8)
        self._library_listbox.bind("<Double-Button-1>",
                                   lambda _ev: self._open_library_selected())

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Apri", command=self._open_library_selected).pack(side="left")
        ttk.Button(btns, text="Chiudi visualizzazione",
                  command=self._close_library_viewer_selected).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Elimina dalla libreria",
                  command=self._delete_library_selected).pack(side="left", padx=(6, 0))

        self._refresh_library_panel()

    def _selected_library_entry(self):
        if self._library_listbox is None:
            return None
        sel = self._library_listbox.curselection()
        if not sel:
            return None
        return self._library_entries[sel[0]]

    def _refresh_library_panel(self) -> None:
        """Re-reads the manifest from disk and redraws the list -- called
        after every auto-save, open, or delete, so the panel (if open)
        never shows stale entries.

        Selection must be captured against the list that is CURRENTLY on
        screen, before it's replaced -- looking the old listbox index up
        in the freshly-loaded (possibly reordered, e.g. a background
        auto-save just added a newer entry in front) list would silently
        select the wrong row."""
        if self._library_listbox is None or not self._library_listbox.winfo_exists():
            return
        selected_id = None
        sel = self._library_listbox.curselection()
        if sel and sel[0] < len(self._library_entries):
            selected_id = self._library_entries[sel[0]].id
        from .library import list_library
        self._library_entries = list_library()
        kind_label = {"2d": "2D", "3d": "3D", "bundle": "bundle"}
        self._library_listbox.delete(0, "end")
        for e in self._library_entries:
            open_marker = "  [aperto]" if e.id in self._open_viewers else ""
            self._library_listbox.insert(
                "end", f"{e.saved_at}  [{kind_label.get(e.kind, e.kind)}]  "
                       f"{e.source_name}{open_marker}")
        if selected_id:
            for i, e in enumerate(self._library_entries):
                if e.id == selected_id:
                    self._library_listbox.selection_set(i)
                    break

    def _open_library_selected(self) -> None:
        entry = self._selected_library_entry()
        if entry is None:
            return
        self.status.set(f"Apertura di {entry.source_name} dalla libreria…")
        self._set_buttons(False)
        threading.Thread(target=self._open_library_worker, args=(entry,), daemon=True).start()

    def _open_library_worker(self, entry) -> None:
        from .library import load_library_payload
        job = Job()
        job.source_name = entry.source_name
        # reuse this entry's already-running viewer (if any) instead of
        # re-saving a duplicate entry for content that's already in the
        # library -- is_live_artifact stays False on purpose
        job.library_entry_id = entry.id
        try:
            data = load_library_payload(entry)
            self._dispatch_payload_bytes(job, data)
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
        self.queue.put(job)

    def _shutdown_viewer(self, key: str) -> None:
        """Pops the running server registered under `key` (a library
        entry id, or a job's own fallback id) and tears it down in a
        background thread -- shared by both places that can close a
        viewer, so the teardown sequence only needs to be right in one
        place.

        The pop happens here, synchronously, so the UI immediately
        reflects the close (e.g. a following _refresh_library_panel()
        call no longer shows it as "aperto"). The actual shutdown()
        does not: http.server's shutdown() blocks the caller until the
        OTHER thread's serve_forever() loop notices, on its next
        poll-interval tick (~0.5s by default) -- doing that on the
        Tkinter main thread would freeze the whole GUI for that long on
        every close/delete click."""
        server, work_dir = self._open_viewers.pop(key)

        def _teardown() -> None:
            import shutil
            server.shutdown()
            server.server_close()
            shutil.rmtree(work_dir, ignore_errors=True)

        threading.Thread(target=_teardown, daemon=True).start()

    def _close_library_viewer_selected(self) -> None:
        """Shuts down the ephemeral local server for the selected entry
        (if one is running) and frees its port -- without this, every
        'Visualizza in 3D' click across a long session leaks one
        background HTTPServer thread, forever, until the app quits."""
        entry = self._selected_library_entry()
        if entry is None or entry.id not in self._open_viewers:
            return
        self._shutdown_viewer(entry.id)
        self._refresh_library_panel()
        self.status.set(f"Visualizzazione di {entry.source_name} chiusa")

    def _delete_library_selected(self) -> None:
        entry = self._selected_library_entry()
        if entry is None:
            return
        if not messagebox.askyesno(
                "balzar", f"Eliminare '{entry.source_name}' dalla libreria?\n"
                "Il file scompare dalla libreria, non solo dalla vista."):
            return
        if entry.id in self._open_viewers:
            self._shutdown_viewer(entry.id)
        # if the currently-displayed job still points at this entry (it
        # was auto-saved but never actually viewed, so it was never in
        # _open_viewers above), clear the reference -- otherwise a later
        # first click on "Visualizza in 3D" for that still-displayed job
        # would resurrect this now-deleted id into _open_viewers, and
        # since it can never appear in the library listbox again, that
        # server could never be closed from this panel again either
        if self.job is not None and self.job.library_entry_id == entry.id:
            self.job.library_entry_id = None
        from .library import delete_from_library
        delete_from_library(entry)
        self._refresh_library_panel()

    def create_bundle(self) -> None:
        """Combine a 3D assembly, an alarm CSV, and any extra consultable
        documents into one .bzx bundle (balzar/bundle.py): one file, one
        future QR/scan, opening straight into the 3D view + alarm search +
        document index with no separate upload steps. Every part is
        optional except that the bundle can't be empty: a 3D-only bundle,
        a 3D + alarm bundle, or a documents-only bundle (no 3D at all) are
        all valid. Three dialogs, each cancellable."""
        threed_path = filedialog.askopenfilename(
            title="Assieme 3D (opzionale -- Annulla per un bundle di soli documenti)",
            filetypes=[("Assieme 3D", "*.3dxml *.b3d"), ("Tutti i file", "*.*")])
        alarm_path = filedialog.askopenfilename(
            title="Tabella allarmi CSV (opzionale -- Annulla per saltare)",
            filetypes=[("CSV", "*.csv"), ("Tutti i file", "*.*")])
        doc_paths = filedialog.askopenfilenames(
            title="Documenti aggiuntivi da consultare (opzionale, selezione multipla)",
            filetypes=[("Tutti i file", "*.*")])
        paths = ([threed_path] if threed_path else []) \
            + ([alarm_path] if alarm_path else []) + list(doc_paths)
        if not paths:
            self.status.set("Bundle annullato (nessun file scelto)")
            return
        self.status.set("Creazione bundle in corso…")
        self._set_buttons(False)
        threading.Thread(target=self._bundle_worker,
                         args=(paths, alarm_path or None), daemon=True).start()

    def _bundle_worker(self, paths: list[str], alarm_path: str | None) -> None:
        job = Job()
        job.source_name = os.path.splitext(os.path.basename(paths[0]))[0] + ".bzx"
        try:
            from .bundle import encode_bundle_files
            data = encode_bundle_files(paths, alarm_paths=[alarm_path] if alarm_path else None)
            self._job_from_bundle(job, data)
            job.stats.insert(0, ("creato da", ", ".join(os.path.basename(p) for p in paths)))
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
        self.queue.put(job)

    def load_alarm_csv(self) -> None:
        """Load a codice_allarme,nome_componente CSV for the 3D viewer's
        search bar (balzar/viewer3d.py) -- baked into the page the next
        time 'Visualizza in 3D' opens one, so the operator can search by
        alarm code with no manual upload step in the browser itself."""
        path = filedialog.askopenfilename(
            title="Carica tabella allarmi (codice_allarme,nome_componente)",
            filetypes=[("CSV", "*.csv"), ("Tutti i file", "*.*")])
        if not path:
            return
        from .viewer3d import parse_alarm_csv
        try:
            rows = parse_alarm_csv(path)
        except OSError as exc:
            messagebox.showerror("balzar", f"Impossibile leggere {os.path.basename(path)}:\n{exc}")
            return
        if not rows:
            messagebox.showwarning(
                "balzar", f"Nessuna riga valida trovata in {os.path.basename(path)}.\n"
                "Formato atteso: codice_allarme,nome_componente (una riga per coppia).")
            return
        self.alarm_rows = rows
        n_codes = len({code for code, _ in rows})
        self.status.set(f"Tabella allarmi caricata: {n_codes} codici, {len(rows)} righe "
                        f"({os.path.basename(path)})")

    def save_program(self) -> None:
        if not self.job:
            return
        path = self._ask_save(self._stem() + ".bzr", ".bzr",
                              [("programma balzar", "*.bzr")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.job.program_text)
            self.status.set(f"Salvato {os.path.basename(path)}")

    def export_rendered(self) -> None:
        job = self.job
        if not job:
            return
        if len(job.frames_rgb) > 1:
            path = self._ask_save(self._stem() + "_rigenerato.gif", ".gif",
                                  [("GIF animata", "*.gif")])
            if path:
                size = save_gif(path, job.width, job.height, job.frames_rgb)
                self.status.set(f"Esportata GIF ({_fmt(size)} B)")
        else:
            path = self._ask_save(self._stem() + "_rigenerato.png", ".png",
                                  [("PNG", "*.png")])
            if path:
                data = png_bytes(job.width, job.height, job.frames_rgb[0])
                with open(path, "wb") as fh:
                    fh.write(data)
                self.status.set(f"Esportato PNG ({_fmt(len(data))} B)")

    def export_svg(self) -> None:
        """True vector export — works only for the vector-safe DSL subset
        (balzar/svg.py); refuses clearly instead of faking it otherwise."""
        job = self.job
        if not job:
            return
        from .svg import UnsupportedForSVG, render_svg
        try:
            svg_text = render_svg(job.program_text)
        except UnsupportedForSVG as exc:
            messagebox.showwarning(
                "balzar",
                f"Questo programma non e' esportabile in SVG:\n\n{exc}\n\n"
                f"Usa 'Esporta rigenerato (PNG/GIF)' invece.")
            return
        path = self._ask_save(self._stem() + ".svg", ".svg",
                              [("SVG vettoriale", "*.svg")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(svg_text)
            self.status.set(f"Esportato SVG ({_fmt(len(svg_text))} B, vettoriale reale)")

    def export_chunks(self) -> None:
        """Payload -> one printable image: a single QR, or an auto-sized
        grid of QR codes if it doesn't fit in one (balzar/qr.py). Same
        'scan this image' experience either way — see 'Scansiona foto QR'."""
        job = self.job
        if not job:
            return
        try:
            from .qr import payload_to_qr_image
        except ImportError:
            messagebox.showerror(
                "balzar", "Richiede i pacchetti 'qrcode' e 'Pillow'\n"
                "(pip install qrcode pillow)")
            return
        path = self._ask_save(self._stem() + "_qr.png", ".png",
                              [("Immagine QR", "*.png")])
        if not path:
            return
        img = payload_to_qr_image(job.payload)
        img.save(path)
        n_chunks = len(chunk_payload(job.payload))
        self.status.set(
            f"QR scritto in {os.path.basename(path)} ({img.width}x{img.height}px, "
            + (f"{n_chunks} codici in griglia" if n_chunks > 1 else "1 codice"))

    def _stem(self) -> str:
        return os.path.splitext(self.job.source_name)[0] if self.job else "output"

    # ------------------------------------------------------------- display

    def _poll_queue(self) -> None:
        try:
            job = self.queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_queue)
            return
        if job.error:
            self.status.set("Errore")
            messagebox.showerror("balzar", job.error)
        else:
            self.job = job
            self._show_job(job)
            if job.is_live_artifact:
                self._save_job_to_library(job)
        self.root.after(100, self._poll_queue)

    def _save_job_to_library(self, job: Job) -> None:
        """Auto-save a scanned/opened artifact to the local library
        (balzar/library.py) -- the concrete need this answers: scan 3
        machines' QR codes one after another, come back to any of the 3
        later without rescanning, even after closing and reopening the
        app. Never blocks/asks -- every Live decode just accumulates,
        the operator prunes old entries from the library panel
        whenever they want, not on every scan."""
        from .library import KIND_2D, KIND_3D, KIND_BUNDLE, save_to_library
        kind = KIND_BUNDLE if job.is_bundle else (KIND_3D if job.is_3d else KIND_2D)
        try:
            entry = save_to_library(job.payload, kind, job.source_name)
            job.library_entry_id = entry.id
        except (OSError, ValueError) as exc:
            # a full disk or a locked-down home directory (OSError), or
            # a corrupt manifest.json / an unrecognized kind (ValueError,
            # incl. json.JSONDecodeError which subclasses it) shouldn't
            # take down the job the operator just successfully
            # opened/scanned -- the artifact is still shown, only the
            # library save failed. This is called from _poll_queue,
            # whose own `after` reschedule runs right after this method
            # returns -- letting anything but these two escape here would
            # permanently stop the GUI's job-queue polling loop.
            self.status.set(f"{self.status.get()} (libreria non salvata: {exc})")
        self._refresh_library_panel()

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for b in (self.btn_payload, self.btn_program,
                  self.btn_export, self.btn_svg, self.btn_chunks, self.btn_view3d):
            b.configure(state=state)

    def _set_buttons_for_job(self, job: Job) -> None:
        """A 3D job has no program text, no 2D render to export as PNG/GIF/
        SVG -- those buttons stay disabled instead of doing something
        meaningless; only the payload/QR (format-agnostic) and the new
        3D viewer button apply."""
        if job.is_3d or job.is_bundle:
            payload_text = "Salva bundle (.bzx)" if job.is_bundle else "Salva payload (.b3d)"
            self.btn_payload.configure(state="normal", text=payload_text)
            self.btn_program.configure(state="disabled")
            self.btn_export.configure(state="disabled")
            self.btn_svg.configure(state="disabled")
            self.btn_chunks.configure(state="normal")
            # a doc-only bundle has no 3D to "view in 3D" -- relabel to
            # match what the button actually opens (the document index)
            view_text = ("Visualizza documenti (browser)"
                         if job.is_bundle and not job.is_3d
                         else "Visualizza in 3D (browser)")
            self.btn_view3d.configure(state="normal", text=view_text)
        else:
            self._set_buttons(True)
            self.btn_payload.configure(text="Salva payload (.bzp)")
            self.btn_view3d.configure(state="disabled", text="Visualizza in 3D (browser)")

    def _photo_from_rgb(self, w: int, h: int, rgb: bytes) -> tk.PhotoImage:
        """RGB bytes -> Tk PhotoImage via in-memory PNG (Tk 8.6 reads PNG)."""
        scale = min(PREVIEW_MAX / w, PREVIEW_MAX / h, 1.0)
        if scale < 1.0:
            from PIL import Image
            img = Image.frombytes("RGB", (w, h), rgb)
            nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
            img = img.resize((nw, nh), Image.NEAREST)
            w, h, rgb = nw, nh, img.tobytes()
        data = base64.b64encode(png_bytes(w, h, rgb))
        return tk.PhotoImage(data=data)

    def _show_job(self, job: Job) -> None:
        if self._anim_after:
            self.root.after_cancel(self._anim_after)
            self._anim_after = None
        self._photo_refs = []

        if job.is_3d or job.is_bundle:
            # no 2D render exists for a 3D assembly or a document bundle --
            # show a text hint instead of pretending there's an image
            self._orig_photos = []
            self._regen_photos = []
            if job.is_bundle and not job.is_3d:
                labels = ("bundle di documenti", "usa 'Visualizza documenti'")
            else:
                labels = ("assieme 3D", "usa 'Visualizza in 3D'")
            for canvas, label in ((self.canvas_orig, labels[0]),
                                  (self.canvas_regen, labels[1])):
                canvas.delete("all")
                canvas.create_text(PREVIEW_MAX // 2, PREVIEW_MAX // 2, text=label,
                                   fill="#888", font=("TkDefaultFont", 11))
            self._frame_count = 1
            for b in (self.btn_prev, self.btn_play, self.btn_next):
                b.configure(state="disabled")
            self.frame_label.configure(text="")
        else:
            self._orig_photos = [self._photo_from_rgb(job.width, job.height, f)
                                 for f in job.original_frames_rgb[:60]]
            self._regen_photos = [self._photo_from_rgb(job.width, job.height, f)
                                  for f in job.frames_rgb[:60]]
            self._photo_refs = self._orig_photos + self._regen_photos
            self._anim_index = 0
            self._playing = True
            self._frame_count = max(len(self._orig_photos), len(self._regen_photos), 1)
            multi = self._frame_count > 1
            for b in (self.btn_prev, self.btn_play, self.btn_next):
                b.configure(state="normal" if multi else "disabled")
            self.btn_play.configure(text="⏸ Pausa")
            self._draw_frame()
            if multi:
                self._animate()

        self.stats_text.configure(state="normal")
        self.stats_text.delete("1.0", "end")
        width = max(len(k) for k, _ in job.stats)
        for k, v in job.stats:
            self.stats_text.insert("end", f"{k.ljust(width)}  {v}\n")
        self.stats_text.configure(state="disabled")

        self._set_buttons_for_job(job)
        self.status.set(f"Fatto: {job.source_name}")

    def _draw_frame(self) -> None:
        for canvas, photos in ((self.canvas_orig, self._orig_photos),
                               (self.canvas_regen, self._regen_photos)):
            canvas.delete("all")
            if photos:
                photo = photos[self._anim_index % len(photos)]
                canvas.create_image(canvas.winfo_width() // 2 or PREVIEW_MAX // 2,
                                    canvas.winfo_height() // 2 or PREVIEW_MAX // 2,
                                    image=photo)
        if self._frame_count > 1:
            self.frame_label.configure(
                text=f"Step {self._anim_index % self._frame_count + 1}/{self._frame_count}")
        else:
            self.frame_label.configure(text="")

    def _animate(self) -> None:
        self._anim_index += 1
        self._draw_frame()
        self._anim_after = self.root.after(120, self._animate)

    def _stop_animation(self) -> None:
        if self._anim_after:
            self.root.after_cancel(self._anim_after)
            self._anim_after = None
        self._playing = False
        self.btn_play.configure(text="▶ Play")

    def prev_frame(self) -> None:
        """Manual back-and-forth navigation — every frame is already a
        fully decoded image in memory, so 'previous' is just an index
        change, not a re-render (random access, not sequential playback)."""
        self._stop_animation()
        self._anim_index = (self._anim_index - 1) % self._frame_count
        self._draw_frame()

    def next_frame(self) -> None:
        self._stop_animation()
        self._anim_index = (self._anim_index + 1) % self._frame_count
        self._draw_frame()

    def toggle_play(self) -> None:
        if self._playing:
            self._stop_animation()
        else:
            self._playing = True
            self.btn_play.configure(text="⏸ Pausa")
            self._animate()


def main() -> None:
    root = tk.Tk()
    BalzarApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
