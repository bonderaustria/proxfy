"""Konfiguration und Sicherheits-Invarianten."""
from __future__ import annotations

import dataclasses
import pathlib
import yaml

# --- Harte Sicherheitsgrenzen -------------------------------------------------
# Wiederhergestellte Test-Gaeste leben AUSSCHLIESSLICH in diesem VMID-Bereich.
# Jede zerstoerende Operation prueft das erneut. Ausserhalb: sofortiger Abbruch.
SCRATCH_VMID_MIN = 9000
SCRATCH_VMID_MAX = 9099

# Marker, an dem der Aufraeumdienst Testgaeste erkennt.
SCRATCH_TAG = "proxfy-test"
SCRATCH_DESC_PREFIX = "PROXFY RESTORE TEST"

# Aeltere Markierungen aus der Zeit vor der Umbenennung. Werden nur noch
# ERKANNT, nicht mehr geschrieben - sonst blieben Testgaeste aus einer
# frueheren Fassung unauffindbar liegen.
LEGACY_MARKERS = ("pverv-test", "PVERV RESTORE TEST")


def is_scratch_marked(config_text: str) -> bool:
    """Traegt dieser Gast eine Markierung dieses Werkzeugs?"""
    return (SCRATCH_TAG in config_text
            or SCRATCH_DESC_PREFIX in config_text
            or any(m in config_text for m in LEGACY_MARKERS))


class SafetyError(RuntimeError):
    """Verletzung einer Sicherheits-Invariante. Niemals abfangen und weitermachen."""


def assert_scratch_vmid(vmid: int) -> int:
    """Torwaechter vor jeder zerstoerenden Aktion."""
    vmid = int(vmid)
    if not (SCRATCH_VMID_MIN <= vmid <= SCRATCH_VMID_MAX):
        raise SafetyError(
            f"VMID {vmid} liegt ausserhalb des Scratch-Bereichs "
            f"{SCRATCH_VMID_MIN}-{SCRATCH_VMID_MAX}. Operation abgebrochen."
        )
    return vmid


@dataclasses.dataclass
class HostConfig:
    host: str
    user: str = "root"
    key_file: str | None = None
    port: int = 22


@dataclasses.dataclass
class RestoreConfig:
    backup_storage: str = "PBS"      # wo die Backups liegen
    target_storage: str = "local-lvm"  # wohin der Testgast wiederhergestellt wird
    isolated_bridge: str = "vmbr9"
    lan_bridge: str = "vmbr0"
    boot_timeout: int = 300
    agent_timeout: int = 240


@dataclasses.dataclass
class AuthConfig:
    """Anbindung an den Anmeldedienst, der als eigener Prozess laeuft."""
    env_file: str = "/opt/proxfy/auth.env"
    port: int = 8100


@dataclasses.dataclass
class Config:
    host: HostConfig
    restore: RestoreConfig
    targets: list[dict]
    auth: AuthConfig = dataclasses.field(default_factory=AuthConfig)
    # Nur einschalten, wenn wirklich ein Reverse Proxy davorsteht. Sonst koennte
    # sich jeder Aufrufer per Kopfzeile eine fremde Herkunftsadresse ausdenken
    # und damit die Anmeldesperre umgehen.
    trust_forwarded_for: bool = False

    # Adresse, unter der der Browser Proxfy sieht, wenn ein Reverse Proxy
    # davorsteht. Leer bedeutet: direkter Zugriff, es gilt die eigene Adresse.
    # Ohne diesen Eintrag weist der Anmeldedienst jede Anfrage vom Proxy als
    # fremde Herkunft ab.
    public_url: str = ""
    # Sitzungscookie nur ueber HTTPS herausgeben. Sperrt zugleich den Zugang
    # ueber http:// im eigenen Netz aus - deshalb eine bewusste Entscheidung
    # und keine Folge der Aussenadresse.
    secure_cookies: bool = False

    # Voreinstellungen fuer neue Laeufe, in der Oberflaeche aenderbar.
    default_keep: str = "destroy"
    default_ttl: int = 60

    # Schwellen der Anmeldesperre.
    delay_from: int = 3
    lock_from: int = 10
    lock_minutes: int = 15

    def anwenden(self, ueberlagerung: dict) -> None:
        """Legt die Werte aus der Datenbank ueber die aus der Datei.

        Bewusst in dieser Richtung: die Datei bleibt die funktionierende
        Grundeinstellung. Wird die Ueberlagerung geleert, ist der Ausgangszustand
        wieder da - das ist der Rettungsweg, wenn in der Oberflaeche etwas
        verstellt wurde.
        """
        for pfad, wert in (ueberlagerung or {}).items():
            teil, _, feld = pfad.partition(".")
            if not feld:
                if hasattr(self, teil):
                    setattr(self, teil, wert)
                continue
            ziel = getattr(self, teil, None)
            if ziel is not None and hasattr(ziel, feld):
                setattr(ziel, feld, wert)

    def als_dict(self) -> dict:
        """Flache Darstellung fuer die Oberflaeche."""
        return {
            "host.host": self.host.host,
            "host.user": self.host.user,
            "host.key_file": self.host.key_file,
            "restore.backup_storage": self.restore.backup_storage,
            "restore.target_storage": self.restore.target_storage,
            "restore.isolated_bridge": self.restore.isolated_bridge,
            "restore.lan_bridge": self.restore.lan_bridge,
            "restore.boot_timeout": self.restore.boot_timeout,
            "restore.agent_timeout": self.restore.agent_timeout,
            "default_keep": self.default_keep,
            "default_ttl": self.default_ttl,
            "public_url": self.public_url,
            "secure_cookies": self.secure_cookies,
            "trust_forwarded_for": self.trust_forwarded_for,
            "delay_from": self.delay_from,
            "lock_from": self.lock_from,
            "lock_minutes": self.lock_minutes,
        }

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "Config":
        raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
        return cls(
            host=HostConfig(**raw["host"]),
            restore=RestoreConfig(**raw.get("restore", {})),
            targets=raw.get("targets", []),
            auth=AuthConfig(**raw.get("auth", {})),
            trust_forwarded_for=bool(raw.get("trust_forwarded_for", False)),
            public_url=str(raw.get("public_url", "") or ""),
            secure_cookies=bool(raw.get("secure_cookies", False)),
        )
