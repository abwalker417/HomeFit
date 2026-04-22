"""Rule-based workout plan generator.

Given a user profile (current weight, goal weight, fitness level, limitations),
produces a weekly plan of home, bodyweight-only workouts and filters out
exercises that conflict with the user's physical limitations.
"""

import json
import random
from pathlib import Path
from typing import Any

EXERCISES_FILE = Path(__file__).parent / "data" / "exercises.json"

# Map UI-friendly limitation keys to contraindication tags in exercises.json
LIMITATION_MAP = {
    "bad_back": "bad_back",
    "bad_knees": "bad_knees",
    "bad_shoulders": "bad_shoulders",
    "bad_wrists": "bad_wrists",
}

VALID_LIMITATIONS = set(LIMITATION_MAP.keys())


def load_exercises() -> list[dict[str, Any]]:
    with open(EXERCISES_FILE) as f:
        return json.load(f)


def filter_exercises(
    exercises: list[dict[str, Any]],
    limitations: list[str],
) -> list[dict[str, Any]]:
    """Remove exercises contraindicated by any of the user's limitations."""
    bad_tags = {LIMITATION_MAP[l] for l in limitations if l in VALID_LIMITATIONS}
    return [e for e in exercises if not (set(e.get("contraindications", [])) & bad_tags)]


def determine_goal(current_weight: float, goal_weight: float) -> str:
    """Return 'lose', 'gain', or 'maintain' based on weight delta."""
    delta = goal_weight - current_weight
    if delta < -2:
        return "lose"
    if delta > 2:
        return "gain"
    return "maintain"


def determine_difficulty_cap(fitness_level: str) -> int:
    """Max exercise difficulty (1-3) allowed for the user's fitness level."""
    return {"beginner": 1, "intermediate": 2, "advanced": 3}.get(fitness_level, 2)


def _pick(pool: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """Deterministic sample helper so the same profile yields the same plan."""
    if not pool:
        return []
    rng = random.Random(seed)
    if len(pool) <= n:
        return list(pool)
    return rng.sample(pool, n)


def build_workout(
    category_pools: dict[str, list[dict[str, Any]]],
    composition: dict[str, int],
    goal: str,
    seed: int,
) -> list[dict[str, Any]]:
    """Assemble a single workout from the per-category exercise pools."""
    workout = []
    for category, count in composition.items():
        picks = _pick(category_pools.get(category, []), count, seed + hash(category))
        for ex in picks:
            ex = dict(ex)  # copy so we don't mutate the source
            # Tune volume for goal
            if goal == "lose":
                ex["sets"] = ex.get("default_sets", 3)
                ex["reps"] = int(ex.get("default_reps", 10) * 1.2)
                ex["rest"] = max(20, ex.get("rest_seconds", 45) - 15)
            elif goal == "gain":
                ex["sets"] = ex.get("default_sets", 3) + 1
                ex["reps"] = max(6, int(ex.get("default_reps", 10) * 0.8))
                ex["rest"] = ex.get("rest_seconds", 45) + 15
            else:  # maintain
                ex["sets"] = ex.get("default_sets", 3)
                ex["reps"] = ex.get("default_reps", 10)
                ex["rest"] = ex.get("rest_seconds", 45)
            workout.append(ex)
    return workout


def generate_plan(
    current_weight: float,
    goal_weight: float,
    fitness_level: str = "beginner",
    limitations: list[str] | None = None,
    days_per_week: int = 4,
) -> dict[str, Any]:
    """Build a week-long home workout plan for the user.

    Returns a dict with:
      - goal: 'lose' | 'gain' | 'maintain'
      - summary: human-readable description
      - days: list of {name, focus, exercises: [...]}  (len == days_per_week)
    """
    limitations = limitations or []
    exercises = filter_exercises(load_exercises(), limitations)
    cap = determine_difficulty_cap(fitness_level)
    exercises = [e for e in exercises if e.get("difficulty", 1) <= cap]

    # Bucket the available exercises by category
    pools: dict[str, list[dict[str, Any]]] = {}
    for ex in exercises:
        pools.setdefault(ex["category"], []).append(ex)

    goal = determine_goal(current_weight, goal_weight)

    # Pick a weekly split pattern based on goal + frequency
    if goal == "lose":
        # More cardio, circuit-style days
        templates = [
            ("Full Body + Cardio", {"legs": 2, "upper": 1, "core": 1, "cardio": 2}),
            ("Lower + Cardio",     {"legs": 3, "cardio": 2, "core": 1}),
            ("Upper + Core",       {"upper": 3, "core": 2, "cardio": 1}),
            ("HIIT Circuit",       {"cardio": 3, "legs": 1, "upper": 1, "core": 1}),
            ("Full Body",          {"legs": 2, "upper": 2, "core": 1, "cardio": 1}),
            ("Core + Cardio",      {"core": 3, "cardio": 2}),
            ("Active Recovery",    {"core": 2, "legs": 1}),
        ]
    elif goal == "gain":
        # Strength-leaning with more volume per muscle group
        templates = [
            ("Lower Body Strength", {"legs": 4, "core": 1}),
            ("Upper Body Strength", {"upper": 4, "core": 1}),
            ("Full Body",           {"legs": 2, "upper": 2, "core": 1}),
            ("Core Focus",          {"core": 3, "legs": 1, "upper": 1}),
            ("Lower + Core",        {"legs": 3, "core": 2}),
            ("Upper + Core",        {"upper": 3, "core": 2}),
            ("Full Body",           {"legs": 2, "upper": 2, "core": 1}),
        ]
    else:  # maintain
        templates = [
            ("Full Body",        {"legs": 2, "upper": 2, "core": 1, "cardio": 1}),
            ("Cardio + Core",    {"cardio": 2, "core": 2, "legs": 1}),
            ("Upper Body",       {"upper": 3, "core": 2}),
            ("Lower Body",       {"legs": 3, "core": 2}),
            ("Full Body",        {"legs": 2, "upper": 2, "core": 1, "cardio": 1}),
            ("Mobility + Core",  {"core": 3, "upper": 1, "cardio": 1}),
            ("Active Recovery",  {"core": 1, "legs": 1, "cardio": 1}),
        ]

    days_per_week = max(1, min(7, days_per_week))
    seed = int((current_weight * 31 + goal_weight * 7 + len(limitations) * 13))

    days = []
    for i in range(days_per_week):
        name, composition = templates[i % len(templates)]
        workout = build_workout(pools, composition, goal, seed + i)
        days.append({
            "day_number": i + 1,
            "name": name,
            "exercises": workout,
        })

    # Summary
    delta = goal_weight - current_weight
    if goal == "lose":
        summary = (
            f"Plan to lose {abs(delta):.1f} lbs. Higher-rep, shorter-rest circuits "
            f"with more cardio to build a calorie deficit."
        )
    elif goal == "gain":
        summary = (
            f"Plan to gain {delta:.1f} lbs. Lower reps, extra sets, longer rest "
            f"to prioritize strength. Pair with a calorie surplus."
        )
    else:
        summary = "Maintenance plan. Balanced volume across strength, core, and cardio."

    return {
        "goal": goal,
        "summary": summary,
        "fitness_level": fitness_level,
        "limitations": limitations,
        "days_per_week": days_per_week,
        "days": days,
    }


def all_exercises_with_status(limitations: list[str]) -> list[dict[str, Any]]:
    """Return all exercises, each annotated with whether it's allowed for the user."""
    exercises = load_exercises()
    bad_tags = {LIMITATION_MAP[l] for l in limitations if l in VALID_LIMITATIONS}
    annotated = []
    for ex in exercises:
        blockers = list(set(ex.get("contraindications", [])) & bad_tags)
        ex_copy = dict(ex)
        ex_copy["allowed"] = not blockers
        ex_copy["blocked_by"] = blockers
        annotated.append(ex_copy)
    return annotated
