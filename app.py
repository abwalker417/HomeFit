"""Flask entrypoint for HomeFit (multi-user v2)."""

import os
import random
from collections import defaultdict
from threading import Lock
from time import time

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

import database
from workout_logic import (
    VALID_EQUIPMENT,
    VALID_LIMITATIONS,
    VALID_MUSCLE_GROUPS,
    all_exercises_with_status,
    build_workout,
    get_exercise_by_id,
    pick_random_muscle_group,
)

app = Flask(__name__)
database.init_db()
app.secret_key = database.load_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HOMEFIT_SESSION_SECURE", "0") == "1",
)

PUBLIC_ENDPOINTS = {
    "profiles", "profile_new", "profile_switch", "profile_unlock",
    "profile_switch_out", "manifest", "service_worker", "static",
}

PIN_FAIL_WINDOW_SEC = 15 * 60
PIN_FAIL_THRESHOLD = 5
PIN_LOCKOUT_SEC = 10 * 60
_pin_fails = defaultdict(list)
_pin_fails_lock = Lock()


def _clean_list(values):
    seen = set()
    clean = []
    for value in values or []:
        item = (value or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
    return clean


def _parse_custom_equipment(raw):
    items = []
    seen = set()
    for piece in (raw or "").replace("\n", ",").split(","):
        item = piece.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def _parse_profile_form(form):
    custom_equipment = _parse_custom_equipment(form.get("custom_equipment", ""))
    return {
        "current_weight": float(form.get("current_weight", 0) or 0),
        "goal_weight": float(form.get("goal_weight", 0) or 0),
        "fitness_level": (form.get("fitness_level") or "beginner").lower(),
        "limitations": _clean_list(form.getlist("limitations")),
        "equipment": _clean_list(form.getlist("equipment")),
        "custom_equipment": custom_equipment,
        "target_muscles": [],
        "preferred_equipment": [],
        "days_per_week": int(form.get("days_per_week", 4) or 4),
    }


def owner_exists():
    users = database.list_users()
    return len(users) > 0


def can_manage_profiles():
    if not owner_exists():
        return True
    return session.get("is_owner") is True


def _prune_and_count(user_id, now):
    fails = [t for t in _pin_fails[user_id] if now - t < PIN_FAIL_WINDOW_SEC]
    _pin_fails[user_id] = fails
    return fails


def pin_lockout_remaining(user_id):
    now = time()
    with _pin_fails_lock:
        fails = _prune_and_count(user_id, now)
        if len(fails) >= PIN_FAIL_THRESHOLD:
            latest = max(fails)
            return max(0, int(PIN_LOCKOUT_SEC - (now - latest)))
        return 0


def record_pin_fail(user_id):
    with _pin_fails_lock:
        _pin_fails[user_id].append(time())


def clear_pin_fails(user_id):
    with _pin_fails_lock:
        _pin_fails.pop(user_id, None)


def _lockout_message(seconds):
    minutes = max(1, (seconds + 59) // 60)
    return f"Too many wrong PINs. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


_LABEL_TO_MUSCLES = {
    "upper body":    ["arms", "back", "chest", "shoulders"],
    "lower body":    ["legs", "glutes"],
    "core & cardio": ["core"],
    "recovery":      ["full body"],
}


def _dashboard_plan(profile):
    labels = ["Upper body", "Lower body", "Core & cardio", "Recovery"]
    days = []
    for index in range(max(1, int(profile.get("days_per_week", 4)))):
        label = labels[index % len(labels)]
        days.append({
            "day_number": index + 1,
            "name": label,
        })
    goal = "maintain"
    if profile.get("goal_weight", 0) < profile.get("current_weight", 0) - 5:
        goal = "lose"
    elif profile.get("goal_weight", 0) > profile.get("current_weight", 0) + 5:
        goal = "gain"
    return {
        "goal": goal,
        "summary": "Choose a focus each day and HomeFit will build a matching workout from your available equipment.",
        "days": days,
    }


def _progress_stats(user_id):
    stats = database.get_stats(user_id)
    stats.setdefault("total_workouts", 0)
    stats.setdefault("last_workout", None)
    stats.setdefault("weight_change", None)
    stats["last_7_days"] = stats.get("total_workouts", 0)
    history = database.get_workout_history(user_id)
    stats["total_minutes"] = sum((item.get("duration_seconds") or 0) // 60 for item in history)
    return stats


def _weight_chart_points(history):
    return [
        {"date": item["logged_at"][:10], "weight": item["weight"]}
        for item in history
    ]


@app.before_request
def require_profile():
    if request.endpoint in PUBLIC_ENDPOINTS or request.path.startswith("/static/"):
        return None
    if "user_id" not in session:
        return redirect(url_for("profiles"))
    return None


@app.context_processor
def inject_globals():
    uid = session.get("user_id")
    user = database.get_user(uid) if uid else None
    return {
        "current_user": user,
        "can_manage_profiles": can_manage_profiles(),
        "is_owner": session.get("is_owner") is True,
    }


@app.route("/profiles")
def profiles():
    users = database.list_users()
    return render_template(
        "profiles.html",
        users=users,
        can_create=can_manage_profiles(),
        owner_exists=owner_exists(),
    )


@app.route("/profiles/new", methods=["GET", "POST"])
def profile_new():
    if not can_manage_profiles():
        return render_template("blocked.html"), 403
    error = None
    if request.method == "POST":
        try:
            user_id = database.create_user(
                request.form.get("name", ""),
                request.form.get("emoji", "💪"),
                request.form.get("pin", "").strip() or None,
            )
            if not owner_exists() or len(database.list_users()) == 1:
                session["is_owner"] = True
            session["user_id"] = user_id
            return redirect(url_for("onboarding"))
        except Exception as exc:
            error = str(exc)
    return render_template("profile_new.html", error=error)


@app.route("/profiles/<int:user_id>/switch", methods=["POST"])
def profile_switch(user_id):
    user = database.get_user(user_id)
    if not user:
        abort(404)
    if user.get("has_pin"):
        return redirect(url_for("profile_unlock", user_id=user_id))
    session["user_id"] = user_id
    if user_id == 1:
        session["is_owner"] = True
    return redirect(url_for("index"))


@app.route("/profiles/<int:user_id>/unlock", methods=["GET", "POST"])
def profile_unlock(user_id):
    user = database.get_user(user_id)
    if not user:
        abort(404)
    error = None
    if request.method == "POST":
        remaining = pin_lockout_remaining(user_id)
        if remaining > 0:
            error = _lockout_message(remaining)
        elif database.verify_pin(user_id, request.form.get("pin", "")):
            clear_pin_fails(user_id)
            session["user_id"] = user_id
            if user_id == 1:
                session["is_owner"] = True
            return redirect(url_for("index"))
        else:
            record_pin_fail(user_id)
            remaining = pin_lockout_remaining(user_id)
            error = _lockout_message(remaining) if remaining > 0 else "Wrong PIN."
    return render_template("profile_unlock.html", user=user, error=error)


@app.route("/profiles/<int:user_id>/edit", methods=["GET", "POST"])
def profile_edit(user_id):
    current_id = session.get("user_id")
    if current_id != user_id and not can_manage_profiles():
        abort(403)
    user = database.get_user(user_id)
    if not user:
        abort(404)
    profile = database.get_profile(user_id)
    error = None
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action == "delete":
                database.delete_user(user_id)
                if session.get("user_id") == user_id:
                    session.pop("user_id", None)
                    session.pop("is_owner", None)
                return redirect(url_for("profiles"))

            pin_action = request.form.get("pin_action", "keep")
            new_pin = request.form.get("pin", "").strip() if pin_action == "set" else None
            database.update_user(
                user_id,
                request.form.get("name", user["name"]),
                request.form.get("emoji", user["emoji"]),
                new_pin,
            )
            if pin_action == "clear":
                database.clear_pin(user_id)

            payload = _parse_profile_form(request.form)
            database.save_profile(user_id=user_id, **payload)
            return redirect(url_for("index"))
        except Exception as exc:
            error = str(exc)
    return render_template(
        "profile_edit.html",
        user=user,
        profile=profile,
        error=error,
        valid_equipment=VALID_EQUIPMENT,
        valid_limitations=VALID_LIMITATIONS,
        valid_muscles=VALID_MUSCLE_GROUPS,
        all_users=database.list_users(),
    )


@app.route("/profiles/switch", methods=["POST", "GET"])
def profile_switch_out():
    session.pop("user_id", None)
    return redirect(url_for("profiles"))


@app.route("/")
def index():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))
    plan = _dashboard_plan(profile)
    stats = _progress_stats(uid)
    return render_template("dashboard.html", profile=profile, plan=plan, stats=stats)


@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    uid = session["user_id"]
    error = None
    profile = database.get_profile(uid)
    if request.method == "POST":
        try:
            payload = _parse_profile_form(request.form)
            database.save_profile(user_id=uid, **payload)
            return redirect(url_for("index"))
        except Exception as exc:
            error = str(exc)
    return render_template(
        "onboarding.html",
        error=error,
        profile=profile,
        valid_limitations=VALID_LIMITATIONS,
        valid_equipment=VALID_EQUIPMENT,
        valid_muscles=VALID_MUSCLE_GROUPS,
    )


@app.route("/start-workout", methods=["GET", "POST"])
def start_workout():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))

    if request.method == "POST":
        focus_mode = request.form.get("focus_mode", "pick")
        selected = _clean_list(request.form.getlist("focus"))
        if focus_mode == "surprise" or not selected:
            selected = [pick_random_muscle_group()]
        workout = build_workout(profile, "Today's Workout", selected, [])
        session["today_workout"] = workout
        return redirect(url_for("today_workout"))

    return render_template("start_workout.html", valid_muscles=VALID_MUSCLE_GROUPS)


@app.route("/build-day")
def build_day():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))
    label = request.args.get("label", "").strip()
    muscles = _LABEL_TO_MUSCLES.get(label.lower())
    if not muscles:
        return redirect(url_for("start_workout"))
    workout = build_workout(profile, label, muscles, [])
    session["today_workout"] = workout
    return redirect(url_for("today_workout"))


@app.route("/today-workout")
def today_workout():
    workout = session.get("today_workout")
    if not workout:
        return redirect(url_for("start_workout"))
    focus_list = workout.get("focus", [])
    focus_label = ", ".join(f.title() for f in focus_list) if focus_list else ""
    day = {
        "day_number": 1,
        "name": workout.get("label", "Today's Workout"),
        "focus": focus_label,
        "exercises": workout.get("exercises", []),
    }
    return render_template("workout.html", day=day, profile=database.get_profile(session["user_id"]))


@app.route("/today-workout/add", methods=["GET", "POST"])
def add_exercise():
    workout = session.get("today_workout")
    if not workout:
        return redirect(url_for("start_workout"))

    if request.method == "POST":
        ex = get_exercise_by_id(request.form.get("exercise_id", ""))
        if ex:
            workout["exercises"].append(ex)
            session["today_workout"] = workout
            session.modified = True
        return redirect(url_for("today_workout"))

    uid = session["user_id"]
    profile = database.get_profile(uid)
    all_ex = all_exercises_with_status(profile)
    added_ids = {ex.get("id") for ex in workout.get("exercises", [])}
    return render_template("add_exercise.html", exercises=all_ex, added_ids=added_ids)


@app.route("/workout/<int:day_number>")
def workout(day_number):
    return redirect(url_for("start_workout"))


@app.route("/api/complete_workout", methods=["POST"])
def complete_workout():
    uid = session["user_id"]
    data = request.get_json(force=True)
    database.log_workout(
        uid,
        data.get("day_name", "Workout"),
        int(data.get("day_number", 1)),
        data.get("exercises", []),
        data.get("duration_seconds"),
    )
    return jsonify({"ok": True})


@app.route("/api/log_weight", methods=["POST"])
def log_weight():
    uid = session["user_id"]
    data = request.get_json(force=True)
    database.log_weight(uid, float(data.get("weight", 0)))
    return jsonify({"ok": True})


@app.route("/exercises")
def exercises():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))
    items = all_exercises_with_status(profile)
    return render_template("exercises.html", exercises=items, profile=profile)


@app.route("/progress")
def progress():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    history = database.get_weight_history(uid)
    workouts = database.get_workout_history(uid)
    stats = _progress_stats(uid)
    weights = _weight_chart_points(history)
    return render_template("progress.html", profile=profile, weights=weights, history=workouts, stats=stats)


@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


@app.route("/sw.js")
def service_worker():
    response = app.send_static_file("js/sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


if __name__ == "__main__":
    host = os.environ.get("HOMEFIT_HOST", "0.0.0.0")
    port = int(os.environ.get("HOMEFIT_PORT", "5000"))
    debug = os.environ.get("HOMEFIT_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
