"""Weboberflaeche: JSON-API plus Server-Sent-Events.

Bewusst ohne Framework - PVE-Hosts bringen ein 'externally managed' Python mit,
jede pip-Abhaengigkeit bedeutet venv-Gefrickel bei der Installation.

WICHTIG: Es laeuft nichts von selbst. Ein Verifikationslauf entsteht nur durch
einen ausdruecklichen Klick oder durch einen Zeitplan, den der Nutzer angelegt
hat. Es gibt keinen eingebauten Standardzeitplan.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import queue
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth as authmod
from . import aussenadresse
from . import checks as checkmod
from . import discover as discovery
from . import netguard, pve
from .config import SCRATCH_VMID_MAX, SCRATCH_VMID_MIN, Config
from .services import Janitor, JobManager, Scheduler
from .ssh import Host
from .store import Store

STATIC = pathlib.Path(__file__).parent / "static"
_CTYPES = {"html": "text/html; charset=utf-8",
           "png": "image/png",
           "js": "application/javascript; charset=utf-8",
           "css": "text/css; charset=utf-8"}

# Pfade, die ohne Anmeldung erreichbar sein muessen - sonst kaeme niemand je
# zur Anmeldemaske. Bewusst kurz gehalten und ausdruecklich aufgezaehlt.
_OFFEN = {"/login", "/login.html", "/login.js", "/api/me", "/api/setup",
          "/logo.png", "/favicon.png", "/favicon.ico",
          "/i18n.js", "/i18n-en.js"}

# Anmeldeversuche, die gebremst und gesperrt werden.
_ANMELDEPFADE = ("/api/auth/sign-in", "/api/auth/two-factor/verify-totp",
                 "/api/auth/two-factor/verify-backup-code")


class Handler(BaseHTTPRequestHandler):
    server_version = "proxfy"
    manager: JobManager
    janitor: Janitor
    store: Store
    cfg: Config
    host: Host
    auth: authmod.AuthClient
    guard: authmod.LoginGuard

    def log_message(self, fmt, *args):   # Zugriffslog unterdruecken
        pass

    # --- Hilfsmittel ---------------------------------------------------------

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rumpf_bytes(self) -> bytes:
        """Liest den Rumpf der Anfrage - auch stueckweise uebertragen.

        Hinter einem Reverse Proxy kommt der Rumpf oft ohne Content-Length an:
        spricht der Browser HTTP/2 mit dem Proxy, kennt dieser die Laenge beim
        Weiterreichen noch nicht und schickt 'Transfer-Encoding: chunked'. Wer
        dann nur Content-Length auswertet, liest null Bytes - die Anmeldung
        scheitert mit 'expected string, received undefined', obwohl der Browser
        alles mitgeschickt hat.
        """
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            teile = []
            while True:
                zeile = self.rfile.readline(1024).strip()
                # Nach der Laenge darf eine Erweiterung stehen, abgetrennt mit
                # Semikolon. Sie interessiert hier nicht.
                laenge = int(zeile.split(b";")[0] or b"0", 16)
                if laenge == 0:
                    break
                teile.append(self.rfile.read(laenge))
                self.rfile.read(2)          # das CRLF hinter dem Stueck
            # Abschliessende Kopfzeilen bis zur Leerzeile wegraeumen.
            while self.rfile.readline(1024).strip():
                pass
            return b"".join(teile)

        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _read_body(self) -> dict:
        return json.loads(self._rumpf_bytes() or b"{}")

    def _file(self, name: str) -> None:
        p = (STATIC / name).resolve()
        if not p.is_file() or STATIC.resolve() not in p.parents:
            return self._json({"error": "nicht gefunden"}, 404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CTYPES.get(name.rsplit(".", 1)[-1], "text/plain"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- Anmeldung -----------------------------------------------------------

    def client_ip(self) -> str:
        """Herkunftsadresse des Aufrufers.

        Hinter einem Reverse Proxy steht die echte Adresse in X-Forwarded-For.
        Der Kopfzeile wird nur geglaubt, wenn das in der Konfiguration
        ausdruecklich erlaubt ist - sonst koennte sich jeder eine beliebige
        Adresse ausdenken und damit die Sperre umgehen.
        """
        if getattr(self.cfg, "trust_forwarded_for", False):
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "?"

    def identity(self):
        """Angemeldete Kennung oder None. Ergebnis gilt fuer diese Anfrage."""
        if not hasattr(self, "_identity_cache"):
            try:
                self._identity_cache = self.auth.identity(self.headers.get("Cookie", ""))
            except authmod.AuthError:
                self._identity_cache = None
        return self._identity_cache

    def _needs_setup(self) -> bool:
        try:
            return self.auth.needs_setup()
        except authmod.AuthError:
            return False

    def allowed(self, path: str) -> bool:
        """Darf diese Anfrage ohne Anmeldung weiterlaufen?"""
        if path in _OFFEN or authmod.is_auth_path(path):
            return True
        # Die Anmeldemaske und ihr Beiwerk.
        if path in ("/style.css",):
            return True
        return self.identity() is not None

    def deny(self, path: str) -> None:
        """Abweisung - als Umleitung fuer Seiten, als 401 fuer die Schnittstelle."""
        if path.startswith("/api/"):
            return self._json({"error": "nicht angemeldet", "login": True}, 401)
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def proxy_auth(self, method: str) -> None:
        """Reicht eine /api/auth/-Anfrage an den Anmeldedienst weiter.

        Anmeldeversuche laufen vorher durch die Bremse. Abgewiesen wird HIER,
        bevor der Anmeldedienst das Passwort ueberhaupt zu sehen bekommt.
        """
        u = urllib.parse.urlparse(self.path)
        body = self._rumpf_bytes()

        ist_anmeldung = any(u.path.startswith(p) for p in _ANMELDEPFADE)
        ip = self.client_ip()
        kennung = self.guard.identifier_from(body) if ist_anmeldung else ""

        if ist_anmeldung:
            erlaubt, verzoegerung, grund = self.guard.check(ip, kennung)
            if not erlaubt:
                self.log_error_line(f"[Anmeldung] gesperrt: {ip} / {kennung or '-'}")
                return self._json({"message": grund, "code": "RATE_LIMITED"}, 429)
            if verzoegerung:
                time.sleep(verzoegerung)

        try:
            status, cookies, out = self.auth.proxy(method, self.path, body, dict(self.headers))
        except authmod.AuthError as e:
            return self._json({"message": str(e)}, 502)

        if ist_anmeldung:
            self.guard.record(ip, kennung, ok=status < 400)

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(out)

    def log_error_line(self, msg: str) -> None:
        print(msg, flush=True)

    # --- Einstellungen -------------------------------------------------------
    # Drei Gruppen mit unterschiedlicher Absicherung:
    #   defaults  harmlos, wirken beim naechsten Lauf
    #   proxmox   erst nach bestandener Verbindungspruefung speicherbar
    #   zugriff   Passwort noetig, danach Ruecknahme-Frist

    FELDER = {
        "defaults": ("restore.backup_storage", "restore.target_storage",
                     "restore.isolated_bridge", "restore.lan_bridge",
                     "restore.boot_timeout", "restore.agent_timeout",
                     "default_keep", "default_ttl"),
        "proxmox": ("host.host", "host.user", "host.key_file"),
        "zugriff": ("trust_forwarded_for", "public_url", "secure_cookies"),
        "sperre": ("delay_from", "lock_from", "lock_minutes"),
    }
    # Gruppen, die ohne erneute Passworteingabe nicht gespeichert werden.
    PASSWORT_NOETIG = ("zugriff", "sperre")

    def _passwort_pruefen(self, body: dict) -> None:
        ident = self.identity()
        pw = body.get("password", "")
        if not pw or not self.auth.verify_password(ident.email, pw):
            raise authmod.Verboten(
                "Das Passwort stimmt nicht. Sicherheitsrelevante Einstellungen "
                "verlangen es erneut.")

    def _einstellungen_lesen(self) -> dict:
        offen = self.store.pending_rollback()
        return {
            "werte": self.cfg.als_dict(),
            "ueberlagert": sorted(self.store.get_settings().keys()),
            "felder": {k: list(v) for k, v in self.FELDER.items()},
            "ruecknahme": ({"faellig": offen["faellig"], "wer": offen["wer"]}
                           if offen else None),
        }

    def _verbindung_pruefen(self, body: dict) -> dict:
        """Testet die angegebene Proxmox-Anbindung, ohne sie zu uebernehmen.

        Zwingend VOR dem Speichern: eine falsche Adresse oder ein falscher
        Schluesselpfad sperrt Proxfy vom Hypervisor aus - und den Nutzer aus
        dieser Maske.
        """
        probe = Host(body.get("host") or self.cfg.host.host,
                     body.get("user") or self.cfg.host.user,
                     body.get("key_file") or self.cfg.host.key_file,
                     self.cfg.host.port)
        try:
            version = probe.ping()
            knoten = probe.node_name()
            return {"ok": True,
                    "message": f"Verbindung steht: {knoten}, {version.splitlines()[0]}"}
        except Exception as e:
            return {"ok": False, "message": f"Keine Verbindung: {e}"}

    def _proxy_pruefen(self, body: dict) -> dict:
        """Ruft die Aussenadresse auf und sagt, was zurueckkam.

        Prueft die Kette von aussen nach innen: loest der Name auf, antwortet
        etwas, und ist das wirklich Proxfy. Der haeufigste Fehler ist ein Proxy,
        der auf den falschen Rechner zeigt - dann antwortet zwar etwas, aber
        eben eine fremde Anwendung.
        """
        url = aussenadresse.pruefe(body.get("public_url") or self.cfg.public_url)
        if not url:
            return {"ok": False, "message": "Keine Aussenadresse eingetragen."}

        ctx = ssl.create_default_context()
        if body.get("insecure"):
            # Ein Zertifikat, dem der Container nicht traut, sagt nichts
            # darueber, ob der Proxy richtig zeigt. Auf Wunsch trotzdem messen.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        anfrage = urllib.request.Request(url + "/api/me", method="GET")
        try:
            with urllib.request.urlopen(anfrage, timeout=10, context=ctx) as antwort:
                roh = antwort.read(4096)
                typ = antwort.headers.get("Content-Type", "")
                code = antwort.status
        except urllib.error.HTTPError as e:
            return {"ok": False, "message":
                    f"{url} antwortet mit {e.code} {e.reason}. Zeigt der Proxy auf "
                    f"{self.cfg.public_url and 'diesen Container' or 'Port 8099'}?"}
        except ssl.SSLCertVerificationError as e:
            return {"ok": False, "zertifikat": True, "message":
                    f"Das Zertifikat von {url} wird nicht anerkannt: {e.verify_message}. "
                    "Bei einem selbst ausgestellten Zertifikat ist das erwartbar."}
        except Exception as e:
            return {"ok": False, "message": f"{url} nicht erreichbar: {e}"}

        try:
            daten = json.loads(roh)
        except Exception:
            return {"ok": False, "message":
                    f"{url} antwortet mit {code}, aber nicht mit Proxfy "
                    f"(Inhaltstyp {typ or 'unbekannt'}). Der Proxy zeigt woanders hin."}
        if "authenticated" not in daten:
            return {"ok": False, "message":
                    f"{url} antwortet, aber nicht wie Proxfy. Der Proxy zeigt woanders hin."}
        return {"ok": True, "message":
                f"{url} erreicht Proxfy. Der Weg ueber den Proxy steht."}

    def _einstellungen_speichern(self, body: dict) -> dict:
        ident = self.identity()
        authmod.mindestens(ident, "super")

        gruppe = body.get("gruppe", "")
        if gruppe not in self.FELDER:
            raise ValueError(f"Unbekannte Gruppe '{gruppe}'")

        werte = {k: v for k, v in (body.get("werte") or {}).items()
                 if k in self.FELDER[gruppe]}
        if not werte:
            raise ValueError("Keine gueltigen Felder uebergeben")

        if gruppe in self.PASSWORT_NOETIG:
            self._passwort_pruefen(body)

        if "public_url" in werte or "secure_cookies" in werte:
            werte["public_url"] = aussenadresse.pruefe(werte["public_url"])

        if gruppe == "proxmox":
            # Ohne bestandene Pruefung wird nicht gespeichert.
            pruefung = self._verbindung_pruefen({
                "host": werte.get("host.host"), "user": werte.get("host.user"),
                "key_file": werte.get("host.key_file")})
            if not pruefung["ok"]:
                raise ValueError(pruefung["message"] + " - nicht gespeichert.")

        vorher = {k: self.cfg.als_dict().get(k) for k in werte}
        self.store.set_settings(werte, ident.email)
        self.cfg.anwenden(werte)

        # Die Sperr-Schwellen wirken sofort im Waechter.
        self.guard.schwellen(self.cfg)

        antwort = {"gespeichert": sorted(werte), "ruecknahme": None}
        if "public_url" in werte or "secure_cookies" in werte:
            # Der Anmeldedienst liest auth.env nur beim Start.
            aussenadresse.angleichen(self.cfg.auth.env_file, self.cfg.public_url,
                                     self.cfg.secure_cookies)
        if gruppe == "zugriff":
            # Diese Gruppe kann aussperren - Frist setzen.
            self.store.arm_rollback(vorher, 10, ident.email)
            antwort["ruecknahme"] = 10
        self.log_error_line(
            f"[Einstellungen] {ident.email} aendert {', '.join(sorted(werte))}")
        return antwort

    def _ruecknahme(self, bestaetigen: bool) -> dict:
        ident = self.identity()
        authmod.mindestens(ident, "super")
        offen = self.store.pending_rollback()
        if not offen:
            return {"offen": False}
        if bestaetigen:
            self.store.clear_rollback()
            self.log_error_line(f"[Einstellungen] {ident.email} bestaetigt die Aenderung")
            return {"bestaetigt": True}
        self.store.set_settings(offen["vorher"], ident.email)
        self.cfg.anwenden(offen["vorher"])
        if "public_url" in offen["vorher"]:
            aussenadresse.angleichen(self.cfg.auth.env_file, self.cfg.public_url,
                                     self.cfg.secure_cookies)
        self.store.clear_rollback()
        self.log_error_line(f"[Einstellungen] {ident.email} nimmt die Aenderung zurueck")
        return {"zurueckgenommen": True}

    # --- Rechte --------------------------------------------------------------

    def _benutzer_endpunkt(self, pfad: str, body: dict):
        """Alles unter Benutzerverwaltung. Gibt die Antwort zurueck oder wirft."""
        ident = self.identity()

        if pfad == "/api/users" and self.command == "GET":
            authmod.mindestens(ident, "admin")
            return self.auth.list_users()

        if pfad == "/api/users":                       # anlegen
            rolle = body.get("role", "user")
            if rolle != "user":
                # Rollen oberhalb 'user' vergibt ausschliesslich der Super Admin.
                authmod.mindestens(ident, "super")
            else:
                authmod.mindestens(ident, "admin")
            return self.auth.create_user(body.get("email", ""), body.get("password", ""),
                                         body.get("name", ""), rolle)

        ziel_id = body.get("id")
        if not ziel_id:
            raise ValueError("id fehlt")
        ziel = self.auth.find_user(ziel_id)
        if not ziel:
            raise ValueError("Benutzer nicht gefunden")

        if pfad == "/api/users/role":
            # Rollen vergeben darf nur der Super Admin - auch das Herabstufen.
            authmod.mindestens(ident, "super")
            neu = body.get("role", "")
            self.log_error_line(
                f"[Rollen] {ident.email} setzt {ziel['email']} auf '{neu}'")
            return self.auth.set_role(ziel_id, neu)

        authmod.darf_verwalten(ident, ziel.get("role", "user"))

        if pfad == "/api/users/delete":
            self.log_error_line(f"[Benutzer] {ident.email} entfernt {ziel['email']}")
            return self.auth.delete_user(ziel_id)
        if pfad == "/api/users/logout":
            return self.auth.logout_user(ziel_id)
        if pfad == "/api/users/reset-2fa":
            self.log_error_line(
                f"[Zwei-Faktor] {ident.email} setzt den zweiten Faktor von "
                f"{ziel['email']} zurueck")
            return self.auth.reset_two_factor(ziel_id)

        raise ValueError("unbekannter Pfad")

    # --- GET -----------------------------------------------------------------

    def do_GET(self) -> None:
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            # Der Waechter steht vor allem anderen.
            if not self.allowed(u.path):
                return self.deny(u.path)

            if authmod.is_auth_path(u.path):
                return self.proxy_auth("GET")

            if u.path in ("/login", "/login.html"):
                return self._file("login.html")
            if u.path == "/login.js":
                return self._file("login.js")
            if u.path in ("/i18n.js", "/i18n-en.js"):
                return self._file(u.path.lstrip("/"))

            if u.path == "/api/me":
                # Beantwortet zugleich, ob ueberhaupt schon ein Konto besteht.
                ident = self.identity()
                nutzer = dataclasses.asdict(ident) if ident else None
                if nutzer:
                    nutzer["sprache"] = self.store.sprache(ident.user_id)
                return self._json({
                    "authenticated": ident is not None,
                    "needs_setup": self._needs_setup(),
                    "user": nutzer,
                })

            if u.path in ("/", "/index.html"):
                return self._file("index.html")
            if u.path in ("/app.js", "/style.css", "/i18n.js", "/i18n-en.js"):
                return self._file(u.path.lstrip("/"))
            if u.path in ("/logo.png", "/favicon.png"):
                return self._file(u.path.lstrip("/"))
            if u.path == "/favicon.ico":
                return self._file("favicon.png")

            if u.path == "/api/users":
                return self._json(self._benutzer_endpunkt(u.path, {}))
            if u.path == "/api/logins":
                authmod.mindestens(self.identity(), "admin")
                return self._json(self.store.recent_logins(
                    int(q.get("limit", ["50"])[0])))

            if u.path == "/api/settings":
                authmod.mindestens(self.identity(), "super")
                return self._json(self._einstellungen_lesen())

            if u.path == "/api/status":
                st = self.manager.state()
                return self._json({
                    **st,
                    "scratch_range": [SCRATCH_VMID_MIN, SCRATCH_VMID_MAX],
                    "backup_storage": self.cfg.restore.backup_storage,
                    "target_storage": self.cfg.restore.target_storage,
                    "isolated_bridge": self.cfg.restore.isolated_bridge,
                    "lan_bridge": self.cfg.restore.lan_bridge,
                    "node": self.host.node_name(),
                    "active_leases": len(self.store.list_leases()),
                    "schedule_count": len(self.store.list_schedules()),
                })
            if u.path == "/api/nodes":
                return self._json(pve.list_nodes(self.host))
            if u.path == "/api/storages":
                node = q.get("node", [None])[0]
                return self._json(pve.list_storages(self.host, node))
            if u.path == "/api/inventory":
                return self._json(self._inventory())
            if u.path == "/api/snapshots":
                vmid = int(q["vmid"][0])
                store_name = q.get("storage", [self.cfg.restore.backup_storage])[0]
                node = q.get("node", [None])[0]
                host = self.host.for_node(node)
                return self._json([dataclasses.asdict(s)
                                   for s in pve.list_snapshots(host, store_name)
                                   if s.vmid == vmid])
            if u.path == "/api/targets":
                return self._json(self.store.list_targets())
            if u.path == "/api/leases":
                return self._json(self.store.list_leases())
            if u.path == "/api/schedules":
                return self._json(self.store.list_schedules())
            if u.path == "/api/ips":
                return self._json(self.store.list_ips())
            if u.path == "/api/jobs":
                vmid = q.get("vmid", [None])[0]
                sched = q.get("schedule_id", [None])[0]
                return self._json(self.store.list_jobs(
                    int(q.get("limit", ["100"])[0]),
                    int(vmid) if vmid else None,
                    int(sched) if sched else None))
            if u.path.startswith("/api/jobs/"):
                job = self.store.get_job(u.path.rsplit("/", 1)[-1])
                return self._json(job or {"error": "unbekannter Job"}, 200 if job else 404)
            if u.path == "/api/stream":
                return self._stream()
            return self._json({"error": "nicht gefunden"}, 404)
        except authmod.Verboten as e:
            return self._json({"error": str(e), "forbidden": True}, 403)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # --- POST ----------------------------------------------------------------

    def do_POST(self) -> None:
        u = urllib.parse.urlparse(self.path)
        try:
            # Waechter und Durchreichen VOR dem Lesen des Rumpfes - der
            # Anmeldedienst liest ihn selbst.
            if authmod.is_auth_path(u.path):
                return self.proxy_auth("POST")
            if not self.allowed(u.path):
                return self.deny(u.path)

            body = self._read_body()

            if u.path == "/api/setup":
                # Erstes Konto anlegen. Nur solange es noch gar keines gibt -
                # danach ist dieser Weg dauerhaft zu.
                if not self._needs_setup():
                    return self._json({"error": "Es besteht bereits ein Konto"}, 403)
                self.auth.create_user(body.get("email", ""), body.get("password", ""),
                                      body.get("name", ""))
                self.log_error_line(f"[Anmeldung] Erstes Konto angelegt: {body.get('email')}")
                return self._json({"created": True})

            if u.path.startswith("/api/users"):
                return self._json(self._benutzer_endpunkt(u.path, body))

            if u.path == "/api/settings":
                return self._json(self._einstellungen_speichern(body))
            if u.path == "/api/me/language":
                ident = self.identity()
                return self._json({"sprache": self.store.set_sprache(
                    ident.user_id, body.get("sprache", "de"))})

            if u.path == "/api/settings/proxy":
                authmod.mindestens(self.identity(), "super")
                return self._json(self._proxy_pruefen(body))
            if u.path == "/api/settings/test":
                authmod.mindestens(self.identity(), "super")
                return self._json(self._verbindung_pruefen(body))
            if u.path == "/api/settings/confirm":
                return self._json(self._ruecknahme(True))
            if u.path == "/api/settings/rollback":
                return self._json(self._ruecknahme(False))

            if u.path == "/api/logins/unlock":
                authmod.mindestens(self.identity(), "admin")
                n = self.store.unlock(body.get("ip"), body.get("identifier"))
                self.log_error_line(
                    f"[Anmeldung] {self.identity().email} hebt Sperren auf ({n} Eintraege)")
                return self._json({"cleared": n})

            # Der QR-Code betrifft immer den eigenen zweiten Faktor - dafuer
            # genuegt eine gueltige Anmeldung.
            if u.path == "/api/qr":
                return self._json(self._qr(body))

            if u.path == "/api/run":
                targets = body.get("targets") or [body]
                return self._json(self.manager.enqueue(self._with_names(targets),
                                                       source="manuell"))
            if u.path == "/api/discover":
                return self._json(self._discover(body))
            if u.path == "/api/checks/try":
                return self._json(self._try_check(body))
            if u.path == "/api/preflight":
                return self._json(self._preflight(body))

            if u.path == "/api/targets":
                return self._json(self.store.save_target(body))
            if u.path == "/api/targets/delete":
                self.store.delete_target(int(body["id"]))
                return self._json({"deleted": True})

            if u.path == "/api/leases/remove":
                return self._json(self.janitor.remove(int(body["scratch_vmid"])))
            if u.path == "/api/leases/extend":
                lease = self.store.extend_lease(int(body["scratch_vmid"]),
                                                int(body.get("minutes", 60)))
                return self._json(lease or {"error": "kein aktiver Testgast"})

            if u.path == "/api/ips":
                authmod.mindestens(self.identity(), "super")
                return self._json(self.store.save_ip(body))
            if u.path == "/api/ips/delete":
                authmod.mindestens(self.identity(), "super")
                self.store.delete_ip(int(body["id"]))
                return self._json({"deleted": True})
            if u.path == "/api/schedules":
                return self._json(self.store.save_schedule(body))
            if u.path == "/api/schedules/delete":
                self.store.delete_schedule(int(body["id"]))
                return self._json({"deleted": True})
            if u.path == "/api/schedules/run":
                sched = next((s for s in self.store.list_schedules()
                              if int(s["id"]) == int(body["id"])), None)
                if not sched:
                    return self._json({"error": "unbekannter Zeitplan"}, 404)
                targets = self._with_names(Scheduler.build_targets(sched))
                return self._json(self.manager.enqueue(
                    targets, source=f"Zeitplan von Hand: {sched['name']}"))

            if u.path == "/api/reap":
                keep_ids = {int(l["scratch_vmid"]) for l in self.store.list_leases()}
                found = pve.reap_orphans(self.host, SCRATCH_VMID_MIN, SCRATCH_VMID_MAX,
                                         dry_run=not body.get("force"), protected=keep_ids)
                return self._json({"found": found, "dry_run": not body.get("force")})
            return self._json({"error": "nicht gefunden"}, 404)
        except authmod.Verboten as e:
            return self._json({"error": str(e), "forbidden": True}, 403)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 400)

    # --- Fachlogik -----------------------------------------------------------

    def _inventory(self) -> list:
        """Gaeste, ihr neuestes Backup und ihr letztes Pruefergebnis."""
        guests = pve.list_guests(self.host)
        snaps = pve.list_snapshots(self.host, self.cfg.restore.backup_storage)

        latest: dict = {}
        counts: dict = {}
        for s in snaps:
            counts[s.vmid] = counts.get(s.vmid, 0) + 1
            if s.vmid not in latest:
                latest[s.vmid] = s
        verdicts = self.store.last_verdicts()
        leases = {int(l["source_vmid"]): l for l in self.store.list_leases()
                  if l.get("source_vmid") is not None}

        out = []
        for g in guests:
            s = latest.get(g["vmid"])
            v = verdicts.get(g["vmid"])
            l = leases.get(g["vmid"])
            out.append({**g,
                        "has_backup": s is not None,
                        "latest_snapshot": s.volid if s else None,
                        "latest_ts": s.ts if s else None,
                        "size": s.size if s else 0,
                        "snapshot_count": counts.get(g["vmid"], 0),
                        "last_verdict": v["verdict"] if v else None,
                        "last_run": v["started"] if v else None,
                        "last_job": v["job_id"] if v else None,
                        "live_scratch": l["scratch_vmid"] if l else None})
        return out

    def _with_names(self, targets: list) -> list:
        """Ergaenzt Gastnamen und loest die Adressauswahl auf.

        Die Oberflaeche schickt die Kennung eines Vorrats-Eintrags. Welche
        Adresse daraus genommen wird, entscheidet erst der Lauf - bis dahin
        koennen andere Laeufe Adressen belegt oder freigegeben haben.
        """
        names = {g["vmid"]: g["name"] for g in pve.list_guests(self.host)}
        out = []
        for t in targets:
            t = dict(t)
            t.setdefault("name", names.get(int(t.get("vmid", 0)), ""))
            if t.get("ip_pool_id"):
                eintrag = self.store.get_ip(int(t["ip_pool_id"]))
                if not eintrag:
                    raise ValueError("Der gewaehlte Adress-Eintrag besteht nicht mehr.")
                t["ip_pool"] = eintrag
                t.setdefault("gateway", eintrag.get("gateway"))
            out.append(t)
        return out

    def _live_ctx(self, scratch_vmid: int):
        """Zugang zu einem laufenden Testgast - die Werkbank fuer Erkennung
        und Probelauf.

        Bewusst nur ueber die Lease-Tabelle: damit ist ausgeschlossen, dass hier
        versehentlich ein produktiver Gast angefasst wird. Erreichbar ist
        ausschliesslich, was dieses Werkzeug selbst erzeugt hat.
        """
        lease = self.store.get_lease(int(scratch_vmid))
        if not lease or lease["state"] != "aktiv":
            raise RuntimeError(
                f"Kein laufender Testgast mit VMID {scratch_vmid}. Zuerst einen Lauf mit "
                "der Lebensdauer 'stehen lassen' starten.")
        host = self.host.for_node(lease.get("node"))
        cmd = "qm" if lease["kind"] == "vm" else "pct"
        if "running" not in host.run(cmd, "status", str(scratch_vmid)).out:
            raise RuntimeError(f"Testgast {scratch_vmid} laeuft nicht")
        return host, lease

    def _discover(self, body: dict) -> dict:
        """Schlaegt Pruefungen anhand dessen vor, was im Testgast wirklich laeuft."""
        host, lease = self._live_ctx(int(body["scratch_vmid"]))
        return discovery.discover(host, int(lease["scratch_vmid"]), lease["kind"])

    def _try_check(self, body: dict) -> dict:
        """Fuehrt eine einzelne Pruefung sofort gegen einen laufenden Testgast aus."""
        host, lease = self._live_ctx(int(body["scratch_vmid"]))
        ip = str(lease["ip"]).split("/")[0] if lease.get("ip") else None
        ctx = checkmod.Ctx(host, int(lease["scratch_vmid"]), lease["kind"], ip=ip)
        res = checkmod.run_check(ctx, body.get("check") or {})
        out = dataclasses.asdict(res)
        out["status"] = res.status
        return out

    def _qr(self, body: dict) -> dict:
        """QR-Code fuer die Authenticator-App. Der Text kommt vom Anmeldedienst
        und wird nur dorthin zurueckgereicht - er verlaesst den Server nicht."""
        return self.auth._internal("POST", "/internal/qr", {"text": body.get("text", "")})

    def _preflight(self, body: dict) -> dict:
        """Prueft eine Wunsch-IP, bevor der Nutzer den Lauf ueberhaupt startet."""
        ip = body.get("ip")
        if not ip:
            return {"ok": False, "message": "Keine IP angegeben"}
        if ip.split("/")[0] in self.store.leased_ips():
            return {"ok": False,
                    "message": f"{ip} ist bereits an einen laufenden Testgast vergeben"}
        try:
            netguard.preflight_ip(self.host, ip, self.cfg.restore.lan_bridge)
            return {"ok": True, "message": f"{ip} ist frei und kann vergeben werden"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _stream(self) -> None:
        """Server-Sent Events: Live-Protokoll des laufenden Auftrags."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q, backlog = self.manager.subscribe()
        try:
            for line in backlog:
                self._sse(line)
            while True:
                try:
                    line = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")   # Verbindung offen halten
                    self.wfile.flush()
                    continue
                self._sse(line)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.manager.unsubscribe(q)

    def _sse(self, line: str) -> None:
        self.wfile.write(("data: " + json.dumps({"line": line}, ensure_ascii=False)
                          + "\n\n").encode("utf-8"))
        self.wfile.flush()


def serve(cfg: Config, db_path: str, bind: str = "0.0.0.0", port: int = 8099) -> None:
    # Erst die Ueberlagerung laden, dann verbinden - sonst spraeche Proxfy mit
    # dem Host aus der Datei, obwohl in der Oberflaeche ein anderer steht.
    vorab = Store(db_path).get_settings()
    cfg.anwenden(vorab)
    host = Host(cfg.host.host, cfg.host.user, cfg.host.key_file, cfg.host.port)
    host.ping()
    store = Store(db_path)

    # Werte aus der Datenbank ueber die aus der Datei legen.
    cfg.anwenden(store.get_settings())

    # auth.env in Einklang mit der eingestellten Aussenadresse bringen. Noetig,
    # weil der Anmeldedienst diese Datei nur beim Start liest - und weil ein
    # "config reset" auf der Kommandozeile sonst nur halb wirken wuerde.
    try:
        if aussenadresse.angleichen(cfg.auth.env_file, cfg.public_url, cfg.secure_cookies):
            print(f"[Start] auth.env an {cfg.public_url or 'die eigene Adresse'} angepasst",
                  flush=True)
    except Exception as e:
        print(f"[Start] Aussenadresse nicht angeglichen: {e}", flush=True)

    # Anmeldedienst anbinden. Ohne ihn startet nichts - eine Oberflaeche, die
    # aus Versehen ohne Anmeldung laeuft, waere schlimmer als gar keine.
    env = authmod.load_env(cfg.auth.env_file)
    secret = env.get("PROXFY_INTERNAL_SECRET", "")
    if not secret:
        raise SystemExit(
            f"PROXFY_INTERNAL_SECRET fehlt in {cfg.auth.env_file}. "
            "Ohne dieses Geheimnis kann die Anmeldung nicht geprueft werden.")
    client = authmod.AuthClient(
        f"http://127.0.0.1:{env.get('PROXFY_AUTH_PORT', cfg.auth.port)}", secret)
    # Der Anmeldedienst braucht nach einem Neustart ein paar Sekunden - und
    # genau eben hat Proxfy ihn womoeglich selbst neu gestartet, weil sich die
    # Aussenadresse geaendert hat. Erst nach mehreren vergeblichen Versuchen
    # ist er wirklich weg.
    for versuch in range(15):
        try:
            setup = client.needs_setup()
            break
        except authmod.AuthError as e:
            if versuch == 14:
                raise SystemExit(f"Anmeldedienst nicht erreichbar: {e}")
            time.sleep(1)

    manager = JobManager(host, cfg, store)
    Handler.manager = manager
    Handler.janitor = Janitor(host, store, cfg=cfg)
    Handler.store = store
    Handler.cfg = cfg
    Handler.host = host
    Handler.auth = client
    Handler.guard = authmod.LoginGuard(store, cfg=cfg)
    Scheduler(store, manager)

    httpd = ThreadingHTTPServer((bind, port), Handler)
    httpd.daemon_threads = True

    shown = bind if bind not in ("0.0.0.0", "") else host.node_name()
    n_sched = len(store.list_schedules())
    print("Weboberflaeche: http://" + shown + ":" + str(port) + "/")
    print("Datenbank:      " + db_path)
    print("Scratch-VMIDs:  " + str(SCRATCH_VMID_MIN) + "-" + str(SCRATCH_VMID_MAX))
    print("Zeitplaene:     " + str(n_sched)
          + (" - es laeuft nichts ohne ausdruecklichen Auftrag" if n_sched == 0 else ""))
    print("Anmeldung:      " + ("EINRICHTUNG OFFEN - erstes Konto ueber die Oberflaeche anlegen"
                                if setup else "aktiv"))
    httpd.serve_forever()
