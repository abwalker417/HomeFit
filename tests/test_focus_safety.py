import app as homefit_app
import workout_logic


def exercise(ex_id, name, category, equipment="bodyweight", difficulty=1):
    return {
        "id": ex_id,
        "name": name,
        "category": category,
        "muscle_groups": ["full_body"],
        "equipment": equipment,
        "difficulty": difficulty,
        "default_sets": 3,
        "default_reps": 10,
    }


def profile():
    return {
        "equipment": [],
        "custom_equipment": [],
        "limitations": [],
        "ignored_exercises": [],
        "fitness_level": "beginner",
        "current_weight": 200,
        "goal_weight": 180,
    }


def test_inferred_arm_focus_does_not_admit_other_upper_or_lower_moves():
    library = [
        exercise("db_bicep_curl", "Dumbbell Bicep Curl", "upper", "dumbbells"),
        exercise("db_chest_press", "Dumbbell Chest Press", "upper", "dumbbells"),
        exercise("db_row", "Dumbbell Row", "upper", "dumbbells"),
        exercise("goblet_squat", "Goblet Squat", "legs", "dumbbells"),
    ]

    result = workout_logic.filter_exercises(library, profile(), selected_muscles=["arms"])

    assert [item["id"] for item in result] == ["db_bicep_curl"]


def test_upper_focus_includes_upper_areas_but_never_legs():
    library = [
        exercise("curl", "Bicep Curl", "upper"),
        exercise("press", "Chest Press", "upper"),
        exercise("row", "Seated Row", "upper"),
        exercise("squat", "Back Squat", "legs"),
    ]
    muscles = workout_logic.focus_muscles_from_label("Upper Body")

    result = workout_logic.filter_exercises(library, profile(), selected_muscles=muscles)

    assert {item["id"] for item in result} == {"curl", "press", "row"}


def test_ai_workout_rejects_off_focus_ids_and_fills_from_safe_pool(monkeypatch):
    core = [exercise(f"core_{i}", f"Plank Variation {i}", "core") for i in range(6)]
    leg = exercise("leg_squat", "Back Squat", "legs")
    library = core + [leg]
    by_id = {
        item["id"]: {
            "id": item["id"], "name": item["name"], "muscles": workout_logic._exercise_muscles(item),
            "equipment": [item["equipment"]], "difficulty": 1, "instructions": "",
            "sets": 3, "reps": 10, "rest": 45, "unit": "reps",
        }
        for item in library
    }
    seen = {}

    monkeypatch.setattr(homefit_app.coach, "is_available", lambda: True)
    monkeypatch.setattr(homefit_app.database, "get_coaching_context", lambda uid: {})
    monkeypatch.setattr(workout_logic, "load_exercises", lambda: library)
    monkeypatch.setattr(homefit_app, "get_exercise_by_id", lambda ex_id: dict(by_id[ex_id]))

    def fake_generate(_context, eligible, focus=None):
        seen["ids"] = {item["id"] for item in eligible}
        return {"name": "Core", "focus": focus, "exercises": [
            {"id": "leg_squat", "sets": 3, "reps": 8},
            {"id": "core_0", "sets": 3, "reps": 10},
        ]}

    monkeypatch.setattr(homefit_app.coach, "generate_workout", fake_generate)

    result = homefit_app._ai_build_workout(1, profile(), focus="Core & Cardio")

    assert seen["ids"] == {item["id"] for item in core}
    assert len(result["exercises"]) == 6
    assert {item["id"] for item in result["exercises"]} == {item["id"] for item in core}


def test_recovery_workout_never_falls_back_to_loaded_strength_moves(monkeypatch):
    light = [exercise(f"light_{i}", f"Step Touch {i}", "cardio") for i in range(6)]
    heavy = exercise("rack_back_squat", "Back Squat", "legs", "power_cage_cable", difficulty=2)
    monkeypatch.setattr(workout_logic, "load_exercises", lambda: light + [heavy])

    result = workout_logic.build_workout(profile(), "Recovery", ["full body"], [])

    assert len(result["exercises"]) == 6
    assert {item["id"] for item in result["exercises"]} == {item["id"] for item in light}


def test_exercise_library_template_matches_status_contract():
    items = workout_logic.all_exercises_with_status(profile())
    required = {"id", "name", "category", "equipment", "difficulty", "instructions", "available", "ignored", "reason"}

    assert items
    assert all(required.issubset(item) for item in items)
    with homefit_app.app.test_request_context("/exercises"):
        rendered = homefit_app.render_template(
            "exercises.html",
            exercises=items,
            profile={"limitations": []},
            user_id=1,
        )

    assert "Exercise library" in rendered
    assert items[0]["name"] in rendered


def test_loading_saved_plan_replaces_off_focus_exercises(monkeypatch):
    saved_plan = [{
        "name": "Core Day",
        "focus": "core",
        "rest": False,
        "exercises": [
            {"id": "barbell_deadlift", "name": "Barbell Deadlift", "sets": 3, "reps": 8},
            {"id": "plank", "name": "Forearm Plank", "sets": 3, "reps": 30},
        ],
    }]
    monkeypatch.setattr(homefit_app.database, "get_apex_plan", lambda uid: {"plan": saved_plan})
    monkeypatch.setattr(homefit_app.database, "is_rest_override", lambda uid, day: False)
    monkeypatch.setattr(homefit_app.database, "get_day_swaps", lambda uid, day: {})
    monkeypatch.setattr(homefit_app.database, "user_today_iso", lambda uid: "2026-08-14")
    monkeypatch.setattr(homefit_app.database, "get_profile", lambda uid: profile())
    client = homefit_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1

    response = client.post("/api/apex-plan/today", json={"weekday": 0})

    assert response.status_code == 200
    with client.session_transaction() as session:
        exercises = session["today_workout"]["exercises"]
    assert len(exercises) == 3
    assert "barbell_deadlift" not in {item["id"] for item in exercises}
    assert all("core" in item["muscles"] for item in exercises)


def test_onboarding_only_collects_the_agreed_essential_fields():
    with homefit_app.app.test_request_context("/onboarding"):
        rendered = homefit_app.render_template(
            "onboarding.html",
            error=None,
            profile={},
            valid_limitations=workout_logic.VALID_LIMITATIONS,
            valid_equipment=workout_logic.VALID_EQUIPMENT,
            valid_muscles=workout_logic.VALID_MUSCLE_GROUPS,
            onboarding_mode=True,
        )

    for field in ("current_weight", "goal_weight", "fitness_level", "days_per_week", "limitations", "equipment"):
        assert f'name="{field}"' in rendered
    for field in ("cardio_days_per_week", "fitness_goal", "workout_duration_target", "target_muscles"):
        assert f'name="{field}"' not in rendered
