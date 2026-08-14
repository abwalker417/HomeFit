import app as homefit_app


def test_profile_creation_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("HOMEFIT_ALLOW_PROFILE_CREATION", raising=False)
    client = homefit_app.app.test_client()

    response = client.get("/profiles/new")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/profiles")


def test_profile_creation_can_be_enabled_for_fresh_install(monkeypatch):
    monkeypatch.setenv("HOMEFIT_ALLOW_PROFILE_CREATION", "1")
    monkeypatch.setattr(homefit_app, "owner_exists", lambda: False)
    client = homefit_app.app.test_client()

    response = client.get("/profiles/new")

    assert response.status_code == 200
    assert b"Create" in response.data
