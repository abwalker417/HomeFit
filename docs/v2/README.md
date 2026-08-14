# HomeFit V2

HomeFit V2 is an incremental evolution of the production application, not a
clean-room rewrite. Production HomeFit remains on CT115 and the `develop`
branch while V2 is built and verified independently.

## Product thesis

HomeFit tells the user what their body is ready for, gives them today's
workout, and makes logging it effortless.

The default experience is organized around four destinations:

1. **Today** — readiness, one recommended workout, and the primary Start action.
2. **Train** — weekly plan, workout builder, history, library, and garage mode.
3. **Health** — weight, sleep, activity, cardio, nutrition, and trends.
4. **Coach** — APEX conversation, recommendations, memory, and plan changes.

Top navigation remains the primary navigation pattern. Workout focus stays out
of onboarding and is selected when starting or changing a workout.

## Delivery principles

- Preserve the production database and behavior until a migration is tested.
- Add characterization tests before moving existing behavior.
- Introduce module boundaries without changing URLs all at once.
- Put new device contracts under `/api/v2`.
- Use scoped, revocable credentials and Authorization headers for V2 clients.
- Keep SQLite initially; application structure is the current constraint, not
  database throughput.
- Keep the iOS HealthKit/APNs bridge while the web experience evolves.

## Milestones

### 0. Baseline

- Tag or otherwise record the exact production revision.
- Keep CT115 read-only except through the existing production deployment flow.
- Establish the separate V2 runtime described in `server.md`.
- Add route smoke tests and configuration validation.

### 1. Foundation

- Add an application factory.
- Split routes into auth, profiles, workouts, health, nutrition, coach, garage,
  and notifications blueprints.
- Separate database migrations and repositories from domain calculations.
- Centralize environment-driven configuration and structured logging.
- Add `/healthz` and `/readyz` probes.

### 2. Today

- Build the Today-first shell and design tokens.
- Place the recommended workout and Start action above analytics.
- Reduce readiness to a compact, actionable summary.
- Preserve resume, change-workout, recovery, and offline fallback flows.

### 3. Workout engine

- Normalize exercise metadata and validate it in CI.
- Separate candidate filtering, workout composition, and progression.
- Make AI choose only from validated deterministic candidates.
- Add explanations and safe empty states for every generated workout.

### 4. Health and Coach

- Move analytics out of Today into Health.
- Version Apple Health/device endpoints and migrate away from query tokens.
- Give APEX a first-class Coach workspace with explicit plan-change approval.

### 5. Cutover

- Copy a sanitized or explicit production database snapshot to V2.
- Run migration and reconciliation checks.
- Complete a household beta on the V2 hostname.
- Switch the public hostname only after a tested rollback rehearsal.

## Non-goals for the first V2 release

- Replacing Flask solely for novelty.
- Replacing SQLite before measurements justify it.
- Rewriting every screen in Swift.
- Replacing the garage kiosk flow.
- Adding more AI features before the daily workout loop is clearer.
