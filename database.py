"""SQLite persistence layer — multi-user v2 schema.

Schema is auto-migrated on app start: if an old v1 (single-user) schema is
detected, we drop it and recreate as v2. Users selected 'start fresh' during
the multi-user upgrade, so no data is preserved from v1.
"""

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

SCHEMA_VERSION = 5

DB_PATH = Path(os.environ.get(
    "HOMEFIT_DB",
    str(Path(__file__).parent / "data" / "workout.db"),
))

SECRET_KEY_PATH = Path(os.environ.get(
    "HOMEFIT_SECRET_KEY_FILE",
    str(Path(__file__).parent / "data" / "secret.key"),
))


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL so readers (dashboard, every page) never block on a writer (iOS
    # health/workout syncs); busy_timeout lets concurrent writers wait instead
    # of erroring with "database is locked".
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 8000")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    pin_hash   TEXT,
    emoji      TEXT NOT NULL DEFAULT '💪',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    user_id             INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    current_weight      REAL    NOT NULL,
    goal_weight         REAL    NOT NULL,
    fitness_level       TEXT    NOT NULL DEFAULT 'beginner',
    limitations         TEXT    NOT NULL DEFAULT '[]',
    equipment           TEXT    NOT NULL DEFAULT '[]',
    custom_equipment    TEXT    NOT NULL DEFAULT '[]',
    target_muscles      TEXT    NOT NULL DEFAULT '[]',
    preferred_equipment TEXT    NOT NULL DEFAULT '[]',
    days_per_week       INTEGER NOT NULL DEFAULT 4,
    updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS weight_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    weight    REAL    NOT NULL,
    logged_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS workout_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day_name         TEXT    NOT NULL,
    day_number       INTEGER NOT NULL,
    exercises_json   TEXT    NOT NULL,
    duration_seconds INTEGER,
    completed_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_weight_user  ON weight_log (user_id, logged_at);
CREATE INDEX IF NOT EXISTS ix_workout_user ON workout_log (user_id, completed_at);
"""


def _current_version(conn):
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return row["version"] if row else 0
    except sqlite3.OperationalError:
        return 0


def _has_table(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _has_column(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _ensure_column(conn, table, column, definition):
    if not _has_column(conn, table, column):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as e:
            # Concurrent gunicorn workers can both pass the _has_column check and
            # race the ALTER on first boot after a new column is added.
            if "duplicate column name" not in str(e):
                raise


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        version = _current_version(conn)

        if version < 2 and _has_table(conn, "profile"):
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(profile)")]
            if "user_id" not in cols:
                for tbl in ("workout_log", "weight_log", "profile"):
                    conn.execute(f"DROP TABLE IF EXISTS {tbl}")

        conn.executescript(SCHEMA_SQL)
        _ensure_column(conn, "profile", "equipment", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "profile", "custom_equipment", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "profile", "target_muscles", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "profile", "preferred_equipment", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "profile", "ignored_exercises", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "profile", "cardio_days_per_week", "INTEGER NOT NULL DEFAULT 5")
        _ensure_column(conn, "profile", "fitness_goal", "TEXT NOT NULL DEFAULT 'general'")
        _ensure_column(conn, "profile", "workout_duration_target", "INTEGER NOT NULL DEFAULT 45")
        _ensure_column(conn, "profile", "accent_color", "TEXT NOT NULL DEFAULT '#f97316'")
        _ensure_column(conn, "users", "api_token", "TEXT")
        _ensure_column(conn, "users", "photo", "TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apex_plan (
                user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                plan_json  TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apex_chat (
                user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                messages    TEXT NOT NULL DEFAULT '[]',
                updated_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apex_weekly_digest (
                user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                digest_text  TEXT NOT NULL,
                week_start   TEXT NOT NULL,
                generated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apex_daily_brief (
                user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                brief_text   TEXT NOT NULL,
                brief_date   TEXT NOT NULL,
                generated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apex_memory (
                user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                content    TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                meal_date   TEXT NOT NULL,
                description TEXT NOT NULL,
                items_json  TEXT NOT NULL,
                calories    INTEGER NOT NULL DEFAULT 0,
                protein_g   REAL NOT NULL DEFAULT 0,
                carbs_g     REAL NOT NULL DEFAULT 0,
                fat_g       REAL NOT NULL DEFAULT 0,
                cost_usd    REAL NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nutrition_goal (
                user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                calories   INTEGER NOT NULL DEFAULT 2000,
                protein_g  REAL NOT NULL DEFAULT 120,
                carbs_g    REAL NOT NULL DEFAULT 200,
                fat_g      REAL NOT NULL DEFAULT 65,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_favorite (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                items_json  TEXT NOT NULL,
                calories    INTEGER NOT NULL DEFAULT 0,
                protein_g   REAL NOT NULL DEFAULT 0,
                carbs_g     REAL NOT NULL DEFAULT 0,
                fat_g       REAL NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workout_draft (
                user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                day_name   TEXT,
                data       TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS readiness_cache (
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cache_date TEXT NOT NULL,
                data       TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, cache_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint          TEXT PRIMARY KEY,
                user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subscription_json TEXT NOT NULL,
                created_at        TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apns_tokens (
                device_token TEXT PRIMARY KEY,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                environment  TEXT NOT NULL DEFAULT 'production',
                updated_at   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                scope      TEXT NOT NULL DEFAULT 'summary',
                label      TEXT,
                created_at TEXT NOT NULL,
                revoked    INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nudge_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                nudge_type TEXT NOT NULL,
                nudge_date TEXT NOT NULL,
                body       TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, nudge_type, nudge_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS external_workouts (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source           TEXT NOT NULL DEFAULT 'apple_health',
                workout_type     TEXT NOT NULL,
                started_at       TEXT NOT NULL,
                ended_at         TEXT,
                duration_minutes INTEGER NOT NULL DEFAULT 0,
                kcal             INTEGER,
                distance_mi      REAL,
                avg_hr           INTEGER,
                status           TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                UNIQUE(user_id, workout_type, started_at)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_metric (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                metric      TEXT NOT NULL,
                metric_date TEXT NOT NULL,
                value       REAL NOT NULL,
                source      TEXT NOT NULL DEFAULT 'apple_health',
                created_at  TEXT NOT NULL,
                UNIQUE(user_id, metric, metric_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sleep_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source           TEXT NOT NULL DEFAULT 'apple_health',
                entry_date       TEXT NOT NULL,
                bedtime          TEXT NOT NULL,
                wake_time        TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                deep_seconds     INTEGER,
                rem_seconds      INTEGER,
                light_seconds    INTEGER,
                awake_seconds    INTEGER,
                status           TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                UNIQUE(user_id, entry_date)
            )
        """)


        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


def load_or_create_secret_key() -> bytes:
    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_bytes()
    key = secrets.token_bytes(48)
    SECRET_KEY_PATH.write_bytes(key)
    try:
        os.chmod(SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


def list_users():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, emoji, photo, (pin_hash IS NOT NULL) AS has_pin FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_user(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, emoji, created_at, (pin_hash IS NOT NULL) AS has_pin FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def create_user(name: str, emoji: str = "💪", pin: Optional[str] = None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required")
    pin_hash = generate_password_hash(pin) if pin else None
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, pin_hash, emoji, created_at) VALUES (?, ?, ?, ?)",
            (name, pin_hash, emoji or "💪", now),
        )
        return cur.lastrowid


def update_user(user_id: int, name: str, emoji: str, pin: Optional[str]):
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required")
    with get_connection() as conn:
        if pin:
            conn.execute(
                "UPDATE users SET name = ?, emoji = ?, pin_hash = ? WHERE id = ?",
                (name, emoji or "💪", generate_password_hash(pin), user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET name = ?, emoji = ? WHERE id = ?",
                (name, emoji or "💪", user_id),
            )


def delete_user(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def clear_pin(user_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE users SET pin_hash = NULL WHERE id = ?", (user_id,))


def verify_pin(user_id: int, pin: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT pin_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["pin_hash"]:
        return False
    return check_password_hash(row["pin_hash"], pin)


def _decode_json_list(value):
    try:
        data = json.loads(value or "[]")
    except json.JSONDecodeError:
        data = []
    return data if isinstance(data, list) else []


def get_profile(user_id):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM profile WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        p = dict(row)
        for field in ("limitations", "equipment", "custom_equipment", "target_muscles", "preferred_equipment", "ignored_exercises"):
            p[field] = _decode_json_list(p.get(field))
        return p


def save_profile(
    user_id,
    current_weight,
    goal_weight,
    fitness_level,
    limitations,
    days_per_week,
    equipment=None,
    custom_equipment=None,
    target_muscles=None,
    preferred_equipment=None,
    ignored_exercises=None,
    fitness_goal="general",
    workout_duration_target=45,
    cardio_days_per_week=5,
):
    now = datetime.now().isoformat()
    values = (
        user_id,
        current_weight,
        goal_weight,
        fitness_level,
        json.dumps(limitations or []),
        json.dumps(equipment or []),
        json.dumps(custom_equipment or []),
        json.dumps(target_muscles or []),
        json.dumps(preferred_equipment or []),
        days_per_week,
        json.dumps(ignored_exercises or []),
        fitness_goal,
        int(workout_duration_target),
        int(cardio_days_per_week or 5),
        now,
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO profile (
                user_id, current_weight, goal_weight, fitness_level, limitations,
                equipment, custom_equipment, target_muscles, preferred_equipment,
                days_per_week, ignored_exercises,
                fitness_goal, workout_duration_target, cardio_days_per_week, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                current_weight=excluded.current_weight,
                goal_weight=excluded.goal_weight,
                fitness_level=excluded.fitness_level,
                limitations=excluded.limitations,
                equipment=excluded.equipment,
                custom_equipment=excluded.custom_equipment,
                target_muscles=excluded.target_muscles,
                preferred_equipment=excluded.preferred_equipment,
                days_per_week=excluded.days_per_week,
                cardio_days_per_week=excluded.cardio_days_per_week,
                ignored_exercises=excluded.ignored_exercises,
                fitness_goal=excluded.fitness_goal,
                workout_duration_target=excluded.workout_duration_target,
                updated_at=excluded.updated_at
            """,
            values,
        )
        last = conn.execute(
            "SELECT weight FROM weight_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not last or last["weight"] != current_weight:
            conn.execute(
                "INSERT INTO weight_log (user_id, weight, logged_at) VALUES (?, ?, ?)",
                (user_id, current_weight, now),
            )


def toggle_ignored_exercise(user_id, exercise_id):
    """Add exercise_id to ignored list if not present, remove it if it is. Returns new state (True=ignored)."""
    with get_connection() as conn:
        row = conn.execute("SELECT ignored_exercises FROM profile WHERE user_id = ?", (user_id,)).fetchone()
        ignored = _decode_json_list(row["ignored_exercises"] if row else None)
        if exercise_id in ignored:
            ignored.remove(exercise_id)
            now_ignored = False
        else:
            ignored.append(exercise_id)
            now_ignored = True
        conn.execute(
            "UPDATE profile SET ignored_exercises = ? WHERE user_id = ?",
            (json.dumps(ignored), user_id),
        )
    return now_ignored


def log_weight(user_id, weight):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO weight_log (user_id, weight, logged_at) VALUES (?, ?, ?)",
            (user_id, weight, now),
        )
        conn.execute(
            "UPDATE profile SET current_weight = ?, updated_at = ? WHERE user_id = ?",
            (weight, now, user_id),
        )


def get_weight_history(user_id, limit=60):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT weight, logged_at FROM weight_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def log_workout(user_id, day_name, day_number, exercises, duration_seconds):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO workout_log (user_id, day_name, day_number, exercises_json, duration_seconds, completed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, day_name, day_number, json.dumps(exercises), duration_seconds, now),
        )


def get_workout_history(user_id, limit=50):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM workout_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["exercises"] = _decode_json_list(d.pop("exercises_json", "[]"))
            result.append(d)
        return result


def get_last_workout(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM workout_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["exercises"] = _decode_json_list(d.pop("exercises_json", "[]"))
        return d


def get_workout_by_id(user_id, log_id):
    """One logged workout (owner-scoped), exercises decoded. None if not found."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM workout_log WHERE id = ? AND user_id = ?", (log_id, user_id),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["exercises"] = _decode_json_list(d.pop("exercises_json", "[]"))
    return d


def update_workout_exercises(user_id, log_id, exercises):
    """Replace a logged workout's exercises (e.g. fix a forgotten weight). Owner-scoped."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE workout_log SET exercises_json = ? WHERE id = ? AND user_id = ?",
            (json.dumps(exercises), log_id, user_id),
        )
        return cur.rowcount > 0


def save_workout_draft(user_id, day_name, data_json):
    """Autosave an in-progress workout server-side so a client crash can't lose it.
    data_json is the client's full state blob (done + per-set weights/reps + timer)."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO workout_draft (user_id, day_name, data, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 day_name=excluded.day_name, data=excluded.data, updated_at=excluded.updated_at""",
            (user_id, day_name, data_json, datetime.now().isoformat()),
        )


def get_workout_draft(user_id):
    """Return the saved in-progress draft {day_name, state} or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT day_name, data FROM workout_draft WHERE user_id = ?", (user_id,),
        ).fetchone()
    if not row:
        return None
    try:
        return {"day_name": row["day_name"], "state": json.loads(row["data"])}
    except Exception:
        return None


def clear_workout_draft(user_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM workout_draft WHERE user_id = ?", (user_id,))


def get_exercise_history(user_id, limit=10):
    """Return per-exercise set/rep/weight history across recent workouts."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT exercises_json, completed_at FROM workout_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    history = {}
    for row in rows:
        date = row["completed_at"][:10]
        exercises = _decode_json_list(row["exercises_json"])
        for ex in exercises:
            ex_id = ex.get("id")
            if not ex_id or not ex.get("completed"):
                continue
            if ex_id not in history:
                history[ex_id] = []
            history[ex_id].append({
                "date": date,
                "sets": ex.get("sets", []),
            })
    return history


def get_coaching_context(user_id):
    """Build full context for the AI coach — profile, history, progression."""
    profile = get_profile(user_id)
    workouts = get_workout_history(user_id, limit=10)
    weight_history = get_weight_history(user_id, limit=10)
    exercise_history = get_exercise_history(user_id, limit=15)
    plan_data = get_apex_plan(user_id)
    profile = profile or {}
    nutrition = []
    hydration = []
    goals = {}
    other_activity = []

    # Nutrition + goals come from HomeFit's own food log.
    homefit_food = get_food_log_days(user_id, days=7)
    if homefit_food:
        nutrition = [{"date": d["meal_date"], "calories": d["calories"],
                      "protein_g": round(d["protein_g"]), "carbs_g": round(d["carbs_g"]),
                      "fat_g": round(d["fat_g"]), "meals": {}} for d in homefit_food]
    hf_goal = get_nutrition_goal(user_id)
    if hf_goal:
        goals = {"calories": hf_goal["calories"], "protein_g": hf_goal["protein_g"],
                 "carbs_g": hf_goal["carbs_g"], "fat_g": hf_goal["fat_g"]}
    return {
        "profile": profile,
        "recent_workouts": workouts,
        "weight_history": weight_history,
        "exercise_history": exercise_history,
        "apex_plan": plan_data["plan"] if plan_data else None,
        "nutrition_log": nutrition,
        "hydration_log": hydration,
        "nutrition_goals": goals,
        "other_activity": other_activity,
        "external_workouts": get_external_workouts(user_id, days=7),
        "sleep_log": get_recent_sleep(user_id, days=7),
        "readiness": get_readiness(user_id),
        "training_load": training_load(user_id),
        "energy_log": get_energy_log(user_id, days=7),
        "steps_log": [{"date": r["metric_date"], "steps": int(r["value"])}
                      for r in get_recent_metric(user_id, "steps", days=7)],
        "step_goal": get_step_goal(user_id),
        "apex_memory": get_apex_memory(user_id),
    }


def get_steps_today(user_id):
    today = datetime.now().date().isoformat()
    for r in get_recent_metric(user_id, "steps", days=2):
        if r["metric_date"] == today:
            return int(r["value"])
    return None


def get_step_goal(user_id):
    """Personalized step goal = each person's own ~2-week average (floored).
    Self-adjusts per lifestyle (office vs on-your-feet); 8000 default until
    there's enough history."""
    today = datetime.now().date().isoformat()
    vals = [r["value"] for r in get_recent_metric(user_id, "steps", days=15)
            if r["metric_date"] != today and r["value"]]   # exclude today (partial)
    if len(vals) >= 5:
        avg = sum(vals) / len(vals)
        return int(max(6000, round(avg / 500) * 500))
    return 8000


def get_activity_rings(user_id):
    """Today's Apple Activity rings (Move/Exercise/Stand) with the user's goals."""
    today = datetime.now().date().isoformat()

    def m(name):
        for r in get_recent_metric(user_id, name, days=2):
            if r["metric_date"] == today:
                return int(r["value"])
        return None

    return {
        "move":     {"current": m("active_energy"),    "goal": m("move_goal")},
        "exercise": {"current": m("exercise_minutes"), "goal": m("exercise_goal")},
        "stand":    {"current": m("stand_hours"),      "goal": m("stand_goal")},
    }


def get_energy_log(user_id, days=7):
    """Per-day Apple Health energy: active (move) + basal (resting) kcal."""
    active = {r["metric_date"]: int(r["value"]) for r in get_recent_metric(user_id, "active_energy", days)}
    resting = {r["metric_date"]: int(r["value"]) for r in get_recent_metric(user_id, "resting_energy", days)}
    out = []
    for d in sorted(set(active) | set(resting), reverse=True):
        out.append({"date": d, "active": active.get(d), "resting": resting.get(d),
                    "total": (active.get(d) or 0) + (resting.get(d) or 0)})
    return out


def get_or_create_api_token(user_id):
    with get_connection() as conn:
        row = conn.execute("SELECT api_token FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and row["api_token"]:
            return row["api_token"]
        token = secrets.token_urlsafe(32)
        conn.execute("UPDATE users SET api_token = ? WHERE id = ?", (token, user_id))
        return token


def get_user_id_by_token(token):
    if not token:
        return None
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE api_token = ?", (token,)).fetchone()
        return row["id"] if row else None


# --- Scoped API keys (read-only, revocable; for external agents like NyX) ---
# Distinct from the per-user `api_token`, which is full-access (it works on write
# relays too). A scoped key only authenticates on endpoints that opt in via
# resolve_scoped_uid(token, scope), so it can't reach write endpoints.

def create_api_key(user_id, scope="summary", label=""):
    token = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO api_keys (token, user_id, scope, label, created_at, revoked) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (token, user_id, scope, label, datetime.now().isoformat()))
    return token


def get_api_key(token):
    if not token:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT user_id, scope FROM api_keys WHERE token = ? AND revoked = 0",
            (token,)).fetchone()
    return {"user_id": row["user_id"], "scope": row["scope"] or ""} if row else None


def revoke_api_key(token):
    with get_connection() as conn:
        conn.execute("UPDATE api_keys SET revoked = 1 WHERE token = ?", (token,))


def list_api_keys(user_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT token, scope, label, created_at, revoked FROM api_keys WHERE user_id = ? "
            "ORDER BY created_at DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def resolve_scoped_uid(token, required_scope):
    """Return uid if `token` is a legacy full api_token, OR a scoped key whose
    scope grants `required_scope` (comma list, or 'all'). None otherwise."""
    uid = get_user_id_by_token(token)
    if uid:
        return uid
    key = get_api_key(token)
    if key:
        scopes = [s.strip() for s in (key["scope"] or "").split(",")]
        if "all" in scopes or required_scope in scopes:
            return key["user_id"]
    return None


def set_accent_color(user_id, color):
    """Set a profile's accent color. Profile row may not exist yet during
    onboarding, so upsert a minimal row if needed."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE profile SET accent_color = ? WHERE user_id = ?", (color, user_id)
        )
        if cur.rowcount == 0:
            # No profile yet (pre-onboarding) — stash on the row so it survives.
            conn.execute(
                """INSERT INTO profile (user_id, current_weight, goal_weight,
                       fitness_level, days_per_week, accent_color, updated_at)
                   VALUES (?, 0, 0, 'beginner', 4, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET accent_color=excluded.accent_color""",
                (user_id, color, now),
            )


def get_accent_color(user_id):
    if not user_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT accent_color FROM profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["accent_color"] if row and row["accent_color"] else None


# ── External workouts (Apple Health relay) ──────────────────────────────────

def find_external_workout(user_id, workout_type, started_at):
    """Idempotency check: has this exact workout already been received?"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, status FROM external_workouts WHERE user_id = ? AND workout_type = ? AND started_at = ?",
            (user_id, workout_type, started_at),
        ).fetchone()
        return dict(row) if row else None


def claim_external_workout(user_id, source, workout_type, started_at, ended_at,
                           duration_minutes, kcal, distance_mi, avg_hr):
    """Atomically claim a workout via the UNIQUE(user, type, started_at) constraint.

    Returns True only for the request that actually inserted the row — concurrent
    duplicate POSTs (the app fires sync on launch AND foreground) get False, so
    only one of them is recorded. Status starts 'pending'; caller updates it.
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO external_workouts
                (user_id, source, workout_type, started_at, ended_at,
                 duration_minutes, kcal, distance_mi, avg_hr, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, source, workout_type, started_at, ended_at,
             duration_minutes, kcal, distance_mi, avg_hr, datetime.now().isoformat()),
        )
        return cur.rowcount > 0


def set_external_workout_status(user_id, workout_type, started_at, status):
    with get_connection() as conn:
        conn.execute(
            "UPDATE external_workouts SET status = ? WHERE user_id = ? AND workout_type = ? AND started_at = ?",
            (status, user_id, workout_type, started_at),
        )


# ── Sleep (Apple Health / Oura relay) ───────────────────────────────────────

def claim_sleep(user_id, source, entry_date, bedtime, wake_time, duration_seconds,
                deep_s, rem_s, light_s, awake_s):
    """Atomically claim one night via UNIQUE(user, entry_date). Returns True if
    newly inserted (caller records it), False if this night already exists.
    Updates stats if a later post for the same night has a longer duration."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO sleep_log
                (user_id, source, entry_date, bedtime, wake_time, duration_seconds,
                 deep_seconds, rem_seconds, light_seconds, awake_seconds, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(user_id, entry_date) DO NOTHING
            """,
            (user_id, source, entry_date, bedtime, wake_time, duration_seconds,
             deep_s, rem_s, light_s, awake_s, now),
        )
        return cur.rowcount > 0


def set_sleep_status(user_id, entry_date, status):
    with get_connection() as conn:
        conn.execute(
            "UPDATE sleep_log SET status = ? WHERE user_id = ? AND entry_date = ?",
            (status, user_id, entry_date),
        )


def get_recent_sleep(user_id, days=14, limit=30):
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT entry_date, bedtime, wake_time, duration_seconds,
                   deep_seconds, rem_seconds, light_seconds, awake_seconds
            FROM sleep_log
            WHERE user_id = ? AND entry_date >= ? AND status IN ('synced', 'recorded')
            ORDER BY entry_date DESC LIMIT ?
            """,
            (user_id, cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def compute_readiness(user_id):
    """Daily readiness 0-100 from last night's sleep (duration + deep/REM) minus
    recent training load. Returns None when there's no recent sleep — so the
    score simply doesn't appear for users who aren't tracking sleep yet."""
    sleep = get_recent_sleep(user_id, days=2)
    if not sleep:
        return None
    n = sleep[0]
    secs = n.get("duration_seconds") or 0
    if secs <= 0:
        return None

    hours = secs / 3600.0
    # Duration: 4h -> 0, 8h -> 100
    dur_score = max(0.0, min(100.0, (hours - 4) / 4 * 100))
    deeprem = (n.get("deep_seconds") or 0) + (n.get("rem_seconds") or 0)
    quality = (deeprem / secs) if secs else 0
    # Quality: ~45% deep+REM of total sleep is excellent
    qual_score = max(0.0, min(100.0, quality / 0.45 * 100))
    sleep_score = 0.65 * dur_score + 0.35 * qual_score

    # Recent training load (last 2 days) — accumulated fatigue gently lowers it
    cutoff = (datetime.now() - timedelta(days=2)).isoformat()
    workouts = [w for w in get_workout_history(user_id, limit=20)
                if (w.get("completed_at") or "") >= cutoff]
    cardio = get_external_workouts(user_id, days=2)
    load = len(workouts) + len(cardio)
    penalty = min(load, 3) * 5

    # Elevated resting HR vs your recent baseline signals under-recovery
    rhr_note = ""
    rhr = get_recent_metric(user_id, "resting_hr", days=14)
    if rhr:
        latest = rhr[0]["value"]
        if len(rhr) >= 4:
            baseline = sum(r["value"] for r in rhr[1:8]) / len(rhr[1:8])
            if latest > baseline + 5:
                penalty += min(12, (latest - baseline))
                rhr_note = f" · resting HR {int(latest)} (↑ vs {int(baseline)} baseline)"
            else:
                rhr_note = f" · resting HR {int(latest)}"
        else:
            rhr_note = f" · resting HR {int(latest)}"

    score = int(round(max(0.0, min(100.0, sleep_score - penalty))))
    if score >= 80:
        level, label = "high", "Primed"
    elif score >= 60:
        level, label = "good", "Ready"
    elif score >= 40:
        level, label = "mod", "Take it easy"
    else:
        level, label = "low", "Recover"

    h, m = divmod(secs // 60, 60)
    reason = f"{h}h{m:02d}m sleep"
    if deeprem:
        reason += f", {deeprem // 60}m deep+REM"
    if load >= 2:
        reason += f" · {load} sessions in 2 days"
    reason += rhr_note
    return {"score": score, "level": level, "label": label, "reason": reason, "hours": round(hours, 1)}


def get_readiness(user_id):
    """Readiness for today, cached once per local day so the dashboard, daily
    digest, and APEX chat all show the SAME number. compute_readiness drifts
    through the day (logging a workout raises the fatigue penalty); caching the
    first real result keeps every surface consistent. Only a non-None result is
    cached, so it keeps retrying until last night's sleep is available."""
    today = datetime.now().date().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT data FROM readiness_cache WHERE user_id=? AND cache_date=?",
            (user_id, today)).fetchone()
    if row:
        try:
            return json.loads(row["data"])
        except Exception:
            pass
    r = compute_readiness(user_id)
    if r:
        # Only lock in the cache once readiness is based on LAST NIGHT's sleep.
        # entry_date is the WAKE date, so last night's sleep is dated TODAY. A
        # call before last night syncs (e.g. the post-midnight job) sees only
        # yesterday's entry; caching that would freeze a stale score all day. So
        # require today's entry — otherwise return the score uncached and let it
        # self-correct once last night lands.
        sleep = get_recent_sleep(user_id, days=2)
        fresh = sleep and (sleep[0].get("entry_date") or "") >= today
        if fresh:
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO readiness_cache (user_id, cache_date, data, created_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, cache_date) DO NOTHING""",
                    (user_id, today, json.dumps(r), datetime.now().isoformat()))
    return r


def upsert_daily_metric(user_id, metric, metric_date, value, source="apple_health"):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO daily_metric (user_id, metric, metric_date, value, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, metric, metric_date) DO UPDATE SET
                 value=excluded.value, source=excluded.source""",
            (user_id, metric, metric_date, value, source, datetime.now().isoformat()),
        )


def get_recent_metric(user_id, metric, days=14):
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT metric_date, value FROM daily_metric
               WHERE user_id = ? AND metric = ? AND metric_date >= ?
               ORDER BY metric_date DESC""",
            (user_id, metric, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_metric(user_id, metric):
    rows = get_recent_metric(user_id, metric, days=14)
    return rows[0] if rows else None


def training_load(user_id):
    """Recent training volume (workouts + cardio) for under-recovery checks."""
    now = datetime.now()
    c7 = (now - timedelta(days=7)).isoformat()
    c3 = (now - timedelta(days=3)).isoformat()
    workouts = get_workout_history(user_id, limit=40)
    w7 = [w for w in workouts if (w.get("completed_at") or "") >= c7]
    w3 = [w for w in workouts if (w.get("completed_at") or "") >= c3]
    cardio7 = get_external_workouts(user_id, days=7)
    cardio3 = get_external_workouts(user_id, days=3)
    minutes = sum((w.get("duration_seconds") or 0) // 60 for w in w7) \
        + sum(c.get("duration_minutes") or 0 for c in cardio7)
    return {
        "sessions_7d": len(w7) + len(cardio7),
        "sessions_3d": len(w3) + len(cardio3),
        "minutes_7d": int(minutes),
    }


def get_external_workouts(user_id, days=14, limit=50):
    """Recent cardio/external workouts to display in HomeFit (newest first).
    Excludes ones skipped as overlapping a HomeFit gym session."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT workout_type, started_at, ended_at, duration_minutes,
                   kcal, distance_mi, avg_hr
            FROM external_workouts
            WHERE user_id = ? AND started_at >= ?
              AND status IN ('synced', 'recorded')
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (user_id, cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def counts_as_workout_session(c):
    """True if an external/Apple-Health activity counts toward the weekly WORKOUT
    goal: Apple recorded it as a real workout (any type except a plain 'Walking'
    entry) OR it's a long session (>=45 min). Short daily walks don't count as a
    workout — they count toward the separate cardio-days goal instead."""
    wtype = (c.get("workout_type") or "").strip().lower()
    return wtype not in ("", "walking") or (c.get("duration_minutes") or 0) >= 45


def record_external_workout(user_id, source, workout_type, started_at, ended_at,
                            duration_minutes, kcal, distance_mi, avg_hr, status):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO external_workouts
                (user_id, source, workout_type, started_at, ended_at,
                 duration_minutes, kcal, distance_mi, avg_hr, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, source, workout_type, started_at, ended_at,
             duration_minutes, kcal, distance_mi, avg_hr, status,
             datetime.now().isoformat()),
        )


def find_overlapping_homefit_workout(user_id, start, end, pad_minutes=20):
    """Return the HomeFit workout whose time window overlaps [start, end], or None.

    Used to dedup external (HealthKit) workouts: a gym session recorded on the
    watch overlaps the HomeFit session that was already logged.
    start/end are naive-UTC datetimes; workout_log.completed_at is naive-UTC ISO.
    """
    pad = timedelta(minutes=pad_minutes)
    day_lo = (start - timedelta(days=1)).isoformat()
    day_hi = (end + timedelta(days=1)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, day_name, duration_seconds, completed_at FROM workout_log
            WHERE user_id = ? AND completed_at BETWEEN ? AND ?
            """,
            (user_id, day_lo, day_hi),
        ).fetchall()
    for r in rows:
        try:
            hf_end = datetime.fromisoformat(r["completed_at"])
        except (ValueError, TypeError):
            continue
        hf_start = hf_end - timedelta(seconds=r["duration_seconds"] or 0)
        if hf_start - pad <= end and start <= hf_end + pad:
            return dict(r)
    return None


def get_stats(user_id):
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM workout_log WHERE user_id = ?",
            (user_id,),
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT completed_at FROM workout_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        weights = conn.execute(
            "SELECT weight FROM weight_log WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()
    trend = None
    if len(weights) >= 2:
        trend = round(weights[-1]["weight"] - weights[0]["weight"], 1)
    return {
        "total_workouts": total,
        "last_workout": last["completed_at"] if last else None,
        "weight_change": trend,
        "starting_weight": weights[0]["weight"] if weights else None,
    }


def workout_day_dates(user_id, since_iso=None):
    """Set of ISO date strings (YYYY-MM-DD) on which the user did a WORKOUT:
    HomeFit logged sessions PLUS Apple-recorded workouts that count toward the
    weekly workout goal (golf, long >=45-min sessions). Plain walks don't count.

    Single source of truth so the dashboard, streak and push reminders all
    agree on what a "workout day" is. Optionally restrict to dates >= since_iso.
    """
    days = set()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date(completed_at) AS d FROM workout_log WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    for row in rows:
        d = row["d"]
        if d and (since_iso is None or d >= since_iso):
            days.add(d)
    # Apple-recorded workouts (golf etc.); wide window so streak history is right
    for c in get_external_workouts(user_id, days=400, limit=5000):
        if not counts_as_workout_session(c):
            continue
        d = (c.get("started_at") or "")[:10]
        if d and (since_iso is None or d >= since_iso):
            days.add(d)
    return days


def get_streak(user_id):
    """Return the current consecutive-day workout streak (0 if broken)."""
    from datetime import date, timedelta
    iso = sorted(workout_day_dates(user_id), reverse=True)
    if not iso:
        return 0
    dates = [date.fromisoformat(s) for s in iso]
    today = date.today()
    if dates[0] < today - timedelta(days=1):
        return 0
    streak = 1
    for i in range(1, len(dates)):
        if dates[i] == dates[i - 1] - timedelta(days=1):
            streak += 1
        else:
            break
    return streak


def get_week_streak(user_id, target_days):
    """Consecutive weeks (Monday start) hitting the days_per_week target.

    The current week counts if the target is already met, or is still
    achievable (workouts so far + days left >= target) — an in-flight week
    shouldn't break the streak before it's lost.
    """
    from datetime import date, timedelta
    target_days = max(1, int(target_days or 1))
    days = workout_day_dates(user_id)
    if not days:
        return 0
    by_week = {}
    for s in days:
        d = date.fromisoformat(s)
        monday = d - timedelta(days=d.weekday())
        by_week[monday] = by_week.get(monday, 0) + 1

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    days_left = 7 - today.weekday()  # includes today
    done_this_week = by_week.get(this_monday, 0)

    streak = 0
    week = this_monday
    if done_this_week >= target_days:
        streak = 1
        week = this_monday - timedelta(days=7)
    elif done_this_week + days_left >= target_days:
        # current week still winnable — skip it without breaking the chain
        week = this_monday - timedelta(days=7)
    else:
        return 0

    while by_week.get(week, 0) >= target_days:
        streak += 1
        week -= timedelta(days=7)
    return streak


def get_weekly_digest(user_id):
    """Return cached digest if it was generated for the current week, else None."""
    from datetime import date, timedelta
    today = date.today()
    days_since_monday = today.weekday()
    week_start = (today - timedelta(days=days_since_monday)).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT digest_text FROM apex_weekly_digest WHERE user_id = ? AND week_start = ?",
            (user_id, week_start),
        ).fetchone()
    return row["digest_text"] if row else None


def save_push_subscription(user_id, subscription):
    """Store a web-push subscription (one row per browser endpoint)."""
    endpoint = subscription.get("endpoint")
    if not endpoint:
        return False
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, user_id, subscription_json, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 user_id=excluded.user_id,
                 subscription_json=excluded.subscription_json""",
            (endpoint, user_id, json.dumps(subscription), datetime.now().isoformat()),
        )
    return True


def get_push_subscriptions(user_id=None):
    """All subscriptions, or just one user's."""
    with get_connection() as conn:
        if user_id is None:
            rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)
            ).fetchall()
    return [
        {"user_id": r["user_id"], "endpoint": r["endpoint"],
         "subscription": json.loads(r["subscription_json"])}
        for r in rows
    ]


def delete_push_subscription(endpoint):
    with get_connection() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def save_apns_token(user_id, device_token, environment="production"):
    """Store/refresh a native iOS APNs device token (one row per device)."""
    if not device_token:
        return False
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO apns_tokens (device_token, user_id, environment, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(device_token) DO UPDATE SET
                 user_id=excluded.user_id,
                 environment=excluded.environment,
                 updated_at=excluded.updated_at""",
            (device_token, user_id, environment, datetime.now().isoformat()),
        )
    return True


def get_apns_tokens(user_id=None):
    with get_connection() as conn:
        if user_id is None:
            rows = conn.execute("SELECT * FROM apns_tokens").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM apns_tokens WHERE user_id = ?", (user_id,)
            ).fetchall()
    return [{"user_id": r["user_id"], "device_token": r["device_token"],
             "environment": r["environment"]} for r in rows]


def delete_apns_token(device_token):
    with get_connection() as conn:
        conn.execute("DELETE FROM apns_tokens WHERE device_token = ?", (device_token,))


def get_push_user_ids():
    """Every user reachable on ANY push channel (web push OR native APNs).
    The cron loops use this so native-only users still get notifications."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id FROM push_subscriptions "
            "UNION SELECT user_id FROM apns_tokens"
        ).fetchall()
    return sorted({r["user_id"] for r in rows})


def nudge_already_sent(user_id, nudge_type, nudge_date):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM nudge_log WHERE user_id=? AND nudge_type=? AND nudge_date=?",
            (user_id, nudge_type, nudge_date),
        ).fetchone()
    return row is not None


def record_nudge(user_id, nudge_type, nudge_date, body=""):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO nudge_log (user_id, nudge_type, nudge_date, body, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, nudge_type, nudge_date) DO NOTHING""",
            (user_id, nudge_type, nudge_date, body, datetime.now().isoformat()),
        )


def save_weekly_digest(user_id, digest_text):
    from datetime import date, timedelta
    today = date.today()
    days_since_monday = today.weekday()
    week_start = (today - timedelta(days=days_since_monday)).isoformat()
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO apex_weekly_digest (user_id, digest_text, week_start, generated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 digest_text=excluded.digest_text,
                 week_start=excluded.week_start,
                 generated_at=excluded.generated_at""",
            (user_id, digest_text, week_start, now),
        )


def get_daily_brief(user_id):
    """Return today's cached daily brief, else None."""
    today = datetime.now().date().isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT brief_text FROM apex_daily_brief WHERE user_id = ? AND brief_date = ?",
            (user_id, today),
        ).fetchone()
    return row["brief_text"] if row else None


def save_daily_brief(user_id, brief_text):
    today = datetime.now().date().isoformat()
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO apex_daily_brief (user_id, brief_text, brief_date, generated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 brief_text=excluded.brief_text,
                 brief_date=excluded.brief_date,
                 generated_at=excluded.generated_at""",
            (user_id, brief_text, today, now),
        )


def get_apex_memory(user_id):
    """APEX's persistent memory about this user (markdown), or '' if none."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT content FROM apex_memory WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["content"] if row else ""


def save_apex_memory(user_id, content):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO apex_memory (user_id, content, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 content=excluded.content, updated_at=excluded.updated_at""",
            (user_id, content, now),
        )


def get_apex_memory_updated_at(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT updated_at FROM apex_memory WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["updated_at"] if row else None


def get_apex_chat_updated_at(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT updated_at FROM apex_chat WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["updated_at"] if row else None


def add_food_log(user_id, description, items, totals, cost_usd=0.0, on_date=None):
    now = datetime.now()
    meal_date = on_date or now.date().isoformat()
    # for back-dated imports, stamp created_at at noon of that day so ordering is sane
    created = now.isoformat() if not on_date else f"{on_date}T12:00:00"
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO food_log (user_id, meal_date, description, items_json,
                   calories, protein_g, carbs_g, fat_g, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, meal_date, description, json.dumps(items),
             int(totals.get("calories", 0)), totals.get("protein_g", 0),
             totals.get("carbs_g", 0), totals.get("fat_g", 0), cost_usd, created),
        )


def get_food_log_days(user_id, days=14):
    """Per-day nutrition totals (newest first) for the history view + APEX."""
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT meal_date,
                      SUM(calories) AS calories, SUM(protein_g) AS protein_g,
                      SUM(carbs_g) AS carbs_g, SUM(fat_g) AS fat_g, COUNT(*) AS n
               FROM food_log WHERE user_id = ? AND meal_date >= ?
               GROUP BY meal_date ORDER BY meal_date DESC""",
            (user_id, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


def get_food_log_today(user_id):
    today = datetime.now().date().isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, description, items_json, calories, protein_g, carbs_g, fat_g, created_at "
            "FROM food_log WHERE user_id = ? AND meal_date = ? ORDER BY created_at",
            (user_id, today),
        ).fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        try:
            r["items"] = json.loads(r.pop("items_json"))
        except Exception:
            r["items"] = []
    return out


def delete_food_log(user_id, log_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM food_log WHERE id = ? AND user_id = ?", (log_id, user_id))


def add_food_favorite(user_id, name, items, totals):
    """Save a meal as a one-tap favorite (stores macros so re-logging needs no parse)."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO food_favorite (user_id, name, items_json,
                   calories, protein_g, carbs_g, fat_g, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, (name or "Saved meal")[:80], json.dumps(items or []),
             int(totals.get("calories", 0)), totals.get("protein_g", 0),
             totals.get("carbs_g", 0), totals.get("fat_g", 0), datetime.now().isoformat()),
        )


def get_food_favorites(user_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, items_json, calories, protein_g, carbs_g, fat_g "
            "FROM food_favorite WHERE user_id = ? ORDER BY name", (user_id,),
        ).fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        try:
            r["items"] = json.loads(r.pop("items_json"))
        except Exception:
            r["items"] = []
    return out


def delete_food_favorite(user_id, fav_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM food_favorite WHERE id = ? AND user_id = ?", (fav_id, user_id))


def get_nutrition_goal(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT calories, protein_g, carbs_g, fat_g FROM nutrition_goal WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def save_nutrition_goal(user_id, calories, protein_g, carbs_g, fat_g):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO nutrition_goal (user_id, calories, protein_g, carbs_g, fat_g, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 calories=excluded.calories, protein_g=excluded.protein_g,
                 carbs_g=excluded.carbs_g, fat_g=excluded.fat_g, updated_at=excluded.updated_at""",
            (user_id, int(calories or 0), float(protein_g or 0), float(carbs_g or 0),
             float(fat_g or 0), now),
        )


def get_apex_plan(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT plan_json, created_at FROM apex_plan WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    return {"plan": json.loads(row["plan_json"]), "created_at": row["created_at"]}


def save_apex_plan(user_id, plan):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO apex_plan (user_id, plan_json, created_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET plan_json=excluded.plan_json, created_at=excluded.created_at""",
            (user_id, json.dumps(plan), now),
        )


def get_apex_chat(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT messages FROM apex_chat WHERE user_id = ?", (user_id,)
        ).fetchone()
    return json.loads(row["messages"]) if row else []


def save_apex_chat(user_id, messages):
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO apex_chat (user_id, messages, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET messages=excluded.messages, updated_at=excluded.updated_at""",
            (user_id, json.dumps(messages[-100:]), now),  # keep last 100 messages
        )


def save_user_photo(user_id, filename):
    with get_connection() as conn:
        conn.execute("UPDATE users SET photo = ? WHERE id = ?", (filename, user_id))


def clear_apex_chat(user_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM apex_chat WHERE user_id = ?", (user_id,))
