# BuiltHere — Current State (August 2026)

Multi-profile, mobile-first Flask fitness PWA with SQLite persistence and Coach
features powered through PeakAI. BuiltHere is deployed independently from the
legacy V1 application and is being
stabilized before Shay and Kelsie move over.

## Current infrastructure

| Service | Location | Address |
|---|---|---|
| Legacy V1 | CT115 | `192.168.68.15:5000` (read-only migration source) |
| BuiltHere | CT120 (`homefit-v2`) | `192.168.68.20:5000` |
| PeakAI | `192.168.68.33` | OpenAI-compatible `/v1` API |

V2 runs as `homefit:homefit` under `homefit.service`. Code releases live under
`/opt/homefit/releases`, persistent data under `/var/lib/homefit`, and secrets
under `/etc/homefit`. V1 and V2 never share a writable database.

Deploy the configured V2 branch from the Proxmox host:

```bash
pct exec 120 -- /usr/local/sbin/homefit-update
```

The updater stages a separate release, backs up SQLite, validates the import,
switches the `current` symlink, requires `/healthz` to pass, and rolls back on
failure. It retains five releases and fourteen database snapshots and refreshes
itself from the validated release.

## Product behavior

- Onboarding collects only current weight, goal weight, fitness level, days per
  week, limitations, and available equipment.
- Workout focus is chosen when starting a daily workout, never during onboarding.
- The dashboard prioritizes **Start today's workout**.
- If today's saved plan has a workout, Start loads that plan. If there is no
  planned workout, the user chooses Upper, Lower, Core, or Recover.
- Coach is a normal top navigation tab and full page, not a floating button.
- Navigation is top-only. The main app uses theme-driven color and gradients,
  without a photo background.
- Active workouts auto-save after every change, show total-set progress, and
  allow an immediate undo for an accidental logged set.
- The Garage panel is an optional household companion for Brent and Shay. It
  shares the same active-workout draft as the phone/PWA, but is not part of the
  core experience required by every BuiltHere household.

## Workout safety

`workout_logic.py` infers accurate muscle groups from the exercise catalog and
enforces focus, equipment, limitations, and ignored exercises. AI receives only
eligible exercise IDs; off-focus or incomplete AI output is rejected and filled
from the safe local pool. Saved V1 plans are sanitized consistently when shown
and when loaded, without rewriting the stored plan.

## Primary routes

| Route | Purpose |
|---|---|
| `/` | Dashboard and today's action |
| `/start-workout` | Focus picker when no planned workout applies |
| `/today-workout` | Active workout logger |
| `/apex-plan` | Weekly training plan |
| `/coach` | Coach workspace |
| `/progress` | Health, activity, and workout history |
| `/log-food` | Nutrition log |
| `/exercises` | Exercise library and availability |
| `/profiles/<id>/edit` | Profile, integrations, and theme |
| `/healthz` | Public database-aware deployment readiness probe |

## Validation status

- Automated suite: 75 tests passing as of 2026-08-14.
- Coverage includes focus-safe generation, saved-plan sanitation, installer
  rollback/retention, primary authenticated page rendering, and missing-token
  rejection across external APIs.
- Desktop and 390 px mobile browser passes completed for Dashboard, Train,
  active workout, Health, Food, Profile, and Exercise Library.
- Exercise Library renders 103 catalog items without its former server error.
- PeakAI V2 authentication and focused Upper workout generation were verified
  against CT120.

## Before household migration

1. Clear the temporary unlogged validation workout/draft after approval.
2. Run a final account-by-account data comparison for Brent, Shay, and Kelsie.
3. Have each person verify PIN, profile values, limitations, equipment, weekly
   plan, workout history, and integrations on V2.
4. Complete one disposable workout per test profile, verify History, then remove
   the disposable records before cutover.
5. Decide whether V2 remains LAN-only or receives an HTTPS hostname. Keep
   `HOMEFIT_SESSION_SECURE=0` only for LAN HTTP; set it to `1` behind HTTPS.
6. If publishing beyond the trusted LAN, add a separate access control decision
   for the intentionally session-light `/garage` wall-panel flow.
7. Take a final V1 SQLite backup, import once, compare row counts, then freeze V1
   writes during the cutover. Do not attempt ongoing two-way SQLite sync.

## Important files

- `app.py`, `database.py`, `workout_logic.py`, `coach.py`
- `templates/base.html`, `dashboard.html`, `start_workout.html`, `workout.html`
- `templates/apex_plan.html`, `coach.html`, `progress.html`, `log_food.html`
- `templates/exercises.html`, `profile_edit.html`, `profile_form_fields.html`
- `static/css/style.css`, `static/js/app.js`
- `data/exercises.json`
- `scripts/homefit-v2-lxc.sh`
- `install/homefit-install.sh`, `install/homefit-update.sh`
- `docs/v2/server.md`, `docs/v2/migration.md`

Untracked `*.hekate-bak` files and SQLite `-wal`/`-shm` files are user/runtime
artifacts. Do not commit or delete them without explicit approval.
