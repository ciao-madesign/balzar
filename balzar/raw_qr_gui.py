"""Raw byte QR transport — a small "app within the app": encode/decode
ANY file via the BZC1 QR chunking machinery, bypassing balzar's
generative engine entirely. Same principle as `balzar chunks --raw`/
`balzar scan --raw` in the CLI (CLAUDE.md §2.4c), here as a GUI.

The pure logic (no Tkinter, testable without it) lives in
balzar/raw_qr_logic.py — this module is only the widget layer, kept
separate so it can become a standalone script later with little rework:
`RawQrTransportWindow` only needs a Tk-ish master to attach a `Toplevel`
to, and `main()` at the bottom runs it as its own root if this module is
executed directly.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .raw_qr_logic import RawQrAssembler, encode_file_to_qr_frames

# ------------------------------------------------------------------- GUI

class RawQrTransportWindow(tk.Toplevel):
    """Two tabs, Codifica/Leggi, each independent of the main balzar
    encode/render workflow — this window never touches a .bzr/.bzp."""

    def __init__(self, master):
        super().__init__(master)
        self.title("Trasporto file (QR) — nessun motore balzar")
        self.geometry("640x540")
        self.minsize(560, 460)

        self._enc_path: str | None = None
        self._dec_paths: list[str] = []
        self._assembler = RawQrAssembler()
        self._dec_queue: queue.Queue = queue.Queue()
        self._enc_queue: queue.Queue = queue.Queue()

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        enc_tab = ttk.Frame(notebook)
        dec_tab = ttk.Frame(notebook)
        notebook.add(enc_tab, text="Codifica: file → QR")
        notebook.add(dec_tab, text="Leggi: QR → file")
        self._build_encode_tab(enc_tab)
        self._build_decode_tab(dec_tab)

        self.after(100, self._poll_queues)

    # ---------------------------------------------------------- encode

    def _build_encode_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="Un file qualunque (PDF, binario, qualsiasi cosa) → sequenza di "
                            "immagini QR. Nessuna compressione: sono gli stessi byte, solo "
                            "spezzettati per il trasporto fisico.",
                 wraplength=580, justify="left").pack(anchor="w", padx=8, pady=(8, 4))

        row = ttk.Frame(tab)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Button(row, text="Scegli file…", command=self._choose_encode_file).pack(side="left")
        self._enc_path_label = ttk.Label(row, text="(nessun file scelto)")
        self._enc_path_label.pack(side="left", padx=(8, 0))

        dim_row = ttk.Frame(tab)
        dim_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(dim_row, text="QR per fotogramma (griglia N×N):").pack(side="left")
        self._enc_grid_dim = tk.StringVar(value="4")
        ttk.Combobox(dim_row, textvariable=self._enc_grid_dim, values=["1", "2", "4", "8"],
                    width=4, state="readonly").pack(side="left", padx=(6, 0))

        ttk.Button(tab, text="Genera QR…", command=self._start_encode).pack(anchor="w", padx=8, pady=8)
        self._enc_status = ttk.Label(tab, text="", wraplength=580, justify="left")
        self._enc_status.pack(anchor="w", padx=8, pady=4)

    def _choose_encode_file(self) -> None:
        path = filedialog.askopenfilename(title="Scegli un file qualunque")
        if not path:
            return
        self._enc_path = path
        size = os.path.getsize(path)
        self._enc_path_label.config(text=f"{os.path.basename(path)} ({size:,} byte)".replace(",", "."))

    def _start_encode(self) -> None:
        if not self._enc_path:
            messagebox.showwarning("balzar", "Scegli prima un file.")
            return
        out_dir = filedialog.askdirectory(title="Cartella per le immagini QR")
        if not out_dir:
            return
        grid_dim = int(self._enc_grid_dim.get())
        self._enc_status.config(text="Generazione in corso…")
        threading.Thread(target=self._encode_worker, args=(self._enc_path, grid_dim, out_dir),
                         daemon=True).start()

    def _encode_worker(self, path: str, grid_dim: int, out_dir: str) -> None:
        try:
            n_frames, n_bytes = encode_file_to_qr_frames(path, grid_dim, out_dir)
            self._enc_queue.put(("ok", n_frames, n_bytes, out_dir))
        except Exception as exc:  # noqa: BLE001 -- reported to the user, never a silent hang
            self._enc_queue.put(("error", str(exc)))

    # ---------------------------------------------------------- decode

    def _build_decode_tab(self, tab: ttk.Frame) -> None:
        ttk.Label(tab, text="Una o più foto/pagine → file ricostruito. Aggiungibili anche a più "
                            "riprese e in qualunque ordine; un capitolo già letto viene ignorato, "
                            "non è un errore.",
                 wraplength=580, justify="left").pack(anchor="w", padx=8, pady=(8, 4))

        dim_row = ttk.Frame(tab)
        dim_row.pack(fill="x", padx=8, pady=4)
        ttk.Label(dim_row, text="QR per immagine (deve corrispondere a come sono stati generati):").pack(side="left")
        self._dec_grid_dim = tk.StringVar(value="4")
        ttk.Combobox(dim_row, textvariable=self._dec_grid_dim, values=["1", "2", "4", "8"],
                    width=4, state="readonly").pack(side="left", padx=(6, 0))

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill="x", padx=8, pady=4)
        ttk.Button(btn_row, text="Aggiungi foto…", command=self._choose_decode_images).pack(side="left")
        ttk.Button(btn_row, text="Ricomincia", command=self._reset_decode).pack(side="left", padx=(6, 0))

        self._dec_listbox = tk.Listbox(tab, font=("TkFixedFont", 9), height=8)
        self._dec_listbox.pack(fill="both", expand=True, padx=8, pady=4)

        self._dec_status = ttk.Label(tab, text="", wraplength=580, justify="left")
        self._dec_status.pack(anchor="w", padx=8, pady=4)

        self._dec_save_btn = ttk.Button(tab, text="Salva file ricostruito…",
                                        command=self._save_decoded, state="disabled")
        self._dec_save_btn.pack(anchor="w", padx=8, pady=(0, 8))

    def _choose_decode_images(self) -> None:
        paths = filedialog.askopenfilenames(title="Scegli una o più foto/pagine QR")
        if not paths:
            return
        new_paths = [p for p in paths if p not in self._dec_paths]
        self._dec_paths.extend(new_paths)
        for p in new_paths:
            self._dec_listbox.insert("end", os.path.basename(p))
        grid_dim = int(self._dec_grid_dim.get())
        self._dec_status.config(text="Lettura in corso…")
        self._dec_save_btn.config(state="disabled")
        threading.Thread(target=self._decode_worker, args=(list(new_paths), grid_dim),
                         daemon=True).start()

    def _decode_worker(self, paths: list[str], grid_dim: int) -> None:
        try:
            complete, missing = False, None
            for p in paths:
                complete, missing = self._assembler.add_image(p, grid_dim=grid_dim)
            self._dec_queue.put(("ok", complete, missing))
        except Exception as exc:  # noqa: BLE001
            self._dec_queue.put(("error", str(exc)))

    def _reset_decode(self) -> None:
        self._dec_paths = []
        self._assembler = RawQrAssembler()
        self._dec_listbox.delete(0, "end")
        self._dec_status.config(text="")
        self._dec_save_btn.config(state="disabled")

    def _save_decoded(self) -> None:
        try:
            data = self._assembler.result()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("balzar", str(exc))
            return
        path = filedialog.asksaveasfilename(title="Salva file ricostruito come…",
                                            initialfile="file_ricostruito")
        if not path:
            return
        with open(path, "wb") as fh:
            fh.write(data)
        messagebox.showinfo("balzar", f"Salvato: {path} ({len(data):,} byte, "
                                      "integrità verificata via CRC32)".replace(",", "."))

    # ---------------------------------------------------------- polling

    def _poll_queues(self) -> None:
        try:
            while True:
                item = self._enc_queue.get_nowait()
                if item[0] == "ok":
                    _, n_frames, n_bytes, out_dir = item
                    self._enc_status.config(
                        text=f"{n_bytes:,} byte -> {n_frames} fotogramma/i scritti in "
                             f"{out_dir}".replace(",", "."))
                else:
                    self._enc_status.config(text=f"Errore: {item[1]}")
                    messagebox.showerror("balzar", item[1])
        except queue.Empty:
            pass

        try:
            while True:
                item = self._dec_queue.get_nowait()
                if item[0] == "ok":
                    _, complete, missing = item
                    if complete:
                        self._dec_status.config(text="Completo — pronto per il salvataggio "
                                                     "(integrità verificata via CRC32 al salvataggio).")
                        self._dec_save_btn.config(state="normal")
                    else:
                        n_missing = len(missing) if missing else "?"
                        self._dec_status.config(text=f"Mancano ancora {n_missing} capitoli — "
                                                     "aggiungi altre foto/pagine.")
                else:
                    self._dec_status.config(text=f"Errore: {item[1]}")
                    messagebox.showerror("balzar", item[1])
        except queue.Empty:
            pass

        if self.winfo_exists():
            self.after(100, self._poll_queues)


def open_raw_qr_window(master) -> RawQrTransportWindow:
    return RawQrTransportWindow(master)


def main() -> None:
    """Standalone entry point: `python3 -m balzar.raw_qr_gui`. Runs the
    same window as its own root instead of a Toplevel under the main
    balzar app — kept possible from day one even though today it's only
    reached via a button in balzar/gui.py."""
    root = tk.Tk()
    root.withdraw()
    win = RawQrTransportWindow(root)
    win.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
