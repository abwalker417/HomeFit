# HomeFit

A tiny self-hosted home-workout planner. Built for iPhone use: install it to
your home screen as a PWA, pick a workout, and run it. Everything is
bodyweight-only — no special equipment required.

## What it does

- **Multiple profiles** — share the app with a partner or household. Each
  profile has its own plan, weight log, and workout history. Optional 4–8
  digit PIN per profile.
- Takes your current weight, goal weight, fitness level, and physical
  limitations (bad back / knees / shoulders / wrists) and builds a weekly
  workout plan.
- Filters exercises that conflict with your limitations so you never see
  push-ups with wrist issues or jumping jacks with bad knees.
- Tracks weight over time and completed workouts per user.
- Exercise demos open as YouTube "proper form" searches in a new tab.
- Installable on iPhone as a standalone app (Add to Home Screen).

## Requirements

- Python 3.10+
- A machine to run it on (laptop, Raspberry Pi, small VPS). Your iPhone must
  be able to reach it on the network.

## Install & run

```bash
cd workout-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The app starts on `http://0.0.0.0:5000`. From your iPhone (same Wi-Fi),
open `http://<your-machine-ip>:5000`. Find your IP with `ipconfig getifaddr en0`
on macOS or `hostname -I` on Linux.

### Installing on your iPhone

1. Open the app URL in Safari on iPhone.
2. Tap the **Share** button → **Add to Home Screen**.
3. Launch it from the home screen — it runs full-screen like a native app.

## Proxmox LXC — one-liner installer

`scripts/homefit-lxc.sh` creates a Debian 12 unprivileged LXC, clones this
repo into it, installs the Python deps, and registers a gunicorn systemd
service. It uses whiptail prompts in the style of community-scripts.org.

### Step 1 — push this project to a Git repo

Create a public or private repo on GitHub / Gitea / GitLab and push the
`workout-app` folder:

```bash
cd workout-app
git init
git add .
git commit -m "Initial HomeFit"
git branch -M main
git remote add origin https://github.com/YOUR_USER/homefit.git
git push -u origin main
```

(A private repo works too — you'll just need to configure a deploy key or
HTTPS token on the LXC first. For personal use a public repo is easiest.)

### Step 2 — host the installer script somewhere the Proxmox host can fetch

Options, in increasing order of hassle:

1. **Raw GitHub**: push `scripts/homefit-lxc.sh` to the same repo; use the
   raw URL, e.g. `https://raw.githubusercontent.com/YOU/homefit/main/scripts/homefit-lxc.sh`.
2. **Gist**: paste the script into a GitHub Gist and use the raw URL.
3. **Your own Gitea / webserver**: any URL that returns the script as plain
   text works.

### Step 3 — run it on your Proxmox node

SSH into the Proxmox host (or open its web shell) as root, then:

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/YOUR_USER/homefit/main/scripts/homefit-lxc.sh)"
```

You'll be prompted (with sensible defaults) for:

- Container ID, hostname, cores, RAM, disk
- Template storage (default `local`) and root disk storage (default `local-lvm`)
- Network bridge and IP (`dhcp` or a CIDR like `192.168.1.50/24`)
- The Git repo URL and branch
- The port HomeFit should listen on
- A root password for the container

Then it creates the CT, installs everything, and prints the URL.

### Non-interactive install

Every prompt has an env-var override, so you can script the whole thing:

```bash
CTID=201 CT_HOSTNAME=homefit \
APP_REPO=https://github.com/YOU/homefit.git APP_BRANCH=main \
APP_PORT=5000 \
bash -c "$(wget -qLO - https://.../homefit-lxc.sh)"
```

You'll still be prompted only for the few that aren't overridden (e.g. the
root password, to avoid putting it on the command line).

### Updating later

From the Proxmox host:

```bash
pct exec <CTID> -- runuser -u homefit -- bash -c \
  'cd ~/workout-app && git pull && .venv/bin/pip install -r requirements.txt'
pct exec <CTID> -- systemctl restart homefit
```

Or just re-run the installer — it detects an existing clone and does a
`git reset --hard` to the chosen branch.

## Running it permanently (manual, no Proxmox)

### systemd (Linux)

Create `/etc/systemd/system/homefit.service`:

```ini
[Unit]
Description=HomeFit
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/workout-app
ExecStart=/path/to/workout-app/.venv/bin/python app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now homefit`.

### Docker (optional)

A minimal Dockerfile:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
```

Build & run:

```bash
docker build -t homefit .
docker run -d --name homefit -p 5000:5000 -v $(pwd)/data:/app/data homefit
```

## Data

Everything lives in `data/`:

- `exercises.json` — the exercise library. Edit freely to add your own moves.
  Each exercise has `contraindications` tags (`bad_back`, `bad_knees`,
  `bad_shoulders`, `bad_wrists`) that drive the filtering.
- `workout.db` — SQLite file storing your profile, weight log, and workout
  history. Back this up if you care about history.

## Exercise library

Exercises are tagged so the planner can skip anything that would aggravate an
injury. Tags: `bad_back`, `bad_knees`, `bad_shoulders`, `bad_wrists`. Adding a
new exercise is just appending an object to `data/exercises.json` — no code
changes. Fields:

```json
{
  "id": "short_unique_id",
  "name": "Display name",
  "category": "legs | upper | core | cardio",
  "difficulty": 1,                 // 1 beginner, 2 intermediate, 3 advanced
  "contraindications": ["bad_knees"],
  "default_reps": 10,
  "default_sets": 3,
  "rest_seconds": 45,
  "unit": "seconds",               // optional — omit for reps
  "instructions": "..."
}
```

## How plans are generated

`workout_logic.py` runs a deterministic rule-based algorithm:

1. Filter out exercises your limitations block.
2. Filter out exercises above your fitness level's difficulty cap.
3. Pick a weekly template (cut/bulk/maintain) and fill each day from the
   remaining exercise pools.
4. Adjust sets / reps / rest based on your goal (cut = more reps, less rest;
   bulk = more sets, longer rest).

No API keys, no outside calls — it runs fully offline after install.

## Profiles & PIN

The first launch sends you straight to "Create your profile". After that, the
home screen shows a picker (Netflix-style) where anyone in the household taps
their name to jump in.

- A profile can optionally set a 4–8 digit PIN. PINs are hashed (werkzeug's
  `generate_password_hash`) before storage — the raw PIN is never written to
  the database.
- Without a PIN, tapping a profile logs you straight in.
- The header shows the current user's emoji + name; tap the ↺ button to
  switch.
- Tap your name in the header to rename, change emoji, set/clear your PIN, or
  delete the profile.

Profiles are not "auth" in a strong security sense — anyone with network
access can try any profile. Use PINs as a friendly "don't poke my data" gate.
If you need actual security, put the app behind a reverse proxy (Caddy,
nginx, Tailscale) with real auth on top.

## Upgrading from a single-user install

The multi-user release bumps the SQLite schema from v1 to v2. On first run
with the new code, HomeFit detects the old `profile` table (which had no
`user_id` column) and drops it along with `weight_log` and `workout_log`, then
recreates everything as v2. **Your weight log and workout history from the v1
install will be lost** — the new release was built assuming a fresh start.

To upgrade a running LXC install:

```bash
# On the Proxmox host:
CTID=<your ct>
pct exec "$CTID" -- runuser -u homefit -- bash -c \
  'cd ~/workout-app && git pull && .venv/bin/pip install -r requirements.txt'
pct exec "$CTID" -- systemctl restart homefit
```

Then open the app — it'll show the "Create your profile" screen.

## Privacy

Everything stays on the box you host this on. No outbound calls except the
YouTube demo links you choose to tap.
