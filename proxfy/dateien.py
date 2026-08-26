"""Einzelne Dateien aus einem Backup holen, ohne es wiederherzustellen.

Grundlage ist ``proxmox-file-restore`` auf dem Hypervisor. Bei einem Container
liest es unmittelbar aus dem Dateiarchiv; bei einer VM schliesst Proxmox das
Blockabbild mit einer winzigen Hilfs-VM auf, die es selbst startet und nach
zehn Minuten wieder abraeumt. Beides dauert Sekunden - eine vollstaendige
Wiederherstellung braucht es dafuer nicht.

Grenzen, die nicht an Proxfy liegen: fuer vzdump-Verzeichnisbackups einer VM
gibt es kein solches Werkzeug. Dort bleibt nur der Umweg ueber einen echten
Testlauf, aus dem sich die Datei dann herausholen laesst.

**Zum Zugriff:** Was hier herauskommt, sind Inhalte - Passwortdateien,
Datenbanken, Schluessel. Das ist etwas anderes als ein Pruefergebnis. Die
Rechtepruefung und die Passwortbestaetigung stehen deshalb in web.py an jedem
Endpunkt, und jeder Abruf wird verzeichnet.
"""
from __future__ import annotations

import base64
import json
import re
import shlex

from .ssh import Host

# Was proxmox-file-restore als Typ meldet.
VERZEICHNIS = ("d", "v")     # 'v' ist ein Einstiegspunkt: Abbild, LVM, Partition


class DateiFehler(RuntimeError):
    pass


# --- Zugang zum Backup-Speicher ----------------------------------------------

def _storage_block(host: Host, storage: str) -> dict[str, str]:
    """Liest den Abschnitt eines Storage aus /etc/pve/storage.cfg."""
    r = host.run("cat", "/etc/pve/storage.cfg", timeout=30)
    if not r.ok:
        raise DateiFehler("storage.cfg nicht lesbar.")
    werte: dict[str, str] = {}
    drin = False
    for zeile in r.out.splitlines():
        kopf = re.match(r"^(\w+):\s*(\S+)\s*$", zeile)
        if kopf:
            drin = kopf.group(2) == storage
            if drin:
                werte["__typ"] = kopf.group(1)
            continue
        if drin and zeile.startswith((" ", "\t")):
            teile = zeile.strip().split(None, 1)
            if teile:
                werte[teile[0]] = teile[1] if len(teile) > 1 else ""
    if not werte:
        raise DateiFehler(f"Storage '{storage}' steht nicht in der storage.cfg.")
    return werte


def umgebung(host: Host, storage: str) -> dict[str, str]:
    """Baut die Umgebung, die proxmox-file-restore fuer diesen Speicher braucht.

    Das Passwort liegt auf dem Hypervisor unter /etc/pve/priv - es wird dort
    gelesen und nur an das Kommando weitergereicht, nie gespeichert und nie an
    die Oberflaeche gegeben.
    """
    block = _storage_block(host, storage)
    if block.get("__typ") != "pbs":
        raise DateiFehler(
            f"'{storage}' ist kein Proxmox Backup Server. Einzelne Dateien "
            "lassen sich nur von dort ohne Wiederherstellung holen.")

    server = block.get("server", "")
    datastore = block.get("datastore", "")
    user = block.get("username", "root@pam")
    if not server or not datastore:
        raise DateiFehler(f"Storage '{storage}' ist unvollstaendig konfiguriert.")

    r = host.sh(f"cat /etc/pve/priv/storage/{shlex.quote(storage)}.pw 2>/dev/null", timeout=30)
    passwort = (r.out or "").strip()
    if not passwort:
        raise DateiFehler(
            f"Kein hinterlegtes Passwort fuer '{storage}'. Ohne das kommt "
            "proxmox-file-restore nicht an den Speicher.")

    env = {"PBS_REPOSITORY": f"{user}@{server}:{datastore}", "PBS_PASSWORD": passwort}
    if block.get("fingerprint"):
        env["PBS_FINGERPRINT"] = block["fingerprint"]
    return env


def snapshot_pfad(volid: str) -> str:
    """Aus 'PBS:backup/ct/110/2026-08-22T14:45:16Z' wird 'ct/110/2026-...'."""
    if ":backup/" in volid:
        return volid.split(":backup/", 1)[1]
    if ":" in volid:
        return volid.split(":", 1)[1]
    return volid


# --- Auflisten ----------------------------------------------------------------

def auflisten(host: Host, storage: str, volid: str, pfad: str = "/") -> list[dict]:
    """Was liegt unter diesem Pfad im Backup?

    'pfad' ist entweder '/' oder eine base64-kodierte Angabe, wie sie aus einer
    vorherigen Auflistung stammt. Das ist die Form, die das Werkzeug selbst
    liefert - und sie erspart die Frage, wie Sonderzeichen in Dateinamen durch
    zwei Schichten kommen.
    """
    env = umgebung(host, storage)
    argv = ["proxmox-file-restore", "list", snapshot_pfad(volid), pfad,
            "--output-format", "json"]
    if pfad != "/":
        argv.append("--base64")
        argv.append("1")

    proc = host.stream(argv, env=env, timeout=300)
    roh, fehler = proc.communicate(timeout=300)
    if proc.returncode != 0:
        raise DateiFehler((fehler or b"").decode("utf-8", "replace").strip()
                          or "Auflistung fehlgeschlagen.")
    try:
        eintraege = json.loads(roh.decode("utf-8", "replace") or "[]")
    except ValueError:
        raise DateiFehler("Die Auflistung war nicht lesbar.") from None

    raus = []
    for e in eintraege:
        raus.append({
            "name": e.get("text"),
            "pfad": e.get("filepath"),          # base64, so wie es zurueckgeht
            "verzeichnis": e.get("type") in VERZEICHNIS,
            "typ": e.get("type"),
            "groesse": e.get("size"),
            "geaendert": e.get("mtime"),
        })
    # Verzeichnisse zuerst, dann nach Namen - wie in jedem Dateibrowser.
    raus.sort(key=lambda x: (not x["verzeichnis"], (x["name"] or "").lower()))
    return raus


def lesbar(pfad_b64: str) -> str:
    """Der base64-Pfad als Text - fuer Anzeige und Protokoll."""
    try:
        return base64.b64decode(pfad_b64).decode("utf-8", "replace")
    except Exception:
        return pfad_b64


# --- Herausziehen -------------------------------------------------------------

def herausziehen(host: Host, storage: str, volid: str, pfad_b64: str):
    """Startet den Abruf und gibt den laufenden Prozess zurueck.

    Die Ausgabe geht nach stdout und wird vom Aufrufer haeppchenweise
    weitergereicht - eine Datei aus einem Backup kann Gigabytes gross sein und
    gehoert nicht in den Arbeitsspeicher. Ein Verzeichnis kommt als Archiv.
    """
    env = umgebung(host, storage)
    argv = ["proxmox-file-restore", "extract", snapshot_pfad(volid), pfad_b64,
            "-", "--base64", "1"]
    return host.stream(argv, env=env, timeout=7200)


def hilfs_vms(host: Host) -> str:
    """Welche Hilfs-VMs laufen gerade? Nur zur Anzeige."""
    r = host.run("proxmox-file-restore", "status", timeout=30)
    return (r.out or "").strip()
