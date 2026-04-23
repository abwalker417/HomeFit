"""Flask entrypoint for HomeFit (multi-user v2).

Run locally:
    pip install -r requirements.txt
    python app.py

Each user has their own profile, plan, weight log, and workout history.
Open the site -> pick a profile (or create one) -> train.
"""

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, session, abort,
)

import database
from workout_logic import generate_plan, all_exercises_with_status, VALID_LIMITATIONS

app = Flask(__name__)

# Init DB and persistent session secret
database.init_db()
app.secret_key = database.load_or_create_secret_key()

PUBLIC_ENDPOINTS = {
    "profiles", "profile_new", "profile_switch", "profile_unlock",
    "profile_switch_out",
    "manifest", "service_worker", "static",
}


@app.before_request
def require_profile():
    """Redirect to profile picker if no user is selected in the session."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if request.path.startswith("/static/"):
        return None

    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("profiles"))
    # Defence: if the user was deleted, kick them back to the picker
    if not database.get_user(uid):
        session.pop("user_id", None)
        return redirect(url_for("profiles"))
    return None


def current_user():
    uid = session.get("user_id")
    return database.get_user(uid) if uid else None


@app.context_processor
def inject_user():
    """Make current_user available in every template."""
    return {"current_user": current_user()}


# ---------- profile picker / auth ------------------------------------------

@app.route("/profiles")
def profiles():
    users = database.list_users()
    if not users:
        # First run — skip the picker and go straight to creating the first profile
        return redirect(url_for("profile_new"))
    return render_template("profiles.html", users=users)


@app.route("/profiles/new", methods=["GET", "POST"])
def profile_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        emoji = request.form.get("emoji", "💪").strip() or "💪"
        pin = request.form.get("pin", "").strip()
        pin_confirm = request.form.get("pin_confirm", "").strip()

        error = None
        if not name:
            error = "Please enter a name."
        elif pin and not pin.isdigit():
            error = "PIN must be digits only."
        elif pin and not (4 <= len(pin) <= 8):
            error = "PIN must be 4–8 digits."
        elif pin != pin_confirm:
            error = "PINs don't match."

        if error:
            return render_template(
                "profile_new.html",
                error=error, name=name, emoji=emoji,
            )

        try:
            uid = database.create_user(name, emoji=emoji, pin=pin or None)
        except ValueError as e:
            return render_template("profile_new.html", error=str(e),
                                   name=name, emoji=emoji)

        # Auto-sign-in after creation
        session["user_id"] = uid
        return redirect(url_for("onboarding"))

    return render_template("profile_new.html")


@app.route("/profiles/<int:user_id>/switch", methods=["POST"])
def profile_switch(user_id):
    user = database.get_user(user_id)
    if not user:
        abort(404)
    if user["has_pin"]:
        return redirect(url_for("profile_unlock", user_id=user_id))
    session["user_id"] = user_id
    return redirect(url_for("index"))


@app.route("/profiles/<int:user_id>/unlock", methods=["GET", "POST"])
def profile_unlock(user_id):
    user = database.get_user(user_id)
    if not user:
        abort(404)
    if not user["has_pin"]:
        session["user_id"] = user_id
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        pin = request.form.get("pin", "").strip()
        if database.verify_pin(user_id, pin):
            session["user_id"] = user_id
            return redirect(url_for("index"))
        error = "Wrong PIN."
    return render_template("profile_unlock.html", user=user, error=error)


@app.route("/profiles/<int:user_id>/edit", methods=["GET", "POST"])
def profile_edit(user_id):
    # Only the logged-in user can edit their own profile
    if session.get("user_id") != user_id:
        abort(403)
    user = database.get_user(user_id)
    if not user:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            database.delete_user(user_id)
            session.pop("user_id", None)
            return redirect(url_for("profiles"))

        name = request.form.get("name", "").strip() or user["name"]
        emoji = request.form.get("emoji", "").strip() or user["emoji"]
        pin_action = request.form.get("pin_action", "keep")
        new_pin = request.form.get("pin", "").strip()
        confirm = request.form.get("pin_confirm", "").strip()

        error = None
        if pin_action == "set":
            if not new_pin.isdigit() or not (4 <= len(new_pin) <= 8):
                error = "PIN must be 4–8 digits."
            elif new_pin != confirm:
                error = "PINs don't match."

        if error:
            return render_template("profile_edit.html", user=user, error=error)

        pin_param = False
        if pin_action == "clear":
            pin_param = None
        elif pin_action == "set":
            pin_param = new_pin

        try:
            database.update_user(user_id, name=name, emoji=emoji, pin=pin_param)
        except Exception as e:
            return render_template("profile_edit.html", user=user, error=str(e))
        return redirect(url_for("index"))

    return render_template("profile_edit.html", user=user)


@app.route("/profiles/switch", methods=["POST", "GET"])
def profile_switch_out():
    """Log out of the current profile and go back to the picker."""
    session.pop("user_id", None)
    return redirect(url_for("profiles"))


# ---------- main app --------------------------------------------------------

@app.route("/")
def index():
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))
    plan = generate_plan(
        profile["current_weight"],
        profile["goal_weight"],
        profile["fitness_level"],
        profile["limitations"],
        profile["days_per_week"],
    )
    stats = database.get_stats(uid)
    return render_template("dashboard.html", profile=profile, plan=plan, stats=stats)


@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    uid = session["user_id"]
    if request.method == "POST":
        current_weight = float(request.form["current_weight"])
        goal_weight = float(request.form["goal_weight"])
        fitness_level = request.form.get("fitness_level", "beginner")
        limitations = request.form.getlist("limitations")
        limitations = [l for l in limitations if l in VALID_LIMITATIONS]
        days_per_week = int(request.form.get("days_per_week", 4))
        database.save_profile(
            uid, current_weight, goal_weight, fitness_level,
            limitations, days_per_week,
        )
        return redirect(url_for("index"))
    profile = database.get_profile(uid) or {}
    return render_template("onboarding.html", profile=profile)


@app.route("/workout/<int:day_number>")
def workout(day_number):
    uid = session["user_id"]
    profile = database.get_profile(uid)
    if not profile:
        return redirect(url_for("onboarding"))
    plan = generate_plan(
        profile["current_weight"],
        profile["goal_weight"],
        profile["fitness_level"],
        profile["limitations"],
        profile["days_per_week"],
    )
    day = next((d for d in plan["days"] if d["day_number"] == day_number), None)
    if not day:
        return redirect(url_for("index"))
    return render_template("workout.html", day=day, profile=profile)


@app.route("/api/complete_workout", methods=["POST"])
def complete_workout():
    uid = session["user_id"]
    data = request.get_json() or {}
    database.log_workout(
        user_id=uid,
        day_name=data.get("day_name", "Workout"),
        day_number=int(data.get("day_number", 0)),
        exercises=data.get("exercises", []),
        duration_seconds=int(data.get("duration_seconds", 0)),
    )
    return jsonify({"ok": True})


@app.route("/api/log_weight", methods=["POST"])
def log_weight():
    uid = session["user_id"]
    data = request.get_json() or {}
    try:
        weight = float(data.get("weight"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid weight"}), 400
    database.log_weight(uid, weight)
    return jsonify({"ok": True})


@app.route("/exercises")
def exercises():
    uid = session["user_id"]
    profile = database.get_profile(uid) or {}
    limitations = profile.get("limitations", [])
    all_ex = all_exercises_with_status(limitations)
    return render_template("exercises.html", exercises=all_ex, profile=profile)


@app.route("/progress")
def progress():
    uid = session["user_id"]
    profile = database.get_profile(uid) or {}
    weights = database.get_weight_history(uid)
    history = database.get_workout_history(uid)
    stats = database.get_stats(uid)
    return render_template(
        "progress.html",
        profile=profile, weights=weights, history=history, stats=stats,
    )


# ---------- PWA assets -----------------------------------------------------

@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


@app.route("/sw.js")
def service_worker():
    response = app.send_static_file("js/sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
