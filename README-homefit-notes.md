# HomeFit — Current State (May 20, 2026)

Multi-user home fitness PWA built with Flask + SQLite. Mobile-first, installable via PWA manifest.

---

## 1. High-level flow

```
/profiles          → pick or create a profile
/onboarding        → first-time setup (weight, fitness level, equipment, limitations)
/                  → dashboard (plan summary, day cards, stats)
/build-day         → tap a day card → auto-builds that day's workout → /today-workout
/start-workout     → manual focus picker or "surprise me" → /today-workout
/today-workout     → active workout (timer, rest overlay, mark-complete, finish)
/today-workout/add → add an exercise mid-workout
/exercises         → full exercise library with availability status
/progress          → weight log chart + workout history
/profiles/<id>/edit → edit your profile; owner sees all profiles + add button
```

---

## 2. Auth / profiles

- Session-based. `session["user_id"]` is set on profile select.
- `session["is_owner"] = True` is set for user_id == 1 (first profile) automatically on switch.
- PIN lockout: 5 wrong attempts in 15 min → 10-min lockout.
- Owner can add/edit all profiles from the Profile tab without touching URLs.

---

## 3. Workout generation

All logic lives in `workout_logic.py`.

- `filter_exercises(profile, focus)` — filters `data/exercises.json` by:
  - Limitations (reads `contraindications` field, maps to VALID_LIMITATIONS via `_CONTRAINDICATION_TO_LIMITATION`)
  - Available equipment (reads `equipment` string, maps via `_EQUIPMENT_ALIASES`, normalizes with `_norm()`)
  - Focus muscles (maps coarse `category` to muscle groups via `CATEGORY_TO_MUSCLES`)
- `build_workout(profile, label, muscles, equipment_focus)` — picks exercises by difficulty cap derived from fitness level; widens cap if too few matches.
- `all_exercises_with_status(profile)` — returns every exercise with `available` bool and `reason` string for the library view.
- `get_exercise_by_id(id)` — used by add-exercise endpoint to append a single exercise to the active session workout.

Dashboard day labels map to muscles via `_LABEL_TO_MUSCLES` in `app.py`:
- Upper body → arms, back, chest, shoulders
- Lower body → legs, glutes
- Core & cardio → core
- Recovery → full body

---

## 4. Templates

| Template | Purpose |
|---|---|
| `base.html` | Layout, top nav, app.js load, `{% block scripts %}` |
| `dashboard.html` | Hero stats, day cards (tap → `/build-day`), quick CTAs |
| `start_workout.html` | Manual focus picker (radio) + "surprise me" |
| `workout.html` | Exercise list, timer, rest overlay, finish button |
| `add_exercise.html` | Searchable exercise picker for mid-workout adds |
| `exercises.html` | Full library with availability badges |
| `profile_edit.html` | Edit self; owner sees Manage Profiles + Add Profile + Danger Zone |
| `profile_form_fields.html` | Shared form fields (weight, fitness level, equipment, limitations) |
| `onboarding.html` | First-run wrapper around profile_form_fields |
| `profiles.html` | Profile switcher |

---

## 5. CSS architecture (`static/css/style.css`)

- CSS variables for colors, radius, shadow.
- Mobile-first; breakpoints at 640px and 720px.
- Key classes: `.card`, `.pill-grid`, `.pill-option`, `.btn`, `.form`, `.exercise-list`, `.exercise-item`.
- `.form label:not(.pill-option)` — the `:not()` is intentional; pill-options need `flex-direction: row`.
- `setupLibraryFilter()` in `app.js` expects `.library-list .exercise-item` with `data-name` and `data-category` attributes.

---

## 6. JavaScript (`static/js/app.js`)

Key functions (all invoked by page-specific `{% block scripts %}` blocks):

- `startWorkout()` — workout timer, rest overlay countdown, mark-complete tracking, finish → POST `/api/complete_workout`.
- `setupLibraryFilter()` — live search + category filter for exercise lists (exercises.html and add_exercise.html).
- `setupWeightForm()` — weight logging widget on progress page.

`app.js` is loaded synchronously (no `defer`) so these are available when inline block scripts call them.

---

## 7. Known issues

1. **Upper-body muscle granularity** — exercises.json has coarse `category: "upper"` with no per-muscle field. Arms/back/chest/shoulders all draw from the same pool.
2. **Empty workout** — no user-facing message when 0 exercises match the selected focus + profile constraints. Screen just renders an empty list.
3. **`data/secret.key`** — untracked. Confirm it's in `.gitignore` before any public push.

---

## 8. Deployment

- LXC at `192.168.68.15`, gunicorn, managed by systemd (`homefit.service`).
- Deploy: `git pull origin develop && systemctl restart homefit`
- Branch: `develop` → `main`. Work on develop; deploy from develop.

---

## 9. Next steps

1. Add per-muscle tags to `exercises.json` (biceps, triceps, chest vs generic "upper").
2. Empty-state message on workout screen when 0 exercises are generated.
3. Persist completed workouts fully to DB (current log stores day name + count; could store full exercise list for history review).
