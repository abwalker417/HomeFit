# AGENTS.md — HomeFit Handoff Context

## Purpose
This file gives coding agents a quick, reliable snapshot of the current HomeFit project state before they make more changes.

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
1. Focused workout generation is still unreliable.
   - Example seen: selecting arms still returned leg moves in some cases.
   - Other times no exercises render at all.
   - Root cause seems to be a mix of fallback logic and poor source metadata in `data/exercises.json`.

2. Exercises tab has thrown an internal server error.
   - Likely caused by mismatch between `all_exercises_with_status()` and `templates/exercises.html` expected fields.

3. Onboarding UI still needs improvement.
   - Current checkboxes are too small/dense.
   - User wants larger stacked selection rows.

4. Profile access should be re-verified.
   - A route guard was adjusted to make the Profile button work, but this should still be tested after any future auth/routing changes.

## Recommended next steps
1. Fix `/exercises` first.
2. Add logging/debugging around `build_workout()` to inspect:
   - selected focus
   - filtered exercise count
   - final chosen exercise list
3. Tighten focus fallback behavior so it does not silently drift into the wrong body area.
4. Refactor onboarding/build-plan checkbox markup into bigger stacked rows.
5. Consider cleaning `data/exercises.json` so exercises have accurate `muscle_groups`.

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
