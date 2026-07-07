"""balzar/viewer3d.py: parse_alarm_csv, the one piece of this module with
real parsing logic worth a unit test (the rest is HTML/JS template
plumbing, verified manually with Playwright per the project's convention
for client-side 3D viewer behaviour -- see CLAUDE.md)."""

import tempfile
import unittest
from pathlib import Path

from balzar.viewer3d import parse_alarm_csv


def _write_csv(text: str) -> str:
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                     newline="", encoding="utf-8")
    fh.write(text)
    fh.close()
    return fh.name


class TestParseAlarmCsv(unittest.TestCase):
    def test_basic_two_column_csv(self):
        path = _write_csv("E204,Bullone-M6\nE310,VASCA_ACCUMULO_SUB009\n")
        self.assertEqual(parse_alarm_csv(path),
                         [("E204", "Bullone-M6"), ("E310", "VASCA_ACCUMULO_SUB009")])
        Path(path).unlink()

    def test_header_row_is_detected_and_skipped(self):
        path = _write_csv("codice_allarme,nome_componente\nE204,Bullone-M6\n")
        self.assertEqual(parse_alarm_csv(path), [("E204", "Bullone-M6")])
        Path(path).unlink()

    def test_header_row_not_assumed_when_absent(self):
        # first row IS data -- must not be dropped just because it's first
        path = _write_csv("E204,Bullone-M6\nE310,VASCA_ACCUMULO_SUB009\n")
        rows = parse_alarm_csv(path)
        self.assertEqual(len(rows), 2)
        Path(path).unlink()

    def test_one_alarm_code_maps_to_several_components(self):
        path = _write_csv("E204,Bullone-M6\nE204,Object-13\n")
        self.assertEqual(parse_alarm_csv(path),
                         [("E204", "Bullone-M6"), ("E204", "Object-13")])
        Path(path).unlink()

    def test_third_column_is_accepted_and_does_not_corrupt_the_name(self):
        # regression: an earlier version built name as ",".join(cells[1:]),
        # which glued a third column (e.g. a linked procedure document,
        # CLAUDE.md SS9.19) onto the component name instead of ignoring it
        path = _write_csv("codice_allarme,nome_componente,documento_procedura\n"
                          "A06,HEATER1,procedura_heater\nA07,RESERVOIR1,procedura_reservoir\n")
        self.assertEqual(parse_alarm_csv(path),
                         [("A06", "HEATER1"), ("A07", "RESERVOIR1")])
        Path(path).unlink()

    def test_component_name_containing_a_comma_is_preserved(self):
        # csv.reader handles quoting properly, unlike the browser-side
        # plain split() -- this is exactly the case that parser can't do
        path = _write_csv('E204,"Bullone, M6 lungo"\n')
        self.assertEqual(parse_alarm_csv(path), [("E204", "Bullone, M6 lungo")])
        Path(path).unlink()

    def test_blank_lines_and_short_rows_are_skipped(self):
        path = _write_csv("E204,Bullone-M6\n\nE310\n,\nE311,Object-13\n")
        self.assertEqual(parse_alarm_csv(path),
                         [("E204", "Bullone-M6"), ("E311", "Object-13")])
        Path(path).unlink()

    def test_empty_file_returns_empty_list(self):
        path = _write_csv("")
        self.assertEqual(parse_alarm_csv(path), [])
        Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
