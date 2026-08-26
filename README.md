<img src="proxfy/static/logo.png" alt="Proxfy" width="150">

# Proxfy

**Prüft, ob sich eure Proxmox-Backups wirklich wiederherstellen lassen — und ob
danach die Anwendung läuft.**

Proxmox Backup Server prüft Chunk-Prüfsummen. Das belegt, dass die Bytes
unversehrt sind. Es belegt nicht, dass daraus eine funktionierende Maschine
wird. Proxfy stellt ein Backup unter einer Wegwerf-VMID wieder her, startet es
abgeschottet, führt echte Funktionsprüfungen aus — Dienst aktiv, Port lauscht,
Weboberfläche antwortet, Datenbank liefert einen aktuellen Datensatz — und räumt
danach auf.

Funktioniert für **VMs und LXC-Container** gleichermaßen.

> **Es läuft nichts von selbst.** Kein eingebauter Zeitplan, kein Cron-Eintrag.
> Ein Lauf entsteht nur durch einen Klick oder durch einen Zeitplan, den ihr
> selbst anlegt.

---

## Installation

Auf dem Proxmox-Host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

Das Skript fragt Container-ID, Adresse, Gateway und Ressourcen ab, legt einen
unprivilegierten Container an, richtet den SSH-Zugang zum Hypervisor ein und
installiert alles hinein. Danach ist die Oberfläche unter `http://<adresse>:8099/`
erreichbar und wartet auf das erste Konto.

Ohne Rückfragen:

```bash
PROXFY_IP=192.168.1.50/24 PROXFY_GW=192.168.1.1 PROXFY_UNATTENDED=1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

Vorher prüfen, ohne etwas anzulegen:

```bash
PROXFY_DRY_RUN=1 PROXFY_UNATTENDED=1 PROXFY_IP=192.168.1.50/24 PROXFY_GW=192.168.1.1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

Das zeigt die erkannten Vorgaben, ob die Container-ID frei ist und ob die
Adresse antwortet — und legt nichts an.

**Voraussetzungen:** Proxmox VE 8 oder 9, ein Storage mit Backup-Inhalt (PBS oder
ein Verzeichnis mit vzdump-Dateien), und im Container Internetzugang für die
Installation von Node.


### Aus dem geklonten Verzeichnis

```bash
git clone https://github.com/bonderaustria/proxfy.git
cd proxfy && bash proxfy.sh
```

### Deinstallation

```bash
bash /opt/proxfy/uninstall.sh
```

`--yes` überspringt die Rückfragen, `--keep-data` behält Konfiguration sowie
Verlaufs- und Benutzerdatenbank. Backups, Backup-Storage und produktive Gäste
werden nie angefasst.

---

## Aktualisieren

Denselben Befehl noch einmal ausführen:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

Das Skript erkennt die vorhandene Installation — es sucht den Container, in dem
`/opt/proxfy/config.yaml` liegt — und aktualisiert nur diese. Es fragt weder
nach Adresse noch nach Ressourcen und legt keinen zweiten Container an.

Erneuert wird ausschließlich der Programmcode samt Oberfläche. Unangetastet
bleiben:

| Datei | Inhalt |
|---|---|
| `config.yaml` | Hypervisor, Storages, Bridges, Zeitschranken |
| `auth.env` | Geheimnisse des Anmeldedienstes |
| `auth.db` | Konten, Passwörter, zweiter Faktor, Sitzungen |
| `proxfy.db` | Zeitpläne, Verlauf, Testgäste, IP-Vorrat, Einstellungen |

Vor jeder Aktualisierung werden diese vier nach
`/opt/proxfy-sicherung/<zeitstempel>/` im Container kopiert; die letzten fünf
Stände bleiben liegen. Zurück geht es damit durch schlichtes Zurückkopieren und
`systemctl restart proxfy proxfy-auth`.

Vorher ansehen, was geschehen würde:

```bash
PROXFY_DRY_RUN=1 PROXFY_UNATTENDED=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```

Soll trotz vorhandener Installation eine zweite, unabhängige entstehen:

```bash
PROXFY_NEU=1 PROXFY_IP=192.168.1.51/24 PROXFY_GW=192.168.1.1 \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
```
## Aufbau

Proxfy läuft in einem eigenen LXC, nicht auf dem Hypervisor — dort ist Python
„externally managed", und Fremdsoftware gehört nicht auf den Hypervisor selbst.

```
   Browser
      │  Port 8099  (einzige Tür nach außen)
      ▼
 ┌─────────────────────────────────────────┐
 │  LXC "proxfy"                           │
 │                                         │
 │   Python  ──── prüft jede Anfrage ────► │
 │   :8099          Node + Better Auth     │
 │                  127.0.0.1:8100         │
 │                  (nur Loopback)         │
 └──────────────┬──────────────────────────┘
                │ SSH mit Schlüssel
                ▼
        Proxmox VE (pct, qm, pvesm)
```

---

## Ablauf eines Laufs

```
Backup wählen  →  Netzwerk planen (Preflight)  →  Wiederherstellen
      →  Netzwerk vereinzeln  →  ISOLIERT starten  →  Prüfungen innen
      →  [nur geroutet] IP vergeben, ins LAN umhängen  →  Prüfungen außen
      →  Lebensdauer-Richtlinie anwenden
```

Ein LXC ist damit in rund 30 Sekunden geprüft, eine 27-GB-VM in gut zwei Minuten.

---

## Sicherheit

Dies ist der Teil, der über Nutzen oder Schaden entscheidet.

### Der Testgast bekommt genau eine Netzwerkkarte

Ein wiederhergestellter Gast trägt **alle** Netzwerkkarten des Originals. Ein
DNS-Server oder Reverse-Proxy hängt schnell mit sechs Karten in sechs VLANs, jede
mit einer statischen Produktiv-IP. Würde man nur `net0` umschreiben, stünde der
Testgast über die übrigen Karten mit den Original-Adressen im Netz und
kollidierte mit dem laufenden Original.

Deshalb werden vor dem ersten Start **alle** Karten außer `net0` gelöscht und
`net0` neu gesetzt. Bleibt danach eine Karte übrig, bricht der Lauf ab.

### Kein Live-Restore

`qmrestore --live-restore` startet die VM als Teil des Restore-Befehls, mit der
Netzwerkkonfiguration aus dem Backup. Es gibt kein Zeitfenster, in dem sich die
Karten vorher korrigieren ließen — die VM stünde für die Dauer des Restores mit
den Original-Adressen im Produktivnetz. Das Verfahren ist nicht absicherbar und
wird nicht verwendet. Praktisch kostet der Verzicht nichts, weil die Prüfungen
ohnehin erst nach Abschluss des Restores anliefen.

### Die zwei Netzwerkmodi

**`isolated`** (Standard) — Bridge ohne Uplink, zur Laufzeit angelegt. Der Gast
kann physisch nichts erreichen. Prüfungen laufen von innen über den
QEMU-Guest-Agent bzw. `pct exec`.

**`routed`** — der Gast bekommt eine Adresse aus dem hinterlegten Vorrat, damit
sich Dienste so prüfen lassen, wie ein Client sie sieht. Abgesichert durch:

| Schutz | Wirkung |
|---|---|
| **IP-Preflight** | Vier unabhängige Proben: ARP-Duplikatsprüfung, ICMP, Nachbarschaftstabelle, Gast-Konfigurationen. Ein Treffer bricht ab — es wird nicht geraten. |
| **Belegte Adressen** | Adressen laufender Testgäste sind gesperrt. |
| **Frische MAC** | Immer neu erzeugt, lokal administriert. DHCP-Reservierungen des Originals greifen nicht. |
| **Isolierter Erststart** | Der Gast trägt die Original-Adressen, bis sie überschrieben sind. In dieser Phase hat er keinen Netzwerkpfad. |
| **Zwei-Stufen-Übergang** | Erst IP setzen, dann Bridge wechseln. Nie umgekehrt. |

> Der Preflight ist nur im lokalen Segment zuverlässig. Eine Adresse aus einem
> anderen VLAN lässt sich nicht per ARP prüfen — dort bleiben ICMP und die
> Konfigurationssuche als schwächere Proben.

### Weitere Invarianten

- **Scratch-Bereich 9000–9099.** Testgäste entstehen nur dort. Jede zerstörende
  Aktion prüft das erneut. Der Bereich gehört ausschließlich Proxfy.
- **Laufende Wiederherstellungen sind tabu.** Ein Gast, der als „stopped" mit
  0 GB dasteht, ist nicht zwangsläufig verwaist — genau so sieht ein laufender
  `qmrestore` aus. Vor jedem Vernichten wird auf laufende Restore-Prozesse
  geprüft.
- **Unverwechselbarer Name.** Der Testgast heißt `proxfy-<original>`, nie wie das
  Original, und trägt das Tag `proxfy-test`.
- **Aufräumen im `finally`.** Beim Dienststart werden zusätzlich Reste eines
  abgestürzten Laufs entfernt, auch solche mit `lock=create`.

---

## Prüfungen

Prüfungen werden als Zeilen bearbeitet: Typ auswählen, passende Felder
ausfüllen, Schalter für „von außen" und „Pflicht". Die JSON-Ansicht bleibt als
Expertenmodus erhalten.

| Typ | Pflichtfelder | Zweck |
|---|---|---|
| `boot` | — | Gast antwortet überhaupt (läuft immer zuerst) |
| `service` | `unit` | systemd-Dienst ist `active` |
| `port` | `port` | TCP-Port lauscht |
| `http` | `url` | Statuscode, optional `expect_body` als Regex |
| `tls` | — | TLS-Handschlag und Restlaufzeit, `port`, `min_days` |
| `command` | `run` oder `argv` | Exitcode, optional `expect_output` als Regex |
| `file` | `path` | Datei vorhanden, optional `min_bytes` |
| `newest_file` | `path` | Alter der jüngsten Datei, `max_age_hours` |
| `file_count` | `path` | Dateianzahl, `min_count`, `pattern` |
| `postgres` / `mysql` | — | echte Abfrage, optional `expect` |
| `db_fresh` | `query` | Alter des jüngsten Datensatzes, `max_age_hours` |

Zusatzfelder: `external: true` prüft vom Host aus gegen die vergebene Adresse
(setzt `routed` voraus), `required: false` zählt nicht gegen das Gesamtergebnis.

### Der Testgast als Werkbank

Zwei Funktionen setzen einen **laufenden** Testgast voraus — also einen Lauf mit
der Lebensdauer „Zeitfenster" oder „stehen lassen". Beide greifen ausschließlich
auf Testgäste zu, nie auf produktive VMs oder Container.

**Aus Testgast erkennen** untersucht den laufenden Testgast und schlägt fertige
Prüfungen vor: laufende systemd-Dienste ohne die Grundausstattung, lauschende
Ports samt Prozessnamen, Docker-Container, erkannte Datenbanken. Erkannt wird
damit, was tatsächlich **im Backup** steckt.

**Probelauf** (▶ an jeder Zeile) führt eine einzelne Prüfung sofort gegen den
laufenden Testgast aus, ohne neuen Restore.

> Vorsicht bei `isolated`: Dienste, die beim Start Daten aus dem Internet ziehen,
> scheitern ohne Netzwerk. paperless-ngx etwa lädt über `uv run` bei jedem Start
> ein Wheel nach und bricht in der Isolation mit einem DNS-Fehler ab. Für solche
> Gäste ist `routed` nötig, oder die Prüfung wird auf `required: false` gesetzt.

---

## Lebensdauer des Testgastes

| Richtlinie | Verhalten |
|---|---|
| `destroy` | sofort nach den Prüfungen vernichten. Richtig für den Automatiklauf. |
| `ttl` | bleibt N Minuten stehen, danach automatisch entfernt |
| `manual` | bleibt stehen, bis jemand ihn unter „Testgäste" entfernt |

Scheitert ein Lauf, **bevor** der Gast lief, wird immer vernichtet — es gäbe
nichts zu untersuchen. Scheitert er danach, greift die Richtlinie: einen
durchgefallenen Gast will man sich ansehen.

---

## Test-Adressen

Einzelne Adressen und Bereiche:

```
192.168.1.240                 einzeln
192.168.1.240/24              einzeln mit Präfix
192.168.1.15-38               Bereich, Kurzform
192.168.1.15-192.168.1.38     Bereich, vollständig
```

Ein Bereich erlaubt **mehrere gleichzeitige Läufe** im Modus `routed` — jeder
Lauf nimmt die nächste freie Adresse und gibt sie danach zurück. Mit einer
einzelnen Adresse ging immer nur ein Gast.

---

## Zeitpläne

Uhrzeit plus Wochentage, mit Mehrfachauswahl der Gäste. Die Gäste laufen
nacheinander durch die Warteschlange, nicht parallel — parallele Restores würden
sich um Storage-Bandbreite und Scratch-Slots streiten.

Jeder Zeitplan lässt sich vollständig nachträglich ändern und mit „Jetzt
ausführen" sofort auslösen. Die letzten Ausführungen stehen beim Zeitplan selbst.

---

## Benutzer und Rollen

Das **erste** angelegte Konto wird Super Admin. Danach ist die
Einrichtungsmaske dauerhaft geschlossen.

| | Super Admin | Admin | Benutzer |
|---|---|---|---|
| Verifikationen starten, Zeitpläne pflegen | ja | ja | ja |
| Eigenes Konto und eigener zweiter Faktor | ja | ja | ja |
| Benutzer anlegen und entfernen | alle | nur Benutzer | nein |
| Zwei-Faktor zurücksetzen | bei allen | nur bei Benutzern | nein |
| Rollen vergeben | ja | nein | nein |
| Anmeldeversuche, Sperren aufheben | ja | ja | nein |
| Einstellungen ändern | ja | nein | nein |

Der letzte Super Admin lässt sich weder löschen noch herabstufen — sonst könnte
niemand mehr Einstellungen ändern.

Geprüft wird **serverseitig** bei jedem Endpunkt. Was die Oberfläche ausblendet,
ist Bequemlichkeit, kein Schutz.

### Anmeldung

Sitzungen liegen serverseitig in SQLite. Der Browser hält nur ein
`HttpOnly`-Cookie — kein Token, kein JWT, nichts, was JavaScript auslesen könnte.
Jede Sitzung lässt sich sofort serverseitig beenden.

**Zwei-Faktor** (TOTP) je Konto aktivierbar, mit zehn Wiederherstellungscodes.
Vor dem zweiten Faktor entsteht **keine** Sitzung.

**Anmeldeversuche:**

| Versuche | Verhalten |
|---|---|
| 1–3 | ohne Verzögerung |
| 4, 5, 6+ | 2 s, 4 s, dann 8 s |
| ab 10 | gesperrt für 15 Minuten, auch bei richtigem Passwort |

Gezählt wird getrennt nach Herkunfts-IP und Benutzerkennung; es zählt der
schlechtere Wert. Abgewiesen wird, **bevor** der Anmeldedienst das Passwort zu
sehen bekommt. Die Schwellen sind änderbar.

---

## Einstellungen

Alles ist in der Oberfläche änderbar. Weil manche Werte aussperren können, sind
sie gestaffelt abgesichert:

| Gruppe | Absicherung |
|---|---|
| Standardwerte | keine — wirken beim nächsten Lauf |
| Proxmox-Anbindung | Verbindungsprüfung erzwungen, Speichern erst danach möglich |
| Zugriff & Netzwerk | Passwort nötig, danach zehn Minuten Rücknahme-Frist |
| Sperr-Schwellen | Passwort nötig |

Die Datenbank überlagert nur die Datei. Rettungsweg, falls die Oberfläche
unerreichbar wird:

```bash
python3 -m proxfy.cli config show
python3 -m proxfy.cli config reset
```

### Ins Internet stellen

1. **TLS davor**, etwa über einen Reverse Proxy.
2. In `auth.env` `PROXFY_SECURE_COOKIES=1` setzen.
3. In `auth.env` `BETTER_AUTH_URL` und `PROXFY_TRUSTED_ORIGINS` auf den
   öffentlichen Namen setzen.
4. `trust_forwarded_for` einschalten, damit die Sperre die echte Herkunftsadresse
   sieht. **Nur**, wenn wirklich ein Proxy davorsteht.
5. Zwei-Faktor für alle Konten aktivieren.

> Der Container hält einen SSH-Schlüssel mit Root-Rechten auf den Hypervisor.
> Wer den Container übernimmt, übernimmt Proxmox.

---

## Kommandozeile

```bash
python3 -m proxfy.cli snapshots --latest-only
python3 -m proxfy.cli run --vmid 118
python3 -m proxfy.cli run --vmid 118 --mode routed --ip 192.168.1.240/24 --gateway 192.168.1.1
python3 -m proxfy.cli reap --force
python3 -m proxfy.cli config show
```

Exitcode `0`, wenn alle Läufe verifiziert wurden.

---

## Grenzen

- **Ohne QEMU-Guest-Agent** ist bei VMs nur „bootet irgendwie" feststellbar.
  Container sind im Vorteil: `pct exec` funktioniert immer.
- **Der Preflight meldet auch Gateway-Adressen** als belegt, weil er die
  Gast-Konfigurationen textuell durchsucht. Konservativ, aber gelegentlich ein
  Fehlalarm.
- **Ein Lauf gleichzeitig.** Mehrfachauswahl reiht ein, statt zu parallelisieren.
- **Kein PDF-Bericht.** Die Daten liegen vollständig in SQLite, nur die Ausgabe
  fehlt.

---

## Lizenz

MIT
