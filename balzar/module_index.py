"""Indice offline componente -> moduli di documentazione.

Un piccolo JSON prodotto da DoqTool (il sistema che scrive la documentazione)
e portato nel bundle come item KIND_MODULE_INDEX. Non contiene i documenti,
solo i riferimenti per ritrovarli fuori dalla macchina (manuale stampato,
PDF, tablet): per ogni componente, quali moduli ne parlano.

    {"v": 1,
     "docs": [{"c": "DOC-042", "t": "Sostituzione cuscinetti", "r": 3, "s": "5.2"}],
     "idx":  {"MOTOR_UNIT_01": [0], ...}}

`docs` e' la tabella dei moduli (ogni titolo una volta sola); `idx` mappa
l'etichetta BOM del componente alle posizioni in `docs`, gia' in ordine di
pertinenza. `c` e' il codice stabile del modulo, `r` la revisione al momento
dell'export, `s` capitolo/pagina (facoltativo).

Come per alarm_graph.py, questo modulo e' solo modello dati + validazione:
nessuna UI, nessun legame col motore deterministico. Un indice e' sempre
opzionale; chi lo legge deve tollerare la sua assenza o un formato futuro.
"""

from __future__ import annotations

import json

SUPPORTED_VERSION = 1
MAX_DOCS = 5000
MAX_KEYS = 20000
MAX_BYTES = 2_000_000


class ModuleIndexError(ValueError):
    pass


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def normalize_module_index(obj) -> dict:
    """Valida e restituisce una copia pulita (solo i campi noti, nessun
    contenuto arbitrario portato nel bundle). Solleva ModuleIndexError."""
    if not isinstance(obj, dict):
        raise ModuleIndexError("atteso un oggetto JSON")
    if obj.get("v") != SUPPORTED_VERSION:
        raise ModuleIndexError(f"versione non supportata: {obj.get('v')!r}")
    docs_in, idx_in = obj.get("docs"), obj.get("idx")
    if not isinstance(docs_in, list) or not isinstance(idx_in, dict):
        raise ModuleIndexError("campi 'docs' (lista) e 'idx' (oggetto) richiesti")
    if len(docs_in) > MAX_DOCS or len(idx_in) > MAX_KEYS:
        raise ModuleIndexError("indice troppo grande")

    docs: list[dict] = []
    for n, d in enumerate(docs_in):
        if not isinstance(d, dict):
            raise ModuleIndexError(f"modulo {n}: atteso un oggetto")
        code, title, rev = d.get("c"), d.get("t"), d.get("r", 0)
        if not isinstance(code, str) or not code.strip():
            raise ModuleIndexError(f"modulo {n}: codice 'c' mancante")
        if not isinstance(title, str):
            raise ModuleIndexError(f"modulo {n}: titolo 't' non valido")
        if not _is_int(rev) or rev < 0:
            raise ModuleIndexError(f"modulo {n}: revisione 'r' non valida")
        entry = {"c": code.strip(), "t": title, "r": rev}
        section = d.get("s")
        if section is not None:
            if not isinstance(section, str):
                raise ModuleIndexError(f"modulo {n}: 's' deve essere testo")
            if section.strip():
                entry["s"] = section
        docs.append(entry)

    idx: dict[str, list[int]] = {}
    for key, positions in idx_in.items():
        if not isinstance(key, str) or not key.strip():
            raise ModuleIndexError("chiave componente vuota o non testuale")
        if not isinstance(positions, list):
            raise ModuleIndexError(f"{key!r}: atteso un elenco di posizioni")
        seen: list[int] = []
        for p in positions:
            if not _is_int(p) or not 0 <= p < len(docs):
                raise ModuleIndexError(f"{key!r}: posizione {p!r} fuori dalla tabella dei moduli")
            if p not in seen:
                seen.append(p)
        idx[key] = seen
    return {"v": SUPPORTED_VERSION, "docs": docs, "idx": idx}


def module_index_to_bytes(obj) -> bytes:
    data = json.dumps(normalize_module_index(obj), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ModuleIndexError("indice troppo grande")
    return data


def module_index_from_bytes(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ModuleIndexError("indice troppo grande")
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModuleIndexError(f"JSON non valido: {exc}") from None
    return normalize_module_index(obj)


def read_module_index_item(items) -> dict | None:
    """Il primo item KIND_MODULE_INDEX di un bundle, validato; None se
    manca. Tollerante di proposito: un indice illeggibile o di una
    versione futura NON deve impedire di aprire il modello 3D e gli altri
    contenuti, quindi qui diventa semplicemente 'nessun indice'."""
    from .bundle import KIND_MODULE_INDEX

    item = next((it for it in items if it.kind == KIND_MODULE_INDEX), None)
    if item is None:
        return None
    try:
        return module_index_from_bytes(item.data)
    except ModuleIndexError:
        return None
