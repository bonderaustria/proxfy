"""Hintergrunddienste.

  JobManager - arbeitet die Warteschlange ab, immer nur einen Lauf gleichzeitig
  Janitor    - vernichtet Testgaeste, deren Frist abgelaufen ist
  Scheduler  - stellt faellige Zeitplaene in die Warteschlange
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import queue
import threading
import time
import traceback

from . import pve
from .config import Config
from .job import Runner
from .ssh import Host
from .store import Store


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


class JobManager:
    """Warteschlange plus Arbeiter.

    Bewusst nur EIN Lauf gleichzeitig: parallele Restores wuerden sich um
    Storage-Bandbreite und Scratch-Slots streiten, und die Ausgabe waere im
    Live-Protokoll nicht mehr zuzuordnen. Eine Mehrfachauswahl in der
    Oberflaeche stellt daher N Auftraege in die Schlange, statt N Laeufe
    gleichzeitig zu starten.
    """

    def __init__(self, host: Host, cfg: Config, store: Store):
        self.host, self.cfg, self.store = host, cfg, store
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self._pending: list[dict] = []
        self.current: dict | None = None
        self._listeners: list[queue.Queue] = []
        self._buffer: list[str] = []
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def busy(self) -> bool:
        return self.current is not None

    def state(self) -> dict:
        with self._lock:
            return {"busy": self.current is not None, "current": self.current,
                    "pending": list(self._pending)}

    # --- Live-Protokoll ------------------------------------------------------

    def subscribe(self):
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._listeners.append(q)
            return q, list(self._buffer)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def emit(self, line) -> None:
        text = str(line)
        with self._lock:
            self._buffer.append(text)
            if len(self._buffer) > 4000:
                del self._buffer[:2000]
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait(text)
            except queue.Full:
                pass

    # --- Auftraege -----------------------------------------------------------

    def enqueue(self, targets: list[dict], source: str = "manuell") -> dict:
        accepted = []
        with self._lock:
            for t in targets:
                item = dict(t)
                item["_source"] = source
                self._pending.append({
                    "vmid": item.get("vmid"), "mode": item.get("mode", "isolated"),
                    "keep": item.get("keep", "destroy"), "source": source})
                self._q.put(item)
                accepted.append(item.get("vmid"))
        return {"queued": accepted, "count": len(accepted)}

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            source = item.pop("_source", "manuell")
            with self._lock:
                if self._pending:
                    self._pending.pop(0)
                self.current = {"vmid": item.get("vmid"), "mode": item.get("mode", "isolated"),
                                "keep": item.get("keep", "destroy"), "source": source}
                self._buffer = []
            try:
                self._run_one(item, source)
            except Exception:
                self.emit("!! Unerwarteter Fehler:\n" + traceback.format_exc())
                self.emit("@@DONE@@" + json.dumps({"verdict": "ABGEBROCHEN"}))
            finally:
                with self._lock:
                    self.current = None
                self._q.task_done()

    def _run_one(self, target: dict, source: str) -> None:
        runner = Runner(self.host, self.cfg, log=self.emit,
                        reserved_ips=self.store.leased_ips())
        report = runner.run(target)
        payload = dataclasses.asdict(report)
        payload["verdict"] = report.verdict
        payload["verified"] = report.verified
        self.store.save_job(payload, "\n".join(self._buffer), source,
                            target.get("schedule_id"))

        # Ueberlebt der Gast, muss die Belegung verzeichnet werden - sonst weiss
        # niemand, dass eine Scratch-VMID und womoeglich eine IP vergeben sind.
        if report.kept and report.scratch_vmid is not None:
            self.store.add_lease({
                "scratch_vmid": report.scratch_vmid, "kind": report.kind,
                "job_id": report.job_id, "source_vmid": report.source_vmid,
                "source_name": report.source_name, "mode": report.mode,
                "ip": report.ip, "keep": report.keep, "expires_at": report.expires_at,
                "node": report.node or None})

        self.emit("@@DONE@@" + json.dumps(payload, ensure_ascii=False))


class Janitor:
    """Entfernt Testgaeste nach Fristablauf und gleicht die Lease-Tabelle
    mit der Wirklichkeit ab."""

    def __init__(self, host: Host, store: Store, log=print, interval: int = 30, cfg=None):
        self.host, self.store, self.log, self.interval = host, store, log, interval
        self.cfg = cfg
        threading.Thread(target=self._startup_sweep, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()

    def _startup_sweep(self) -> None:
        """Einmalig beim Start: Reste eines abgestuerzten Dienstes wegraeumen.

        Wird der Dienst mitten in einem Restore beendet, bleibt ein Gast mit
        lock=create stehen. Er hat keine Belegung, gehoert also niemandem, und
        wuerde sonst dauerhaft einen Scratch-Slot blockieren.
        """
        from .config import SCRATCH_VMID_MAX, SCRATCH_VMID_MIN
        try:
            keep = {int(l["scratch_vmid"]) for l in self.store.list_leases()}
            found = pve.reap_orphans(self.host, SCRATCH_VMID_MIN, SCRATCH_VMID_MAX,
                                     dry_run=False, protected=keep)
            if found:
                self.log(f"[Aufraeumer] Reste vom letzten Lauf entfernt: {', '.join(found)}")
        except Exception as e:
            self.log(f"[Aufraeumer] Start-Aufraeumen fehlgeschlagen: {e}")

    def _loop(self) -> None:
        while True:
            try:
                self.sweep()
                self.ruecknahme_pruefen()
            except Exception as e:
                self.log(f"[Aufraeumer] Fehler: {e}")
            time.sleep(self.interval)

    def sweep(self) -> list[str]:
        removed: list[str] = []
        for lease in self.store.due_leases():
            vmid, kind = int(lease["scratch_vmid"]), lease["kind"]
            host = self.host.for_node(lease.get("node"))
            try:
                pve.destroy(host, vmid, kind)
                self.store.release_lease(vmid, "Frist abgelaufen")
                removed.append(f"{kind}/{vmid}")
                self.log(f"[Aufraeumer] {kind}/{vmid} nach Fristablauf vernichtet")
            except Exception as e:
                self.log(f"[Aufraeumer] {kind}/{vmid} nicht vernichtbar: {e}")

        # Leases, deren Gast gar nicht mehr existiert, stillschweigend schliessen.
        for lease in self.store.list_leases():
            vmid, kind = int(lease["scratch_vmid"]), lease["kind"]
            host = self.host.for_node(lease.get("node"))
            cmd = "qm" if kind == "vm" else "pct"
            if not host.run(cmd, "config", str(vmid)).ok:
                self.store.release_lease(vmid, "Gast nicht mehr vorhanden")
        return removed


    def ruecknahme_pruefen(self) -> None:
        """Nimmt eine gefaehrliche Einstellungsaenderung selbsttaetig zurueck.

        Wer Zugriff oder Netzwerk verstellt, kann sich aussperren. Bestaetigt
        niemand die Aenderung binnen der Frist, gilt sie als misslungen - dann
        zaehlt der vorherige Stand.
        """
        offen = self.store.pending_rollback()
        if not offen:
            return
        try:
            faellig = dt.datetime.fromisoformat(offen["faellig"])
        except (ValueError, TypeError):
            return
        if now() < faellig:
            return
        self.store.set_settings(offen["vorher"], "selbsttaetige Ruecknahme")
        if self.cfg is not None:
            self.cfg.anwenden(offen["vorher"])
        self.store.clear_rollback()
        self.log("[Einstellungen] Aenderung nicht bestaetigt - vorheriger Stand "
                 "wiederhergestellt: " + ", ".join(sorted(offen["vorher"])))

    def remove(self, scratch_vmid: int) -> dict:
        """Manuelles Entfernen - der Knopf 'Testgast entfernen' in der Oberflaeche."""
        lease = self.store.get_lease(scratch_vmid)
        if not lease or lease["state"] != "aktiv":
            raise RuntimeError(f"Kein aktiver Testgast mit VMID {scratch_vmid}")
        host = self.host.for_node(lease.get("node"))
        pve.destroy(host, int(scratch_vmid), lease["kind"])
        self.store.release_lease(int(scratch_vmid), "von Hand entfernt")
        self.log(f"[Aufraeumer] {lease['kind']}/{scratch_vmid} von Hand entfernt")
        return {"removed": f"{lease['kind']}/{scratch_vmid}"}


class Scheduler:
    """Stellt faellige Zeitplaene in die Warteschlange.

    Absichtlich minutengenau, ohne Cron-Syntax: Uhrzeit plus Wochentage decken
    den Anwendungsfall ab und sind in einer Oberflaeche ohne Handbuch bedienbar.
    """

    def __init__(self, store: Store, manager: JobManager, log=print, interval: int = 20):
        self.store, self.manager, self.log, self.interval = store, manager, log, interval
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:
                self.log(f"[Zeitplaner] Fehler: {e}")
            time.sleep(self.interval)

    @staticmethod
    def is_due(sched: dict, at: dt.datetime) -> bool:
        if not sched.get("enabled"):
            return False
        if at.weekday() not in (sched.get("weekdays") or []):
            return False
        try:
            hh, mm = (int(x) for x in str(sched.get("at_time", "03:00")).split(":"))
        except ValueError:
            return False
        if (at.hour, at.minute) != (hh, mm):
            return False
        # Doppelausloesung innerhalb derselben Minute verhindern.
        last = sched.get("last_run")
        if last:
            try:
                if (dt.datetime.fromisoformat(last).strftime("%Y-%m-%d %H:%M")
                        == at.strftime("%Y-%m-%d %H:%M")):
                    return False
            except ValueError:
                pass
        return True

    def tick(self) -> None:
        at = now()
        for sched in self.store.list_schedules():
            if not self.is_due(sched, at):
                continue
            self.store.mark_schedule_run(int(sched["id"]), at)
            targets = self.build_targets(sched)
            for t in targets:
                t.setdefault("name", "")
            self.manager.enqueue(targets, source=f"Zeitplan: {sched['name']}")
            self.log(f"[Zeitplaner] '{sched['name']}' ausgeloest, "
                     f"{len(targets)} Gast/Gaeste eingereiht")

    @staticmethod
    def build_targets(sched: dict) -> list[dict]:
        """Faechert einen Zeitplan in einzelne Auftraege auf.

        Eine feste IP laesst sich nicht auf mehrere Gaeste aufteilen. Bei
        Mehrfachauswahl faellt der Modus deshalb auf 'isolated' zurueck, statt
        nacheinander dieselbe Adresse zu vergeben.
        """
        vmids = [int(v) for v in sched.get("vmids", [])]
        # Geroutet geht, sobald ein Adress-Eintrag hinterlegt ist. Ob dessen
        # Bereich fuer alle Gaeste reicht, entscheidet der Lauf selbst - er
        # nimmt jeweils die naechste freie Adresse.
        geroutet = sched.get("mode") == "routed" and bool(sched.get("ip_pool_id"))

        out = []
        for vmid in vmids:
            t = {"vmid": vmid,
                 "mode": "routed" if geroutet else "isolated",
                 "checks": sched.get("checks", []),
                 "keep": sched.get("keep", "destroy"),
                 "ttl_minutes": sched.get("ttl_minutes"),
                 "backup_storage": sched.get("backup_storage"),
                 "target_storage": sched.get("target_storage"),
                 "node": sched.get("node"),
                 "schedule_id": sched.get("id")}
            if geroutet:
                t["ip_pool_id"] = sched.get("ip_pool_id")
                t["gateway"] = sched.get("gateway")
            out.append(t)
        return out
