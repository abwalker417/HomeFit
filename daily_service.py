"""Daily APEX brief orchestration — shared by the dashboard (/api/daily-brief)
and the push_send.py cron so both show the same sleep-aware brief, cached per day."""

import coach
import database


def get_or_generate(user_id):
    """Return today's daily brief, generating and caching it if needed. None if AI offline."""
    cached = database.get_daily_brief(user_id)
    if cached:
        return cached
    if not coach.is_available():
        return None
    coaching_data = database.get_coaching_context(user_id)
    brief = coach.generate_daily_brief(coaching_data)
    if brief:
        brief = brief.strip()
        database.save_daily_brief(user_id, brief)
    return brief
