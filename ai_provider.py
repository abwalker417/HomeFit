"""Small OpenAI-compatible provider adapter shared by Coach and food parsing."""

from urllib.parse import urlparse

import requests


def validate_settings(settings):
    base_url = (settings.get("base_url") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid OpenAI-compatible base URL, such as https://api.openai.com/v1.")
    if not (settings.get("api_key") or "").strip():
        raise ValueError("An API key is required.")
    for field in ("coach_model", "fast_model", "vision_model"):
        if not (settings.get(field) or "").strip():
            raise ValueError("Choose a model for every task.")
    return base_url


def chat_completions_url(base_url):
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"


def models_url(base_url):
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url.rsplit("/chat/completions", 1)[0] + "/models"
    return f"{base_url}/models"


def headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def list_models(settings, timeout=8):
    base_url = validate_settings(settings)
    response = requests.get(models_url(base_url), headers=headers(settings["api_key"]), timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return sorted({item.get("id") for item in payload.get("data", []) if item.get("id")})
