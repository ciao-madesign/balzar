"""A small local library of decoded/scanned balzar artifacts (Balzar
Live's consumption side), persisted to disk on the reading device --
not just in-memory for the current session. Answers a concrete need:
an operator scans 3 machines' QR codes one after another and needs to
come back to any of the 3 later, without rescanning, even after
closing and reopening the desktop app.

Deliberately local-only: "physical/cloud storage of the reading
device" (as discussed) means a normal folder on disk here -- if the
user points it at a folder synced by Dropbox/OneDrive/iCloud, that's
the device's own OS doing the "cloud" part, not balzar integrating
with any specific provider. Building an actual cloud API integration
would be a new, unrelated feature (auth, a provider to pick, network
error handling) -- not attempted here, and not implied by anything
already in this module.

One JSON manifest (`manifest.json`) lists every entry; the payload
bytes themselves live as sibling files, one per entry, named by the
entry's own id plus the appropriate extension for its kind (matching
the same extensions used everywhere else in the project: .bzp/.b3d/
.bzx) -- so a library directory is just as inspectable by hand as any
other balzar output, nothing hidden in the JSON."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, asdict

KIND_2D = "2d"
KIND_3D = "3d"
KIND_BUNDLE = "bundle"

_EXTENSION = {KIND_2D: ".bzp", KIND_3D: ".b3d", KIND_BUNDLE: ".bzx"}


@dataclass
class LibraryEntry:
    id: str
    label: str
    kind: str          # KIND_2D / KIND_3D / KIND_BUNDLE
    filename: str       # payload file name, inside the library directory
    source_name: str    # original file/scan name, for display only
    saved_at: str       # ISO 8601 UTC, e.g. "2026-07-07T12:34:56Z"


def library_dir() -> str:
    """Overridable via BALZAR_LIBRARY_DIR (tests, or a user who wants the
    library on a different disk/synced folder) -- defaults to a hidden
    folder under the user's home directory, created on first use."""
    path = os.environ.get("BALZAR_LIBRARY_DIR") or os.path.join(
        os.path.expanduser("~"), ".balzar", "library")
    os.makedirs(path, exist_ok=True)
    return path


def _manifest_path() -> str:
    return os.path.join(library_dir(), "manifest.json")


def _read_manifest() -> list[LibraryEntry]:
    path = _manifest_path()
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [LibraryEntry(**item) for item in raw]


def _write_manifest(entries: list[LibraryEntry]) -> None:
    """Writes to a temp file in the same directory, then renames it over
    the real manifest -- os.replace is atomic on both POSIX and Windows,
    so a crash/disk-full/power-loss mid-write leaves either the old
    manifest intact or the new one complete, never a truncated/corrupt
    file in between (which would otherwise break every future
    list_library()/save_to_library() call)."""
    directory = library_dir()
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".manifest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump([asdict(e) for e in entries], fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, _manifest_path())
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def save_to_library(payload: bytes, kind: str, source_name: str) -> LibraryEntry:
    """Store `payload` (already a complete BZR1/BZM1/BZX1 payload -- not
    a bare DSL program) as a new library entry, labelled with
    `source_name` (the scanned photo's filename, or the opened file's
    name) so the operator can tell entries apart without opening each
    one. Never overwrites an existing entry -- every save is a new one,
    even if the bytes are identical to something already saved (the
    caller decides when re-saving is worth it, not this function)."""
    if kind not in _EXTENSION:
        raise ValueError(f"kind sconosciuto: {kind!r}")
    entry_id = uuid.uuid4().hex[:12]
    filename = entry_id + _EXTENSION[kind]
    with open(os.path.join(library_dir(), filename), "wb") as fh:
        fh.write(payload)
    entry = LibraryEntry(
        id=entry_id, label=source_name, kind=kind, filename=filename,
        source_name=source_name, saved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    entries = _read_manifest()
    entries.append(entry)
    _write_manifest(entries)
    return entry


def list_library() -> list[LibraryEntry]:
    """Newest first -- the operator almost always wants the machine they
    just scanned, not one from last month.

    saved_at has only 1-second resolution, and Python's sort is stable
    even under reverse=True (entries with an equal key keep their
    original relative order, they are NOT reversed among themselves) --
    so two scans completed within the same second would otherwise come
    out oldest-of-the-pair-first. Breaking ties by original manifest
    position (append order), descending, fixes exactly that case."""
    entries = list(enumerate(_read_manifest()))
    entries.sort(key=lambda pair: (pair[1].saved_at, pair[0]), reverse=True)
    return [entry for _, entry in entries]


def load_library_payload(entry: LibraryEntry) -> bytes:
    with open(os.path.join(library_dir(), entry.filename), "rb") as fh:
        return fh.read()


def delete_from_library(entry: LibraryEntry) -> None:
    """Removes the entry from the manifest and its payload file. Missing
    payload file (e.g. the folder was tidied up by hand outside balzar)
    is not an error here -- the manifest entry is still gone, which is
    the part that matters to the caller."""
    entries = [e for e in _read_manifest() if e.id != entry.id]
    _write_manifest(entries)
    try:
        os.remove(os.path.join(library_dir(), entry.filename))
    except OSError:
        pass
