"""Englische Fassung der Meldungen, die den Server verlassen.

Wie im Browser ist Deutsch die Quelle: die Meldungen stehen im Code so, wie sie
gedacht sind, und hier liegt daneben, wie sie auf Englisch heissen. Uebersetzt
wird an der Ausgabegrenze - in web.py, kurz bevor eine Antwort das Haus
verlaesst. Damit muessen die rund zweihundert Stellen, an denen eine Meldung
entsteht, nicht einzeln angefasst werden, und was spaeter dazukommt, ist ohne
weiteres Zutun erfasst.

Fehlt ein Eintrag, bleibt die Meldung deutsch. Das ist gewollt: sichtbar und in
einer Zeile behebbar, statt einer leeren Stelle oder eines Schluessels.
"""
from __future__ import annotations

import re

# Meldungen ohne eingesetzte Werte.
EN: dict[str, str] = {
    # --- Adressen ---------------------------------------------------------
    "Keine Adresse angegeben.": "No address given.",
    "Das Ende des Bereichs liegt vor dem Anfang.": "The end of the range lies before its start.",

    # --- Aussenadresse ----------------------------------------------------
    "Keine Aussenadresse eingetragen.": "No external address configured.",

    # --- Rechte und Anmeldung ---------------------------------------------
    "nicht angemeldet": "not signed in",
    "Es besteht bereits ein Konto": "An account already exists",
    "Das Passwort stimmt nicht. Sicherheitsrelevante Einstellungen verlangen es erneut.":
        "The password is not correct. Security-relevant settings ask for it again.",
    "Der letzte Super Admin kann nicht geloescht werden.":
        "The last super admin cannot be deleted.",
    "Der letzte Super Admin kann nicht herabgestuft werden.":
        "The last super admin cannot be demoted.",
    "Rollen oberhalb 'user' vergibt ausschliesslich der Super Admin.":
        "Only a super admin hands out roles above 'user'.",

    # --- Einstellungen -----------------------------------------------------
    "Keine gueltigen Felder uebergeben": "No valid fields were provided",
    "Verbindung steht": "Connection is up",

    # --- Laeufe -------------------------------------------------------------
    "Kein Backup gefunden.": "No backup found.",
    "Der Gast antwortet nicht.": "The guest does not respond.",
    "Es laeuft bereits ein Lauf fuer diesen Gast.": "A run for this guest is already in progress.",
    "manuell": "by hand",
    "Zeitplan": "schedule",

    # --- Ergebnis einer Pruefung -------------------------------------------
    "bestanden": "passed",
    "durchgefallen": "failed",
    "uebersprungen": "skipped",
    "verifiziert": "verified",
    "nie geprueft": "never verified",
    "Uhrzeit im Gast nicht lesbar": "Clock inside the guest not readable",
}

# Meldungen mit eingesetzten Werten. Reihenfolge zaehlt: das erste passende
# Muster gewinnt.
MUSTER: list[tuple[re.Pattern, str]] = [
    # --- Adressen -----------------------------------------------------------
    (re.compile(r"^'(.+)' ist keine gueltige IPv4-Adresse\.$"),
     r"'\1' is not a valid IPv4 address."),
    (re.compile(r"^'(.+)' ist kein gueltiges letztes Oktett\.$"),
     r"'\1' is not a valid last octet."),
    (re.compile(r"^Praefix /(\d+) ist unbrauchbar\.$"),
     r"Prefix /\1 is not usable."),
    (re.compile(r"^Der Bereich umfasst (\d+) Adressen\. Mehr als (\d+) sind fast immer ein Tippfehler\.$"),
     r"The range holds \1 addresses. More than \2 is almost always a typo."),
    (re.compile(r"^Alle (\d+) Adressen aus (.+) sind an laufende Testgaeste vergeben\.$"),
     r"All \1 addresses from \2 are taken by running test guests."),
    (re.compile(r"^Keine freie Adresse in (.+) gefunden\. Zuletzt geprueft:$"),
     r"No free address found in \1. Last checked:"),
    (re.compile(r"^'(.+)' hat kein Schema\. Erwartet wird https://name\.example\.org$"),
     r"'\1' has no scheme. Expected something like https://name.example.org"),
    (re.compile(r"^'(.+)' ist keine brauchbare Adresse\. Erwartet wird Schema, Name und wahlweise Port, ohne Pfad - etwa https://verify\.example\.org$"),
     r"'\1' is not a usable address. Expected scheme, name and optionally a port, without a path - for example https://verify.example.org"),

    # --- Einstellungen und Rechte -------------------------------------------
    (re.compile(r"^Unbekannte Gruppe '(.+)'$"), r"Unknown group '\1'"),
    (re.compile(r"^Unbekannte Sprache '(.+)'$"), r"Unknown language '\1'"),
    (re.compile(r"^Keine Verbindung: (.+)$"), r"No connection: \1"),
    (re.compile(r"^VMID (\d+) liegt ausserhalb des Scratch-Bereichs (\d+)-(\d+)\. Operation abgebrochen\.$"),
     r"VMID \1 lies outside the scratch range \2-\3. Operation aborted."),
    (re.compile(r"^(\S+) antwortet auf Ping - die Adresse ist belegt\.$"),
     r"\1 answers a ping - the address is taken."),

    # --- Ergebnisse der Pruefungen ------------------------------------------
    (re.compile(r"^(\S+) ist active$"), r"\1 is active"),
    (re.compile(r"^(\S+) ist '(.*)'$"), r"\1 is '\2'"),
    (re.compile(r"^Port (\d+) lauscht \(innen\)$"), r"Port \1 is listening (inside)"),
    (re.compile(r"^Port (\d+) lauscht nicht \(innen\)$"), r"Port \1 is not listening (inside)"),
    (re.compile(r"^(\S+) ist erreichbar$"), r"\1 is reachable"),
    (re.compile(r"^(\S+) ist nicht erreichbar$"), r"\1 is not reachable"),
    (re.compile(r"^(.+) lieferte HTTP (.+), erwartet (.+)$"),
     r"\1 returned HTTP \2, expected \3"),
    (re.compile(r"^, Muster '(.+)' gefunden$"), r", pattern '\1' found"),
    (re.compile(r"^Kein TLS-Handschlag mit (.+) moeglich$"),
     r"No TLS handshake with \1 possible"),
    (re.compile(r"^TLS-Handschlag mit (.+) erfolgreich, Ablaufdatum nicht lesbar$"),
     r"TLS handshake with \1 succeeded, expiry date not readable"),
    (re.compile(r"^Zertifikat laeuft in (\d+) Tagen ab \((.+)\), verlangt sind (\d+)$"),
     r"Certificate expires in \1 days (\2), \3 are required"),
    (re.compile(r"^(\S+) nicht vorhanden$"), r"\1 does not exist"),
    (re.compile(r"^Unter (.+) wurde keine Datei gefunden$"), r"No file found under \1"),
    (re.compile(r"^Groesse von (.+) nicht lesbar: (.*)$"), r"Size of \1 not readable: \2"),
    (re.compile(r"^Juengste Datei ist ([\d.]+) h alt, erlaubt sind (\d+) h ?(.*)$"),
     r"Newest file is \1 h old, \2 h are allowed \3"),
    (re.compile(r"^Juengste Datei ([\d.]+) h alt: (.*)$"), r"Newest file \1 h old: \2"),
    (re.compile(r"^Juengster Datensatz ist ([\d.]+) h alt, erlaubt sind (\d+) h$"),
     r"Newest record is \1 h old, \2 h are allowed"),
    (re.compile(r"^Anzahl nicht lesbar: (.*)$"), r"Count not readable: \1"),
    (re.compile(r"^Zeitstempel nicht lesbar: (.*)$"), r"Timestamp not readable: \1"),
    (re.compile(r"^Kein Kommando ausfuehrbar: (.*)$"), r"No command could be run: \1"),
    (re.compile(r"^ss nicht ausfuehrbar: (.*)$"), r"ss could not be run: \1"),
    (re.compile(r"^Pflichtfeld (.+) fehlt in der Pruefungsdefinition$"),
     r"Required field \1 missing in the check definition"),

    # Muss hinter den genaueren stehen - sonst schluckt es sie.
    (re.compile(r"^(.+) nicht erreichbar: (.+)$"), r"\1 not reachable: \2"),
]


def uebersetze(text: str, sprache: str) -> str:
    """Ein einzelner Text. Unbekanntes bleibt, wie es ist."""
    if sprache != "en" or not isinstance(text, str):
        return text
    kern = text.strip()
    if not kern:
        return text
    if kern in EN:
        return text.replace(kern, EN[kern])
    for muster, ersatz in MUSTER:
        if muster.match(kern):
            return text.replace(kern, muster.sub(ersatz, kern))
    return text


# Felder, deren Inhalt Daten sind und keine Meldung: Namen von Gaesten,
# Storages und Knoten. Sie werden nicht angefasst - sonst hiesse ein Gast
# namens "Zeitplan" auf einmal "schedule".
_DATENFELDER = frozenset({
    "name", "hostname", "vmid", "scratch_vmid", "source_vmid", "node",
    "storage", "backup_storage", "target_storage", "ip", "gateway", "email",
    "user_id", "id", "label", "snapshot", "key_file", "host", "public_url",
})


def durchgehen(wert, sprache: str):
    """Geht eine Antwort durch und uebersetzt, was eine Meldung ist."""
    if sprache != "en":
        return wert
    if isinstance(wert, str):
        return uebersetze(wert, sprache)
    if isinstance(wert, list):
        return [durchgehen(x, sprache) for x in wert]
    if isinstance(wert, dict):
        return {k: (v if k in _DATENFELDER else durchgehen(v, sprache))
                for k, v in wert.items()}
    return wert
