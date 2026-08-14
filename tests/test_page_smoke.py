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


def test_unauthenticated_pages_redirect_to_profile_picker():
    client = homefit_app.app.test_client()

    for path in ("/", "/start-workout", "/exercises", "/progress"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/profiles")
