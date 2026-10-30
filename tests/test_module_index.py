"""Indice offline componente -> moduli di documentazione (balzar/module_index.py,
KIND_MODULE_INDEX, balzar-modules.js): validazione, trasporto nel bundle,
lettura tollerante, effetto sulla distinta (collapse_names), pagina del
visualizzatore desktop e logica di abbinamento lato JS.

Il vincolo piu' importante e' l'ADDITIVITA': un bundle senza indice si
comporta esattamente come prima, e un indice illeggibile o di una versione
futura non deve mai impedire di aprire il modello e gli altri contenuti."""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
from test_webapi import _make_3dxml_with_group_bytes

from balzar.bundle import (KIND_3D, KIND_ALARM_GRAPH, KIND_DOC, KIND_MODULE_INDEX,
                           BundleItem, decode_bundle, encode_bundle)
from balzar.module_index import (ModuleIndexError, module_index_from_bytes,
                                 module_index_to_bytes, normalize_module_index,
                                 read_module_index_item)
from balzar.webapi import LOCAL_LIMITS, handle_encode_3d, handle_render

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _index(**over):
    base = {"v": 1,
            "docs": [{"c": "DOC-042", "t": "Sostituzione cuscinetti", "r": 3, "s": "5.2"},
                     {"c": "DOC-019", "t": "Lubrificazione", "r": 2}],
            "idx": {"HEATER1": [0, 1], "BoltA": [1]}}
    base.update(over)
    return base


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


class TestValidation(unittest.TestCase):
    def test_valid_index_is_normalized(self):
        out = normalize_module_index(_index())
        self.assertEqual(out["v"], 1)
        self.assertEqual(out["docs"][0], {"c": "DOC-042", "t": "Sostituzione cuscinetti", "r": 3, "s": "5.2"})
        self.assertNotIn("s", out["docs"][1])
        self.assertEqual(out["idx"]["HEATER1"], [0, 1])

    def test_unknown_fields_are_dropped(self):
        idx = _index()
        idx["docs"][0]["script"] = "<script>alert(1)</script>"
        idx["extra"] = {"x": 1}
        out = normalize_module_index(idx)
        self.assertNotIn("script", out["docs"][0])
        self.assertNotIn("extra", out)

    def test_positions_are_deduplicated_in_order(self):
        out = normalize_module_index(_index(idx={"A": [1, 0, 1, 0]}))
        self.assertEqual(out["idx"]["A"], [1, 0])

    def test_rejects_malformed_input(self):
        bad = [
            "non un oggetto",
            _index(v=2),
            _index(docs="x"),
            _index(idx=[]),
            _index(idx={"A": [5]}),                  # fuori tabella
            _index(idx={"A": [-1]}),
            _index(idx={"A": [True]}),                # bool non e' un indice
            _index(idx={"A": "0"}),
            _index(idx={"": [0]}),
            _index(docs=[{"t": "senza codice", "r": 1}]),
            _index(docs=[{"c": "  ", "t": "x", "r": 1}]),
            _index(docs=[{"c": "D", "t": 5, "r": 1}]),
            _index(docs=[{"c": "D", "t": "x", "r": -1}]),
            _index(docs=[{"c": "D", "t": "x", "r": True}]),
            _index(docs=[{"c": "D", "t": "x", "r": 1, "s": 7}]),
        ]
        for case in bad:
            with self.subTest(case=str(case)[:60]):
                with self.assertRaises(ModuleIndexError):
                    normalize_module_index(case)

    def test_bytes_roundtrip_and_compact(self):
        data = module_index_to_bytes(_index())
        self.assertNotIn(b" ", data.replace("Sostituzione cuscinetti".encode(), b""))
        self.assertEqual(module_index_from_bytes(data), normalize_module_index(_index()))

    def test_from_bytes_rejects_garbage(self):
        for raw in (b"\xff\xfe", b"{non json", b"[]", b'{"v":1}'):
            with self.subTest(raw=raw):
                with self.assertRaises(ModuleIndexError):
                    module_index_from_bytes(raw)


class TestBundleTransport(unittest.TestCase):
    def test_item_roundtrips_through_a_bundle(self):
        data = module_index_to_bytes(_index())
        blob = encode_bundle([BundleItem(KIND_3D, "a.b3d", b"x"),
                              BundleItem(KIND_MODULE_INDEX, "moduli.json", data)])
        items = decode_bundle(blob)
        self.assertEqual(read_module_index_item(items), normalize_module_index(_index()))

    def test_absent_index_is_none(self):
        self.assertIsNone(read_module_index_item([BundleItem(KIND_DOC, "n.txt", b"x")]))

    def test_unreadable_or_future_index_is_tolerated_not_fatal(self):
        future = json.dumps(_index(v=2)).encode()
        for payload in (b"\x00garbage", b"{broken", future):
            with self.subTest(payload=payload[:12]):
                items = [BundleItem(KIND_MODULE_INDEX, "moduli.json", payload)]
                self.assertIsNone(read_module_index_item(items))

    def test_documents_list_ignores_the_index_item(self):
        # un lettore che non conosce il ruolo lo salta: l'indice non diventa
        # un "documento" nell'elenco consultabile
        from balzar.viewer3d import _documents_from_items
        items = [BundleItem(KIND_DOC, "n.txt", b"ciao"),
                 BundleItem(KIND_MODULE_INDEX, "moduli.json", module_index_to_bytes(_index()))]
        self.assertEqual([d["label"] for d in _documents_from_items(items)], ["n.txt"])


class TestWebApi(unittest.TestCase):
    def _encode(self, **extra):
        body = {"data": _b64(_make_3dxml_with_group_bytes())}
        body.update(extra)
        return handle_encode_3d(body, LOCAL_LIMITS)

    def test_encode_with_index_builds_a_bundle_and_echoes_it(self):
        status, resp = self._encode(module_index=_index())
        self.assertEqual(status, 200, resp)
        self.assertTrue(resp["bundled"])
        self.assertEqual(resp["module_index"], normalize_module_index(_index()))
        kinds = [it.kind for it in decode_bundle(base64.b64decode(resp["payload_base64"]))]
        self.assertIn(KIND_MODULE_INDEX, kinds)

    def test_encode_without_index_is_unchanged(self):
        status, resp = self._encode()
        self.assertEqual(status, 200)
        self.assertFalse(resp["bundled"])          # nessun bundle creato di nascosto
        self.assertIsNone(resp["module_index"])
        self.assertEqual({e["name"] for e in resp["bom"]}, {"BoltA", "BoltB"})

    def test_invalid_index_gives_clean_400(self):
        status, resp = self._encode(module_index=_index(idx={"A": [99]}))
        self.assertEqual(status, 400)
        self.assertIn("indice moduli non valido", resp["error"])

    def test_index_never_changes_the_bom(self):
        # un registro puo' elencare l'assieme radice e ogni sotto-assieme:
        # se l'indice facesse collassare la distinta, tutto il modello
        # diventerebbe una riga sola. Deve restare puramente additivo.
        idx = {"v": 1, "docs": [{"c": "D", "t": "T", "r": 1}],
               "idx": {"HEATER1": [0], "BoltA": [0], "Root": [0]}}
        _, with_index = self._encode(module_index=idx)
        _, without = self._encode()
        self.assertEqual(sorted(e["name"] for e in with_index["bom"]), ["BoltA", "BoltB"])
        self.assertEqual(with_index["bom"], without["bom"])
        self.assertEqual(with_index["shape_count"], without["shape_count"])

    def test_alarm_component_still_collapses_with_an_index_present(self):
        from balzar.alarm_graph import AlarmGraph, AlarmNode
        graph = AlarmGraph(alarms=[AlarmNode("A06", "x", "HEATER1")])
        _, resp = self._encode(alarm_graph=graph.to_json_dict(), module_index=_index())
        self.assertEqual([e["name"] for e in resp["bom"]], ["HEATER1"])

    def test_index_does_not_disturb_the_alarm_graph(self):
        from balzar.alarm_graph import AlarmGraph, AlarmNode
        graph = AlarmGraph(alarms=[AlarmNode("A06", "x", "HEATER1")])
        status, resp = self._encode(alarm_graph=graph.to_json_dict(), module_index=_index())
        self.assertEqual(status, 200, resp)
        self.assertEqual(resp["alarm_graph"]["alarms"][0]["code"], "A06")
        self.assertIsNotNone(resp["module_index"])

    def test_open_roundtrip_returns_the_index(self):
        _, enc = self._encode(module_index=_index())
        status, resp = handle_render({"data": enc["payload_base64"]}, LOCAL_LIMITS)
        self.assertEqual(status, 200, resp)
        self.assertEqual(resp["module_index"], normalize_module_index(_index()))
        self.assertEqual({e["name"] for e in resp["bom"]}, {"BoltA", "BoltB"})   # distinta invariata

    def test_open_bundle_with_corrupt_index_still_opens_the_model(self):
        from balzar.scene3d import decode_payload, encode_payload, parse_3dxml
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.3dxml")
            with open(path, "wb") as fh:
                fh.write(_make_3dxml_with_group_bytes())
            payload = encode_payload(parse_3dxml(path))
        blob = encode_bundle([BundleItem(KIND_3D, "a.b3d", payload),
                              BundleItem(KIND_MODULE_INDEX, "moduli.json", b"{rotto")])
        status, resp = handle_render({"data": _b64(blob)}, LOCAL_LIMITS)
        self.assertEqual(status, 200, resp)
        self.assertIsNone(resp["module_index"])
        self.assertTrue(resp["has_3d"])

    def test_bare_3d_payload_reports_no_index(self):
        _, enc = self._encode()
        status, resp = handle_render({"data": enc["payload_base64"]}, LOCAL_LIMITS)
        self.assertEqual(status, 200)
        self.assertIsNone(resp["module_index"])


class TestDesktopViewerPage(unittest.TestCase):
    def _open(self, items):
        from balzar.viewer3d import open_bundle_in_browser
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        with mock.patch("balzar.viewer3d.webbrowser.open"):
            server = open_bundle_in_browser(encode_bundle(items), work)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with open(os.path.join(work, "viewer.html"), encoding="utf-8") as fh:
            return fh.read()

    def _3d_item(self):
        from balzar.scene3d import encode_payload, parse_3dxml
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.3dxml")
            with open(path, "wb") as fh:
                fh.write(_make_3dxml_with_group_bytes())
            return BundleItem(KIND_3D, "a.b3d", encode_payload(parse_3dxml(path)))

    def test_page_with_index_has_command_panel_and_embedded_data(self):
        page = self._open([self._3d_item(),
                           BundleItem(KIND_MODULE_INDEX, "moduli.json", module_index_to_bytes(_index()))])
        self.assertIn('id="modules-btn"', page)
        self.assertIn('id="modules-panel"', page)
        self.assertIn("window.__BALZAR_MODULES__ = {", page)
        self.assertIn("DOC-042", page)
        self.assertIn("BalzarModules", page)         # libreria condivisa incorporata
        self.assertIn("__balzarOnSelection", page)
        self.assertNotIn("{modules_", page)          # nessun segnaposto rimasto

    def test_page_without_index_is_unchanged(self):
        page = self._open([self._3d_item()])
        self.assertNotIn('id="modules-btn"', page)
        self.assertIn("window.__BALZAR_MODULES__ = null;", page)
        self.assertNotIn("BalzarModules.prepare", page)

    def test_script_close_tag_in_a_title_cannot_break_the_page(self):
        evil = {"v": 1, "docs": [{"c": "X", "t": "</script><b>x", "r": 1}], "idx": {"HEATER1": [0]}}
        page = self._open([self._3d_item(), BundleItem(KIND_MODULE_INDEX, "moduli.json",
                                                       module_index_to_bytes(evil))])
        self.assertNotIn("</script><b>x", page)
        self.assertIn("<\\/script><b>x", page)


@unittest.skipUnless(shutil.which("node"), "node non disponibile")
class TestSharedLookupJs(unittest.TestCase):
    def _run(self, expr: str):
        script = ("const M=require(%s);const idx=%s;const p=M.prepare(idx);"
                  "console.log(JSON.stringify(%s));") % (
            json.dumps(os.path.join(ROOT, "balzar-modules.js")), json.dumps(_index()), expr)
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_exact_normalized_merge_and_dedupe(self):
        self.assertEqual([m["c"] for m in self._run('M.lookup(p,["BoltA"])')], ["DOC-019"])
        # grafia diversa (maiuscole, separatori) trova lo stesso componente
        self.assertEqual([m["c"] for m in self._run('M.lookup(p,["heater1"])')], ["DOC-042", "DOC-019"])
        self.assertEqual([m["c"] for m in self._run('M.lookup(p,["BOLTA"])')], ["DOC-019"])
        # un separatore in piu' e' un altro nome: nessun "indovinare" (come in DoqTool)
        self.assertEqual(self._run('M.lookup(p,["bolt-a"])'), [])
        # piu' selezioni: unione senza doppioni, ordine di pertinenza
        self.assertEqual([m["c"] for m in self._run('M.lookup(p,["BoltA","HEATER1"])')], ["DOC-019", "DOC-042"])

    def test_unknown_empty_and_invalid(self):
        self.assertEqual(self._run('M.lookup(p,["Sconosciuto"])'), [])
        self.assertEqual(self._run('M.lookup(p,[])'), [])
        self.assertIsNone(self._run('M.prepare(null)'))
        self.assertIsNone(self._run('M.prepare({docs:[]})'))
        self.assertEqual(self._run('M.lookup(null,["A"])'), [])

    def test_position_pointing_nowhere_is_skipped(self):
        script = ("const M=require(%s);const p=M.prepare({docs:[{c:'D',t:'T',r:1}],idx:{A:[0,7]}});"
                  "console.log(JSON.stringify(M.lookup(p,['A']).map(m=>m.c)));") % (
            json.dumps(os.path.join(ROOT, "balzar-modules.js")))
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(out.stdout), ["D"])

    def test_normname_matches_doqtool(self):
        self.assertEqual(self._run('M.normName("MOTOR_UNIT_01")'), "motor unit 01")
        self.assertEqual(self._run('M.normName(" Cuscinetto  lato è ")'), "cuscinetto lato e")


if __name__ == "__main__":
    unittest.main()
