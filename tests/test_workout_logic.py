"""Deterministic tests for workout_logic.filter_exercises and build_workout.
Run with: cd HomeFit && .venv/bin/python -m pytest tests/
"""

import pytest
import workout_logic

# Small deterministic fixture of exercises
SAMPLE_EXERCISES = [
    {
        "id": "dumbbell_curl",
        "name": "Dumbbell Curl",
        "equipment": ["dumbbells"],
        "contraindications": [],
        "category": "upper",
        "muscle_groups": ["arms"],
    },
    {
        "id": "bench_press",
        "name": "Bench Press",
        "equipment": ["bench"],
        "contraindications": ["bad_knees"],
        "category": "upper",
        "muscle_groups": ["chest"],
    },
    {
        "id": "squat",
        "name": "Squat",
        "equipment": ["none"],
        "contraindications": ["bad_knees"],
        "category": "legs",
        "muscle_groups": ["legs"],
    },
]
SAMPLE_IDS = {ex["id"] for ex in SAMPLE_EXERCISES}

def test_filter_equipment_no_available_keeps_all():
    profile = {"equipment": [], "custom_equipment": [], "limitations": [], "ignored_exercises": []}
    filtered = workout_logic.filter_exercises(SAMPLE_EXERCISES, profile)
    filtered_ids = {ex["id"] for ex in filtered}
    # All sample exercises should be present when no equipment is specified
    assert filtered_ids == SAMPLE_IDS

def test_filter_equipment_with_available_limits_to_intersection():
    profile = {"equipment": ["dumbbells"], "custom_equipment": [], "limitations": [], "ignored_exercises": []}
    filtered = workout_logic.filter_exercises(SAMPLE_EXERCISES, profile)
    filtered_ids = {ex["id"] for ex in filtered}
    # Only exercises that are bodyweight/none or require dumbbells should remain
    assert filtered_ids == {"dumbbell_curl", "squat"}

def test_filter_limitations_excludes_contraindicated():
    profile = {"equipment": [], "custom_equipment": [], "limitations": ["knee pain"], "ignored_exercises": []}
    filtered = workout_logic.filter_exercises(SAMPLE_EXERCISES, profile)
    filtered_ids = {ex["id"] for ex in filtered}
    # Exercises with "bad_knees" contraindication should be excluded
    assert filtered_ids == {"dumbbell_curl"}

def test_filter_selected_muscles_primary_over_target():
    # A precise arm focus must not accept a chest movement merely because both
    # live in the broad "upper" category.
    profile = {"equipment": [], "custom_equipment": [], "limitations": [], "ignored_exercises": []}
    filtered = workout_logic.filter_exercises(SAMPLE_EXERCISES, profile, selected_muscles=["arms"])
    filtered_ids = {ex["id"] for ex in filtered}
    assert filtered_ids == {"dumbbell_curl"}

def test_build_workout_returns_valid_structure(monkeypatch):
    # Monkeypatch load_exercises to use SAMPLE_EXERCISES
    monkeypatch.setattr(workout_logic, "load_exercises", lambda: SAMPLE_EXERCISES)
    profile = {
        "equipment": [],
        "custom_equipment": [],
        "limitations": [],
        "ignored_exercises": [],
        "fitness_level": "beginner",
        "current_weight": 180,
        "goal_weight": 170,
    }
    result = workout_logic.build_workout(profile, day_label="Full Body")
    # Verify top-level keys
    assert set(result.keys()) == {"label", "goal", "focus", "equipment_focus", "exercises"}
    # Goal should be "lose" and label "Full Body"
    assert result["goal"] == "lose"
    assert result["label"] == "Full Body"
    # All returned exercise IDs must be from the sample set
    for ex in result["exercises"]:
        assert ex["id"] in SAMPLE_IDS
