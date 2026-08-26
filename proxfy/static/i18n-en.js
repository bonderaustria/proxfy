"use strict";
// Englische Fassung der Texte aus dem Markup.
//
// Schluessel ist der deutsche Text, genau so, wie er in index.html und
// login.html steht. Aendert sich dort ein Wort, greift der Eintrag nicht mehr
// und es erscheint wieder Deutsch - sichtbar und behebbar.
//
// Nicht uebersetzt und deshalb nicht aufgefuehrt: Eigennamen (Proxfy, Proxmox,
// VMID, JSON, 2FA), Feldnamen fremder Oberflaechen (Domain Names, Force SSL),
// Befehle und Konfigurationsblöcke.

Object.assign(EN, {
  // --- Kopfzeile und Navigation ---
  "Restore-Verifikation für Proxmox Backup Server": "Restore verification for Proxmox Backup Server",
  "Restore-Verifikation für Proxmox Backup Server.": "Restore verification for Proxmox Backup Server.",
  "Neu laden": "Reload",
  "Abmelden": "Sign out",
  "Prüfen": "Verify",
  "Testgäste": "Test guests",
  "Zeitpläne": "Schedules",
  "Verlauf": "History",
  "Einstellungen": "Settings",
  "Konto": "Account",

  // --- Gastliste ---
  "Gäste": "Guests",
  "0 ausgewählt": "0 selected",
  "nur mit Backup": "with backup only",
  "Name": "Name",
  "Typ": "Type",
  "Neuestes Backup": "Latest backup",
  "Größe": "Size",
  "Letzte Prüfung": "Last verified",
  "lade …": "loading …",

  // --- Lauf ---
  "Live-Protokoll": "Live log",
  "bereit. Es läuft nichts ohne ausdrücklichen Auftrag.": "ready. Nothing runs without an explicit request.",
  "Lauf konfigurieren": "Configure run",
  "nichts gewählt": "nothing selected",
  "Cluster-Knoten": "Cluster node",
  "Backup-Quelle": "Backup source",
  "Ziel-Storage für den Testgast": "Target storage for the test guest",
  "Backup-Stand": "Backup snapshot",
  "neuestes verwenden": "use latest",
  "Netzwerkmodus": "Network mode",
  "isoliert — Bridge ohne Uplink (empfohlen)": "isolated — bridge without uplink (recommended)",
  "geroutet — echte IP im LAN": "routed — real IP on the LAN",
  "Adresse": "Address",
  "Gateway": "Gateway",
  "IP jetzt prüfen": "Check IP now",
  "Der Gast startet immer zuerst isoliert. Erst nachdem die IP im Gast gesetzt ist, wird die Netzwerkkarte ins LAN umgehängt — die Original-VM bleibt unberührt.":
    "The guest always starts isolated. Only once the IP is set inside the guest is the network card moved onto the LAN — the original VM is never touched.",
  "Lebensdauer des Testgastes": "Lifetime of the test guest",
  "sofort vernichten (nach den Prüfungen)": "destroy immediately (after the checks)",
  "Zeitfenster — danach automatisch entfernen": "time window — removed automatically afterwards",
  "stehen lassen, bis ich ihn entferne": "keep until I remove it",
  "Minuten": "minutes",
  "Prüfungen": "Checks",
  "Aus Testgast erkennen": "Discover from test guest",
  "+ Prüfung": "+ Check",
  "Der Probelauf": "A trial run",
  "führt eine Prüfung sofort gegen einen laufenden Testgast aus — ohne neuen Restore.":
    "runs a check straight against a running test guest — without restoring again.",
  "Verifikation starten": "Start verification",
  "Als Zeitplan anlegen": "Save as schedule",

  // --- Laufende Testgaeste ---
  "Laufende Testgäste": "Running test guests",
  "Aktualisieren": "Refresh",
  "Scratch": "Scratch",
  "Quelle": "Source",
  "Modus": "Mode",
  "Lebensdauer": "Lifetime",
  "Läuft ab": "Expires",
  "Testgäste mit der Richtlinie „stehen lassen“ verschwinden": "Test guests set to “keep” disappear",
  "nur": "only",
  "durch „Entfernen“. Sie belegen eine Scratch-VMID und, im Modus geroutet, eine IP-Adresse.":
    "when you remove them. They hold a scratch VMID and, in routed mode, an IP address.",

  // --- Zeitplaene ---
  "Angelegte Zeitpläne": "Configured schedules",
  "+ Neuer Zeitplan": "+ New schedule",
  "Ohne Zeitplan läuft": "Without a schedule",
  "nichts": "nothing",
  "von selbst. Es gibt keine eingebaute tägliche Prüfung.": "runs by itself. There is no built-in daily check.",
  "Neuer Zeitplan": "New schedule",
  "Uhrzeit": "Time of day",
  "sofort vernichten": "destroy immediately",
  "Zeitfenster": "time window",
  "stehen lassen": "keep",
  "Wochentage": "Weekdays",
  "isoliert — Bridge ohne Uplink": "isolated — bridge without uplink",
  "geroutet — Adressen aus dem Vorrat": "routed — addresses from the pool",
  "Adress-Eintrag": "Address entry",
  "Bei mehreren Gästen muss es ein Bereich sein — jeder Lauf nimmt die nächste freie Adresse.":
    "With more than one guest it has to be a range — each run takes the next free address.",
  "Gäste (Mehrfachauswahl)": "Guests (multiple selection)",
  "Die Gäste laufen nacheinander durch die Warteschlange, nicht gleichzeitig.":
    "The guests go through the queue one after another, not at the same time.",
  "Prüfungen für alle Gäste dieses Zeitplans": "Checks for every guest in this schedule",
  "Speichern": "Save",
  "Jetzt ausführen": "Run now",
  "Deaktivieren": "Disable",
  "Löschen": "Delete",
  "Letzte Ausführungen dieses Zeitplans": "Recent runs of this schedule",

  // --- Verlauf ---
  "Zeit": "Time",
  "Gast": "Guest",
  "Ergebnis": "Result",
  "Dauer": "Duration",
  "Backup": "Backup",
  "Ausgelöst durch": "Triggered by",

  // --- Einstellungen: Seitenleiste ---
  "Wie Proxfy arbeitet": "How Proxfy works",
  "Proxmox-Anbindung": "Proxmox connection",
  "Standardwerte": "Defaults",
  "Test-Adressen": "Test addresses",
  "Zugriff & Netzwerk": "Access & network",
  "Über Proxfy": "About Proxfy",

  // --- Einstellungen: Proxmox ---
  "Verbindung zu Proxmox": "Connection to Proxmox",
  "Proxfy läuft in einem eigenen Container und spricht über SSH mit dem Hypervisor. Diese Angaben entscheiden, ob es arbeiten kann.":
    "Proxfy runs in its own container and talks to the hypervisor over SSH. These values decide whether it can work at all.",
  "Adresse des Hypervisors": "Address of the hypervisor",
  "Benutzer": "User",
  "Privater Schlüssel": "Private key",
  "Verbindung prüfen": "Test connection",
  "Speichern wird erst möglich, wenn die Prüfung erfolgreich war.": "Saving is only possible once the test has succeeded.",
  "Eine falsche Adresse oder ein falscher Schlüsselpfad sperrt Proxfy vom Hypervisor aus. Deshalb wird die Prüfung erzwungen. Rettungsweg im Container:":
    "A wrong address or key path locks Proxfy out of the hypervisor. That is why the test is mandatory. Way back in, from inside the container:",

  // --- Einstellungen: Standardwerte ---
  "Vorgaben für jeden Lauf": "Defaults for every run",
  "Diese Werte sind beim Start eines Laufs vorausgewählt und lassen sich dort je Lauf noch ändern.":
    "These values are preselected when a run starts and can still be changed there, per run.",
  "Ziel-Storage": "Target storage",
  "Isolierte Bridge": "Isolated bridge",
  "LAN-Bridge": "LAN bridge",
  "Wartezeit auf den Gast (s)": "Wait for the guest (s)",
  "Wartezeit auf den Guest-Agent (s)": "Wait for the guest agent (s)",
  "Scratch-Bereich": "Scratch range",
  "Voreingestellte Lebensdauer": "Default lifetime",
  "Länge des Zeitfensters (Minuten)": "Length of the time window (minutes)",
  "Verwerfen": "Discard",

  // --- Einstellungen: Test-Adressen ---
  "Hinterlegte Test-Adressen": "Stored test addresses",
  "Bezeichnung": "Label",
  "Notiz": "Note",
  "Einzelne Adresse": "Single address",
  ", Bereich": ", range",
  "oder": "or",
  ". Ohne Angabe wird": ". Without one,",
  "ergänzt. Ein Bereich erlaubt mehrere gleichzeitige Läufe im Modus geroutet.":
    "is assumed. A range allows several routed runs at the same time.",
  "Adresse speichern": "Save address",
  "Vorher prüfen": "Check first",

  // --- Einstellungen: Zugriff und Netzwerk ---
  "Zugriff und Netzwerk": "Access and network",
  "Port der Weboberfläche": "Port of the web interface",
  "Änderbar in der systemd-Unit; ein Portwechsel verlangt ohnehin einen Neustart des Dienstes.":
    "Changeable in the systemd unit; changing the port needs a service restart anyway.",
  "Anmeldedienst": "Login service",
  "Adresse von außen (Reverse Proxy)": "Address from outside (reverse proxy)",
  "Die Adresse, unter der der Browser Proxfy sieht. Steht ein Proxy davor, kommt jede Anfrage mit dessen Herkunft an — ohne diesen Eintrag lehnt der Anmeldedienst sie mit":
    "The address the browser sees Proxfy under. With a proxy in front, every request arrives carrying its origin — without this entry the login service rejects them with",
  "ab. Leer lassen, wenn Proxfy direkt aufgerufen wird. Beginnt der Eintrag mit":
    ". Leave empty when Proxfy is called directly. If the entry starts with",
  ", gibt der Browser das Sitzungscookie nur noch verschlüsselt heraus — die Anmeldung läuft dann":
    ", the browser only hands out the session cookie over an encrypted connection — signing in then works",
  "ausschließlich": "only",
  "über den Proxy, nicht mehr über": "through the proxy, no longer over",
  "im LAN.": "on the LAN.",
  "Hinter einem Reverse Proxy betreiben": "Running behind a reverse proxy",
  "Proxfy glaubt dann der Kopfzeile": "Proxfy then trusts the header",
  ". Nur einschalten, wenn wirklich ein Proxy davorsteht — sonst kann sich jeder eine fremde Herkunftsadresse ausdenken und die Anmeldesperre umgehen.":
    ". Only switch this on when a proxy really is in front — otherwise anyone can invent an origin address and walk around the login lockout.",
  "Sitzungscookie nur über HTTPS": "Session cookie over HTTPS only",
  "Richtig, sobald Proxfy aus dem Internet erreichbar ist. Danach kommst du":
    "Right as soon as Proxfy is reachable from the internet. After that you get in",
  "nur noch über den Proxy": "only through the proxy",
  "hinein — der Aufruf über": "— calling it over",
  "im eigenen Netz nimmt das Cookie dann nicht mehr an.": "on your own network no longer accepts the cookie.",
  "Weg über den Proxy prüfen": "Test the path through the proxy",
  "Einstellungen für den Proxy anzeigen": "Show settings for the proxy",
  "Im Nginx Proxy Manager unter": "In Nginx Proxy Manager under",
  "an": "on",
  "Unter": "Under",
  "ein Zertifikat wählen und": "pick a certificate and switch on",
  "einschalten. Unter": ". Under",
  "gehört dieser Block hinein — ohne ihn bleibt das mitlaufende Protokoll eines Laufs stehen, weil der Proxy den Datenstrom sammelt statt ihn durchzureichen:":
    "this block belongs — without it the live log of a run stands still, because the proxy collects the stream instead of passing it through:",
  "Block kopieren": "Copy block",
  "Dein Passwort zur Bestätigung": "Your password, to confirm",
  "Diese Einstellung kann dich aussperren. Proxfy merkt sich deshalb den vorherigen Stand und stellt ihn wieder her, wenn du die Änderung nicht binnen zehn Minuten bestätigst.":
    "This setting can lock you out. Proxfy therefore remembers the previous state and restores it unless you confirm the change within ten minutes.",
  "Für den Internetbetrieb gehören außerdem TLS davor und": "For operation on the internet you also want TLS in front and",
  "in": "in",
  "— beides wirkt erst nach einem Neustart des Anmeldedienstes.": "— both take effect only after the login service restarts.",

  // --- Einstellungen: Ueber Proxfy ---
  "Zustand": "State",
  "Wartung": "Maintenance",
  "Verwaiste Testgäste suchen": "Look for orphaned test guests",
  "Verwaiste Testgäste räumt Proxfy beim Start ohnehin selbsttätig weg. Laufende Wiederherstellungen bleiben dabei unangetastet.":
    "Proxfy clears orphaned test guests away by itself at startup anyway. Restores in progress are left alone.",

  // --- Konto ---
  "Wer Proxfy benutzt": "Who uses Proxfy",
  "Mein Konto": "My account",
  "Anmeldeversuche": "Sign-in attempts",
  "Der zweite Faktor ist": "The second factor is",
  "nicht": "not",
  "aktiv. Wer Proxfy aus dem Internet erreichbar macht, sollte ihn einschalten.":
    "active. Anyone making Proxfy reachable from the internet should switch it on.",
  "Passwort zur Bestätigung": "Password, to confirm",
  "Zwei-Faktor einrichten": "Set up two-factor",
  "Scanne den Code mit deiner Authenticator-App und gib anschließend eine erzeugte Ziffernfolge ein.":
    "Scan the code with your authenticator app, then enter one of the generated numbers.",
  "Falls Scannen nicht geht — Schlüssel zum Abtippen": "If scanning does not work — key to type in",
  "Sechsstelliger Code aus der App": "Six-digit code from the app",
  "Aktivieren": "Activate",
  "Der zweite Faktor ist aktiv.": "The second factor is active.",
  "Zwei-Faktor abschalten": "Turn off two-factor",
  "E-Mail": "Email",
  "Rolle": "Role",
  "Passwort (mindestens 10 Zeichen)": "Password (at least 10 characters)",
  "Benutzer anlegen": "Create user",
  "gesperrt wird nach 10 Fehlversuchen für 15 Minuten": "locked after 10 failed attempts for 15 minutes",
  "Alle Sperren aufheben": "Lift all locks",
  "Herkunft": "Origin",
  "Kennung": "Identifier",
  "Wann gesperrt wird": "When it locks",
  "Bremse ab dem": "Slow down from attempt",
  "Fehlversuch. Danach 2, 4, 8 Sekunden.": "onwards. Then 2, 4, 8 seconds.",
  "Sperre ab dem": "Lock from attempt",
  "Fehlversuch.": "onwards.",
  "Dauer der Sperre": "Length of the lock",
  "Minuten.": "minutes.",
  "Gezählt wird getrennt nach Herkunftsadresse und nach Kennung; es zählt der schlechtere Wert. Während einer Sperre wird auch das richtige Passwort abgewiesen. Die Passwortabfrage verhindert, dass eine übernommene Sitzung die Sperre unwirksam macht.":
    "Counted separately by origin address and by identifier; the worse of the two applies. While a lock is active even the correct password is rejected. Asking for the password keeps a stolen session from undoing the lock.",

  // --- Kleinkram ---
  "Jetzt prüfen": "Check now",
  "Schließen": "Close",
  "Nächtliche Prüfung": "Nightly check",
  "Testplatz 1": "Test slot 1",
  "optional": "optional",
  "Vorname Nachname": "First name Last name",
  "kollege@example.org": "colleague@example.org",
  "du@example.org": "you@example.org",

  // --- Anmeldemaske ---
  "Anmeldung – Proxfy": "Sign in – Proxfy",
  "Ersteinrichtung": "First-time setup",
  "Es besteht noch kein Konto. Lege das erste an — danach ist dieser Weg dauerhaft geschlossen.":
    "No account exists yet. Create the first one — after that this way in is closed for good.",
  "Passwort wiederholen": "Repeat password",
  "Konto anlegen": "Create account",
  "Passwort": "Password",
  "Anmelden": "Sign in",
  "Zweiter Faktor": "Second factor",
  "Sechsstelliger Code aus deiner Authenticator-App.": "Six-digit code from your authenticator app.",
  "Bestätigen": "Confirm",
  "Stattdessen Wiederherstellungscode": "Use a recovery code instead",
  "Wiederherstellungscode": "Recovery code",
  "Einer der Codes, die bei der Einrichtung angezeigt wurden. Jeder Code gilt nur einmal.":
    "One of the codes shown during setup. Each code works once.",
  "Zurück zum Code": "Back to the code",
  "Sitzungen liegen serverseitig. Es wird nichts im Browser gespeichert.":
    "Sessions live on the server. Nothing is stored in the browser.",
});

// --- Was app.js zur Laufzeit erzeugt ----------------------------------------
Object.assign(EN, {
  // Zustand einer Pruefung
  "nie geprüft": "never verified",
  "verifiziert": "verified",
  "durchgefallen": "failed",
  "übersprungen": "skipped",
  "keine Einträge": "no entries",
  "keine Prüfungen": "no checks",
  "nichts gewählt": "nothing selected",
  "kein Testgast läuft gerade": "no test guest is running",
  "noch keine Läufe": "no runs yet",
  "kein Backup": "no backup",
  "manuell ausgelöst": "started by hand",
  "Läufe gesamt": "Runs in total",
  "Testgäste aktiv": "Test guests active",

  // Lauf konfigurieren
  "neuestes verwenden": "use latest",
  "Leer lassen heißt: das neueste Backup.": "Leaving it empty means the latest backup.",
  "Bei Mehrfachauswahl wird je Gast automatisch das neueste Backup genommen.":
    "With more than one guest selected, the latest backup is taken for each.",
  "geroutet — braucht einen Adressbereich": "routed — needs an address range",
  "Der Gast landet mit der angegebenen Adresse im echten Netz. Vor dem Start läuft ein Preflight, der abbricht, falls die Adresse belegt ist.":
    "The guest ends up on the real network under the given address. Before it starts, a preflight aborts if the address is taken.",
  "Der Gast hängt an einer Bridge ohne Uplink und kann nichts erreichen. Prüfungen laufen von innen.":
    "The guest sits on a bridge without uplink and can reach nothing. Checks run from inside.",
  "Der Testgast verschwindet direkt nach den Prüfungen. Richtig für den Automatiklauf.":
    "The test guest disappears right after the checks. The right choice for unattended runs.",
  "Der Testgast bleibt für das Zeitfenster erreichbar und wird danach automatisch entfernt.":
    "The test guest stays reachable for the time window and is removed automatically afterwards.",
  "Der Testgast bleibt stehen, bis du ihn unter „Laufende Testgäste“ entfernst. Er belegt so lange eine Scratch-VMID.":
    "The test guest stays until you remove it under “Running test guests”. Until then it holds a scratch VMID.",

  // Werkbank fuer Pruefungen
  "Für Erkennung und Probelauf genau einen Gast auswählen.":
    "Select exactly one guest for discovery and trial runs.",
  "Noch keine Prüfung. „Aus Testgast erkennen“ oder „+ Prüfung“ verwenden.":
    "No checks yet. Use “Discover from test guest” or “+ Check”.",
  "Dieser Gast wurde noch nie geprüft. Ein Lauf entsteht nur durch „Jetzt prüfen“ oder durch einen Zeitplan.":
    "This guest has never been verified. A run only happens through “Check now” or a schedule.",
  "Das JSON ist nicht gueltig - die Zeilenansicht zeigt den letzten gueltigen Stand.":
    "The JSON is not valid — the row view shows the last valid state.",
  "Prüfungen sind kein gültiges JSON:": "Checks are not valid JSON:",
  "es muss eine Liste sein": "it has to be a list",
  "Muster in der Ausgabe": "Pattern in the output",

  // Schnellwahl
  "+ HTTP 80 von außen": "+ HTTP 80 from outside",
  "+ HTTPS 443 von außen": "+ HTTPS 443 from outside",
  "+ TLS-Zertifikat": "+ TLS certificate",
  "+ Alle Dienste ok": "+ All services ok",
  "leeren": "clear",
  "HTTP 80 von außen": "HTTP 80 from outside",
  "HTTPS 443 von außen": "HTTPS 443 from outside",
  "Zertifikat gültig": "Certificate valid",
  "Weboberfläche antwortet": "Web interface responds",
  "Dienst läuft": "Service running",
  "Oberfläche 81": "Interface 81",
  "DNS läuft": "DNS running",

  // Meldungen und Rueckfragen
  "nicht angemeldet": "not signed in",
  "Bitte mindestens einen Gast auswählen.": "Please select at least one guest.",
  "Bitte mindestens einen Wochentag auswählen.": "Please select at least one weekday.",
  "Der Modus geroutet verlangt eine hinterlegte Adresse. Unter Einstellungen anlegen.":
    "Routed mode needs a stored address. Create one under Settings.",
  "Der Modus geroutet verlangt einen Adress-Eintrag. Unter Einstellungen anlegen.":
    "Routed mode needs an address entry. Create one under Settings.",
  "Mehrere Gäste geroutet prüfen geht nur mit einem Adressbereich.":
    "Verifying several guests in routed mode only works with an address range.",
  "Einzelne Adresse gewählt.": "A single address is selected.",
  "Für ein Zeitfenster braucht es eine Minutenzahl größer null":
    "A time window needs a number of minutes greater than zero",
  "Keine Adresse gewählt.": "No address selected.",
  "Keine verwaisten Testgäste gefunden.": "No orphaned test guests found.",
  "prüfe …": "checking …",
  "prüfe Verbindung …": "testing connection …",
  "Prüfung fehlgeschlagen.": "Check failed.",
  "Trage zuerst die Adresse von außen ein.": "Enter the address from outside first.",

  // Konto und Benutzer
  "Benutzer": "User",
  "das bist du": "that is you",
  "Ändern": "Edit",
  "Änderungen speichern": "Save changes",
  "Änderung speichern": "Save change",
  "2FA zurücksetzen": "Reset 2FA",
  "Zwei-Faktor zurücksetzen, damit sich der Benutzer neu einrichten kann":
    "Reset two-factor so the user can set it up again",
  "Benutzer verwalten dürfen nur Admins.": "Only admins may manage users.",
  "Bitte das eigene Passwort zur Bestätigung eingeben.": "Please enter your own password to confirm.",
  "Das Passwort stimmt nicht.": "The password is not correct.",
  "Das Passwort braucht mindestens 10 Zeichen.": "The password needs at least 10 characters.",
  "Der Code hat sechs Ziffern.": "The code has six digits.",
  "Der Code stimmt nicht.": "The code is not correct.",
  "Zwei-Faktor ist jetzt aktiv. Ab der nächsten Anmeldung wird der Code verlangt.":
    "Two-factor is active now. From the next sign-in on, the code is required.",
  "Zwei-Faktor ist abgeschaltet.": "Two-factor is switched off.",
  "an": "on",
  "aus": "off",

  // Einstellungen
  "Alle Werte stammen aus der Konfigurationsdatei.": "Every value comes from the configuration file.",
  "Änderung bestätigen": "Confirm change",
  "Jetzt zurücknehmen": "Roll back now",
  "Änderung gespeichert.": "Change saved.",
  "Eintrag angelegt.": "Entry created.",
  "Änderung noch nicht bestätigt.": "Change not confirmed yet.",
  "kein Backup-Storage gefunden": "no backup storage found",
});

// Saetze mit eingesetzten Werten. Ein fester Schluessel kann hier nie passen,
// deshalb Muster. Reihenfolge zaehlt: das erste passende gewinnt.
MUSTER.push(
  [/^(\d+) ausgewählt$/, "$1 selected"],
  [/^(.+) · (.+) · (.+) GB frei$/, "$1 · $2 · $3 GB free"],
  [/^Testgast (\d+) läuft$/, "Test guest $1 running"],
  [/^läuft: (.+)$/, "running: $1"],
  [/^(\d+) in der Warteschlange$/, "$1 queued"],
  [/^Der Bereich hat (\d+) Adressen, ausgewählt sind (\d+) Gäste\.$/,
   "The range holds $1 addresses, $2 guests are selected."],
  [/^(\d+) Auftrag\/Aufträge eingereiht\.(.*)$/, "$1 job(s) queued.$2"],
  [/^(\d+) Einträge · (\d+) Adressen$/, "$1 entries · $2 addresses"],
  [/^· (\d+) Stände$/, "· $1 snapshots"],
  [/^(\d+) Wert\(e\) in der Oberfläche geändert:(.*)$/, "$1 value(s) changed in the interface:$2"],
  [/^Der Fortschritt steht unter „Prüfen“ im Live-Protokoll\.$/,
   "Progress is shown under “Verify” in the live log."],
  [/^Werkbank: Testgast (.+) läuft — Erkennung und Probelauf sind möglich\.$/,
   "Workbench: test guest $1 is running — discovery and trial runs are possible."],
  [/^Eintrag (.+) wird geändert\.(.*)$/, "Entry $1 is being edited.$2"],
  [/^Prüfe (.+) …$/, "Testing $1 …"],
  [/^Bestätige die Änderung innerhalb von (\d+) Minuten, sonst wird sie zurückgenommen\.$/,
   "Confirm the change within $1 minutes, otherwise it is rolled back."],
);

// Rueckfragen ueber confirm() und alert(). Sie stehen nicht in der Seite, die
// Uebersetzung beim Anzeigen erreicht sie also nicht - deshalb im Quelltext
// gefasst und hier gefuehrt.
Object.assign(EN, {
  "Diesen Zeitplan löschen?": "Delete this schedule?",
  "Diesen Eintrag entfernen?": "Remove this entry?",
  "Diesen Benutzer endgültig entfernen?": "Remove this user for good?",
  "Zwei-Faktor wirklich abschalten?": "Really switch off two-factor?",
  "Zwei-Faktor dieses Benutzers zurücksetzen? Er meldet sich danach nur mit Passwort an und richtet ihn neu ein. Alle seine Sitzungen werden beendet.":
    "Reset this user's second factor? They will sign in with the password alone and set it up again. All their sessions end.",
  "Testgast ": "Test guest ",
  " endgültig entfernen?": " — remove for good?",
  "Gefunden: ": "Found: ",
  "Jetzt vernichten?": "Destroy them now?",
  "Rolle auf „": "Change role to “",
  "“ ändern?": "”?",
});

// --- Gleichzeitige Laeufe ----------------------------------------------------
Object.assign(EN, {
  "Gleichzeitige Läufe": "Concurrent runs",
  "Wie viele Gäste gleichzeitig geprüft werden dürfen. Eins ist die sichere Wahl: parallele Wiederherstellungen teilen sich die Bandbreite des Storage, und ist die der Engpass, dauern zwei zusammen genauso lange wie nacheinander. Bei schnellem NVMe lohnt höher. Eine Verringerung greift erst nach einem Neustart des Dienstes.":
    "How many guests may be verified at the same time. One is the safe choice: parallel restores share the storage bandwidth, and if that is the bottleneck, two together take just as long as one after the other. With fast NVMe, higher pays off. Lowering it takes effect after the service restarts.",
});

// --- Dateien aus einem Backup ------------------------------------------------
Object.assign(EN, {
  "Dateien": "Files",
  "Datei aus einem Backup holen": "Retrieve a file from a backup",
  "Backup-Stand": "Backup snapshot",
  "Es wird nichts wiederhergestellt und nichts gestartet. Bei einem Container wird unmittelbar im Dateiarchiv gelesen; bei einer VM schließt Proxmox das Blockabbild kurz mit einer eigenen Hilfsmaschine auf und räumt sie danach selbst wieder ab.":
    "Nothing is restored and nothing is started. For a container the file archive is read directly; for a VM, Proxmox briefly opens the disk image with a small helper machine of its own and clears it away again afterwards.",
  "Wähle einen Gast und einen Backup-Stand.": "Pick a guest and a backup snapshot.",
  "Herunterladen": "Download",
  "Noch nichts gewählt.": "Nothing selected yet.",
  "bitte wählen": "please choose",
  "hier liegt nichts": "nothing here",
  "Anfang": "Top",
  "hole die Datei …": "fetching the file …",
  "Fertig.": "Done.",
  "Hier kommen Inhalte heraus, keine Prüfergebnisse — Passwortdateien, Datenbanken, Schlüssel. Deshalb nur Super Admin, deshalb das Passwort erneut, und jeder Abruf steht im Journal mit Konto, Gast, Pfad und Zeit.":
    "What comes out here is content, not verdicts — password files, databases, keys. Hence super admin only, hence the password again, and every retrieval is recorded in the journal with account, guest, path and time.",
  "Ein Verzeichnis kommt als Archiv. Bei großen Dateien beginnt der Download, sobald das erste Stück da ist — es wird nichts zwischengelagert.":
    "A directory comes as an archive. For large files the download starts as soon as the first chunk arrives — nothing is buffered in between.",
});

// Wochentage. Kurz, und der Beobachter unterscheidet nicht nach Ort: er
// uebersetzt jeden Textknoten, dessen ganzer Inhalt genau so lautet. Bei
// diesen sieben ist das vertretbar - ein Gast oder eine Pruefung, die exakt
// "Mo" oder "So" heisst und sonst nichts, ist kaum zu erwarten.
Object.assign(EN, {
  "Mo": "Mon", "Di": "Tue", "Mi": "Wed", "Do": "Thu",
  "Fr": "Fri", "Sa": "Sat", "So": "Sun",
});
