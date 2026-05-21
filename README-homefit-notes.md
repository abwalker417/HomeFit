# HomeFit — Current State (May 19, 2026)

These notes capture where the app is right now, what was recently changed, and what still needs work before this flow is "done".

---

## 1. High-level flow

- **Profiles**
  - Multi-user profiles are stored in SQLite via `database.py`.
  - Owner flag (`is_owner`) is used to control who can manage profiles.
- **Onboarding / Build your plan**
  - Single shared form (`profile_form_fields.html`) collects:
    - Current weight, goal weight, fitness level, days per week
    - Limitations (checkboxes)
    - Available equipment (checkboxes + custom text)
  - Workout focus (target muscles, preferred equipment) has been moved *out* of onboarding.
- **Dashboard**
  - Shows plan summary (cut / bulk / maintain) derived from current vs goal weight.
  - Shows a “Start today’s workout” CTA and an optional “Pick one for me” path.
  - Weekly day tiles are just entry points into the workout builder, not pre‑generated workouts.
- **Start workout**
  - User chooses focus (arms, legs, etc.) or taps “Pick one for me”.
  - Selection is posted to `/start-workout`, which calls `build_workout` in `workout_logic.py`.
  - Generated workout is stored in the session and rendered via `today_workout` → `workout.html`.
- **Exercises tab**
  - Should show an exercise library listing using `all_exercises_with_status(profile)`.
  - Currently hitting an internal error (see open issues).

---

## 2. Recent code changes

### 2.1 Routing and flows (`app.py`)

- Replaced the original weekly prebuilt plan logic with `_dashboard_plan`, which just creates labels for days and defers workout generation until the user taps **Start**.
- Added `/start-workout` route:
  - GET: renders `start_workout.html` with muscle focus options.
  - POST: reads `focus_mode` and `focus` checkboxes, chooses muscle group(s), calls `build_workout`, saves to `session['today_workout']`, and redirects to `/today-workout`.
- Added `/today-workout` route:
  - Reads `session['today_workout']`.
  - Builds a simple `day` dict with `day_number`, `name`, and `exercises` and renders `workout.html`.
- Adjusted `/workout/<int:day_number>` to redirect into the builder instead of assuming prebuilt plans.
- Tweaked profile editing guard so the owner/admin can open any profile edit page.

### 2.2 Workout generation (`workout_logic.py`)

- `load_exercises()` now pulls from `data/exercises.json`.
- `filter_exercises()` logic:
  - Normalizes profile limitations, equipment, and target muscles.
  - Respects limitations and available equipment.
  - Introduced `CATEGORY_TO_MUSCLES` and `_exercise_targets_selected_muscles()` to compensate for many exercises being labeled `full_body` while their `category` is more specific (`upper`, `legs`, `core`, `cardio`).
- `determine_difficulty_cap()` works with integer difficulty levels from the JSON (1–3) instead of strings.
- `build_workout()`:
  - Uses goal to set target exercise count (6 for cut, 5 otherwise).
  - Picks from the filtered pool using difficulty cap.
  - If too few matches, first broadens difficulty (up to {1,2,3}) *within the filtered pool* before falling back.
  - Returns a dict with `label`, `goal`, `focus`, `equipment_focus`, and `exercises`.

### 2.3 Templates

- `base.html`
  - Switched to `app-header`, `top-nav`, and `app-main` classes to match `style.css`.
  - Added/removes bottom nav: final state is **top nav only**, per your preference.
- `dashboard.html`
  - Primary CTA: **Start today’s workout**.
  - Secondary CTA: **Pick one for me** (routes into surprise focus mode).
  - Weekly day cards say “Tap to build today” and route into `/start-workout`.
- `start_workout.html`
  - New page for picking focus areas (checkbox list) or surprise mode.
- `workout.html`
  - Renders `day.exercises` with timer and completion checkboxes.

- `profile_form_fields.html`
  - Workout‑focus fields removed from onboarding/profile form.
  - Only collects weight, fitness level, limitations, and equipment.

### 2.4 Styling (`static/css/style.css`)

- Shared mobile‑first layout for `.hero`, `.card`, `.day-grid`, forms, pill options, etc.
- `grid-two` and `pill-grid` stack on mobile and split into 2 columns at ≥640px.
- Workout selection / dashboard tweaks:
  - `.hero-actions` stack on small screens, side‑by‑side on larger screens.
  - `.section-row` used for the “Today’s workout flow” card.
  - Mobile tweaks for `.hero-top`, `.hero-stats`, `.inline-form`, and `.day-grid`.

---

## 3. Known issues (as of this session)

These are the main problems you reported that still need work:

1. **Arms focus still not perfect**
   - Even after category mapping, arms workouts can still include non‑arm moves under some combinations of limitations/equipment.
   - Root cause: `exercises.json` tags many movements as `full_body` with only a coarse `category` field.
   - Current mitigation: filter first by focus + category mapping, then broaden difficulty cap; if *still* too few, a global fallback can re‑introduce off‑focus moves.
   - TODO: tighten the arms/upper logic further or clean up `exercises.json` to use more accurate `muscle_groups`.

2. **Sometimes no exercises when building a plan**
   - You observed cases where a focus selection produces an empty workout.
   - Likely scenarios:
     - Profile limitations + available equipment + selected focus filter out almost all candidates.
     - `build_workout()` broadens difficulty but still ends up with <3 matches and returns a very small or empty set.
   - TODO:
     - Add logging around `filtered` length and chosen exercises.
     - Implement a clear user‑facing message when zero exercises are available instead of silently rendering an empty list.

3. **Exercises tab internal server error**
   - `/exercises` currently errors.
   - Template expects fields: `ex.category`, `ex.name`, `ex.difficulty`, `ex.equipment`, `ex.allowed`, etc.
   - `all_exercises_with_status()` now returns a simplified structure; likely a field mismatch (e.g., `available` vs `allowed`, `reason` vs `blocked_by`).
   - TODO:
     - Align `all_exercises_with_status()` output with what `templates/exercises.html` expects *or* adjust the template to the new names.

4. **Build‑your‑plan UI doesn’t match desired list layout**
   - Onboarding / plan page still shows small inline checkboxes for limitations and equipment.
   - You want larger, stacked list‑style rows with big checkboxes or pill‑cards.
   - TODO:
     - Update `onboarding.html` & `profile_form_fields.html` markup to use the `.pill-option` / `.pill-grid` pattern for both sections.
     - Ensure checkbox inputs are at least 20–24px with generous padding around the row.

5. **Focus selection UX**
   - Current builder allows selecting multiple focus areas but doesn’t clearly highlight what’s active or how that maps to the generated workout.
   - TODO:
     - Consider limiting to one focus choice per workout (e.g., radio‑style) or visually emphasizing the primary selection.
     - Surface the chosen focus at the top of the workout screen (e.g., “Focus: Arms”).

---

## 4. Next steps when you come back

When you pick this up again, a good order of attack:

1. **Fix Exercises tab error**
   - Compare `templates/exercises.html` with `all_exercises_with_status()` and make their field names match.
   - Add basic error handling/logging around that route.
2. **Add debug logging around workout generation**
   - Log selected focus, filtered candidate count, and final chosen exercises.
   - Use that to tune the focus matching rules, especially for arms vs legs.
3. **Upgrade onboarding checkboxes to list layout**
   - Refactor limitations & equipment sections to use `.pill-grid` / `.pill-option` with bigger tap targets.
4. **Tighten focus logic or clean exercise data**
   - Either improve `CATEGORY_TO_MUSCLES` and `_exercise_targets_selected_muscles()` further or edit `exercises.json` for more accurate groups.

These notes should give you a clear snapshot of what’s implemented and what still needs attention so you can quickly get your bearings next time.
