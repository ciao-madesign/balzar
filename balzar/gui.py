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
        self.bom_lines: list[tuple[str, int]] = []


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

        self._build_ui()
        root.after(100, self._poll_queue)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Apri file…", command=self.open_file).pack(side="left")
        ttk.Button(top, text="Scansiona foto QR…",
                  command=self.open_qr_photo).pack(side="left", padx=(6, 0))
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

    # ------------------------------------------------------------- actions

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Apri immagine, GIF animata, assieme 3D (.3dxml) o payload balzar",
            filetypes=[("Immagini, 3D e payload",
                        "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.3dxml *.bzp *.b3d *.bzr"),
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
            self._job_from_payload(job, path, payload)
            job.stats.insert(0, ("scansionato da", os.path.basename(path)))
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
        self.queue.put(job)

    def _worker(self, path: str) -> None:
        job = Job()
        job.source_name = os.path.basename(path)
        try:
            if path.endswith(".3dxml"):
                self._job_from_3dxml(job, path)
            else:
                with open(path, "rb") as fh:
                    data = fh.read()
                from .scene3d import MAGIC as BZM1_MAGIC
                if data[:4] == BZM1_MAGIC or path.endswith(".b3d"):
                    self._job_from_3d_payload(job, data)
                elif data[:4] == MAGIC or path.endswith(".bzr"):
                    self._job_from_payload(job, path, data)
                else:
                    self._job_from_image(job, data, len(data))
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

    def _finish_3d_job(self, job: Job, payload: bytes, bom, extra_stats) -> None:
        from .gltf import scene3d_to_glb
        from .scene3d import decode_payload

        scene = decode_payload(payload)
        job.is_3d = True
        job.glb = scene3d_to_glb(scene)
        job.bom_lines = [(e.name, e.count) for e in sorted(bom, key=lambda e: -e.count)]
        job.stats = [
            ("sorgente", job.source_name),
            *extra_stats,
            ("distinta base", f"{_fmt(len(bom))} parti uniche, "
             f"{_fmt(sum(e.count for e in bom))} posizionamenti totali"),
            ("payload", f"{_fmt(len(payload))} B"),
            ("QR singolo", "sì" if fits_in_qr(payload) else
             f"no ({_fmt(len(chunk_payload(payload)))} capitoli)"),
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
        # a 3D job's payload is BZM1, a genuinely different format from
        # the 2D BZR1 payload -- different extension so it's never
        # confused with (or opened as) a 2D program
        ext = ".b3d" if self.job.is_3d else ".bzp"
        label = "payload 3D balzar" if self.job.is_3d else "payload balzar"
        path = self._ask_save(self._stem() + ext, ext, [(label, f"*{ext}")])
        if path:
            with open(path, "wb") as fh:
                fh.write(self.job.payload)
            self.status.set(f"Salvato {os.path.basename(path)} "
                            f"({_fmt(len(self.job.payload))} B)")

    def view_3d(self) -> None:
        """Open the regenerated assembly (+ BOM overlay) in the system's
        default browser via balzar/viewer3d.py -- Tkinter itself cannot
        show a GLB, so this delegates to model-viewer the same way
        gltf.py delegates rendering instead of writing a 3D engine here."""
        job = self.job
        if not job or not job.is_3d:
            return
        import tempfile

        from .viewer3d import open_glb_in_browser
        work_dir = tempfile.mkdtemp(prefix="balzar_view3d_")
        open_glb_in_browser(job.glb, job.bom_lines, work_dir, alarm_rows=self.alarm_rows or None)
        self.status.set("Aperto nel browser predefinito")

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
        self.root.after(100, self._poll_queue)

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
        if job.is_3d:
            self.btn_payload.configure(state="normal", text="Salva payload (.b3d)")
            self.btn_program.configure(state="disabled")
            self.btn_export.configure(state="disabled")
            self.btn_svg.configure(state="disabled")
            self.btn_chunks.configure(state="normal")
            self.btn_view3d.configure(state="normal")
        else:
            self._set_buttons(True)
            self.btn_payload.configure(text="Salva payload (.bzp)")
            self.btn_view3d.configure(state="disabled")

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

        if job.is_3d:
            # no 2D render exists for a 3D assembly -- show a text hint
            # instead of pretending there's an image to preview
            self._orig_photos = []
            self._regen_photos = []
            for canvas, label in ((self.canvas_orig, "assieme 3D"),
                                  (self.canvas_regen, "usa 'Visualizza in 3D'")):
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
