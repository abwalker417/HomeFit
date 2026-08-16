import pytest

import app as homefit_app


@pytest.fixture
def onboarded_client(tmp_path, monkeypatch):
    monkeypatch.setattr(homefit_app.database, "DB_PATH", tmp_path / "smoke.db")
    homefit_app.database.init_db()
    user_id = homefit_app.database.create_user("Smoke Test")
    homefit_app.database.save_profile(
        user_id=user_id,
        current_weight=180,
        goal_weight=170,
        fitness_level="intermediate",
        limitations=[],
        days_per_week=4,
        equipment=["dumbbells", "bodyweight"],
    )
    client = homefit_app.app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
    return client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/start-workout",
        "/apex-plan",
        "/coach",
        "/exercises",
        "/progress",
        "/log-food",
        "/onboarding",
    ],
)
def test_authenticated_pages_render_without_server_errors(onboarded_client, path):
    response = onboarded_client.get(path)

    assert response.status_code == 200
    assert b"<!doctype html" in response.data.lower()


def test_today_workout_redirects_to_builder_without_active_session(onboarded_client):
    response = onboarded_client.get("/today-workout")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/start-workout")


def test_offline_workout_redirects_without_a_saved_plan(onboarded_client):
    response = onboarded_client.get("/offline-workout")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/start-workout")


def test_offline_workout_renders_today_saved_plan(onboarded_client):
    user_id = 1
    plan_day = {"name": "Lower", "focus": "Lower", "rest": False,
                "exercises": [{"id": "bodyweight_squat", "sets": 3, "reps": 10}]}
    homefit_app.database.save_apex_plan(user_id, [plan_day] * 7)

    response = onboarded_client.get("/offline-workout")

    assert response.status_code == 200
    assert b"Offline-ready workout" in response.data


def test_owner_settings_render_and_keep_the_provider_key_masked(onboarded_client):
    with onboarded_client.session_transaction() as session:
        session["is_owner"] = True

    response = onboarded_client.get("/settings")

    assert response.status_code == 200
    assert b"OpenAI-compatible connection" in response.data
    assert b"API key" in response.data
    assert b"peak-homefit-key" not in response.data


def test_owner_can_review_exercise_demos_and_held_demos_do_not_load(onboarded_client):
    with onboarded_client.session_transaction() as session:
        session["is_owner"] = True

    response = onboarded_client.get("/settings/exercise-demo-review")

    assert response.status_code == 200
    assert b"Wall Push-Up" in response.data
    assert b"Needs review" in response.data

    homefit_app._exercise_animations = None
    animations = homefit_app._load_exercise_animations()
    assert "wall_push_up" not in animations
    assert "push_up" in animations


def test_any_signed_in_user_can_hold_a_suspect_demo(onboarded_client):
    response = onboarded_client.post(
        "/api/exercise-demo-report",
        json={"exercise_id": "push_up", "note": "The setup does not match."},
    )

    assert response.status_code == 200
    homefit_app._exercise_animations = None
    assert "push_up" not in homefit_app._load_exercise_animations()


def test_unauthenticated_pages_redirect_to_profile_picker():
    client = homefit_app.app.test_client()

    for path in ("/", "/start-workout", "/exercises", "/progress"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/profiles")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/last_workout"),
        ("get", "/api/recent-workouts"),
        ("get", "/api/last_weight"),
        ("get", "/api/panel-summary"),
        ("get", "/api/coach/readiness"),
        ("get", "/api/coach/plan"),
        ("get", "/api/coach/swap-options"),
        ("get", "/api/food/agent/today"),
        ("get", "/api/food/agent/favorites"),
        ("post", "/api/external-workout"),
        ("post", "/api/sleep"),
        ("post", "/api/health-metric"),
        ("post", "/api/push/register-apns"),
        ("post", "/api/coach/rest-day"),
        ("post", "/api/coach/rest-day/clear"),
        ("post", "/api/coach/swap"),
        ("post", "/api/food/agent/log"),
        ("post", "/api/food/agent/log-image"),
        ("post", "/api/food/agent/goals"),
        ("post", "/api/food/agent/log-favorite"),
        ("post", "/api/away"),
        ("post", "/api/away/end"),
    ],
)
def test_token_apis_reject_missing_credentials(tmp_path, monkeypatch, method, path):
    monkeypatch.setattr(homefit_app.database, "DB_PATH", tmp_path / "security.db")
    homefit_app.database.init_db()
    client = homefit_app.app.test_client()

    response = getattr(client, method)(path, json={})

    assert response.status_code == 401
