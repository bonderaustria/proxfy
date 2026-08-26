"""Umzug der Daten von SQLite nach PostgreSQL.

Betrifft bestehende Installationen. Beide Datenbanken werden zeilenweise
uebernommen: proxfy.db mit Verlauf, Zeitplaenen, Testgaesten und Einstellungen,
auth.db mit Konten, Sitzungen und dem zweiten Faktor.

Drei Vorsaetze:

* **Nichts loeschen.** Die SQLite-Dateien bleiben liegen, wo sie sind. Geht
  etwas schief, ist der Rueckweg das Zurueckstellen einer Zeile in der
  config.yaml.
* **Tabellenweise, nur wenn leer.** Steht drueben schon etwas, wird die Tabelle
  uebersprungen - sonst setzte ein zweiter Aufruf einen inzwischen
  weitergelaufenen Betrieb auf den Stand der alten Datei zurueck. Damit ist der
  Umzug zugleich wiederholbar: ein Abbruch mittendrin laesst sich fortsetzen.
* **Typen umsetzen.** SQLite kennt keine Wahrheitswerte und keine Zeitstempel;
  beides liegt dort als Zahl oder Text. PostgreSQL nimmt das nicht ungefragt an.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3

from . import db

# Reihenfolge zaehlt nicht - es gibt keine Fremdschluessel zwischen den
# Tabellen. Aufgefuehrt sind sie trotzdem einzeln, damit eine neue Tabelle
# auffaellt, statt stillschweigend liegenzubleiben.
TABELLEN_PROXFY = (
    "targets", "jobs", "leases", "settings", "pending_rollback",
    "ip_pool", "login_attempts", "schedules", "user_prefs",
)

# Better Auth legt sein Schema selbst an; hier werden nur die Zeilen umgesetzt.
# 'user' zuerst: die uebrigen verweisen darauf.
TABELLEN_AUTH = ("user", "account", "session", "verification", "twoFactor")


class UmzugFehler(RuntimeError):
    pass


# --- Werte umsetzen ----------------------------------------------------------

_WAHR = {1, "1", "true", "TRUE", "t", True}


def _als_wahrheitswert(wert):
    if wert is None or isinstance(wert, bool):
        return wert
    return wert in _WAHR


def _als_zeitpunkt(wert):
    """ISO-Text oder Unix-Zeit in einen Zeitpunkt. Nichts Passendes: unveraendert."""
    if wert is None or isinstance(wert, dt.datetime):
        return wert
    if isinstance(wert, (int, float)):
        # Better Auth schreibt Millisekunden, andere Stellen Sekunden. Die
        # Grenze liegt weit jenseits jedes plausiblen Datums in Sekunden.
        zahl = float(wert)
        return dt.datetime.fromtimestamp(zahl / 1000 if zahl > 1e11 else zahl,
                                         dt.timezone.utc)
    text = str(wert).strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return wert


def _umsetzen(wert, typ: str):
    if typ == "boolean":
        return _als_wahrheitswert(wert)
    if typ.startswith("timestamp") or typ == "date":
        return _als_zeitpunkt(wert)
    return wert


# --- Quelle ------------------------------------------------------------------

def _sqlite_tabellen(pfad: pathlib.Path) -> set[str]:
    c = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()


def _zeilen(pfad: pathlib.Path, tabelle: str) -> tuple[list[str], list[tuple]]:
    c = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        reihen = c.execute(f'SELECT * FROM "{tabelle}"').fetchall()
        if not reihen:
            return [], []
        spalten = list(reihen[0].keys())
        return spalten, [tuple(r[s] for s in spalten) for r in reihen]
    finally:
        c.close()


# --- Ziel --------------------------------------------------------------------

def _spalten(ziel, tabelle: str) -> dict[str, str]:
    return {r["column_name"]: r["data_type"] for r in ziel.execute(
        "SELECT column_name::text AS column_name, data_type::text AS data_type "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=?", (tabelle,))}


def _belegt(ziel, tabelle: str) -> bool:
    r = ziel.execute(f'SELECT COUNT(*) AS n FROM "{tabelle}"').fetchone()
    return bool(r and int(r["n"]) > 0)


# Verweise aus anderen Tabellen auf eine Nummer, die es dort laengst nicht mehr
# gibt. Der Verlauf haelt die Zeitplan-Nummer eines Laufs fest, auch wenn der
# Zeitplan danach geloescht wurde. Wer den Zaehler nur an den vorhandenen
# Zeilen ausrichtet, vergibt eine solche Nummer erneut - und der neue Zeitplan
# erbt die Laeufe des geloeschten.
_VERWEISE = {
    "schedules": [("jobs", "schedule_id")],
}


def _folgenummern(ziel, tabelle: str, typen: dict[str, str]) -> None:
    """Setzt den Zaehler hinter die uebernommenen Schluessel.

    Ohne das vergaebe PostgreSQL beim naechsten Eintrag die 1 - und stiesse
    sofort auf einen bestehenden Schluessel.
    """
    # Nur bei ganzzahligen Schluesseln mit Zaehler. Der Anmeldedienst vergibt
    # seine Kennungen als Text - dort gibt es nichts weiterzustellen.
    if not typen.get("id", "").startswith(("integer", "bigint", "smallint")):
        return

    teile = [f'COALESCE((SELECT MAX(id) FROM "{tabelle}"), 0)']
    for fremd, spalte in _VERWEISE.get(tabelle, []):
        teile.append(f'COALESCE((SELECT MAX("{spalte}") FROM "{fremd}"), 0)')
    hoechste = teile[0] if len(teile) == 1 else "GREATEST(" + ", ".join(teile) + ")"

    ziel.execute(
        "SELECT setval(pg_get_serial_sequence(?, 'id'), "
        f"{hoechste} + 1, false)", (tabelle,))


def _uebernehmen(ziel, tabelle: str, spalten: list[str], reihen: list[tuple]) -> int:
    typen = _spalten(ziel, tabelle)
    if not typen:
        raise UmzugFehler(f"Tabelle '{tabelle}' fehlt in PostgreSQL.")
    if _belegt(ziel, tabelle):
        return 0
    if not reihen:
        return 0

    # Nur Spalten, die es drueben wirklich gibt. Eine alte Datei kann Spalten
    # tragen, die inzwischen entfallen sind.
    behalten = [i for i, s in enumerate(spalten) if s in typen]
    namen = ", ".join(f'"{spalten[i]}"' for i in behalten)
    platz = ", ".join("?" for _ in behalten)
    umgesetzt = [tuple(_umsetzen(r[i], typen[spalten[i]]) for i in behalten)
                 for r in reihen]

    ziel.cursor().executemany(
        f'INSERT INTO "{tabelle}" ({namen}) VALUES ({platz}) ON CONFLICT DO NOTHING',
        umgesetzt)
    _folgenummern(ziel, tabelle, typen)
    return len(umgesetzt)


# --- Ablauf ------------------------------------------------------------------

def uebernehmen(dsn: str, proxfy_db: str | pathlib.Path,
                auth_db: str | pathlib.Path | None = None) -> dict[str, int]:
    """Holt die Zeilen herueber. Gibt zurueck, was tatsaechlich uebernommen wurde.

    Jede Tabelle einzeln und nur, wenn sie drueben leer ist. Dadurch laesst sich
    der Aufruf gefahrlos wiederholen.
    """
    bilanz: dict[str, int] = {}
    db.sicherstellen(dsn)

    for datei, tabellen, wessen in ((proxfy_db, TABELLEN_PROXFY, "Proxfy"),
                                    (auth_db, TABELLEN_AUTH, "Anmeldedienst")):
        if not datei:
            continue
        pfad = pathlib.Path(datei)
        if not pfad.is_file():
            continue
        vorhanden = _sqlite_tabellen(pfad)
        for tabelle in tabellen:
            if tabelle not in vorhanden:
                continue
            spalten, reihen = _zeilen(pfad, tabelle)
            if not reihen:
                continue
            # Jede Tabelle fuer sich festschreiben: scheitert eine, bleibt das
            # bereits Uebernommene stehen und der naechste Aufruf macht weiter.
            with db.Verbindung(dsn) as ziel:
                try:
                    n = _uebernehmen(ziel, tabelle, spalten, reihen)
                except UmzugFehler:
                    raise UmzugFehler(
                        f"Tabelle '{tabelle}' fehlt in PostgreSQL. Das Schema "
                        f"des {wessen} muss vorher angelegt sein.") from None
            if n:
                bilanz[tabelle] = n
    return bilanz
