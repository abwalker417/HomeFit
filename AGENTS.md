# AGENTS.md — BuiltHere Handoff Context

## Purpose
This file gives coding agents a quick, reliable snapshot of the current BuiltHere project state before they make more changes. Internal paths and compatibility identifiers may still use `homefit`.

## Product direction
- Onboarding should only collect:
  - current weight
  - goal weight
  - fitness level
  - days per week
  - limitations
  - available equipment
- Workout focus should **not** be collected during onboarding.
- Workout focus should be chosen when the user starts a daily workout.
- Dashboard should guide the user toward **Start today's workout**.
- Navigation should remain at the top; bottom/footer nav was removed by user preference.
- The build-your-plan page should eventually use larger, stacked, list-style checkbox rows.

## Current architecture
- `app.py`
  - Flask entrypoint and main routes.
  - Dashboard uses a lightweight summary plan instead of prebuilding all workouts.
  - `/start-workout` handles focus selection or surprise mode.
  - `/today-workout` renders the session-built workout.
- `workout_logic.py`
  - Loads exercises from `data/exercises.json`.
  - Filters by limitations, equipment, and selected focus.
  - Uses category-to-muscle mapping to compensate for poor metadata in exercises.
  - Still has unresolved exercise-generation issues.
- Templates
  - `base.html` uses top-only nav.
  - `dashboard.html` includes start-workout CTA.
  - `start_workout.html` exists for focus selection.
  - `workout.html` expects `day.name` and `day.exercises`.
  - `templates/exercises.html` may be out of sync with helper output.
- Styling
  - `static/css/style.css` is partly mobile-first.
  - The onboarding/build-plan page still needs bigger stacked checkbox/list rows.

## Known issues
1. The intentionally session-light `/garage` wall-panel flow needs a separate
   access-control decision before V2 is exposed beyond the trusted LAN.
2. A temporary unlogged Upper workout/draft from V2 browser validation should
   be cleared only with explicit user approval.
3. Final household cutover still requires profile-by-profile data comparison
   and disposable end-to-end workout tests for Shay and Kelsie.

## Recommended next steps
1. Complete the account-by-account migration verification checklist in
   `README-homefit-notes.md`.
2. Decide on LAN-only versus HTTPS exposure and configure secure cookies when
   HTTPS is active.
3. Protect or disable the garage panel routes before public exposure.
4. Remove the temporary validation workout/draft after user approval.

## Files to inspect first
- `app.py`
- `workout_logic.py`
- `templates/exercises.html`
- `templates/workout.html`
- `templates/dashboard.html`
- `templates/profile_form_fields.html`
- `templates/start_workout.html`
- `static/css/style.css`
- `data/exercises.json`
- `README-homefit-notes.md`
- `.gitnotes.md`

## Agent instruction
Before making additional changes, read this file, then `README-homefit-notes.md`, then inspect the files above. Avoid reintroducing workout focus into onboarding.
