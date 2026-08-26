"""IP-Vergabe im Gast fuer den Modus 'routed'.

Das Problem: eine wiederhergestellte VM traegt die Netzwerkkonfiguration des
Originals. Wuerde man sie direkt an die LAN-Bridge haengen, stuende sie mit der
IP der noch laufenden Produktions-VM im Netz - genau der Schaden, den dieses
Werkzeug verhindern soll.

Die Loesung ist zweistufig:

  1. Gast startet an der ISOLIERTEN Bridge. Er kann nichts erreichen.
  2. Innen wird die Adresse auf die Wunsch-IP umgeschrieben.
  3. Erst danach wird die Netzwerkkarte auf die LAN-Bridge umgehaengt.

Die Umkonfiguration ist bewusst fluechtig (ip addr statt netplan/NetworkManager):
der Gast wird nach dem Test ohnehin vernichtet, und ein fluechtiger Eingriff
funktioniert quer ueber alle Distributionen ohne Erkennungslogik.
"""
from __future__ import annotations

import ipaddress
import re

from . import pve
from .ssh import Host


class GuestNetError(RuntimeError):
    pass


def _primary_iface(host: Host, vmid: int, kind: str) -> str:
    """Findet die Netzwerkkarte des Gastes - ohne Annahme, dass sie eth0 heisst."""
    r = pve.guest_exec(host, vmid, kind, ["sh", "-c",
        "ls /sys/class/net | grep -v -E '^(lo|docker|veth|br-|virbr|tailscale|wg)' | head -1"], timeout=30)
    name = r.out.strip().splitlines()[0].strip() if r.out.strip() else ""
    if not name:
        raise GuestNetError("Im Gast wurde keine Netzwerkkarte gefunden")
    return name


def assign_ip_linux(host: Host, vmid: int, kind: str, ip_cidr: str, gateway: str | None) -> str:
    """Setzt die Wunsch-IP im laufenden Linux-Gast. Gibt eine Kurzbeschreibung zurueck."""
    iface = ipaddress.ip_interface(ip_cidr)
    dev = _primary_iface(host, vmid, kind)

    script = "; ".join(filter(None, [
        # Alles abschalten, was die Adresse spaeter zurueckdrehen koennte.
        "systemctl stop NetworkManager 2>/dev/null || true",
        "systemctl stop systemd-networkd 2>/dev/null || true",
        "pkill dhclient 2>/dev/null || true",
        f"ip addr flush dev {dev}",
        f"ip link set {dev} up",
        f"ip addr add {ip_cidr} dev {dev}",
        f"ip route replace default via {gateway} dev {dev}" if gateway else "",
    ]))
    r = pve.guest_exec(host, vmid, kind, ["sh", "-c", script], timeout=60)
    if not r.ok:
        raise GuestNetError(f"IP-Vergabe im Gast fehlgeschlagen: {(r.err or r.out).strip()[:400]}")

    verify = pve.guest_exec(host, vmid, kind, ["ip", "-4", "-o", "addr", "show", "dev", dev], timeout=30)
    if str(iface.ip) not in verify.out:
        raise GuestNetError(
            f"Nach der Vergabe traegt {dev} die Adresse nicht:\n{verify.out.strip()[:400]}"
        )
    return f"{dev} = {ip_cidr}" + (f", Gateway {gateway}" if gateway else "")


def assign_ip_windows(host: Host, vmid: int, ip_cidr: str, gateway: str | None) -> str:
    """Gegenstueck fuer Windows-Gaeste via netsh."""
    iface = ipaddress.ip_interface(ip_cidr)
    mask = str(iface.network.netmask)

    r = pve.guest_exec(host, vmid, "vm", ["powershell", "-NoProfile", "-Command",
        "(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -First 1).Name"], timeout=60)
    name = r.out.strip().splitlines()[-1].strip() if r.out.strip() else ""
    if not name:
        raise GuestNetError("Im Windows-Gast wurde keine aktive Netzwerkkarte gefunden")

    cmd = f'netsh interface ip set address name="{name}" static {iface.ip} {mask}'
    if gateway:
        cmd += f" {gateway} 1"
    r = pve.guest_exec(host, vmid, "vm", ["cmd", "/c", cmd], timeout=60)
    if not r.ok:
        raise GuestNetError(f"netsh fehlgeschlagen: {(r.err or r.out).strip()[:400]}")
    return f"{name} = {ip_cidr}" + (f", Gateway {gateway}" if gateway else "")


def is_windows(host: Host, vmid: int) -> bool:
    cfg = host.run("qm", "config", str(vmid))
    return bool(re.search(r"^ostype:\s*win", cfg.out, re.M))


def switch_to_bridge(host: Host, vmid: int, kind: str, bridge: str, mac: str) -> None:
    """Haengt die Netzwerkkarte auf die Ziel-Bridge um - der Schritt ins echte Netz."""
    r = pve.apply_network(host, vmid, kind, bridge, mac)
    r.check(f"Netzwerkkarte auf {bridge} umhaengen")
