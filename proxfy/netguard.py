"""Netzwerk-Sicherung.

Hier entscheidet sich, ob das Werkzeug harmlos ist oder einen Produktionsausfall
verursacht. Zwei Modi:

  isolated : Bridge ohne Uplink. Der Gast kann physisch nichts erreichen.
  routed   : Echte Bridge, vorgegebene IP. Nur nach bestandenem Preflight.

In beiden Faellen bekommt der Gast IMMER eine neue MAC, damit DHCP-Reservierungen
und Switch-Port-Bindungen des Originals nicht greifen.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import random
import re

from .ssh import Host


class PreflightError(RuntimeError):
    """IP-Preflight nicht bestanden. Job wird abgebrochen, nicht geraten."""


def random_mac() -> str:
    """Lokal administrierte Unicast-MAC (zweites Bit gesetzt, erstes geloescht)."""
    first = (random.getrandbits(8) & 0xFE) | 0x02
    rest = [random.getrandbits(8) for _ in range(5)]
    return ":".join(f"{b:02X}" for b in [first, *rest])


# Anschluesse, die PVE selbst fuer Gaeste anlegt. Sie fuehren nirgendwohin:
#   tap<vmid>i<n>   Netzwerkkarte einer VM
#   veth<vmid>i<n>  Netzwerkkarte eines Containers
#   fwpr / fwln     Veth-Paare der PVE-Firewall, entstehen bei firewall=1
_GUEST_PORT_RE = re.compile(r"^(tap|veth|fwpr|fwln)\d+")


def ensure_isolated_bridge(host: Host, name: str) -> None:
    """Legt eine Bridge ohne Uplink an - zur Laufzeit, ohne /etc/network anzufassen.

    Bewusst nicht persistent: eine Bridge, die nach einem Reboot verschwindet,
    ist ungefaehrlicher als ein Eintrag, den spaeter jemand mit einem Uplink
    versieht.

    Die Gegenprobe darf NICHT auf "gar keine Ports" pruefen: sobald ein Testgast
    daran haengt - und mit der Richtlinie 'manual' bleibt er das - traegt die
    Bridge dessen Anschluss. Entscheidend ist allein, dass kein Port nach
    draussen fuehrt.
    """
    if not host.run("ip", "-o", "link", "show", name).ok:
        host.sh(f"ip link add name {name} type bridge").check(f"Bridge {name} anlegen")
    host.sh(f"ip link set {name} up").check(f"Bridge {name} aktivieren")

    # Eine Adresse auf der Bridge deutet darauf hin, dass jemand sie umgewidmet
    # hat - etwa als geroutetes Netz mit NAT. Dann ist sie nicht mehr isoliert.
    addrs = host.sh(f"ip -4 -o addr show dev {name} 2>/dev/null").out.strip()
    if addrs:
        raise PreflightError(
            f"Bridge {name} traegt eine IP-Adresse. Eine isolierte Bridge darf keine "
            "haben, sonst besteht ein Weg zum Host.")

    ports = host.sh(f"ls /sys/class/net/{name}/brif 2>/dev/null").out.split()
    uplinks = [p for p in ports if not _GUEST_PORT_RE.match(p)]
    if uplinks:
        raise PreflightError(
            f"Bridge {name} hat Anschluesse nach draussen: {uplinks}. Sie ist damit NICHT "
            "isoliert. Bitte eine andere Bridge als isolated_bridge eintragen oder diese "
            "Anschluesse entfernen."
        )


@dataclasses.dataclass
class IpPlan:
    """Ergebnis der Netzwerkplanung fuer einen Testlauf."""
    mode: str                 # "isolated" | "routed"
    bridge: str
    mac: str
    ip_cidr: str | None = None
    gateway: str | None = None

    @property
    def is_routed(self) -> bool:
        return self.mode == "routed"


def preflight_ip(host: Host, ip_cidr: str, bridge: str, strict: bool = True) -> None:
    """Prueft, ob die Ziel-IP wirklich frei ist.

    Drei unabhaengige Proben. Jeder Treffer bricht ab - wir raten hier nicht,
    weil ein IP-Konflikt genau der Schaden ist, den dieses Werkzeug vermeiden soll.
    """
    iface = ipaddress.ip_interface(ip_cidr)
    ip = str(iface.ip)
    findings: list[str] = []

    # 1) ARP-Duplikatspruefung - der verlaesslichste Test im lokalen Segment.
    arping = host.sh(f"command -v arping >/dev/null 2>&1 && echo yes || echo no").out.strip()
    if arping == "yes":
        r = host.sh(f"arping -f -c 3 -w 4 -I {bridge} {ip} 2>&1 || true")
        if re.search(r"Unicast reply from|reply from", r.out, re.I):
            findings.append(f"ARP-Antwort von {ip} erhalten - die Adresse ist belegt")
    elif strict:
        findings.append(
            "arping ist auf dem Host nicht installiert - die zuverlaessigste "
            "Pruefung fehlt. Installieren mit: apt install iputils-arping"
        )

    # 2) ICMP - schwaecher (Firewalls schlucken es), aber ein Treffer ist eindeutig.
    r = host.sh(f"ping -c 2 -W 2 {ip} >/dev/null 2>&1 && echo alive || echo silent")
    if r.out.strip() == "alive":
        findings.append(f"{ip} antwortet auf Ping - die Adresse ist belegt")

    # 3) Nachbarschaftstabelle des Hosts.
    r = host.sh(f"ip neigh show {ip} 2>/dev/null")
    if re.search(r"lladdr\s+[0-9a-f:]{17}", r.out, re.I) and "FAILED" not in r.out.upper():
        findings.append(f"{ip} steht mit MAC in der ARP-Tabelle des Hosts")

    # 4) Belegt die IP bereits ein konfigurierter Gast auf diesem Host?
    r = host.sh(
        f"grep -rlE '(^|[^0-9]){re.escape(ip)}([^0-9]|$)' /etc/pve/qemu-server /etc/pve/lxc 2>/dev/null || true"
    )
    if r.out.strip():
        who = ", ".join(sorted(r.out.split()))
        findings.append(f"{ip} ist in Gast-Konfigurationen hinterlegt: {who}")

    if findings:
        raise PreflightError(
            "IP-Preflight fehlgeschlagen fuer " + ip_cidr + ":\n  - " + "\n  - ".join(findings)
        )


def plan_network(
    host: Host,
    mode: str,
    isolated_bridge: str,
    lan_bridge: str,
    ip_cidr: str | None = None,
    gateway: str | None = None,
    skip_preflight: bool = False,
) -> IpPlan:
    """Baut den Netzwerkplan und erzwingt die zum Modus gehoerenden Pruefungen."""
    mac = random_mac()

    if mode == "isolated":
        ensure_isolated_bridge(host, isolated_bridge)
        return IpPlan(mode="isolated", bridge=isolated_bridge, mac=mac)

    if mode == "routed":
        if not ip_cidr:
            raise PreflightError("Modus 'routed' verlangt eine IP in CIDR-Notation, z.B. 192.168.1.240/24")
        if not skip_preflight:
            preflight_ip(host, ip_cidr, lan_bridge)
        return IpPlan(mode="routed", bridge=lan_bridge, mac=mac, ip_cidr=ip_cidr, gateway=gateway)

    raise ValueError(f"Unbekannter Netzwerkmodus: {mode}")
