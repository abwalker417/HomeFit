import app as homefit_app


def test_healthz_is_public_and_checks_database():
    client = homefit_app.app.test_client()

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_healthz_reports_database_failure(monkeypatch):
    def broken_connection():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(homefit_app.database, "get_connection", broken_connection)
    client = homefit_app.app.test_client()

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.get_json() == {"status": "unhealthy"}
