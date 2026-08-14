# BuiltHere

A self-hosted, multi-profile fitness PWA that builds training around the space,
equipment, ability, and time you have available.

**Training built around what you have.**

## What it does

- **Multiple profiles** — each person in the household has their own plan, weight log, workout history, and optional PIN.
- **Coach** — conversational guidance that builds weekly workout plans, adjusts for limitations and equipment, and supports progressive overload. Backed by an OpenAI-compatible LLM such as PeakAI.
- **SparkyFitness sync** — each user connects their own Sparky account. Coach can use recent nutrition, hydration, and goal data when helping that person.
- **Goal updates from chat** — Coach can propose updated calorie/macro targets and push them to Sparky only after confirmation.
- **Workout generation** — rule-based fallback if AI is offline. Filters exercises by fitness level, available equipment, and physical limitations.
- **Weight & workout logging** — tracks over time; weight syncs back to Sparky automatically.
- **Exercise library** — 100+ exercises with form guidance from Coach on demand.
- **PWA** — installable on iPhone/Android as a standalone full-screen app.

## Requirements

- Python 3.10+
- A machine to host it (laptop, Raspberry Pi, LXC container)
- Optional: an [OpenAI-compatible LLM endpoint](https://github.com/BerriAI/litellm) for Coach
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

## Coach and AI provider settings

BuiltHere works with any OpenAI-compatible Chat Completions endpoint: OpenAI,
LiteLLM, Ollama, Azure-compatible gateways, PeakAI, and self-hosted proxies.
The household owner can configure the endpoint, API key, and separate models
for Coach/plans, fast text parsing, and meal-photo vision in **Settings**.
The key is never rendered back to the browser; leave the field blank to keep a
saved key unchanged.

For a headless or first-run deployment, environment variables remain supported:

| Variable | Example |
|---|---|
| `PEAKAI_URL` | `http://192.168.68.33:4000` |
| `PEAKAI_API_KEY` | `your-key` |
| `PEAKAI_MODEL` | `claude-haiku` |

The legacy `PEAKAI_*` names are compatible defaults. New installations may use
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_COACH_MODEL`,
`OPENAI_FAST_MODEL`, and `OPENAI_VISION_MODEL` instead.

Any LiteLLM proxy, Ollama, or OpenAI-compatible endpoint works. Coach keeps a persistent chat history per user (last 100 messages, shared across devices).

## SparkyFitness Integration

Each BuiltHere user connects their **own** Sparky account so household profiles keep separate nutrition data.

**Setup per user:**
1. Log into BuiltHere under your profile.
2. Go to **Settings → SparkyFitness Sync**.
3. Enter the shared Sparky URL and **your personal API key** (from your Sparky account settings).
4. Save — Coach can now use the recent food diary, hydration, and current nutrition goals.

**What Coach can do with Sparky:**
- Read your food diary and hydration (last 7 days)
- Read your current calorie/macro targets
- Propose updated nutrition goals and push them to Sparky when you say "update my goals"
- Push completed workouts and weight entries back to Sparky automatically

## Proxmox LXC — one-liner installer

`scripts/homefit-v2-lxc.sh` creates a Debian 13 unprivileged LXC and installs BuiltHere with atomic, health-checked updates.

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/abwalker417/HomeFit/main/scripts/homefit-v2-lxc.sh)"
```

You'll be prompted for container ID, hostname, storage, network, and resources.

### Updating

```bash
# From the Proxmox host:
pct exec <CTID> -- /usr/local/sbin/homefit-update
```

## Running permanently (systemd)

```ini
[Unit]
Description=BuiltHere
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
| `workout.db` | SQLite — users, profiles, weight log, workout log, Coach plans, chat history |
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
