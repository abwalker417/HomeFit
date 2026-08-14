#!/usr/bin/env bash
# BuiltHere — standalone Proxmox VE Helper-style LXC creator.
# Run as root on a Proxmox VE host. No existing containers are modified.
set -Eeuo pipefail

APP_REPO="${APP_REPO:-https://github.com/abwalker417/HomeFit.git}"
APP_BRANCH="${APP_BRANCH:-codex/v2-foundation}"
INSTALLER_URL="${INSTALLER_URL:-https://raw.githubusercontent.com/abwalker417/HomeFit/refs/heads/${APP_BRANCH}/install/homefit-install.sh}"
UPDATER_URL="${UPDATER_URL:-https://raw.githubusercontent.com/abwalker417/HomeFit/refs/heads/${APP_BRANCH}/install/homefit-update.sh}"

CTID="${CTID:-120}"
CT_HOSTNAME="${CT_HOSTNAME:-homefit-v2}"
CT_IP="${CT_IP:-192.168.68.20/24}"
CT_GATEWAY="${CT_GATEWAY:-192.168.68.1}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_STORAGE="${CT_STORAGE:-local-lvm}"
CT_TEMPLATE_STORAGE="${CT_TEMPLATE_STORAGE:-local}"
CT_CORES="${CT_CORES:-2}"
CT_RAM="${CT_RAM:-2048}"
CT_SWAP="${CT_SWAP:-512}"
CT_DISK="${CT_DISK:-12}"
APP_PORT="${APP_PORT:-5000}"

YW=$'\033[33m'; GN=$'\033[32m'; RD=$'\033[31m'; CY=$'\033[36m'; CL=$'\033[0m'
info() { printf ' %b➜%b %s\n' "$CY" "$CL" "$1"; }
ok() { printf ' %b✓%b %s\n' "$GN" "$CL" "$1"; }
die() { printf ' %b✗%b %s\n' "$RD" "$CL" "$1" >&2; exit 1; }
trap 'die "Installation failed on line ${LINENO}. The new CT was not promoted to production."' ERR

[[ ${EUID} -eq 0 ]] || die "Run this script as root on the Proxmox VE host."
command -v pveversion >/dev/null || die "pveversion was not found."
command -v pct >/dev/null || die "pct was not found."
command -v pveam >/dev/null || die "pveam was not found."
command -v curl >/dev/null || die "curl was not found."

if pct status "$CTID" >/dev/null 2>&1; then
  die "CT ${CTID} already exists. Choose another CTID; this script will never overwrite it."
fi

printf '\n%bBuiltHere LXC%b\n\n' "$YW" "$CL"
printf ' CT:       %s (%s)\n' "$CTID" "$CT_HOSTNAME"
printf ' Network:  %s via %s\n' "$CT_IP" "$CT_GATEWAY"
printf ' Resources:%s cores, %s MiB RAM, %s GiB disk\n' "$CT_CORES" "$CT_RAM" "$CT_DISK"
printf ' Source:   %s (%s)\n\n' "$APP_REPO" "$APP_BRANCH"
read -r -p "Create this unprivileged Debian 13 container? [y/N] " answer
[[ "${answer,,}" =~ ^(y|yes)$ ]] || die "Cancelled."

info "Finding the latest Debian 13 template"
pveam update >/dev/null
template_name="$(pveam available --section system | awk '/debian-13-standard.*amd64/ {print $2}' | tail -n 1)"
[[ -n "$template_name" ]] || die "No Debian 13 amd64 template is available."
template_ref="${CT_TEMPLATE_STORAGE}:vztmpl/${template_name}"
if ! pveam list "$CT_TEMPLATE_STORAGE" | awk '{print $1}' | grep -Fxq "$template_ref"; then
  pveam download "$CT_TEMPLATE_STORAGE" "$template_name"
fi
ok "Template ready: ${template_ref}"

info "Downloading the BuiltHere container installer"
installer_tmp="$(mktemp /tmp/homefit-install.XXXXXX.sh)"
updater_tmp="$(mktemp /tmp/homefit-update.XXXXXX.sh)"
curl -fsSL "$INSTALLER_URL" -o "$installer_tmp"
curl -fsSL "$UPDATER_URL" -o "$updater_tmp"
grep -q 'HOMEFIT_INSTALLER_V2=1' "$installer_tmp" || die "Downloaded installer failed its identity check."
grep -q 'HOMEFIT_UPDATER_V2=1' "$updater_tmp" || die "Downloaded updater failed its identity check."
chmod 0755 "$installer_tmp"
chmod 0755 "$updater_tmp"
ok "Installer verified"

info "Creating CT ${CTID}"
pct create "$CTID" "$template_ref" \
  --hostname "$CT_HOSTNAME" \
  --cores "$CT_CORES" \
  --memory "$CT_RAM" \
  --swap "$CT_SWAP" \
  --rootfs "${CT_STORAGE}:${CT_DISK}" \
  --net0 "name=eth0,bridge=${CT_BRIDGE},ip=${CT_IP},gw=${CT_GATEWAY}" \
  --unprivileged 1 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --start 1 \
  --description "BuiltHere self-hosted fitness platform"
ok "CT ${CTID} created"

info "Waiting for network and DNS"
for _ in $(seq 1 45); do
  if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then break; fi
  sleep 2
done
pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 || die "The container has no working network/DNS."
ok "Container network is ready"

info "Installing BuiltHere inside CT ${CTID}"
pct push "$CTID" "$installer_tmp" /root/homefit-install.sh -perms 0755
pct push "$CTID" "$updater_tmp" /root/homefit-update.sh -perms 0755
pct exec "$CTID" -- env \
  HOMEFIT_REPO="$APP_REPO" \
  HOMEFIT_BRANCH="$APP_BRANCH" \
  HOMEFIT_PORT="$APP_PORT" \
  bash /root/homefit-install.sh
rm -f "$installer_tmp"
rm -f "$updater_tmp"

ip_without_prefix="${CT_IP%/*}"
ok "BuiltHere is installed"
printf '\n URL:       http://%s:%s\n' "$ip_without_prefix" "$APP_PORT"
printf ' Logs:      pct exec %s -- journalctl -u homefit -f\n' "$CTID"
printf ' Update:    pct exec %s -- /usr/local/sbin/homefit-update\n' "$CTID"
printf ' Configure: pct enter %s; nano /etc/homefit/homefit.env\n\n' "$CTID"
printf 'Cloudflare and APNs are intentionally not configured by this installer.\n'
