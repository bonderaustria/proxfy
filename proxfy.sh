#!/usr/bin/env bash
# Proxfy - Einrichtung und Aktualisierung auf einem Proxmox-VE-Host.
#
# Aufruf direkt auf dem Hypervisor:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
#
# Beim ersten Mal legt das Skript einen unprivilegierten Container an, richtet
# den SSH-Zugang zum Hypervisor ein und installiert Proxfy hinein.
#
# Beim zweiten und jedem weiteren Mal erkennt es die vorhandene Installation
# und aktualisiert nur diese. Konten, Einstellungen, Zeitplaene, Verlauf und
# Geheimnisse bleiben erhalten - erneuert wird ausschliesslich der Programmcode.
#
# Nur pruefen, ohne etwas zu aendern:
#   PROXFY_DRY_RUN=1 bash -c "$(curl -fsSL .../proxfy.sh)"
#
# Trotz vorhandener Installation eine zweite, unabhaengige anlegen:
#   PROXFY_NEU=1 PROXFY_IP=192.168.1.51/24 PROXFY_GW=192.168.1.1 bash ...
set -euo pipefail

REPO="${PROXFY_REPO:-bonderaustria/proxfy}"
ZWEIG="${PROXFY_BRANCH:-main}"

# --- Ausgabe -----------------------------------------------------------------
if [ -t 1 ]; then
    B=$'\033[1m'; GRUEN=$'\033[32m'; ROT=$'\033[31m'; GELB=$'\033[33m'; AUS=$'\033[0m'
else
    B=""; GRUEN=""; ROT=""; GELB=""; AUS=""
fi
schritt() { printf '%s==>%s %s\n' "$GRUEN" "$AUS" "$*"; }
hinweis() { printf '    %s\n' "$*"; }
warnung() { printf '%s !! %s%s\n' "$GELB" "$*" "$AUS"; }
abbruch() { printf '%sFEHLER:%s %s\n' "$ROT" "$AUS" "$*" >&2; exit 1; }

printf '\n%sProxfy%s  Restore-Verifikation fuer Proxmox Backup Server\n\n' "$B" "$AUS"

# --- Voraussetzungen ---------------------------------------------------------
[ "$(id -u)" = "0" ] || abbruch "Bitte als root ausfuehren."
command -v pct   >/dev/null 2>&1 || abbruch "pct nicht gefunden - dieses Skript gehoert auf einen Proxmox-VE-Host."
command -v pvesh >/dev/null 2>&1 || abbruch "pvesh nicht gefunden."
command -v curl  >/dev/null 2>&1 || abbruch "curl fehlt: apt install curl"

# --- Vorhandene Installation suchen ------------------------------------------
# Gesucht wird der Container, in dem /opt/proxfy/config.yaml liegt. Der Name in
# der Container-Konfiguration ist nur die Vorauswahl - entschieden wird erst,
# wenn die Datei wirklich da ist. Sonst wuerde ein Container, der zufaellig
# "proxfy" heisst, faelschlich als Installation gelten.
laeuft() { pct status "$1" 2>/dev/null | grep -q running; }

bestand_suchen() {
    local kandidaten=() id cfg
    if [ -n "${PROXFY_VMID:-}" ]; then
        kandidaten=("$PROXFY_VMID")
    else
        for cfg in /etc/pve/lxc/*.conf; do
            [ -f "$cfg" ] || continue
            grep -qi 'proxfy' "$cfg" || continue
            id="${cfg##*/}"
            kandidaten+=("${id%.conf}")
        done
    fi
    [ ${#kandidaten[@]} -gt 0 ] || return 1
    for id in "${kandidaten[@]}"; do
        [ -n "$id" ] || continue
        laeuft "$id" || continue
        if pct exec "$id" -- test -f /opt/proxfy/config.yaml >/dev/null 2>&1; then
            printf '%s' "$id"
            return 0
        fi
    done
    return 1
}

BESTAND="$(bestand_suchen || true)"

if [ -n "$BESTAND" ] && [ "${PROXFY_NEU:-0}" = "1" ]; then
    warnung "Container $BESTAND traegt bereits eine Installation."
    hinweis "PROXFY_NEU=1 ist gesetzt - es wird trotzdem eine zweite angelegt."
    BESTAND=""
fi

if [ -n "$BESTAND" ]; then
    MODUS="update"
else
    MODUS="neu"
fi

# --- Vorgaben und Abfragen (nur bei Neuinstallation) -------------------------
if [ "$MODUS" = "neu" ]; then
    # Ein Storage mit Backup-Inhalt ist der eigentliche Daseinszweck.
    if ! pvesm status --content backup >/dev/null 2>&1 \
       || [ -z "$(pvesm status --content backup 2>/dev/null | awk 'NR>1')" ]; then
        warnung "Kein Storage mit Backup-Inhalt gefunden."
        hinweis "Proxfy laesst sich installieren, hat aber nichts zu pruefen,"
        hinweis "solange kein PBS oder Verzeichnis-Backup eingebunden ist."
    fi

    VMID_VORGABE=$(pvesh get /cluster/nextid 2>/dev/null || echo 200)
    BRIDGE_VORGABE=$(awk '/^auto vmbr/ {print $2; exit}' /etc/network/interfaces 2>/dev/null || echo vmbr0)
    STORAGE_VORGABE=$(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 {print $1; exit}')
    STORAGE_VORGABE="${STORAGE_VORGABE:-local-lvm}"
    GW_VORGABE=$(ip route | awk '/^default/ {print $3; exit}')

    VMID="${PROXFY_VMID:-$VMID_VORGABE}"
    HOSTNAME_CT="${PROXFY_HOSTNAME:-proxfy}"
    BRIDGE="${PROXFY_BRIDGE:-$BRIDGE_VORGABE}"
    STORAGE="${PROXFY_STORAGE:-$STORAGE_VORGABE}"
    GW="${PROXFY_GW:-$GW_VORGABE}"
    IP="${PROXFY_IP:-}"
    CORES="${PROXFY_CORES:-2}"
    MEMORY="${PROXFY_MEMORY:-1024}"
    DISK="${PROXFY_DISK:-8}"

    frage() {   # frage <Text> <Vorgabe>
        local antwort
        printf '    %s [%s]: ' "$1" "$2" >&2
        read -r antwort </dev/tty || antwort=""
        printf '%s' "${antwort:-$2}"
    }

    if [ "${PROXFY_UNATTENDED:-0}" != "1" ]; then
        schritt "Einstellungen"
        VMID=$(frage "Container-ID" "$VMID")
        HOSTNAME_CT=$(frage "Hostname" "$HOSTNAME_CT")
        while :; do
            IP=$(frage "IP-Adresse mit Praefix (z. B. 192.168.1.50/24)" "$IP")
            case "$IP" in
                */*) break ;;
                *) printf '    Bitte mit Praefix angeben, etwa 192.168.1.50/24\n' >&2 ;;
            esac
        done
        GW=$(frage "Gateway" "$GW")
        BRIDGE=$(frage "Bridge" "$BRIDGE")
        STORAGE=$(frage "Storage fuer den Container" "$STORAGE")
        CORES=$(frage "Kerne" "$CORES")
        MEMORY=$(frage "Arbeitsspeicher in MB" "$MEMORY")
        DISK=$(frage "Datentraeger in GB" "$DISK")
        echo
    fi

    [ -n "$IP" ] || abbruch "Keine IP-Adresse angegeben (PROXFY_IP)."
    [ -n "$GW" ] || abbruch "Kein Gateway angegeben (PROXFY_GW)."
    case "$IP" in */*) : ;; *) abbruch "Die IP braucht ein Praefix, etwa $IP/24." ;; esac
    BARE_IP="${IP%%/*}"

    schritt "Zusammenfassung"
    hinweis "Neuinstallation"
    hinweis "Container   $VMID  ($HOSTNAME_CT)"
    hinweis "Adresse     $IP  Gateway $GW  Bridge $BRIDGE"
    hinweis "Storage     $STORAGE   ${CORES} Kerne, ${MEMORY} MB, ${DISK} GB"
else
    BARE_IP=$(pct exec "$BESTAND" -- hostname -I 2>/dev/null | awk '{print $1}')
    HOSTNAME_CT=$(pct exec "$BESTAND" -- hostname 2>/dev/null || echo proxfy)
    schritt "Zusammenfassung"
    hinweis "Aktualisierung einer vorhandenen Installation"
    hinweis "Container   $BESTAND  ($HOSTNAME_CT)"
    hinweis "Adresse     ${BARE_IP:-unbekannt}"
    hinweis "Erneuert    Programmcode und Weboberflaeche"
    hinweis "Bleibt      Konten, Einstellungen, Zeitplaene, Verlauf, Geheimnisse"
fi
echo

if [ "${PROXFY_UNATTENDED:-0}" != "1" ]; then
    printf '    Fortfahren? [J/n]: '
    read -r ja </dev/tty || ja="j"
    case "${ja:-j}" in [nN]*) abbruch "Abgebrochen." ;; esac
    echo
fi

# --- Quelle holen ------------------------------------------------------------
ARBEIT=$(mktemp -d /tmp/proxfy-download-XXXXXX)
trap 'rm -rf "$ARBEIT"' EXIT

if [ -f "$(dirname "${BASH_SOURCE[0]}")/create-lxc.sh" ]; then
    schritt "Quelle aus dem lokalen Verzeichnis"
    QUELLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    schritt "Quelle von GitHub laden ($REPO, Zweig $ZWEIG)"
    if ! curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$ZWEIG" \
         -o "$ARBEIT/proxfy.tar.gz"; then
        abbruch "Download fehlgeschlagen. Ist das Repository oeffentlich und der Zweig '$ZWEIG' richtig?"
    fi
    tar -xzf "$ARBEIT/proxfy.tar.gz" -C "$ARBEIT"
    # -mindepth 1 ist noetig: ohne das listet find auch das Startverzeichnis,
    # und dessen Name passt selbst auf das Muster.
    QUELLE=$(find "$ARBEIT" -mindepth 1 -maxdepth 1 -type d | head -1)
    [ -n "$QUELLE" ] || abbruch "Das Archiv sieht unerwartet aus."
fi
[ -f "$QUELLE/create-lxc.sh" ] || abbruch "create-lxc.sh fehlt in der Quelle."
[ -f "$QUELLE/install.sh" ]    || abbruch "install.sh fehlt in der Quelle."

# --- Trockenlauf -------------------------------------------------------------
if [ "${PROXFY_DRY_RUN:-0}" = "1" ]; then
    schritt "Trockenlauf - es wird nichts geaendert"
    hinweis "Quelle geladen und vollstaendig: $QUELLE"
    if [ "$MODUS" = "update" ]; then
        hinweis "Erkannte Installation: Container $BESTAND"
        hinweis "Diese Dateien werden gesichert und bleiben unveraendert:"
        for f in config.yaml auth.env proxfy.db auth.db; do
            if pct exec "$BESTAND" -- test -e "/opt/proxfy/$f" >/dev/null 2>&1; then
                hinweis "  $f"
            fi
        done
    else
        hinweis "Naechste freie Container-ID laut Proxmox: $VMID_VORGABE"
        hinweis "Erkannte Bridge:  $BRIDGE_VORGABE"
        hinweis "Erkannter Storage: $STORAGE_VORGABE"
        hinweis "Erkanntes Gateway: $GW_VORGABE"
        if pvesm status --content backup >/dev/null 2>&1; then
            hinweis "Backup-Storages:   $(pvesm status --content backup 2>/dev/null | awk 'NR>1 {printf "%s ", $1}')"
        fi
        if pct status "$VMID" >/dev/null 2>&1 || qm status "$VMID" >/dev/null 2>&1; then
            warnung "Container-ID $VMID ist bereits vergeben."
        else
            hinweis "Container-ID $VMID ist frei."
        fi
        if ping -c 1 -W 2 "$BARE_IP" >/dev/null 2>&1; then
            warnung "$BARE_IP antwortet auf Ping - die Adresse ist belegt."
        else
            hinweis "$BARE_IP antwortet nicht - sieht frei aus."
        fi
    fi
    echo
    exit 0
fi

# --- Aktualisieren -----------------------------------------------------------
if [ "$MODUS" = "update" ]; then
    ZEIT=$(date +%Y%m%d-%H%M%S)
    SICHER="/opt/proxfy-sicherung/$ZEIT"

    schritt "Daten sichern"
    # install.sh fasst diese Dateien nicht an. Die Sicherung kostet fast nichts
    # und ist der Rueckweg, falls doch einmal etwas schiefgeht.
    pct exec "$BESTAND" -- mkdir -p "$SICHER"
    for f in config.yaml auth.env proxfy.db auth.db; do
        if pct exec "$BESTAND" -- test -e "/opt/proxfy/$f" >/dev/null 2>&1; then
            pct exec "$BESTAND" -- cp -a "/opt/proxfy/$f" "$SICHER/"
            hinweis "$f"
        fi
    done
    # Nur die letzten fuenf Staende behalten, sonst waechst das unbegrenzt.
    pct exec "$BESTAND" -- sh -c \
        "ls -1dt /opt/proxfy-sicherung/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf" || true
    hinweis "Gesichert nach $SICHER (im Container)"

    schritt "Neue Quelle in den Container kopieren"
    TAR=$(mktemp /tmp/proxfy-src-XXXX.tar)
    tar -cf "$TAR" -C "$QUELLE" --exclude node_modules --exclude __pycache__ --exclude '*.db' .
    # Das Quellverzeichnis wird geleert, damit entfallene Dateien nicht als
    # Leichen zurueckbleiben. /opt/proxfy selbst bleibt unangetastet.
    pct exec "$BESTAND" -- rm -rf /opt/proxfy-src
    pct exec "$BESTAND" -- mkdir -p /opt/proxfy-src
    pct push "$BESTAND" "$TAR" /tmp/proxfy-src.tar >/dev/null
    pct exec "$BESTAND" -- tar -xf /tmp/proxfy-src.tar -C /opt/proxfy-src
    pct exec "$BESTAND" -- rm -f /tmp/proxfy-src.tar
    rm -f "$TAR"

    # install.sh muss wieder wissen, wie es an den Hypervisor kommt. Beides
    # steht bereits in der vorhandenen config.yaml.
    PVE_HOST=$(pct exec "$BESTAND" -- awk '/^[[:space:]]*host:/ {print $2; exit}' /opt/proxfy/config.yaml)
    PVE_KEY=$(pct exec "$BESTAND" -- awk '/^[[:space:]]*key_file:/ {print $2; exit}' /opt/proxfy/config.yaml)
    [ -n "$PVE_HOST" ] || abbruch "In der vorhandenen config.yaml steht kein Hypervisor."
    [ -n "$PVE_KEY" ]  || abbruch "In der vorhandenen config.yaml steht kein Schluessel."

    schritt "Aktualisierung im Container ausfuehren"
    echo
    pct exec "$BESTAND" -- sh -c \
        "cd /opt/proxfy-src && PVE_HOST=$PVE_HOST PVE_KEY=$PVE_KEY PUBLIC_IP=$BARE_IP bash install.sh"

    echo
    printf '%sAktualisiert.%s  Weboberflaeche: %shttp://%s:8099/%s\n\n' \
        "$GRUEN" "$AUS" "$B" "$BARE_IP" "$AUS"
    hinweis "Konten, Einstellungen, Zeitplaene und Verlauf sind unveraendert."
    hinweis "Sicherung der Daten: $SICHER im Container."
    echo
    exit 0
fi

# --- Einrichten --------------------------------------------------------------
schritt "Container anlegen und Proxfy installieren"
echo
bash "$QUELLE/create-lxc.sh" \
    --ip "$IP" --gw "$GW" --vmid "$VMID" --hostname "$HOSTNAME_CT" \
    --bridge "$BRIDGE" --storage "$STORAGE" \
    --cores "$CORES" --memory "$MEMORY" --disk "$DISK"

echo
printf '%sFertig.%s  Weboberflaeche: %shttp://%s:8099/%s\n\n' "$GRUEN" "$AUS" "$B" "$BARE_IP" "$AUS"
warnung "Rufe die Adresse jetzt auf und lege das erste Konto an."
hinweis "Solange keines besteht, koennte es jeder tun, der die Adresse erreicht."
hinweis "Das erste Konto ist zugleich der Super Admin."
echo
