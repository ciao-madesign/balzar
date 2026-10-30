"""Avvisi sui nomi che non corrispondono al modello (balzar/component_check.py),
parametro dell'indice moduli in bundle/CLI e avvisi nelle risposte web.

Il motore ignora in silenzio un componente sconosciuto (vedi
test_no_alarm_match_leaves_bom_uncollapsed): qui si verifica che l'autore ne
sia informato senza che la codifica fallisca."""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_webapi import _make_3dxml_with_group_bytes

from balzar.alarm_graph import AlarmGraph, AlarmNode
from balzar.bundle import (KIND_3D, KIND_MODULE_INDEX, BundleError, BundleItem,
                           decode_bundle, encode_bundle, encode_bundle_files)
from balzar.cli import main
from balzar.component_check import bundle_warnings, component_warnings, normalize_name
from balzar.module_index import module_index_to_bytes
from balzar.scene3d import encode_payload, parse_3dxml
from balzar.webapi import LOCAL_LIMITS, handle_encode_3d, handle_render


def _scene_and_payload():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.3dxml")
        with open(path, "wb") as fh:
            fh.write(_make_3dxml_with_group_bytes())
        scene = parse_3dxml(path)
    return scene, encode_payload(scene)


def _graph(*pairs):
    return AlarmGraph(alarms=[AlarmNode(code, "x", comp) for code, comp in pairs]).to_json_dict()


def _index(*keys):
    return {"v": 1, "docs": [{"c": "D-1", "t": "T", "r": 1}], "idx": {k: [0] for k in keys}}


def _b64(b):
    return base64.b64encode(b).decode("ascii")


class TestNormalizeName(unittest.TestCase):
    def test_same_rules_as_doqtool_and_the_js_lookup(self):
        self.assertEqual(normalize_name("MOTOR_UNIT_01"), "motor unit 01")
        self.assertEqual(normalize_name("  Cuscinetto  lato è "), "cuscinetto lato e")
        self.assertEqual(normalize_name(""), "")
        self.assertEqual(normalize_name(None), "")


class TestComponentWarnings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene, cls.payload = _scene_and_payload()

    def test_nothing_to_check_means_no_warnings(self):
        self.assertEqual(component_warnings(self.scene), [])
        self.assertEqual(component_warnings(self.scene, _graph(("A1", "")), None), [])

    def test_alarm_component_that_exists_is_fine_leaf_or_group(self):
        # il gruppo si comprime in una riga, la parte singola resta selezionabile
        self.assertEqual(component_warnings(self.scene, _graph(("A1", "HEATER1"))), [])
        self.assertEqual(component_warnings(self.scene, _graph(("A1", "BoltA"))), [])

    def test_different_spelling_is_called_out_with_the_real_name(self):
        [w] = component_warnings(self.scene, _graph(("A1", "heater1")))
        self.assertIn("A1", w)
        self.assertIn("«HEATER1»", w)
        self.assertIn("grafia diversa", w)

    def test_typo_gets_a_suggestion_unrelated_name_does_not(self):
        [typo] = component_warnings(self.scene, _graph(("A2", "HEATER")))
        self.assertIn("non esiste", typo)
        self.assertIn("Forse intendevi «HEATER1»", typo)
        [none] = component_warnings(self.scene, _graph(("A3", "ZZZ-QQQ")))
        self.assertIn("non esiste", none)
        self.assertNotIn("Forse intendevi", none)

    def test_many_bad_alarms_are_capped_with_a_summary(self):
        graph = _graph(*[(f"A{i}", f"NOPE-{i}") for i in range(30)])
        ws = component_warnings(self.scene, graph)
        self.assertEqual(len(ws), 21)
        self.assertIn("altri 10 allarmi", ws[-1])

    def test_index_keys_unknown_and_unselectable_groups_are_split(self):
        ws = component_warnings(self.scene, None, _index("BoltA", "Ghost", "HEATER1"))
        self.assertEqual(len(ws), 2)
        self.assertTrue(any("Ghost" in w and "senza corrispondenza nel modello" in w for w in ws))
        self.assertTrue(any("HEATER1" in w and "sotto-assiem" in w and "nessun allarme" in w for w in ws))
        self.assertFalse(any("BoltA" in w for w in ws))

    def test_an_alarm_that_collapses_the_group_makes_it_selectable(self):
        ws = component_warnings(self.scene, _graph(("A1", "HEATER1")), _index("HEATER1", "BoltA"))
        # HEATER1 e' ora una riga e BoltA ci sta dentro: non e' selezionabile da solo,
        # ma e' una parte reale e DoqTool ne riunisce i moduli sotto HEATER1, quindi
        # non si perde nulla e non va segnalato
        self.assertEqual(ws, [])

    def test_index_key_in_different_spelling_still_matches(self):
        self.assertEqual(component_warnings(self.scene, None, _index("bolta", "BOLTB")), [])

    def test_examples_are_limited(self):
        ws = component_warnings(self.scene, None, _index(*[f"Ghost{i}" for i in range(9)]))
        self.assertEqual(len(ws), 1)
        self.assertIn("9 componente/i", ws[0])
        self.assertIn("e altri 4", ws[0])


class TestBundleWarnings(unittest.TestCase):
    def test_from_bundle_items(self):
        _, payload = _scene_and_payload()
        items = [BundleItem(KIND_3D, "a.b3d", payload),
                 BundleItem(KIND_MODULE_INDEX, "moduli.json", module_index_to_bytes(_index("Ghost")))]
        self.assertEqual(len(bundle_warnings(items)), 1)

    def test_no_3d_or_unreadable_inputs_give_no_warnings(self):
        self.assertEqual(bundle_warnings([BundleItem(KIND_MODULE_INDEX, "moduli.json",
                                                      module_index_to_bytes(_index("X")))]), [])
        self.assertEqual(bundle_warnings([BundleItem(KIND_3D, "a.b3d", b"non un payload")]), [])


class TestEncodeBundleFilesModuleIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as fh:
            fh.write(data)
        return p

    def test_index_becomes_a_normalized_bundle_item(self):
        xml = self._write("a.3dxml", _make_3dxml_with_group_bytes())
        raw = {"v": 1, "docs": [{"c": " D-1 ", "t": "T", "r": 1, "extra": "x"}], "idx": {"BoltA": [0, 0]}}
        idx = self._write("moduli.json", json.dumps(raw))
        items = decode_bundle(encode_bundle_files([xml], module_index_path=idx))
        item = next(it for it in items if it.kind == KIND_MODULE_INDEX)
        self.assertEqual(item.label, "moduli.json")
        self.assertEqual(json.loads(item.data), {"v": 1, "docs": [{"c": "D-1", "t": "T", "r": 1}], "idx": {"BoltA": [0]}})

    def test_invalid_index_refuses_with_a_clear_reason(self):
        xml = self._write("a.3dxml", _make_3dxml_with_group_bytes())
        for name, content in (("rotto.json", "{non json"), ("fuori.json", json.dumps(_index("A") | {"idx": {"A": [9]}}))):
            with self.subTest(name=name):
                with self.assertRaises(BundleError) as ctx:
                    encode_bundle_files([xml], module_index_path=self._write(name, content))
                self.assertIn(name, str(ctx.exception))
                self.assertIn("indice moduli non valido", str(ctx.exception))

    def test_without_the_parameter_nothing_changes(self):
        xml = self._write("a.3dxml", _make_3dxml_with_group_bytes())
        kinds = [it.kind for it in decode_bundle(encode_bundle_files([xml]))]
        self.assertEqual(kinds, [KIND_3D])


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _files(self, index):
        xml = os.path.join(self.tmp.name, "a.3dxml")
        with open(xml, "wb") as fh:
            fh.write(_make_3dxml_with_group_bytes())
        idx = os.path.join(self.tmp.name, "moduli.json")
        with open(idx, "w", encoding="utf-8") as fh:
            fh.write(index if isinstance(index, str) else json.dumps(index))
        return xml, idx, os.path.join(self.tmp.name, "out.bzx")

    def test_module_index_flag_adds_the_item_and_prints_name_warnings_on_stderr(self):
        xml, idx, out_path = self._files(_index("BoltA", "Ghost"))
        code, out, err = self._run(["encode-bundle", xml, "--module-index", idx, "-o", out_path])
        self.assertEqual(code, 0)
        self.assertIn("module_index", out)
        self.assertIn("avviso:", err)
        self.assertIn("Ghost", err)
        self.assertTrue(os.path.exists(out_path))       # l'avviso non impedisce la codifica

    def test_clean_index_prints_no_warning(self):
        xml, idx, out_path = self._files(_index("BoltA", "BoltB"))
        code, out, err = self._run(["encode-bundle", xml, "--module-index", idx, "-o", out_path])
        self.assertEqual((code, err), (0, ""))

    def test_invalid_index_is_a_clean_error(self):
        xml, idx, out_path = self._files("{rotto")
        code, out, err = self._run(["encode-bundle", xml, "--module-index", idx, "-o", out_path])
        self.assertEqual(code, 1)
        self.assertIn("indice moduli non valido", err)
        self.assertFalse(os.path.exists(out_path))


class TestWebApiWarnings(unittest.TestCase):
    def _encode(self, **extra):
        return handle_encode_3d({"data": _b64(_make_3dxml_with_group_bytes()), **extra}, LOCAL_LIMITS)

    def test_encode_reports_unknown_names_and_still_succeeds(self):
        status, resp = self._encode(alarm_graph=_graph(("E1", "HEATR1")), module_index=_index("Ghost"))
        self.assertEqual(status, 200, resp)
        self.assertEqual(len(resp["warnings"]), 2)
        self.assertTrue(any("Forse intendevi «HEATER1»" in w for w in resp["warnings"]))

    def test_clean_encode_has_an_empty_warnings_list(self):
        for kw in ({}, {"module_index": _index("BoltA")}, {"alarm_graph": _graph(("E1", "HEATER1"))}):
            with self.subTest(kw=list(kw)):
                status, resp = self._encode(**kw)
                self.assertEqual((status, resp["warnings"]), (200, []))

    def test_reopening_the_bundle_gives_the_same_warnings(self):
        _, enc = self._encode(module_index=_index("Ghost"))
        status, resp = handle_render({"data": enc["payload_base64"]}, LOCAL_LIMITS)
        self.assertEqual(status, 200, resp)
        self.assertEqual(resp["warnings"], enc["warnings"])

    def test_bare_3d_payload_and_documents_only_bundle_have_no_warnings(self):
        _, enc = self._encode()
        _, bare = handle_render({"data": enc["payload_base64"]}, LOCAL_LIMITS)
        self.assertEqual(bare["warnings"], [])
        blob = encode_bundle([BundleItem(KIND_MODULE_INDEX, "moduli.json", module_index_to_bytes(_index("X")))])
        status, resp = handle_render({"data": _b64(blob)}, LOCAL_LIMITS)
        self.assertEqual((status, resp["warnings"]), (200, []))


if __name__ == "__main__":
    unittest.main()
