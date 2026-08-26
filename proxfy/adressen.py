"""Test-Adressen: einzelne Adressen und Bereiche.

Ein Eintrag ist entweder eine einzelne Adresse oder ein Bereich. Bereiche sind
der Grund, warum sich im Modus 'routed' mehrere Gaeste gleichzeitig pruefen
lassen - mit einer festen Adresse ging immer nur einer.

Erlaubte Schreibweisen:
    192.168.20.240              einzeln, Praefix aus der Vorgabe
    192.168.20.240/24           einzeln mit Praefix
    192.168.20.15-38            Bereich, Kurzform fuer das letzte Oktett
    192.168.20.15-192.168.20.38 Bereich, vollstaendig
    beides zusaetzlich mit /24 am Ende
"""
from __future__ import annotations

import dataclasses
import ipaddress
import re

MAX_BEREICH = 256   # Groessere Bereiche sind fast immer ein Tippfehler.


class AdressFehler(ValueError):
    pass


@dataclasses.dataclass
class Eintrag:
    """Ein Eintrag aus dem Vorrat, bereits zerlegt."""
    von: ipaddress.IPv4Address
    bis: ipaddress.IPv4Address
    praefix: int

    @property
    def ist_bereich(self) -> bool:
        return self.von != self.bis

    @property
    def anzahl(self) -> int:
        return int(self.bis) - int(self.von) + 1

    def adressen(self) -> list[str]:
        """Alle Adressen des Eintrags, jeweils mit Praefix."""
        return [f"{ipaddress.IPv4Address(n)}/{self.praefix}"
                for n in range(int(self.von), int(self.bis) + 1)]

    def anzeige(self) -> str:
        if not self.ist_bereich:
            return f"{self.von}/{self.praefix}"
        # Kurzform, wenn sich nur das letzte Oktett unterscheidet.
        v, b = str(self.von), str(self.bis)
        if v.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]:
            return f"{v}-{b.rsplit('.', 1)[1]}/{self.praefix}"
        return f"{v}-{b}/{self.praefix}"


_MIT_PRAEFIX = re.compile(r"^(?P<rest>.+?)/(?P<praefix>\d{1,2})$")


def zerlege(text: str, praefix_vorgabe: int = 24) -> Eintrag:
    """Zerlegt eine Eingabe in einen Eintrag. Wirft bei Unsinn."""
    text = (text or "").strip().replace(" ", "")
    if not text:
        raise AdressFehler("Keine Adresse angegeben.")

    praefix = praefix_vorgabe
    m = _MIT_PRAEFIX.match(text)
    if m:
        text = m["rest"]
        praefix = int(m["praefix"])
    if not 8 <= praefix <= 32:
        raise AdressFehler(f"Praefix /{praefix} ist unbrauchbar.")

    if "-" not in text:
        try:
            adr = ipaddress.IPv4Address(text)
        except ValueError:
            raise AdressFehler(f"'{text}' ist keine gueltige IPv4-Adresse.") from None
        return Eintrag(adr, adr, praefix)

    links, _, rechts = text.partition("-")
    try:
        von = ipaddress.IPv4Address(links)
    except ValueError:
        raise AdressFehler(f"'{links}' ist keine gueltige IPv4-Adresse.") from None

    if "." in rechts:
        try:
            bis = ipaddress.IPv4Address(rechts)
        except ValueError:
            raise AdressFehler(f"'{rechts}' ist keine gueltige IPv4-Adresse.") from None
    else:
        # Kurzform: nur das letzte Oktett.
        if not rechts.isdigit() or not 0 <= int(rechts) <= 255:
            raise AdressFehler(f"'{rechts}' ist kein gueltiges letztes Oktett.")
        bis = ipaddress.IPv4Address(".".join(str(von).split(".")[:3] + [rechts]))

    if int(bis) < int(von):
        raise AdressFehler("Das Ende des Bereichs liegt vor dem Anfang.")
    if int(bis) - int(von) + 1 > MAX_BEREICH:
        raise AdressFehler(
            f"Der Bereich umfasst {int(bis) - int(von) + 1} Adressen. "
            f"Mehr als {MAX_BEREICH} sind fast immer ein Tippfehler.")
    return Eintrag(von, bis, praefix)


def naechste_freie(eintrag: Eintrag, belegt: set[str], pruefer=None) -> str:
    """Waehlt die naechste freie Adresse aus einem Eintrag.

    'belegt' sind Adressen laufender Testgaeste - die sieht der Preflight nicht
    zuverlaessig, weil ein Gast auch mal nicht antwortet. 'pruefer' ist eine
    Funktion, die eine Adresse zusaetzlich im Netz prueft und bei Belegung wirft.
    """
    fehler = []
    for kandidat in eintrag.adressen():
        if kandidat.split("/")[0] in belegt:
            continue
        if pruefer is None:
            return kandidat
        try:
            pruefer(kandidat)
            return kandidat
        except Exception as e:
            fehler.append(f"{kandidat.split('/')[0]}: {str(e).splitlines()[-1][:80]}")

    if not fehler:
        raise AdressFehler(
            f"Alle {eintrag.anzahl} Adressen aus {eintrag.anzeige()} sind an laufende "
            "Testgaeste vergeben.")
    raise AdressFehler(
        f"Keine freie Adresse in {eintrag.anzeige()} gefunden. Zuletzt geprueft:\n  "
        + "\n  ".join(fehler[-4:]))
