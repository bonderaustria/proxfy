#!/usr/bin/env bash
# Proxfy - Einrichtung auf einem Proxmox-VE-Host.
#
# Aufruf direkt auf dem Hypervisor:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/bonderaustria/proxfy/main/proxfy.sh)"
#
# Legt einen unprivilegierten Container an, richtet den SSH-Zugang zum
# Hypervisor ein und installiert Proxfy hinein. Nach dem Durchlauf ist die
# Weboberflaeche erreichbar und wartet auf das erste Konto.
#
# Nur pruefen, ohne etwas anzulegen:
#   PROXFY_DRY_RUN=1 PROXFY_IP=192.168.1.50/24 PROXFY_GW=192.168.1.1 \
#     PROXFY_UNATTENDED=1 bash proxfy.sh
#
# Ohne Rueckfragen, mit Vorgaben:
#   PROXFY_IP=192.168.1.50/24 PROXFY_GW=192.168.1.1 PROXFY_UNATTENDED=1 \
#     bash -c "$(curl -fsSL .../proxfy.sh)"
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

# Ein Storage mit Backup-Inhalt ist der eigentliche Daseinszweck.
if ! pvesm status --content backup >/dev/null 2>&1 \
   || [ -z "$(pvesm status --content backup 2>/dev/null | awk 'NR>1')" ]; then
    warnung "Kein Storage mit Backup-Inhalt gefunden."
    hinweis "Proxfy laesst sich installieren, hat aber nichts zu pruefen,"
    hinweis "solange kein PBS oder Verzeichnis-Backup eingebunden ist."
fi

# --- Vorgaben ----------------------------------------------------------------
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

# --- Abfragen ----------------------------------------------------------------
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
hinweis "Container   $VMID  ($HOSTNAME_CT)"
hinweis "Adresse     $IP  Gateway $GW  Bridge $BRIDGE"
hinweis "Storage     $STORAGE   ${CORES} Kerne, ${MEMORY} MB, ${DISK} GB"
echo
if [ "${PROXFY_UNATTENDED:-0}" != "1" ]; then
    printf '    Fortfahren? [J/n]: '
    read -r ja </dev/tty || ja="j"
    case "${ja:-j}" in [nN]*) abbruch "Abgebrochen." ;; esac
    echo
fi

# --- Trockenlauf ---------------------------------------------------------------
if [ "${PROXFY_DRY_RUN:-0}" = "1" ]; then
    schritt "Trockenlauf - es wird nichts angelegt"
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
    echo
    exit 0
fi

# --- Quelle holen ------------------------------------------------------------
ARBEIT=$(mktemp -d /tmp/proxfy-XXXXXX)
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
    QUELLE=$(find "$ARBEIT" -maxdepth 1 -type d -name "proxfy-*" | head -1)
    [ -n "$QUELLE" ] || abbruch "Das Archiv sieht unerwartet aus."
fi
[ -f "$QUELLE/create-lxc.sh" ] || abbruch "create-lxc.sh fehlt in der Quelle."

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
