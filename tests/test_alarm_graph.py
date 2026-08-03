"""balzar/alarm_graph.py -- Slice 1 of the "collega allarmi" feature:
data model, fixed-schema CSV template parsing, JSON round-trip.

No bundle/UI integration exists yet (that's later slices), so this
suite only exercises the module directly."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from balzar.alarm_graph import (AlarmGraph, AlarmGraphError,
                                 parse_alarm_graph_csvs)

ALARMS_CSV = (
    "codice,descrizione\n"
    "E100,Sovratemperatura vasca riscaldo\n"
    "E102,Quadro elettrico non raggiungibile\n"
    "E115,Livello minimo vasca accumulo\n"
)

CAUSES_CSV = (
    "causa_soluzione,allarmi_collegati,procedura_collegata\n"
    "Termostato di sicurezza intervenuto - verificare taratura,E100,PR-014_reset_termostato.pdf\n"
    "Interruttore generale QE aperto - richiudere,E102,\n"
    "Sovraccarico rete elettrica di stabilimento,E102;E115,PR-021_ripristino_QE.pdf\n"
)


class TestParseAlarmGraphCsvs(unittest.TestCase):
    def test_basic_parse_counts_and_content(self):
        graph, warnings = parse_alarm_graph_csvs(ALARMS_CSV, CAUSES_CSV)
        self.assertEqual(warnings, [])
        self.assertEqual([a.code for a in graph.alarms], ["E100", "E102", "E115"])
        self.assertEqual(graph.alarms[0].description, "Sovratemperatura vasca riscaldo")
        self.assertEqual(len(graph.causes), 3)
        # two distinct procedure filenames referenced across the rows
        self.assertEqual(sorted(p.label for p in graph.procedures),
                          ["PR-014_reset_termostato.pdf", "PR-021_ripristino_QE.pdf"])

    def test_alarm_row_with_no_alarms_collegati_column_gets_no_procedure_link(self):
        graph, warnings = parse_alarm_graph_csvs(ALARMS_CSV, CAUSES_CSV)
        # second cause row ("Interruttore generale...") has a blank
        # procedura_collegata -- optional link, no cause_links entry
        interruttore = next(c for c in graph.causes if c.text.startswith("Interruttore"))
        self.assertFalse(any(frm == interruttore.id for frm, _ in graph.cause_links))

    def test_cause_linking_multiple_alarm_codes_via_semicolon(self):
        graph, warnings = parse_alarm_graph_csvs(ALARMS_CSV, CAUSES_CSV)
        sovraccarico = next(c for c in graph.causes if c.text.startswith("Sovraccarico"))
        linked_codes = sorted(code for code, cid in graph.alarm_links if cid == sovraccarico.id)
        self.assertEqual(linked_codes, ["E102", "E115"])

    def test_procedure_referenced_twice_is_deduplicated_by_label(self):
        causes_csv = (
            "causa_soluzione,allarmi_collegati,procedura_collegata\n"
            "Causa A,E100,shared.pdf\n"
            "Causa B,E102,shared.pdf\n"
        )
        graph, warnings = parse_alarm_graph_csvs(ALARMS_CSV, causes_csv)
        self.assertEqual(warnings, [])
        self.assertEqual(len(graph.procedures), 1)
        self.assertEqual(len(graph.cause_links), 2)
        # both cause->procedure links point at the SAME procedure id
        proc_ids = {pid for _, pid in graph.cause_links}
        self.assertEqual(proc_ids, {graph.procedures[0].id})

    def test_unknown_alarm_code_reference_is_a_warning_not_a_crash(self):
        causes_csv = (
            "causa_soluzione,allarmi_collegati,procedura_collegata\n"
            "Causa orfana,E999,\n"
        )
        graph, warnings = parse_alarm_graph_csvs(ALARMS_CSV, causes_csv)
        self.assertEqual(graph.alarm_links, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("E999", warnings[0])

    def test_partially_unknown_row_still_links_the_valid_codes(self):
        causes_csv = (
            "causa_soluzione,allarmi_collegati,procedura_collegata\n"
            "Causa mista,E100;E999,\n"
        )
        graph, warnings = parse_alarm_graph_csvs(ALARMS_CSV, causes_csv)
        self.assertEqual(len(graph.alarm_links), 1)
        self.assertEqual(graph.alarm_links[0][0], "E100")
        self.assertEqual(len(warnings), 1)

    def test_duplicate_alarm_code_raises_and_names_the_code(self):
        bad = "codice,descrizione\nE100,Uno\nE100,Due\n"
        with self.assertRaises(AlarmGraphError) as ctx:
            parse_alarm_graph_csvs(bad, "causa_soluzione,allarmi_collegati,procedura_collegata\n")
        self.assertIn("E100", str(ctx.exception))

    def test_blank_inputs_yield_empty_graph_not_an_error(self):
        graph, warnings = parse_alarm_graph_csvs("", "")
        self.assertEqual(graph.alarms, [])
        self.assertEqual(graph.causes, [])
        self.assertEqual(warnings, [])

    def test_blank_rows_are_skipped(self):
        alarms_csv = "codice,descrizione\nE100,Uno\n\n\nE102,Due\n"
        graph, warnings = parse_alarm_graph_csvs(alarms_csv, "")
        self.assertEqual([a.code for a in graph.alarms], ["E100", "E102"])


class TestUnlinkedAlarmCodes(unittest.TestCase):
    def test_alarm_without_a_cause_link_is_reported(self):
        graph, _ = parse_alarm_graph_csvs(ALARMS_CSV, CAUSES_CSV)
        # E115 only appears via the shared "Sovraccarico" cause; E100 and
        # E102 are directly linked too -- none should be unlinked here
        self.assertEqual(graph.unlinked_alarm_codes(), [])

    def test_alarm_with_zero_links_is_reported(self):
        alarms_csv = "codice,descrizione\nE100,Uno\nE200,Senza causa\n"
        causes_csv = "causa_soluzione,allarmi_collegati,procedura_collegata\nCausa,E100,\n"
        graph, _ = parse_alarm_graph_csvs(alarms_csv, causes_csv)
        self.assertEqual(graph.unlinked_alarm_codes(), ["E200"])

    def test_cause_without_a_procedure_is_not_flagged_unlinked(self):
        # procedures are optional ("eventuali") -- unlinked_alarm_codes
        # only ever looks at alarm_links, never cause_links
        graph, _ = parse_alarm_graph_csvs(ALARMS_CSV, CAUSES_CSV)
        self.assertEqual(graph.unlinked_alarm_codes(), [])


class TestJsonRoundtrip(unittest.TestCase):
    def test_to_json_dict_and_back_preserves_everything(self):
        graph, _ = parse_alarm_graph_csvs(ALARMS_CSV, CAUSES_CSV)
        restored = AlarmGraph.from_json_dict(graph.to_json_dict())
        self.assertEqual(restored, graph)

    def test_empty_graph_roundtrip(self):
        graph = AlarmGraph()
        restored = AlarmGraph.from_json_dict(graph.to_json_dict())
        self.assertEqual(restored, graph)

    def test_from_json_dict_tolerates_missing_keys(self):
        # a hand-written/partial dict (e.g. from an older schema) should
        # not crash -- every key defaults to empty via .get(..., [])
        restored = AlarmGraph.from_json_dict({})
        self.assertEqual(restored, AlarmGraph())


if __name__ == "__main__":
    unittest.main()
