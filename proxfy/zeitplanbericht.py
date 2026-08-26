"""Bericht ueber einen Zeitplan: alle Laeufe, alle Pruefungen.

Beantwortet die Frage, die eine Liste einzelner Laeufe nicht beantwortet: hat
dieser Plan getan, was er sollte - und woran ist es gescheitert, wo es
gescheitert ist.

Der Bericht ist zum Weggeben gedacht. Deshalb steht oben, worauf er sich
bezieht (Plan, Zeitraum, Stand), und bei jedem Lauf, aus welchem Backup er kam:
ohne diese Kennung belegt ein Bericht nur ein Datum, nicht einen Datenstand.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

from . import pdf

LOGO = pathlib.Path(__file__).parent / "static" / "logo-print.jpg"
LOGO_BREITE, LOGO_HOEHE = 300, 282

BESTANDEN = ("VERIFIZIERT", "OK", "BESTANDEN")


def _zeit(wert, mit_zeit: bool = True) -> str:
    if not wert:
        return "-"
    s = str(wert).replace("T", " ")
    return s[:16] if mit_zeit else s[:10]


def _urteil(wert) -> str:
    return str(wert or "-").upper()


def _pruefungen(job: dict) -> list[dict]:
    roh = job.get("report")
    if not roh:
        return []
    try:
        rep = json.loads(roh) if isinstance(roh, str) else roh
    except (TypeError, ValueError):
        return []
    return rep.get("checks", []) or []


def _bericht_daten(job: dict) -> dict:
    roh = job.get("report")
    try:
        return json.loads(roh) if isinstance(roh, str) else (roh or {})
    except (TypeError, ValueError):
        return {}


class Blatt:
    """Haelt die aktuelle Seite und den Schreibzeiger.

    Der Zeiger laeuft von oben nach unten; wird der Platz knapp, faengt eine
    neue Seite an. Ohne das endeten lange Berichte still am unteren Rand.
    """

    def __init__(self, dok: pdf.Dokument, titel: str, unterzeile: str):
        self.dok = dok
        self.titel, self.unterzeile = titel, unterzeile
        self.nr = 0
        self.seite = None
        self.y = 0.0
        self.neue_seite()

    @property
    def breite(self) -> float:
        return pdf.A4[0] - 2 * pdf.RAND

    def neue_seite(self) -> None:
        self.nr += 1
        self.seite = self.dok.neue_seite()
        oben = pdf.A4[1] - pdf.RAND

        if self.nr == 1:
            # Logo links oben, wie gewuenscht.
            h = 44.0
            b = h * LOGO_BREITE / LOGO_HOEHE
            self.seite.bild("Logo", pdf.RAND, oben - h, b, h)
            self.seite.text(pdf.RAND + b + 14, oben - 17, "Proxfy", 17, fett=True)
            self.seite.text(pdf.RAND + b + 14, oben - 31,
                            "Restore-Verifikation für Proxmox Backup Server", 8.5,
                            grau=0.45)
            self.y = oben - h - 26
            self.seite.text(pdf.RAND, self.y, self.titel, 14, fett=True)
            self.y -= 15
            self.seite.text(pdf.RAND, self.y, self.unterzeile, 9, grau=0.4)
            self.y -= 16
            self.seite.linie(pdf.RAND, self.y, pdf.A4[0] - pdf.RAND, self.y)
            self.y -= 22
        else:
            self.seite.text(pdf.RAND, oben - 9, self.titel, 9, grau=0.45)
            self.seite.linie(pdf.RAND, oben - 16, pdf.A4[0] - pdf.RAND, oben - 16)
            self.y = oben - 34

        self.seite.text(pdf.A4[0] - pdf.RAND - 40, pdf.RAND - 14,
                        f"Seite {self.nr}", 8, grau=0.55)

    def platz(self, hoehe: float) -> None:
        if self.y - hoehe < pdf.RAND + 12:
            self.neue_seite()

    def absatz(self, text: str, groesse: float = 9.5, fett: bool = False,
               grau: float = 0.0, einzug: float = 0.0) -> None:
        for zeile in pdf.Dokument.umbrechen(text, self.breite - einzug, groesse):
            self.platz(groesse + 3)
            self.seite.text(pdf.RAND + einzug, self.y, zeile, groesse, fett, grau)
            self.y -= groesse + 3

    def luft(self, n: float = 8) -> None:
        self.y -= n


def erzeugen(plan: dict, jobs: list[dict], erzeugt_von: str = "") -> bytes:
    """Baut das PDF. 'jobs' sind die Laeufe dieses Plans, neueste zuerst."""
    jetzt = dt.datetime.now().astimezone()
    dok = pdf.Dokument(titel=f"Proxfy - Zeitplan {plan.get('name', '')}")
    if LOGO.is_file():
        dok.bild_aufnehmen("Logo", LOGO.read_bytes(), LOGO_BREITE, LOGO_HOEHE)

    zeitraum = "-"
    if jobs:
        zeitraum = f"{_zeit(jobs[-1].get('started'))} bis {_zeit(jobs[0].get('started'))}"

    blatt = Blatt(dok, f"Zeitplan: {plan.get('name', '(ohne Namen)')}",
                  f"Stand {jetzt.strftime('%d.%m.%Y %H:%M')}"
                  + (f" - erstellt von {erzeugt_von}" if erzeugt_von else ""))

    # --- Was der Plan tut ----------------------------------------------------
    WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    tage = ", ".join(WD[i] for i in (plan.get("weekdays") or []) if 0 <= i < 7) or "-"
    zeilen = [
        ("Ausführung", f"{plan.get('at_time', '-')} Uhr, {tage}"),
        ("Zustand", "aktiv" if plan.get("enabled") else "abgeschaltet"),
        ("Gäste", ", ".join(str(v) for v in (plan.get("vmids") or [])) or "-"),
        ("Netzwerkmodus", plan.get("mode") or "-"),
        ("Lebensdauer", plan.get("keep") or "-"),
        ("Zeitraum im Bericht", zeitraum),
        ("Läufe im Bericht", str(len(jobs))),
    ]
    for name, wert in zeilen:
        blatt.platz(13)
        blatt.seite.text(pdf.RAND, blatt.y, name, 9, grau=0.45)
        blatt.seite.text(pdf.RAND + 132, blatt.y, str(wert), 9)
        blatt.y -= 13

    # --- Bilanz --------------------------------------------------------------
    bestanden = sum(1 for j in jobs if _urteil(j.get("verdict")) in BESTANDEN)
    blatt.luft(10)
    blatt.platz(28)
    blatt.seite.kasten(pdf.RAND, blatt.y - 8, blatt.breite, 26, 0.95)
    blatt.seite.text(pdf.RAND + 10, blatt.y + 2,
                     f"{bestanden} von {len(jobs)} Läufen verifiziert", 11, fett=True)
    if jobs:
        blatt.seite.text_rechts(pdf.A4[0] - pdf.RAND - 10, blatt.y + 2,
                                f"{round(100 * bestanden / len(jobs))} Prozent", 11,
                                grau=0.35)
    blatt.y -= 34

    if not jobs:
        blatt.absatz("Dieser Zeitplan hat noch keinen Lauf ausgelöst.", 9.5, grau=0.4)

    # --- Jeder Lauf einzeln ---------------------------------------------------
    for job in jobs:
        rep = _bericht_daten(job)
        pruef = _pruefungen(job)
        blatt.luft(6)
        blatt.platz(40)
        blatt.seite.linie(pdf.RAND, blatt.y + 12, pdf.A4[0] - pdf.RAND, blatt.y + 12,
                          0.4, 0.85)

        kopf = (f"{_zeit(job.get('started'))}   Gast {job.get('vmid')}"
                f" ({rep.get('source_name') or job.get('kind') or '-'})")
        blatt.seite.text(pdf.RAND, blatt.y, kopf, 10.5, fett=True)
        urteil = _urteil(job.get("verdict"))
        blatt.seite.text_rechts(pdf.A4[0] - pdf.RAND, blatt.y, urteil, 10.5,
                                fett=True)
        blatt.y -= 14

        dauer = job.get("duration")
        angaben = [
            f"Backup-Stand: {rep.get('snapshot') or job.get('snapshot') or '-'}",
            f"Modus: {rep.get('mode') or '-'}   "
            f"Dauer: {round(float(dauer), 1) if dauer else '-'} s   "
            f"Knoten: {rep.get('node') or '-'}",
        ]
        for a in angaben:
            blatt.absatz(a, 8.5, grau=0.45, einzug=0)

        if rep.get("error"):
            blatt.absatz(f"Abbruchgrund: {rep['error']}", 9, grau=0.15)

        if not pruef:
            blatt.absatz("Keine Prüfungen aufgezeichnet.", 8.5, grau=0.5, einzug=12)
            continue

        blatt.luft(3)
        for p in pruef:
            zeichen = ("übersprungen" if p.get("skipped")
                       else "bestanden" if p.get("passed") else "GESCHEITERT")
            name = p.get("name") or p.get("kind") or "?"
            pflicht = "" if p.get("required", True) else "  (keine Pflicht)"
            blatt.platz(12)
            blatt.seite.text(pdf.RAND + 12, blatt.y, name, 9,
                             fett=not p.get("passed") and not p.get("skipped"))
            blatt.seite.text_rechts(pdf.A4[0] - pdf.RAND, blatt.y,
                                    zeichen + pflicht, 8.5,
                                    grau=0.0 if zeichen == "GESCHEITERT" else 0.45)
            blatt.y -= 12
            # Die Begruendung ist der eigentliche Wert des Berichts - ohne sie
            # steht dort nur, DASS etwas scheiterte.
            if p.get("detail"):
                blatt.absatz(str(p["detail"])[:400], 8, grau=0.4, einzug=24)

    blatt.luft(14)
    blatt.absatz(
        "Erzeugt von Proxfy. Jeder Lauf hat ein Backup unter einer Wegwerf-Nummer "
        "wiederhergestellt, gestartet und geprüft; die ursprünglichen Gäste und die "
        "Backups blieben dabei unberührt.", 8, grau=0.5)

    return dok.bytes()
