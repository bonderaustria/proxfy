# Proxfy

**PrÃ¼ft, ob sich eure Proxmox-Backups wirklich wiederherstellen lassen â und ob
danach die Anwendung lÃ¤uft.**

Proxmox Backup Server prÃ¼ft Chunk-PrÃ¼fsummen. Das belegt, dass die Bytes
unversehrt sind. Es belegt nicht, dass daraus eine funktionierende Maschine
wird. Proxfy stellt ein Backup unter einer Wegwerf-VMID wieder her, startet es
abgeschottet, fÃ¼hrt echte FunktionsprÃ¼fungen aus â Dienst aktiv, Port lauscht,
WeboberflÃ¤che antwortet, Datenbank liefert einen aktuellen Datensatz â und rÃ¤umt
danach auf.

Funktioniert fÃ¼r **VMs und LXC-Container** gleichermaÃen.

> **Es lÃ¤uft nichts von selbst.** Kein eingebauter Zeitplan, kein Cron-Eintrag.
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
installiert alles hinein. Danach ist die OberflÃ¤che unter `http://<adresse>:8099/`
erreichbar und wartet auf das erste Konto.

Ohne RÃ¼ckfragen:

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
ein Verzeichnis mit vzdump-Dateien), und im Container Internetzugang fÃ¼r die
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

`--yes` Ã¼berspringt die RÃ¼ckfragen, `--keep-data` behÃ¤lt Konfiguration sowie
Verlaufs- und Benutzerdatenbank. Backups, Backup-Storage und produktive GÃ¤ste
werden nie angefasst.

---

## Aufbau

Proxfy lÃ¤uft in einem eigenen LXC, nicht auf dem Hypervisor â dort ist Python
âexternally managed", und Fremdsoftware gehÃ¶rt nicht auf den Hypervisor selbst.

```
   Browser
      â  Port 8099  (einzige TÃ¼r nach auÃen)
      â¼
 âââââââââââââââââââââââââââââââââââââââââââ
 â  LXC "proxfy"                           â
 â                                         â
 â   Python  ââââ prÃ¼ft jede Anfrage âââââº â
 â   :8099          Node + Better Auth     â
 â                  127.0.0.1:8100         â
 â                  (nur Loopback)         â
 ââââââââââââââââ¬âââââââââââââââââââââââââââ
                â SSH mit SchlÃ¼ssel
                â¼
        Proxmox VE (pct, qm, pvesm)
```

---

## Ablauf eines Laufs

```
Backup wÃ¤hlen  â  Netzwerk planen (Preflight)  â  Wiederherstellen
      â  Netzwerk vereinzeln  â  ISOLIERT starten  â  PrÃ¼fungen innen
      â  [nur geroutet] IP vergeben, ins LAN umhÃ¤ngen  â  PrÃ¼fungen auÃen
      â  Lebensdauer-Richtlinie anwenden
```

Ein LXC ist damit in rund 30 Sekunden geprÃ¼ft, eine 27-GB-VM in gut zwei Minuten.

---

## Sicherheit

Dies ist der Teil, der Ã¼ber Nutzen oder Schaden entscheidet.

### Der Testgast bekommt genau eine Netzwerkkarte

Ein wiederhergestellter Gast trÃ¤gt **alle** Netzwerkkarten des Originals. Ein
DNS-Server oder Reverse-Proxy hÃ¤ngt schnell mit sechs Karten in sechs VLANs, jede
mit einer statischen Produktiv-IP. WÃ¼rde man nur `net0` umschreiben, stÃ¼nde der
Testgast Ã¼ber die Ã¼brigen Karten mit den Original-Adressen im Netz und
kollidierte mit dem laufenden Original.

Deshalb werden vor dem ersten Start **alle** Karten auÃer `net0` gelÃ¶scht und
`net0` neu gesetzt. Bleibt danach eine Karte Ã¼brig, bricht der Lauf ab.

### Kein Live-Restore

`qmrestore --live-restore` startet die VM als Teil des Restore-Befehls, mit der
Netzwerkkonfiguration aus dem Backup. Es gibt kein Zeitfenster, in dem sich die
Karten vorher korrigieren lieÃen â die VM stÃ¼nde fÃ¼r die Dauer des Restores mit
den Original-Adressen im Produktivnetz. Das Verfahren ist nicht absicherbar und
wird nicht verwendet. Praktisch kostet der Verzicht nichts, weil die PrÃ¼fungen
ohnehin erst nach Abschluss des Restores anliefen.

### Die zwei Netzwerkmodi

**`isolated`** (Standard) â Bridge ohne Uplink, zur Laufzeit angelegt. Der Gast
kann physisch nichts erreichen. PrÃ¼fungen laufen von innen Ã¼ber den
QEMU-Guest-Agent bzw. `pct exec`.

**`routed`** â der Gast bekommt eine Adresse aus dem hinterlegten Vorrat, damit
sich Dienste so prÃ¼fen lassen, wie ein Client sie sieht. Abgesichert durch:

| Schutz | Wirkung |
|---|---|
| **IP-Preflight** | Vier unabhÃ¤ngige Proben: ARP-DuplikatsprÃ¼fung, ICMP, Nachbarschaftstabelle, Gast-Konfigurationen. Ein Treffer bricht ab â es wird nicht geraten. |
| **Belegte Adressen** | Adressen laufender TestgÃ¤ste sind gesperrt. |
| **Frische MAC** | Immer neu erzeugt, lokal administriert. DHCP-Reservierungen des Originals greifen nicht. |
| **Isolierter Erststart** | Der Gast trÃ¤gt die Original-Adressen, bis sie Ã¼berschrieben sind. In dieser Phase hat er keinen Netzwerkpfad. |
| **Zwei-Stufen-Ãbergang** | Erst IP setzen, dann Bridge wechseln. Nie umgekehrt. |

> Der Preflight ist nur im lokalen Segment zuverlÃ¤ssig. Eine Adresse aus einem
> anderen VLAN lÃ¤sst sich nicht per ARP prÃ¼fen â dort bleiben ICMP und die
> Konfigurationssuche als schwÃ¤chere Proben.

### Weitere Invarianten

- **Scratch-Bereich 9000â9099.** TestgÃ¤ste entstehen nur dort. Jede zerstÃ¶rende
  Aktion prÃ¼ft das erneut. Der Bereich gehÃ¶rt ausschlieÃlich Proxfy.
- **Laufende Wiederherstellungen sind tabu.** Ein Gast, der als âstopped" mit
  0 GB dasteht, ist nicht zwangslÃ¤ufig verwaist â genau so sieht ein laufender
  `qmrestore` aus. Vor jedem Vernichten wird auf laufende Restore-Prozesse
  geprÃ¼ft.
- **Unverwechselbarer Name.** Der Testgast heiÃt `proxfy-<original>`, nie wie das
  Original, und trÃ¤gt das Tag `proxfy-test`.
- **AufrÃ¤umen im `finally`.** Beim Dienststart werden zusÃ¤tzlich Reste eines
  abgestÃ¼rzten Laufs entfernt, auch solche mit `lock=create`.

---

## PrÃ¼fungen

PrÃ¼fungen werden als Zeilen bearbeitet: Typ auswÃ¤hlen, passende Felder
ausfÃ¼llen, Schalter fÃ¼r âvon auÃen" und âPflicht". Die JSON-Ansicht bleibt als
Expertenmodus erhalten.

| Typ | Pflichtfelder | Zweck |
|---|---|---|
| `boot` | â | Gast antwortet Ã¼berhaupt (lÃ¤uft immer zuerst) |
| `service` | `unit` | systemd-Dienst ist `active` |
| `port` | `port` | TCP-Port lauscht |
| `http` | `url` | Statuscode, optional `expect_body` als Regex |
| `tls` | â | TLS-Handschlag und Restlaufzeit, `port`, `min_days` |
| `command` | `run` oder `argv` | Exitcode, optional `expect_output` als Regex |
| `file` | `path` | Datei vorhanden, optional `min_bytes` |
| `newest_file` | `path` | Alter der jÃ¼ngsten Datei, `max_age_hours` |
| `file_count` | `path` | Dateianzahl, `min_count`, `pattern` |
| `postgres` / `mysql` | â | echte Abfrage, optional `expect` |
| `db_fresh` | `query` | Alter des jÃ¼ngsten Datensatzes, `max_age_hours` |

Zusatzfelder: `external: true` prÃ¼ft vom Host aus gegen die vergebene Adresse
(setzt `routed` voraus), `required: false` zÃ¤hlt nicht gegen das Gesamtergebnis.

### Der Testgast als Werkbank

Zwei Funktionen setzen einen **laufenden** Testgast voraus â also einen Lauf mit
der Lebensdauer âZeitfenster" oder âstehen lassen". Beide greifen ausschlieÃlich
auf TestgÃ¤ste zu, nie auf produktive VMs oder Container.

**Aus Testgast erkennen** untersucht den laufenden Testgast und schlÃ¤gt fertige
PrÃ¼fungen vor: laufende systemd-Dienste ohne die Grundausstattung, lauschende
Ports samt Prozessnamen, Docker-Container, erkannte Datenbanken. Erkannt wird
damit, was tatsÃ¤chlich **im Backup** steckt.

**Probelauf** (â¶ an jeder Zeile) fÃ¼hrt eine einzelne PrÃ¼fung sofort gegen den
laufenden Testgast aus, ohne neuen Restore.

> Vorsicht bei `isolated`: Dienste, die beim Start Daten aus dem Internet ziehen,
> scheitern ohne Netzwerk. paperless-ngx etwa lÃ¤dt Ã¼ber `uv run` bei jedem Start
> ein Wheel nach und bricht in der Isolation mit einem DNS-Fehler ab. FÃ¼r solche
> GÃ¤ste ist `routed` nÃ¶tig, oder die PrÃ¼fung wird auf `required: false` gesetzt.

---

## Lebensdauer des Testgastes

| Richtlinie | Verhalten |
|---|---|
| `destroy` | sofort nach den PrÃ¼fungen vernichten. Richtig fÃ¼r den Automatiklauf. |
| `ttl` | bleibt N Minuten stehen, danach automatisch entfernt |
| `manual` | bleibt stehen, bis jemand ihn unter âTestgÃ¤ste" entfernt |

Scheitert ein Lauf, **bevor** der Gast lief, wird immer vernichtet â es gÃ¤be
nichts zu untersuchen. Scheitert er danach, greift die Richtlinie: einen
durchgefallenen Gast will man sich ansehen.

---

## Test-Adressen

Einzelne Adressen und Bereiche:

```
192.168.1.240                 einzeln
192.168.1.240/24              einzeln mit PrÃ¤fix
192.168.1.15-38               Bereich, Kurzform
192.168.1.15-192.168.1.38     Bereich, vollstÃ¤ndig
```

Ein Bereich erlaubt **mehrere gleichzeitige LÃ¤ufe** im Modus `routed` â jeder
Lauf nimmt die nÃ¤chste freie Adresse und gibt sie danach zurÃ¼ck. Mit einer
einzelnen Adresse ging immer nur ein Gast.

---

## ZeitplÃ¤ne

Uhrzeit plus Wochentage, mit Mehrfachauswahl der GÃ¤ste. Die GÃ¤ste laufen
nacheinander durch die Warteschlange, nicht parallel â parallele Restores wÃ¼rden
sich um Storage-Bandbreite und Scratch-Slots streiten.

Jeder Zeitplan lÃ¤sst sich vollstÃ¤ndig nachtrÃ¤glich Ã¤ndern und mit âJetzt
ausfÃ¼hren" sofort auslÃ¶sen. Die letzten AusfÃ¼hrungen stehen beim Zeitplan selbst.

---

## Benutzer und Rollen

Das **erste** angelegte Konto wird Super Admin. Danach ist die
Einrichtungsmaske dauerhaft geschlossen.

| | Super Admin | Admin | Benutzer |
|---|---|---|---|
| Verifikationen starten, ZeitplÃ¤ne pflegen | ja | ja | ja |
| Eigenes Konto und eigener zweiter Faktor | ja | ja | ja |
| Benutzer anlegen und entfernen | alle | nur Benutzer | nein |
| Zwei-Faktor zurÃ¼cksetzen | bei allen | nur bei Benutzern | nein |
| Rollen vergeben | ja | nein | nein |
| Anmeldeversuche, Sperren aufheben | ja | ja | nein |
| Einstellungen Ã¤ndern | ja | nein | nein |

Der letzte Super Admin lÃ¤sst sich weder lÃ¶schen noch herabstufen â sonst kÃ¶nnte
niemand mehr Einstellungen Ã¤ndern.

GeprÃ¼ft wird **serverseitig** bei jedem Endpunkt. Was die OberflÃ¤che ausblendet,
ist Bequemlichkeit, kein Schutz.

### Anmeldung

Sitzungen liegen serverseitig in SQLite. Der Browser hÃ¤lt nur ein
`HttpOnly`-Cookie â kein Token, kein JWT, nichts, was JavaScript auslesen kÃ¶nnte.
Jede Sitzung lÃ¤sst sich sofort serverseitig beenden.

**Zwei-Faktor** (TOTP) je Konto aktivierbar, mit zehn Wiederherstellungscodes.
Vor dem zweiten Faktor entsteht **keine** Sitzung.

**Anmeldeversuche:**

| Versuche | Verhalten |
|---|---|
| 1â3 | ohne VerzÃ¶gerung |
| 4, 5, 6+ | 2 s, 4 s, dann 8 s |
| ab 10 | gesperrt fÃ¼r 15 Minuten, auch bei richtigem Passwort |

GezÃ¤hlt wird getrennt nach Herkunfts-IP und Benutzerkennung; es zÃ¤hlt der
schlechtere Wert. Abgewiesen wird, **bevor** der Anmeldedienst das Passwort zu
sehen bekommt. Die Schwellen sind Ã¤nderbar.

---

## Einstellungen

Alles ist in der OberflÃ¤che Ã¤nderbar. Weil manche Werte aussperren kÃ¶nnen, sind
sie gestaffelt abgesichert:

| Gruppe | Absicherung |
|---|---|
| Standardwerte | keine â wirken beim nÃ¤chsten Lauf |
| Proxmox-Anbindung | VerbindungsprÃ¼fung erzwungen, Speichern erst danach mÃ¶glich |
| Zugriff & Netzwerk | Passwort nÃ¶tig, danach zehn Minuten RÃ¼cknahme-Frist |
| Sperr-Schwellen | Passwort nÃ¶tig |

Die Datenbank Ã¼berlagert nur die Datei. Rettungsweg, falls die OberflÃ¤che
unerreichbar wird:

```bash
python3 -m proxfy.cli config show
python3 -m proxfy.cli config reset
```

### Ins Internet stellen

1. **TLS davor**, etwa Ã¼ber einen Reverse Proxy.
2. In `auth.env` `PROXFY_SECURE_COOKIES=1` setzen.
3. In `auth.env` `BETTER_AUTH_URL` und `PROXFY_TRUSTED_ORIGINS` auf den
   Ã¶ffentlichen Namen setzen.
4. `trust_forwarded_for` einschalten, damit die Sperre die echte Herkunftsadresse
   sieht. **Nur**, wenn wirklich ein Proxy davorsteht.
5. Zwei-Faktor fÃ¼r alle Konten aktivieren.

> Der Container hÃ¤lt einen SSH-SchlÃ¼ssel mit Root-Rechten auf den Hypervisor.
> Wer den Container Ã¼bernimmt, Ã¼bernimmt Proxmox.

---

## Kommandozeile

```bash
python3 -m proxfy.cli snapshots --latest-only
python3 -m proxfy.cli run --vmid 118
python3 -m proxfy.cli run --vmid 118 --mode routed --ip 192.168.1.240/24 --gateway 192.168.1.1
python3 -m proxfy.cli reap --force
python3 -m proxfy.cli config show
```

Exitcode `0`, wenn alle LÃ¤ufe verifiziert wurden.

---

## Grenzen

- **Ohne QEMU-Guest-Agent** ist bei VMs nur âbootet irgendwie" feststellbar.
  Container sind im Vorteil: `pct exec` funktioniert immer.
- **Der Preflight meldet auch Gateway-Adressen** als belegt, weil er die
  Gast-Konfigurationen textuell durchsucht. Konservativ, aber gelegentlich ein
  Fehlalarm.
- **Ein Lauf gleichzeitig.** Mehrfachauswahl reiht ein, statt zu parallelisieren.
- **Kein PDF-Bericht.** Die Daten liegen vollstÃ¤ndig in SQLite, nur die Ausgabe
  fehlt.

---

## Lizenz

MIT
