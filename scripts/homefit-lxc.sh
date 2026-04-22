#!/usr/bin/env bash
# HomeFit — Proxmox LXC installer
# Creates a Debian 12 unprivileged LXC, installs HomeFit, and starts it as
# a systemd service. Inspired by community-scripts.org.
#
# Usage (run on your Proxmox host, as root):
#   bash -c "$(wget -qLO - https://YOUR-URL/homefit-lxc.sh)"
#
# Or with env overrides:
#   APP_REPO=https://github.com/you/homefit.git CTID=201 bash -c "$(...)"
#
set -Eeuo pipefail

# ---------- pretty output ---------------------------------------------------
YW=$'\033[33m'; BL=$'\033[36m'; GN=$'\033[32m'; RD=$'\033[31m'; CL=$'\033[0m'
CM=" ${GN}✓${CL}"; XM=" ${RD}✗${CL}"; IN=" ${BL}➜${CL}"
msg_info()  { echo -e "${IN} ${YW}${1}${CL}"; }
msg_ok()    { echo -e "${CM} ${1}"; }
msg_error() { echo -e "${XM} ${RD}${1}${CL}"; }
die()       { msg_error "$1"; exit 1; }

trap 'msg_error "An error occurred on line $LINENO. Check output above."' ERR

banner() {
  clear
  cat <<'EOF'
    __  __                   _____ _ __
   / / / /___  ____ ___  ___/ __(_) /_
  / /_/ / __ \/ __ `__ \/ _ \ /_/ / __/
 / __  / /_/ / / / / / /  __/ __/ / /_
/_/ /_/\____/_/ /_/ /_/\___/_/ /_/\__/

   Self-hosted home workout LXC installer
EOF
  echo
}

# ---------- preflight -------------------------------------------------------
[[ $EUID -eq 0 ]] || die "Run this script as root on your Proxmox host."
command -v pveversion >/dev/null || die "This doesn't look like a Proxmox host (pveversion not found)."
command -v pct        >/dev/null || die "pct command missing — are you on a Proxmox node?"
command -v whiptail   >/dev/null || die "whiptail missing — install with: apt install whiptail"

banner

# ---------- defaults (override via env) ------------------------------------
APP_NAME="${APP_NAME:-homefit}"
APP_REPO="${APP_REPO:-https://github.com/CHANGE_ME/homefit.git}"
APP_BRANCH="${APP_BRANCH:-main}"
APP_PORT="${APP_PORT:-5000}"

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_HOSTNAME_DEFAULT="${CT_HOSTNAME:-homefit}"
TEMPLATE_STORAGE_DEFAULT="local"
ROOTFS_STORAGE_DEFAULT="local-lvm"
DISK_DEFAULT="4"
CPU_DEFAULT="1"
RAM_DEFAULT="512"
BRIDGE_DEFAULT="vmbr0"
IP_DEFAULT="dhcp"

# ---------- prompts ---------------------------------------------------------
ask_input() {
  # $1 title, $2 prompt, $3 default — returns echoed value
  whiptail --title "HomeFit Installer" --inputbox "$2" 10 70 "$3" 3>&1 1>&2 2>&3
}
ask_password() {
  whiptail --title "HomeFit Installer" --passwordbox "$1" 10 70 3>&1 1>&2 2>&3
}

whiptail --title "HomeFit LXC Installer" \
  --yesno "This will create a new Debian 12 LXC and install HomeFit.\n\nContinue?" \
  12 70 || die "Aborted."

CTID=$(ask_input "Container ID" "Container ID (pick a free one):" "$CTID")
CT_HOST=$(ask_input "Hostname" "Hostname:" "$CT_HOSTNAME_DEFAULT")
CPU=$(ask_input "CPU cores" "CPU cores:" "$CPU_DEFAULT")
RAM=$(ask_input "Memory (MB)" "Memory in MB:" "$RAM_DEFAULT")
DISK=$(ask_input "Disk (GB)" "Root disk size in GB:" "$DISK_DEFAULT")
TEMPLATE_STORAGE=$(ask_input "Template storage" "Storage pool that holds CT templates:" "$TEMPLATE_STORAGE_DEFAULT")
ROOTFS_STORAGE=$(ask_input "Root FS storage" "Storage pool for the root disk:" "$ROOTFS_STORAGE_DEFAULT")
BRIDGE=$(ask_input "Bridge" "Network bridge:" "$BRIDGE_DEFAULT")
IP=$(ask_input "IP address" "IP address (use 'dhcp' or CIDR like 192.168.1.50/24):" "$IP_DEFAULT")

GATEWAY=""
if [[ "$IP" != "dhcp" ]]; then
  GATEWAY=$(ask_input "Gateway" "Default gateway:" "$(echo "$IP" | awk -F. '{print $1"."$2"."$3".1"}' | cut -d/ -f1)")
fi

REPO=$(ask_input "Git repo" "Git URL of your HomeFit repo:" "$APP_REPO")
BRANCH=$(ask_input "Branch" "Git branch:" "$APP_BRANCH")
PORT=$(ask_input "App port" "Port HomeFit will listen on:" "$APP_PORT")
ROOT_PW=$(ask_password "Set the container's root password (needed only to enter it later):")
[[ -n "$ROOT_PW" ]] || die "Root password cannot be empty."

whiptail --title "Review" --yesno \
  "Create CT $CTID ($CT_HOST) with:\n  $CPU vCPU, ${RAM}MB RAM, ${DISK}GB disk\n  bridge $BRIDGE, ip $IP\n  repo: $REPO ($BRANCH)\n  port: $PORT\n\nProceed?" \
  16 70 || die "Aborted."

# ---------- download template if needed ------------------------------------
msg_info "Checking CT template"
TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
TEMPLATE_PATH_CHECK="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | grep -E "debian-12.*amd64" | head -n1 || true)"

if [[ -z "$TEMPLATE_PATH_CHECK" ]]; then
  msg_info "Updating template list"
  pveam update >/dev/null
  TEMPLATE="$(pveam available | awk '/debian-12-standard.*amd64/ {print $2}' | tail -n1)"
  [[ -n "$TEMPLATE" ]] || die "Couldn't find a debian-12-standard template on pveam."
  msg_info "Downloading $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
  TEMPLATE_PATH_CHECK="${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}"
fi
msg_ok "Template ready: $TEMPLATE_PATH_CHECK"

# ---------- build network spec ---------------------------------------------
if [[ "$IP" == "dhcp" ]]; then
  NET="name=eth0,bridge=${BRIDGE},ip=dhcp"
else
  NET="name=eth0,bridge=${BRIDGE},ip=${IP},gw=${GATEWAY}"
fi

# ---------- create LXC ------------------------------------------------------
msg_info "Creating LXC $CTID"
pct create "$CTID" "$TEMPLATE_PATH_CHECK" \
  --hostname "$CT_HOST" \
  --cores "$CPU" \
  --memory "$RAM" \
  --swap 512 \
  --rootfs "${ROOTFS_STORAGE}:${DISK}" \
  --net0 "$NET" \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 \
  --onboot 1 \
  --password "$ROOT_PW" \
  --description "HomeFit self-hosted workout app" >/dev/null
msg_ok "LXC $CTID created"

msg_info "Starting LXC"
pct start "$CTID" >/dev/null
sleep 5

# Wait for network if dhcp
if [[ "$IP" == "dhcp" ]]; then
  msg_info "Waiting for DHCP lease"
  for _ in $(seq 1 20); do
    if pct exec "$CTID" -- sh -c 'getent hosts deb.debian.org >/dev/null 2>&1'; then
      break
    fi
    sleep 1
  done
fi
msg_ok "LXC is online"

# ---------- install inside LXC ---------------------------------------------
msg_info "Installing OS packages inside LXC"
pct exec "$CTID" -- bash -Eeuo pipefail <<'INNER'
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-pip git curl ca-certificates >/dev/null
INNER
msg_ok "OS packages installed"

msg_info "Creating homefit user"
pct exec "$CTID" -- bash -Eeuo pipefail <<'INNER'
id homefit &>/dev/null || adduser --disabled-password --gecos "" homefit >/dev/null
INNER
msg_ok "User created"

msg_info "Cloning $REPO"
pct exec "$CTID" -- bash -Eeuo pipefail -c "
  sudo -u homefit bash -c '
    cd /home/homefit &&
    if [ -d workout-app/.git ]; then
      cd workout-app && git fetch --all --quiet && git reset --hard origin/$BRANCH
    else
      git clone --quiet --branch $BRANCH $REPO workout-app
    fi
  '
"
msg_ok "Repo cloned"

msg_info "Installing Python dependencies"
pct exec "$CTID" -- bash -Eeuo pipefail <<'INNER'
sudo -u homefit bash -c '
  cd /home/homefit/workout-app &&
  python3 -m venv .venv &&
  .venv/bin/pip install --quiet --upgrade pip &&
  .venv/bin/pip install --quiet -r requirements.txt &&
  .venv/bin/pip install --quiet gunicorn
'
INNER
msg_ok "Dependencies installed"

msg_info "Writing systemd unit"
pct exec "$CTID" -- bash -Eeuo pipefail -c "
cat > /etc/systemd/system/${APP_NAME}.service <<EOF
[Unit]
Description=HomeFit
After=network.target

[Service]
Type=simple
User=homefit
Group=homefit
WorkingDirectory=/home/homefit/workout-app
Environment=\"HOMEFIT_DB=/home/homefit/workout-app/data/workout.db\"
ExecStart=/home/homefit/workout-app/.venv/bin/gunicorn --workers 2 --bind 0.0.0.0:${PORT} app:app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ${APP_NAME}.service >/dev/null
"
msg_ok "Service enabled and started"

# ---------- finished -------------------------------------------------------
# Figure out the IP we should print
if [[ "$IP" == "dhcp" ]]; then
  CT_IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)"
else
  CT_IP="${IP%/*}"
fi

echo
msg_ok "HomeFit is up and running."
echo
echo "   LXC ID:   $CTID"
echo "   Hostname: $CT_HOST"
echo "   URL:      http://${CT_IP:-<unknown>}:${PORT}"
echo
echo "   Enter the container with: pct enter $CTID"
echo "   View logs with:           pct exec $CTID -- journalctl -u ${APP_NAME} -f"
echo "   Update later with:        pct exec $CTID -- sudo -u homefit bash -c 'cd ~/workout-app && git pull && .venv/bin/pip install -r requirements.txt' && pct exec $CTID -- systemctl restart ${APP_NAME}"
echo
