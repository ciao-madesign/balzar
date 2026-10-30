"""Elenco dei componenti di un assieme 3D come `componenti.csv`, nel formato
che il registro componenti di DoqTool importa.

La distinta di Balzar e' piatta (nome, quantita', materiali) e non ha codici
articolo, ma la scena e' un grafo di riferimenti: i sotto-assiemi con nome sono
gruppi. Qui si esporta l'albero intero, cosi' DoqTool riceve gli stessi nomi
che il visualizzatore mostra e la gerarchia dell'assieme (le sue chiavi di
ricerca per i moduli di documentazione ereditano dai figli, vedi
CLAUDE.md SS15).

Colonne (DoqTool le legge per NOME, quelle che non conosce le ignora):
  codice      sempre vuoto: il codice articolo non esiste nel modello 3D e lo
              assegna chi gestisce il registro in DoqTool (finche' non c'e',
              DoqTool usa l'etichetta come codice provvisorio)
  etichetta   il nome esatto che Balzar mostra nella distinta (e che usano
              allarmi e indice moduli per ritrovare il componente)
  nome        vuoto: il nome da usare nei documenti lo sceglie chi scrive
  materiali   i nomi dei materiali glTF da evidenziare (separati da ';')
  conteggio   quante volte il componente e' posizionato nell'assieme
  livello     0 = direttamente sotto la radice, 1 = dentro un sotto-assieme...
  padre       etichetta del sotto-assieme che lo contiene ('' se nessuno)

Limiti dichiarati: una parte riusata in piu' sotto-assiemi ha un solo padre (il
primo in cui compare); la radice dell'assieme non e' una riga (e' il
contenitore); due righe con la stessa etichetta si riducono alla prima.
"""

from __future__ import annotations

import csv
import io

from .scene3d import (Scene3D, _collect_leaf_material_names, effective_display_name)

HEADER = ["codice", "etichetta", "nome", "materiali", "conteggio", "livello", "padre"]


def component_rows(scene: Scene3D) -> list[dict]:
    """Righe in ordine padre -> figli. Ogni riga: label, materials (lista),
    count, level, parent."""
    rows: dict[str, dict] = {}
    order: list[str] = []

    def add(label: str, materials: list[str], level: int, parent: str) -> dict | None:
        if label in rows:
            rows[label]["count"] += 1
            return None
        rows[label] = {"label": label, "materials": materials, "count": 1,
                       "level": level, "parent": parent}
        order.append(label)
        return rows[label]

    def walk(idx: int, parent_ref, group: str, level: int) -> None:
        ref = scene.references[idx]
        if ref.shape_index is not None:
            label = effective_display_name(parent_ref, ref)
            add(label, [label], level, group)
            return
        next_group, next_level = group, level
        if idx != scene.root and ref.name:
            add(ref.name, sorted(_collect_leaf_material_names(scene, idx, ref.name)), level, group)
            next_group, next_level = ref.name, level + 1
        for target, _inst_name, _matrix in ref.children:
            walk(target, ref, next_group, next_level)

    walk(scene.root, None, "", 0)
    return [rows[label] for label in order]


def components_csv(scene: Scene3D) -> str:
    """`componenti.csv` come testo (UTF-8, separatore ',', RFC 4180)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for r in component_rows(scene):
        writer.writerow(["", r["label"], "", ";".join(r["materials"]), r["count"], r["level"], r["parent"]])
    return out.getvalue()
