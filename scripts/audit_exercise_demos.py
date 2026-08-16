#!/usr/bin/env python3
"""Bulk-audit BuiltHere demo candidates with the configured vision model.

Run this only on the BuiltHere server, where it uses the persistent production
database and the owner-configured OpenAI-compatible provider:

    /opt/homefit/current/.venv/bin/python scripts/audit_exercise_demos.py

It is intentionally conservative: an error or uncertain response records
needs_review, so a questionable demo is never left visible by accident.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database
import exercise_quality
from workout_logic import load_exercises


def main():
    parser = argparse.ArgumentParser(description="Verify BuiltHere exercise demos.")
    parser.add_argument("--limit", type=int, help="Verify at most this many demos.")
    parser.add_argument("--include-held", action="store_true", help="Recheck demos already held for review.")
    parser.add_argument("--retry-errors", action="store_true", help="Retry only entries held by a verifier error.")
    args = parser.parse_args()

    database.init_db()
    with (ROOT / "data" / "exercise_animations.json").open() as handle:
        demos = json.load(handle)
    existing = database.get_exercise_demo_review_overrides()
    exercises = [exercise for exercise in load_exercises() if exercise["id"] in demos]
    if args.retry_errors:
        exercises = [exercise for exercise in exercises
                     if (existing.get(exercise["id"], {}).get("note") or "").startswith("Verifier error:")]
    elif not args.include_held:
        exercises = [exercise for exercise in exercises if exercise["id"] not in existing]
    if args.limit:
        exercises = exercises[:args.limit]

    approved = held = errors = 0
    total = len(exercises)
    for index, exercise in enumerate(exercises, start=1):
        exercise_id = exercise["id"]
        try:
            result = exercise_quality.verify_demo(exercise, demos[exercise_id])
            database.save_exercise_demo_review(
                exercise_id, result["status"], result["reason"], result.get("instructions") or None
            )
            approved += result["status"] == "approved"
            held += result["status"] != "approved"
            print(f"[{index}/{total}] {exercise['name']}: {result['status']} ({result['confidence']:.0%})")
        except Exception as exc:
            errors += 1
            held += 1
            database.save_exercise_demo_review(exercise_id, "needs_review", f"Verifier error: {exc}")
            print(f"[{index}/{total}] {exercise['name']}: needs_review (verifier error)")

    print(f"Complete: {approved} approved, {held} held, {errors} verifier errors.")


if __name__ == "__main__":
    main()
