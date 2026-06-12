#!/usr/bin/env python3
"""Cron entry point for push notifications.

Usage:
    python3 push_send.py digest   # Monday morning: weekly digest to subscribers
    python3 push_send.py streak   # evening: warn when today is the last chance
                                  # to hit the weekly workout target
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
        # Last chance: still achievable with today, lost without it
        must_train = (done < target
                      and done + days_left >= target
                      and done + days_left - 1 < target)
        if not must_train:
            continue
        week_streak = database.get_week_streak(uid, target)
        body = f"Today is your last chance to hit {target} workouts this week"
        if week_streak >= 2:
            body += f" and keep your {week_streak}-week streak"
        body += "."
        n = push_notify.send_to_user(uid, "Streak at risk", body, url="/")
        print(f"user {uid}: streak push sent to {n} device(s)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "digest":
        send_digests()
    elif cmd == "streak":
        send_streak_reminders()
    else:
        print(__doc__)
        sys.exit(1)
