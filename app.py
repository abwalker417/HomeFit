"""Flask entrypoint for the self-hosted home workout app.

Run locally:
    pip install -r requirements.txt
    python app.py

Then open http://<your-machine-ip>:5000 on your iPhone (same wifi).
Tap the Share button -> Add to Home Screen for a PWA experience.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
import database
from workout_logic import generate_plan, all_exercises_with_status, VALID_LIMITATIONS

app = Flask(__name__)

# Initialize DB on import
database.init_db()


@app.route("/")
def index():
    profile = database.get_profile()
    if not profile:
        return redirect(url_for("onboarding"))
    plan = generate_plan(
        profile["current_weight"],
        profile["goal_weight"],
        profile["fitness_level"],
        profile["limitations"],
        profile["days_per_week"],
    )
    stats = database.get_stats()
    return render_template("dashboard.html", profile=profile, plan=plan, stats=stats)


@app.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    if request.method == "POST":
        current_weight = float(request.form["current_weight"])
        goal_weight = float(request.form["goal_weight"])
        fitness_level = request.form.get("fitness_level", "beginner")
        limitations = request.form.getlist("limitations")
        # Sanitize limitations against known set
        limitations = [l for l in limitations if l in VALID_LIMITATIONS]
        days_per_week = int(request.form.get("days_per_week", 4))
        database.save_profile(
            current_weight, goal_weight, fitness_level, limitations, days_per_week
        )
        return redirect(url_for("index"))
    profile = database.get_profile() or {}
    return render_template("onboarding.html", profile=profile)


@app.route("/workout/<int:day_number>")
def workout(day_number):
    profile = database.get_profile()
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
    data = request.get_json() or {}
    database.log_workout(
        day_name=data.get("day_name", "Workout"),
        day_number=int(data.get("day_number", 0)),
        exercises=data.get("exercises", []),
        duration_seconds=int(data.get("duration_seconds", 0)),
    )
    return jsonify({"ok": True})


@app.route("/api/log_weight", methods=["POST"])
def log_weight():
    data = request.get_json() or {}
    try:
        weight = float(data.get("weight"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid weight"}), 400
    database.log_weight(weight)
    return jsonify({"ok": True})


@app.route("/exercises")
def exercises():
    profile = database.get_profile() or {}
    limitations = profile.get("limitations", [])
    all_ex = all_exercises_with_status(limitations)
    return render_template("exercises.html", exercises=all_ex, profile=profile)


@app.route("/progress")
def progress():
    profile = database.get_profile() or {}
    weights = database.get_weight_history()
    history = database.get_workout_history()
    stats = database.get_stats()
    return render_template(
        "progress.html",
        profile=profile,
        weights=weights,
        history=history,
        stats=stats,
    )


@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


@app.route("/sw.js")
def service_worker():
    # Serve from root so it can control the whole site
    response = app.send_static_file("js/sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


if __name__ == "__main__":
    # 0.0.0.0 so it's reachable from your iPhone on the same network
    app.run(host="0.0.0.0", port=5000, debug=True)
