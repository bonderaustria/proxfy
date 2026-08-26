"""Auswertungen ueber den Bestand und die Laeufe.

Die Frage, die eine Liste einzelner Laeufe nicht beantwortet: **welche Gaeste
sind eigentlich nie geprueft worden?** Ein Backup, das nie wiederhergestellt
wurde, ist genau das Risiko, gegen das Proxfy antritt - und es faellt in einer
Zeitliste nicht auf, weil es dort schlicht fehlt.

Hier wird deshalb von der anderen Seite gerechnet: nicht von den Laeufen zu den
Gaesten, sondern vom Bestand zu den Luecken.

Alles rein rechnerisch, ohne Zugriff auf Proxmox - die Gastliste kommt von
aussen herein. Damit laesst sich die Auswertung auch fuer einen Zeitraum
wiederholen, ohne den Hypervisor zu befragen.
"""
from __future__ import annotations

import collections
import datetime as dt
import json


def _zeitpunkt(wert) -> dt.datetime | None:
    """Zeitangaben liegen als ISO-Text vor, gelegentlich ohne Zeitzone."""
    if not wert:
        return None
    if isinstance(wert, dt.datetime):
        return wert if wert.tzinfo else wert.astimezone()
    try:
        z = dt.datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
    except ValueError:
        return None
    return z if z.tzinfo else z.astimezone()


def _tage_her(wert, jetzt: dt.datetime) -> float | None:
    z = _zeitpunkt(wert)
    return None if z is None else (jetzt - z).total_seconds() / 86400


def abdeckung(inventar: list[dict], frist_tage: int = 30,
              jetzt: dt.datetime | None = None) -> dict:
    """Wie vollstaendig und wie frisch ist der Bestand geprueft?

    Gezaehlt wird nur, was ueberhaupt ein Backup hat - ein Gast ohne Backup
    laesst sich nicht wiederherstellen, und ihn als ungeprueft zu fuehren waere
    ein Vorwurf an der falschen Adresse.
    """
    jetzt = jetzt or dt.datetime.now().astimezone()

    mit_backup = [g for g in inventar if g.get("has_backup")]
    ohne_backup = [g for g in inventar if not g.get("has_backup")]

    nie, veraltet, frisch, gescheitert = [], [], [], []
    for g in mit_backup:
        alter = _tage_her(g.get("last_run"), jetzt)
        eintrag = {
            "vmid": g.get("vmid"), "name": g.get("name"), "kind": g.get("kind"),
            "verdict": g.get("last_verdict"), "last_run": g.get("last_run"),
            "tage": None if alter is None else round(alter, 1),
            "backup_tage": (lambda a: None if a is None else round(a, 1))(
                _tage_her(g.get("latest_ts"), jetzt)),
        }
        if alter is None:
            nie.append(eintrag)
            continue
        # Ein durchgefallener Lauf zaehlt nicht als Abdeckung: geprueft wurde,
        # aber das Ergebnis ist gerade der Grund zur Sorge.
        if str(g.get("last_verdict") or "").upper() not in ("VERIFIZIERT", "OK", "BESTANDEN"):
            gescheitert.append(eintrag)
        elif alter > frist_tage:
            veraltet.append(eintrag)
        else:
            frisch.append(eintrag)

    nach_alter = sorted(nie + gescheitert + veraltet,
                        key=lambda e: (e["tage"] is not None, e["tage"] or 0),
                        reverse=True)
    return {
        "frist_tage": frist_tage,
        "stand": jetzt.isoformat(timespec="seconds"),
        "gaeste_gesamt": len(inventar),
        "ohne_backup": len(ohne_backup),
        "mit_backup": len(mit_backup),
        "frisch": len(frisch),
        "veraltet": len(veraltet),
        "gescheitert": len(gescheitert),
        "nie": len(nie),
        # Der eigentliche Punkt der Auswertung: was Aufmerksamkeit braucht,
        # in der Reihenfolge, in der man es angehen sollte.
        "handlungsbedarf": nach_alter[:50],
        "quote": (round(100 * len(frisch) / len(mit_backup)) if mit_backup else 0),
    }


def ursachen(jobs: list[dict], grenze: int = 10) -> list[dict]:
    """Welche Pruefung scheitert am haeufigsten - und woran?

    Nicht die Zahl der gescheiterten Laeufe, sondern die der gescheiterten
    Pruefungen. Ein Lauf kann an einer einzigen Kleinigkeit scheitern, und
    dieselbe Kleinigkeit trifft dann oft viele Gaeste.
    """
    zaehler: collections.Counter = collections.Counter()
    beispiele: dict[str, dict] = {}
    for j in jobs:
        roh = j.get("report")
        if not roh:
            continue
        try:
            rep = json.loads(roh) if isinstance(roh, str) else roh
        except (TypeError, ValueError):
            continue
        for p in rep.get("checks", []):
            if p.get("passed") or p.get("skipped"):
                continue
            schluessel = f"{p.get('kind', '?')}: {p.get('name', '?')}"
            zaehler[schluessel] += 1
            beispiele.setdefault(schluessel, {
                "detail": p.get("detail"),
                "vmid": rep.get("source_vmid"),
                "name": rep.get("source_name"),
            })
    return [{"pruefung": k, "anzahl": n, **beispiele.get(k, {})}
            for k, n in zaehler.most_common(grenze)]


def verlauf(jobs: list[dict], tage: int = 30,
            jetzt: dt.datetime | None = None) -> list[dict]:
    """Laeufe und Bestehensquote je Tag, luecken los.

    Auch Tage ohne Lauf stehen drin - gerade die Luecken sind die Aussage.
    """
    jetzt = jetzt or dt.datetime.now().astimezone()
    von = (jetzt - dt.timedelta(days=tage - 1)).date()

    je_tag: dict[dt.date, dict] = {}
    for j in jobs:
        z = _zeitpunkt(j.get("started"))
        if z is None or z.date() < von:
            continue
        e = je_tag.setdefault(z.date(), {"laeufe": 0, "bestanden": 0})
        e["laeufe"] += 1
        if str(j.get("verdict") or "").upper() in ("VERIFIZIERT", "OK", "BESTANDEN"):
            e["bestanden"] += 1

    raus = []
    for i in range(tage):
        tag = von + dt.timedelta(days=i)
        e = je_tag.get(tag, {"laeufe": 0, "bestanden": 0})
        raus.append({"tag": tag.isoformat(), **e})
    return raus


def dauern(jobs: list[dict]) -> dict:
    """Wie lange dauert ein Lauf - getrennt nach VM und Container.

    Der Median, nicht der Mittelwert: ein einzelner Ausreisser durch ein
    haengendes Backup verschoebe den Mittelwert bis zur Unbrauchbarkeit.
    """
    nach_art: dict[str, list[float]] = collections.defaultdict(list)
    for j in jobs:
        d = j.get("duration")
        if d:
            nach_art[j.get("kind") or "?"].append(float(d))

    raus = {}
    for art, werte in nach_art.items():
        werte.sort()
        mitte = len(werte) // 2
        median = (werte[mitte] if len(werte) % 2
                  else (werte[mitte - 1] + werte[mitte]) / 2)
        raus[art] = {"anzahl": len(werte), "median": round(median, 1),
                     "kuerzeste": round(werte[0], 1), "laengste": round(werte[-1], 1)}
    return raus


def ueberblick(inventar: list[dict], jobs: list[dict], frist_tage: int = 30,
               tage: int = 30) -> dict:
    """Alles zusammen - das, was die Berichtsansicht braucht."""
    jetzt = dt.datetime.now().astimezone()
    return {
        "abdeckung": abdeckung(inventar, frist_tage, jetzt),
        "ursachen": ursachen(jobs),
        "verlauf": verlauf(jobs, tage, jetzt),
        "dauern": dauern(jobs),
        "laeufe_gesamt": len(jobs),
    }
