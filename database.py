"""SQLite persistence layer — multi-user v2 schema.

Schema is auto-migrated on app start: if an old v1 (single-user) schema is
detected, we drop it and recreate as v2. Users selected 'start fresh' during
the multi-user upgrade, so no data is preserved from v1.
"""

import json
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

SCHEMA_VERSION = 3

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
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
            "SELECT id, name, emoji, (pin_hash IS NOT NULL) AS has_pin FROM users ORDER BY id"
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
    now = datetime.utcnow().isoformat()
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
        for field in ("limitations", "equipment", "custom_equipment", "target_muscles", "preferred_equipment"):
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
):
    now = datetime.utcnow().isoformat()
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
        now,
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO profile (
                user_id, current_weight, goal_weight, fitness_level, limitations,
                equipment, custom_equipment, target_muscles, preferred_equipment,
                days_per_week, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "SELECT weight, logged_at FROM weight_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def log_workout(user_id, day_name, day_number, exercises, duration_seconds):
    now = datetime.utcnow().isoformat()
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
    }
