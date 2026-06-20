#!/usr/bin/env python3
"""Cron entry point for push notifications.

Usage:
    python3 push_send.py digest   # Monday morning: weekly digest to subscribers
    python3 push_send.py daily    # Tue-Sun morning: short APEX brief (yesterday
                                  # recap + today's focus)
    python3 push_send.py streak   # evening: remind anyone who hasn't trained yet
                                  # and is still short of the weekly target
    python3 push_send.py nudge    # late afternoon/evening: one proactive,
                                  # data-driven APEX nudge (train / log dinner /
                                  # protein), personable and deduped per day
    python3 push_send.py memory   # nightly: APEX updates its persistent memory
                                  # of each user from the day's conversation
"""

import sys

import database
import push_notify


def send_digests():
    import digest_service
    for uid in database.get_push_user_ids():
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
    import daily_service
    for uid in database.get_push_user_ids():
        try:
            brief = daily_service.get_or_generate(uid)
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
    for uid in database.get_push_user_ids():
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


def send_nudges():
    """Proactive contextual nudges: one per user per run, deduped per type/day.
    Triggers are data-driven (nudge_service); APEX phrases them personably."""
    import coach
    import nudge_service
    from datetime import date
    today = date.today().isoformat()
    ai_ok = coach.is_available()
    for uid in database.get_push_user_ids():
        try:
            nudge = nudge_service.evaluate(uid)
        except Exception as e:
            print(f"nudge eval failed for user {uid}: {e}")
            continue
        if not nudge:
            continue
        if database.nudge_already_sent(uid, nudge["type"], today):
            print(f"user {uid}: {nudge['type']} already sent today")
            continue
        title = body = None
        if ai_ok:
            try:
                ctx = database.get_coaching_context(uid)
                out = coach.generate_nudge(ctx, nudge["type"], nudge["facts"])
                if out:
                    title, body = out
            except Exception as e:
                print(f"nudge phrasing failed for user {uid}: {e}")
        if not body:
            title, body = nudge_service.fallback_text(nudge)
        n = push_notify.send_to_user(uid, title, body, url="/")
        database.record_nudge(uid, nudge["type"], today, body)
        print(f"user {uid}: nudge '{nudge['type']}' sent to {n} device(s): {body}")


def update_memories():
    """Nightly: let APEX update its persistent memory of each user from the
    day's conversation. Skips users with no new chat since the last update."""
    import coach
    for u in database.list_users():
        uid = u["id"]
        messages = database.get_apex_chat(uid)
        if not messages:
            continue
        chat_ts = database.get_apex_chat_updated_at(uid)
        mem_ts = database.get_apex_memory_updated_at(uid)
        if chat_ts and mem_ts and chat_ts <= mem_ts:
            print(f"user {uid}: no new conversation, memory unchanged")
            continue
        try:
            profile = database.get_profile(uid) or {}
            profile.setdefault("name", u.get("name"))
            existing = database.get_apex_memory(uid)
            updated = coach.update_memory(existing, messages, profile)
            if updated and updated != existing:
                database.save_apex_memory(uid, updated)
                print(f"user {uid}: memory updated ({len(updated)} chars)")
            else:
                database.save_apex_memory(uid, existing)  # bump timestamp
                print(f"user {uid}: memory unchanged")
        except Exception as e:
            print(f"memory update failed for user {uid}: {e}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "digest":
        send_digests()
    elif cmd == "daily":
        send_daily_briefs()
    elif cmd == "streak":
        send_streak_reminders()
    elif cmd == "nudge":
        send_nudges()
    elif cmd == "memory":
        update_memories()
    else:
        print(__doc__)
        sys.exit(1)
