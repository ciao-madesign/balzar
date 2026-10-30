"""Export di `componenti.csv` (balzar/components_export.py): l'elenco dei
componenti con la gerarchia dell'assieme, nel formato che il registro
componenti di DoqTool importa. Verifica righe/gerarchia, il CONTRATTO con le
intestazioni che DoqTool riconosce, la CLI (`export-components`) e il campo
`components_csv` delle risposte web."""

import base64
import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_webapi import _make_3dxml_bytes, _make_3dxml_with_group_bytes

from balzar.bundle import KIND_3D, KIND_DOC, BundleItem, encode_bundle
from balzar.cli import main
from balzar.components_export import HEADER, component_rows, components_csv
from balzar.scene3d import Reference, Scene3D, Shape, encode_payload, parse_3dxml
from balzar.webapi import LOCAL_LIMITS, handle_encode_3d, handle_render

# Intestazioni che il registro di DoqTool riconosce (easydoc/src/lib/components.ts,
# HEADERS, confrontate dopo normalizzazione). Un'intestazione fuori da questo
# elenco verrebbe ignorata in silenzio: e' il contratto fra i due progetti.
DOQTOOL_HEADERS = {
    "codice": "code", "etichetta": "label", "nome": "name",
    "materiali": "materials", "alias": "aliases", "padre": "parent",
}
IGNORED_BY_DOQTOOL = {"conteggio", "livello"}   # informativi, per chi legge il file


def _scene(xml_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.3dxml")
        with open(path, "wb") as fh:
            fh.write(xml_bytes)
        return parse_3dxml(path)


def _b64(b):
    return base64.b64encode(b).decode("ascii")


def _parse(text):
    return list(csv.DictReader(io.StringIO(text)))


class TestRows(unittest.TestCase):
    def test_group_comes_first_with_its_parts_as_children(self):
        rows = component_rows(_scene(_make_3dxml_with_group_bytes()))
        self.assertEqual([(r["label"], r["level"], r["parent"]) for r in rows],
                         [("HEATER1", 0, ""), ("BoltA", 1, "HEATER1"), ("BoltB", 1, "HEATER1")])

    def test_group_materials_are_the_collapsed_ones_a_part_has_its_own_name(self):
        rows = {r["label"]: r for r in component_rows(_scene(_make_3dxml_with_group_bytes()))}
        self.assertEqual(rows["HEATER1"]["materials"], ["BoltA§HEATER1", "BoltB§HEATER1"])
        self.assertEqual(rows["BoltA"]["materials"], ["BoltA"])

    def test_the_root_container_is_not_a_row(self):
        labels = [r["label"] for r in component_rows(_scene(_make_3dxml_with_group_bytes()))]
        self.assertNotIn("Root", labels)

    def test_a_part_placed_twice_is_one_row_with_its_count(self):
        rows = component_rows(_scene(_make_3dxml_bytes()))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["count"], rows[0]["level"], rows[0]["parent"]), (2, 0, ""))

    def test_same_label_reached_twice_keeps_the_first_parent(self):
        # una parte riusata in due sotto-assiemi ha un solo padre: il primo
        shape = Shape(name=None, color=(1, 2, 3), vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        ident = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
        refs = [
            Reference("Root", None, [(1, "a", ident), (2, "b", ident)]),
            Reference("G1", None, [(3, "p1", ident)]),
            Reference("G2", None, [(3, "p2", ident)]),
            Reference("Vite", 0, []),
        ]
        rows = {r["label"]: r for r in component_rows(Scene3D([shape], refs, 0))}
        self.assertEqual(rows["Vite"]["parent"], "G1")
        self.assertEqual(rows["Vite"]["count"], 2)

    def test_empty_scene_gives_only_the_header(self):
        shape = Shape(name=None, color=(0, 0, 0), vertices=[], strips=[])
        text = components_csv(Scene3D([shape], [Reference("Root", None, [])], 0))
        self.assertEqual(text.strip(), ",".join(HEADER))


class TestCsvContract(unittest.TestCase):
    def setUp(self):
        self.text = components_csv(_scene(_make_3dxml_with_group_bytes()))

    def test_every_column_is_known_to_doqtool_or_informational(self):
        for col in HEADER:
            self.assertTrue(col in DOQTOOL_HEADERS or col in IGNORED_BY_DOQTOOL, col)

    def test_the_label_column_doqtool_requires_is_present_and_filled(self):
        rows = _parse(self.text)
        self.assertTrue(all(r["etichetta"] for r in rows))

    def test_code_and_name_are_left_empty_for_the_registry_owner(self):
        rows = _parse(self.text)
        self.assertTrue(all(r["codice"] == "" and r["nome"] == "" for r in rows))

    def test_lists_use_semicolons_and_the_parent_refers_to_a_label_in_the_file(self):
        rows = _parse(self.text)
        labels = {r["etichetta"] for r in rows}
        self.assertEqual(rows[0]["materiali"].split(";"), ["BoltA§HEATER1", "BoltB§HEATER1"])
        for r in rows:
            self.assertTrue(r["padre"] == "" or r["padre"] in labels)

    def test_crlf_utf8_and_quoting_of_awkward_names(self):
        shape = Shape(name=None, color=(0, 0, 0), vertices=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], strips=[[0, 1, 2]])
        ident = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
        scene = Scene3D([shape], [Reference("Root", None, [(1, "x", ident)]),
                                  Reference('Gruppo "A", è', 0, [])], 0)
        text = components_csv(scene)
        self.assertIn("\r\n", text)
        self.assertEqual(_parse(text)[0]["etichetta"], 'Gruppo "A", è')


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _p(self, name):
        return os.path.join(self.tmp.name, name)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _write(self, name, data):
        with open(self._p(name), "wb") as fh:
            fh.write(data)
        return self._p(name)

    def test_from_3dxml_b3d_and_bundle_give_the_same_file(self):
        xml = self._write("a.3dxml", _make_3dxml_with_group_bytes())
        payload = encode_payload(_scene(_make_3dxml_with_group_bytes()))
        b3d = self._write("a.b3d", payload)
        bzx = self._write("a.bzx", encode_bundle([BundleItem(KIND_DOC, "n.txt", b"x"), BundleItem(KIND_3D, "a.b3d", payload)]))
        results = []
        for src in (xml, b3d, bzx):
            out = self._p(os.path.basename(src) + ".csv")
            code, stdout, err = self._run(["export-components", src, "-o", out])
            self.assertEqual((code, err), (0, ""), src)
            self.assertIn("3 righe, 1 sotto-assiemi", stdout)
            with open(out, encoding="utf-8", newline="") as fh:
                results.append(fh.read())
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_default_output_name(self):
        xml = self._write("macchina.3dxml", _make_3dxml_with_group_bytes())
        code, out, err = self._run(["export-components", xml])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self._p("macchina_componenti.csv")))

    def test_bundle_without_3d_and_broken_input_are_clean_errors(self):
        bzx = self._write("doc.bzx", encode_bundle([BundleItem(KIND_DOC, "n.txt", b"x")]))
        code, out, err = self._run(["export-components", bzx, "-o", self._p("x.csv")])
        self.assertEqual(code, 1)
        self.assertIn("nessun assieme 3D", err)
        bad = self._write("rotto.b3d", b"non un payload")
        code, out, err = self._run(["export-components", bad, "-o", self._p("y.csv")])
        self.assertEqual(code, 1)
        self.assertIn("errore:", err)
        self.assertFalse(os.path.exists(self._p("y.csv")))


class TestWebApi(unittest.TestCase):
    def test_encode_and_reopen_carry_the_same_csv(self):
        status, enc = handle_encode_3d({"data": _b64(_make_3dxml_with_group_bytes())}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(enc["components_csv"], components_csv(_scene(_make_3dxml_with_group_bytes())))
        _, bare = handle_render({"data": enc["payload_base64"]}, LOCAL_LIMITS)   # BZM1 nudo
        self.assertEqual(bare["components_csv"], enc["components_csv"])

    def test_bundle_carries_it_and_a_documents_only_bundle_does_not(self):
        _, enc = handle_encode_3d({"data": _b64(_make_3dxml_with_group_bytes()),
                                   "documents": [{"label": "n.txt", "data": _b64(b"x")}]}, LOCAL_LIMITS)
        status, resp = handle_render({"data": enc["payload_base64"]}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertEqual(resp["components_csv"], enc["components_csv"])
        blob = encode_bundle([BundleItem(KIND_DOC, "n.txt", b"x")])
        _, docs_only = handle_render({"data": _b64(blob)}, LOCAL_LIMITS)
        self.assertEqual(docs_only["components_csv"], "")

    def test_the_bom_is_not_affected(self):
        _, enc = handle_encode_3d({"data": _b64(_make_3dxml_with_group_bytes())}, LOCAL_LIMITS)
        self.assertEqual(sorted(e["name"] for e in enc["bom"]), ["BoltA", "BoltB"])


if __name__ == "__main__":
    unittest.main()
