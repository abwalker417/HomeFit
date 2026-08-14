# BuiltHere server specification

## Deployed placement

| Setting | Value |
|---|---|
| Proxmox host | `pm-nuc11` (`192.168.68.6`) |
| Container | CT120 |
| Hostname | `homefit-v2` |
| Address | `192.168.68.20/24` |
| OS | Debian 13 unprivileged LXC |
| Application port | `5000` |
| Initial resources | 2 vCPU, 2 GiB RAM, 12 GiB disk |
| Timezone | `America/Denver` |
| App user | `homefit` |
| App path | `/opt/homefit` |
| Data path | `/var/lib/homefit` |
| Config path | `/etc/homefit/homefit.env` |
| Service | `homefit.service` |

CT120 is deployed and active. Do not run the creator again with CTID 120; use
`/usr/local/sbin/homefit-update` inside the existing container.

The weekly Proxmox backup job covers newly created containers automatically,
but V2 should also create an application-consistent SQLite backup before schema
migrations and cutovers.

## Isolation rules

- Do not mount or share CT115's live SQLite database.
- Do not reuse CT115's Flask session secret, device tokens, or push tables.
- Use a distinct Cloudflare hostname during development.
- Do not register production APNs devices against V2 by default.
- Use a separate PeakAI application key so V2 usage is attributable.
- Keep uploads and generated exercise media in V2-owned storage.

## Suggested hostname

Use a private LAN address during household testing. The purchased public domain
is `getbuilthere.com`; do not attach it to the service until HTTPS, access
control, and the cutover checklist are complete.

## Required environment

```dotenv
HOMEFIT_ENV=production
HOMEFIT_HOST=127.0.0.1
HOMEFIT_PORT=5000
HOMEFIT_DATA_DIR=/var/lib/homefit
HOMEFIT_SESSION_SECURE=1
HOMEFIT_SECRET_KEY_FILE=/etc/homefit/session.key
PEAKAI_URL=http://192.168.68.33:4000
PEAKAI_API_KEY=replace-with-v2-scoped-key
PEAKAI_MODEL=claude-sonnet
```

These legacy environment names remain supported for the current deployment.
BuiltHere can instead use any OpenAI-compatible endpoint; the household owner
can configure its base URL, key, and separate Coach/fast/vision models from
the in-app Settings workspace. The API key is not displayed after saving and
is stored only in the protected application database. For headless setup, use
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_COACH_MODEL`,
`OPENAI_FAST_MODEL`, and `OPENAI_VISION_MODEL`.

Secrets must be installed directly on the container with owner-only
permissions. They do not belong in Git or in the Cloudflare hostname config.

The installer initially sets `HOMEFIT_ALLOW_PROFILE_CREATION=1` so the first
owner can be created over the LAN. Set it to `0` and restart BuiltHere before
adding the Cloudflare hostname. Existing owners can still manage household
profiles through the authenticated application flow.

## Network exposure sequence

1. Bind Gunicorn to loopback and proxy it through a local web server, or bind to
   the LAN only while developing.
2. Validate `/healthz` from the container.
3. Validate the application from the LAN at `.20`.
4. Add an Uptime Kuma monitor.
5. Add the Cloudflare tunnel hostname route.
6. Run browser and iOS beta checks through the public hostname.

## Data strategy

V2 uses its own production-mode database. Any further V1 data refresh must use
an explicit snapshot workflow:

1. Create an online SQLite backup on CT115 without stopping production.
2. Copy that backup to CT120 as a dated, read-only source artifact.
3. Restore into a new V2 database path.
4. Run migrations.
5. Compare user, workout, weight, health, food, plan, and token row counts.
6. Strip or rotate device/API credentials for beta unless deliberately retained.

Never point both application versions at the same writable SQLite file.

## Provisioning checkpoints

Provisioning changes the homelab and therefore remains an explicit operation.
Before running it, confirm:

- CT120 and `192.168.68.20` are still free.
- The Debian 13 template name on `local` storage.
- Available `local-lvm` capacity and host memory.
- Whether the V2 hostname should be public through Cloudflare immediately.
- Whether V2 needs production-like APNs during beta.

## Installer paths

BuiltHere ships two deployment entrypoints. Compatibility filenames and paths
retain the existing `homefit` name:

- `scripts/homefit-v2-lxc.sh` is the immediately usable, standalone creator for
  the repository. It follows the Helper-Scripts interaction model but does not
  depend on BuiltHere already being accepted upstream.
- `ct/homefit.sh` plus `install/homefit-install.sh` follow the current
  community-scripts repository layout and are intended for a future upstream
  contribution. The official build framework downloads installers from its own
  `install/` directory, so that entrypoint becomes directly usable only after
  the pair exists in community-scripts/ProxmoxVE (or a maintained fork).

Both layouts install the versioned `install/homefit-update.sh` as
`/usr/local/sbin/homefit-update`. Updates are staged as a
new release with a separate virtual environment, checked by importing the app,
then activated with an atomic `current` symlink. SQLite is backed up before the
new release is validated, and a failed HTTP health check restores the previous
application symlink. A successful release refreshes the updater and retains the
five newest releases plus fourteen SQLite snapshots.

From the Proxmox host, invoke the updater with its absolute path because
`pct exec` does not always include `/usr/local/sbin` in `PATH`:

```bash
pct exec 120 -- /usr/local/sbin/homefit-update
```
