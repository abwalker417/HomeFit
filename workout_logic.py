import json
import random
from pathlib import Path

EXERCISE_PATH = Path(__file__).parent / "data" / "exercises.json"

VALID_LIMITATIONS = ["knee pain", "back pain", "shoulder pain", "low impact", "wrist pain"]
VALID_EQUIPMENT = [
    "none",
    "dumbbells",
    "kettlebell",
    "resistance bands",
    "bench",
    "barbell",
    "power cage",
    "ab machine",
    "pull-up bar",
    "exercise bike",
    "treadmill",
    "jump rope",
    "yoga mat",
]
VALID_MUSCLE_GROUPS = [
    "full body",
    "arms",
    "back",
    "chest",
    "core",
    "glutes",
    "legs",
    "shoulders",
]

DAY_SPLITS = {
    1: ["full body"],
    2: ["upper body", "lower body"],
    3: ["push", "pull", "legs"],
    4: ["chest", "back", "legs", "arms"],
    5: ["chest", "back", "legs", "shoulders", "core"],
}

# JSON equipment names (after underscore→space) that map to VALID_EQUIPMENT names
_EQUIPMENT_ALIASES = {
    "bench or chair": "bench",
    "power cage cable": "power cage",
}

# JSON contraindication keys → VALID_LIMITATIONS values
_CONTRAINDICATION_TO_LIMITATION = {
    "bad_knees": "knee pain",
    "bad_back": "back pain",
    "bad_shoulders": "shoulder pain",
    "bad_wrists": "wrist pain",
}

CATEGORY_TO_MUSCLES = {
    "upper": {"arms", "back", "chest", "shoulders"},
    "legs": {"legs", "glutes"},
    "core": {"core"},
    "cardio": {"full body"},
}


def load_exercises():
    with open(EXERCISE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _norm(s):
    """Lowercase + normalize underscores and dashes to spaces."""
    return s.strip().lower().replace("_", " ").replace("-", " ")


def _normalize_values(values):
    if isinstance(values, str):
        values = [values]
    return [_norm(v) for v in (values or []) if isinstance(v, str) and v.strip()]


def _exercise_equipment(exercise):
    eq = exercise.get("equipment") or exercise.get("equipment_needed", "none")
    if isinstance(eq, str):
        eq = [eq]
    result = []
    for e in eq:
        n = _norm(e)
        result.append(_EQUIPMENT_ALIASES.get(n, n))
    return result


def _exercise_limitations(exercise):
    result = []
    for c in (exercise.get("contraindications") or []):
        mapped = _CONTRAINDICATION_TO_LIMITATION.get(c)
        if mapped:
            result.append(mapped)
    return result


def _exercise_muscles(exercise):
    muscles = exercise.get("muscle_groups") or [exercise.get("muscle_group", "full body")]
    return _normalize_values(muscles)


def _exercise_targets_selected_muscles(exercise, target_muscles):
    if not target_muscles:
        return True
    normalized_targets = set(_normalize_values(list(target_muscles)))
    if "full body" in normalized_targets:
        return True
    ex_muscles = set(_exercise_muscles(exercise))
    if ex_muscles & normalized_targets:
        return True
    category = (exercise.get("category") or "").strip().lower()
    mapped = CATEGORY_TO_MUSCLES.get(category, set())
    return bool(mapped & normalized_targets)


def filter_exercises(exercises, profile, selected_muscles=None, preferred_equipment=None):
    limitations = set(_normalize_values(profile.get("limitations") or []))
    available_equipment = set(_normalize_values(profile.get("equipment") or []))
    available_equipment.update(_normalize_values(profile.get("custom_equipment") or []))
    preferred_equipment = set(_normalize_values(preferred_equipment or profile.get("preferred_equipment") or []))
    target_muscles = set(_normalize_values(selected_muscles or profile.get("target_muscles") or []))

    if "full body" in target_muscles and len(target_muscles) > 1:
        target_muscles.discard("full body")

    filtered = []
    for ex in exercises:
        if limitations & set(_exercise_limitations(ex)):
            continue

        ex_equipment = set(_exercise_equipment(ex))
        # bodyweight and "none" exercises are always available
        needs_equipment = (
            ex_equipment
            and "none" not in ex_equipment
            and "bodyweight" not in ex_equipment
        )
        if needs_equipment:
            if available_equipment and not ex_equipment & available_equipment:
                continue
            if preferred_equipment and not (ex_equipment & preferred_equipment):
                continue

        if target_muscles and not _exercise_targets_selected_muscles(ex, target_muscles):
            continue

        filtered.append(ex)
    return filtered


def determine_goal(current_weight, goal_weight):
    if goal_weight < current_weight - 5:
        return "lose"
    if goal_weight > current_weight + 5:
        return "gain"
    return "maintain"


def determine_difficulty_cap(fitness_level):
    fitness_level = str(fitness_level or "beginner").lower()
    if fitness_level == "beginner":
        return {1}
    if fitness_level == "intermediate":
        return {1, 2}
    return {1, 2, 3}


def _pick(exercises, count, difficulty_cap):
    pool = []
    used_names = set()
    for ex in exercises:
        difficulty = ex.get("difficulty", 1)
        try:
            difficulty = int(difficulty)
        except (TypeError, ValueError):
            difficulty = 1
        if difficulty not in difficulty_cap:
            continue
        name = ex.get("name")
        if name in used_names:
            continue
        pool.append(ex)
        used_names.add(name)
    random.shuffle(pool)
    return pool[:count]


def build_workout(profile, day_label, selected_muscles=None, preferred_equipment=None):
    exercises = load_exercises()
    filtered = filter_exercises(exercises, profile, selected_muscles, preferred_equipment)
    difficulty_cap = determine_difficulty_cap(profile.get("fitness_level"))
    goal = determine_goal(profile.get("current_weight", 0), profile.get("goal_weight", 0))

    target_count = 6 if goal == "lose" else 5
    chosen = _pick(filtered, target_count, difficulty_cap)
    if len(chosen) < 3:
        chosen = _pick(filtered, max(3, target_count), {1, 2, 3})
    if len(chosen) < 3 and not selected_muscles:
        chosen = _pick(exercises, target_count, difficulty_cap)

    workout = []
    for ex in chosen:
        workout.append({
            "id": ex.get("id", ""),
            "name": ex.get("name"),
            "muscles": _exercise_muscles(ex),
            "equipment": _exercise_equipment(ex),
            "difficulty": ex.get("difficulty", 1),
            "instructions": ex.get("instructions", ""),
            "sets": ex.get("default_sets", 3),
            "reps": ex.get("default_reps", 10),
            "rest": ex.get("rest_seconds", 45),
            "unit": ex.get("unit", "reps"),
        })
    return {
        "label": day_label,
        "goal": goal,
        "focus": list(selected_muscles or profile.get("target_muscles") or []),
        "equipment_focus": list(preferred_equipment or profile.get("preferred_equipment") or []),
        "exercises": workout,
    }


def generate_plan(profile):
    days = max(1, min(int(profile.get("days_per_week", 4)), 5))
    split = DAY_SPLITS.get(days, DAY_SPLITS[4])
    selected_muscles = profile.get("target_muscles") or []
    preferred_equipment = profile.get("preferred_equipment") or []
    plan = []
    for idx in range(days):
        label = split[idx % len(split)]
        day_focus = selected_muscles or [label]
        plan.append({
            "day_number": idx + 1,
            "day_name": f"Day {idx + 1}",
            "label": label.title(),
            "workout": build_workout(profile, label.title(), day_focus, preferred_equipment),
        })
    return plan


def all_exercises_with_status(profile):
    exercises = load_exercises()
    available_equipment = set(_normalize_values(profile.get("equipment") or []))
    available_equipment.update(_normalize_values(profile.get("custom_equipment") or []))
    blocked_limitations = set(_normalize_values(profile.get("limitations") or []))
    out = []
    for ex in exercises:
        ex_limitations = set(_exercise_limitations(ex))
        ex_equipment = set(_exercise_equipment(ex))
        needs_equipment = (
            ex_equipment
            and "none" not in ex_equipment
            and "bodyweight" not in ex_equipment
        )
        is_blocked = False
        reasons = []
        if blocked_limitations & ex_limitations:
            is_blocked = True
            reasons.append("blocked by limitation")
        if needs_equipment and available_equipment and not (ex_equipment & available_equipment):
            is_blocked = True
            reasons.append("missing equipment")
        out.append({
            "id": ex.get("id", ""),
            "name": ex.get("name"),
            "category": ex.get("category", ""),
            "muscles": _exercise_muscles(ex),
            "equipment": sorted(ex_equipment) or ["none"],
            "difficulty": ex.get("difficulty", 1),
            "instructions": ex.get("instructions", ""),
            "available": not is_blocked,
            "reason": ", ".join(reasons),
        })
    return out


def get_exercise_by_id(exercise_id):
    for ex in load_exercises():
        if ex.get("id") == exercise_id:
            return {
                "id": ex["id"],
                "name": ex.get("name"),
                "muscles": _exercise_muscles(ex),
                "equipment": _exercise_equipment(ex),
                "difficulty": ex.get("difficulty", 1),
                "instructions": ex.get("instructions", ""),
                "sets": ex.get("default_sets", 3),
                "reps": ex.get("default_reps", 10),
                "rest": ex.get("rest_seconds", 45),
                "unit": ex.get("unit", "reps"),
            }
    return None


def pick_random_muscle_group():
    return random.choice(["arms", "back", "chest", "core", "glutes", "legs", "shoulders"])
