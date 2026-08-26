"""Kommandozeile."""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

from . import pve
from .config import SCRATCH_VMID_MAX, SCRATCH_VMID_MIN, Config
from .job import JobReport, Runner
from .ssh import Host


def _host(cfg: Config) -> Host:
    return Host(cfg.host.host, cfg.host.user, cfg.host.key_file, cfg.host.port)


def _print_report(r: JobReport) -> None:
    print()
    print("-" * 72)
    print(f"Job {r.job_id}  |  Quelle {r.kind}/{r.source_vmid}  |  Modus {r.mode}")
    print(f"Backup  {r.snapshot}")
    print(f"Dauer   {r.duration:.0f}s   Aufgeraeumt: {'ja' if r.cleaned_up else 'NEIN'}")
    print("-" * 72)
    for p in r.phases:
        print(f"  [{'ok ' if p.ok else 'FEHL'}] {p.name:<26} {p.duration:6.1f}s  {p.detail}")
    if r.checks:
        print("  Pruefungen:")
        for c in r.checks:
            mark = "ok " if c.passed else ("--- " if c.skipped else "FEHL")
            opt = "" if c.required else " (optional)"
            print(f"  [{mark}] {c.name:<26} {c.duration:6.1f}s  {c.detail.splitlines()[0][:80] if c.detail else ''}{opt}")
    if r.error:
        print(f"  Abbruchgrund: {r.error}")
    print("-" * 72)
    print(f"ERGEBNIS: {r.verdict}")
    print("-" * 72)


def cmd_snapshots(args, cfg: Config) -> int:
    snaps = pve.list_snapshots(_host(cfg), cfg.restore.backup_storage)
    if args.vmid:
        snaps = [s for s in snaps if s.vmid == args.vmid]
    seen: set[int] = set()
    for s in snaps:
        if args.latest_only:
            if s.vmid in seen:
                continue
            seen.add(s.vmid)
        print(f"{s.kind}/{s.vmid:<5} {s.ts:<24} {s.size / 1e9:8.2f} GB  {s.volid}")
    return 0


def cmd_run(args, cfg: Config) -> int:
    host = _host(cfg)
    host.ping()

    if args.vmid:
        targets = [{
            "vmid": args.vmid,
            "mode": args.mode,
            "ip": args.ip,
            "gateway": args.gateway,
            "skip_preflight": args.skip_preflight,
            "checks": [],
        }]
    else:
        targets = cfg.targets
        if args.only:
            targets = [t for t in targets if int(t["vmid"]) in args.only]
    if not targets:
        print("Keine Ziele. Entweder --vmid angeben oder 'targets' in der Konfiguration fuellen.",
              file=sys.stderr)
        return 2

    runner = Runner(host, cfg)
    reports = [runner.run(t) for t in targets]
    for r in reports:
        _print_report(r)

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps([dataclasses.asdict(r) for r in reports], indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"\nBericht geschrieben: {args.json}")

    ok = sum(1 for r in reports if r.verified)
    print(f"\nGesamt: {ok}/{len(reports)} verifiziert")
    return 0 if ok == len(reports) else 1


def cmd_config(args, cfg: Config) -> int:
    """Rettungsweg, wenn die Oberflaeche nicht mehr erreichbar ist.

    Die Datenbank ueberlagert die Datei. Wer sich mit einer Einstellung
    ausgesperrt hat, raeumt die Ueberlagerung hier weg - danach gilt wieder,
    was in der config.yaml steht.
    """
    from .store import Store
    store = Store(args.db)

    if args.aktion == "show":
        cfg.anwenden(store.get_settings())
        ueberlagert = store.get_settings()
        for schluessel, wert in sorted(cfg.als_dict().items()):
            herkunft = "Oberflaeche" if schluessel in ueberlagert else "Datei"
            print(f"{schluessel:<32} {str(wert):<28} ({herkunft})")
        return 0

    if args.aktion == "set":
        if not args.schluessel or args.wert is None:
            print("Aufruf: config set <schluessel> <wert>", file=sys.stderr)
            return 2
        wert: object = args.wert
        if wert.lower() in ("true", "ja"):
            wert = True
        elif wert.lower() in ("false", "nein"):
            wert = False
        elif wert.lstrip("-").isdigit():
            wert = int(wert)
        store.set_settings({args.schluessel: wert}, "Kommandozeile")
        print(f"{args.schluessel} = {wert!r} gesetzt. Dienst neu starten, damit es greift.")
        return 0

    if args.aktion == "reset":
        keys = [args.schluessel] if args.schluessel else None
        store.clear_settings(keys)
        print("Zurueckgesetzt auf die Werte der Datei: "
              + (args.schluessel if args.schluessel else "alles"))
        print("Dienst neu starten, damit es greift.")
        return 0

    print(f"Unbekannte Aktion '{args.aktion}'", file=sys.stderr)
    return 2


def cmd_serve(args, cfg: Config) -> int:
    from .web import serve
    serve(cfg, args.db, args.bind, args.port)
    return 0


def cmd_reap(args, cfg: Config) -> int:
    host = _host(cfg)
    found = pve.reap_orphans(host, SCRATCH_VMID_MIN, SCRATCH_VMID_MAX, dry_run=not args.force)
    if not found:
        print("Keine verwaisten Testgaeste gefunden.")
        return 0
    action = "vernichtet" if args.force else "gefunden (Probelauf, --force zum Vernichten)"
    print(f"{len(found)} Testgast/-gaeste {action}: {', '.join(found)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="proxfy",
        description="Restore-Verifikation fuer Proxmox Backup Server: "
                    "stellt Backups isoliert wieder her, prueft sie und raeumt auf.")
    ap.add_argument("-c", "--config", default="config.yaml", help="Konfigurationsdatei")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("snapshots", help="Verfuegbare Backups auflisten")
    p.add_argument("--vmid", type=int, help="Nur diese VMID")
    p.add_argument("--latest-only", action="store_true", help="Nur das neueste Backup je Gast")
    p.set_defaults(fn=cmd_snapshots)

    p = sub.add_parser("run", help="Verifikationslauf starten")
    p.add_argument("--vmid", type=int, help="Einzelnes Ziel, uebergeht die Konfiguration")
    p.add_argument("--mode", choices=["isolated", "routed"], default="isolated",
                   help="isolated = Bridge ohne Uplink (Standard); routed = echte IP im LAN")
    p.add_argument("--ip", help="Ziel-IP in CIDR-Notation, z.B. 192.168.1.240/24 (Modus routed)")
    p.add_argument("--gateway", help="Gateway fuer Modus routed")
    p.add_argument("--skip-preflight", action="store_true",
                   help="IP-Preflight ueberspringen - NICHT verwenden, ausser die IP ist "
                        "nachweislich frei")
    p.add_argument("--only", type=int, nargs="*", help="Nur diese VMIDs aus der Konfiguration")
    p.add_argument("--json", help="Bericht als JSON-Datei schreiben")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("serve", help="Weboberflaeche starten")
    p.add_argument("--bind", default="0.0.0.0", help="Adresse, an die gebunden wird")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--db", default="postgresql:///proxfy?host=/var/run/postgresql",
               help="Verbindung zur Datenbank fuer Verlauf und Profile")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("config", help="Einstellungen anzeigen, setzen oder zuruecksetzen")
    p.add_argument("aktion", choices=["show", "set", "reset"])
    p.add_argument("schluessel", nargs="?", help="z. B. host.host")
    p.add_argument("wert", nargs="?")
    p.add_argument("--db", default="postgresql:///proxfy?host=/var/run/postgresql")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("reap", help="Verwaiste Testgaeste aus abgestuerzten Laeufen aufraeumen")
    p.add_argument("--force", action="store_true", help="Wirklich vernichten (sonst Probelauf)")
    p.set_defaults(fn=cmd_reap)

    args = ap.parse_args(argv)

    if args.cmd == "run" and args.mode == "routed" and not args.ip and args.vmid:
        ap.error("Modus 'routed' verlangt --ip")

    cfg = Config.load(args.config)
    return args.fn(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
