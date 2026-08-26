"""Proxmox-Operationen: Backups auflisten, wiederherstellen, pruefen, vernichten."""
from __future__ import annotations

import dataclasses
import json
import re
import shlex
import time

from .config import (SCRATCH_DESC_PREFIX, SCRATCH_TAG, SafetyError,
                     assert_scratch_vmid, is_scratch_marked)
from .ssh import Host, Result


@dataclasses.dataclass
class Snapshot:
    volid: str
    vmid: int
    kind: str        # "vm" | "ct"
    ts: str
    size: int
    pbs: bool = True   # False bei vzdump-Dateien in einem Verzeichnis-Storage

    @property
    def label(self) -> str:
        return f"{self.kind}/{self.vmid} @ {self.ts}"


# PBS: <store>:backup/vm/112/2026-08-25T14:45:07Z
_PBS_RE = re.compile(r"^(?P<store>[^:]+):backup/(?P<kind>vm|ct)/(?P<vmid>\d+)/(?P<ts>\S+)$")

# Verzeichnis: <store>:backup/vzdump-qemu-112-2026_08_25-14_45_07.vma.zst
_DIR_RE = re.compile(
    r"^(?P<store>[^:]+):backup/vzdump-(?P<what>qemu|lxc|openvz)-(?P<vmid>\d+)-"
    r"(?P<ts>\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2})\.\S+$")


def _parse_volid(volid: str):
    """Erkennt beide Backup-Layouts und vereinheitlicht sie."""
    m = _PBS_RE.match(volid)
    if m:
        return m["kind"], int(m["vmid"]), m["ts"], True
    m = _DIR_RE.match(volid)
    if m:
        kind = "vm" if m["what"] == "qemu" else "ct"
        # 2026_08_25-14_45_07 -> 2026-08-25T14:45:07, damit die Sortierung stimmt.
        d, t = m["ts"].split("-")
        ts = d.replace("_", "-") + "T" + t.replace("_", ":")
        return kind, int(m["vmid"]), ts, False
    return None


def list_snapshots(host: Host, storage: str) -> list[Snapshot]:
    """Alle Backups eines Storage, neueste zuerst."""
    r = host.run("pvesm", "list", storage).check(f"Backups aus {storage} lesen")
    out: list[Snapshot] = []
    for line in r.out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        parsed = _parse_volid(parts[0])
        if not parsed:
            continue
        kind, vmid, ts, pbs = parsed
        # Spalten: Volid  Format  Type  Size  VMID -> Groesse ist die vorletzte.
        size = int(parts[-2]) if parts[-2].isdigit() else 0
        out.append(Snapshot(volid=parts[0], vmid=vmid, kind=kind, ts=ts, size=size, pbs=pbs))
    return sorted(out, key=lambda s: s.ts, reverse=True)


def latest_snapshot(host: Host, storage: str, vmid: int) -> Snapshot:
    snaps = [s for s in list_snapshots(host, storage) if s.vmid == vmid]
    if not snaps:
        raise RuntimeError(f"Kein Backup fuer VMID {vmid} in Storage {storage} gefunden")
    return snaps[0]


def list_nodes(host: Host) -> list[dict]:
    """Knoten des Clusters. Bei einem Einzelhost genau einer."""
    r = host.run("pvesh", "get", "/nodes", "--output-format", "json")
    if r.ok:
        try:
            return [{"node": n["node"], "status": n.get("status", "?")}
                    for n in json.loads(r.out)]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return [{"node": host.node_name(), "status": "online"}]


def list_storages(host: Host, node: str | None = None) -> dict:
    """Storages, getrennt nach Verwendungszweck.

    'sources' sind Storages mit Backup-Inhalt - das kann PBS sein, aber ebenso
    ein lokales Verzeichnis mit vzdump-Dateien.
    'targets' sind Storages, die Datentraeger aufnehmen koennen.
    """
    # Immer knotenbezogen abfragen - nur dieser Pfad liefert die Belegung mit.
    path = f"/nodes/{node or host.node_name()}/storage"
    r = host.run("pvesh", "get", path, "--output-format", "json")
    sources: list[dict] = []
    targets: list[dict] = []
    if r.ok:
        try:
            for s in json.loads(r.out):
                name = s.get("storage")
                content = str(s.get("content", ""))
                entry = {"name": name, "type": s.get("type"), "content": content,
                         "active": bool(s.get("active", 1)),
                         "avail": s.get("avail"), "total": s.get("total")}
                if "backup" in content:
                    sources.append(entry)
                if "images" in content or "rootdir" in content:
                    targets.append(entry)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"sources": sources, "targets": targets}


def list_guests(host: Host) -> list[dict]:
    """Alle VMs und Container des Hosts - Grundlage der Uebersicht in der GUI."""
    guests: list[dict] = []

    r = host.run("qm", "list")
    for line in r.out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            guests.append({"vmid": int(parts[0]), "name": parts[1],
                           "kind": "vm", "status": parts[2]})

    r = host.run("pct", "list")
    for line in r.out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit():
            # Spalten: VMID Status [Lock] Name - der Name ist immer das letzte Feld.
            guests.append({"vmid": int(parts[0]), "name": parts[-1],
                           "kind": "ct", "status": parts[1]})

    return sorted(guests, key=lambda g: g["vmid"])


def pick_scratch_vmid(host: Host, lo: int, hi: int, vergeben=()) -> int:
    """Freie VMID aus dem Scratch-Bereich. Nie eine produktive ID.

    'vergeben' sind IDs, die ein anderer Lauf gerade fuer sich beansprucht, aber
    noch nicht angelegt hat. Ohne die griffen zwei gleichzeitige Laeufe nach
    derselben Nummer - der zweite Restore liefe dann in eine bestehende
    Konfiguration.
    """
    used = set(int(v) for v in vergeben)
    r = host.sh("ls /etc/pve/qemu-server /etc/pve/lxc 2>/dev/null")
    for name in r.out.split():
        m = re.match(r"^(\d+)\.conf$", name)
        if m:
            used.add(int(m.group(1)))
    for vmid in range(lo, hi + 1):
        if vmid not in used:
            return vmid
    raise RuntimeError(f"Kein freier Scratch-Slot im Bereich {lo}-{hi}")


# --- Wiederherstellung --------------------------------------------------------

def restore(host: Host, snap: Snapshot, target_vmid: int, storage: str,
            timeout: int = 3600) -> Result:
    """Stellt ein Backup unter einer Scratch-VMID wieder her - ohne es zu starten.

    Bewusst OHNE --live-restore. Das Verfahren klingt verlockend, weil die VM in
    Sekunden statt Minuten laeuft, ist hier aber nicht absicherbar: qmrestore
    startet die VM als Teil des Befehls, mit der Netzwerkkonfiguration aus dem
    Backup. Es gibt kein Zeitfenster, in dem sich die Karten vorher auf die
    isolierte Bridge umhaengen liessen - die VM stuende also fuer die Dauer des
    Restores mit den Original-Adressen im Produktivnetz.

    Praktisch kostet der Verzicht nichts: die Pruefungen liefen ohnehin erst
    nach Abschluss des Restores an.
    """
    assert_scratch_vmid(target_vmid)

    if snap.kind == "vm":
        args = ["qmrestore", snap.volid, str(target_vmid),
                "--storage", storage,
                "--unique"]          # neue MAC-Adressen, Pflicht
    else:
        args = ["pct", "restore", str(target_vmid), snap.volid,
                "--storage", storage]
    return host.run(*args, timeout=timeout)


def list_nics(host: Host, vmid: int, kind: str) -> list[str]:
    """Alle Netzwerkkarten des Gastes, z. B. ['net0', 'net1', 'net6']."""
    cmd = "qm" if kind == "vm" else "pct"
    cfg = host.run(cmd, "config", str(vmid)).out
    nets = {m.group(1) for m in re.finditer(r"^(net\d+):", cfg, re.M)}
    return sorted(nets, key=lambda n: int(n[3:]))


def strip_extra_nics(host: Host, vmid: int, kind: str) -> list[str]:
    """Entfernt ALLE Netzwerkkarten ausser net0.

    Das ist sicherheitskritisch. Ein Gast wie ein DNS-Server oder ein Router
    haengt haeufig mit mehreren Karten in mehreren VLANs. Wuerde man nur net0
    umschreiben, stuenden die uebrigen Karten weiterhin mit den Original-IPs
    im Produktivnetz - der Testgast kollidiert dann mit dem Original, obwohl
    net0 sauber isoliert ist.

    Der Testgast bekommt daher grundsaetzlich genau eine Netzwerkkarte, und
    zwar die, die wir selbst setzen.
    """
    assert_scratch_vmid(vmid)
    extra = [n for n in list_nics(host, vmid, kind) if n != "net0"]
    if not extra:
        return []
    cmd = "qm" if kind == "vm" else "pct"
    host.run(cmd, "set", str(vmid), "--delete", ",".join(extra)).check(
        f"Zusaetzliche Netzwerkkarten entfernen ({', '.join(extra)})")

    # Gegenprobe - eine verbliebene Karte waere ein Weg ins Produktivnetz.
    rest = [n for n in list_nics(host, vmid, kind) if n != "net0"]
    if rest:
        raise RuntimeError(
            f"Netzwerkkarten {rest} liessen sich nicht entfernen. Abbruch, weil der "
            "Testgast sonst mit den Original-Adressen im Netz staende.")
    return extra


def apply_network(host: Host, vmid: int, kind: str, bridge: str, mac: str,
                  ip_cidr: str | None = None, gateway: str | None = None) -> Result:
    """Setzt net0 des Testgastes. Immer mit frischer MAC."""
    assert_scratch_vmid(vmid)
    if kind == "vm":
        spec = f"virtio={mac},bridge={bridge},firewall=1"
        return host.run("qm", "set", str(vmid), "--net0", spec)

    spec = f"name=eth0,hwaddr={mac},bridge={bridge},firewall=1"
    if ip_cidr:
        spec += f",ip={ip_cidr}"
        if gateway:
            spec += f",gw={gateway}"
    else:
        spec += ",ip=manual"
    return host.run("pct", "set", str(vmid), "--net0", spec)


def scratch_label(source_name: str, source_vmid: int) -> str:
    """Unverwechselbarer Name fuer einen Testgast.

    Ohne das traegt der wiederhergestellte Gast den Namen des Originals und ist
    in der PVE-Oberflaeche nicht davon zu unterscheiden - eine Einladung zur
    Verwechslung. Das Praefix macht auf einen Blick klar, was man vor sich hat.
    """
    base = re.sub(r"[^a-zA-Z0-9-]+", "-", (source_name or "").strip()).strip("-").lower()
    base = base[:28] or f"{source_vmid}"
    return f"proxfy-{base}"


def mark_as_test(host: Host, vmid: int, kind: str, job_id: str,
                 source_name: str = "", source_vmid: int = 0) -> None:
    """Markiert den Gast: Name, Tag und Beschreibung.

    Der Tag ist die Rueckfallebene - an ihm findet der Aufraeumdienst auch
    Gaeste wieder, die ein abgestuerzter Lauf zurueckgelassen hat.
    """
    assert_scratch_vmid(vmid)
    cmd = "qm" if kind == "vm" else "pct"
    desc = (f"{SCRATCH_DESC_PREFIX} job={job_id} quelle={kind}/{source_vmid} "
            f"-- AUTOMATISCH ERZEUGT, NICHT VERWENDEN")
    label = scratch_label(source_name, source_vmid)

    host.run(cmd, "set", str(vmid), "--description", desc)
    host.run(cmd, "set", str(vmid), "--tags", SCRATCH_TAG)
    if kind == "vm":
        host.run(cmd, "set", str(vmid), "--name", label)
    else:
        host.run(cmd, "set", str(vmid), "--hostname", label)


def start(host: Host, vmid: int, kind: str) -> Result:
    assert_scratch_vmid(vmid)
    cmd = "qm" if kind == "vm" else "pct"
    return host.run(cmd, "start", str(vmid), timeout=180)


def stop(host: Host, vmid: int, kind: str) -> Result:
    assert_scratch_vmid(vmid)
    cmd = "qm" if kind == "vm" else "pct"
    return host.run(cmd, "stop", str(vmid), timeout=180)


def restore_running(host: Host, vmid: int) -> bool:
    """Laeuft gerade eine Wiederherstellung auf diese VMID?

    Waehrend eines Restores steht der Gast als 'stopped' mit 0 GB Datentraeger
    da und sieht damit aus wie ein verwaister Rest. Wer ihn dann vernichtet,
    zerschiesst einen laufenden Auftrag. Deshalb wird vor jeder zerstoerenden
    Aktion nachgesehen, ob ein Restore-Prozess auf diese VMID zeigt.
    """
    r = host.sh(
        "ps -eo args= 2>/dev/null | grep -E '(qmrestore|pct[[:space:]]+restore)' "
        f"| grep -v grep | grep -cE '(^|[^0-9]){vmid}([^0-9]|$)' || true")
    try:
        return int(r.out.strip() or "0") > 0
    except ValueError:
        return False


def destroy(host: Host, vmid: int, kind: str, force: bool = False) -> Result:
    """Vernichtet den Testgast. Torwaechter laeuft hier ein zweites Mal.

    Vorher zwei Pruefungen:
      - Laeuft eine Wiederherstellung auf diese VMID, wird abgebrochen. Sonst
        zerschiesst das Aufraeumen einen laufenden Auftrag.
      - Eine haengende Sperre wird geloest: bricht ein Restore mittendrin ab,
        bleibt der Gast mit lock=create stehen und laesst sich sonst weder
        starten noch entfernen.
    """
    assert_scratch_vmid(vmid)
    if not force and restore_running(host, vmid):
        raise SafetyError(
            f"Auf VMID {vmid} laeuft gerade eine Wiederherstellung. Nicht vernichtet - "
            "sonst waere der laufende Auftrag zerstoert.")
    cmd = "qm" if kind == "vm" else "pct"
    host.run(cmd, "unlock", str(vmid))
    stop(host, vmid, kind)
    time.sleep(2)
    if kind == "vm":
        return host.run("qm", "destroy", str(vmid), "--purge", "1",
                        "--destroy-unreferenced-disks", "1", timeout=600)
    return host.run("pct", "destroy", str(vmid), "--purge", "1", timeout=600)


def reste_ohne_konfiguration(host: Host, lo: int, hi: int,
                             protected: set[int] | None = None) -> dict[int, list[str]]:
    """Datentraeger im Scratch-Bereich, zu denen es keinen Gast mehr gibt.

    Bricht eine Wiederherstellung mittendrin ab, koennen Datentraeger und
    Einhaengepunkte die Konfiguration ueberleben - der Gast ist dann fort, aber
    zwoelf Gigabyte liegen weiter belegt herum. Wer nur nach Konfigurations-
    dateien sucht, sieht das nie.
    """
    protected = protected or set()
    vorhanden = set()
    r = host.sh("ls /etc/pve/qemu-server /etc/pve/lxc 2>/dev/null")
    for name in r.out.split():
        m = re.match(r"^(\d+)\.conf$", name)
        if m:
            vorhanden.add(int(m.group(1)))

    raus: dict[int, list[str]] = {}
    # pvesm listet die Datentraeger je Storage samt zugehoeriger VMID.
    st = host.sh("pvesm status --content images,rootdir 2>/dev/null | awk 'NR>1 {print $1}'")
    for storage in st.out.split():
        lst = host.sh(f"pvesm list {shlex.quote(storage)} 2>/dev/null")
        for zeile in lst.out.splitlines()[1:]:
            teile = zeile.split()
            if len(teile) < 5:
                continue
            volid = teile[0]
            try:
                vmid = int(teile[-1])
            except ValueError:
                continue
            if not (lo <= vmid <= hi) or vmid in protected or vmid in vorhanden:
                continue
            raus.setdefault(vmid, []).append(volid)
    return raus


def reste_entfernen(host: Host, vmid: int, volids: list[str]) -> list[str]:
    """Haengt aus und gibt frei. Nur im Scratch-Bereich - das prueft der Torwaechter."""
    assert_scratch_vmid(vmid)
    getan = []
    # Erst aushaengen: solange etwas gemountet ist, verweigert LVM das Loeschen
    # mit "contains a filesystem in use". Mehrfach, weil sich bei einem
    # abgebrochenen Restore Einhaengungen uebereinander stapeln koennen.
    pfad = f"/var/lib/lxc/{vmid}/rootfs"
    host.sh(f"for i in 1 2 3 4; do umount {shlex.quote(pfad)} 2>/dev/null || break; done")
    for volid in volids:
        r = host.run("pvesm", "free", volid, timeout=300)
        getan.append(f"{volid}: {'freigegeben' if r.ok else (r.err or r.out).strip()[:80]}")
    host.sh(f"rm -rf {shlex.quote('/var/lib/lxc/' + str(vmid))} 2>/dev/null")
    return getan


def reap_orphans(host: Host, lo: int, hi: int, dry_run: bool = True,
                 protected: set[int] | None = None) -> list[str]:
    """Findet Testgaeste, die ein abgestuerzter Lauf zurueckgelassen hat.

    'protected' sind VMIDs mit gueltiger Belegung - also Testgaeste, die
    absichtlich stehen bleiben. Die werden uebergangen, sonst raeumt dieser
    Knopf genau das weg, was der Nutzer behalten wollte.

    Alles andere im Scratch-Bereich gilt als entbehrlich - unabhaengig von der
    Markierung. Der Bereich gehoert ausschliesslich diesem Werkzeug, und ein
    Restore, der mittendrin abbricht, kommt gar nicht mehr dazu, die Markierung
    zu setzen: er haengt dann mit lock=create fest und waere sonst unauffindbar.
    """
    protected = protected or set()
    found: list[str] = []
    r = host.sh("ls /etc/pve/qemu-server /etc/pve/lxc 2>/dev/null")
    for name in sorted(set(r.out.split())):
        m = re.match(r"^(\d+)\.conf$", name)
        if not m:
            continue
        vmid = int(m.group(1))
        if not (lo <= vmid <= hi) or vmid in protected:
            continue
        for kind, cmd in (("vm", "qm"), ("ct", "pct")):
            cfg = host.run(cmd, "config", str(vmid))
            if not cfg.ok:
                continue
            marked = is_scratch_marked(cfg.out)
            found.append(f"{kind}/{vmid}" + ("" if marked else " (unfertig)"))
            if not dry_run:
                destroy(host, vmid, kind)

    # Und was gar keine Konfiguration mehr hat, aber weiter Platz belegt.
    for vmid, volids in sorted(reste_ohne_konfiguration(host, lo, hi, protected).items()):
        found.append(f"reste/{vmid} ({len(volids)} Datentraeger)")
        if not dry_run:
            reste_entfernen(host, vmid, volids)
            break
    return found


# --- Ausfuehrung im Gast ------------------------------------------------------

def agent_ping(host: Host, vmid: int) -> bool:
    assert_scratch_vmid(vmid)
    return host.run("qm", "agent", str(vmid), "ping", timeout=20).ok


def wait_for_agent(host: Host, vmid: int, timeout: int, poll: int = 5) -> float:
    """Wartet, bis der QEMU-Guest-Agent antwortet. Gibt die Dauer zurueck."""
    assert_scratch_vmid(vmid)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if agent_ping(host, vmid):
            return time.monotonic() - t0
        time.sleep(poll)
    raise TimeoutError(
        f"Guest-Agent von VM {vmid} hat innerhalb von {timeout}s nicht geantwortet. "
        "Entweder bootet die VM nicht, oder qemu-guest-agent ist nicht installiert."
    )


def wait_for_ct(host: Host, vmid: int, timeout: int, poll: int = 3) -> float:
    """Wartet, bis im Container Kommandos ausfuehrbar sind."""
    assert_scratch_vmid(vmid)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if host.run("pct", "exec", str(vmid), "--", "true", timeout=20).ok:
            return time.monotonic() - t0
        time.sleep(poll)
    raise TimeoutError(f"Container {vmid} war nach {timeout}s nicht erreichbar")


@dataclasses.dataclass
class ExecResult:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def guest_exec(host: Host, vmid: int, kind: str, argv: list[str], timeout: int = 60) -> ExecResult:
    """Fuehrt ein Kommando im Gast aus - via Guest-Agent (VM) oder pct exec (CT)."""
    assert_scratch_vmid(vmid)
    if kind == "ct":
        r = host.run("pct", "exec", str(vmid), "--", *argv, timeout=timeout + 20)
        return ExecResult(r.rc, r.out, r.err)

    r = host.run("qm", "guest", "exec", str(vmid), "--timeout", str(timeout), "--", *argv,
                 timeout=timeout + 30)
    if not r.ok:
        return ExecResult(255, "", r.err.strip() or "guest exec fehlgeschlagen")
    try:
        data = json.loads(r.out)
    except json.JSONDecodeError:
        return ExecResult(255, r.out, "Antwort des Guest-Agent war kein gueltiges JSON")
    return ExecResult(int(data.get("exitcode", 255)),
                      data.get("out-data", "") or "",
                      data.get("err-data", "") or "")
