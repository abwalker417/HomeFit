"""Background sync of completed HomeFit workouts to SparkyFitness."""

import json
import os
import threading
from datetime import date, datetime

import requests

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_CONFIG_PATH = os.path.join(_DATA_DIR, "sparky_config.json")
_CACHE_PATH = os.path.join(_DATA_DIR, "sparky_exercise_cache.json")

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
    "cable": "Cable",
    "machine": "Machine",
}


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


def _find_or_create_exercise(base_url, api_key, exercise):
    """Return the Sparky UUID for this exercise, creating it in Sparky if needed."""
    cache = _load_cache()
    name = exercise["name"]

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

    # Not found — create it as a custom exercise
    raw_equipment = exercise.get("equipment") or ["bodyweight"]
    mapped_equipment = [_EQUIPMENT_MAP.get(e, e.capitalize()) for e in raw_equipment]

    category = exercise.get("category", "upper")
    # HomeFit stores only a broad muscle_group; use category to give Sparky something meaningful
    muscles = exercise.get("muscles") or []
    non_generic = [m for m in muscles if m.lower() not in ("full body", "full_body")]
    mapped_muscles = [m.replace("_", " ").title() for m in non_generic] if non_generic \
        else _MUSCLE_MAP.get(category, ["Full Body"])

    exercise_data = json.dumps({
        "name": name,
        "category": _CATEGORY_MAP.get(category, "Strength"),
        "equipment": mapped_equipment,
        "muscle_groups": mapped_muscles,
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

            sets_count = int(exercise.get("sets") or 3)
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

            entry = {
                "exercise_id": exercise_id,
                "entry_date": date_str,
                "duration_minutes": duration_per_exercise,
                "sets": sets_data,
            }

            requests.post(
                f"{base_url}/api/exercise-entries",
                headers={**_headers(api_key), "Content-Type": "application/json"},
                json=entry,
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
