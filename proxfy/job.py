"""Ablauf eines Verifikationslaufs.

Ein Lauf ist erfolgreich, wenn jede als 'required' markierte Pruefung besteht.

Der Testgast wird am Ende nach der Lebensdauer-Richtlinie behandelt:

  destroy : sofort vernichten (Standard, richtig fuer den Automatiklauf)
  ttl     : N Minuten stehen lassen, der Aufraeumdienst entfernt ihn
  manual  : stehen lassen, bis jemand ihn von Hand entfernt

Bei 'ttl' und 'manual' wird der Gast NICHT vernichtet - dafuer traegt er die
Markierung, und die Belegung landet in der Lease-Tabelle. Beides zusammen
verhindert, dass ein vergessener Testgast unbemerkt Platz belegt.

Scheitert ein Lauf, BEVOR der Gast lief, wird immer vernichtet - dann gibt es
nichts, in das man hineinschauen koennte. Scheitert er danach, greift die
Richtlinie: einen durchgefallenen Gast will man untersuchen.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import time
import uuid

from . import adressen, checks, guestnet, netguard, pve
from .config import SCRATCH_VMID_MAX, SCRATCH_VMID_MIN, Config
from .ssh import Host


@dataclasses.dataclass
class Phase:
    name: str
    ok: bool
    detail: str
    duration: float


@dataclasses.dataclass
class JobReport:
    job_id: str
    source_vmid: int
    kind: str
    snapshot: str
    mode: str
    scratch_vmid: int | None
    started: str
    source_name: str = ""
    node: str = ""
    backup_storage: str = ""
    target_storage: str = ""
    phases: list[Phase] = dataclasses.field(default_factory=list)
    checks: list[checks.CheckResult] = dataclasses.field(default_factory=list)
    error: str | None = None
    duration: float = 0.0
    cleaned_up: bool = False
    keep: str = "destroy"
    kept: bool = False
    expires_at: str | None = None
    ip: str | None = None

    @property
    def verified(self) -> bool:
        """Verifiziert heisst: alles bis zu den Pruefungen lief, und keine
        Pflichtpruefung ist durchgefallen."""
        if self.error or not all(p.ok for p in self.phases):
            return False
        required = [c for c in self.checks if c.required and not c.skipped]
        return bool(required) and all(c.passed for c in required)

    @property
    def verdict(self) -> str:
        if self.error:
            return "ABGEBROCHEN"
        return "VERIFIZIERT" if self.verified else "DURCHGEFALLEN"


class Runner:
    def __init__(self, host: Host, cfg: Config, log=print, reserved_ips=None,
                 reserviere=None):
        self.host, self.cfg, self.log = host, cfg, log
        # Adressen, die bereits von lebenden Testgaesten belegt sind. Der
        # Preflight allein sieht sie nicht, wenn der Gast gerade nicht antwortet.
        self.reserved_ips = set(reserved_ips or ())
        # Rueckruf, mit dem der Lauf seine Scratch-VMID und seine Adresse
        # anmeldet, solange beides noch nicht in der Wirklichkeit steht.
        # Ohne ihn (Kommandozeile) laeuft ohnehin nur einer.
        self.reserviere = reserviere or (lambda **_: None)

    @contextlib.contextmanager
    def _phase(self, report: JobReport, name: str):
        """Misst eine Phase, protokolliert sie und haengt sie an den Bericht.

        Der Block bekommt ein dict, in das er unter 'd' eine Kurzbeschreibung
        legen kann - die landet im Bericht.
        """
        detail: dict = {}
        self.log(f"  -> {name} ...")
        t0 = time.monotonic()
        try:
            yield detail
        except Exception as e:
            d = time.monotonic() - t0
            report.phases.append(Phase(name, False, f"{type(e).__name__}: {e}", d))
            self.log(f"     {name}: FEHLER ({d:.1f}s) {e}")
            raise
        d = time.monotonic() - t0
        report.phases.append(Phase(name, True, detail.get("d", "ok"), d))
        self.log(f"     {name}: ok ({d:.1f}s) {detail.get('d', '')}")

    def _adresse_waehlen(self, target: dict, host, reserviert: set) -> tuple:
        """Loest die Adressangabe eines Laufs auf.

        Kommt ein Vorrats-Eintrag mit, wird daraus die naechste freie Adresse
        genommen - erst gegen laufende Testgaeste, dann gegen das echte Netz
        geprueft. Damit lassen sich mehrere Gaeste gleichzeitig geroutet pruefen,
        was mit einer festen Adresse nicht ging.
        """
        vorrat = target.get("ip_pool")
        if not vorrat:
            return target.get("ip"), target.get("gateway")

        eintrag = adressen.zerlege(str(vorrat.get("ip_cidr", "")))
        gateway = target.get("gateway") or vorrat.get("gateway")

        def pruefer(kandidat: str) -> None:
            netguard.preflight_ip(host, kandidat, self.cfg.restore.lan_bridge)

        if not eintrag.ist_bereich:
            gewaehlt = eintrag.adressen()[0]
            if gewaehlt.split("/")[0] in reserviert:
                raise netguard.PreflightError(
                    f"{gewaehlt} ist bereits an einen laufenden Testgast vergeben")
            return gewaehlt, gateway

        gewaehlt = adressen.naechste_freie(eintrag, reserviert, pruefer)
        self.log(f"     aus {eintrag.anzeige()} gewaehlt: {gewaehlt}")
        return gewaehlt, gateway

    def run(self, target: dict) -> JobReport:
        source_vmid = int(target["vmid"])
        mode = target.get("mode", "isolated")
        keep = target.get("keep", "destroy")
        ttl_minutes = int(target.get("ttl_minutes") or 0)
        if keep == "ttl" and ttl_minutes <= 0:
            keep = "destroy"

        backup_storage = target.get("backup_storage") or self.cfg.restore.backup_storage
        target_storage = target.get("target_storage") or self.cfg.restore.target_storage
        node = target.get("node") or None

        job_id = uuid.uuid4().hex[:8]
        t0 = time.monotonic()

        report = JobReport(
            job_id=job_id, source_vmid=source_vmid, kind="?", snapshot="?", mode=mode,
            scratch_vmid=None,
            started=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            source_name=target.get("name", ""), node=node or "",
            backup_storage=backup_storage, target_storage=target_storage, keep=keep,
        )
        self.log(f"\n=== Job {job_id}: VMID {source_vmid}, Modus '{mode}', "
                 f"Lebensdauer '{keep}' ===")

        # Ein Restore muss auf dem Knoten laufen, der den Gast am Ende traegt.
        host = self.host.for_node(node)
        if node:
            self.log(f"  (Knoten: {node})")

        scratch: int | None = None
        kind = "?"
        started_ok = False
        try:
            with self._phase(report, "Backup auswaehlen") as p:
                if target.get("snapshot"):
                    snap = next(s for s in pve.list_snapshots(host, backup_storage)
                                if s.volid == target["snapshot"])
                else:
                    snap = pve.latest_snapshot(host, backup_storage, source_vmid)
                kind = report.kind = snap.kind
                report.snapshot = snap.volid
                p["d"] = (f"{snap.label} ({snap.size / 1e9:.1f} GB) aus {backup_storage}"
                          + ("" if snap.pbs else " [vzdump-Datei]"))

            with self._phase(report, "Netzwerk planen") as p:
                ip, gateway = (self._adresse_waehlen(target, host, self.reserved_ips)
                               if mode == "routed" else (None, None))
                plan = netguard.plan_network(
                    host, mode, self.cfg.restore.isolated_bridge, self.cfg.restore.lan_bridge,
                    ip_cidr=ip, gateway=gateway,
                    skip_preflight=bool(target.get("skip_preflight")) or bool(target.get("ip_pool")))
                report.ip = plan.ip_cidr
                p["d"] = f"MAC {plan.mac}, Start an {self.cfg.restore.isolated_bridge}"

            with self._phase(report, "Wiederherstellen") as p:
                scratch = pve.pick_scratch_vmid(host, SCRATCH_VMID_MIN, SCRATCH_VMID_MAX,
                                                vergeben=self.reserviere(fragen=True))
                self.reserviere(scratch=scratch)
                report.scratch_vmid = scratch
                r = pve.restore(host, snap, scratch, target_storage)
                if not r.ok:
                    raise RuntimeError(f"Restore fehlgeschlagen: {(r.err or r.out).strip()[-800:]}")
                pve.mark_as_test(host, scratch, kind, job_id,
                                 source_name=report.source_name, source_vmid=source_vmid)
                p["d"] = f"als {kind}/{scratch} auf {target_storage}"

            with self._phase(report, "Netzwerk vereinzeln") as p:
                # Muss VOR dem ersten Start passieren: ein Gast mit mehreren
                # Karten in mehreren VLANs stuende sonst mit den Original-IPs
                # im Produktivnetz.
                removed = pve.strip_extra_nics(host, scratch, kind)
                netguard.ensure_isolated_bridge(host, self.cfg.restore.isolated_bridge)
                pve.apply_network(host, scratch, kind,
                                  self.cfg.restore.isolated_bridge, plan.mac).check("net0 setzen")
                p["d"] = (f"eine Karte an {self.cfg.restore.isolated_bridge}"
                          + (f", entfernt: {', '.join(removed)}" if removed else ""))

            with self._phase(report, "Isoliert starten") as p:
                cmd = "qm" if kind == "vm" else "pct"
                if "running" not in host.run(cmd, "status", str(scratch)).out:
                    pve.start(host, scratch, kind).check("Gast starten")
                started_ok = True
                p["d"] = f"an {self.cfg.restore.isolated_bridge}, kein Uplink"

            with self._phase(report, "Auf Gast warten") as p:
                if kind == "vm":
                    took = pve.wait_for_agent(host, scratch, self.cfg.restore.agent_timeout)
                    p["d"] = f"Guest-Agent nach {took:.0f}s"
                else:
                    took = pve.wait_for_ct(host, scratch, self.cfg.restore.boot_timeout)
                    p["d"] = f"Container nach {took:.0f}s"

            ctx = checks.Ctx(host, scratch, kind, ip=None)
            specs = [{"type": "boot", "name": "Gast bootet"}] + list(target.get("checks", []))
            inner = [s for s in specs if not checks.is_external(s)]
            external = [s for s in specs if checks.is_external(s)]

            self.log(f"  -> Pruefungen innen ({len(inner)})")
            for spec in inner:
                res = checks.run_check(ctx, spec)
                report.checks.append(res)
                self.log(f"     [{res.status}] {res.name}: "
                         f"{res.detail.splitlines()[0][:140] if res.detail else ''}")

            if plan.is_routed:
                with self._phase(report, "IP im Gast vergeben") as p:
                    if kind == "ct":
                        pve.stop(host, scratch, kind)
                        pve.apply_network(host, scratch, kind, self.cfg.restore.lan_bridge,
                                          plan.mac, plan.ip_cidr,
                                          plan.gateway).check("CT-Netz setzen")
                        pve.start(host, scratch, kind).check("CT neu starten")
                        pve.wait_for_ct(host, scratch, self.cfg.restore.boot_timeout)
                        p["d"] = f"{plan.ip_cidr} via PVE-Konfiguration"
                    else:
                        if guestnet.is_windows(host, scratch):
                            d = guestnet.assign_ip_windows(host, scratch, plan.ip_cidr, plan.gateway)
                        else:
                            d = guestnet.assign_ip_linux(host, scratch, kind, plan.ip_cidr,
                                                         plan.gateway)
                        guestnet.switch_to_bridge(host, scratch, kind,
                                                  self.cfg.restore.lan_bridge, plan.mac)
                        p["d"] = d

                ctx.ip = plan.ip_cidr.split("/")[0]
                time.sleep(3)
                if external:
                    self.log(f"  -> Pruefungen aussen gegen {ctx.ip} ({len(external)})")
                    for spec in external:
                        res = checks.run_check(ctx, spec)
                        report.checks.append(res)
                        self.log(f"     [{res.status}] {res.name}: "
                                 f"{res.detail.splitlines()[0][:140] if res.detail else ''}")
            elif external:
                for spec in external:
                    report.checks.append(checks.CheckResult(
                        spec.get("name", spec.get("type", "?")), spec.get("type", "?"), False,
                        "Modus 'isolated' - keine Aussen-Pruefung moeglich", 0.0,
                        bool(spec.get("required", True)), skipped=True))

        except Exception as e:
            report.error = f"{type(e).__name__}: {e}"
            self.log(f"  !! Abbruch: {report.error}")

        finally:
            if scratch is not None:
                self._finish(report, host, scratch, kind, keep, ttl_minutes, started_ok)

        report.duration = time.monotonic() - t0
        self.log(f"=== Job {job_id}: {report.verdict} ({report.duration:.0f}s) ===")
        return report

    def _finish(self, report: JobReport, host: Host, scratch: int, kind: str,
                keep: str, ttl_minutes: int, started_ok: bool) -> None:
        """Wendet die Lebensdauer-Richtlinie an."""
        if keep == "destroy" or not started_ok:
            if keep != "destroy" and not started_ok:
                self.log("  -> Der Gast lief nie - es gibt nichts zu untersuchen, "
                         "daher wird trotz Richtlinie vernichtet")
            self.log(f"  -> Aufraeumen {kind}/{scratch}")
            try:
                pve.destroy(host, scratch, kind, force=True)
                report.cleaned_up = True
                self.log("     vernichtet")
            except Exception as e:
                self.log(f"     !! Aufraeumen fehlgeschlagen: {e} - der Gast traegt die "
                         "Markierung und wird vom Aufraeumdienst erfasst")
            return

        report.kept = True
        if keep == "ttl":
            until = dt.datetime.now().astimezone() + dt.timedelta(minutes=ttl_minutes)
            report.expires_at = until.isoformat(timespec="seconds")
            self.log(f"  -> {kind}/{scratch} bleibt bis {until:%H:%M} stehen "
                     f"({ttl_minutes} Minuten)")
        else:
            self.log(f"  -> {kind}/{scratch} bleibt stehen, bis er von Hand entfernt wird")
        if report.ip:
            self.log(f"     erreichbar unter {report.ip.split('/')[0]}")
