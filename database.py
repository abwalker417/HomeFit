"""SQLite persistence layer. Single-user self-hosted app, so no auth yet."""

import os
import sqlite3
import json
from pathlib import Path
from datetime import datetime

# Override with HOMEFIT_DB env var if you want to store the DB elsewhere
# (e.g., a volume mount or $HOME in a container).
DB_PATH = Path(os.environ.get(
    "HOMEFIT_DB",
    str(Path(__file__).parent / "data" / "workout.db"),
))


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            current_weight REAL NOT NULL,
            goal_weight REAL NOT NULL,
            fitness_level TEXT NOT NULL DEFAULT 'beginner',
            limitations TEXT NOT NULL DEFAULT '[]',
            days_per_week INTEGER NOT NULL DEFAULT 4,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS weight_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weight REAL NOT NULL,
            logged_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workout_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day_name TEXT NOT NULL,
            day_number INTEGER NOT NULL,
            exercises_json TEXT NOT NULL,
            duration_seconds INTEGER,
            completed_at TEXT NOT NULL
        );
        """)


def get_profile():
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        if not row:
            return None
        p = dict(row)
        p["limitations"] = json.loads(p["limitations"])
        return p


def save_profile(current_weight, goal_weight, fitness_level, limitations, days_per_week):
    now = datetime.utcnow().isoformat()
    lim_json = json.dumps(limitations)
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO profile (id, current_weight, goal_weight, fitness_level,
                                 limitations, days_per_week, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                current_weight=excluded.current_weight,
                goal_weight=excluded.goal_weight,
                fitness_level=excluded.fitness_level,
                limitations=excluded.limitations,
                days_per_week=excluded.days_per_week,
                updated_at=excluded.updated_at
        """, (current_weight, goal_weight, fitness_level, lim_json, days_per_week, now))
        # Also log current weight if it's new
        last = conn.execute(
            "SELECT weight FROM weight_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not last or last["weight"] != current_weight:
            conn.execute(
                "INSERT INTO weight_log (weight, logged_at) VALUES (?, ?)",
                (current_weight, now),
            )


def log_weight(weight):
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO weight_log (weight, logged_at) VALUES (?, ?)",
            (weight, now),
        )
        conn.execute(
            "UPDATE profile SET current_weight = ?, updated_at = ? WHERE id = 1",
            (weight, now),
        )


def get_weight_history(limit=60):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT weight, logged_at FROM weight_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def log_workout(day_name, day_number, exercises, duration_seconds):
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO workout_log (day_name, day_number, exercises_json,
                                     duration_seconds, completed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (day_name, day_number, json.dumps(exercises), duration_seconds, now))


def get_workout_history(limit=50):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM workout_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["exercises"] = json.loads(d["exercises_json"])
            del d["exercises_json"]
            result.append(d)
        return result


def get_stats():
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM workout_log").fetchone()["c"]
        last_7 = conn.execute("""
            SELECT COUNT(*) c FROM workout_log
            WHERE completed_at >= datetime('now', '-7 days')
        """).fetchone()["c"]
        total_minutes = conn.execute("""
            SELECT COALESCE(SUM(duration_seconds), 0) / 60 AS m FROM workout_log
        """).fetchone()["m"]
        return {
            "total_workouts": total,
            "last_7_days": last_7,
            "total_minutes": total_minutes,
        }
