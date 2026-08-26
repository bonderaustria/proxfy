"""Zugang zur Datenbank.

Proxfy spricht PostgreSQL. Der Zugriff laeuft ueber eine schmale Zwischen-
schicht, damit die Abfragen in store.py so bleiben koennen, wie sie sind:

* Platzhalter stehen dort als ``?``. psycopg erwartet ``%s``. Das wird hier
  uebersetzt, statt an 55 Stellen im Quelltext.
* Zeilen kommen als Abbildung zurueck, sind also weiterhin ueber den
  Spaltennamen ansprechbar (``r["name"]``).
* ``with verbindung() as c`` schliesst wie gewohnt ab: bei einem Fehler wird
  zurueckgerollt, sonst festgeschrieben.

Warum ueberhaupt eine eigene Schicht: der direkte Weg waere gewesen, jede
Abfrage anzufassen. Das sind 55 Gelegenheiten, einen Tippfehler einzubauen, den
erst der Betrieb findet.
"""
from __future__ import annotations

import re
import threading

import psycopg
from psycopg.rows import dict_row

# Ein ``?`` innerhalb einer Zeichenkette in der Abfrage darf nicht ersetzt
# werden. Deshalb wird nur ausserhalb von Hochkommata getauscht.
_AUSSERHALB = re.compile(r"'[^']*'|\?")


def _platzhalter(sql: str) -> str:
    return _AUSSERHALB.sub(lambda m: "%s" if m.group(0) == "?" else m.group(0), sql)


class Cursor:
    """Duennes Futteral um den Cursor von psycopg."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, parameter=()):
        self._cur.execute(_platzhalter(sql), tuple(parameter))
        return self

    def executemany(self, sql: str, reihen):
        self._cur.executemany(_platzhalter(sql), [tuple(r) for r in reihen])
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class Verbindung:
    """Verbindung mit demselben Verhalten, das store.py bisher kannte."""

    def __init__(self, dsn: str):
        self._conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False,
                                     client_encoding="UTF8")

    def cursor(self) -> Cursor:
        return Cursor(self._conn.cursor())

    def execute(self, sql: str, parameter=()) -> Cursor:
        return self.cursor().execute(sql, parameter)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Verbindung":
        return self

    def __exit__(self, art, wert, spur) -> bool:
        try:
            if art is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False


# --- Anlegen -----------------------------------------------------------------

_ANGELEGT: set[str] = set()
_SPERRE = threading.Lock()


def sicherstellen(dsn: str) -> None:
    """Legt die Datenbank an, falls sie fehlt. Einmal je Prozess und DSN."""
    with _SPERRE:
        if dsn in _ANGELEGT:
            return
        try:
            psycopg.connect(dsn, connect_timeout=10).close()
        except psycopg.OperationalError as e:
            if "does not exist" not in str(e):
                raise
            # An die Verwaltungsdatenbank andocken und anlegen.
            teile = psycopg.conninfo.conninfo_to_dict(dsn)
            name = teile.pop("dbname", "proxfy")
            verwaltung = psycopg.conninfo.make_conninfo(dbname="postgres", **teile)
            with psycopg.connect(verwaltung, autocommit=True) as c:
                c.execute(f'CREATE DATABASE "{name}"')
        _ANGELEGT.add(dsn)
