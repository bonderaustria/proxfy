"""Anbindung an den Anmeldedienst.

Der eigentliche Anmeldedienst (Better Auth) laeuft als eigener Prozess auf
127.0.0.1. Diese Anwendung ist die einzige Tuer nach aussen und uebernimmt drei
Aufgaben:

  1. /api/auth/* unveraendert durchreichen, samt Cookies in beide Richtungen
  2. bei JEDER anderen Anfrage die Sitzung dort nachschlagen
  3. Anmeldeversuche begrenzen und sperren

Punkt 3 gehoert bewusst hierher und nicht in den Anmeldedienst: hier kommt die
echte Herkunfts-IP an, und hier laesst sich ein Versuch abweisen, BEVOR er
ueberhaupt geprueft wird.

Sitzungen liegen serverseitig in der Datenbank des Anmeldedienstes. Der Browser
haelt nur eine HttpOnly-Cookie-Kennung - kein Token, kein JWT, nichts, was
JavaScript auslesen koennte. Eine Sitzung laesst sich damit jederzeit
serverseitig beenden.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import time
import urllib.error
import urllib.request

# --- Grenzwerte fuer Anmeldeversuche ------------------------------------------
FENSTER_SEKUNDEN = 15 * 60      # Beobachtungsfenster
VERZOEGERUNG_AB = 3             # ab dem wievielten Fehlversuch gebremst wird
SPERRE_AB = 10                  # ab wann gesperrt wird
SPERRE_SEKUNDEN = 15 * 60
MAX_VERZOEGERUNG = 8.0          # Sekunden


class AuthError(RuntimeError):
    pass


def load_env(path: str | pathlib.Path) -> dict:
    """Liest die Datei mit den Geheimnissen (KEY=VALUE je Zeile)."""
    out: dict[str, str] = {}
    p = pathlib.Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


@dataclasses.dataclass
class Identity:
    """Wer gerade angemeldet ist."""
    user_id: str
    email: str
    name: str
    two_factor: bool
    role: str = "user"


class AuthClient:
    """Sprachrohr zum Anmeldedienst."""

    def __init__(self, base_url: str, internal_secret: str, timeout: int = 15):
        self.base = base_url.rstrip("/")
        self.secret = internal_secret
        self.timeout = timeout

    # --- Rohzugriff ----------------------------------------------------------

    def _request(self, method: str, path: str, *, body: bytes | None = None,
                 headers: dict | None = None) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(self.base + path, data=body, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except urllib.error.URLError as e:
            raise AuthError(f"Anmeldedienst nicht erreichbar: {e.reason}") from e

    def _internal(self, method: str, path: str, payload: dict | None = None,
                  cookie: str | None = None) -> dict:
        headers = {"X-Internal-Secret": self.secret}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie is not None:
            headers["X-Forward-Cookie"] = cookie
        status, _, raw = self._request(method, path, body=data, headers=headers)
        try:
            out = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise AuthError(f"Unlesbare Antwort des Anmeldedienstes: {raw[:200]!r}")
        if status >= 400 and isinstance(out, dict) and out.get("error"):
            raise AuthError(out["error"])
        return out

    # --- Sitzungen -----------------------------------------------------------

    def identity(self, cookie_header: str) -> Identity | None:
        """Loest das mitgeschickte Cookie in eine Kennung auf."""
        if not cookie_header:
            return None
        data = self._internal("GET", "/internal/session", cookie=cookie_header)
        if not data.get("authenticated"):
            return None
        u = data["user"]
        return Identity(u["id"], u.get("email", ""), u.get("name", ""),
                        bool(u.get("twoFactorEnabled")), u.get("role", "user"))

    # --- Benutzerverwaltung --------------------------------------------------

    def needs_setup(self) -> bool:
        return bool(self._internal("GET", "/internal/bootstrap").get("needsSetup"))

    def list_users(self) -> list:
        return self._internal("GET", "/internal/users")

    def create_user(self, email: str, password: str, name: str = "",
                    role: str = "user") -> dict:
        return self._internal("POST", "/internal/users",
                              {"email": email, "password": password,
                               "name": name, "role": role})

    def delete_user(self, user_id: str) -> dict:
        return self._internal("POST", "/internal/users/delete", {"id": user_id})

    def logout_user(self, user_id: str) -> dict:
        return self._internal("POST", "/internal/users/logout", {"id": user_id})

    def set_role(self, user_id: str, role: str) -> dict:
        return self._internal("POST", "/internal/users/role",
                              {"id": user_id, "role": role})

    def verify_password(self, email: str, password: str) -> bool:
        """Prueft ein Passwort, ohne eine Sitzung zu hinterlassen."""
        r = self._internal("POST", "/internal/verify-password",
                           {"email": email, "password": password})
        return bool(r.get("ok"))

    def reset_two_factor(self, user_id: str) -> dict:
        return self._internal("POST", "/internal/users/reset-2fa", {"id": user_id})

    def find_user(self, user_id: str) -> dict | None:
        """Einzelner Benutzer - noetig, um vor einer Aktion seine Rolle zu kennen."""
        for u in self.list_users():
            if u.get("id") == user_id:
                return u
        return None

    # --- Durchreichen --------------------------------------------------------

    def proxy(self, method: str, path: str, body: bytes, headers: dict) -> tuple[int, list, bytes]:
        """Reicht eine /api/auth/-Anfrage weiter und gibt die Antwort zurueck.

        Weitergereicht wird nur, was der Anmeldedienst wirklich braucht -
        insbesondere das Cookie. Zurueck kommen alle Set-Cookie-Kopfzeilen
        unveraendert, sonst kaeme keine Sitzung zustande.
        """
        # Origin und Referer MUESSEN mit: Better Auth prueft sie gegen die
        # erlaubten Herkuenfte. Das ist der CSRF-Schutz - wer sie unterschlaegt,
        # bekommt "Missing or null Origin" und schaltet den Schutz nicht etwa
        # ab, sondern macht die Endpunkte unbenutzbar.
        fwd = {}
        for name in ("Content-Type", "Cookie", "Accept", "User-Agent",
                     "Origin", "Referer"):
            if headers.get(name):
                fwd[name] = headers[name]

        req = urllib.request.Request(self.base + path, data=body or None, method=method)
        for k, v in fwd.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.headers.get_all("Set-Cookie") or [], r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get_all("Set-Cookie") or [], e.read()
        except urllib.error.URLError as e:
            raise AuthError(f"Anmeldedienst nicht erreichbar: {e.reason}") from e


class LoginGuard:
    """Gestaffelte Bremse und Sperre fuer Anmeldeversuche.

    Gezaehlt wird getrennt nach Herkunfts-IP und nach Benutzerkennung, und es
    zaehlt der jeweils schlechtere Wert. Damit hilft es weder, die Kennung
    durchzuprobieren, noch, dieselbe Kennung von vielen Adressen anzugreifen.
    """

    def __init__(self, store, log=print, cfg=None):
        self.store, self.log = store, log
        # Voreinstellungen aus config.py, in der Oberflaeche aenderbar.
        self.verzoegerung_ab = VERZOEGERUNG_AB
        self.sperre_ab = SPERRE_AB
        self.sperre_sekunden = SPERRE_SEKUNDEN
        if cfg is not None:
            self.schwellen(cfg)

    def schwellen(self, cfg) -> None:
        """Uebernimmt geaenderte Schwellen ohne Neustart."""
        self.verzoegerung_ab = max(1, int(getattr(cfg, "delay_from", VERZOEGERUNG_AB)))
        self.sperre_ab = max(self.verzoegerung_ab + 1,
                             int(getattr(cfg, "lock_from", SPERRE_AB)))
        self.sperre_sekunden = max(60, int(getattr(cfg, "lock_minutes", 15)) * 60)

    @staticmethod
    def identifier_from(body: bytes) -> str:
        """Zieht die E-Mail aus dem Anmelderumpf - nur zum Zaehlen."""
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return ""
        val = data.get("email") or data.get("username") or ""
        return str(val).strip().lower()[:200]

    def check(self, ip: str, identifier: str) -> tuple[bool, float, str]:
        """Darf dieser Versuch stattfinden?

        Rueckgabe: (erlaubt, Verzoegerung in Sekunden, Begruendung)
        """
        n_ip = self.store.count_failures(ip=ip, seconds=FENSTER_SEKUNDEN)
        n_user = self.store.count_failures(identifier=identifier,
                                           seconds=FENSTER_SEKUNDEN) if identifier else 0
        n = max(n_ip, n_user)

        if n >= self.sperre_ab:
            rest = self.store.lock_remaining(ip, identifier, self.sperre_sekunden)
            minuten = max(1, int(rest // 60) + (1 if rest % 60 else 0))
            return False, 0.0, (
                f"Zu viele Fehlversuche. Gesperrt fuer noch etwa {minuten} Minuten.")

        delay = 0.0
        if n >= self.verzoegerung_ab:
            delay = min(MAX_VERZOEGERUNG, 2.0 ** (n - self.verzoegerung_ab + 1))
        return True, delay, ""

    def record(self, ip: str, identifier: str, ok: bool) -> None:
        self.store.add_login_attempt(ip, identifier, ok)
        if ok:
            self.store.clear_failures(ip, identifier)
        else:
            self.log(f"[Anmeldung] Fehlversuch von {ip} fuer '{identifier or 'unbekannt'}'")


_SAFE_AUTH_PATH = re.compile(r"^/api/auth/[A-Za-z0-9/_\-\.]*$")


def is_auth_path(path: str) -> bool:
    """Nur wohlgeformte Pfade werden durchgereicht - keine Traversierung."""
    return bool(_SAFE_AUTH_PATH.match(path)) and ".." not in path

# --- Rollen und Rechte --------------------------------------------------------
# Die Oberflaeche blendet aus, was jemand nicht darf. Das ist Bequemlichkeit,
# kein Schutz - mit einem curl waere es umgangen. Verbindlich ist ausschliesslich
# diese Pruefung hier.

ROLLEN = ("user", "admin", "super")


def rang(rolle: str) -> int:
    """Ordnet die Rollen. Unbekanntes gilt als niedrigste Stufe."""
    try:
        return ROLLEN.index(rolle)
    except ValueError:
        return 0


class Verboten(RuntimeError):
    """Der Aufrufer darf das nicht. Fuehrt zu HTTP 403."""


def mindestens(ident, rolle: str) -> None:
    """Wirft, wenn die Kennung nicht mindestens diese Rolle hat."""
    if ident is None:
        raise Verboten("Nicht angemeldet.")
    if rang(ident.role) < rang(rolle):
        wort = {"admin": "Admins", "super": "Super Admins"}.get(rolle, "Berechtigte")
        raise Verboten(f"Das duerfen nur {wort}.")


def darf_verwalten(ident, ziel_rolle: str) -> None:
    """Darf diese Kennung einen Benutzer mit dieser Rolle verwalten?

    Super Admins duerfen jeden verwalten. Admins ausschliesslich einfache
    Benutzer - nicht andere Admins und erst recht keine Super Admins. Sonst
    koennte ein Admin sich durch Zuruecksetzen fremder Zwei-Faktoren nach oben
    arbeiten.
    """
    mindestens(ident, "admin")
    if ident.role == "super":
        return
    if rang(ziel_rolle) >= rang("admin"):
        raise Verboten(
            "Admins duerfen nur einfache Benutzer verwalten, keine Admins oder Super Admins.")
