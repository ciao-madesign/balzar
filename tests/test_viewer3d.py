"""balzar/viewer3d.py: parse_component_table, the one piece of this
module with real parsing logic worth a unit test (the rest is HTML/JS
template plumbing, verified manually with Playwright per the project's
convention for client-side 3D viewer behaviour -- see CLAUDE.md)."""

import tempfile
import unittest
from pathlib import Path

from balzar.viewer3d import ComponentTable, parse_component_table


def _write_csv(text: str) -> str:
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                     newline="", encoding="utf-8")
    fh.write(text)
    fh.close()
    return fh.name


class TestParseComponentTable(unittest.TestCase):
    def test_arbitrary_columns_in_any_order(self):
        path = _write_csv(
            "nome componente,codice,funzione,allarme,procedure,ricambio,info\n"
            "HEATER1,C001,riscaldo,A06,procedura_heater,RIC-01,pulire ogni 6 mesi\n")
        table = parse_component_table(path)
        self.assertEqual(table.headers,
                         ["nome componente", "codice", "funzione", "allarme",
                          "procedure", "ricambio", "info"])
        self.assertEqual(table.rows, [["HEATER1", "C001", "riscaldo", "A06",
                                       "procedura_heater", "RIC-01", "pulire ogni 6 mesi"]])
        Path(path).unlink()

    def test_header_row_is_always_the_first_row_no_heuristic(self):
        # unlike the old fixed two-column format, there is no attempt to
        # guess whether row 0 is a header -- it always is
        path = _write_csv("codice_allarme,nome_componente\nE204,Bullone-M6\n")
        table = parse_component_table(path)
        self.assertEqual(table.headers, ["codice_allarme", "nome_componente"])
        self.assertEqual(table.rows, [["E204", "Bullone-M6"]])
        Path(path).unlink()

    def test_missing_header_raises_a_clear_error(self):
        # an empty first line can't be a header -- with free-form columns
        # there is no way to know what any column means without one
        path = _write_csv("\nE204,Bullone-M6\n")
        with self.assertRaises(ValueError) as ctx:
            parse_component_table(path)
        self.assertIn("intestazione", str(ctx.exception))
        Path(path).unlink()

    def test_short_rows_are_padded_with_empty_cells(self):
        # a row missing trailing values (e.g. no "info" filled in) is
        # not dropped -- it's padded to the header's width instead
        path = _write_csv("componente,allarme,info\nBullone-M6,E204\n")
        table = parse_component_table(path)
        self.assertEqual(table.rows, [["Bullone-M6", "E204", ""]])
        Path(path).unlink()

    def test_long_rows_are_truncated_to_header_width(self):
        path = _write_csv("componente,allarme\nBullone-M6,E204,extra,junk\n")
        table = parse_component_table(path)
        self.assertEqual(table.rows, [["Bullone-M6", "E204"]])
        Path(path).unlink()

    def test_one_value_can_appear_on_several_rows(self):
        path = _write_csv("componente,allarme\nBullone-M6,E204\nObject-13,E204\n")
        table = parse_component_table(path)
        self.assertEqual(table.rows, [["Bullone-M6", "E204"], ["Object-13", "E204"]])
        Path(path).unlink()

    def test_component_name_containing_a_comma_is_preserved(self):
        # csv.reader handles quoting properly, unlike the browser-side
        # plain split() -- this is exactly the case that parser can't do
        path = _write_csv('componente,allarme\n"Bullone, M6 lungo",E204\n')
        table = parse_component_table(path)
        self.assertEqual(table.rows, [["Bullone, M6 lungo", "E204"]])
        Path(path).unlink()

    def test_blank_lines_are_skipped(self):
        path = _write_csv("componente,allarme\nBullone-M6,E204\n\nObject-13,E311\n")
        table = parse_component_table(path)
        self.assertEqual(table.rows, [["Bullone-M6", "E204"], ["Object-13", "E311"]])
        Path(path).unlink()

    def test_empty_file_returns_an_empty_table_not_an_error(self):
        path = _write_csv("")
        table = parse_component_table(path)
        self.assertEqual(table.headers, [])
        self.assertEqual(table.rows, [])
        Path(path).unlink()

    def test_all_values_collects_every_non_empty_cell(self):
        table = ComponentTable(
            headers=["componente", "allarme", "info"],
            rows=[["HEATER1", "A06", "pulire ogni 6 mesi"],
                 ["Bullone-M6", "A06", ""]])
        self.assertEqual(table.all_values(),
                         {"HEATER1", "A06", "pulire ogni 6 mesi", "Bullone-M6"})

    def test_all_values_is_empty_for_an_empty_table(self):
        self.assertEqual(ComponentTable(headers=[], rows=[]).all_values(), set())

    def test_to_json_dict_shape(self):
        table = ComponentTable(headers=["a", "b"], rows=[["1", "2"]])
        self.assertEqual(table.to_json_dict(), {"headers": ["a", "b"], "rows": [["1", "2"]]})


if __name__ == "__main__":
    unittest.main()
