# HomeFit

A self-hosted home-fitness PWA. Built for iPhone use — install it to your home screen, let APEX (the AI coach) build your weekly plan, track your workouts, and sync nutrition from SparkyFitness.

## What it does

- **Multiple profiles** — each person in the household has their own plan, weight log, workout history, and optional PIN.
- **APEX AI coach** — conversational AI that builds weekly workout plans, adjusts for your limitations and equipment, and coaches you through progressive overload. Backed by an OpenAI-compatible LLM (PeakAI or any compatible endpoint).
- **SparkyFitness sync** — each user connects their own Sparky account. APEX sees your last 7 days of calories, protein, carbs, fat, and hydration automatically.
- **Workout generation** — rule-based fallback if AI is offline. Filters exercises by fitness level, available equipment, and physical limitations.
- **Weight & workout logging** — tracks over time; weight syncs back to Sparky automatically.
- **Exercise library** — 86+ exercises with form tips via APEX on demand.
- **PWA** — installable on iPhone/Android as a standalone full-screen app.

## Requirements

- Python 3.10+
- A machine to host it (laptop, Raspberry Pi, LXC container)
- Optional: an [OpenAI-compatible LLM endpoint](https://github.com/BerriAI/litellm) for APEX
- Optional: a [SparkyFitness](https://github.com/codewithcj/sparkyfitness) instance for nutrition sync

## Install & run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The app starts on `http://0.0.0.0:5000`. From your phone (same Wi-Fi), open `http://<your-machine-ip>:5000`.

### Installing on iPhone

1. Open the app URL in Safari.
2. Tap **Share** → **Add to Home Screen**.
3. Launch from the home screen — runs full-screen like a native app.

## AI — APEX Coach

APEX uses an OpenAI-compatible API. Set these in `data/peakai_config.json` or via the Settings page:

| Variable | Example |
|---|---|
| `PEAKAI_URL` | `http://192.168.68.33:4000` |
| `PEAKAI_API_KEY` | `your-key` |
| `PEAKAI_MODEL` | `claude-haiku` |

Any LiteLLM proxy, Ollama, or OpenAI-compatible endpoint works. APEX keeps a persistent chat history per user (last 100 messages, shared across devices).

## SparkyFitness Integration

Each HomeFit user connects their **own** Sparky account — so multiple family members each see their own nutrition data in APEX.

**Setup per user:**
1. Log into HomeFit under your profile.
2. Go to **Settings → SparkyFitness Sync**.
3. Enter the shared Sparky URL and **your personal API key** (from your Sparky account settings).
4. Hit Save — APEX will now see your last 7 days of food diary and hydration.

Completed workouts and weight entries sync back to Sparky automatically.

## Proxmox LXC — one-liner installer

`scripts/homefit-lxc.sh` creates a Debian 12 unprivileged LXC, clones this repo, installs Python deps, and registers a gunicorn systemd service.

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/YOUR_USER/homefit/main/scripts/homefit-lxc.sh)"
```

You'll be prompted for container ID, hostname, network, Git repo URL, and port.

### Updating

```bash
# From Proxmox host:
pct exec <CTID> -- runuser -u homefit -- bash -c \
  'cd ~/workout-app && git pull && .venv/bin/pip install -r requirements.txt'
pct exec <CTID> -- systemctl restart homefit
```

## Running permanently (systemd)

```ini
[Unit]
Description=HomeFit
After=network.target

[Service]
Type=simple
User=homefit
WorkingDirectory=/home/homefit/workout-app
ExecStart=/home/homefit/workout-app/.venv/bin/gunicorn app:app --bind 0.0.0.0:5000 --workers 2 --timeout 180
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Data

Everything in `data/`:

| File | Purpose |
|---|---|
| `workout.db` | SQLite — users, profiles, weight log, workout log, APEX plans, chat history |
| `exercises.json` | Exercise library — edit to add custom moves |
| `sparky_config.json` | Sparky base URL (shared); each user's API key is in their DB profile |

## Going public — NGINX Proxy Manager

Two env vars control exposure:

- **`HOMEFIT_TRUSTED_NETS`** — CIDRs allowed to create new profiles. Anyone outside sees a locked page but can still sign in. Default: `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8`
- **`HOMEFIT_TRUSTED_PROXIES`** — IP of your reverse proxy so `X-Forwarded-For` is trusted.
- **`HOMEFIT_SESSION_SECURE`** — set `1` when serving HTTPS.

Example `/etc/homefit/homefit.env`:
```
HOMEFIT_DB=/home/homefit/workout-app/data/workout.db
HOMEFIT_TRUSTED_NETS=192.168.68.0/24
HOMEFIT_TRUSTED_PROXIES=192.168.68.20
HOMEFIT_SESSION_SECURE=1
```

## Profiles & PIN

- First launch → "Create your profile".
- After that, home screen shows a Netflix-style profile picker.
- Optional 4–8 digit PIN (hashed with werkzeug, never stored raw).
- First profile created is the "owner" — can add/manage all profiles.

## Privacy

All data stays on your server. The only outbound connections are to your configured LLM endpoint and SparkyFitness instance — both of which you control.
