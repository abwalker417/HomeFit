"""Flask entrypoint for HomeFit (multi-user v2)."""

import json
import os
import random
import subprocess
from collections import defaultdict
from threading import Lock
from time import time

import requests


def _git_version():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "0"


STATIC_VERSION = _git_version()

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

import coach
import database
import sparky_sync
from workout_logic import (
    VALID_EQUIPMENT,
    VALID_LIMITATIONS,
    VALID_MUSCLE_GROUPS,
    all_exercises_with_status,
    build_workout,
    get_exercise_by_id,
    get_progressive_overload_suggestions,
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


@app.after_request
def no_cache(response):
    if "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

PUBLIC_ENDPOINTS = {
    "profiles", "profile_new", "profile_switch", "profile_unlock",
    "profile_switch_out", "manifest", "service_worker", "static",
    "api_last_workout", "api_last_weight", "api_external_workout", "api_sleep",
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
        "sparky_sync": form.get("sparky_sync") == "1",
        "fitness_goal": form.get("fitness_goal", "general"),
        "workout_duration_target": int(form.get("workout_duration_target", 45) or 45),
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
    labels = ["Upper body", "Lower body", "Core & Cardio", "Recovery"]
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


def _calc_kcal(enriched_exercises, weight_lbs, duration_seconds):
    if not duration_seconds or not weight_lbs or not enriched_exercises:
        return 0
    weight_kg = weight_lbs / 2.20462
    hours = duration_seconds / 3600
    duration_per_ex = hours / len(enriched_exercises)

    total_kcal = 0.0
    for ex in enriched_exercises:
        if not ex:
            continue
        base_met = float(ex.get("met") or 5.0)
        logged_sets = ex.get("sets_logged") or []
        weighted_sets = [s for s in logged_sets if s.get("weight")]

        if weighted_sets:
            # Adjust MET based on average load relative to body weight
            avg_lifted_kg = sum(s["weight"] for s in weighted_sets) / len(weighted_sets) / 2.20462
            load_ratio = avg_lifted_kg / weight_kg
            if load_ratio < 0.3:
                met = max(base_met, 4.0)
            elif load_ratio < 0.6:
                met = max(base_met, 5.5)
            elif load_ratio < 1.0:
                met = max(base_met, 7.0)
            else:
                met = max(base_met, 8.5)
        else:
            met = base_met

        total_kcal += met * weight_kg * duration_per_ex

    return round(total_kcal)


def _progress_stats(user_id):
    from datetime import datetime, timedelta
    stats = database.get_stats(user_id)
    stats.setdefault("total_workouts", 0)
    stats.setdefault("last_workout", None)
    stats.setdefault("weight_change", None)
    history = database.get_workout_history(user_id)
    today = datetime.now().date()
    # weekday(): Mon=0 … Sun=6 — roll back to the most recent Sunday
    days_since_sunday = (today.weekday() + 1) % 7
    week_start = datetime.combine(today - timedelta(days=days_since_sunday), datetime.min.time()).isoformat()
    stats["last_7_days"] = sum(1 for item in history if (item.get("completed_at") or "") >= week_start)
    stats["total_minutes"] = sum((item.get("duration_seconds") or 0) // 60 for item in history)
    stats["streak"] = database.get_streak(user_id)
    profile = database.get_profile(user_id) or {}
    stats["target_days"] = profile.get("days_per_week") or 4
    stats["week_streak"] = database.get_week_streak(user_id, stats["target_days"])
    # Weekly-target tracking (Monday start, matching get_week_streak)
    monday = today - timedelta(days=today.weekday())
    week_dates = {(item.get("completed_at") or "")[:10] for item in history
                  if (item.get("completed_at") or "")[:10] >= monday.isoformat()}
    week_dates.discard("")
    trained_today = today.isoformat() in week_dates
    days_left = 7 - today.weekday()  # includes today
    done = len(week_dates)
    stats["week_workouts"] = done
    # Today is the last chance to keep the weekly target reachable
    stats["must_train_today"] = (
        not trained_today
        and done < stats["target_days"]
        and done + days_left >= stats["target_days"]
        and done + days_left - 1 < stats["target_days"]
    )
    if stats["last_workout"]:
        try:
            last_dt = datetime.fromisoformat(stats["last_workout"])
            stats["days_since_workout"] = (datetime.now() - last_dt).days
        except Exception:
            stats["days_since_workout"] = None
    else:
        stats["days_since_workout"] = None
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


# Per-profile accent palette: label -> (hex, "r, g, b"). The hex is stored on
# the profile; both values are injected as CSS vars so the whole app re-tints.
ACCENT_PALETTE = {
    "orange": ("#f97316", "249, 115, 22"),
    "ice":    ("#22d3ee", "34, 211, 238"),
    "blue":   ("#3b82f6", "59, 130, 246"),
    "green":  ("#22c55e", "34, 197, 94"),
    "violet": ("#a855f7", "168, 85, 247"),
    "red":    ("#ef4444", "239, 68, 68"),
    "pink":   ("#ec4899", "236, 72, 153"),
}
DEFAULT_ACCENT = "#22d3ee"  # ice — brand default for the redesign


def _accent_rgb(hex_color):
    h = (hex_color or DEFAULT_ACCENT).lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return f"{r}, {g}, {b}"
    except (ValueError, IndexError):
        return "249, 115, 22"


@app.context_processor
def inject_globals():
    uid = session.get("user_id")
    user = database.get_user(uid) if uid else None
    accent = (database.get_accent_color(uid) if uid else None) or DEFAULT_ACCENT
    accent_name = next((n for n, (hex_, _) in ACCENT_PALETTE.items() if hex_ == accent), "ice")
    return {
        "current_user": user,
        "can_manage_profiles": can_manage_profiles(),
        "is_owner": session.get("is_owner") is True,
        "static_version": STATIC_VERSION,
        "accent": accent,
        "accent_rgb": _accent_rgb(accent),
        "accent_name": accent_name,
        "accent_palette": ACCENT_PALETTE,
        # Handed to the native iOS app (via the homefitNative JS bridge) so it can
        # read Apple Health workouts and POST them to /api/external-workout.
        "api_token": database.get_or_create_api_token(uid) if uid else None,
    }


@app.route("/api/accent", methods=["POST"])
def set_accent():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    color = (request.get_json(silent=True) or {}).get("color", "")
    valid = {hex_ for hex_, _ in ACCENT_PALETTE.values()}
    if color not in valid:
        return jsonify({"error": "invalid color"}), 400
    database.set_accent_color(uid, color)
    return jsonify({"ok": True, "color": color, "accent_rgb": _accent_rgb(color)})


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
    # Profile creation disabled for now — re-enable by removing this redirect.
    return redirect(url_for("profiles"))
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
                user.get("emoji", ""),
                new_pin,
            )
            if pin_action == "clear":
                database.clear_pin(user_id)

            # Handle photo upload
            photo_file = request.files.get("photo")
            if photo_file and photo_file.filename:
                import imghdr
                upload_dir = os.path.join(os.path.dirname(__file__), "static", "uploads")
                os.makedirs(upload_dir, exist_ok=True)
                ext = os.path.splitext(photo_file.filename)[1].lower() or ".jpg"
                filename = f"profile_{user_id}{ext}"
                photo_file.save(os.path.join(upload_dir, filename))
                database.save_user_photo(user_id, filename)

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
        sparky_configured=bool(sparky_sync.load_config().get("url")),
        api_token=database.get_or_create_api_token(user_id) if user_id == session.get("user_id") else None,
    )


@app.route("/profiles/switch", methods=["POST", "GET"])
def profile_switch_out():
    session.pop("user_id", None)
    return redirect(url_for("profiles"))


@app.route("/cancel-workout", methods=["POST"])
def cancel_workout():
    session.pop("today_workout", None)
    return redirect(url_for("index"))


def _cardio_display(uid, days=14):
    """Recent cardio with local-time display fields for templates."""
    from datetime import datetime, timezone
    items = database.get_external_workouts(uid, days=days)
    for c in items:
        try:
            dt = datetime.fromisoformat(c["started_at"]).replace(tzinfo=timezone.utc).astimezone()
            c["when"] = dt.strftime("%b %-d")
            c["time"] = dt.strftime("%-I:%M %p")
        except (ValueError, TypeError):
            c["when"] = ""
            c["time"] = ""
        bits = []
        if c.get("distance_mi"):
            bits.append(f"{c['distance_mi']} mi")
        if c.get("kcal"):
            bits.append(f"{c['kcal']} kcal")
        if c.get("avg_hr"):
            bits.append(f"{c['avg_hr']} bpm")
        c["detail"] = " · ".join(bits)
    return items


def _sleep_display(uid, days=14):
    """Recent sleep with formatted hours and local bed/wake times."""
    from datetime import datetime
    items = database.get_recent_sleep(uid, days=days)
    for s in items:
        secs = s.get("duration_seconds") or 0
        h, m = divmod(secs // 60, 60)
        s["hm"] = f"{h}h {m}m"
        s["hours"] = round(secs / 3600, 1)
        for k in ("bedtime", "wake_time"):
            try:
                dt = datetime.fromisoformat(str(s.get(k)))
                if dt.tzinfo:
                    dt = dt.astimezone()
                s[k + "_fmt"] = dt.strftime("%-I:%M %p")
            except (ValueError, TypeError):
                s[k + "_fmt"] = ""
        stage_bits = []
        for label, key in (("Deep", "deep_seconds"), ("REM", "rem_seconds")):
            if s.get(key):
                sm = s[key] // 60
                stage_bits.append(f"{label} {sm // 60}h {sm % 60}m" if sm >= 60 else f"{label} {sm}m")
        s["stages"] = " · ".join(stage_bits)
    return items


@app.route("/")
def index():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))
    plan = _dashboard_plan(profile)
    stats = _progress_stats(uid)
    # Check for a workout completed today
    from datetime import date
    last = database.get_last_workout(uid)
    if last and last.get("completed_at", "")[:10] == date.today().isoformat():
        dur = last.get("duration_seconds") or 0
        exs = json.loads(last.get("exercises_json") or "[]")
        weight_kg = (profile.get("current_weight") or 0) * 0.453592
        kcal = round(5.0 * weight_kg * (dur / 3600))
        stats["today_workout"] = {
            "name": last.get("day_name", "Workout"),
            "duration_min": dur // 60,
            "kcal": kcal,
        }
    else:
        stats["today_workout"] = None
    start_w = stats.get("starting_weight") or profile.get("current_weight")
    current_w = profile.get("current_weight")
    goal_w = profile.get("goal_weight")
    if start_w and goal_w and start_w != goal_w:
        span = abs(goal_w - start_w)
        done = (start_w - current_w) if goal_w < start_w else (current_w - start_w)
        stats["weight_progress_pct"] = min(100, max(0, round(done / span * 100)))
    else:
        stats["weight_progress_pct"] = 0
    sleep = _sleep_display(uid, days=3)
    return render_template("dashboard.html", profile=profile, plan=plan, stats=stats,
                           cardio=_cardio_display(uid, days=14)[:3],
                           last_sleep=sleep[0] if sleep else None,
                           has_active_workout=bool(session.get("today_workout")),
                           ai_online=coach.is_available())


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
        sparky_configured=bool(sparky_sync.load_config().get("url")),
    )


def _ai_build_workout(uid, profile, focus=None):
    """Try to build a workout with AI, return None if unavailable."""
    if not coach.is_available():
        return None
    try:
        from workout_logic import load_exercises
        coaching_data = database.get_coaching_context(uid)
        exercise_library = [
            {"id": e["id"], "name": e["name"], "muscle_group": e.get("muscle_group", ""),
             "equipment": e.get("equipment", "bodyweight"),
             "default_sets": e.get("default_sets", 3), "default_reps": e.get("default_reps", 10)}
            for e in load_exercises()
        ]
        ai_plan = coach.generate_workout(coaching_data, exercise_library, focus=focus)
        # Enrich AI-chosen exercises with full data from library
        exercises = []
        for item in ai_plan.get("exercises", []):
            ex = get_exercise_by_id(item["id"])
            if not ex:
                continue
            ex["sets"] = item.get("sets", ex["sets"])
            ex["reps"] = item.get("reps", ex["reps"])
            exercises.append(ex)
        if not exercises:
            return None
        return {
            "label": ai_plan.get("name", "Today's Workout"),
            "focus": ai_plan.get("focus", ""),
            "ai_generated": True,
            "exercises": exercises,
        }
    except Exception:
        return None


@app.route("/start-workout", methods=["GET", "POST"])
def start_workout():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))

    if request.method == "POST":
        focus_mode = request.form.get("focus_mode", "ai")

        if focus_mode == "ai" and coach.is_available():
            session.pop("today_workout", None)
            session["building_workout"] = {"label": "Today's Workout", "muscles": []}
            return render_template("workout_loading.html", focus="your personalised workout")

        selected = _clean_list(request.form.getlist("focus"))
        if not selected:
            selected = [pick_random_muscle_group()]
        workout = build_workout(profile, "Today's Workout", selected, [])
        session["today_workout"] = workout
        return redirect(url_for("today_workout"))

    ai_online = coach.is_available()
    return render_template("start_workout.html", valid_muscles=VALID_MUSCLE_GROUPS, ai_online=ai_online)


@app.route("/api/regenerate-workout", methods=["POST"])
def regenerate_workout():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    workout = _ai_build_workout(uid, profile)
    if not workout:
        return jsonify({"error": "Coach unavailable"}), 503
    session["today_workout"] = workout
    return jsonify({"ok": True})


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

    if coach.is_available():
        # Clear any existing workout and show loading screen while AI generates
        session.pop("today_workout", None)
        session["building_workout"] = {"label": label, "muscles": muscles}
        return render_template("workout_loading.html", focus=label)

    workout = build_workout(profile, label, muscles, [])
    session["today_workout"] = workout
    return redirect(url_for("today_workout"))


@app.route("/api/workout-ready")
def workout_ready():
    if session.get("today_workout"):
        return jsonify({"ready": True})
    building = session.get("building_workout")
    if not building:
        return jsonify({"ready": False})
    uid = session["user_id"]
    profile = database.get_profile(uid)
    label = building["label"]
    muscles = building["muscles"]
    workout = _ai_build_workout(uid, profile, focus=label)
    if not workout:
        workout = build_workout(profile, label, muscles, [])
    session["today_workout"] = workout
    session.pop("building_workout", None)
    return jsonify({"ready": True})


@app.route("/today-workout")
def today_workout():
    workout = session.get("today_workout")
    if not workout:
        return redirect(url_for("start_workout"))
    focus_raw = workout.get("focus", [])
    if isinstance(focus_raw, list):
        focus_label = ", ".join(f.title() for f in focus_raw) if focus_raw else ""
    else:
        focus_label = str(focus_raw)
    uid = session["user_id"]

    # Build per-exercise weight hints
    ex_history = database.get_exercise_history(uid, limit=15)
    overload_suggestions = {
        s["exercise_id"]: s
        for s in get_progressive_overload_suggestions(ex_history)
    }

    def weight_hint(ex_id):
        sessions = ex_history.get(ex_id, [])
        for s in sessions:
            weights = [w["weight"] for w in s.get("sets", []) if w.get("weight")]
            if weights:
                last_weight = max(weights)
                last_reps = max((w.get("reps") or 0 for w in s.get("sets", []) if w.get("weight")), default=0)
                if ex_id in overload_suggestions:
                    sug = overload_suggestions[ex_id]
                    return {
                        "last_weight": last_weight,
                        "last_reps": last_reps,
                        "suggested_weight": sug["suggested_weight"],
                        "ready": True,
                    }
                return {"last_weight": last_weight, "last_reps": last_reps, "suggested_weight": last_weight, "ready": False}
        return None

    images = _load_exercise_images()
    exercises = workout.get("exercises", [])
    for ex in exercises:
        ex["weight_hint"] = weight_hint(ex.get("id", ""))
        ex["demo_image"] = images.get(ex.get("name", ""))

    day = {
        "day_number": 1,
        "name": workout.get("label", "Today's Workout"),
        "focus": focus_label,
        "ai_generated": workout.get("ai_generated", False),
        "exercises": exercises,
    }
    return render_template("workout.html", day=day, profile=database.get_profile(uid), user_id=uid)


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
    exercises = data.get("exercises", [])
    duration = data.get("duration_seconds")
    day_name = data.get("day_name", "Workout")

    from datetime import date, datetime
    # Idempotency: a failed-then-retried Finish (or a double tap) would otherwise
    # log the same session twice. Skip if an identical workout was just saved.
    last = database.get_last_workout(uid)
    if last and last.get("day_name") == day_name:
        try:
            age = (datetime.now() - datetime.fromisoformat(last["completed_at"])).total_seconds()
        except (ValueError, TypeError, KeyError):
            age = 999
        if 0 <= age < 120:
            profile = database.get_profile(uid)
            completed_n = len([e for e in last.get("exercises", []) if e.get("completed")])
            enriched = [get_exercise_by_id(e["id"]) for e in last.get("exercises", [])
                        if e.get("id") and e.get("completed")]
            kcal = _calc_kcal([e for e in enriched if e],
                              (profile or {}).get("current_weight") or 0,
                              last.get("duration_seconds"))
            return jsonify({"ok": True, "kcal": kcal,
                            "exercises_completed": completed_n, "duplicate": True})

    database.log_workout(
        uid,
        day_name,
        int(data.get("day_number", 1)),
        exercises,
        duration,
    )
    completed = [e for e in exercises if e.get("completed") and e.get("id")]
    sets_by_id = {e["id"]: e.get("sets", []) for e in completed}
    enriched = [get_exercise_by_id(e["id"]) for e in completed]
    enriched = [e for e in enriched if e]
    for e in enriched:
        e["sets_logged"] = sets_by_id.get(e["id"], [])
    profile = database.get_profile(uid)
    if enriched and (profile or {}).get("sparky_sync"):
        sparky_sync.sync_workout_async(enriched, date.today(), duration, api_key=(profile or {}).get("sparky_api_key"))
    kcal = _calc_kcal(enriched, (profile or {}).get("current_weight") or 0, duration)
    session.pop("today_workout", None)

    return jsonify({"ok": True, "kcal": kcal, "exercises_completed": len(enriched)})


@app.route("/api/post-workout-insight", methods=["POST"])
def post_workout_insight():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    if not coach.is_available():
        return jsonify({"insight": None, "overload": []})

    data = request.get_json(force=True)
    exercises = data.get("exercises", [])

    # Build a name→sets summary of what was just done
    completed = [e for e in exercises if e.get("completed") and e.get("id")]
    sets_by_id = {e["id"]: e.get("sets", []) for e in completed}
    enriched = [get_exercise_by_id(e["id"]) for e in completed]
    enriched = [e for e in enriched if e]
    for e in enriched:
        e["sets_logged"] = sets_by_id.get(e["id"], [])

    try:
        coaching_data = database.get_coaching_context(uid)
        ex_history = coaching_data.get("exercise_history", {})
        overload = get_progressive_overload_suggestions(ex_history)
        insight = coach.generate_post_workout_insight(coaching_data, overload, enriched)
        return jsonify({"insight": insight, "overload": overload})
    except Exception:
        return jsonify({"insight": None, "overload": []})


@app.route("/api/weekly-digest", methods=["GET"])
def weekly_digest():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import digest_service
        digest = digest_service.get_or_generate(uid)
        return jsonify({"digest": digest})
    except Exception:
        return jsonify({"digest": None})


_exercise_images = None

def _load_exercise_images():
    global _exercise_images
    if _exercise_images is None:
        path = os.path.join(os.path.dirname(__file__), "data", "exercise_images.json")
        try:
            with open(path) as f:
                _exercise_images = json.load(f)
        except Exception:
            _exercise_images = {}
    return _exercise_images


@app.route("/apex-plan")
def apex_plan_page():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("profiles"))
    from datetime import date
    plan_data = database.get_apex_plan(uid)
    today_index = date.today().weekday()  # 0=Monday
    return render_template(
        "apex_plan.html",
        plan=plan_data["plan"] if plan_data else None,
        created_at=plan_data["created_at"] if plan_data else "",
        today_index=today_index,
    )


@app.route("/api/apex-plan", methods=["GET"])
def get_apex_plan():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    plan_data = database.get_apex_plan(uid)
    return jsonify(plan_data or {})


@app.route("/api/apex-plan/generate", methods=["POST"])
def generate_apex_plan():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    if not coach.is_available():
        return jsonify({"error": "APEX offline"}), 503
    try:
        from workout_logic import load_exercises
        coaching_data = database.get_coaching_context(uid)
        exercise_library = [
            {"id": e["id"], "name": e["name"], "muscle_group": e.get("muscle_group", ""),
             "equipment": e.get("equipment", "bodyweight")}
            for e in load_exercises()
        ]
        result = coach.generate_weekly_plan(coaching_data, exercise_library)
        # Enrich exercises with full data
        for day in result.get("plan", []):
            enriched = []
            for item in day.get("exercises", []):
                ex = get_exercise_by_id(item["id"])
                if ex:
                    ex["sets"] = item.get("sets", ex["sets"])
                    ex["reps"] = item.get("reps", ex["reps"])
                    enriched.append(ex)
            day["exercises"] = enriched
        database.save_apex_plan(uid, result["plan"])
        return jsonify({"ok": True, "plan": result["plan"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/apex-plan/today", methods=["POST"])
def load_plan_today():
    """Load today's planned workout into the session."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    plan_data = database.get_apex_plan(uid)
    if not plan_data:
        return jsonify({"error": "no plan"}), 404
    # Use client-supplied weekday (0=Mon) to avoid UTC offset issues
    body = request.get_json(silent=True) or {}
    client_weekday = body.get("weekday")
    if client_weekday is not None:
        day_of_week = int(client_weekday)
    else:
        from datetime import date
        day_of_week = date.today().weekday()
    plan = plan_data["plan"]
    day_index = day_of_week % len(plan)
    day = plan[day_index]
    if day.get("rest"):
        return jsonify({"rest": True, "name": day.get("name", "Rest Day")})
    if not day.get("exercises"):
        return jsonify({"error": "no exercises for today"}), 404
    session["today_workout"] = {
        "label": day.get("name", "Today's Workout"),
        "focus": day.get("focus", ""),
        "ai_generated": True,
        "exercises": day["exercises"],
    }
    return jsonify({"ok": True})


@app.route("/api/apex-chat", methods=["GET"])
def get_apex_chat():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"messages": []})
    return jsonify({"messages": database.get_apex_chat(uid)})


@app.route("/api/apex-chat/clear", methods=["POST"])
def clear_apex_chat():
    uid = session.get("user_id")
    if uid:
        database.clear_apex_chat(uid)
    return jsonify({"ok": True})


@app.route("/api/exercise-cue")
def exercise_cue():
    if not session.get("user_id"):
        return jsonify({"error": "unauthorized"}), 401
    ex_name = request.args.get("name", "")
    ex_id = request.args.get("id", "")
    if not ex_name or not coach.is_available():
        return jsonify({"cue": None})
    uid = session["user_id"]
    try:
        profile = database.get_profile(uid)
        limitations = ", ".join(profile.get("limitations") or []) or "none"
        fitness_level = profile.get("fitness_level", "beginner")
        prompt = f"""Give 2-3 short, practical form cues for {ex_name} for a {fitness_level} with limitations: {limitations}.
Focus on the most important things to watch. Be direct — no intro, just the cues. Use bullet points."""
        cue = coach._generate(prompt, timeout=30)
        return jsonify({"cue": cue or None})
    except Exception:
        return jsonify({"cue": None})


@app.route("/settings/sparky", methods=["GET", "POST"])
def sparky_settings():
    uid = session["user_id"]
    message = None
    ok = False
    config = sparky_sync.load_config()
    profile = database.get_profile(uid) or {}

    if request.method == "POST":
        action = request.form.get("action", "save")
        url = request.form.get("url", "").strip()
        api_key = request.form.get("api_key", "").strip()

        if action == "clear":
            database.save_sparky_api_key(uid, "", enabled=False)
            message = "SparkyFitness sync disconnected for your profile."
            ok = True
        elif action == "test":
            ok, message = sparky_sync.test_connection(url, api_key)
        elif action == "push_exercises":
            user_key = api_key or profile.get("sparky_api_key") or ""
            ok, message = sparky_sync.push_exercises_to_sparky(api_key=user_key)
        elif action == "refresh_exercises":
            user_key = api_key or profile.get("sparky_api_key") or ""
            ok, message = sparky_sync.fetch_and_replace_exercises(api_key=user_key)
        else:
            sparky_sync.save_config(url)
            if api_key:
                database.save_sparky_api_key(uid, api_key, enabled=True)
            config = sparky_sync.load_config()
            test_key = api_key or profile.get("sparky_api_key") or ""
            ok, message = sparky_sync.test_connection(url, test_key)
            if ok:
                message = "Settings saved and connection verified."
        profile = database.get_profile(uid) or {}

    return render_template("sparky_settings.html", config=config, message=message, ok=ok, profile=profile)


@app.route("/api/log_weight", methods=["POST"])
def log_weight():
    uid = session["user_id"]
    data = request.get_json(force=True)
    weight = float(data.get("weight", 0))
    database.log_weight(uid, weight)
    profile = database.get_profile(uid)
    synced = None
    if (profile or {}).get("sparky_sync"):
        # Synchronous so we can tell the user if it didn't reach Sparky, instead
        # of silently showing "Logged" while the sync failed in the background.
        synced = sparky_sync.sync_weight(weight, api_key=(profile or {}).get("sparky_api_key"))
    return jsonify({"ok": True, "sparky_synced": synced})


@app.route("/exercises")
def exercises():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))
    items = all_exercises_with_status(profile)
    return render_template("exercises.html", exercises=items, profile=profile, user_id=uid)


@app.route("/api/toggle_ignore/<exercise_id>", methods=["POST"])
def toggle_ignore(exercise_id):
    uid = session["user_id"]
    now_ignored = database.toggle_ignored_exercise(uid, exercise_id)
    return jsonify({"ok": True, "ignored": now_ignored})


@app.route("/api/last_workout")
def api_last_workout():
    from datetime import datetime, timedelta
    token = request.args.get("token", "")
    uid = database.get_user_id_by_token(token)
    if not uid:
        return jsonify({"error": "invalid token"}), 401
    workout = database.get_last_workout(uid)
    if not workout:
        return jsonify({"error": "no workouts found"}), 404
    profile = database.get_profile(uid)
    duration_s = workout.get("duration_seconds") or 0
    completed_at = datetime.fromisoformat(workout["completed_at"])
    start_time = completed_at - timedelta(seconds=duration_s)
    exercises = workout.get("exercises", [])
    enriched = [get_exercise_by_id(e["id"]) for e in exercises if e.get("id") and e.get("completed")]
    enriched = [e for e in enriched if e]
    kcal = _calc_kcal(enriched, (profile or {}).get("current_weight") or 0, duration_s)
    return jsonify({
        "name": workout.get("day_name", "Workout"),
        "workout_type": "Traditional Strength Training",
        "start_time": start_time.astimezone().isoformat(),
        "end_time": completed_at.astimezone().isoformat(),
        "duration_minutes": round(duration_s / 60),
        "kcal": kcal,
    })


@app.route("/api/last_weight")
def api_last_weight():
    token = request.args.get("token", "")
    uid = database.get_user_id_by_token(token)
    if not uid:
        return jsonify({"error": "invalid token"}), 401
    history = database.get_weight_history(uid, limit=1)
    if not history:
        return jsonify({"error": "no weight logged"}), 404
    entry = history[0]
    return jsonify({
        "weight_lbs": entry["weight"],
        "logged_at": entry["logged_at"],
    })


@app.route("/api/external-workout", methods=["POST"])
def api_external_workout():
    """Relay for Apple Health workouts (posted by an iOS Shortcut).

    Dedups against HomeFit's own sessions (which already sync to Sparky with
    full details), then forwards genuinely external cardio to Sparky.
    Body: {type, start, [end], [duration_minutes], [kcal], [distance_mi], [avg_hr], [source]}
    """
    from datetime import datetime, timedelta, timezone
    token = request.args.get("token", "") or \
        request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
    uid = database.get_user_id_by_token(token)
    if not uid:
        return jsonify({"error": "invalid token"}), 401

    data = request.get_json(silent=True) or {}
    name = str(data.get("type") or data.get("name") or "").strip()
    raw_start = str(data.get("start") or "").strip()
    if not name or not raw_start:
        return jsonify({"error": "required fields: type, start (ISO 8601)"}), 400

    def _parse(ts):
        # Returns (naive-UTC datetime, date-as-written-locally)
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        local_date = dt.date()
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt, local_date

    try:
        start, local_date = _parse(raw_start)
    except ValueError:
        return jsonify({"error": f"unparseable start: {raw_start}"}), 400
    end = None
    if data.get("end"):
        try:
            end, _ = _parse(str(data["end"]))
        except ValueError:
            return jsonify({"error": f"unparseable end: {data['end']}"}), 400

    def _num(key, cast):
        try:
            return cast(float(data.get(key)))
        except (TypeError, ValueError):
            return None

    duration_minutes = _num("duration_minutes", int) or 0
    if not duration_minutes and end:
        duration_minutes = max(1, int((end - start).total_seconds() // 60))
    if end is None:
        end = start + timedelta(minutes=duration_minutes or 1)
    kcal = _num("kcal", int)
    distance_mi = _num("distance_mi", float)
    avg_hr = _num("avg_hr", int)
    source = str(data.get("source") or "apple_health").strip()

    started_iso = start.isoformat()
    # Claim the dedup key FIRST so concurrent posts can't both reach Sparky.
    claimed = database.claim_external_workout(
        uid, source, name, started_iso, end.isoformat(),
        duration_minutes, kcal, distance_mi, avg_hr)
    if not claimed:
        return jsonify({"status": "duplicate", "detail": "this workout was already received"}), 200

    overlap = database.find_overlapping_homefit_workout(uid, start, end)
    if overlap:
        database.set_external_workout_status(uid, name, started_iso, "skipped_overlap")
        return jsonify({
            "status": "skipped",
            "reason": f"overlaps HomeFit workout '{overlap['day_name']}' (already synced to Sparky)",
        }), 200

    profile = database.get_profile(uid) or {}
    synced = False
    if profile.get("sparky_sync"):
        synced = sparky_sync.push_external_workout(
            name, local_date.isoformat(), duration_minutes,
            kcal=kcal, distance_mi=distance_mi, avg_hr=avg_hr,
            api_key=profile.get("sparky_api_key"))
    status = "synced" if synced else "recorded"
    database.set_external_workout_status(uid, name, started_iso, status)
    return jsonify({"status": status, "workout": name, "date": local_date.isoformat()}), 201


@app.route("/api/sleep", methods=["POST"])
def api_sleep():
    """Relay for Apple Health / Oura sleep (posted by the native app).
    Body: {date, bedtime, wake_time, duration_seconds, [deep_s,rem_s,light_s,awake_s], [source]}
    Dedups one night per date (UNIQUE), then forwards to Sparky.
    """
    from datetime import datetime
    token = request.args.get("token", "") or \
        request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
    uid = database.get_user_id_by_token(token)
    if not uid:
        return jsonify({"error": "invalid token"}), 401

    data = request.get_json(silent=True) or {}
    bedtime = str(data.get("bedtime") or "").strip()
    wake = str(data.get("wake_time") or "").strip()
    entry_date = str(data.get("date") or "").strip()
    try:
        duration_seconds = int(float(data.get("duration_seconds")))
    except (TypeError, ValueError):
        duration_seconds = 0
    if not bedtime or not wake or duration_seconds <= 0:
        return jsonify({"error": "required: bedtime, wake_time, duration_seconds"}), 400
    if not entry_date:
        # Derive the night's date from local wake time
        try:
            entry_date = datetime.fromisoformat(wake.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return jsonify({"error": "unparseable wake_time"}), 400

    def _num(k):
        try:
            return int(float(data.get(k)))
        except (TypeError, ValueError):
            return None

    source = str(data.get("source") or "apple_health").strip()
    claimed = database.claim_sleep(
        uid, source, entry_date, bedtime, wake, duration_seconds,
        _num("deep_s"), _num("rem_s"), _num("light_s"), _num("awake_s"))
    if not claimed:
        return jsonify({"status": "duplicate", "date": entry_date}), 200

    profile = database.get_profile(uid) or {}
    synced = False
    if profile.get("sparky_sync"):
        synced = sparky_sync.push_sleep(
            entry_date, bedtime, wake, duration_seconds,
            api_key=profile.get("sparky_api_key"))
    status = "synced" if synced else "recorded"
    database.set_sleep_status(uid, entry_date, status)
    hrs = round(duration_seconds / 3600, 1)
    return jsonify({"status": status, "date": entry_date, "hours": hrs}), 201


@app.route("/coach")
@app.route("/apex")
def coach_page():
    # Legacy full-page chat removed — the APEX panel on the dashboard replaces it.
    return redirect(url_for("index", apex=1))


@app.route("/api/coach", methods=["POST"])
def coach_chat():
    uid = session["user_id"]
    data = request.get_json(force=True)
    message = data.get("message", "").strip()
    history = data.get("history", [])
    local_date = data.get("local_date")
    local_day = data.get("local_day")
    if not message:
        return jsonify({"error": "empty message"}), 400
    if not coach.is_available():
        return jsonify({"error": "APEX is offline — check that PeakAI is running."}), 503
    try:
        import logging
        logging.warning(f"APEX chat: local_date={local_date!r} local_day={local_day!r}")
        coaching_data = database.get_coaching_context(uid)
        if local_date:
            coaching_data["local_date"] = local_date
        if local_day:
            coaching_data["local_day"] = local_day
        plan_saved = False

        # Detect intent to save the plan — skip chat call if successful
        extraction_error = None
        if coach.wants_to_save_plan(message):
            try:
                from workout_logic import load_exercises
                exercise_library = [
                    {"id": e["id"], "name": e["name"], "muscle_group": e.get("muscle_group", ""),
                     "equipment": e.get("equipment", "bodyweight"),
                     "default_sets": e.get("default_sets", 3), "default_reps": e.get("default_reps", 10)}
                    for e in load_exercises()
                ]
                full_history = history + [{"role": "user", "content": message}]
                existing = database.get_apex_plan(uid)
                current_plan = existing["plan"] if existing else None
                result = coach.extract_plan_from_chat(full_history, exercise_library, current_plan=current_plan)
                for day in result.get("plan", []):
                    enriched = []
                    for item in day.get("exercises", []):
                        ex = get_exercise_by_id(item["id"])
                        if ex:
                            ex["sets"] = item.get("sets", ex["sets"])
                            ex["reps"] = item.get("reps", ex["reps"])
                            enriched.append(ex)
                    day["exercises"] = enriched
                database.save_apex_plan(uid, result["plan"])
                plan_saved = True
                response = "Done! I've saved that as your weekly plan. Tap **📅 My Plan** to see the full schedule and load today's workout."
            except Exception as e:
                extraction_error = str(e)

        goals_updated = False
        if not plan_saved and coach.wants_to_update_goals(message):
            try:
                full_history = history + [{"role": "user", "content": message}]
                goals = coach.extract_goals_from_chat(full_history)
                if goals:
                    profile = database.get_profile(uid) or {}
                    sparky_key = profile.get("sparky_api_key") or None
                    ok, msg = sparky_sync.update_goals(
                        calories=goals.get("calories"),
                        protein_g=goals.get("protein_g"),
                        carbs_g=goals.get("carbs_g"),
                        fat_g=goals.get("fat_g"),
                        api_key=sparky_key,
                    )
                    if ok:
                        parts = [f"{round(goals[k])}{'g' if k != 'calories' else ' kcal'}"
                                 for k in ("calories", "protein_g", "carbs_g", "fat_g") if k in goals]
                        response = f"Done! Your Sparky goals have been updated: {', '.join(parts)}."
                        goals_updated = True
                    else:
                        response = f"I couldn't update your goals in Sparky: {msg}. You can update them manually in Sparky settings."
                        goals_updated = True  # suppress fallback chat
            except Exception as e:
                pass

        if not plan_saved and not goals_updated:
            response = coach.chat(message, coaching_data, history)
            if extraction_error:
                response += f"\n\n*(Note: I tried to save your plan but hit an error: {extraction_error[:100]}. Try saying \"save my plan\" again.)*"

        all_messages = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response},
        ]
        database.save_apex_chat(uid, all_messages)
        return jsonify({"response": response, "plan_saved": plan_saved, "goals_updated": goals_updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/progress")
def progress():
    from datetime import date, datetime, timedelta
    uid = session["user_id"]
    profile = database.get_profile(uid)
    history = database.get_weight_history(uid)
    workouts = database.get_workout_history(uid)
    stats = _progress_stats(uid)
    weights = _weight_chart_points(history)
    weight_lbs = (profile or {}).get("current_weight") or 0
    for w in workouts:
        eids = [e["id"] for e in w.get("exercises", []) if e.get("id")]
        enriched = [get_exercise_by_id(eid) for eid in eids]
        w["kcal"] = _calc_kcal([e for e in enriched if e], weight_lbs, w.get("duration_seconds"))
        # Per-exercise detail for the expandable history row
        detail = []
        for e in w.get("exercises", []):
            ex = get_exercise_by_id(e.get("id") or "")
            detail.append({
                "name": (ex or {}).get("name") or "Unknown exercise",
                "completed": e.get("completed", True),
                "sets": [s for s in (e.get("sets") or []) if s.get("weight") or s.get("reps")],
            })
        w["detail"] = detail
    stats["total_kcal"] = sum(w["kcal"] for w in workouts)

    # Group history by ISO week (Monday start), newest first
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    history_weeks = []
    for w in workouts:
        try:
            d = datetime.fromisoformat(w["completed_at"][:10]).date()
        except (ValueError, TypeError):
            continue
        monday = d - timedelta(days=d.weekday())
        if monday == this_monday:
            label = "This week"
        elif monday == this_monday - timedelta(days=7):
            label = "Last week"
        else:
            label = "Week of " + monday.strftime("%b %-d")
        if not history_weeks or history_weeks[-1]["label"] != label:
            history_weeks.append({"label": label, "workouts": []})
        history_weeks[-1]["workouts"].append(w)
    cardio = _cardio_display(uid, days=30)
    cardio_kcal = sum(c.get("kcal") or 0 for c in cardio)
    sleep = _sleep_display(uid, days=14)
    return render_template("progress.html", profile=profile, weights=weights,
                           history=workouts, history_weeks=history_weeks, stats=stats,
                           cardio=cardio, cardio_kcal=cardio_kcal, sleep=sleep,
                           sparky_enabled=bool((profile or {}).get("sparky_sync")))


@app.route("/api/push/public-key")
def push_public_key():
    import push_notify
    if not push_notify.is_available():
        return jsonify({"error": "push not available"}), 503
    return jsonify({"publicKey": push_notify.get_public_key()})


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    uid = session["user_id"]
    sub = request.get_json(force=True)
    if not sub or not sub.get("endpoint"):
        return jsonify({"error": "invalid subscription"}), 400
    database.save_push_subscription(uid, sub)
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    data = request.get_json(force=True)
    if data.get("endpoint"):
        database.delete_push_subscription(data["endpoint"])
    return jsonify({"ok": True})


@app.route("/api/strength-history")
def strength_history():
    """Per-exercise max logged weight per session — feeds the strength chart."""
    uid = session["user_id"]
    by_exercise = {}
    for w in reversed(database.get_workout_history(uid, limit=200)):
        d = (w.get("completed_at") or "")[:10]
        for e in w.get("exercises", []):
            weights = [s.get("weight") for s in (e.get("sets") or []) if s.get("weight")]
            if not weights or not e.get("id"):
                continue
            ex = get_exercise_by_id(e["id"])
            name = (ex or {}).get("name") or e["id"]
            entry = by_exercise.setdefault(e["id"], {"id": e["id"], "name": name, "points": []})
            # one point per session date — keep the heaviest
            if entry["points"] and entry["points"][-1]["date"] == d:
                entry["points"][-1]["weight"] = max(entry["points"][-1]["weight"], max(weights))
            else:
                entry["points"].append({"date": d, "weight": max(weights)})
    exercises = [v for v in by_exercise.values() if len(v["points"]) >= 2]
    exercises.sort(key=lambda v: len(v["points"]), reverse=True)
    return jsonify({"exercises": exercises})


@app.route("/api/energy-balance")
def energy_balance():
    """Last-7-days calories in (Sparky food log) vs burned (HomeFit workouts)."""
    from datetime import date, timedelta
    uid = session["user_id"]
    profile = database.get_profile(uid) or {}
    if not profile.get("sparky_sync"):
        return jsonify({"days": []})
    try:
        nutrition = sparky_sync.fetch_nutrition_log(days=7, api_key=profile.get("sparky_api_key"))
    except Exception:
        nutrition = []
    eaten = {n["date"]: n["calories"] for n in nutrition}

    weight_lbs = profile.get("current_weight") or 0
    cutoff = (date.today() - timedelta(days=6)).isoformat()
    burned = {}
    for w in database.get_workout_history(uid):
        d = (w.get("completed_at") or "")[:10]
        if d < cutoff:
            continue
        eids = [e["id"] for e in w.get("exercises", []) if e.get("id") and e.get("completed")]
        enriched = [get_exercise_by_id(eid) for eid in eids]
        kcal = _calc_kcal([e for e in enriched if e], weight_lbs, w.get("duration_seconds"))
        burned[d] = burned.get(d, 0) + (kcal or 0)

    days = []
    for i in range(6, -1, -1):
        d = date.today() - timedelta(days=i)
        ds = d.isoformat()
        days.append({
            "date": ds,
            "day": d.strftime("%a"),
            "eaten": eaten.get(ds, 0),
            "burned": burned.get(ds, 0),
        })
    return jsonify({"days": days})


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
