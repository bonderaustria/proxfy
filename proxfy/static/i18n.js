"use strict";
// Zweisprachigkeit.
//
// Deutsch ist die Quelle: es steht im Markup und im Code, so wie es angezeigt
// wird. Englisch liegt daneben in einem Verzeichnis, dessen Schluessel der
// deutsche Text selbst ist. Ein neues Feature heisst damit: deutschen Text
// schreiben, eine englische Zeile ergaenzen. Fehlt sie, erscheint Deutsch -
// sichtbar und behebbar, statt einer leeren Stelle oder eines Schluessels.

const EN = {};   // wird von i18n-en.js gefuellt

let SPRACHE = "de";

/** Uebersetzt einen Text. Unbekanntes bleibt, wie es ist. */
function t(text) {
  if (SPRACHE === "de") return text;
  const s = String(text);
  if (EN[s] !== undefined) return EN[s];
  // Auch mit Satzzeichen am Rand noch treffen - im Markup steht oft ein
  // Doppelpunkt oder ein Punkt direkt hinter dem Text.
  const kern = s.replace(/^[\s]+|[\s]+$/g, "");
  return EN[kern] !== undefined ? EN[kern] : s;
}

/** Setzt Zahl und Text zusammen: mengen(3, "Gast", "Gäste"). */
function mengen(n, ein, viele) {
  return `${n} ${n === 1 ? t(ein) : t(viele)}`;
}

// --- Statisches Markup -------------------------------------------------------
// Beim ersten Lauf wird festgehalten, was urspruenglich dastand. Danach laesst
// sich jederzeit umschalten, ohne dass sich Uebersetzungen aufeinanderstapeln.
// Bewusst nur der Bestand beim Laden: alles, was app.js spaeter erzeugt, ist
// dort schon durch t() gegangen.
let BESTAND = null;

function bestandAufnehmen() {
  const knoten = [];
  const lauf = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const eltern = n.parentNode;
      if (!eltern) return NodeFilter.FILTER_REJECT;
      const tag = eltern.nodeName;
      if (tag === "SCRIPT" || tag === "STYLE") return NodeFilter.FILTER_REJECT;
      // Bereiche, die app.js selbst fuellt, bleiben unangetastet.
      if (eltern.closest("[data-dynamisch]")) return NodeFilter.FILTER_REJECT;
      return n.nodeValue.trim().length > 1
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });
  let n;
  while ((n = lauf.nextNode())) knoten.push([n, n.nodeValue]);

  const attribute = [];
  for (const el of document.querySelectorAll("[placeholder],[title]")) {
    for (const a of ["placeholder", "title"]) {
      const w = el.getAttribute(a);
      if (w && w.trim().length > 1) attribute.push([el, a, w]);
    }
  }
  return { knoten, attribute };
}

/** Traegt die aktuelle Sprache in das statische Markup ein. */
function seiteUebersetzen() {
  if (!BESTAND) BESTAND = bestandAufnehmen();
  for (const [knoten, urtext] of BESTAND.knoten) {
    // Fuehrende und folgende Leerzeichen erhalten - sie tragen im Fliesstext
    // den Abstand zwischen zwei Elementen.
    const m = urtext.match(/^(\s*)([\s\S]*?)(\s*)$/);
    knoten.nodeValue = m[1] + t(m[2].replace(/\s+/g, " ")) + m[3];
  }
  for (const [el, name, urtext] of BESTAND.attribute) {
    el.setAttribute(name, t(urtext));
  }
  document.documentElement.lang = SPRACHE;
}

/** Setzt die Sprache und zeichnet das statische Markup neu. */
function spracheSetzen(sprache) {
  SPRACHE = sprache === "en" ? "en" : "de";
  try { localStorage.setItem("proxfy-sprache", SPRACHE); } catch (e) { /* egal */ }
  seiteUebersetzen();
  beobachten();
}

function sprache() {
  return SPRACHE;
}

/** Vorgabe, solange kein Konto etwas anderes sagt. */
function spracheVorgabe() {
  try {
    const gemerkt = localStorage.getItem("proxfy-sprache");
    if (gemerkt === "de" || gemerkt === "en") return gemerkt;
  } catch (e) { /* egal */ }
  const b = (navigator.language || "de").toLowerCase();
  return b.startsWith("de") ? "de" : "en";
}

/** Vorlage, deren feste Teile uebersetzt werden.
 *
 * Aus  `Der Bereich hat ${n} Adressen.`  wird  T`Der Bereich hat ${n} Adressen.`
 * Die eingesetzten Werte bleiben unangetastet, die Textstuecke davor und
 * dazwischen gehen durch t(). Damit muss nicht jeder Satz einzeln aus dem
 * Markup geloest werden, und ein fehlender Eintrag faellt auf Deutsch zurueck
 * statt die Zeile zu zerlegen.
 */
function T(stuecke, ...werte) {
  let raus = "";
  for (let i = 0; i < stuecke.length; i++) {
    raus += t(stuecke[i]);
    if (i < werte.length) raus += werte[i];
  }
  return raus;
}

// --- Was app.js erzeugt ------------------------------------------------------
// Statt jede Zeichenkette im Quelltext einzeln zu fassen, wird uebersetzt, was
// tatsaechlich in der Seite landet. Das haelt app.js frei von Uebersetzungs-
// aufrufen, und was spaeter dazukommt, ist ohne weiteres Zutun erfasst.
//
// Zwei Vorkehrungen gegen Fehlgriffe: sehr kurze oder mehrdeutige Texte stehen
// in NUR_STATISCH und werden hier nicht angefasst - sonst wuerde aus einem Gast
// namens "nur" plötzlich "only". Und Saetze mit eingesetzten Werten laufen
// ueber MUSTER, weil ein fester Schluessel dort nie passen kann.
const NUR_STATISCH = new Set([
  "an", "in", "nur", "oder", "nicht", "nichts", "http", "Name", "Typ", "Zeit",
  "Gast", "Quelle", "Modus", "Backup", "Adresse", "Gateway", "Rolle", "Notiz",
  "optional", "ausschließlich", "im LAN.", "Minuten", "Minuten.", "Unter",
]);

const MUSTER = [];   // wird von i18n-en.js gefuellt: [regex, ersatz]

function musterUebersetzen(text) {
  for (const [muster, ersatz] of MUSTER) {
    const m = text.match(muster);
    if (m) return text.replace(muster, ersatz);
  }
  return null;
}

function knotenUebersetzen(knoten) {
  const roh = knoten.nodeValue;
  const kern = roh.trim();
  if (kern.length < 2) return;
  if (NUR_STATISCH.has(kern)) return;

  let neu = null;
  if (EN[kern] !== undefined) {
    neu = EN[kern];
  } else {
    const zusammen = kern.replace(/\s+/g, " ");
    if (EN[zusammen] !== undefined) neu = EN[zusammen];
    else neu = musterUebersetzen(zusammen);
  }
  if (neu === null || neu === kern) return;
  knoten.nodeValue = roh.replace(kern, neu);
}

function zweigUebersetzen(wurzel) {
  if (wurzel.nodeType === Node.TEXT_NODE) return knotenUebersetzen(wurzel);
  if (wurzel.nodeType !== Node.ELEMENT_NODE) return;
  const lauf = document.createTreeWalker(wurzel, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const tag = n.parentNode && n.parentNode.nodeName;
      return (tag === "SCRIPT" || tag === "STYLE")
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
    },
  });
  let n;
  while ((n = lauf.nextNode())) knotenUebersetzen(n);
  for (const el of wurzel.querySelectorAll("[placeholder],[title]")) {
    for (const a of ["placeholder", "title"]) {
      const w = el.getAttribute(a);
      if (w && EN[w.trim()] !== undefined) el.setAttribute(a, EN[w.trim()]);
    }
  }
}

let BEOBACHTER = null;

function beobachten() {
  if (BEOBACHTER) BEOBACHTER.disconnect();
  if (SPRACHE === "de") { BEOBACHTER = null; return; }
  // Nur childList: das Setzen von nodeValue loest keine solche Meldung aus,
  // die Uebersetzung kann sich also nicht selbst erneut anstossen.
  BEOBACHTER = new MutationObserver((meldungen) => {
    for (const m of meldungen) {
      for (const knoten of m.addedNodes) zweigUebersetzen(knoten);
    }
  });
  BEOBACHTER.observe(document.body, { childList: true, subtree: true });
}
