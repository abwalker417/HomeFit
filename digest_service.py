"""Weekly digest orchestration — shared by the /api/weekly-digest route and
the push_send.py cron so both produce identical digests (cached per ISO week)."""

import coach
import database


def get_or_generate(user_id):
    """Return this week's digest, generating and caching it if needed. None if AI offline."""
    cached = database.get_weekly_digest(user_id)
    if cached:
        return cached
    if not coach.is_available():
        return None
    coaching_data = database.get_coaching_context(user_id)
    digest = coach.generate_weekly_digest(coaching_data)
    database.save_weekly_digest(user_id, digest)
    return digest
