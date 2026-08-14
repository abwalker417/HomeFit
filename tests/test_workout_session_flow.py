from datetime import datetime
from pathlib import Path

import app as homefit_app


ROOT = Path(__file__).resolve().parents[1]


def test_phone_workout_flow_tracks_set_progress_and_sends_complete_insight_data():
    source = (ROOT / "static" / "js" / "workout-flow.js").read_text()

    assert "logged_at: Date.now()" in source
    assert "of ${totalSets()} sets logged" in source
    assert "Undo last set" in (ROOT / "templates" / "workout.html").read_text()
    assert "completed: true" in source


def test_completion_screen_links_to_the_coach_workspace():
    template = (ROOT / "templates" / "workout.html").read_text()

    assert "url_for('coach_page')" in template
    assert 'href="/apex"' not in template


def test_garage_completion_does_not_double_log_recent_finish(monkeypatch):
    now = datetime(2026, 8, 14, 9, 0, 0)
    logged = []
    monkeypatch.setattr(homefit_app.database, "get_last_workout", lambda _uid: {
        "day_name": "Upper", "completed_at": now.isoformat(),
        "duration_seconds": 600,
        "exercises": [{"id": "pushup", "completed": True, "sets": []}],
    })
    monkeypatch.setattr(homefit_app.database, "user_now", lambda _uid: now)
    monkeypatch.setattr(homefit_app.database, "get_profile", lambda _uid: {"current_weight": 180})
    monkeypatch.setattr(homefit_app.database, "clear_workout_draft", lambda _uid: None)
    monkeypatch.setattr(homefit_app.database, "log_workout", lambda *args: logged.append(args))
    monkeypatch.setattr(homefit_app, "get_exercise_by_id", lambda _id: None)

    client = homefit_app.app.test_client()
    with client.session_transaction() as session:
        session["garage_user"] = 1

    response = client.post("/api/garage/complete", json={
        "day_name": "Upper", "duration_seconds": 600,
        "exercises": [{"id": "pushup", "completed": True, "sets": []}],
    })

    assert response.status_code == 200
    assert response.get_json()["duplicate"] is True
    assert logged == []
