"""Weekly digest orchestration — shared by the /api/weekly-digest route and
the push_send.py cron so both produce identical digests (cached per ISO week)."""

import coach
import database
import sparky_sync


def _append_data_check(digest, coaching_data):
    """Tack a nutrition sanity-check line onto the digest when Sparky data looks off."""
    profile = coaching_data.get("profile") or {}
    if not profile.get("sparky_sync"):
        return digest
    try:
        issues = sparky_sync.scan_nutrition_issues(days=7, api_key=profile.get("sparky_api_key"))
    except Exception:
        issues = []
    if issues:
        digest = digest.rstrip() + "\n- Data check: " + "; ".join(issues[:3])
    return digest


def get_or_generate(user_id):
    """Return this week's digest, generating and caching it if needed. None if AI offline."""
    cached = database.get_weekly_digest(user_id)
    if cached:
        return cached
    if not coach.is_available():
        return None
    coaching_data = database.get_coaching_context(user_id)
    digest = coach.generate_weekly_digest(coaching_data)
    digest = _append_data_check(digest, coaching_data)
    database.save_weekly_digest(user_id, digest)
    return digest
