"""Background sync of completed HomeFit workouts to SparkyFitness."""

import json
import os
import re
import threading
from datetime import date, datetime

import requests

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_CONFIG_PATH = os.path.join(_DATA_DIR, "sparky_config.json")
_CACHE_PATH = os.path.join(_DATA_DIR, "sparky_exercise_cache.json")
_EXERCISES_PATH = os.path.join(_DATA_DIR, "exercises.json")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# ── HomeFit → Sparky mappings (for pushing exercises up) ─────────────────────

_CATEGORY_MAP = {
    "upper": "Strength",
    "legs": "Strength",
    "core": "Strength",
    "cardio": "Cardio",
}

_MUSCLE_MAP = {
    "upper": ["Upper Body"],
    "legs": ["Legs"],
    "core": ["Core"],
    "cardio": ["Cardiovascular"],
}

_EQUIPMENT_MAP = {
    "bodyweight": "None",
    "dumbbells": "Dumbbell",
    "kettlebell": "Kettlebell",
    "resistance_bands": "Resistance Band",
    "barbell": "Barbell",
    "pull_up_bar": "Pull-up Bar",
    "bench": "Bench",
    "bench_or_chair": "Bench",
    "cable": "Cable",
    "machine": "Machine",
    "power_cage_cable": "Cable",
    "ab_machine": "Machine",
    "pull-up bar": "Pull-up Bar",
}

# ── Sparky → HomeFit mappings (for pulling exercises down) ───────────────────

_SPARKY_EQUIPMENT_TO_HOMEFIT = {
    "none": "bodyweight",
    "body only": "bodyweight",
    "bodyweight": "bodyweight",
    "dumbbell": "dumbbells",
    "dumbbells": "dumbbells",
    "kettlebell": "kettlebell",
    "kettlebells": "kettlebell",
    "resistance band": "resistance_bands",
    "resistance bands": "resistance_bands",
    "bands": "resistance_bands",
    "band": "resistance_bands",
    "barbell": "barbell",
    "e-z curl bar": "barbell",
    "pull-up bar": "pull_up_bar",
    "bench": "bench_or_chair",
    "cable": "power_cage_cable",
    "machine": "machine",
    "ab machine": "ab_machine",
    "power cage": "power_cage_cable",
    "other": "bodyweight",
    "exercise ball": "bodyweight",
    "medicine ball": "kettlebell",
    "foam roll": "bodyweight",
}

_BROAD_MUSCLE_MAP = {
    "upper body": "upper",
    "legs": "legs",
    "lower body": "legs",
    "core": "core",
    "cardiovascular": "cardio",
}
_LEG_KEYWORDS = {"quad", "hamstring", "glute", "calf", "calve", "adduct", "abduct", "hip", "leg"}
_CORE_KEYWORDS = {"ab", "oblique", "core", "transverse"}


def _parse_muscles(val):
    """Return a list of muscle strings regardless of whether val is a list or JSON string."""
    if not val:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [val]
    return []


def _sparky_category_to_homefit(sparky_ex):
    cat = (sparky_ex.get("category") or "").lower()
    if cat == "cardio":
        return "cardio"
    if cat in ("stretching", "flexibility"):
        return "core"
    muscles_list = _parse_muscles(sparky_ex.get("primary_muscles"))
    # Direct match for our broad muscle labels
    for m in muscles_list:
        result = _BROAD_MUSCLE_MAP.get(m.lower())
        if result:
            return result
    # Keyword search for Free Exercise DB muscle names
    muscles_str = " ".join(muscles_list).lower()
    for kw in _LEG_KEYWORDS:
        if kw in muscles_str:
            return "legs"
    for kw in _CORE_KEYWORDS:
        if kw in muscles_str:
            return "core"
    return "upper"


def _sparky_equipment_to_homefit(equipment_list):
    if not equipment_list:
        return "bodyweight"
    first = (equipment_list[0] or "").lower().strip()
    return _SPARKY_EQUIPMENT_TO_HOMEFIT.get(first, "bodyweight")


def _sparky_level_to_difficulty(level):
    return {"beginner": 1, "intermediate": 2, "expert": 3, "advanced": 3}.get(
        (level or "").lower(), 1
    )


def _sparky_to_homefit(ex):
    instructions = ex.get("instructions") or []
    instructions_str = " ".join(instructions) if isinstance(instructions, list) else str(instructions)
    return {
        "id": ex["id"],
        "name": ex["name"],
        "category": _sparky_category_to_homefit(ex),
        "difficulty": _sparky_level_to_difficulty(ex.get("level")),
        "equipment": _sparky_equipment_to_homefit(ex.get("equipment") or []),
        "muscle_group": (_parse_muscles(ex.get("primary_muscles")) or ["full_body"])[0].lower().replace(" ", "_"),
        "muscles": _parse_muscles(ex.get("primary_muscles")),
        "contraindications": [],
        "default_reps": 10,
        "default_sets": 3,
        "rest_seconds": 45,
        "instructions": instructions_str,
        "met": (ex.get("calories_per_hour") or 350) / 70,
        "unit": "reps",
    }


# ── Config / cache helpers ────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(_CONFIG_PATH):
        return {}
    with open(_CONFIG_PATH) as f:
        return json.load(f)


def save_config(url, api_key):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        json.dump({"url": url.rstrip("/"), "api_key": api_key}, f)


def test_connection(url, api_key):
    """Return (ok: bool, message: str)."""
    try:
        r = requests.get(
            f"{url.rstrip('/')}/api/exercises",
            params={"limit": 1},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if r.ok:
            return True, "Connected successfully."
        return False, f"Server returned {r.status_code}."
    except requests.exceptions.ConnectionError:
        return False, "Could not reach SparkyFitness. Check the URL."
    except Exception as e:
        return False, str(e)


def _load_cache():
    if not os.path.exists(_CACHE_PATH):
        return {}
    with open(_CACHE_PATH) as f:
        return json.load(f)


def _save_cache(cache):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_CACHE_PATH, "w") as f:
        json.dump(cache, f)


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def _is_uuid(s):
    return bool(s and _UUID_RE.match(str(s)))


# ── Exercise library: push HomeFit → Sparky ──────────────────────────────────

def push_exercises_to_sparky():
    """Push all exercises from exercises.json to Sparky (skips ones already there)."""
    config = load_config()
    if not config.get("url") or not config.get("api_key"):
        return False, "SparkyFitness not configured."

    base_url = config["url"]
    api_key = config["api_key"]

    with open(_EXERCISES_PATH) as f:
        exercises = json.load(f)

    # Only push exercises whose id is NOT already a UUID (i.e. original HomeFit exercises)
    to_push = [e for e in exercises if not _is_uuid(e.get("id"))]
    if not to_push:
        return True, "All exercises are already Sparky-sourced."

    pushed, skipped = 0, 0
    cache = _load_cache()

    for ex in to_push:
        name = ex["name"]
        if name in cache:
            skipped += 1
            continue

        # Check if it already exists in Sparky
        r = requests.get(f"{base_url}/api/exercises",
                         params={"search": name, "limit": 5},
                         headers=_headers(api_key), timeout=10)
        if r.ok:
            for s_ex in r.json().get("exercises", []):
                if s_ex["name"].lower() == name.lower():
                    cache[name] = s_ex["id"]
                    skipped += 1
                    break
            else:
                # Create it
                category = ex.get("category", "upper")
                equip = ex.get("equipment", "bodyweight")
                muscles = _MUSCLE_MAP.get(category, ["Full Body"])

                data = json.dumps({
                    "name": name,
                    "category": _CATEGORY_MAP.get(category, "Strength"),
                    "equipment": [_EQUIPMENT_MAP.get(equip, equip.capitalize())],
                    "primary_muscles": muscles,
                    "description": ex.get("instructions", name),
                    "instructions": [ex.get("instructions", name)],
                    "is_public": False,
                    "source": "custom",
                    "is_custom": True,
                })
                cr = requests.post(f"{base_url}/api/exercises",
                                   headers=_headers(api_key),
                                   files={"exerciseData": (None, data)},
                                   timeout=10)
                if cr.ok:
                    cache[name] = cr.json()["id"]
                    pushed += 1

    _save_cache(cache)
    return True, f"Pushed {pushed} exercises to Sparky, {skipped} already existed."


# ── Exercise library: fetch Sparky → HomeFit ─────────────────────────────────

def fetch_and_replace_exercises():
    """Pull all exercises from Sparky and overwrite exercises.json."""
    config = load_config()
    if not config.get("url") or not config.get("api_key"):
        return False, "SparkyFitness not configured."

    try:
        skip_sources = {"HealthKit", "healthkit"}
        sparky_exercises = []
        offset = 0
        page_size = 10

        while True:
            r = requests.get(
                f"{config['url']}/api/exercises",
                params={"limit": page_size, "offset": offset},
                headers=_headers(config["api_key"]),
                timeout=30,
            )
            if not r.ok:
                return False, f"Sparky returned {r.status_code}."
            data = r.json()
            page = data.get("exercises", [])
            sparky_exercises += [
                e for e in page
                if e.get("name") and e.get("source") not in skip_sources
            ]
            if len(page) < page_size or offset + page_size >= data.get("totalCount", 0):
                break
            offset += page_size

        homefit_exercises = [_sparky_to_homefit(e) for e in sparky_exercises]

        with open(_EXERCISES_PATH, "w") as f:
            json.dump(homefit_exercises, f, indent=2)

        # Clear the name→UUID cache since IDs are now baked into exercises.json
        if os.path.exists(_CACHE_PATH):
            os.remove(_CACHE_PATH)

        return True, f"exercises.json replaced with {len(homefit_exercises)} exercises from Sparky."
    except Exception as e:
        return False, str(e)


# ── Workout sync ─────────────────────────────────────────────────────────────

def _find_or_create_exercise(base_url, api_key, exercise):
    """Return the Sparky UUID for this exercise.

    If the exercise id is already a Sparky UUID (post-migration), use it directly.
    Otherwise fall back to name-based lookup/create for legacy HomeFit IDs.
    """
    # Fast path: exercise came from Sparky, ID is already a UUID
    if _is_uuid(exercise.get("id")):
        return exercise["id"]

    cache = _load_cache()
    name = exercise.get("name")
    if not name:
        return None

    if name in cache:
        return cache[name]

    r = requests.get(
        f"{base_url}/api/exercises",
        params={"search": name, "limit": 10},
        headers=_headers(api_key),
        timeout=10,
    )
    if r.ok:
        for ex in r.json().get("exercises", []):
            if ex["name"].lower() == name.lower():
                cache[name] = ex["id"]
                _save_cache(cache)
                return ex["id"]

    # Not found — create it
    category = exercise.get("category", "upper")
    equip = exercise.get("equipment", "bodyweight")
    if isinstance(equip, list):
        equip = equip[0] if equip else "bodyweight"

    exercise_data = json.dumps({
        "name": name,
        "category": _CATEGORY_MAP.get(category, "Strength"),
        "equipment": [_EQUIPMENT_MAP.get(equip, equip.capitalize())],
        "primary_muscles": _MUSCLE_MAP.get(category, ["Full Body"]),
        "description": exercise.get("instructions", f"Imported from HomeFit"),
        "instructions": [exercise.get("instructions", name)],
        "is_public": False,
        "source": "custom",
        "is_custom": True,
    })

    r = requests.post(
        f"{base_url}/api/exercises",
        headers=_headers(api_key),
        files={"exerciseData": (None, exercise_data)},
        timeout=10,
    )
    if r.ok:
        exercise_id = r.json()["id"]
        cache[name] = exercise_id
        _save_cache(cache)
        return exercise_id

    return None


def _sync_workout(config, exercises, workout_date, duration_seconds):
    base_url = config["url"]
    api_key = config["api_key"]

    if isinstance(workout_date, (datetime, date)):
        date_str = workout_date.strftime("%Y-%m-%d")
    else:
        date_str = str(workout_date)[:10]

    exercise_count = max(len(exercises), 1)
    duration_per_exercise = max(1, (duration_seconds or 0) // exercise_count // 60)

    for exercise in exercises:
        try:
            exercise_id = _find_or_create_exercise(base_url, api_key, exercise)
            if not exercise_id:
                continue

            sets_count = int(exercise.get("sets") or exercise.get("default_sets") or 3)
            reps = int(exercise.get("reps") or exercise.get("default_reps") or 10)
            is_timed = exercise.get("unit") == "seconds"

            sets_data = []
            for i in range(sets_count):
                s = {"set_number": i + 1}
                if is_timed:
                    s["duration"] = reps
                else:
                    s["reps"] = reps
                sets_data.append(s)

            requests.post(
                f"{base_url}/api/exercise-entries",
                headers={**_headers(api_key), "Content-Type": "application/json"},
                json={
                    "exercise_id": exercise_id,
                    "entry_date": date_str,
                    "duration_minutes": duration_per_exercise,
                    "sets": sets_data,
                },
                timeout=10,
            )
        except Exception:
            pass


def sync_workout_async(exercises, workout_date, duration_seconds):
    """Fire-and-forget: push a completed workout to SparkyFitness in the background."""
    config = load_config()
    if not config.get("url") or not config.get("api_key"):
        return

    t = threading.Thread(
        target=_sync_workout,
        args=(config, exercises, workout_date, duration_seconds),
        daemon=True,
    )
    t.start()


# ── Weight sync ───────────────────────────────────────────────────────────────

def _sync_weight(config, weight, log_date):
    # SparkyFitness stores weight in kg; HomeFit uses lbs
    weight_kg = round(weight / 2.20462, 4)
    try:
        requests.post(
            f"{config['url']}/api/health-data",
            headers={**_headers(config["api_key"]), "Content-Type": "application/json"},
            json=[{"type": "weight", "value": weight_kg, "date": log_date}],
            timeout=10,
        )
    except Exception:
        pass


def sync_weight_async(weight, log_date=None):
    """Fire-and-forget: push a weight entry to SparkyFitness in the background."""
    config = load_config()
    if not config.get("url") or not config.get("api_key"):
        return

    if log_date is None:
        from datetime import date
        log_date = date.today().isoformat()
    elif hasattr(log_date, "isoformat"):
        log_date = log_date.isoformat()

    t = threading.Thread(
        target=_sync_weight,
        args=(config, weight, log_date),
        daemon=True,
    )
    t.start()
