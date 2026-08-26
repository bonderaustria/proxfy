"""Die Adresse, unter der der Browser Proxfy sieht.

Steht ein Reverse Proxy davor, kommt jede Anfrage mit der Herkunft des Proxys
an - also etwa https://verify.example.org statt http://192.168.1.35:8099. Der
Anmeldedienst weist eine unbekannte Herkunft ab; ohne diesen Eintrag scheitert
deshalb jede Anmeldung hinter einem Proxy mit "Invalid origin".

Der Anmeldedienst liest seine Werte aus auth.env und nur beim Start. Diese
Datei haelt die Datei mit der Einstellung im Gleichlauf und startet den Dienst
neu, wenn sich wirklich etwas geaendert hat.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

DIENST = "proxfy-auth"

# Erlaubt ist eine reine Herkunft: Schema, Rechnername, wahlweise Port. Kein
# Pfad - Better Auth vergleicht Herkuenfte, und ein Pfad passt dann nie.
_ERLAUBT = re.compile(r"^https?://[A-Za-z0-9._-]+(:\d{1,5})?$")


class AdressFehler(ValueError):
    pass


def pruefe(url: str) -> str:
    """Nimmt die Eingabe an oder wirft mit einem brauchbaren Grund."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        raise AdressFehler(
            f"'{url}' hat kein Schema. Erwartet wird https://name.example.org")
    if not _ERLAUBT.match(url):
        raise AdressFehler(
            f"'{url}' ist keine brauchbare Adresse. Erwartet wird Schema, Name "
            "und wahlweise Port, ohne Pfad - etwa https://verify.example.org")
    return url


def _lesen(pfad: pathlib.Path) -> dict[str, str]:
    werte: dict[str, str] = {}
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, _, wert = zeile.partition("=")
        werte[schluessel.strip()] = wert.strip()
    return werte


def _schreiben(pfad: pathlib.Path, werte: dict[str, str]) -> None:
    inhalt = "".join(f"{k}={v}\n" for k, v in werte.items())
    # Ueber eine Nebendatei, damit ein Abbruch mitten im Schreiben nicht die
    # Geheimnisse zerlegt und der Dienst danach gar nicht mehr startet.
    neben = pfad.with_suffix(pfad.suffix + ".neu")
    neben.write_text(inhalt, encoding="utf-8")
    neben.chmod(0o600)
    neben.replace(pfad)


def angleichen(env_datei: str, public_url: str, secure_cookies: bool = False,
               neustart: bool = True) -> bool:
    """Traegt die Adresse in auth.env ein. Gibt zurueck, ob sich etwas aenderte.

    Die Grundherkuenfte - die eigene Adresse im LAN, der Rechnername,
    localhost - bleiben immer erlaubt. Sonst waere Proxfy nach dem Eintragen
    einer Proxy-Adresse nur noch ueber den Proxy erreichbar, und ein Fehler in
    dessen Konfiguration wuerde endgueltig aussperren.
    """
    pfad = pathlib.Path(env_datei)
    if not pfad.is_file():
        raise AdressFehler(f"{env_datei} nicht gefunden.")

    werte = _lesen(pfad)
    alt = dict(werte)

    # Aeltere Installationen kennen die Grundherkuenfte noch nicht getrennt.
    # Was bisher erlaubt war, wird dann zur Grundmenge.
    if "PROXFY_BASE_ORIGINS" not in werte:
        werte["PROXFY_BASE_ORIGINS"] = werte.get("PROXFY_TRUSTED_ORIGINS", "")

    basis = [t for t in werte["PROXFY_BASE_ORIGINS"].split(",") if t.strip()]
    herkuenfte = list(dict.fromkeys([t.strip() for t in basis] +
                                    ([public_url] if public_url else [])))

    werte["PROXFY_TRUSTED_ORIGINS"] = ",".join(herkuenfte)
    # baseURL bestimmt, welche Adresse der Dienst in Verweise schreibt. Steht
    # ein Proxy davor, ist das dessen Adresse.
    if public_url:
        werte["BETTER_AUTH_URL"] = public_url
    elif basis:
        werte["BETTER_AUTH_URL"] = basis[0]
    # Bewusst NICHT aus dem Schema abgeleitet: mit 'Secure' gibt der Browser
    # das Cookie ueber http:// gar nicht mehr heraus, und der Zugang ueber die
    # eigene Adresse im Netz waere weg.
    werte["PROXFY_SECURE_COOKIES"] = "1" if secure_cookies else "0"

    if werte == alt:
        return False

    _schreiben(pfad, werte)
    if neustart:
        subprocess.run(["systemctl", "restart", DIENST],
                       check=False, capture_output=True, timeout=60)
    return True
