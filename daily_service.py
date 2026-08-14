"""Daily Coach brief orchestration — shared by the dashboard (/api/daily-brief)
and the push_send.py cron so both show the same sleep-aware brief, cached per day."""

import coach
import database


def get_or_generate(user_id, force=False):
    """Return today's daily brief, generating and caching it if needed. None if AI offline.

    force=True regenerates from current data and overwrites the cache — used by the
    9 AM push so the brief reflects last night's sleep at send time, not whatever was
    cached earlier (e.g. an early dashboard open before sleep synced)."""
    if not force:
        cached = database.get_daily_brief(user_id)
        if cached:
            return cached
    if not coach.is_available():
        return database.get_daily_brief(user_id)  # fall back to any cache if AI offline
    coaching_data = database.get_coaching_context(user_id)
    brief = coach.generate_daily_brief(coaching_data)
    if brief:
        brief = brief.strip()
        database.save_daily_brief(user_id, brief)
    return brief
