"""balzar/library.py: the local persistent library of decoded/scanned
Balzar Live artifacts. Pure filesystem/JSON logic, no Tkinter involved
-- unlike the rest of gui.py, this is testable directly."""

import os
import tempfile
import unittest


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._old_env = os.environ.get("BALZAR_LIBRARY_DIR")
        os.environ["BALZAR_LIBRARY_DIR"] = self.tmpdir.name
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("BALZAR_LIBRARY_DIR", None)
        else:
            os.environ["BALZAR_LIBRARY_DIR"] = self._old_env

    def test_save_creates_a_file_and_a_manifest_entry(self):
        from balzar.library import KIND_3D, list_library, save_to_library

        entry = save_to_library(b"fake-b3d-bytes", KIND_3D, "macchina_1.3dxml")
        self.assertTrue(entry.filename.endswith(".b3d"))
        self.assertEqual(entry.source_name, "macchina_1.3dxml")
        entries = list_library()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].id, entry.id)

    def test_load_returns_the_exact_bytes_saved(self):
        from balzar.library import KIND_BUNDLE, load_library_payload, save_to_library

        payload = b"\x00\x01BZX1-ish-bytes\xff"
        entry = save_to_library(payload, KIND_BUNDLE, "linea_2.bzx")
        self.assertEqual(load_library_payload(entry), payload)

    def test_three_scans_are_three_independent_entries(self):
        # the concrete scenario: 3 machines, 3 QR scans, 3 entries to
        # pick among later -- never merged/overwritten
        from balzar.library import KIND_3D, list_library, save_to_library

        save_to_library(b"m1", KIND_3D, "macchina_1")
        save_to_library(b"m2", KIND_3D, "macchina_2")
        save_to_library(b"m3", KIND_3D, "macchina_3")
        entries = list_library()
        self.assertEqual(len(entries), 3)
        self.assertEqual({e.source_name for e in entries},
                         {"macchina_1", "macchina_2", "macchina_3"})

    def test_newest_first(self):
        from balzar.library import KIND_2D, list_library, save_to_library

        first = save_to_library(b"a", KIND_2D, "first")
        second = save_to_library(b"b", KIND_2D, "second")
        entries = list_library()
        self.assertEqual([e.id for e in entries], [second.id, first.id])

    def test_newest_first_breaks_same_second_ties_by_append_order(self):
        # saved_at has 1-second resolution -- two scans completed within
        # the same wall-clock second must still show the more recently
        # saved one first. Regression test for a real bug: Python's sort
        # is stable even under reverse=True (equal keys keep their
        # original relative order, they are not reversed), so a naive
        # `sorted(..., key=saved_at, reverse=True)` left the
        # earlier-of-the-pair on top for same-second saves.
        import balzar.library as library

        original_strftime = library.time.strftime
        library.time.strftime = lambda *a, **k: "2026-01-01T00:00:00Z"
        try:
            first = library.save_to_library(b"a", library.KIND_2D, "first")
            second = library.save_to_library(b"b", library.KIND_2D, "second")
        finally:
            library.time.strftime = original_strftime
        entries = library.list_library()
        self.assertEqual(entries[0].saved_at, entries[1].saved_at)
        self.assertEqual([e.id for e in entries], [second.id, first.id])

    def test_delete_removes_manifest_entry_and_file(self):
        from balzar.library import (library_dir, list_library,
                                    save_to_library, delete_from_library, KIND_3D)

        entry = save_to_library(b"m1", KIND_3D, "macchina_1")
        path = os.path.join(library_dir(), entry.filename)
        self.assertTrue(os.path.exists(path))
        delete_from_library(entry)
        self.assertEqual(list_library(), [])
        self.assertFalse(os.path.exists(path))

    def test_delete_of_already_missing_file_does_not_raise(self):
        from balzar.library import save_to_library, delete_from_library, KIND_3D

        entry = save_to_library(b"m1", KIND_3D, "macchina_1")
        os.remove(os.path.join(os.environ["BALZAR_LIBRARY_DIR"], entry.filename))
        delete_from_library(entry)  # must not raise despite the missing file

    def test_unknown_kind_rejected(self):
        from balzar.library import save_to_library

        with self.assertRaises(ValueError):
            save_to_library(b"x", "not-a-real-kind", "whatever")

    def test_empty_library_returns_empty_list(self):
        from balzar.library import list_library

        self.assertEqual(list_library(), [])
