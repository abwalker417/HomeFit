import json
import logging
import math
import random
import re
from pathlib import Path

EXERCISE_PATH = Path(__file__).parent / "data" / "exercises.json"
log = logging.getLogger(__name__)

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

_MUSCLE_RULES = (
    (re.compile(r"crunch|plank|dead.?bug|bird.?dog|pallof|rotation|twist|oblique|knee.?raise|suitcase|figure.?eight|crossbody|superman"), {"core"}),
    (re.compile(r"glute|hip.?thrust|kickback|clamshell"), {"glutes"}),
    (re.compile(r"squat|lunge|step.?up|calf|wall.?sit|deadlift|romanian|\brdl\b|good.?morning"), {"legs", "glutes"}),
    (re.compile(r"bicep|hammer.?curl|barbell.?curl|tricep|skull.?crusher|\bdip\b"), {"arms"}),
    (re.compile(r"chest|bench.?press|incline.?press|push.?up|floor.?fly|cable.?fly"), {"chest"}),
    (re.compile(r"shoulder|overhead|lateral|arnold|halo|landmine.?press|clean.?and.?press|push.?press|rear.?delt|arm.?circle"), {"shoulders"}),
    (re.compile(r"row|pull.?up|chin.?up|pulldown|pull.?apart|face.?pull|dead.?hang|scapular|shrug|superman"), {"back"}),
)


def load_exercises():
    if not EXERCISE_PATH.exists():
        return []
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
    normalized = _normalize_values(muscles)
    # Most of the original library was bulk-tagged "full_body", which is too
    # broad for focus-safe workouts. Infer a primary area from stable IDs/names
    # until the source data has complete muscle metadata.
    if not normalized or set(normalized) == {"full body"}:
        text = _norm(f"{exercise.get('id', '')} {exercise.get('name', '')}")
        inferred = set()
        for pattern, targets in _MUSCLE_RULES:
            if pattern.search(text):
                inferred.update(targets)
        if inferred:
            return sorted(inferred)
        category = (exercise.get("category") or "").strip().lower()
        category_targets = CATEGORY_TO_MUSCLES.get(category, set())
        if category_targets:
            return sorted(category_targets)
    return normalized or ["full body"]


def focus_muscles_from_label(label):
    """Convert a user/plan-facing focus label into strict library muscles."""
    value = _norm(str(label or ""))
    if not value:
        return []
    if "full body" in value or "total body" in value or "recovery" in value:
        return ["full body"]
    if "upper" in value:
        return ["arms", "back", "chest", "shoulders"]
    if "lower" in value or "leg" in value or "glute" in value:
        return ["legs", "glutes"]
    if "push" in value:
        return ["arms", "chest", "shoulders"]
    if "pull" in value:
        return ["arms", "back"]
    matches = set(m for m in VALID_MUSCLE_GROUPS if m != "full body" and m in value)
    if re.search(r"arm|bicep|tricep", value):
        matches.add("arms")
    if re.search(r"ab|core", value):
        matches.add("core")
    if re.search(r"quad|hamstring|calf", value):
        matches.add("legs")
    return sorted(matches)


def is_recovery_exercise(exercise):
    """Recovery sessions stay light and avoid loaded strength equipment."""
    try:
        difficulty = int(exercise.get("difficulty") or 1)
    except (TypeError, ValueError):
        difficulty = 1
    equipment = set(_exercise_equipment(exercise))
    return difficulty == 1 and bool(
        equipment & {"bodyweight", "none", "resistance bands", "yoga mat"}
    )


def _exercise_targets_selected_muscles(exercise, target_muscles):
    if not target_muscles:
        return True
    normalized_targets = set(_normalize_values(list(target_muscles)))
    if "full body" in normalized_targets:
        return True
    ex_muscles = set(_exercise_muscles(exercise))
    category = (exercise.get("category") or "").strip().lower()
    mapped = CATEGORY_TO_MUSCLES.get(category, set())
    if mapped and ex_muscles == mapped and not mapped.issubset(normalized_targets):
        return False
    if ex_muscles & normalized_targets:
        return True
    return False


def filter_exercises(exercises, profile, selected_muscles=None, preferred_equipment=None):
    limitations = set(_normalize_values(profile.get("limitations") or []))
    available_equipment = set(_normalize_values(profile.get("equipment") or []))
    available_equipment.update(_normalize_values(profile.get("custom_equipment") or []))
    preferred_equipment = set(_normalize_values(preferred_equipment or profile.get("preferred_equipment") or []))
    target_muscles = set(_normalize_values(selected_muscles or profile.get("target_muscles") or []))
    ignored = set(profile.get("ignored_exercises") or [])

    if "full body" in target_muscles and len(target_muscles) > 1:
        target_muscles.discard("full body")

    filtered = []
    for ex in exercises:
        if ex.get("id") in ignored:
            continue
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


def build_workout(profile, day_label, selected_muscles=None, preferred_equipment=None, target_count=6):
    exercises = load_exercises()
    filtered = filter_exercises(exercises, profile, selected_muscles, preferred_equipment)
    if _norm(str(day_label or "")) == "recovery":
        filtered = [ex for ex in filtered if is_recovery_exercise(ex)]
    difficulty_cap = determine_difficulty_cap(profile.get("fitness_level"))
    goal = determine_goal(profile.get("current_weight", 0), profile.get("goal_weight", 0))

    target_count = max(3, min(int(target_count or 6), 10))
    chosen = _pick(filtered, target_count, difficulty_cap)
    if len(chosen) < target_count:
        # Relax difficulty constraint first (e.g. intermediate exercises for a beginner profile)
        chosen = _pick(filtered, target_count, {1, 2, 3})
    if len(chosen) < target_count and not selected_muscles:
        # Not enough exercises even without difficulty filter — draw from full library
        chosen = _pick(exercises, target_count, {1, 2, 3})

    log.info(
        "workout build label=%r focus=%s eligible=%d chosen=%s",
        day_label,
        list(selected_muscles or []),
        len(filtered),
        [ex.get("id") for ex in chosen],
    )

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
    ignored = set(profile.get("ignored_exercises") or [])
    out = []
    for ex in exercises:
        ex_id = ex.get("id", "")
        ex_limitations = set(_exercise_limitations(ex))
        ex_equipment = set(_exercise_equipment(ex))
        needs_equipment = (
            ex_equipment
            and "none" not in ex_equipment
            and "bodyweight" not in ex_equipment
        )
        is_blocked = False
        reasons = []
        if ex_id in ignored:
            is_blocked = True
            reasons.append("ignored")
        elif blocked_limitations & ex_limitations:
            is_blocked = True
            reasons.append("blocked by limitation")
        elif needs_equipment and available_equipment and not (ex_equipment & available_equipment):
            is_blocked = True
            reasons.append("missing equipment")
        out.append({
            "id": ex_id,
            "name": ex.get("name"),
            "category": ex.get("category", ""),
            "muscles": _exercise_muscles(ex),
            "equipment": sorted(ex_equipment) or ["none"],
            "difficulty": ex.get("difficulty", 1),
            "instructions": ex.get("instructions", ""),
            "available": not is_blocked,
            "ignored": ex_id in ignored,
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


def _snap_overload_weight(ex_id, equipment, current, suggested):
    """Snap a suggested weight UP onto what the equipment can actually load:
    dumbbells move in 5s, barbell is the 45 lb bar + plate pairs (10 lb jumps),
    landmine is the bar + one-side plates (5 lb jumps), cable is the effective
    stack/2 weight (5.5 lb jumps, 220 lb stack = 110 max)."""
    eq = " ".join(equipment or []).replace("_", " ").lower()

    def up(base, step, cap=None):
        k = math.ceil(round((suggested - base) / step, 6))
        w = base + max(k, 0) * step
        if w <= current:
            w = current + step
        if cap is not None:
            w = min(w, cap)
        return round(w, 1)

    ex_id = str(ex_id).lower()
    if ex_id.startswith("landmine"):
        return up(45, 5)
    if "cage" in eq or "cable" in eq:
        # Cage tag mixes cable attachments, bodyweight bars and racked barbell lifts.
        if re.search(r"cable|pulldown|pushdown|face.?pull", ex_id):
            return up(0, 5.5, cap=110)
        if re.search(r"pullup|pull.?up|dip", ex_id):
            return suggested
        return up(45, 10)
    if "barbell" in eq:
        return up(45, 10)
    if "dumbbell" in eq:
        return up(0, 5)
    return suggested


def get_progressive_overload_suggestions(exercise_history):
    """
    Analyze exercise history and return suggestions for progressive overload.
    Returns list of {exercise_id, exercise_name, current_weight, suggested_weight, reason}.
    """
    suggestions = []
    for ex_id, sessions in exercise_history.items():
        # Need at least 3 sessions with logged sets to make a suggestion
        sessions_with_sets = [s for s in sessions if s.get("sets")]
        if len(sessions_with_sets) < 3:
            continue

        # Look at the 3 most recent sessions with logged weights
        recent = sessions_with_sets[:3]
        weights = []
        for session in recent:
            session_weights = [s["weight"] for s in session["sets"] if s.get("weight")]
            if session_weights:
                weights.append(max(session_weights))

        if len(weights) < 3:
            continue

        # If weight has been consistent across last 3 sessions, suggest an increase
        min_w, max_w = min(weights), max(weights)
        if max_w > 0 and (max_w - min_w) / max_w < 0.1:  # within 10% = consistent
            # Suggest ~5-10% increase, rounded to nearest 2.5lbs
            increase = max(2.5, round(max_w * 0.075 / 2.5) * 2.5)
            suggested = max_w + increase
            ex_data = get_exercise_by_id(ex_id)
            suggested = _snap_overload_weight(ex_id, (ex_data or {}).get("equipment"), max_w, suggested)
            if suggested <= max_w:
                continue
            suggestions.append({
                "exercise_id": ex_id,
                "exercise_name": ex_data["name"] if ex_data else ex_id,
                "current_weight": max_w,
                "suggested_weight": suggested,
                "sessions_at_current": len(weights),
            })

    return suggestions
