#!/usr/bin/env bash
# Entfernt die Restore-Verifikation restlos von einem Proxmox-VE-Host.
#
#   bash uninstall.sh              fragt vor jedem Schritt nach
#   bash uninstall.sh --yes        ohne Rueckfragen
#   bash uninstall.sh --keep-data  Datenbank und Konfiguration behalten
#
# Sicherheitsregel: es werden ausschliesslich Gaeste im VMID-Bereich 9000-9099
# angefasst. Dieser Bereich gehoert allein dem Werkzeug - produktive VMs und
# Container liegen ausserhalb und werden nie beruehrt. Vor dem Vernichten wird
# jeder Fund aufgelistet und nachgefragt.
set -euo pipefail

DEST="${DEST:-/opt/proxfy}"
LO=9000
HI=9099
TAG="proxfy-test"
ASSUME_YES=0
KEEP_DATA=0

for arg in "$@"; do
    case "$arg" in
        --yes|-y)    ASSUME_YES=1 ;;
        --keep-data) KEEP_DATA=1 ;;
        *) printf 'Unbekannte Option: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

say() { printf '==> %s\n' "$*"; }
ask() {
    [ "$ASSUME_YES" = "1" ] && return 0
    printf '%s [j/N] ' "$1"
    read -r a </dev/tty || return 1
    case "$a" in [jJyY]*) return 0 ;; *) return 1 ;; esac
}

[ "$(id -u)" = "0" ] || { printf 'FEHLER: bitte als root ausfuehren.\n' >&2; exit 1; }

# --- Dienst anhalten ---------------------------------------------------------
# Bewusst ohne "systemctl ... | grep -q": grep beendet sich beim ersten Treffer,
# systemctl bekommt SIGPIPE, und pipefail wertet den Erfolgsfall als Fehler.
GEFUNDEN=0
for unit in proxfy proxfy-auth; do
    if [ -f "/etc/systemd/system/$unit.service" ] || systemctl cat "$unit.service" >/dev/null 2>&1; then
        say "Dienst $unit anhalten und deaktivieren"
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$unit.service"
        systemctl reset-failed "$unit" 2>/dev/null || true
        GEFUNDEN=1
    fi
done
systemctl daemon-reload
[ "$GEFUNDEN" = "1" ] || say "Kein Dienst proxfy vorhanden"

# --- Zurueckgebliebene Testgaeste --------------------------------------------
# Laeuft die Anwendung im eigenen Container, gibt es hier weder pct noch qm.
# Dann ist auf dem Hypervisor aufzuraeumen, nicht hier.
if ! command -v pct >/dev/null 2>&1; then
    say "Kein pct vorhanden - dies ist kein Proxmox-Host"
    printf '    Etwaige Testgaeste im Bereich %s-%s bitte AUF DEM HYPERVISOR entfernen.\n' "$LO" "$HI"
    printf '    Vorher pruefen mit:  pct list | awk \x27$1>=%s\x27\n' "$LO"
    SKIP_GUESTS=1
else
    SKIP_GUESTS=0
fi

say "Testgaeste im Bereich $LO-$HI suchen"
FOUND=""
if [ "$SKIP_GUESTS" = "1" ]; then FOUND=""; fi
[ "$SKIP_GUESTS" = "1" ] || for vmid in $(seq "$LO" "$HI"); do
    # Ausgabe erst einsammeln, dann pruefen - aus demselben SIGPIPE-Grund.
    if [ -f "/etc/pve/qemu-server/${vmid}.conf" ]; then
        FOUND="$FOUND vm:$vmid"
        CFG=$(qm config "$vmid" 2>/dev/null || true)
        case "$CFG" in *"$TAG"*) : ;; *) printf \
            '    Hinweis: VM %s ohne Markierung - vermutlich ein abgebrochener Restore\n' "$vmid" ;;
        esac
    elif [ -f "/etc/pve/lxc/${vmid}.conf" ]; then
        FOUND="$FOUND ct:$vmid"
        CFG=$(pct config "$vmid" 2>/dev/null || true)
        case "$CFG" in *"$TAG"*) : ;; *) printf \
            '    Hinweis: CT %s ohne Markierung - vermutlich ein abgebrochener Restore\n' "$vmid" ;;
        esac
    fi
done

if [ -n "$FOUND" ]; then
    printf '    gefunden:%s\n' "$FOUND"
    if ask "Diese Testgaeste jetzt endgueltig vernichten?"; then
        for entry in $FOUND; do
            kind="${entry%%:*}"; vmid="${entry##*:}"
            say "vernichte $kind/$vmid"
            if [ "$kind" = "vm" ]; then
                qm unlock "$vmid" >/dev/null 2>&1 || true
                qm stop "$vmid" >/dev/null 2>&1 || true
                qm destroy "$vmid" --purge 1 --destroy-unreferenced-disks 1 >/dev/null 2>&1 || \
                    printf '    WARNUNG: VM %s konnte nicht vernichtet werden\n' "$vmid"
            else
                pct unlock "$vmid" >/dev/null 2>&1 || true
                pct stop "$vmid" >/dev/null 2>&1 || true
                pct destroy "$vmid" --purge 1 >/dev/null 2>&1 || \
                    printf '    WARNUNG: CT %s konnte nicht vernichtet werden\n' "$vmid"
            fi
        done
    else
        say "Testgaeste bleiben stehen - sie belegen weiterhin Speicher"
    fi
else
    say "  keine gefunden"
fi

# --- Isolierte Bridge --------------------------------------------------------
BRIDGE=$(awk -F': *' '/^ *isolated_bridge:/ {print $2; exit}' "$DEST/config.yaml" 2>/dev/null || true)
BRIDGE="${BRIDGE:-vmbr9}"
BRIDGE="${BRIDGE%%[[:space:]]*}"

if [ "$SKIP_GUESTS" = "1" ]; then
    say "Bridge $BRIDGE liegt auf dem Hypervisor - dort entfernen, falls gewuenscht"
elif ip link show "$BRIDGE" >/dev/null 2>&1; then
    PORTS=$(ls "/sys/class/net/$BRIDGE/brif" 2>/dev/null || true)
    if [ -n "$PORTS" ]; then
        say "Bridge $BRIDGE hat Ports ($PORTS) - wird NICHT entfernt"
    elif ask "Isolierte Bridge $BRIDGE entfernen?"; then
        ip link set "$BRIDGE" down 2>/dev/null || true
        ip link delete "$BRIDGE" type bridge 2>/dev/null || true
        say "  $BRIDGE entfernt"
    fi
else
    say "Bridge $BRIDGE existiert nicht"
fi

# --- Dateien -----------------------------------------------------------------
if [ -d "$DEST" ]; then
    if [ "$KEEP_DATA" = "1" ]; then
        say "Programmdateien entfernen, Daten behalten"
        rm -rf "$DEST/proxfy" "$DEST/auth/node_modules" "$DEST/auth"/*.js "$DEST/auth/package.json"
        say "  behalten: $DEST/config.yaml, $DEST/proxfy.db, $DEST/auth.db, $DEST/auth.env"
    elif ask "Verzeichnis $DEST samt Konfiguration, Verlaufs- UND Benutzerdatenbank loeschen?"; then
        rm -rf "$DEST"
        say "  $DEST geloescht"
    else
        say "  $DEST bleibt bestehen"
    fi
fi

cat <<DONE

Deinstallation abgeschlossen.

Nicht angefasst wurden: eure Backups, der Backup-Storage, alle produktiven
VMs und Container sowie das Paket iputils-arping.
DONE
