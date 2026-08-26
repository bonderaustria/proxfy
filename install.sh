#!/usr/bin/env bash
# Installiert die Restore-Verifikation samt Anmeldedienst.
#
# Ueblicher Weg: nicht dieses Skript direkt aufrufen, sondern auf dem
# Proxmox-Host "bash create-lxc.sh --ip ... --gw ..." - das legt den Container
# an und ruft dieses Skript darin auf.
#
# Direkter Aufruf, im entpackten Quellverzeichnis:
#   PVE_HOST=192.168.1.9 PVE_KEY=/root/.ssh/id_proxfy bash install.sh
#   bash install.sh                      (auf dem Hypervisor selbst)
#
# Das Skript legt WEDER einen Zeitplan NOCH einen Cron-Eintrag an. Nach der
# Installation laeuft nichts, bis jemand einen Lauf ausloest oder in der
# Oberflaeche einen Zeitplan anlegt.
set -euo pipefail

DEST="${DEST:-/opt/proxfy}"
PORT="${PORT:-8099}"
AUTH_PORT="${AUTH_PORT:-8100}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PVE_HOST="${PVE_HOST:-}"
PVE_KEY="${PVE_KEY:-}"
PUBLIC_IP="${PUBLIC_IP:-$(hostname -I | awk '{print $1}')}"

say() { printf '==> %s\n' "$*"; }
die() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "Bitte als root ausfuehren."
[ -f "$SRC/proxfy/cli.py" ] || die "proxfy/cli.py fehlt - bitte im Quellverzeichnis ausfuehren."

# --- Betriebsart -------------------------------------------------------------
if [ -n "$PVE_HOST" ]; then
    MODE="fern"
    say "Betriebsart: eigener Container, Zugriff auf $PVE_HOST ueber SSH"
    [ -n "$PVE_KEY" ] || die "PVE_KEY fehlt (Pfad zum privaten Schluessel)."
    [ -f "$PVE_KEY" ] || die "Schluessel $PVE_KEY nicht gefunden."
elif command -v pvesm >/dev/null 2>&1; then
    MODE="lokal"
    say "Betriebsart: direkt auf dem Proxmox-Host"
else
    die "Weder PVE_HOST gesetzt noch pvesm gefunden - unklar, wo Proxmox liegt."
fi

# --- Pakete ------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
say "Pakete pruefen"
command -v python3 >/dev/null 2>&1 || apt-get install -y -qq python3 >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
    || die "Python ist zu alt, benoetigt wird 3.11 oder neuer."
python3 -c 'import yaml' 2>/dev/null || { say "  python3-yaml"; apt-get install -y -qq python3-yaml >/dev/null; }
command -v ssh      >/dev/null 2>&1 || apt-get install -y -qq openssh-client >/dev/null
command -v openssl  >/dev/null 2>&1 || apt-get install -y -qq openssl >/dev/null
command -v curl     >/dev/null 2>&1 || apt-get install -y -qq curl >/dev/null

# arping braucht der IP-Preflight - und zwar dort, wo Proxmox laeuft.
if [ "$MODE" = "lokal" ]; then
    command -v arping >/dev/null 2>&1 || { say "  iputils-arping"; apt-get install -y -qq iputils-arping >/dev/null; }
else
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i "$PVE_KEY" \
        "root@$PVE_HOST" "command -v arping >/dev/null 2>&1 || apt-get install -y -qq iputils-arping >/dev/null" \
        || die "Kein SSH-Zugriff auf $PVE_HOST mit $PVE_KEY."
fi

# Node braucht der Anmeldedienst. Debian liefert nur Node 20, Better Auth
# verlangt aber 22 - deshalb aus dem NodeSource-Verzeichnis.
NODE_OK=0
if command -v node >/dev/null 2>&1; then
    node -e 'process.exit(process.versions.node.split(".")[0] >= 22 ? 0 : 1)' && NODE_OK=1
fi
if [ "$NODE_OK" = "0" ]; then
    say "Installiere Node 22 (Better Auth verlangt mindestens 22)"
    apt-get install -y -qq ca-certificates gnupg >/dev/null
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg 2>/dev/null
    echo 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main' \
        > /etc/apt/sources.list.d/nodesource.list
    apt-get update -qq >/dev/null
    apt-get install -y -qq nodejs >/dev/null
fi
say "  Node $(node --version), Python $(python3 -c 'import platform;print(platform.python_version())')"

# --- Dateien -----------------------------------------------------------------
say "Dateien nach $DEST kopieren"
mkdir -p "$DEST/proxfy/static" "$DEST/auth"
cp -f "$SRC"/proxfy/*.py     "$DEST/proxfy/"
cp -f "$SRC"/proxfy/static/* "$DEST/proxfy/static/"
cp -f "$SRC"/auth/*.js "$SRC"/auth/package.json "$DEST/auth/"
[ -f "$SRC/README.md" ]    && cp -f "$SRC/README.md"    "$DEST/"
[ -f "$SRC/uninstall.sh" ] && cp -f "$SRC/uninstall.sh" "$DEST/" && chmod 0755 "$DEST/uninstall.sh"

# --- Geheimnisse -------------------------------------------------------------
if [ -f "$DEST/auth.env" ]; then
    say "Bestehende auth.env bleibt unveraendert"
else
    say "Erzeuge Geheimnisse"
    umask 077
    cat > "$DEST/auth.env" <<ENV
BETTER_AUTH_SECRET=$(openssl rand -base64 36 | tr -d '\n')
PROXFY_INTERNAL_SECRET=$(openssl rand -hex 32)
BETTER_AUTH_URL=http://$PUBLIC_IP:$PORT
PROXFY_TRUSTED_ORIGINS=http://$PUBLIC_IP:$PORT,http://$(hostname):$PORT,http://localhost:$PORT
PROXFY_AUTH_DB=$DEST/auth.db
PROXFY_AUTH_PORT=$AUTH_PORT
ENV
    chmod 600 "$DEST/auth.env"
fi

# --- Anmeldedienst -----------------------------------------------------------
say "Installiere den Anmeldedienst (Better Auth)"
( cd "$DEST/auth" && npm install --omit=dev --no-audit --no-fund >/dev/null 2>&1 ) \
    || die "npm install fehlgeschlagen."

say "Lege das Datenbankschema an"
( cd "$DEST/auth" && set -a && . "$DEST/auth.env" && set +a \
  && npx --yes auth@latest migrate --yes >/dev/null 2>&1 ) \
    || die "Migration des Anmeldeschemas fehlgeschlagen."

# --- Konfiguration -----------------------------------------------------------
if [ -f "$DEST/config.yaml" ]; then
    say "Bestehende config.yaml bleibt unveraendert"
else
    say "Konfiguration erzeugen"
    if [ "$MODE" = "lokal" ]; then
        HOSTBLOCK="  host: local"
        PVESM="pvesm"
    else
        HOSTBLOCK="  host: $PVE_HOST
  user: root
  key_file: $PVE_KEY"
        PVESM="ssh -o BatchMode=yes -i $PVE_KEY root@$PVE_HOST pvesm"
    fi

    BACKUP_STORE=$($PVESM status --content backup 2>/dev/null | awk 'NR>1 {print $1; exit}')
    BACKUP_STORE="${BACKUP_STORE:-local}"
    TARGET_STORE=$($PVESM status --content images 2>/dev/null | awk 'NR>1 {print $1; exit}')
    [ -n "$TARGET_STORE" ] || TARGET_STORE=$($PVESM status --content rootdir 2>/dev/null | awk 'NR>1 {print $1; exit}')
    TARGET_STORE="${TARGET_STORE:-local-lvm}"

    cat > "$DEST/config.yaml" <<CFG
# Restore-Verifikation - Grundeinstellungen.
# Backup-Quelle, Ziel-Storage und Cluster-Knoten sind in der Oberflaeche pro
# Lauf waehlbar; die Werte hier sind nur die Vorauswahl.
host:
$HOSTBLOCK

restore:
  backup_storage: $BACKUP_STORE
  target_storage: $TARGET_STORE
  isolated_bridge: vmbr9
  lan_bridge: vmbr0
  boot_timeout: 300
  agent_timeout: 240

auth:
  env_file: $DEST/auth.env
  port: $AUTH_PORT

# Nur einschalten, wenn wirklich ein Reverse Proxy davorsteht. Sonst koennte
# sich jeder eine fremde Herkunftsadresse ausdenken und die Anmeldesperre
# umgehen.
trust_forwarded_for: false

# Nur fuer den Sammellauf auf der Kommandozeile. Loest von sich aus NICHTS aus.
targets: []
CFG
    say "  Backup-Quelle $BACKUP_STORE, Ziel $TARGET_STORE"
fi

# --- Dienste -----------------------------------------------------------------
say "systemd-Units schreiben"
cat > /etc/systemd/system/proxfy-auth.service <<UNIT
[Unit]
Description=Anmeldedienst (Better Auth) fuer die Restore-Verifikation
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DEST/auth
EnvironmentFile=$DEST/auth.env
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/proxfy.service <<UNIT
[Unit]
Description=Restore-Verifikation fuer Proxmox Backup Server
Documentation=file://$DEST/README.md
After=network-online.target proxfy-auth.service
Wants=network-online.target
Requires=proxfy-auth.service

[Service]
Type=simple
WorkingDirectory=$DEST
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -m proxfy.cli --config $DEST/config.yaml serve --port $PORT --db $DEST/proxfy.db
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable proxfy-auth proxfy >/dev/null 2>&1
systemctl restart proxfy-auth
sleep 3
systemctl restart proxfy
sleep 3

for unit in proxfy-auth proxfy; do
    if ! systemctl is-active --quiet "$unit"; then
        printf 'FEHLER: %s laeuft nicht.\n' "$unit" >&2
        journalctl -u "$unit" -n 25 --no-pager >&2
        exit 1
    fi
done

cat <<DONE

Fertig.

  Weboberflaeche   http://$PUBLIC_IP:$PORT/
  Konfiguration    $DEST/config.yaml
  Geheimnisse      $DEST/auth.env   (nur fuer root lesbar)
  Datenbanken      $DEST/proxfy.db, $DEST/auth.db
  Deinstallation   bash $DEST/uninstall.sh

Der Anmeldedienst lauscht nur auf 127.0.0.1:$AUTH_PORT und ist von aussen
nicht erreichbar. Die einzige Tuer nach aussen ist Port $PORT.

NAECHSTER SCHRITT: Oberflaeche aufrufen und das erste Konto anlegen. Solange
das nicht geschehen ist, koennte es jeder tun, der die Adresse erreicht.

Testgaeste entstehen ausschliesslich im VMID-Bereich 9000-9099.
Es wurde KEIN Zeitplan angelegt.
DONE
