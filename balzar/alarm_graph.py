"""Alarm -> cause/solution -> procedure link graph, for the "collega
allarmi" flow discussed alongside 3D+alarms encoding.

Always exactly three node kinds in two fixed tiers -- never a generic
node-link graph. That is a deliberate scope cut (no overcoding): the
feature this supports is specifically "an alarm has a cause/solution,
which may have a procedure document", not an arbitrary graph editor, so
the model mirrors that shape directly instead of building a generic
typed-graph abstraction that would need its own id-namespacing rules to
stay safe. Two separate link lists -- alarm->cause and cause->procedure
-- follow from the same reasoning: an alarm CODE (user data, arbitrary
format) can never collide with a synthetic cause/procedure id, because
the two are never compared against each other.

This module is Slice 1 of the feature (data model + fixed-schema CSV
template parsing + JSON (de)serialization only). No UI, no bundle.py
integration yet -- see CLAUDE.md for the phased plan and what each
later slice adds.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field


class AlarmGraphError(ValueError):
    pass


@dataclass
class AlarmNode:
    code: str
    description: str
    # optional BOM part/group name to highlight in the 3D viewer when this
    # alarm is opened (Slice 3) -- blank if the alarm has no single 3D
    # component to point at. Matched by generate_bom/scene3d_to_glb's
    # existing "any candidate string, unmatched ones ignored" convention
    # (SS9.21), same as ComponentTable's collapse_names today -- no new
    # matching logic needed for this to be safe.
    component: str = ""


@dataclass
class CauseNode:
    id: str
    text: str


@dataclass
class ProcedureNode:
    id: str
    label: str  # filename of a document meant to be carried in the same
                # bundle as a KIND_DOC item (balzar/bundle.py) -- this
                # module does not read/validate that file itself, that
                # cross-check belongs to Slice 2's bundle integration


@dataclass
class AlarmGraph:
    alarms: list[AlarmNode] = field(default_factory=list)
    causes: list[CauseNode] = field(default_factory=list)
    procedures: list[ProcedureNode] = field(default_factory=list)
    alarm_links: list[tuple[str, str]] = field(default_factory=list)   # (alarm.code, cause.id)
    cause_links: list[tuple[str, str]] = field(default_factory=list)   # (cause.id, procedure.id)

    def unlinked_alarm_codes(self) -> list[str]:
        """Alarm codes with no outgoing link -- the "non collegato" state
        that should block encoding in the editor (Slice 4). A cause with
        no linked procedure is NOT flagged here: procedures are optional
        ("eventuali procedure"), so an unlinked cause is a normal state,
        not an error -- only an alarm without any cause is."""
        linked = {code for code, _ in self.alarm_links}
        return [a.code for a in self.alarms if a.code not in linked]

    def to_json_dict(self) -> dict:
        return {
            "alarms": [{"code": a.code, "description": a.description, "component": a.component}
                       for a in self.alarms],
            "causes": [{"id": c.id, "text": c.text} for c in self.causes],
            "procedures": [{"id": p.id, "label": p.label} for p in self.procedures],
            "alarm_links": [[code, cause_id] for code, cause_id in self.alarm_links],
            "cause_links": [[cause_id, proc_id] for cause_id, proc_id in self.cause_links],
        }

    @staticmethod
    def from_json_dict(d: dict) -> AlarmGraph:
        return AlarmGraph(
            alarms=[AlarmNode(a["code"], a["description"], a.get("component", ""))
                    for a in d.get("alarms", [])],
            causes=[CauseNode(c["id"], c["text"]) for c in d.get("causes", [])],
            procedures=[ProcedureNode(p["id"], p["label"]) for p in d.get("procedures", [])],
            alarm_links=[(l[0], l[1]) for l in d.get("alarm_links", [])],
            cause_links=[(l[0], l[1]) for l in d.get("cause_links", [])],
        )

    def to_bytes(self) -> bytes:
        """UTF-8 JSON -- the native form carried by a KIND_ALARM_GRAPH
        bundle item (balzar/bundle.py, Slice 2). A graph is a handful of
        short strings, nowhere near the size where a binary format would
        earn its own inspectability cost (unlike BZM1, see CLAUDE.md
        SS9.4 point 2), so plain JSON stays the whole story here."""
        return json.dumps(self.to_json_dict(), ensure_ascii=False).encode("utf-8")

    @staticmethod
    def from_bytes(data: bytes) -> AlarmGraph:
        return AlarmGraph.from_json_dict(json.loads(data.decode("utf-8")))


def _read_rows(text: str) -> list[list[str]]:
    """CSV text -> data rows, header (row 0) dropped. Column MEANING is
    fixed by position, not by header name (see the two template schemas
    below) -- so unlike parse_component_table_text (viewer3d.py), which
    validates the header because it drives arbitrary-column display,
    there is nothing to validate here: a header row is always assumed
    present and simply skipped, and an entirely blank file yields no
    rows at all (the ordinary "nothing uploaded yet" state, not an
    error -- same convention parse_component_table_text uses)."""
    rows = list(csv.reader(io.StringIO(text)))
    return rows[1:] if rows else []


def parse_alarm_graph_csvs(alarms_csv_text: str, causes_csv_text: str) -> tuple[AlarmGraph, list[str]]:
    """Parse the two fixed-schema template CSVs into a starting graph,
    plus a list of human-readable warnings for anything skipped (never
    silently dropped -- same "declare what didn't make it" principle
    vectorio.py's ingest functions already use for skipped entities).

    allarmi_template.csv          columns: codice, descrizione, componente (opzionale)
    cause_soluzioni_template.csv  columns: causa_soluzione, allarmi_collegati, procedura_collegata

    componente: il nome del gruppo/parte 3D da evidenziare quando questo
    allarme viene aperto nel pannello di lettura (Slice 3) -- vuoto se
    l'allarme non punta a un componente singolo. Un valore che non
    corrisponde a nessun gruppo reale dell'assieme viene semplicemente
    ignorato al momento della vista, mai un errore qui: questo modulo non
    ha visibilita' sulla scena 3D per verificarlo.
      - allarmi_collegati: alarm codes separated by ';', pre-linking this
        cause to those alarms -- a starting point the visual editor
        (Slice 4) then lets the user refine by hand, not a locked-in
        structure.
      - procedura_collegata: a document filename, blank if none yet.

    A duplicate alarm code is a hard error (codes are the join key used
    by allarmi_collegati, so an ambiguous one breaks linking); an
    allarmi_collegati code that doesn't match any parsed alarm is a
    warning, not a crash -- that row's other codes (if any) still link
    normally, only the unresolved reference is dropped."""
    warnings: list[str] = []

    alarms: list[AlarmNode] = []
    seen_codes: set[str] = set()
    for row in _read_rows(alarms_csv_text):
        code = (row[0] if len(row) > 0 else "").strip()
        if not code:
            continue
        if code in seen_codes:
            raise AlarmGraphError(f"codice allarme duplicato nel file allarmi: {code!r}")
        seen_codes.add(code)
        description = (row[1] if len(row) > 1 else "").strip()
        component = (row[2] if len(row) > 2 else "").strip()
        alarms.append(AlarmNode(code, description, component))

    causes: list[CauseNode] = []
    procedures: list[ProcedureNode] = []
    alarm_links: list[tuple[str, str]] = []
    cause_links: list[tuple[str, str]] = []
    proc_id_by_label: dict[str, str] = {}

    for row in _read_rows(causes_csv_text):
        text = (row[0] if len(row) > 0 else "").strip()
        if not text:
            continue
        cause_id = f"cause:{len(causes) + 1}"
        causes.append(CauseNode(cause_id, text))

        codes_field = row[1] if len(row) > 1 else ""
        for code in (c.strip() for c in codes_field.split(";")):
            if not code:
                continue
            if code not in seen_codes:
                warnings.append(
                    f"causa {cause_id!r}: codice allarme {code!r} non trovato nel file allarmi, collegamento ignorato")
                continue
            alarm_links.append((code, cause_id))

        label = (row[2] if len(row) > 2 else "").strip()
        if label:
            proc_id = proc_id_by_label.get(label)
            if proc_id is None:
                proc_id = f"proc:{len(procedures) + 1}"
                proc_id_by_label[label] = proc_id
                procedures.append(ProcedureNode(proc_id, label))
            cause_links.append((cause_id, proc_id))

    graph = AlarmGraph(alarms, causes, procedures, alarm_links, cause_links)
    return graph, warnings
