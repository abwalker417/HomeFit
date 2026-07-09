"""Flask entrypoint for HomeFit (multi-user v2)."""

import json
import os
import random
import re
import subprocess
from collections import defaultdict
from datetime import timedelta
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

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for

import coach
import database
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
    # Persist the login across app restarts. Without an explicit lifetime the
    # session cookie has no expiry, so the iOS WKWebView drops it when the app
    # is killed and the user is forced to re-login every launch.
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
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
    "api_last_workout", "api_recent_workouts", "api_last_weight", "api_external_workout", "api_sleep",
    "api_health_metric", "push_register_apns", "api_panel_summary",
    "garage", "garage_pick", "garage_choose", "garage_workout_view", "garage_complete",
    "garage_autosave",
    "garage_media", "garage_media_control", "garage_media_art", "garage_light",
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
        "cardio_days_per_week": int(form.get("cardio_days_per_week", 5) or 5),
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
    monday_iso = monday.isoformat()
    externals = database.get_external_workouts(user_id, days=7)
    # Workout days = HomeFit sessions + counting Apple workouts (golf, long
    # sessions); shared with the streak + push reminders.
    week_dates = database.workout_day_dates(user_id, since_iso=monday_iso)
    trained_today = today.isoformat() in week_dates
    days_left = 7 - today.weekday()  # includes today
    done = len(week_dates)
    stats["week_workouts"] = done
    # Away mode (travel/vacation/sick): paused days reduce this week's targets
    # and pause the streak/nudges — see database.streak_pause.
    pause = database.current_week_pause(user_id)
    stats["away"] = pause["active"]
    stats["away_upcoming"] = database.get_upcoming_pause(user_id)
    stats["target_days_week"] = max(0, stats["target_days"] - pause["paused_days"])
    usable_days_left = max(0, days_left - pause["remaining_paused"])
    # Separate cardio goal: distinct days this week with any logged walk/cardio
    stats["cardio_goal"] = profile.get("cardio_days_per_week") or 5
    stats["cardio_goal_week"] = max(0, stats["cardio_goal"] - pause["paused_days"])
    cardio_dates = {(c.get("started_at") or "")[:10] for c in externals
                    if (c.get("started_at") or "")[:10] >= monday_iso}
    cardio_dates.discard("")
    stats["cardio_days"] = len(cardio_dates)
    # Today is the last chance to keep the weekly target reachable
    stats["must_train_today"] = (
        not trained_today
        and not pause["today_paused"]
        and stats["target_days_week"] > 0
        and done < stats["target_days_week"]
        and done + usable_days_left >= stats["target_days_week"]
        and done + usable_days_left - 1 < stats["target_days_week"]
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
    # Keep the login cookie persistent (survives app/browser restarts) rather
    # than a session-scoped cookie the WKWebView drops when the app is killed.
    session.permanent = True
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


@app.route("/api/away", methods=["POST"])
def api_away_start():
    """Start (or schedule/backdate) away mode — travel/vacation/sick days that
    pause streaks, relax weekly targets and mute training nudges."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    from datetime import date
    data = request.get_json(silent=True) or {}
    reason = data.get("reason") or "travel"
    if reason not in ("travel", "vacation", "sick"):
        return jsonify({"error": "invalid reason"}), 400
    start = (data.get("start_date") or "").strip() or None
    end = (data.get("end_date") or "").strip() or None
    try:
        if start:
            date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
    except ValueError:
        return jsonify({"error": "invalid date"}), 400
    if start and end and end < start:
        return jsonify({"error": "end date is before start date"}), 400
    database.start_streak_pause(uid, reason=reason, start_date=start, end_date=end)
    return jsonify({"ok": True, "away": database.get_active_pause(uid),
                    "upcoming": database.get_upcoming_pause(uid)})


@app.route("/api/away/end", methods=["POST"])
def api_away_end():
    """"I'm back" — close the active away window (and drop scheduled ones)."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    database.end_streak_pause(uid)
    return jsonify({"ok": True})


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
        api_token=database.get_or_create_api_token(user_id) if user_id == session.get("user_id") else None,
        apex_memory=database.get_apex_memory(user_id),
        away=database.get_active_pause(user_id),
        away_upcoming=database.get_upcoming_pause(user_id),
    )


@app.route("/api/apex-memory", methods=["POST"])
def save_apex_memory_route():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    content = (request.get_json(silent=True) or {}).get("content", "")
    if len(content) > 4000:
        content = content[:4000]
    database.save_apex_memory(uid, content.strip())
    return jsonify({"ok": True})


# ── Food logging prototype (single-call parser, the cheap path) ───────────────

@app.route("/log-food")
def log_food_page():
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("profiles"))
    goal = database.get_nutrition_goal(uid)
    if not goal:
        database.save_nutrition_goal(uid, 2000, 120, 200, 65)
        goal = database.get_nutrition_goal(uid)
    from datetime import date as _date
    _today = _date.today().isoformat()
    past = [d for d in database.get_food_log_days(uid, days=14) if d["meal_date"] != _today]
    return render_template("log_food.html",
                           today_foods=database.get_food_log_today(uid), goal=goal,
                           past_days=past, favorites=database.get_food_favorites(uid))


@app.route("/api/food/goals", methods=["POST"])
def api_food_goals():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    database.save_nutrition_goal(uid, d.get("calories"), d.get("protein_g"),
                                 d.get("carbs_g"), d.get("fat_g"))
    return jsonify({"ok": True, "goal": database.get_nutrition_goal(uid)})


def _food_day_total(uid):
    """Sum of today's logged meals, for goal-aware coaching context."""
    foods = database.get_food_log_today(uid)
    return {
        "calories": sum(f.get("calories") or 0 for f in foods),
        "protein_g": round(sum(f.get("protein_g") or 0 for f in foods), 1),
        "carbs_g": round(sum(f.get("carbs_g") or 0 for f in foods), 1),
        "fat_g": round(sum(f.get("fat_g") or 0 for f in foods), 1),
    }


@app.route("/api/food/parse", methods=["POST"])
def api_food_parse():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    text = (request.get_json(silent=True) or {}).get("text", "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400
    try:
        import food_parser
        return jsonify(food_parser.parse_meal(
            text, goal=database.get_nutrition_goal(uid),
            day_total=_food_day_total(uid)))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/food/parse-image", methods=["POST"])
def api_food_parse_image():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    data_url = d.get("image", "")
    if not data_url.startswith("data:image"):
        return jsonify({"error": "no image"}), 400
    if len(data_url) > 8_000_000:  # ~6MB image; client should downscale first
        return jsonify({"error": "image too large"}), 413
    try:
        import food_parser
        return jsonify(food_parser.parse_meal_image(
            data_url, d.get("note", ""), goal=database.get_nutrition_goal(uid),
            day_total=_food_day_total(uid)))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/food/log", methods=["POST"])
def api_food_log():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    items = d.get("items") or []
    totals = d.get("totals") or {}
    if not items:
        return jsonify({"error": "no items"}), 400
    database.add_food_log(uid, d.get("description", ""), items, totals, d.get("cost_usd", 0) or 0)
    return jsonify({"ok": True, "today": database.get_food_log_today(uid)})


@app.route("/api/food/delete", methods=["POST"])
def api_food_delete():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    log_id = (request.get_json(silent=True) or {}).get("id")
    if log_id:
        database.delete_food_log(uid, int(log_id))
    return jsonify({"ok": True, "today": database.get_food_log_today(uid)})


@app.route("/api/food/favorite", methods=["POST"])
def api_food_favorite():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    items = d.get("items") or []
    if not items:
        return jsonify({"error": "no items"}), 400
    database.add_food_favorite(uid, d.get("name", ""), items, d.get("totals") or {})
    return jsonify({"ok": True, "favorites": database.get_food_favorites(uid)})


@app.route("/api/food/favorite/delete", methods=["POST"])
def api_food_favorite_delete():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    fav_id = (request.get_json(silent=True) or {}).get("id")
    if fav_id:
        database.delete_food_favorite(uid, int(fav_id))
    return jsonify({"ok": True, "favorites": database.get_food_favorites(uid)})


@app.route("/profiles/switch", methods=["POST", "GET"])
def profile_switch_out():
    session.pop("user_id", None)
    return redirect(url_for("profiles"))


@app.route("/cancel-workout", methods=["POST"])
def cancel_workout():
    session.pop("today_workout", None)
    uid = session.get("user_id")
    if uid:
        database.clear_workout_draft(uid)
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


def _fmt_dur(secs):
    if not secs:
        return ""
    m = secs // 60
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _sleep_display(uid, days=14):
    """Recent sleep with formatted hours, stages, and local bed/wake times."""
    from datetime import datetime
    items = database.get_recent_sleep(uid, days=days)
    for s in items:
        s["hm"] = _fmt_dur(s.get("duration_seconds"))
        s["hours"] = round((s.get("duration_seconds") or 0) / 3600, 1)
        try:
            s["dow"] = datetime.fromisoformat(s["entry_date"]).strftime("%a")
        except (ValueError, TypeError, KeyError):
            s["dow"] = ""
        s["deep_fmt"] = _fmt_dur(s.get("deep_seconds"))
        s["rem_fmt"] = _fmt_dur(s.get("rem_seconds"))
        s["light_fmt"] = _fmt_dur(s.get("light_seconds"))
        for k in ("bedtime", "wake_time"):
            try:
                dt = datetime.fromisoformat(str(s.get(k)))
                if dt.tzinfo:
                    dt = dt.astimezone()
                s[k + "_fmt"] = dt.strftime("%-I:%M %p")
            except (ValueError, TypeError):
                s[k + "_fmt"] = ""
        stage_bits = []
        if s["deep_fmt"]:
            stage_bits.append("Deep " + s["deep_fmt"])
        if s["rem_fmt"]:
            stage_bits.append("REM " + s["rem_fmt"])
        s["stages"] = " · ".join(stage_bits)
    return items


def _dashboard_steps(uid):
    sc = database.get_steps_today(uid)
    disp = (f"{sc / 1000:.1f}K" if sc and sc >= 1000 else (str(sc) if sc is not None else "–"))
    return {"current": sc, "goal": database.get_step_goal(uid), "display": disp}


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
    rhr = database.get_latest_metric(uid, "resting_hr")
    return render_template("dashboard.html", profile=profile, plan=plan, stats=stats,
                           cardio=_cardio_display(uid, days=14)[:3],
                           last_sleep=sleep[0] if sleep else None,
                           resting_hr=int(rhr["value"]) if rhr else None,
                           readiness=database.get_readiness(uid),
                           rings=database.get_activity_rings(uid),
                           steps=_dashboard_steps(uid),
                           has_active_workout=bool(session.get("today_workout")),
                           today_iso=date.today().isoformat(),
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


# ── Garage kiosk: HomeFit workout logger for a wall-mounted strip panel ──────
# Standalone, no-PIN, big-button. Reuses the plan + logging; own lean template.

def _garage_workout(uid):
    """Today's planned workout for the garage logger (from the saved plan)."""
    from datetime import date
    plan_data = database.get_apex_plan(uid)
    if not plan_data or not plan_data.get("plan"):
        return None
    plan = plan_data["plan"]
    wd = date.today().weekday()
    if wd >= len(plan):
        return None
    day = plan[wd]
    if day.get("rest"):
        return {"rest": True, "name": day.get("name") or "Rest Day"}
    ex_history = database.get_exercise_history(uid, limit=15)

    def last_weight(ex_id):
        for s in ex_history.get(ex_id, []):
            ws = [w["weight"] for w in s.get("sets", []) if w.get("weight")]
            if ws:
                return max(ws)
        return None

    anims = _load_exercise_animations()
    from workout_logic import load_exercises
    raw = {x["id"]: x for x in load_exercises()}
    exercises = []
    for e in day.get("exercises", []):
        exercises.append({
            "id": e.get("id"), "name": e.get("name", "?"),
            "sets": int(e.get("sets") or 3), "reps": int(e.get("reps") or 10),
            "unit": e.get("unit", "reps"), "rest": int(e.get("rest") or 60),
            "last_weight": last_weight(e.get("id")),
            "equipment": e.get("equipment") or raw.get(e.get("id"), {}).get("equipment"),
            "anim": anims.get(e.get("id")),
            "instructions": raw.get(e.get("id"), {}).get("instructions"),
        })
    return {"rest": False, "name": day.get("name", "Workout"), "exercises": exercises}


GARAGE_TYPES = {
    "apex":     "APEX",
    "upper":    "Upper Body",
    "lower":    "Lower Body",
    "core":     "Core",
    "recovery": "Recovery",
}


def _garage_workout_for_type(uid, wtype):
    """Garage workout for the chosen option: 'apex' = today's saved plan;
    upper/lower/core = a fresh pick from that category; recovery = light
    bodyweight movements. Deterministic (no AI call)."""
    if not wtype or wtype == "apex":
        return _garage_workout(uid)
    if wtype not in GARAGE_TYPES:
        return _garage_workout(uid)

    import random
    from workout_logic import all_exercises_with_status, load_exercises
    profile = database.get_profile(uid) or {}
    raw = {e["id"]: e for e in load_exercises()}
    ex_history = database.get_exercise_history(uid, limit=15)

    def last_weight(ex_id):
        for s in ex_history.get(ex_id, []):
            ws = [w["weight"] for w in s.get("sets", []) if w.get("weight")]
            if ws:
                return max(ws)
        return None

    avail = [s for s in all_exercises_with_status(profile)
             if s.get("available") and not s.get("ignored")]
    cat = {"upper": "upper", "lower": "legs", "core": "core"}.get(wtype)
    if wtype == "recovery":
        pool = [s for s in avail
                if raw.get(s["id"], {}).get("difficulty", 1) <= 1
                and raw.get(s["id"], {}).get("equipment") in ("bodyweight", "resistance_bands")]
    else:
        pool = [s for s in avail if s.get("category") == cat]
    random.shuffle(pool)
    chosen = pool[:6]
    if not chosen:
        return None

    anims = _load_exercise_animations()
    exercises = []
    for s in chosen:
        e = raw.get(s["id"], {})
        exercises.append({
            "id": s["id"], "name": s["name"],
            "sets": int(e.get("default_sets") or 3), "reps": int(e.get("default_reps") or 10),
            "unit": e.get("unit", "reps"), "rest": int(e.get("rest_seconds") or 45),
            "last_weight": last_weight(s["id"]),
            "equipment": e.get("equipment"),
            "anim": anims.get(s["id"]),
            "instructions": e.get("instructions"),
        })
    return {"rest": False, "name": GARAGE_TYPES[wtype], "exercises": exercises}


def _accent_ctx(uid):
    """Accent hex/rgb/name for a garage user (the panel has no logged-in session,
    so we resolve it from the picked garage_user, mirroring inject_globals)."""
    accent = (database.get_accent_color(uid) if uid else None) or DEFAULT_ACCENT
    accent_name = next((n for n, (hex_, _) in ACCENT_PALETTE.items() if hex_ == accent), "ice")
    return {"accent": accent, "accent_rgb": _accent_rgb(accent), "accent_name": accent_name}


@app.route("/garage")
def garage():
    return render_template("garage.html", users=database.list_users(), **_accent_ctx(None))


@app.route("/garage/pick", methods=["POST"])
def garage_pick():
    try:
        uid = int(request.form.get("user_id", 0))
    except (TypeError, ValueError):
        uid = 0
    if database.get_user(uid):
        session["garage_user"] = uid
        session.permanent = True
    return redirect(url_for("garage_choose"))


@app.route("/garage/choose")
def garage_choose():
    uid = session.get("garage_user")
    if not uid or not database.get_user(uid):
        return redirect(url_for("garage"))
    return render_template("garage_choose.html", user=database.get_user(uid), **_accent_ctx(uid))


@app.route("/garage/workout")
def garage_workout_view():
    uid = session.get("garage_user")
    if not uid or not database.get_user(uid):
        return redirect(url_for("garage"))
    wtype = request.args.get("type", "apex")
    workout = _garage_workout_for_type(uid, wtype)
    # Shared in-progress draft: resume a session started on the phone (or recover a
    # panel session). The client gates on recency + exercise-id overlap.
    draft = database.get_workout_draft(uid)
    draft_state = draft["state"] if draft else None
    return render_template("garage_workout.html",
                           workout=workout,
                           user=database.get_user(uid), uid=uid, wtype=wtype,
                           has_media=bool(_garage_media_cfg().get("player")),
                           draft_state=draft_state,
                           **_accent_ctx(uid))


_GARAGE_MEDIA_FILE = os.path.join(os.path.dirname(__file__), "data", "garage_media.json")


def _garage_media_cfg():
    """Garage media player config (gitignored): {ha_url, ha_token, player}."""
    try:
        with open(_GARAGE_MEDIA_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


@app.route("/api/garage/media")
def garage_media():
    cfg = _garage_media_cfg()
    if not cfg.get("ha_url") or not cfg.get("player"):
        return jsonify({"available": False})
    try:
        r = requests.get(f"{cfg['ha_url'].rstrip('/')}/api/states/{cfg['player']}",
                         headers={"Authorization": f"Bearer {cfg.get('ha_token', '')}"}, timeout=6)
        r.raise_for_status()
        st = r.json()
        a = st.get("attributes", {}) or {}
        return jsonify({"available": True, "state": st.get("state"),
                        "title": a.get("media_title"), "artist": a.get("media_artist"),
                        "volume": a.get("volume_level"),
                        "art": a.get("entity_picture_local") or a.get("entity_picture")})
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@app.route("/api/garage/light")
def garage_light():
    """State of the garage's light (for the panel's dim-when-dark veil).
    Uses the garage_media.json config (ha_url/ha_token) + a `dim_light` entity."""
    cfg = _garage_media_cfg()
    ent = cfg.get("dim_light")
    if not cfg.get("ha_url") or not ent:
        return jsonify({"available": False})
    try:
        r = requests.get(f"{cfg['ha_url'].rstrip('/')}/api/states/{ent}",
                         headers={"Authorization": f"Bearer {cfg.get('ha_token', '')}"}, timeout=6)
        r.raise_for_status()
        return jsonify({"available": True, "on": r.json().get("state") == "on"})
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@app.route("/api/garage/media/<action>", methods=["POST"])
def garage_media_control(action):
    cfg = _garage_media_cfg()
    svc = {"play": "media_play_pause", "next": "media_next_track", "prev": "media_previous_track",
           "volup": "volume_up", "voldown": "volume_down"}.get(action)
    if not cfg.get("ha_url") or not svc:
        return jsonify({"error": "bad request"}), 400
    try:
        requests.post(f"{cfg['ha_url'].rstrip('/')}/api/services/media_player/{svc}",
                      headers={"Authorization": f"Bearer {cfg.get('ha_token', '')}"},
                      json={"entity_id": cfg["player"]}, timeout=6)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/garage/media/art")
def garage_media_art():
    cfg = _garage_media_cfg()
    path = request.args.get("path", "")
    if not cfg.get("ha_url") or not path.startswith("/api/"):
        return Response(status=204)
    try:
        r = requests.get(f"{cfg['ha_url'].rstrip('/')}{path}",
                         headers={"Authorization": f"Bearer {cfg.get('ha_token', '')}"}, timeout=6)
        r.raise_for_status()
        return Response(r.content, content_type=r.headers.get("Content-Type", "image/jpeg"))
    except Exception:
        return Response(status=502)


@app.route("/api/garage/complete", methods=["POST"])
def garage_complete():
    uid = session.get("garage_user")
    if not uid:
        return jsonify({"error": "no garage user"}), 400
    from datetime import date
    data = request.get_json(force=True) or {}
    exercises = data.get("exercises", [])
    duration = data.get("duration_seconds")
    day_name = data.get("day_name", "Garage Workout")
    database.log_workout(uid, day_name, 1, exercises, duration)
    database.clear_workout_draft(uid)
    completed = [e for e in exercises if e.get("completed") and e.get("id")]
    enriched = [get_exercise_by_id(e["id"]) for e in completed]
    enriched = [e for e in enriched if e]
    sets_by_id = {e["id"]: e.get("sets", []) for e in completed}
    for e in enriched:
        e["sets_logged"] = sets_by_id.get(e["id"], [])
    profile = database.get_profile(uid)
    kcal = _calc_kcal(enriched, (profile or {}).get("current_weight") or 0, duration)

    # APEX post-workout feedback for the panel's completion card. Best-effort:
    # never let a coach hiccup block logging the workout.
    insight, overload = None, []
    if coach.is_available():
        try:
            coaching_data = database.get_coaching_context(uid)
            overload = get_progressive_overload_suggestions(coaching_data.get("exercise_history", {}))
            insight = coach.generate_post_workout_insight(coaching_data, overload, enriched)
        except Exception:
            insight, overload = None, []

    session.pop("garage_user", None)
    return jsonify({"ok": True, "kcal": kcal, "exercises_completed": len(enriched),
                    "day_name": day_name, "insight": insight, "overload": overload})


@app.route("/api/garage/autosave", methods=["POST"])
def garage_autosave():
    """Mirror an in-progress panel workout to the shared server draft so the phone
    can resume it (and a panel crash loses nothing). Uses the garage session."""
    uid = session.get("garage_user")
    if not uid:
        return jsonify({"error": "no garage user"}), 400
    d = request.get_json(silent=True) or {}
    state = d.get("state")
    if state is None:
        return jsonify({"error": "no state"}), 400
    database.save_workout_draft(uid, d.get("day_name", ""), json.dumps(state))
    return jsonify({"ok": True})


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
    anims = _load_exercise_animations()
    exercises = workout.get("exercises", [])
    for ex in exercises:
        ex["weight_hint"] = weight_hint(ex.get("id", ""))
        ex["demo_image"] = images.get(ex.get("name", ""))
        ex["anim"] = anims.get(ex.get("id", ""))

    # Embed the full exercise library so "Add exercise" works in-page (no network
    # round-trip — the native app re-fetches every screen, which froze on flaky
    # connections). Client adds exercises to the DOM; Finish posts them all.
    from workout_logic import all_exercises_with_status, load_exercises as _load_ex
    profile = database.get_profile(uid) or {}
    raw = {e["id"]: e for e in _load_ex()}
    in_workout = {x.get("id") for x in exercises}
    library = []
    for st in all_exercises_with_status(profile):
        r = raw.get(st["id"], {})
        library.append({
            "id": st["id"], "name": st["name"], "category": st["category"],
            "equipment": st["equipment"], "default_sets": r.get("default_sets", 3),
            "default_reps": r.get("default_reps", 10), "unit": r.get("unit", "reps"),
            "instructions": r.get("instructions", ""), "rest_seconds": r.get("rest_seconds", 60),
            "available": st["available"], "in_workout": st["id"] in in_workout,
            "anim": anims.get(st["id"]),
        })

    day = {
        "day_number": 1,
        "name": workout.get("label", "Today's Workout"),
        "focus": focus_label,
        "ai_generated": workout.get("ai_generated", False),
        "exercises": exercises,
    }
    # Shared in-progress draft. The client decides whether to resume it (recency +
    # exercise-id overlap), so it works for crash recovery AND phone<->panel handoff
    # even though the two surfaces label the day differently.
    draft = database.get_workout_draft(uid)
    draft_state = draft["state"] if draft else None
    return render_template("workout.html", day=day, profile=profile,
                           user_id=uid, exercise_library=library, draft_state=draft_state)


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
            database.clear_workout_draft(uid)
            return jsonify({"ok": True, "kcal": kcal,
                            "exercises_completed": completed_n, "duplicate": True})

    database.log_workout(
        uid,
        day_name,
        int(data.get("day_number", 1)),
        exercises,
        duration,
    )
    database.clear_workout_draft(uid)
    completed = [e for e in exercises if e.get("completed") and e.get("id")]
    sets_by_id = {e["id"]: e.get("sets", []) for e in completed}
    enriched = [get_exercise_by_id(e["id"]) for e in completed]
    enriched = [e for e in enriched if e]
    for e in enriched:
        e["sets_logged"] = sets_by_id.get(e["id"], [])
    profile = database.get_profile(uid)
    kcal = _calc_kcal(enriched, (profile or {}).get("current_weight") or 0, duration)
    session.pop("today_workout", None)

    return jsonify({"ok": True, "kcal": kcal, "exercises_completed": len(enriched)})


@app.route("/edit-workout/<int:log_id>")
def edit_workout_page(log_id):
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("profiles"))
    w = database.get_workout_by_id(uid, log_id)
    if not w:
        return redirect(url_for("progress"))
    # enrich each exercise with a display name (stored name, else from the library)
    for ex in w.get("exercises", []):
        if not ex.get("name") and ex.get("id"):
            lib = get_exercise_by_id(ex["id"])
            ex["name"] = (lib or {}).get("name", ex["id"])
        ex["sets"] = ex.get("sets") or []
    return render_template("edit_workout.html", workout=w)


@app.route("/api/workout/update", methods=["POST"])
def api_workout_update():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    log_id = d.get("id")
    exercises = d.get("exercises")
    if not log_id or exercises is None:
        return jsonify({"error": "bad request"}), 400
    ok = database.update_workout_exercises(uid, int(log_id), exercises)
    return jsonify({"ok": ok})


@app.route("/api/workout/autosave", methods=["POST"])
def api_workout_autosave():
    """Mirror the in-progress workout to the server after each set, so a native-app
    crash that wipes localStorage doesn't lose the session."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    state = d.get("state")
    if state is None:
        return jsonify({"error": "no state"}), 400
    database.save_workout_draft(uid, d.get("day_name", ""), json.dumps(state))
    return jsonify({"ok": True})


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


@app.route("/api/daily-brief", methods=["GET"])
def daily_brief():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import daily_service
        return jsonify({"brief": daily_service.get_or_generate(uid)})
    except Exception:
        return jsonify({"brief": None})


_exercise_images = None
_exercise_animations = None

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

def _load_exercise_animations():
    """exercise id -> [start_frame_url, end_frame_url] for looping demo gifs."""
    global _exercise_animations
    if _exercise_animations is None:
        path = os.path.join(os.path.dirname(__file__), "data", "exercise_animations.json")
        try:
            with open(path) as f:
                _exercise_animations = json.load(f)
        except Exception:
            _exercise_animations = {}
    return _exercise_animations


@app.route("/api/identify-exercise", methods=["POST"])
def api_identify_exercise():
    """Upload a short clip -> identify the move + match a free-exercise-db demo.
    Returns a PREVIEW proposal (nothing is saved until /confirm)."""
    if not session.get("user_id"):
        return jsonify({"error": "unauthorized"}), 401
    f = request.files.get("video")
    if not f:
        return jsonify({"error": "no video uploaded"}), 400
    import tempfile
    import exercise_identifier
    suffix = os.path.splitext(f.filename or "")[1] or ".mov"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    try:
        f.save(tmp.name)
        if os.path.getsize(tmp.name) > 40_000_000:
            return jsonify({"error": "video too large (40MB max — keep it a few seconds)"}), 413
        return jsonify(exercise_identifier.identify_video(tmp.name))
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


@app.route("/api/identify-exercise/confirm", methods=["POST"])
def api_identify_exercise_confirm():
    """Persist a confirmed proposal into the exercise library (+ its demo)."""
    if not session.get("user_id"):
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    entry = d.get("exercise") or {}
    demo = d.get("demo")
    if not entry.get("id") or not entry.get("name"):
        return jsonify({"error": "invalid exercise"}), 400

    data_dir = os.path.join(os.path.dirname(__file__), "data")
    ex_path = os.path.join(data_dir, "exercises.json")
    with open(ex_path) as fh:
        lib = json.load(fh)
    created = entry["id"] not in {e["id"] for e in lib}
    if created:
        lib.append(entry)
        with open(ex_path, "w") as fh:
            json.dump(lib, fh, indent=2, ensure_ascii=False)

    if demo and len(demo) >= 2:
        anim_path = os.path.join(data_dir, "exercise_animations.json")
        with open(anim_path) as fh:
            anims = json.load(fh)
        if entry["id"] not in anims:
            anims[entry["id"]] = demo[:2]
            with open(anim_path, "w") as fh:
                json.dump(anims, fh, indent=1, ensure_ascii=False)
        global _exercise_animations
        _exercise_animations = None  # invalidate cache so the new demo loads

    return jsonify({"ok": True, "id": entry["id"], "created": created})


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


@app.route("/api/log_weight", methods=["POST"])
def log_weight():
    uid = session["user_id"]
    data = request.get_json(force=True)
    weight = float(data.get("weight", 0))
    database.log_weight(uid, weight)
    return jsonify({"ok": True})


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


@app.route("/api/recent-workouts")
def api_recent_workouts():
    """Recent HomeFit gym sessions for the native app to backfill into Apple Health.

    A workout logged outside the iOS app (garage panel, desktop browser, the other
    user's phone) never hits the logWorkout JS bridge, so it never reaches Apple
    Health or the rings. The app pulls this list on foreground and writes any
    session not already in Health (deduped client-side by time overlap).
    Auth: ?token= (same api_token as the other relays).
    """
    from datetime import datetime, timedelta
    token = request.args.get("token", "")
    uid = database.get_user_id_by_token(token)
    if not uid:
        return jsonify({"error": "invalid token"}), 401
    try:
        days = max(1, min(30, int(request.args.get("days", 3))))
    except (TypeError, ValueError):
        days = 3
    cutoff = datetime.now() - timedelta(days=days)
    profile = database.get_profile(uid)
    weight = (profile or {}).get("current_weight") or 0
    out = []
    for w in database.get_workout_history(uid, limit=50):
        try:
            completed_at = datetime.fromisoformat(w["completed_at"])
        except (TypeError, ValueError, KeyError):
            continue
        if completed_at < cutoff:
            break  # history is newest-first; everything older follows
        duration_s = w.get("duration_seconds") or 0
        start_time = completed_at - timedelta(seconds=duration_s)
        enriched = [get_exercise_by_id(e["id"]) for e in w.get("exercises", [])
                    if e.get("id") and e.get("completed")]
        enriched = [e for e in enriched if e]
        out.append({
            "id": w.get("id"),
            "name": w.get("day_name", "Workout"),
            # Whole-second ISO (no microseconds): iOS ISO8601DateFormatter can't
            # reliably parse 6-digit fractional seconds, and the app skips any
            # workout whose timestamps fail to parse.
            "start": start_time.astimezone().replace(microsecond=0).isoformat(),
            "end": completed_at.astimezone().replace(microsecond=0).isoformat(),
            "duration_seconds": duration_s,
            "kcal": _calc_kcal(enriched, weight, duration_s),
        })
    return jsonify({"workouts": out})


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
    """Relay for Apple Health workouts (posted by the native app / an iOS Shortcut).

    Dedups against HomeFit's own logged sessions, then records genuinely external
    cardio into HomeFit's external_workouts table (progress + energy balance).
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
    # Claim the dedup key FIRST so concurrent posts can't both be recorded.
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
            "reason": f"overlaps HomeFit workout '{overlap['day_name']}'",
        }), 200

    database.set_external_workout_status(uid, name, started_iso, "recorded")
    return jsonify({"status": "recorded", "workout": name, "date": local_date.isoformat()}), 201


@app.route("/api/sleep", methods=["POST"])
def api_sleep():
    """Relay for Apple Health / Oura sleep (posted by the native app).
    Body: {date, bedtime, wake_time, duration_seconds, [deep_s,rem_s,light_s,awake_s], [source]}
    Dedups one night per date (UNIQUE) and records it into HomeFit's sleep_log.
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

    database.set_sleep_status(uid, entry_date, "recorded")
    hrs = round(duration_seconds / 3600, 1)
    return jsonify({"status": "recorded", "date": entry_date, "hours": hrs}), 201


# Daily health metrics the app may post (resting HR; extensible later)
ALLOWED_METRICS = {"resting_hr", "active_energy", "resting_energy",
                   "move_goal", "exercise_minutes", "exercise_goal",
                   "stand_hours", "stand_goal", "steps"}


@app.route("/api/health-metric", methods=["POST"])
def api_health_metric():
    """Relay for daily Apple Health metrics (resting HR). Body: {metric, date, value, [source]}."""
    token = request.args.get("token", "") or \
        request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
    uid = database.get_user_id_by_token(token)
    if not uid:
        return jsonify({"error": "invalid token"}), 401
    data = request.get_json(silent=True) or {}
    metric = str(data.get("metric") or "").strip()
    metric_date = str(data.get("date") or "").strip()
    if metric not in ALLOWED_METRICS or not metric_date:
        return jsonify({"error": "required: valid metric + date"}), 400
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return jsonify({"error": "value must be numeric"}), 400
    database.upsert_daily_metric(uid, metric, metric_date, value, str(data.get("source") or "apple_health"))
    return jsonify({"status": "ok", "metric": metric, "date": metric_date, "value": value}), 201


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
                unmapped = []
                for day in result.get("plan", []):
                    enriched, seen = [], set()
                    for item in day.get("exercises", []):
                        eid = item.get("id")
                        if not eid or eid in seen:
                            continue  # drop blanks + duplicate exercises within the day
                        ex = get_exercise_by_id(eid)
                        if ex:
                            ex = dict(ex)  # copy so we don't mutate the library cache
                            ex["sets"] = item.get("sets", ex.get("sets"))
                            ex["reps"] = item.get("reps", ex.get("reps"))
                            enriched.append(ex)
                            seen.add(eid)
                        else:
                            unmapped.append(eid)
                    day["exercises"] = enriched

                # Did the plan actually change? (signature = names + rest + exercise ids per day)
                def _sig(plan):
                    return [
                        (d.get("name"), bool(d.get("rest")),
                         [e.get("id") for e in (d.get("exercises") or [])])
                        for d in (plan or [])
                    ]
                changed = _sig(result["plan"]) != _sig(current_plan)

                database.save_apex_plan(uid, result["plan"])
                plan_saved = True
                if changed:
                    response = "Done! I've saved that as your weekly plan. Tap **📅 My Plan** to see the full schedule and load today's workout."
                else:
                    response = ("I saved your plan, but it came out the same as before — I may have "
                                "missed the change. Tell me the specific edit again (e.g. \"replace "
                                "push-ups with dumbbell chest press on every day\") and say \"save the change\".")
                if unmapped:
                    uniq = ", ".join(sorted(set(unmapped)))
                    response += (f"\n\n*(Heads up: I couldn't match these to your exercise library so "
                                 f"they were skipped: {uniq}. Pick a different exercise and re-save.)*")
            except Exception as e:
                extraction_error = str(e)

        goals_updated = False
        if not plan_saved and coach.wants_to_update_goals(message):
            try:
                full_history = history + [{"role": "user", "content": message}]
                goals = coach.extract_goals_from_chat(full_history)
                if goals:
                    current = database.get_nutrition_goal(uid) or {}
                    cal = goals.get("calories") or current.get("calories") or 2000
                    pro = goals.get("protein_g") or current.get("protein_g") or 150
                    carb = goals.get("carbs_g") or current.get("carbs_g") or 200
                    fat = goals.get("fat_g") or current.get("fat_g") or 65
                    database.save_nutrition_goal(uid, cal, pro, carb, fat)
                    parts = [f"{round(goals[k])}{'g' if k != 'calories' else ' kcal'}"
                             for k in ("calories", "protein_g", "carbs_g", "fat_g") if k in goals]
                    response = f"Done! Your nutrition goals are updated: {', '.join(parts)}."
                    goals_updated = True
            except Exception:
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

        # If the user explicitly asked APEX to remember something, distil it into
        # the persistent memory now (the nightly job handles passive updates).
        if coach.wants_to_remember(message):
            try:
                existing = coaching_data.get("apex_memory", "")
                updated = coach.update_memory(existing, all_messages, coaching_data.get("profile"))
                if updated and updated != existing:
                    database.save_apex_memory(uid, updated)
            except Exception:
                pass

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
                           cardio=cardio, cardio_kcal=cardio_kcal, sleep=sleep)


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


@app.route("/api/push/register-apns", methods=["POST"])
def push_register_apns():
    """The native iOS app registers its APNs device token here.
    Auth: Bearer api_token (same as the Apple Health relay). Body:
    {device_token, environment: 'sandbox'|'production'}."""
    token = request.args.get("token", "") or \
        request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
    uid = database.get_user_id_by_token(token)
    if not uid:
        return jsonify({"error": "invalid token"}), 401
    data = request.get_json(silent=True) or {}
    device_token = (data.get("device_token") or "").strip()
    if not device_token:
        return jsonify({"error": "device_token required"}), 400
    env = data.get("environment") if data.get("environment") in ("sandbox", "production") else "production"
    database.save_apns_token(uid, device_token, env)
    app.logger.info("APNs token registered: user=%s env=%s token=%s…%s",
                    uid, env, device_token[:8], device_token[-6:])
    return jsonify({"ok": True})


@app.route("/api/push/test", methods=["POST"])
def push_test():
    """Send a test notification to the logged-in user (web push + APNs)."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    import push_notify
    n = push_notify.send_to_user(uid, "HomeFit", "Test notification — you're wired up.", "/")
    return jsonify({"ok": True, "sent": n})


@app.route("/api/panel-summary")
def api_panel_summary():
    """Compact 'today' summary for an external panel (homestrip) or a read-only
    agent (NyX). Read-only. Auth: Bearer/?token= — the full api_token OR a scoped
    key with the 'summary' scope. Returns readiness + today's workout + nutrition."""
    from datetime import date
    token = request.args.get("token", "") or \
        request.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
    uid = database.resolve_scoped_uid(token, "summary")
    if not uid:
        return jsonify({"error": "invalid token"}), 401

    today = date.today().isoformat()
    weekday = date.today().weekday()

    # today's planned workout
    plan_data = database.get_apex_plan(uid)
    workout = None
    if plan_data and plan_data.get("plan") and weekday < len(plan_data["plan"]):
        day = plan_data["plan"][weekday]
        if day.get("rest"):
            workout = {"name": "Rest Day", "rest": True}
        else:
            workout = {"name": day.get("name") or "Workout", "rest": False,
                       "exercises": len(day.get("exercises", []))}
    recent = database.get_workout_history(uid, limit=10)
    today_workouts = [w for w in recent if (w.get("completed_at") or "").startswith(today)]
    trained = bool(today_workouts)
    workout_minutes = sum((w.get("duration_seconds") or 0) // 60 for w in today_workouts)
    if workout:
        workout["done"] = trained

    # nutrition vs goal
    foods = database.get_food_log_today(uid)
    goal = database.get_nutrition_goal(uid) or {}
    nutrition = {
        "calories": sum(f.get("calories") or 0 for f in foods),
        "protein_g": round(sum(f.get("protein_g") or 0 for f in foods)),
        "goal_calories": goal.get("calories") or 0,
        "goal_protein_g": goal.get("protein_g") or 0,
    }

    # Apple Activity rings (today) — Move / Exercise / Stand with the user's goals
    rings = database.get_activity_rings(uid)
    energy_today = next((e for e in database.get_energy_log(uid, days=2) if e["date"] == today), None)

    return jsonify({
        "name": (database.get_profile(uid) or {}).get("name"),
        "readiness": database.get_readiness(uid),
        "workout": workout,
        "workout_minutes": workout_minutes,
        "nutrition": nutrition,
        "rings": rings,
        "steps": {"current": database.get_steps_today(uid), "goal": database.get_step_goal(uid)},
        "energy": {
            "active": (energy_today or {}).get("active"),
            "resting": (energy_today or {}).get("resting"),
        },
    })


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
    """Last-7-days calories in (HomeFit food log) vs burned (HomeFit workouts + Apple Health cardio)."""
    from datetime import date, timedelta
    uid = session["user_id"]
    profile = database.get_profile(uid) or {}
    eaten = {d["meal_date"]: (d["calories"] or 0) for d in database.get_food_log_days(uid, days=7)}

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

    # Add cardio burn (Apple Health) so the "burned" side is complete
    from datetime import datetime, timezone
    for c in database.get_external_workouts(uid, days=7):
        if not c.get("kcal"):
            continue
        try:
            d = datetime.fromisoformat(c["started_at"]).replace(tzinfo=timezone.utc).astimezone().date().isoformat()
        except (ValueError, TypeError):
            continue
        if d >= cutoff:
            burned[d] = burned.get(d, 0) + c["kcal"]

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


def _sw_cache_version():
    """Newest static-asset mtime → a version token that changes every deploy that
    touches a cached asset, so the SW purges old caches and refetches on its own."""
    latest = 0
    static_dir = os.path.join(app.root_path, "static")
    for root, _dirs, files in os.walk(static_dir):
        for f in files:
            if f.endswith((".js", ".css", ".json", ".html")):
                try:
                    m = os.stat(os.path.join(root, f)).st_mtime_ns
                    if m > latest:
                        latest = m
                except OSError:
                    pass
    return latest


@app.route("/sw.js")
def service_worker():
    # Inject a deploy-derived cache version and serve sw.js itself uncached, so a
    # new deploy always propagates instead of the browser pinning a stale worker.
    sw_path = os.path.join(app.root_path, "static", "js", "sw.js")
    with open(sw_path, "r") as fh:
        body = fh.read()
    body = re.sub(
        r"const CACHE = '[^']*';",
        "const CACHE = 'homefit-%d';" % _sw_cache_version(),
        body,
        count=1,
    )
    response = app.response_class(body, mimetype="text/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


if __name__ == "__main__":
    host = os.environ.get("HOMEFIT_HOST", "0.0.0.0")
    port = int(os.environ.get("HOMEFIT_PORT", "5000"))
    debug = os.environ.get("HOMEFIT_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
