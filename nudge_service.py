"""Proactive APEX nudges — decide (cheaply, deterministically) whether a user
needs a contextual nudge right now, then let APEX phrase it personably.

Triggers are evaluated from data (no LLM needed to DECIDE — only to PHRASE), so
this is cheap and never spammy. One nudge per run, deduped per type per day via
nudge_log. Driven by push_send.py's `nudge` cron (run late afternoon/evening).
"""
from datetime import date, datetime

import database

# Priority order — the first firing trigger wins for a given run.
PRIORITY = ["untrained_plan_day", "dinner_reminder", "protein_low"]

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
              "Friday", "Saturday", "Sunday"]


def _today_totals(user_id):
    foods = database.get_food_log_today(user_id)
    return {
        "calories": sum(f.get("calories") or 0 for f in foods),
        "protein_g": round(sum(f.get("protein_g") or 0 for f in foods)),
        "items": len(foods),
    }


def _trained_today(user_id, today_iso):
    for w in database.get_workout_history(user_id, limit=10):
        if (w.get("completed_at") or "").startswith(today_iso):
            return True
    return False


def _plan_today(user_id, weekday):
    data = database.get_apex_plan(user_id)
    if not data or not data.get("plan"):
        return None
    plan = data["plan"]
    if weekday >= len(plan):
        return None
    return plan[weekday]


def evaluate(user_id, now=None):
    """Return {'type', 'facts'} for the highest-priority firing trigger, or None.

    Time-aware: meal nudges only fire in the evening, the training nudge from
    mid-afternoon. The cron's run time also gates this."""
    now = now or datetime.now()
    hour = now.hour
    today_iso = now.date().isoformat()
    weekday = now.weekday()

    goal = database.get_nutrition_goal(user_id) or {}
    goal_cals = goal.get("calories") or 0
    goal_protein = goal.get("protein_g") or 0
    totals = _today_totals(user_id)

    candidates = {}

    # 1) Planned training day, not trained yet, it's afternoon+.
    day = _plan_today(user_id, weekday)
    if day and not day.get("rest") and hour >= 14 and not _trained_today(user_id, today_iso):
        candidates["untrained_plan_day"] = {
            "day_name": day.get("name") or _DAY_NAMES[weekday],
            "exercises": [e.get("name", e.get("id", "")) for e in day.get("exercises", [])][:4],
        }

    # 2) Evening + well under calorie goal (likely hasn't logged dinner).
    if goal_cals and hour >= 17 and totals["calories"] < 0.55 * goal_cals:
        candidates["dinner_reminder"] = {
            "eaten": totals["calories"], "goal": goal_cals,
            "remaining": max(0, goal_cals - totals["calories"]),
            "logged_meals": totals["items"],
        }

    # 3) Evening + ate enough calories but low on protein.
    if (goal_protein and hour >= 17 and totals["protein_g"] < 0.5 * goal_protein
            and totals["calories"] >= 0.55 * (goal_cals or 1)):
        candidates["protein_low"] = {
            "protein": totals["protein_g"], "goal": goal_protein,
            "remaining": max(0, goal_protein - totals["protein_g"]),
        }

    for t in PRIORITY:
        if t in candidates:
            return {"type": t, "facts": candidates[t]}
    return None


def fallback_text(nudge):
    """Deterministic phrasing if APEX is offline — never blocks a nudge."""
    t, f = nudge["type"], nudge["facts"]
    if t == "untrained_plan_day":
        return ("Time to train", f"{f['day_name']} is still on today's plan — no workout logged yet.")
    if t == "dinner_reminder":
        return ("Fuel up", f"You're at {f['eaten']} of {f['goal']} cal — {f['remaining']} left. Don't forget dinner.")
    if t == "protein_low":
        return ("Protein check", f"Only {f['protein']}g protein so far — about {f['remaining']}g to your goal.")
    return ("HomeFit", "Check in with APEX.")
