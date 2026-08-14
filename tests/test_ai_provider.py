import database

import ai_provider


def test_openai_compatible_urls_support_base_or_completion_endpoint():
    assert ai_provider.chat_completions_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert ai_provider.chat_completions_url("http://localhost:11434/v1/chat/completions") == "http://localhost:11434/v1/chat/completions"
    assert ai_provider.models_url("https://api.openai.com/v1") == "https://api.openai.com/v1/models"


def test_legacy_provider_environment_remains_a_compatible_default(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("PEAKAI_URL", "http://gateway.local:4000")
    monkeypatch.setenv("PEAKAI_API_KEY", "legacy-key")

    settings = database._default_ai_provider_settings()

    assert settings["base_url"] == "http://gateway.local:4000/v1"
    assert settings["api_key"] == "legacy-key"


def test_saved_provider_settings_are_instance_wide_and_keep_blank_key(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "provider.db")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PEAKAI_API_KEY", raising=False)
    database.init_db()
    database.save_ai_provider_settings(
        base_url="https://example.test/v1",
        api_key="secret-value",
        coach_model="coach-model",
        fast_model="fast-model",
        vision_model="vision-model",
    )
    database.save_ai_provider_settings(
        base_url="https://example.test/v1",
        api_key="",
        coach_model="coach-model-2",
        fast_model="fast-model",
        vision_model="vision-model",
    )

    settings = database.get_ai_provider_settings()
    assert settings["api_key"] == "secret-value"
    assert settings["coach_model"] == "coach-model-2"
