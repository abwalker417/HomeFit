#!/usr/bin/env python3
"""Cron entry point for push notifications.

Usage:
    python3 push_send.py tick     # every 30 min: fires each push at the USER's
                                  # local time (profile.timezone) — digest Mon
                                  # 9:00, daily brief Tue-Sun 9:00, streak 18:30,
                                  # nudges 16:00 + 20:00, memory 23:30. Deduped
                                  # per user/kind/local-day via push_log.
    python3 push_send.py tick --dry-run   # show what would fire, send nothing

Legacy one-shot commands (manual use / testing — send immediately, no window):
    python3 push_send.py digest | daily | streak | nudge | memory
"""

import sys

import database
import push_notify


def send_digest_for(uid):
    import digest_service
    try:
        digest = digest_service.get_or_generate(uid)
    except Exception as e:
        print(f"digest failed for user {uid}: {e}")
        return
    if not digest:
        return
    first = next(
        (l.lstrip("- ").strip() for l in digest.splitlines() if l.strip()),
        "Your weekly digest is ready.",
    )
    n = push_notify.send_to_user(uid, "APEX Weekly Digest", first, url="/")
    print(f"user {uid}: digest push sent to {n} device(s)")


def send_daily_brief_for(uid):
    import daily_service
    try:
        # force a fresh pull at send time so the brief uses last night's sleep,
        # not a brief cached earlier (e.g. by an early dashboard open)
        brief = daily_service.get_or_generate(uid, force=True)
    except Exception as e:
        print(f"daily brief failed for user {uid}: {e}")
        return
    if not brief:
        return
    n = push_notify.send_to_user(uid, "APEX Daily Brief", brief.strip(), url="/")
    print(f"user {uid}: daily brief sent to {n} device(s)")


def send_streak_reminder_for(uid):
    from datetime import timedelta
    today = database.user_now(uid).date()
    days_left = 7 - today.weekday()  # includes today
    monday = (today - timedelta(days=today.weekday())).isoformat()
    profile = database.get_profile(uid) or {}
    # Away mode (travel/vacation/sick): never nag on a paused day, and
    # paused days this week shrink the target — same math as the dashboard.
    pause = database.current_week_pause(uid)
    if pause["today_paused"]:
        return
    target = max(0, (profile.get("days_per_week") or 4) - pause["paused_days"])
    if target == 0:
        return  # fully-away week — nothing owed
    usable_days_left = max(0, days_left - pause["remaining_paused"])
    # Workout days this week = HomeFit sessions + counting Apple workouts
    # (golf, long sessions) — same source of truth as the dashboard/streak.
    week_dates = database.workout_day_dates(uid, since_iso=monday)
    if today.isoformat() in week_dates:
        return  # already trained today
    done = len(week_dates)
    if done >= target:
        return  # weekly target already met
    # Last chance: still achievable with today, lost without it
    last_chance = (done + usable_days_left >= target
                   and done + usable_days_left - 1 < target)
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


def send_nudge_for(uid, ai_ok=None):
    """One proactive contextual nudge, deduped per type/local-day (nudge_log).
    Triggers are data-driven (nudge_service); APEX phrases them personably."""
    import coach
    import nudge_service
    if ai_ok is None:
        ai_ok = coach.is_available()
    today = database.user_today_iso(uid)
    if database.get_active_pause(uid):
        return  # away mode — no train/food nudges while traveling or sick
    try:
        nudge = nudge_service.evaluate(uid)
    except Exception as e:
        print(f"nudge eval failed for user {uid}: {e}")
        return
    if not nudge:
        return
    if database.nudge_already_sent(uid, nudge["type"], today):
        print(f"user {uid}: {nudge['type']} already sent today")
        return
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


def update_memory_for(uid, name=None):
    """Let APEX update its persistent memory of one user from the day's
    conversation. Skips users with no new chat since the last update."""
    import coach
    messages = database.get_apex_chat(uid)
    if not messages:
        return
    chat_ts = database.get_apex_chat_updated_at(uid)
    mem_ts = database.get_apex_memory_updated_at(uid)
    if chat_ts and mem_ts and chat_ts <= mem_ts:
        print(f"user {uid}: no new conversation, memory unchanged")
        return
    try:
        profile = database.get_profile(uid) or {}
        if name:
            profile.setdefault("name", name)
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


# ── tick: per-user local-time scheduling ─────────────────────────────────────
# (kind, window-start minutes into the user's local day, weekday filter).
# The cron fires every 30 min; a window is [start, start+30) so each kind
# fires exactly once per user-local day (claimed in push_log). Half/quarter-
# hour timezones (e.g. +5:45) still get exactly one tick inside each window.

WINDOWS = [
    ("digest",     9 * 60,       lambda wd: wd == 0),   # Monday 9:00
    ("daily",      9 * 60,       lambda wd: wd != 0),   # Tue-Sun 9:00
    ("nudge_1600", 16 * 60,      None),
    ("streak",     18 * 60 + 30, None),
    ("nudge_2000", 20 * 60,      None),
    ("memory",     23 * 60 + 30, None),
]


def tick(dry_run=False):
    users = {u["id"]: u.get("name") for u in database.list_users()}
    push_ids = set(database.get_push_user_ids())
    fired = 0
    for uid, name in users.items():
        lt = database.user_now(uid)
        minutes = lt.hour * 60 + lt.minute
        for kind, start, day_ok in WINDOWS:
            if not (start <= minutes < start + 30):
                continue
            if day_ok and not day_ok(lt.weekday()):
                continue
            # memory runs for everyone with chat; the rest only for push users
            if kind != "memory" and uid not in push_ids:
                continue
            if dry_run:
                print(f"[dry-run] user {uid} ({name}): would fire '{kind}' "
                      f"(local {lt.strftime('%a %H:%M')}, tz {database.get_user_timezone(uid)})")
                fired += 1
                continue
            if not database.claim_push_send(uid, kind, lt.date().isoformat()):
                continue  # already fired this user-local day
            fired += 1
            if kind == "digest":
                send_digest_for(uid)
            elif kind == "daily":
                send_daily_brief_for(uid)
            elif kind == "streak":
                send_streak_reminder_for(uid)
            elif kind.startswith("nudge"):
                send_nudge_for(uid)
            elif kind == "memory":
                update_memory_for(uid, name)
    if fired == 0:
        print(f"tick: nothing due")


# ── legacy one-shot batch commands (manual use) ──────────────────────────────

def send_digests():
    for uid in database.get_push_user_ids():
        send_digest_for(uid)


def send_daily_briefs():
    for uid in database.get_push_user_ids():
        send_daily_brief_for(uid)


def send_streak_reminders():
    for uid in database.get_push_user_ids():
        send_streak_reminder_for(uid)


def send_nudges():
    import coach
    ai_ok = coach.is_available()
    for uid in database.get_push_user_ids():
        send_nudge_for(uid, ai_ok=ai_ok)


def update_memories():
    for u in database.list_users():
        update_memory_for(u["id"], u.get("name"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "tick":
        tick(dry_run="--dry-run" in sys.argv)
    elif cmd == "digest":
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
