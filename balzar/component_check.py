"""Controlli di coerenza tra i nomi usati dai contenuti di un bundle (componenti
degli allarmi, chiavi dell'indice dei moduli) e il modello 3D.

Il motore e' volutamente muto su un nome sbagliato: un componente che non
corrisponde a nessuna parte viene ignorato (e' coperto da un test, vedi
test_no_alarm_match_leaves_bom_uncollapsed) perche' un bundle non deve
fallire per un refuso. Il costo e' che l'autore scopre l'errore solo in
campo, quando il componente non si evidenzia. Questo modulo restituisce
AVVISI leggibili, mai errori: chi codifica li vede subito, il bundle si
produce comunque.

Solo funzioni pure: nessuna UI, nessuna dipendenza dal motore oltre alla
scena gia' decodificata.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

MAX_ALARM_WARNINGS = 20
MAX_EXAMPLES = 5


def normalize_name(s: str) -> str:
    """Stessa normalizzazione di balzar-modules.js e di DoqTool: minuscole,
    senza accenti, separatori (_ - . /) come spazio, spazi collassati."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[_\-./]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _examples(names: list[str]) -> str:
    shown = ", ".join(f"«{n}»" for n in names[:MAX_EXAMPLES])
    return shown + (f" e altri {len(names) - MAX_EXAMPLES}" if len(names) > MAX_EXAMPLES else "")


def _closest(name: str, candidates: set[str]) -> str | None:
    by_norm = {}
    for c in sorted(candidates):
        by_norm.setdefault(normalize_name(c), c)
    hit = difflib.get_close_matches(normalize_name(name), list(by_norm), n=1, cutoff=0.8)
    return by_norm[hit[0]] if hit else None


def component_warnings(scene, alarm_graph: dict | None = None, module_index: dict | None = None,
                       bom_names: list[str] | None = None) -> list[str]:
    """Avvisi sui nomi che non corrispondono al modello.

    `bom_names` = le etichette di distinta effettivamente mostrate (con i
    gruppi gia' compressi); se manca, si ricalcolano comprimendo i
    componenti del grafo allarmi, come fa la codifica.

    Controlla solo cio' che e' esplicito: i componenti di un grafo allarmi e
    le chiavi dell'indice moduli. Una tabella allarmi CSV ha colonne libere e
    non si puo' sapere quale sia quella dei componenti (la individua la UI
    confrontando i valori con la distinta), quindi non e' controllata."""
    from .scene3d import generate_bom

    if bom_names is None:
        collapse = {a["component"] for a in (alarm_graph or {}).get("alarms", []) if a.get("component")}
        bom_names = [e.name for e in generate_bom(scene, collapse or None)]
    selectable = set(bom_names)
    group_names = {r.name for r in scene.references if r.name and r.shape_index is None}
    leaf_names = {r.name for r in scene.references if r.name and r.shape_index is not None}
    known = selectable | group_names | leaf_names

    warnings: list[str] = []

    bad_alarms = []
    for a in (alarm_graph or {}).get("alarms", []):
        comp = (a.get("component") or "").strip()
        if comp and comp not in selectable:
            bad_alarms.append((a.get("code", "?"), comp))
    for code, comp in bad_alarms[:MAX_ALARM_WARNINGS]:
        same = next((k for k in sorted(known) if normalize_name(k) == normalize_name(comp)), None)
        if same:
            warnings.append(f"allarme {code}: nel modello il componente si chiama «{same}», non «{comp}» "
                            f"(grafia diversa): non verrebbe evidenziato.")
            continue
        near = _closest(comp, known)
        hint = f" Forse intendevi «{near}»." if near else ""
        warnings.append(f"allarme {code}: il componente «{comp}» non esiste nel modello 3D, "
                        f"nessuna parte verrebbe evidenziata.{hint}")
    if len(bad_alarms) > MAX_ALARM_WARNINGS:
        warnings.append(f"… e altri {len(bad_alarms) - MAX_ALARM_WARNINGS} allarmi con componente non trovato nel modello.")

    if module_index:
        sel_norm = {normalize_name(n) for n in selectable}
        group_norm = {normalize_name(n) for n in group_names}
        leaf_norm = {normalize_name(n) for n in leaf_names}
        missing, groups_only = [], []
        for key in module_index.get("idx", {}):
            k = normalize_name(key)
            if k in sel_norm:
                continue
            if k in leaf_norm and k not in group_norm:
                # a real part hidden inside a sub-assembly that an alarm collapses
                # into one row: not selectable on its own, but DoqTool already
                # folds its modules under the group's key -- nothing is lost
                continue
            (groups_only if k in group_norm else missing).append(key)
        if missing:
            warnings.append(f"indice moduli: {len(missing)} componente/i senza corrispondenza nel modello 3D "
                            f"(i loro moduli non compariranno): {_examples(missing)}.")
        if groups_only:
            warnings.append(f"indice moduli: {len(groups_only)} sotto-assieme/i senza riga selezionabile nella "
                            f"distinta, perche' nessun allarme lo comprime (i loro moduli non compariranno): "
                            f"{_examples(groups_only)}.")
    return warnings


def bundle_warnings(items) -> list[str]:
    """Come component_warnings, partendo dagli item di un bundle gia' decodificato
    (per CLI e GUI). Nessun avviso se manca il modello 3D o se non c'e' nulla da
    controllare."""
    from .alarm_graph import AlarmGraph
    from .bundle import KIND_3D, KIND_ALARM_GRAPH
    from .module_index import read_module_index_item
    from .scene3d import Scene3DError, decode_payload

    three_d = next((it for it in items if it.kind == KIND_3D), None)
    if three_d is None:
        return []
    graph_item = next((it for it in items if it.kind == KIND_ALARM_GRAPH), None)
    try:
        graph = AlarmGraph.from_bytes(graph_item.data).to_json_dict() if graph_item else None
        scene = decode_payload(three_d.data)
    except (ValueError, KeyError, Scene3DError):
        return []
    return component_warnings(scene, graph, read_module_index_item(items))
