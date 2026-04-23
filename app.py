"""Flask entrypoint for HomeFit (multi-user v2).

Run locally:
    pip install -r requirements.txt
    python app.py

Each user has their own profile, plan, weight log, and workout history.
Open the site -> pick a profile (or create one) -> train.

Security env vars (all optional, suitable defaults for home-LAN use):
    HOMEFIT_TRUSTED_NETS
        Comma-separated CIDRs allowed to CREATE new profiles.
        Default: 192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8
        Lock this down when exposing HomeFit through a reverse proxy, e.g.
        HOMEFIT_TRUSTED_NETS=192.168.68.0/24

    HOMEFIT_TRUSTED_PROXIES
        Comma-separated CIDRs whose X-Forwarded-For header we trust. Set this
        to the IP of your reverse proxy (e.g. NGINX Proxy Manager). Leave
        empty when there is no proxy in front.

    HOMEFIT_SESSION_SECURE
        Set to "1" to mark the session cookie Secure (HTTPS only). Use when
        the proxy serves HTTPS.
"""

import ipaddress
import os
from collections import defaultdict
from threading import Lock
from time import time

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, session, abort,
)

import database
from workout_logic import generate_plan, all_exercises_with_status, VALID_LIMITATIONS, VALID_EQUIPMENT

app = Flask(__name__)

# Init DB and persistent session secret
database.init_db()
app.secret_key = database.load_or_create_secret_key()

# Harden the session cookie. SameSite=Lax is always safe; Secure only when
# explicitly opted in (setting it on plain-HTTP breaks the cookie).
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("HOMEFIT_SESSION_SECURE", "0") == "1",
)

PUBLIC_ENDPOINTS = {
    "profiles", "profile_new", "profile_switch", "profile_unlock",
    "profile_switch_out",
    "manifest", "service_worker", "static",
}


# ---------- trusted-network gate -------------------------------------------

def _parse_net_list(raw):
    nets = []
    if not raw:
        return nets
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            app.logger.warning("Ignoring invalid CIDR in trust list: %r", item)
    return nets


TRUSTED_NETS = _parse_net_list(os.environ.get(
    "HOMEFIT_TRUSTED_NETS",
    "192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8",
))
TRUSTED_PROXIES = _parse_net_list(os.environ.get("HOMEFIT_TRUSTED_PROXIES", ""))


def _ip_in(ip_str, networks):
    try:
        ip = ipaddress.ip_address(ip_str)
    except (ValueError, TypeError):
        return False
    return any(ip in n for n in networks)


def _client_ip():
    """Return the client's real IP.

    If the request arrived from a trusted proxy (per HOMEFIT_TRUSTED_PROXIES),
    honour the left-most entry of X-Forwarded-For. Otherwise trust only the
    direct peer address so an attacker can't spoof the header.
    """
    remote = request.remote_addr or ""
    if TRUSTED_PROXIES and _ip_in(remote, TRUSTED_PROXIES):
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return remote


def on_trusted_network():
    return _ip_in(_client_ip(), TRUSTED_NETS)


# ---------- PIN rate-limit (in-memory, per-process) ------------------------

PIN_FAIL_WINDOW_SEC = 15 * 60   # how long a failed attempt is remembered
PIN_FAIL_THRESHOLD = 5          # fails within the window trigger lockout
PIN_LOCKOUT_SEC = 10 * 60       # length of the lockout

_pin_fails = defaultdict(list)
_pin_fails_lock = Lock()


def _prune_and_count(user_id, now):
    fails = [t for t in _pin_fails[user_id] if now - t < PIN_FAIL_WINDOW_SEC]
    _pin_fails[user_id] = fails
    return fails


def pin_lockout_remaining(user_id):
    """Seconds left on a PIN lockout for this user, or 0 if not locked."""
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
    can_create = on_trusted_network()
    if not users:
        # First run â€” nobody exists yet.
        if can_create:
            return redirect(url_for("profile_new"))
        # Public access before setup â€” show a locked screen.
        return render_template("blocked.html",
                               reason="No profiles exist yet. Connect from the "
                                      "local network to set up the first profile.",
                               client_ip=_client_ip()), 403
    return render_template("profiles.html", users=users, can_create=can_create)


@app.route("/profiles/new", methods=["GET", "POST"])
def profile_new():
    if not on_trusted_network():
        return render_template("blocked.html",
                               reason="Creating profiles is only allowed from "
                                      "the local network.",
                               client_ip=_client_ip()), 403
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        emoji = request.form.get("emoji", "ðŸ’ª").strip() or "ðŸ’ª"
        pin = request.form.get("pin", "").strip()
        pin_confirm = request.form.get("pin_confirm", "").strip()

        error = None
        if not name:
            error = "Please enter a name."
        elif pin and not pin.isdigit():
            error = "PIN must be digits only."
        elif pin and not (4 <= len(pin) <= 8):
            error = "PIN must be 4â€“8 digits."
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
    lockout = pin_lockout_remaining(user_id)
    if request.method == "POST":
        if lockout > 0:
            error = _lockout_message(lockout)
        else:
            pin = request.form.get("pin", "").strip()
            if database.verify_pin(user_id, pin):
                clear_pin_fails(user_id)
                session["user_id"] = user_id
                return redirect(url_for("index"))
            record_pin_fail(user_id)
            lockout = pin_lockout_remaining(user_id)
            error = _lockout_message(lockout) if lockout > 0 else "Wrong PIN."
    elif lockout > 0:
        error = _lockout_message(lockout)
    return render_template(
        "profile_unlock.html", user=user, error=error,
        locked=lockout > 0,
    )


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
                error = "PIN must be 4â€“8 digits."
            elif new_pin != confirm:
                error = "PINs don't match."

        if error:
            return render_template("profile_edit.html", user=user, profile_data=database.get_profile(user_id) or {}, error=error)

        pin_param = False
        if pin_action == "clear":
            pin_param = None
        elif pin_action == "set":
            pin_param = new_pin

        try:
            database.update_user(user_id, name=name, emoji=emoji, pin=pin_param)
        except Exception as e:
            return render_template("profile_edit.html", user=user, profile_data=database.get_profile(user_id) or {}, error=str(e))
        return redirect(url_for("index"))

    return render_template("profile_edit.html", user=user, profile_data=database.get_profile(user_id) or {}, error=None)


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
        profile.get("equipment", []),
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
        equipment = request.form.getlist("equipment")
        equipment = [e for e in equipment if e in VALID_EQUIPMENT]
        days_per_week = int(request.form.get("days_per_week", 4))
        database.save_profile(
            uid, current_weight, goal_weight, fitness_level,
            limitations, days_per_week, equipment,
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
        profile.get("equipment", []),
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
    equipment = profile.get("equipment", [])
    all_ex = all_exercises_with_status(limitations, equipment)
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
