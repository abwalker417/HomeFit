#!/usr/bin/env python3
"""Cron entry point for push notifications.

Usage:
    python3 push_send.py digest   # Monday morning: weekly digest to subscribers
    python3 push_send.py daily    # Tue-Sun morning: short APEX brief (yesterday
                                  # recap + today's focus)
    python3 push_send.py streak   # evening: remind anyone who hasn't trained yet
                                  # and is still short of the weekly target
"""

import sys

import database
import push_notify


def send_digests():
    import digest_service
    user_ids = {s["user_id"] for s in database.get_push_subscriptions()}
    for uid in sorted(user_ids):
        try:
            digest = digest_service.get_or_generate(uid)
        except Exception as e:
            print(f"digest failed for user {uid}: {e}")
            continue
        if not digest:
            continue
        first = next(
            (l.lstrip("- ").strip() for l in digest.splitlines() if l.strip()),
            "Your weekly digest is ready.",
        )
        n = push_notify.send_to_user(uid, "APEX Weekly Digest", first, url="/")
        print(f"user {uid}: digest push sent to {n} device(s)")


def send_daily_briefs():
    import coach
    if not coach.is_available():
        print("daily brief skipped: AI offline")
        return
    user_ids = {s["user_id"] for s in database.get_push_subscriptions()}
    for uid in sorted(user_ids):
        try:
            coaching_data = database.get_coaching_context(uid)
            brief = coach.generate_daily_brief(coaching_data)
        except Exception as e:
            print(f"daily brief failed for user {uid}: {e}")
            continue
        if not brief:
            continue
        n = push_notify.send_to_user(uid, "APEX Daily Brief", brief.strip(), url="/")
        print(f"user {uid}: daily brief sent to {n} device(s)")


def send_streak_reminders():
    from datetime import date, timedelta
    today = date.today()
    days_left = 7 - today.weekday()  # includes today
    monday = (today - timedelta(days=today.weekday())).isoformat()
    user_ids = {s["user_id"] for s in database.get_push_subscriptions()}
    for uid in sorted(user_ids):
        profile = database.get_profile(uid) or {}
        target = profile.get("days_per_week") or 4
        history = database.get_workout_history(uid, limit=30)
        week_dates = {(w.get("completed_at") or "")[:10] for w in history
                      if (w.get("completed_at") or "")[:10] >= monday}
        week_dates.discard("")
        if today.isoformat() in week_dates:
            continue  # already trained today
        done = len(week_dates)
        if done >= target:
            continue  # weekly target already met
        # Last chance: still achievable with today, lost without it
        last_chance = (done + days_left >= target
                       and done + days_left - 1 < target)
        week_streak = database.get_week_streak(uid, target)
        if last_chance:
            title = "Streak at risk"
            body = f"Today is your last chance to hit {target} workouts this week"
            if week_streak >= 2:
                body += f" and keep your {week_streak}-week streak"
            body += "."
        else:
            title = "Time to train"
            body = f"No workout logged today — you're at {done} of {target} this week."
        n = push_notify.send_to_user(uid, title, body, url="/")
        print(f"user {uid}: streak push sent to {n} device(s)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "digest":
        send_digests()
    elif cmd == "daily":
        send_daily_briefs()
    elif cmd == "streak":
        send_streak_reminders()
    else:
        print(__doc__)
        sys.exit(1)
