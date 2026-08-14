#!/usr/bin/env bash
# HOMEFIT_INSTALLER_V2=1
# HomeFit V2 in-container installer. Normally invoked by homefit-v2-lxc.sh.
set -Eeuo pipefail

HOMEFIT_REPO="${HOMEFIT_REPO:-https://github.com/abwalker417/HomeFit.git}"
HOMEFIT_BRANCH="${HOMEFIT_BRANCH:-main}"
HOMEFIT_PORT="${HOMEFIT_PORT:-5000}"

[[ ${EUID} -eq 0 ]] || { echo "Run as root inside the LXC." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "[1/7] Installing dependencies"
apt-get update -qq
apt-get install -y -qq \
  ca-certificates curl ffmpeg git openssl python3 python3-pip python3-venv sqlite3

echo "[2/7] Creating service account and persistent paths"
id homefit >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/homefit --shell /usr/sbin/nologin homefit
install -d -o homefit -g homefit -m 0750 \
  /opt/homefit/releases /var/lib/homefit /var/lib/homefit/backups \
  /var/lib/homefit/catalog /var/lib/homefit/uploads
install -d -o root -g homefit -m 0750 /etc/homefit

echo "[3/7] Installing the atomic updater"
cat >/usr/local/sbin/homefit-update <<'UPDATER'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ ${EUID} -eq 0 ]] || { echo "Run homefit-update as root." >&2; exit 1; }
exec 9>/run/lock/homefit-update.lock
flock -n 9 || { echo "Another HomeFit update is already running." >&2; exit 1; }

source /etc/homefit/release.conf
remote_commit="$(git ls-remote "$HOMEFIT_REPO" "refs/heads/$HOMEFIT_BRANCH" | awk '{print $1}')"
[[ "$remote_commit" =~ ^[0-9a-f]{40}$ ]] || { echo "Could not resolve the configured branch." >&2; exit 1; }
short_commit="${remote_commit:0:12}"
current_commit="$(cat /opt/homefit/current/.homefit-version 2>/dev/null || true)"
if [[ "$current_commit" == "$remote_commit" ]]; then
  echo "HomeFit is already current (${short_commit})."
  exit 0
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
release_tmp="/opt/homefit/releases/.staging-${short_commit}-${timestamp}"
release_dir="/opt/homefit/releases/${short_commit}-${timestamp}"
previous="$(readlink -f /opt/homefit/current 2>/dev/null || true)"

cleanup() { [[ -d "$release_tmp" ]] && rm -rf -- "$release_tmp"; }
trap cleanup EXIT

echo "Backing up SQLite data"
if [[ -f /var/lib/homefit/workout.db ]]; then
  sqlite3 /var/lib/homefit/workout.db ".backup '/var/lib/homefit/backups/workout-${timestamp}.db'"
fi

echo "Downloading ${HOMEFIT_BRANCH}@${short_commit}"
git clone --quiet --depth 1 --branch "$HOMEFIT_BRANCH" "$HOMEFIT_REPO" "$release_tmp"
actual_commit="$(git -C "$release_tmp" rev-parse HEAD)"
[[ "$actual_commit" == "$remote_commit" ]] || { echo "Downloaded commit did not match the resolved branch." >&2; exit 1; }

python3 -m venv "$release_tmp/.venv"
"$release_tmp/.venv/bin/pip" install --quiet --upgrade pip
"$release_tmp/.venv/bin/pip" install --quiet -r "$release_tmp/requirements.txt" gunicorn

for catalog_file in exercises.json exercise_animations.json; do
  if [[ ! -f "/var/lib/homefit/catalog/$catalog_file" && -f "$release_tmp/data/$catalog_file" ]]; then
    cp "$release_tmp/data/$catalog_file" "/var/lib/homefit/catalog/$catalog_file"
  fi
  if [[ -f "/var/lib/homefit/catalog/$catalog_file" ]]; then
    rm -f -- "$release_tmp/data/$catalog_file"
    ln -s "/var/lib/homefit/catalog/$catalog_file" "$release_tmp/data/$catalog_file"
  fi
done
rm -rf -- "$release_tmp/static/uploads"
ln -s /var/lib/homefit/uploads "$release_tmp/static/uploads"
printf '%s\n' "$actual_commit" >"$release_tmp/.homefit-version"
chown -R homefit:homefit "$release_tmp"
mv "$release_tmp" "$release_dir"

echo "Validating the new release"
set -a
source /etc/homefit/homefit.env
set +a
runuser -u homefit -- "$release_dir/.venv/bin/python" -c "import sys; sys.path.insert(0, '$release_dir'); import app"

ln -sfn "$release_dir" /opt/homefit/current
systemctl restart homefit
if ! curl -fsS --max-time 15 "http://127.0.0.1:${HOMEFIT_PORT}/profiles" >/dev/null; then
  echo "Health check failed; rolling back the application symlink." >&2
  if [[ -n "$previous" && -d "$previous" ]]; then
    ln -sfn "$previous" /opt/homefit/current
    systemctl restart homefit
  fi
  exit 1
fi

echo "HomeFit updated successfully to ${short_commit}."
UPDATER
chmod 0755 /usr/local/sbin/homefit-update

echo "[4/7] Writing configuration"
cat >/etc/homefit/release.conf <<EOF
HOMEFIT_REPO=${HOMEFIT_REPO}
HOMEFIT_BRANCH=${HOMEFIT_BRANCH}
HOMEFIT_PORT=${HOMEFIT_PORT}
EOF
chmod 0644 /etc/homefit/release.conf

if [[ ! -f /etc/homefit/homefit.env ]]; then
  session_key="$(openssl rand -hex 48)"
  printf '%s' "$session_key" >/etc/homefit/session.key
  chown root:homefit /etc/homefit/session.key
  chmod 0640 /etc/homefit/session.key
  cat >/etc/homefit/homefit.env <<EOF
HOMEFIT_ENV=development
HOMEFIT_DB=/var/lib/homefit/workout.db
HOMEFIT_SECRET_KEY_FILE=/etc/homefit/session.key
HOMEFIT_SESSION_SECURE=0
HOMEFIT_ALLOW_PROFILE_CREATION=1
PEAKAI_URL=http://192.168.68.33:4000
PEAKAI_API_KEY=
PEAKAI_MODEL=claude-sonnet
PEAKAI_CHEAP_MODEL=gpt-4o-mini
PEAKAI_VISION_MODEL=gpt-4o
EOF
  chown root:homefit /etc/homefit/homefit.env
  chmod 0640 /etc/homefit/homefit.env
fi

echo "[5/7] Writing systemd service"
cat >/etc/systemd/system/homefit.service <<EOF
[Unit]
Description=HomeFit self-hosted fitness platform
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=homefit
Group=homefit
WorkingDirectory=/opt/homefit/current
EnvironmentFile=/etc/homefit/homefit.env
ExecStart=/opt/homefit/current/.venv/bin/gunicorn --workers 2 --threads 2 --timeout 180 --bind 0.0.0.0:${HOMEFIT_PORT} app:app
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/var/lib/homefit /opt/homefit

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable homefit >/dev/null

echo "[6/7] Installing the initial release"
homefit-update

echo "[7/7] Verifying HomeFit"
systemctl is-active --quiet homefit
curl -fsS --max-time 15 "http://127.0.0.1:${HOMEFIT_PORT}/profiles" >/dev/null
rm -f /root/homefit-install.sh
echo "HomeFit installation completed."
