#!/usr/bin/env bash
# Legt einen Container fuer die Restore-Verifikation an und installiert alles
# hinein. AUF DEM PROXMOX-HOST ausfuehren, im entpackten Quellverzeichnis.
#
#   bash create-lxc.sh --ip 192.168.1.35/24 --gw 192.168.1.1
#
# Weitere Schalter:
#   --vmid N        VMID des Containers (Vorgabe: naechste freie)
#   --storage NAME  Storage fuer den Container (Vorgabe: erster passende)
#   --bridge NAME   Bridge (Vorgabe: erste vmbr aus /etc/network/interfaces)
#   --hostname NAME Hostname (Vorgabe: proxfy)
#   --dns IP        Nameserver (Vorgabe: das Gateway)
#
# Warum ein eigener Container: auf dem Hypervisor ist Python "externally
# managed", und Fremdsoftware gehoert nicht auf den Hypervisor selbst. Im
# Container darf installiert werden, was gebraucht wird.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IP=""; GW=""; VMID=""; STORAGE=""; BRIDGE=""; HOSTNAME_CT="proxfy"; DNS=""
CORES=2; MEMORY=1024; DISK=8

while [ $# -gt 0 ]; do
    case "$1" in
        --ip)       IP="$2"; shift 2 ;;
        --gw)       GW="$2"; shift 2 ;;
        --vmid)     VMID="$2"; shift 2 ;;
        --storage)  STORAGE="$2"; shift 2 ;;
        --bridge)   BRIDGE="$2"; shift 2 ;;
        --hostname) HOSTNAME_CT="$2"; shift 2 ;;
        --dns)      DNS="$2"; shift 2 ;;
        --cores)    CORES="$2"; shift 2 ;;
        --memory)   MEMORY="$2"; shift 2 ;;
        --disk)     DISK="$2"; shift 2 ;;
        *) printf 'Unbekannte Option: %s\n' "$1" >&2; exit 2 ;;
    esac
done

say() { printf '==> %s\n' "$*"; }
die() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "Bitte als root ausfuehren."
command -v pct >/dev/null 2>&1 || die "pct nicht gefunden - dieses Skript gehoert auf den PVE-Host."
[ -n "$IP" ] || die "--ip fehlt, z. B. --ip 192.168.1.35/24"
[ -n "$GW" ] || die "--gw fehlt, z. B. --gw 192.168.1.1"
DNS="${DNS:-$GW}"

# --- Vorgaben ermitteln ------------------------------------------------------
VMID="${VMID:-$(pvesh get /cluster/nextid)}"
[ -n "$STORAGE" ] || STORAGE=$(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 {print $1; exit}')
STORAGE="${STORAGE:-local-lvm}"
[ -n "$BRIDGE" ] || BRIDGE=$(awk '/^auto vmbr/ {print $2; exit}' /etc/network/interfaces 2>/dev/null)
BRIDGE="${BRIDGE:-vmbr0}"

# --- Ist die Adresse frei? ---------------------------------------------------
BARE_IP="${IP%%/*}"
say "Pruefe, ob $BARE_IP frei ist"
if ping -c 2 -W 2 "$BARE_IP" >/dev/null 2>&1; then
    die "$BARE_IP antwortet bereits auf Ping. Bitte eine freie Adresse waehlen."
fi
if grep -rqE "(^|[^0-9])${BARE_IP//./\\.}([^0-9]|\$)" /etc/pve/lxc /etc/pve/qemu-server 2>/dev/null; then
    die "$BARE_IP steht bereits in einer Gast-Konfiguration."
fi

# --- Vorlage besorgen --------------------------------------------------------
TMPL=$(pveam list local 2>/dev/null | awk '/debian-1[3-9]-standard/ {print $1; exit}')
if [ -z "$TMPL" ]; then
    say "Debian-Vorlage fehlt, lade sie herunter"
    pveam update >/dev/null 2>&1 || true
    NAME=$(pveam available --section system 2>/dev/null | awk '/debian-1[3-9]-standard/ {print $2}' | sort -r | head -1)
    [ -n "$NAME" ] || die "Keine Debian-Vorlage gefunden."
    pveam download local "$NAME" >/dev/null
    TMPL="local:vztmpl/$NAME"
fi

# --- Container anlegen -------------------------------------------------------
say "Lege Container $VMID an ($HOSTNAME_CT, $IP, Storage $STORAGE, Bridge $BRIDGE)"
pct create "$VMID" "$TMPL" \
    --hostname "$HOSTNAME_CT" \
    --cores "$CORES" --memory "$MEMORY" --swap 512 \
    --rootfs "$STORAGE:$DISK" \
    --net0 "name=eth0,bridge=$BRIDGE,ip=$IP,gw=$GW" \
    --nameserver "$DNS" \
    --unprivileged 1 --features nesting=1 \
    --onboot 1 \
    --description "Restore-Verifikation (proxfy) - Weboberflaeche auf Port 8099" >/dev/null

pct start "$VMID"
say "Warte auf den Container"
for _ in $(seq 1 30); do
    pct exec "$VMID" -- true >/dev/null 2>&1 && break
    sleep 2
done
pct exec "$VMID" -- true >/dev/null 2>&1 || die "Container startet nicht."

# --- Schluessel fuer den Zugriff auf den Hypervisor --------------------------
say "Erzeuge Schluesselpaar im Container"
pct exec "$VMID" -- sh -c \
    "mkdir -p /root/.ssh && chmod 700 /root/.ssh && \
     [ -f /root/.ssh/id_proxfy ] || ssh-keygen -t ed25519 -N '' -C 'proxfy@$HOSTNAME_CT' -f /root/.ssh/id_proxfy -q"
PUB=$(pct exec "$VMID" -- cat /root/.ssh/id_proxfy.pub)

say "Hinterlege den oeffentlichen Schluessel auf dem Hypervisor"
mkdir -p /root/.ssh && touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
grep -qF "$PUB" /root/.ssh/authorized_keys || printf '%s\n' "$PUB" >> /root/.ssh/authorized_keys

HOST_IP=$(hostname -I | awk '{print $1}')
pct exec "$VMID" -- sh -c \
    "ssh-keyscan -H $HOST_IP >> /root/.ssh/known_hosts 2>/dev/null; chmod 600 /root/.ssh/known_hosts"

# --- Quelle hineinkopieren und installieren ----------------------------------
say "Kopiere die Quelle in den Container"
TAR=$(mktemp /tmp/proxfy-src-XXXX.tar)
tar -cf "$TAR" -C "$SRC" --exclude node_modules --exclude __pycache__ --exclude '*.db' .
pct exec "$VMID" -- mkdir -p /opt/proxfy-src
pct push "$VMID" "$TAR" /tmp/proxfy-src.tar >/dev/null
pct exec "$VMID" -- tar -xf /tmp/proxfy-src.tar -C /opt/proxfy-src
pct exec "$VMID" -- rm -f /tmp/proxfy-src.tar
rm -f "$TAR"

say "Starte die Installation im Container"
pct exec "$VMID" -- sh -c "cd /opt/proxfy-src && PVE_HOST=$HOST_IP PVE_KEY=/root/.ssh/id_proxfy PUBLIC_IP=$BARE_IP bash install.sh"

cat <<DONE

Fertig.

  Container       $VMID ($HOSTNAME_CT)
  Weboberflaeche  http://$BARE_IP:8099/
  Zugriff auf PVE ueber SSH-Schluessel /root/.ssh/id_proxfy

NAECHSTER SCHRITT: Rufe die Weboberflaeche auf und lege das erste Konto an.
Solange das nicht geschehen ist, koennte es jeder tun, der die Adresse erreicht.
DONE
