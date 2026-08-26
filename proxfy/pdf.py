"""Ein kleiner PDF-Schreiber - gerade genug fuer einen Bericht.

Warum selbst geschrieben: Proxfy kommt ohne Fremdbibliotheken aus, und eine
PDF-Bibliothek waere fuer ein paar Seiten Text mit einem Logo ein schweres
Geschuetz. PDF ist im Kern ein Textformat mit einer Verweistabelle am Ende;
was hier gebraucht wird, sind Absaetze, Linien, eine Tabelle und ein Bild.

Absichtlich nicht dabei: Zeilenumbruch im Blocksatz, eingebettete Schriften,
Farbverlaeufe. Die vierzehn Grundschriften stehen in jedem Betrachter zur
Verfuegung und muessen nicht mitgeliefert werden; WinAnsi deckt Umlaute ab.

Masse in Punkt (1/72 Zoll). A4 ist 595 x 842.
"""
from __future__ import annotations

import dataclasses

A4 = (595.28, 841.89)
RAND = 48.0

# Zeichenbreiten der Grundschriften waeren eine eigene Tabelle. Fuer den
# Umbruch reicht eine Schaetzung: Helvetica laeuft bei etwa 0,5 em je Zeichen.
_BREITE_JE_ZEICHEN = 0.52


def _text_escape(s: str) -> str:
    return (s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))


def _winansi(s: str) -> bytes:
    """PDF-Grundschriften rechnen in WinAnsi. Was dort fehlt, wird ersetzt."""
    ersatz = {"–": "-", "—": "-", "‘": "'", "’": "'",
              "“": '"', "”": '"', "„": '"', "…": "...",
              " ": " ", "→": "->", "·": "-"}
    for a, b in ersatz.items():
        s = s.replace(a, b)
    return s.encode("cp1252", "replace")


@dataclasses.dataclass
class Bild:
    daten: bytes
    breite: int
    hoehe: int


class Seite:
    def __init__(self, breite: float, hoehe: float):
        self.breite, self.hoehe = breite, hoehe
        self.teile: list[bytes] = []

    def text(self, x: float, y: float, s: str, groesse: float = 10,
             fett: bool = False, grau: float = 0.0) -> None:
        self.teile.append(
            b"BT /" + (b"F2" if fett else b"F1") + b" " + f"{groesse:.2f}".encode()
            + b" Tf " + f"{grau:.3f} {grau:.3f} {grau:.3f}".encode() + b" rg "
            + f"{x:.2f} {y:.2f}".encode() + b" Td ("
            + _winansi(_text_escape(s)) + b") Tj ET\n")

    def text_rechts(self, rechts: float, y: float, s: str, groesse: float = 10,
                    fett: bool = False, grau: float = 0.0) -> None:
        """Setzt den Text so, dass er rechts an 'rechts' endet.

        Die Breite wird geschaetzt - genau waere sie nur mit der Breitentabelle
        der Schrift. Fuer eine rechte Spalte reicht das; im Zweifel steht das
        Wort ein paar Punkt weiter links, statt aus der Seite zu laufen.
        """
        breite = len(s) * groesse * _BREITE_JE_ZEICHEN * (1.06 if fett else 1.0)
        self.text(rechts - breite, y, s, groesse, fett, grau)

    def linie(self, x1: float, y1: float, x2: float, y2: float,
              staerke: float = 0.5, grau: float = 0.75) -> None:
        self.teile.append(
            f"{grau:.3f} {grau:.3f} {grau:.3f} RG {staerke:.2f} w ".encode()
            + f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S\n".encode())

    def kasten(self, x: float, y: float, b: float, h: float,
               grau: float = 0.94) -> None:
        self.teile.append(
            f"{grau:.3f} {grau:.3f} {grau:.3f} rg ".encode()
            + f"{x:.2f} {y:.2f} {b:.2f} {h:.2f} re f\n".encode())

    def bild(self, name: str, x: float, y: float, b: float, h: float) -> None:
        self.teile.append(
            b"q " + f"{b:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm".encode()
            + b" /" + name.encode() + b" Do Q\n")

    def inhalt(self) -> bytes:
        return b"".join(self.teile)


class Dokument:
    """Sammelt Seiten und schreibt am Ende die Datei."""

    def __init__(self, titel: str = "", autor: str = "Proxfy"):
        self.seiten: list[Seite] = []
        self.bilder: dict[str, Bild] = {}
        self.titel, self.autor = titel, autor

    def neue_seite(self) -> Seite:
        s = Seite(*A4)
        self.seiten.append(s)
        return s

    def bild_aufnehmen(self, name: str, jpeg: bytes, breite: int, hoehe: int) -> None:
        self.bilder[name] = Bild(jpeg, breite, hoehe)

    @staticmethod
    def umbrechen(text: str, breite_pt: float, groesse: float) -> list[str]:
        """Grober Zeilenumbruch an Wortgrenzen."""
        je_zeile = max(8, int(breite_pt / (groesse * _BREITE_JE_ZEICHEN)))
        zeilen, aktuell = [], ""
        for wort in str(text).split():
            probe = (aktuell + " " + wort).strip()
            if len(probe) <= je_zeile:
                aktuell = probe
            else:
                if aktuell:
                    zeilen.append(aktuell)
                # Ein einzelnes Wort, das laenger ist als die Zeile, wird hart
                # getrennt - sonst liefe es aus der Seite heraus.
                while len(wort) > je_zeile:
                    zeilen.append(wort[:je_zeile])
                    wort = wort[je_zeile:]
                aktuell = wort
        if aktuell:
            zeilen.append(aktuell)
        return zeilen or [""]

    def bytes(self) -> bytes:
        objekte: list[bytes] = []      # 1-basiert, Index 0 bleibt leer

        def neu(inhalt: bytes) -> int:
            objekte.append(inhalt)
            return len(objekte)

        katalog = neu(b"")             # 1, spaeter gefuellt
        seitenbaum = neu(b"")          # 2
        f1 = neu(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                 b"/Encoding /WinAnsiEncoding >>")
        f2 = neu(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                 b"/Encoding /WinAnsiEncoding >>")

        bild_nr: dict[str, int] = {}
        for name, b in self.bilder.items():
            kopf = (f"<< /Type /XObject /Subtype /Image /Width {b.breite} "
                    f"/Height {b.hoehe} /ColorSpace /DeviceRGB /BitsPerComponent 8 "
                    f"/Filter /DCTDecode /Length {len(b.daten)} >>\nstream\n").encode()
            bild_nr[name] = neu(kopf + b.daten + b"\nendstream")

        seiten_nr: list[int] = []
        for s in self.seiten:
            inhalt = s.inhalt()
            strom = neu(f"<< /Length {len(inhalt)} >>\nstream\n".encode()
                        + inhalt + b"\nendstream")
            bilder = " ".join(f"/{n} {bild_nr[n]} 0 R" for n in self.bilder)
            seiten_nr.append(neu(
                (f"<< /Type /Page /Parent {seitenbaum} 0 R "
                 f"/MediaBox [0 0 {s.breite:.2f} {s.hoehe:.2f}] "
                 f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> "
                 f"/XObject << {bilder} >> >> "
                 f"/Contents {strom} 0 R >>").encode()))

        kinder = " ".join(f"{n} 0 R" for n in seiten_nr)
        objekte[seitenbaum - 1] = (
            f"<< /Type /Pages /Count {len(seiten_nr)} /Kids [{kinder}] >>").encode()

        info = neu(b"<< /Title (" + _winansi(_text_escape(self.titel))
                   + b") /Producer (" + _winansi(_text_escape(self.autor)) + b") >>")
        objekte[katalog - 1] = (
            f"<< /Type /Catalog /Pages {seitenbaum} 0 R >>").encode()

        # --- Zusammensetzen ---
        raus = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        stellen = [0] * (len(objekte) + 1)
        for i, inhalt in enumerate(objekte, start=1):
            stellen[i] = len(raus)
            raus += f"{i} 0 obj\n".encode() + inhalt + b"\nendobj\n"

        xref = len(raus)
        raus += f"xref\n0 {len(objekte) + 1}\n".encode()
        raus += b"0000000000 65535 f \n"
        for i in range(1, len(objekte) + 1):
            raus += f"{stellen[i]:010d} 00000 n \n".encode()
        raus += (f"trailer\n<< /Size {len(objekte) + 1} /Root {katalog} 0 R "
                 f"/Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
        return bytes(raus)
