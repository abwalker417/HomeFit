# HomeFit — Current State (June 2026)

Multi-user home fitness PWA built with Flask + SQLite. Mobile-first, installable as a PWA. AI coaching via APEX (PeakAI / OpenAI-compatible). Nutrition sync from SparkyFitness (per-user API keys).

---

## Infrastructure

| Service | LXC | Address |
|---|---|---|
| HomeFit | 115 | `192.168.68.15:5000` — gunicorn, systemd |
| PeakAI | 133 | `192.168.68.33:4000` — LiteLLM proxy, model `claude-haiku` |
| Sparky | 120 | `192.168.68.20:3004` — Docker compose |
| Cloudflare | — | `homefit.hidethechaos.com` |

Deploy: `git pull origin develop` from `/home/homefit/workout-app` (DNS fix: `140.82.114.4 github.com` in `/etc/hosts`)

---

## 1. Route map

```
/profiles          → pick or create a profile
/onboarding        → first-time setup (weight, fitness level, equipment, limitations)
/                  → dashboard (plan summary, day cards, stats, today's workout card)
/build-day         → tap a day card → auto-builds that day's workout → /today-workout
/start-workout     → manual focus picker or "surprise me" → /today-workout
/today-workout     → active workout (timer, rest overlay, mark-complete, finish)
/today-workout/add → add an exercise mid-workout
/exercises         → full exercise library with availability status
/progress          → weight log chart + workout history
/apex-plan         → full weekly plan view (today highlighted)
/profiles/<id>/edit → edit profile; owner sees all profiles + add button
/settings/sparky   → per-user Sparky API key + shared URL config
```

---

## 2. Auth / profiles

- Session-based. `session["user_id"]` set on profile select.
- `session["is_owner"] = True` for user_id == 1 (first profile).
- PIN lockout: 5 wrong attempts in 15 min → 10-min lockout.
- Owner can add/edit all profiles.

---

## 3. APEX AI Coach

All AI calls route through PeakAI at `http://192.168.68.33:4000/v1/chat/completions`:
- `is_available()` checks `GET /v1/models`
- `chat()` uses full conversation history + system context
- `_generate()` wraps prompt in messages array for structured calls
- `_parse_json_safe()` strips code fences + repairs trailing commas

**Coaching context** built per-conversation in `database.get_coaching_context()`:
- Today's date/day (from browser local clock via `local_date`/`local_day` params)
- User profile: weight, goal, fitness level, equipment, limitations, duration target
- Weight trend from log
- Last 5 workouts with exercise IDs and weights used
- Sparky nutrition log: last 7 days (calories, protein, carbs, fat + meal breakdown)
- Sparky hydration log: last 7 days (water_ml per day)
- **Sparky nutrition goals**: current calorie/macro targets
- Full weekly plan

**Persistent chat**: `apex_chat` DB table, last 100 messages per user, shared across devices.

**Plan saving**: `apex_plan` DB table. `wants_to_save_plan()` triggers on ~15 phrases. `extract_plan_from_chat()` parses plan from recent APEX messages.

**Goal updates**: `wants_to_update_goals()` triggers on phrases like "update my goals". `extract_goals_from_chat()` uses AI to parse proposed numbers from recent APEX messages. `sparky_sync.update_goals()` pushes to Sparky via `POST /api/goals/manage-timeline`.

---

## 4. SparkyFitness Integration

- **Shared config**: `data/sparky_config.json` stores the Sparky base URL only.
- **Per-user API keys**: each user's Sparky Bearer token stored in `profile.sparky_api_key`.
- `profile.sparky_sync = 1` when a user has an active key.
- All Sparky functions accept `api_key=None` — use user's key, fall back to global config if not set.

| Function | Endpoint | Direction |
|---|---|---|
| `fetch_nutrition_log(days, api_key)` | GET `/api/food-entries` | Sparky → APEX context |
| `fetch_hydration_log(days, api_key)` | GET `/v2/measurements/water-intake/:date` | Sparky → APEX context |
| `fetch_goals(api_key)` | GET `/api/goals/by-date/:date` | Sparky → APEX context |
| `update_goals(calories, protein_g, carbs_g, fat_g, api_key)` | POST `/api/goals/manage-timeline` | APEX → Sparky |
| `sync_workout_async(..., api_key)` | POST `/api/exercise-entries` | HomeFit → Sparky |
| `sync_weight_async(..., api_key)` | POST `/api/health-data` | HomeFit → Sparky (lbs→kg) |

**Current Sparky users (live)**:
- Brent: connected (key in profile)
- Shay: connected (key in profile)
- Kelsie: not connected

**Sparky AI**: global/public provider pointing to PeakAI (`openai_compatible`, `claude-haiku`, `http://192.168.68.33:4000/v1`). Signup disabled. Food photo recognition and AI chat (which use tool calling) work — PeakAI passes `tools`, `tool_choice`, and streaming tool-call deltas through to Anthropic.

---

## 5. Database schema (version 5)

| Table | Key columns |
|---|---|
| `users` | id, name, pin_hash, emoji (legacy), photo, api_token |
| `profile` | user_id, fitness data, equipment, limitations, sparky_sync, **sparky_api_key**, ignored_exercises |
| `workout_log` | user_id, day_name, day_number, exercises_json, duration_seconds, completed_at |
| `weight_log` | user_id, weight (lbs), logged_at |
| `apex_plan` | user_id (PK), plan_json, created_at |
| `apex_chat` | user_id (PK), messages (JSON last 100), updated_at |

---

## 6. Workout generation

All logic in `workout_logic.py`:

- `filter_exercises(profile, focus)` — filters `data/exercises.json` by limitations, equipment, focus muscles
- `build_workout(profile, label, muscles, equipment_focus)` — difficulty cap from fitness level
- `all_exercises_with_status(profile)` — each exercise with `available` bool + `reason`

---

## 7. Known issues / pending

- Plan save occasionally fails if APEX response is very large (`max_tokens=4096` on extractions)
- `fetch_and_replace_exercises()` deduplication bug — do not call
- Profile photo upload not validated for file type server-side
- Upper-body exercise granularity (arms/back/chest all from same pool)
