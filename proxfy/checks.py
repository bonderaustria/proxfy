"""Pruefungen im wiederhergestellten Gast.

Eine Pruefung beantwortet immer dieselbe Frage: Funktioniert der Dienst, wegen
dem diese VM ueberhaupt gesichert wird? "Bootet" reicht dafuer nicht.

Pruefungen laufen entweder INNEN (via Guest-Agent bzw. pct exec) oder AUSSEN
(vom PVE-Host aus gegen die vergebene IP). Aussen-Pruefungen setzen den Modus
'routed' voraus, weil es sonst keinen Netzwerkpfad gibt.
"""
from __future__ import annotations

import dataclasses
import re
import time

from . import pve
from .ssh import Host


@dataclasses.dataclass
class CheckResult:
    name: str
    kind: str
    passed: bool
    detail: str
    duration: float
    required: bool = True
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "UEBERSPRUNGEN"
        return "BESTANDEN" if self.passed else "FEHLGESCHLAGEN"


@dataclasses.dataclass
class Ctx:
    """Alles, was eine Pruefung ueber den laufenden Testgast wissen muss."""
    host: Host
    vmid: int
    kind: str                 # "vm" | "ct"
    ip: str | None = None     # nur im Modus 'routed' gesetzt

    def exec(self, argv: list[str], timeout: int = 60) -> pve.ExecResult:
        return pve.guest_exec(self.host, self.vmid, self.kind, argv, timeout)


# --- Einzelne Pruefungstypen --------------------------------------------------

def _check_boot(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Laeuft im Gast ueberhaupt etwas? Kommt in jedem Lauf zuerst."""
    r = ctx.exec(["uptime"], timeout=30)
    if not r.ok:
        return False, f"Kein Kommando ausfuehrbar: {r.err or r.out}"
    return True, r.out.strip()[:200]


def _check_service(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """systemd-Dienst aktiv?"""
    unit = spec["unit"]
    r = ctx.exec(["systemctl", "is-active", unit], timeout=30)
    state = (r.out or r.err).strip()
    if state == "active":
        return True, f"{unit} ist active"
    detail = ctx.exec(["systemctl", "status", "--no-pager", "-n", "10", unit], timeout=30)
    return False, f"{unit} ist '{state or 'unbekannt'}'\n{detail.out.strip()[:600]}"


def _check_port(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Lauscht ein TCP-Port? Innen via ss, aussen via nc gegen die vergebene IP."""
    port = int(spec["port"])
    if spec.get("external"):
        if not ctx.ip:
            return False, "Aussen-Pruefung verlangt Modus 'routed' mit vergebener IP"
        r = ctx.host.sh(f"timeout 8 bash -c '</dev/tcp/{ctx.ip}/{port}' 2>/dev/null && echo open || echo closed")
        ok = r.out.strip() == "open"
        return ok, f"{ctx.ip}:{port} ist {'erreichbar' if ok else 'nicht erreichbar'}"

    r = ctx.exec(["ss", "-ltnH"], timeout=30)
    if not r.ok:
        return False, f"ss nicht ausfuehrbar: {r.err or r.out}"
    listening = re.search(rf"[:\.]{port}\s", r.out) is not None
    return listening, f"Port {port} {'lauscht' if listening else 'lauscht nicht'} (innen)"


def _check_http(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """HTTP-Antwort mit erwartetem Statuscode und optionalem Textmuster."""
    url = spec["url"]
    expect = int(spec.get("expect_status", 200))
    pattern = spec.get("expect_body")
    external = bool(spec.get("external"))

    curl = ["curl", "-sk", "-m", str(spec.get("timeout", 15)), "-o", "/tmp/.proxfy_body",
            "-w", "%{http_code}", url]

    if external:
        if not ctx.ip:
            return False, "Aussen-Pruefung verlangt Modus 'routed' mit vergebener IP"
        # Innen-URLs auf die vergebene Adresse umschreiben, damit dieselbe
        # Pruefungsdefinition in beiden Modi verwendbar bleibt.
        shown = url.replace("://localhost", f"://{ctx.ip}").replace("://127.0.0.1", f"://{ctx.ip}")
        r = ctx.host.sh(f"curl -sk -m {spec.get('timeout', 15)} -o /tmp/.proxfy_body -w '%{{http_code}}' {shown!r}")
        code, body_src = r.out.strip(), ctx.host.sh("cat /tmp/.proxfy_body 2>/dev/null").out
        shown += " (vom Host)"
    else:
        shown = url
        r = ctx.exec(curl, timeout=spec.get("timeout", 15) + 20)
        code = r.out.strip()
        body_src = ctx.exec(["cat", "/tmp/.proxfy_body"], timeout=20).out

    if code != str(expect):
        return False, f"{shown} lieferte HTTP {code or '(keine Antwort)'}, erwartet {expect}"
    if pattern and not re.search(pattern, body_src, re.I | re.S):
        return False, f"{shown} lieferte HTTP {code}, aber Muster '{pattern}' fehlt im Rumpf"
    return True, f"{shown} lieferte HTTP {code}" + (f", Muster '{pattern}' gefunden" if pattern else "")


def _check_command(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Beliebiges Kommando. Erfolg = Exitcode 0 und optionales Ausgabemuster."""
    argv = spec["argv"] if isinstance(spec.get("argv"), list) else ["sh", "-c", spec["run"]]
    r = ctx.exec(argv, timeout=spec.get("timeout", 60))
    combined = (r.out + r.err).strip()
    expect_rc = int(spec.get("expect_rc", 0))
    if r.rc != expect_rc:
        return False, f"Exitcode {r.rc}, erwartet {expect_rc}\n{combined[:600]}"
    pattern = spec.get("expect_output")
    if pattern and not re.search(pattern, combined, re.I | re.S):
        return False, f"Exitcode {expect_rc}, aber Muster '{pattern}' fehlt\n{combined[:600]}"
    return True, combined[:2000] or f"Exitcode {expect_rc}"


def _check_file(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Datei vorhanden und optional groesser als min_bytes."""
    path = spec["path"]
    min_bytes = int(spec.get("min_bytes", 1))
    r = ctx.exec(["stat", "-c", "%s", path], timeout=30)
    if not r.ok:
        return False, f"{path} nicht vorhanden"
    try:
        size = int(r.out.strip())
    except ValueError:
        return False, f"Groesse von {path} nicht lesbar: {r.out.strip()}"
    if size < min_bytes:
        return False, f"{path} hat {size} Bytes, erwartet mindestens {min_bytes}"
    return True, f"{path} vorhanden, {size} Bytes"


def _as_user(user: str, inner: str) -> list[str]:
    """Kommando als anderer Nutzer ausfuehren, OHNE Login-Shell.

    'su - postgres' startet eine Login-Shell und gibt damit die Begruessung des
    Containers mit aus - bei den Community-Scripts-Vorlagen ein mehrzeiliges
    Banner. Das landete in der Antwort und liess jeden Soll-Vergleich scheitern.
    Ohne Bindestrich bleibt die Ausgabe sauber.
    """
    return ["su", "-s", "/bin/sh", user, "-c", inner]


def _last_line(text: str) -> str:
    """Letzte nicht leere Zeile - der eigentliche Rueckgabewert einer Abfrage."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def _check_postgres(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Datenbank antwortet auf eine echte Abfrage - der aussagekraeftigste Test."""
    db = spec.get("database", "postgres")
    user = spec.get("user", "postgres")
    query = spec.get("query", "SELECT 1")
    inner = f"psql -U {user} -d {db} -tAc {query!r}"
    argv = _as_user(user, inner) if spec.get("via_su", True) else ["sh", "-c", inner]
    r = ctx.exec(argv, timeout=spec.get("timeout", 60))
    if not r.ok:
        return False, f"Abfrage fehlgeschlagen: {(r.err or r.out).strip()[:400]}"
    value = _last_line(r.out)
    expect = spec.get("expect")
    if expect is not None and str(expect) != value:
        return False, f"Abfrage lieferte '{value}', erwartet '{expect}'"
    return True, f"Abfrage lieferte '{value[:120]}'"


def _check_mysql(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    db = spec.get("database", "")
    query = spec.get("query", "SELECT 1")
    cred = f"-u{spec['user']}" if spec.get("user") else ""
    cred += f" -p{spec['password']}" if spec.get("password") else ""
    inner = f"mysql {cred} -N -B {db} -e {query!r}"
    r = ctx.exec(["sh", "-c", inner], timeout=spec.get("timeout", 60))
    if not r.ok:
        return False, f"Abfrage fehlgeschlagen: {(r.err or r.out).strip()[:400]}"
    return True, f"Abfrage lieferte '{r.out.strip()[:120]}'"


# --- Datenaktualitaet ---------------------------------------------------------
# Der teuerste Backup-Fehler ist nicht "bootet nicht", sondern "bootet, aber die
# Daten sind drei Wochen alt". Diese drei Pruefungen beantworten genau das.

def _age_hours(ctx: Ctx, epoch: int) -> float:
    now = ctx.exec(["date", "+%s"], timeout=20).out.strip()
    return (int(now) - epoch) / 3600.0 if now.lstrip("-").isdigit() else -1.0


def _check_newest_file(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Wie alt ist die juengste Datei unter einem Pfad?"""
    path = spec["path"]
    max_age = float(spec.get("max_age_hours", 48))
    r = ctx.exec(["sh", "-c",
                  f"find {path!r} -type f -printf '%T@ %p\\n' 2>/dev/null "
                  f"| sort -rn | head -1"], timeout=spec.get("timeout", 90))
    if not r.out.strip():
        return False, f"Unter {path} wurde keine Datei gefunden"

    stamp, _, name = r.out.strip().partition(" ")
    try:
        epoch = int(float(stamp))
    except ValueError:
        return False, f"Zeitstempel nicht lesbar: {r.out.strip()[:200]}"

    age = _age_hours(ctx, epoch)
    if age < 0:
        return False, "Uhrzeit im Gast nicht lesbar"
    if age > max_age:
        return False, (f"Juengste Datei ist {age:.1f} h alt, erlaubt sind {max_age:.0f} h "
                       f"({name.strip()})")
    return True, f"Juengste Datei {age:.1f} h alt: {name.strip()[:120]}"


def _check_file_count(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Liegen ueberhaupt genug Dateien da? Faengt halb leere Wiederherstellungen."""
    path = spec["path"]
    min_count = int(spec.get("min_count", 1))
    max_count = spec.get("max_count")
    pattern = spec.get("pattern")

    cmd = f"find {path!r} -type f"
    if pattern:
        cmd += f" -name {pattern!r}"
    cmd += " 2>/dev/null | wc -l"
    r = ctx.exec(["sh", "-c", cmd], timeout=spec.get("timeout", 90))
    val = r.out.strip()
    if not val.isdigit():
        return False, f"Anzahl nicht lesbar: {(r.err or r.out).strip()[:200]}"

    n = int(val)
    if n < min_count:
        return False, f"{n} Dateien unter {path}, erwartet mindestens {min_count}"
    if max_count is not None and n > int(max_count):
        return False, f"{n} Dateien unter {path}, erwartet hoechstens {max_count}"
    return True, f"{n} Dateien unter {path}"


def _check_db_fresh(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """Wie alt ist der juengste Datensatz? Die eigentliche Frage an ein Backup."""
    engine = spec.get("engine", "postgres")
    query = spec.get("query") or "SELECT 1"
    max_age = float(spec.get("max_age_hours", 48))

    if engine == "postgres":
        user = spec.get("user", "postgres")
        db = spec.get("database", "postgres")
        # Direkt als Unix-Zeit abfragen, das erspart das Parsen von Datumsformaten.
        wrapped = f"SELECT extract(epoch from ({query.rstrip(';')}))::bigint"
        inner = f"psql -U {user} -d {db} -tAc {wrapped!r}"
        argv = _as_user(user, inner)
    else:
        db = spec.get("database", "")
        cred = f"-u{spec['user']}" if spec.get("user") else ""
        cred += f" -p{spec['password']}" if spec.get("password") else ""
        wrapped = f"SELECT UNIX_TIMESTAMP(({query.rstrip(';')}))"
        argv = ["sh", "-c", f"mysql {cred} -N -B {db} -e {wrapped!r}"]

    r = ctx.exec(argv, timeout=spec.get("timeout", 60))
    val = _last_line(r.out)
    if not r.ok or not val or val.upper() in ("NULL", "\\N"):
        return False, f"Abfrage lieferte keinen Zeitstempel: {(r.err or r.out).strip()[:300]}"
    try:
        epoch = int(float(val))
    except ValueError:
        return False, f"Zeitstempel nicht lesbar: {val[:120]}"

    age = _age_hours(ctx, epoch)
    if age < 0:
        return False, "Uhrzeit im Gast nicht lesbar"
    if age > max_age:
        return False, f"Juengster Datensatz ist {age:.1f} h alt, erlaubt sind {max_age:.0f} h"
    return True, f"Juengster Datensatz {age:.1f} h alt"


def _check_tls(ctx: Ctx, spec: dict) -> tuple[bool, str]:
    """TLS-Handschlag und Restlaufzeit des Zertifikats.

    Laeuft vom Host aus und setzt daher den Modus 'routed' voraus - ein
    Zertifikat pruefen zu wollen, ohne das Netz zu benutzen, ergibt keinen Sinn.
    """
    if not ctx.ip:
        return False, "TLS-Pruefung verlangt Modus 'routed' mit vergebener IP"
    port = int(spec.get("port", 443))
    min_days = int(spec.get("min_days", 7))
    servername = spec.get("servername") or ctx.ip

    r = ctx.host.sh(
        f"echo | timeout 15 openssl s_client -connect {ctx.ip}:{port} "
        f"-servername {servername} 2>/dev/null | openssl x509 -noout -subject -enddate 2>/dev/null")
    if not r.out.strip():
        return False, f"Kein TLS-Handschlag mit {ctx.ip}:{port} moeglich"

    m = re.search(r"notAfter=(.+)", r.out)
    if not m:
        return True, f"TLS-Handschlag mit {ctx.ip}:{port} erfolgreich, Ablaufdatum nicht lesbar"

    end = m.group(1).strip()
    rc = ctx.host.sh(f"date -d {end!r} +%s").out.strip()
    if not rc.lstrip("-").isdigit():
        return True, f"TLS-Handschlag erfolgreich, gueltig bis {end}"

    days = (int(rc) - int(ctx.host.sh("date +%s").out.strip())) // 86400
    if days < min_days:
        return False, f"Zertifikat laeuft in {days} Tagen ab ({end}), verlangt sind {min_days}"
    return True, f"TLS ok, Zertifikat noch {days} Tage gueltig (bis {end})"


_REGISTRY = {
    "boot": _check_boot,
    "tls": _check_tls,
    "newest_file": _check_newest_file,
    "file_count": _check_file_count,
    "db_fresh": _check_db_fresh,
    "service": _check_service,
    "port": _check_port,
    "http": _check_http,
    "command": _check_command,
    "file": _check_file,
    "postgres": _check_postgres,
    "mysql": _check_mysql,
}


def is_external(spec: dict) -> bool:
    """Braucht diese Pruefung einen Netzwerkpfad vom Host zum Gast?

    TLS immer - ein Zertifikat ohne Netz zu pruefen ergibt keinen Sinn.
    """
    return bool(spec.get("external")) or spec.get("type") == "tls"


def run_check(ctx: Ctx, spec: dict) -> CheckResult:
    kind = spec.get("type", "command")
    name = spec.get("name", kind)
    required = bool(spec.get("required", True))

    fn = _REGISTRY.get(kind)
    if fn is None:
        return CheckResult(name, kind, False,
                           f"Unbekannter Pruefungstyp '{kind}'. Bekannt: {', '.join(sorted(_REGISTRY))}",
                           0.0, required)

    if is_external(spec) and not ctx.ip:
        return CheckResult(name, kind, False, "Benoetigt Modus 'routed'", 0.0, required, skipped=True)

    t0 = time.monotonic()
    try:
        passed, detail = fn(ctx, spec)
    except KeyError as e:
        passed, detail = False, f"Pflichtfeld {e} fehlt in der Pruefungsdefinition"
    except Exception as e:  # eine kaputte Pruefung darf den Lauf nicht abbrechen
        passed, detail = False, f"{type(e).__name__}: {e}"
    return CheckResult(name, kind, passed, detail, time.monotonic() - t0, required)
