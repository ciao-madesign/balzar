// Abbinamento componente selezionato -> moduli di documentazione.
//
// UNA sola implementazione, usata sia dalla UI web (index.html/app.js) sia
// dalla pagina del visualizzatore desktop (balzar/viewer3d.py, che ne incorpora
// il testo): cosi' le due non possono divergere. Nessuna dipendenza, nessun
// accesso alla rete: l'indice viaggia nel bundle (balzar/module_index.py).
(function (root) {
  "use strict";

  // Stessa normalizzazione usata da DoqTool: minuscole, senza accenti,
  // separatori (_ - . /) come spazio, spazi collassati.
  function normName(s) {
    return String(s == null ? "" : s)
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[_\-./]+/g, " ").replace(/\s+/g, " ").trim();
  }

  // Indice grezzo -> struttura di ricerca; null se non utilizzabile.
  function prepare(index) {
    if (!index || !Array.isArray(index.docs) || !index.idx || typeof index.idx !== "object") return null;
    var exact = new Map(), norm = new Map();
    Object.keys(index.idx).forEach(function (key) {
      var positions = Array.isArray(index.idx[key]) ? index.idx[key] : [];
      exact.set(key, positions);
      var nk = normName(key);
      var merged = norm.get(nk) || [];
      positions.forEach(function (p) { if (merged.indexOf(p) < 0) merged.push(p); });
      norm.set(nk, merged);
    });
    return { docs: index.docs, exact: exact, norm: norm };
  }

  // Moduli per le etichette selezionate, senza doppioni, nell'ordine di
  // pertinenza (prima etichetta, poi le successive).
  function lookup(prepared, labels) {
    if (!prepared) return [];
    var seen = new Set(), out = [];
    (labels || []).forEach(function (label) {
      var positions = prepared.exact.get(label) || prepared.norm.get(normName(label)) || [];
      positions.forEach(function (p) {
        var doc = prepared.docs[p];
        if (doc && !seen.has(p)) { seen.add(p); out.push(doc); }
      });
    });
    return out;
  }

  var api = { normName: normName, prepare: prepare, lookup: lookup };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BalzarModules = api;
})(typeof window !== "undefined" ? window : globalThis);
