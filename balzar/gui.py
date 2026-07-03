"""Desktop app: the offline product. Open a file, compress it, save it —
like a zipper, but what you save is a generative program, not data.

    python3 -m balzar gui

Pure stdlib Tkinter + Pillow (already required by the encoder), so the
whole thing packages into a single executable with PyInstaller:

    pyinstaller --onefile --windowed --name balzar balzar-app.py

Accepts images (PNG/JPEG/BMP/...), animated GIFs (encoded as a delta
video, README §4.3) and .bzp payloads (decoded and re-rendered). Encoding
runs in a worker thread so the window never freezes.
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


class BalzarApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("balzar — compressione generativa deterministica")
        root.minsize(900, 640)

        self.queue: queue.Queue[Job] = queue.Queue()
        self.job: Job | None = None
        self._anim_after: str | None = None
        self._anim_index = 0
        self._photo_refs: list = []  # keep PhotoImage references alive

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
        self.btn_chunks = ttk.Button(btns, text="Esporta QR (immagine)",
                                     command=self.export_chunks, state="disabled")
        self.btn_chunks.pack(fill="x", pady=2)

    # ------------------------------------------------------------- actions

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Apri immagine, GIF animata o payload balzar",
            filetypes=[("Immagini e payload",
                        "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.bzp *.bzr"),
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
            with open(path, "rb") as fh:
                data = fh.read()
            if data[:4] == MAGIC or path.endswith(".bzr"):
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
            lossless = result.lossless
            extra = [("tiling", f"{result.tile[0]}x{result.tile[1]} px"
                      if result.tile else "non trovato")]
        else:
            from .video import encode_video
            result = encode_video(w, h, frames)
            frame_info = f"{result.frame_count} frame (delta-encoding)"
            lossless = result.lossless
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
            ("colori", f"{result.palette_size} "
             + ("(lossless)" if lossless else "(quantizzati 3-3-2, lossy)")),
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
        path = self._ask_save(self._stem() + ".bzp", ".bzp",
                              [("payload balzar", "*.bzp")])
        if path:
            with open(path, "wb") as fh:
                fh.write(self.job.payload)
            self.status.set(f"Salvato {os.path.basename(path)} "
                            f"({_fmt(len(self.job.payload))} B)")

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
                  self.btn_export, self.btn_chunks):
            b.configure(state=state)

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

        self._orig_photos = [self._photo_from_rgb(job.width, job.height, f)
                             for f in job.original_frames_rgb[:60]]
        self._regen_photos = [self._photo_from_rgb(job.width, job.height, f)
                              for f in job.frames_rgb[:60]]
        self._photo_refs = self._orig_photos + self._regen_photos
        self._anim_index = 0
        self._draw_frame()
        if max(len(self._orig_photos), len(self._regen_photos)) > 1:
            self._animate()

        self.stats_text.configure(state="normal")
        self.stats_text.delete("1.0", "end")
        width = max(len(k) for k, _ in job.stats)
        for k, v in job.stats:
            self.stats_text.insert("end", f"{k.ljust(width)}  {v}\n")
        self.stats_text.configure(state="disabled")

        self._set_buttons(True)
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

    def _animate(self) -> None:
        self._anim_index += 1
        self._draw_frame()
        self._anim_after = self.root.after(120, self._animate)


def main() -> None:
    root = tk.Tk()
    BalzarApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
