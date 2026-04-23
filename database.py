"""SQLite persistence layer — multi-user v2 schema.

Schema is auto-migrated on app start: if an old v1 (single-user) schema is
detected, we drop it and recreate as v2. Users selected 'start fresh' during
the multi-user upgrade, so no data is preserved from v1.
"""

import os
import sqlite3
import json
import secrets
from pathlib import Path
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash

SCHEMA_VERSION = 2

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


# ---------- schema / migration ---------------------------------------------

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
    user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    current_weight REAL    NOT NULL,
    goal_weight    REAL    NOT NULL,
    fitness_level  TEXT    NOT NULL DEFAULT 'beginner',
    limitations    TEXT    NOT NULL DEFAULT '[]',
    days_per_week  INTEGER NOT NULL DEFAULT 4,
    updated_at     TEXT    NOT NULL
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
    """Return the schema version stored in the DB, or 0 if unknown."""
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return row["version"] if row else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def _has_table(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def init_db():
    """Create tables if missing; drop-and-recreate if migrating from v1."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as conn:
        version = _current_version(conn)

        if version < 2:
            # v1 had a 'profile' table without a user_id column. Detect by
            # looking for the column's absence and wipe if present.
            if _has_table(conn, "profile"):
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(profile)")]
                if "user_id" not in cols:
                    for tbl in ("workout_log", "weight_log", "profile"):
                        conn.execute(f"DROP TABLE IF EXISTS {tbl}")

        conn.executescript(SCHEMA_SQL)

        # Stamp the schema version
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


def load_or_create_secret_key() -> bytes:
    """Persist a Flask session key across restarts."""
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


# ---------- users -----------------------------------------------------------

def list_users():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, emoji, (pin_hash IS NOT NULL) AS has_pin "
            "FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_user(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, emoji, (pin_hash IS NOT NULL) AS has_pin "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def create_user(name: str, emoji: str = "💪", pin: str | None = None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Name required")
    if len(name) > 40:
        raise ValueError("Name too long")
    pin_hash = generate_password_hash(pin) if pin else None
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (name, pin_hash, emoji, created_at) "
                "VALUES (?, ?, ?, ?)",
                (name, pin_hash, emoji or "💪", now),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError("A profile with that name already exists")


def update_user(user_id: int, name: str | None = None,
                emoji: str | None = None, pin: str | None | bool = False):
    """Update name/emoji/pin. Pass pin=None to clear it, pin=str to set,
    or leave pin=False (default) to not touch it."""
    sets, params = [], []
    if name is not None:
        sets.append("name = ?")
        params.append(name.strip())
    if emoji is not None:
        sets.append("emoji = ?")
        params.append(emoji)
    if pin is not False:
        if pin is None or pin == "":
            sets.append("pin_hash = NULL")
        else:
            sets.append("pin_hash = ?")
            params.append(generate_password_hash(pin))
    if not sets:
        return
    params.append(user_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)


def delete_user(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def verify_pin(user_id: int, pin: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT pin_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row or not row["pin_hash"]:
        return False
    return check_password_hash(row["pin_hash"], pin)


# ---------- profile (per-user) ---------------------------------------------

def get_profile(user_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        p = dict(row)
        p["limitations"] = json.loads(p["limitations"])
        return p


def save_profile(user_id, current_weight, goal_weight,
                 fitness_level, limitations, days_per_week):
    now = datetime.utcnow().isoformat()
    lim_json = json.dumps(limitations)
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO profile (user_id, current_weight, goal_weight, fitness_level,
                                 limitations, days_per_week, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                current_weight=excluded.current_weight,
                goal_weight=excluded.goal_weight,
                fitness_level=excluded.fitness_level,
                limitations=excluded.limitations,
                days_per_week=excluded.days_per_week,
                updated_at=excluded.updated_at
        """, (user_id, current_weight, goal_weight, fitness_level, lim_json,
              days_per_week, now))
        # Log current weight if it's new for this user
        last = conn.execute(
            "SELECT weight FROM weight_log WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not last or last["weight"] != current_weight:
            conn.execute(
                "INSERT INTO weight_log (user_id, weight, logged_at) VALUES (?, ?, ?)",
                (user_id, current_weight, now),
            )


# ---------- weight log -----------------------------------------------------

def log_weight(user_id, weight):
    now = datetime.utcnow().isoformat()
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
            "SELECT weight, logged_at FROM weight_log WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# ---------- workout log ----------------------------------------------------

def log_workout(user_id, day_name, day_number, exercises, duration_seconds):
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO workout_log (user_id, day_name, day_number, exercises_json,
                                     duration_seconds, completed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, day_name, day_number, json.dumps(exercises),
              duration_seconds, now))


def get_workout_history(user_id, limit=50):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM workout_log WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["exercises"] = json.loads(d["exercises_json"])
            del d["exercises_json"]
            result.append(d)
        return result


def get_stats(user_id):
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM workout_log WHERE user_id = ?",
            (user_id,),
        ).fetchone()["c"]
        last_7 = conn.execute("""
            SELECT COUNT(*) c FROM workout_log
            WHERE user_id = ? AND completed_at >= datetime('now', '-7 days')
        """, (user_id,)).fetchone()["c"]
        total_minutes = conn.execute("""
            SELECT COALESCE(SUM(duration_seconds), 0) / 60 AS m FROM workout_log
            WHERE user_id = ?
        """, (user_id,)).fetchone()["m"]
        return {
            "total_workouts": total,
            "last_7_days": last_7,
            "total_minutes": total_minutes,
        }
