"""Prüfungen aus einem laufenden Testgast ableiten.

Niemand weiss auswendig, dass paperless auf 8000 hoert und einen Redis braucht.
Statt raten zu lassen, sieht dieses Modul im Gast nach: welche Ports lauschen,
welche Dienste laufen, welche Datenbanken es gibt - und schlaegt daraus fertige
Pruefungen vor.

Wichtig: Das laeuft ausschliesslich gegen einen TESTGAST, nie gegen einen
produktiven Gast. Erkannt wird damit, was tatsaechlich im Backup steckt - was
ohnehin die interessantere Frage ist als das, was auf dem Original laeuft.

Alle Kommandos sind rein lesend.
"""
from __future__ import annotations

import re

from . import pve
from .ssh import Host

# Dienste, die auf jedem Debian laufen und nichts ueber die Anwendung aussagen.
_BORING_UNITS = {
    "systemd-journald", "systemd-logind", "systemd-udevd", "systemd-networkd",
    "systemd-resolved", "systemd-timesyncd", "systemd-user-sessions",
    "dbus", "cron", "rsyslog", "getty", "serial-getty", "console-getty",
    "networking", "ifup", "ifupdown-pre", "user", "session", "polkit",
    "container-getty", "ssh", "sshd", "postfix", "patchmon-agent",
    "unattended-upgrades", "apt-daily", "apt-daily-upgrade", "e2scrub_reap",
    "systemd-tmpfiles-setup", "keyboard-setup", "console-setup", "qemu-guest-agent",
}

# Ports, die zur Grundausstattung gehoeren.
_BORING_PORTS = {22}

# Ports, hinter denen ueblicherweise etwas Web-artiges steckt.
_WEB_PORTS = {80, 443, 3000, 8000, 8080, 8081, 8096, 8123, 9000, 9980, 3456}


def _exec(host: Host, vmid: int, kind: str, argv: list[str], timeout: int = 30):
    return pve.guest_exec(host, vmid, kind, argv, timeout)


def _units(host: Host, vmid: int, kind: str) -> list[dict]:
    """Laufende systemd-Dienste, ohne die Grundausstattung."""
    r = _exec(host, vmid, kind,
              ["systemctl", "list-units", "--type=service", "--state=running",
               "--no-legend", "--plain", "--no-pager"])
    out = []
    for line in r.out.splitlines():
        parts = line.split()
        if not parts or not parts[0].endswith(".service"):
            continue
        unit = parts[0][: -len(".service")]
        base = unit.split("@")[0]
        if base in _BORING_UNITS or base.startswith("systemd-"):
            continue
        out.append({"type": "service", "name": f"Dienst {unit} laeuft", "unit": unit})
    return out


def _ports(host: Host, vmid: int, kind: str) -> tuple[list[dict], list[int]]:
    """Lauschende TCP-Ports samt Prozessnamen."""
    r = _exec(host, vmid, kind, ["ss", "-ltnpH"])
    seen: dict[int, str] = {}
    for line in r.out.splitlines():
        m = re.search(r"\s\S*?[:\.](\d+)\s", " " + line)
        if not m:
            continue
        port = int(m.group(1))
        if port in _BORING_PORTS or port in seen:
            continue
        proc = ""
        pm = re.search(r'users:\(\("([^"]+)"', line)
        if pm:
            proc = pm.group(1)
        seen[port] = proc

    checks = []
    for port in sorted(seen):
        proc = seen[port]
        label = f"Port {port} lauscht" + (f" ({proc})" if proc else "")
        checks.append({"type": "port", "name": label, "port": port})
    return checks, sorted(seen)


def _web(ports: list[int]) -> list[dict]:
    """Fuer Web-Ports zusaetzlich eine HTTP-Pruefung vorschlagen."""
    out = []
    for port in ports:
        if port not in _WEB_PORTS:
            continue
        scheme = "https" if port == 443 else "http"
        host = "localhost" if port in (80, 443) else f"localhost:{port}"
        out.append({"type": "http", "name": f"Weboberflaeche auf {port} antwortet",
                    "url": f"{scheme}://{host}/", "expect_status": 200,
                    "required": False})
    return out


def _docker(host: Host, vmid: int, kind: str) -> list[dict]:
    """Laufen Container im Gast? Dann pruefen, dass alle wieder hochkommen."""
    r = _exec(host, vmid, kind, ["sh", "-c",
              "command -v docker >/dev/null 2>&1 && docker ps -q | wc -l || echo -"])
    val = r.out.strip()
    if not val.isdigit() or int(val) == 0:
        return []
    return [{"type": "command", "name": f"Alle {val} Docker-Container laufen",
             "run": "docker ps -q | wc -l", "expect_output": f"^{val}$"}]


def _databases(host: Host, vmid: int, kind: str) -> list[dict]:
    """Datenbanken erkennen und passende Vorlagen vorschlagen."""
    out = []
    r = _exec(host, vmid, kind, ["sh", "-c", "command -v psql >/dev/null 2>&1 && echo ja || echo -"])
    if r.out.strip() == "ja":
        out.append({"type": "postgres", "name": "PostgreSQL antwortet",
                    "query": "SELECT 1", "expect": "1"})
        # Vorlage fuer die Datenaktualitaet - Tabelle und Spalte kennt nur der
        # Betreiber, deshalb bewusst als auszufuellende Vorlage.
        out.append({"type": "db_fresh", "name": "Daten sind aktuell (ausfuellen)",
                    "engine": "postgres", "database": "postgres",
                    "query": "SELECT max(created_at) FROM meine_tabelle",
                    "max_age_hours": 48, "required": False})

    r = _exec(host, vmid, kind, ["sh", "-c",
              "(command -v mysql || command -v mariadb) >/dev/null 2>&1 && echo ja || echo -"])
    if r.out.strip() == "ja":
        out.append({"type": "mysql", "name": "MySQL/MariaDB antwortet", "query": "SELECT 1"})
    return out


def _system(host: Host, vmid: int, kind: str) -> list[dict]:
    """Grundpruefung: kein Dienst im Fehlerzustand."""
    return [{"type": "command", "name": "Kein Dienst im Fehlerzustand",
             "run": "systemctl is-system-running || true",
             "expect_output": "running|degraded"}]


def discover(host: Host, vmid: int, kind: str) -> dict:
    """Untersucht den Testgast und schlaegt Pruefungen vor."""
    port_checks, ports = _ports(host, vmid, kind)
    checks = (_system(host, vmid, kind)
              + _units(host, vmid, kind)
              + port_checks
              + _web(ports)
              + _docker(host, vmid, kind)
              + _databases(host, vmid, kind))

    return {"scratch_vmid": vmid, "kind": kind, "count": len(checks), "checks": checks,
            "ports": ports}
