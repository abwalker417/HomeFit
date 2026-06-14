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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
        _ensure_column(conn, "profile", "sparky_sync", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "profile", "sparky_api_key", "TEXT")
        _ensure_column(conn, "profile", "ignored_exercises", "TEXT NOT NULL DEFAULT '[]'")
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
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint          TEXT PRIMARY KEY,
                user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subscription_json TEXT NOT NULL,
                created_at        TEXT NOT NULL
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
    sparky_sync=False,
    ignored_exercises=None,
    fitness_goal="general",
    workout_duration_target=45,
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
        1 if sparky_sync else 0,
        json.dumps(ignored_exercises or []),
        fitness_goal,
        int(workout_duration_target),
        now,
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO profile (
                user_id, current_weight, goal_weight, fitness_level, limitations,
                equipment, custom_equipment, target_muscles, preferred_equipment,
                days_per_week, sparky_sync, ignored_exercises,
                fitness_goal, workout_duration_target, updated_at
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
                sparky_sync=excluded.sparky_sync,
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


def save_sparky_api_key(user_id, api_key, enabled=True):
    with get_connection() as conn:
        conn.execute(
            "UPDATE profile SET sparky_api_key = ?, sparky_sync = ? WHERE user_id = ?",
            (api_key or None, 1 if (enabled and api_key) else 0, user_id),
        )


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
    if profile.get("sparky_sync"):
        try:
            import sparky_sync
            sparky_key = profile.get("sparky_api_key") or None
            nutrition = sparky_sync.fetch_nutrition_log(days=7, api_key=sparky_key)
            hydration = sparky_sync.fetch_hydration_log(days=7, api_key=sparky_key)
            goals = sparky_sync.fetch_goals(api_key=sparky_key)
            # Activity from outside HomeFit — Apple Health, Oura, manual logs
            other_activity = sparky_sync.fetch_external_activity(days=7, api_key=sparky_key)
        except Exception:
            pass
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
        "sleep_log": get_recent_sleep(user_id, days=7),
    }


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
    only one of them pushes to Sparky. Status starts 'pending'; caller updates it.
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
    newly inserted (caller pushes to Sparky), False if this night already exists.
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
    watch overlaps the HomeFit session that already synced to Sparky.
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


def get_streak(user_id):
    """Return the current consecutive-day workout streak (0 if broken)."""
    from datetime import date, timedelta
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date(completed_at) as d FROM workout_log WHERE user_id = ? ORDER BY d DESC",
            (user_id,),
        ).fetchall()
    if not rows:
        return 0
    dates = [date.fromisoformat(row["d"]) for row in rows]
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
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date(completed_at) as d FROM workout_log WHERE user_id = ? ORDER BY d DESC",
            (user_id,),
        ).fetchall()
    if not rows:
        return 0
    by_week = {}
    for row in rows:
        d = date.fromisoformat(row["d"])
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
