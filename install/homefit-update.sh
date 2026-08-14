#!/usr/bin/env bash
# HOMEFIT_UPDATER_V2=1
# Atomic, self-refreshing updater for an installed BuiltHere container.
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "Run homefit-update as root." >&2; exit 1; }
exec 9>/run/lock/homefit-update.lock
flock -n 9 || { echo "Another BuiltHere update is already running." >&2; exit 1; }

source /etc/homefit/release.conf
remote_commit="$(git ls-remote "$HOMEFIT_REPO" "refs/heads/$HOMEFIT_BRANCH" | awk '{print $1}')"
[[ "$remote_commit" =~ ^[0-9a-f]{40}$ ]] || { echo "Could not resolve the configured branch." >&2; exit 1; }
short_commit="${remote_commit:0:12}"
current_commit="$(cat /opt/homefit/current/.homefit-version 2>/dev/null || true)"
if [[ "$current_commit" == "$remote_commit" ]]; then
  echo "BuiltHere is already current (${short_commit})."
  exit 0
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
release_dir="/opt/homefit/releases/${short_commit}-${timestamp}"
release_tmp="$release_dir"
previous=""
if [[ -L /opt/homefit/current ]]; then
  resolved_previous="$(readlink -f /opt/homefit/current 2>/dev/null || true)"
  if [[ -n "$resolved_previous" && -d "$resolved_previous" && "$resolved_previous" != "/opt/homefit/current" ]]; then
    previous="$resolved_previous"
  fi
fi

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

echo "Validating the new release"
set -a
source /etc/homefit/homefit.env
set +a
runuser -u homefit -- "$release_dir/.venv/bin/python" -c "import sys; sys.path.insert(0, '$release_dir'); import app"

ln -sfnT "$release_dir" /opt/homefit/current
systemctl restart homefit
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${HOMEFIT_PORT}/healthz" >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "$healthy" != "1" ]]; then
  echo "Health check failed; rolling back the application symlink." >&2
  if [[ -n "$previous" && -d "$previous" ]]; then
    ln -sfnT "$previous" /opt/homefit/current
    systemctl restart homefit
  else
    rm -f -- /opt/homefit/current
    systemctl stop homefit
  fi
  exit 1
fi

release_tmp=""

# Upgrade the updater only from a release that has passed validation/readiness.
if [[ -f "$release_dir/install/homefit-update.sh" ]] && \
   grep -q 'HOMEFIT_UPDATER_V2=1' "$release_dir/install/homefit-update.sh"; then
  install -o root -g root -m 0755 "$release_dir/install/homefit-update.sh" /usr/local/sbin/homefit-update
fi

# Retain five releases and fourteen database snapshots for local rollback.
mapfile -t stale_releases < <(
  find /opt/homefit/releases -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr | awk 'NR > 5 {sub(/^[^ ]+ /, ""); print}'
)
for stale_release in "${stale_releases[@]}"; do
  [[ "$stale_release" == "$(readlink -f /opt/homefit/current)" ]] || rm -rf -- "$stale_release"
done

mapfile -t stale_backups < <(
  find /var/lib/homefit/backups -mindepth 1 -maxdepth 1 -type f -name 'workout-*.db' -printf '%T@ %p\n' \
    | sort -nr | awk 'NR > 14 {sub(/^[^ ]+ /, ""); print}'
)
for stale_backup in "${stale_backups[@]}"; do
  rm -f -- "$stale_backup"
done

echo "BuiltHere updated successfully to ${short_commit}."
