"""Tests for coach JSON extraction helpers.
Run with: cd HomeFit && .venv/bin/python -m pytest tests/
"""

import pytest
import coach
import requests

@pytest.fixture(autouse=True)
def block_requests(monkeypatch):
    # Prevent any real HTTP calls during tests
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Network disabled")))
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Network disabled")))

def test_parse_json_safe_clean():
    raw = "{\"status\": \"ok\", \"value\": 42}"
    assert coach._parse_json_safe(raw) == {"status": "ok", "value": 42}

def test_parse_json_safe_markdown_fence():
    raw = "```json\n{\"a\": 1, \"b\": 2}\n```"
    assert coach._parse_json_safe(raw) == {"a": 1, "b": 2}

def test_parse_json_safe_leading_prose():
    raw = "Here is the result: {\"x\": \"y\"}"
    assert coach._parse_json_safe(raw) == {"x": "y"}

def test_parse_json_safe_trailing_comma():
    raw = "{\"a\": 1,}"
    assert coach._parse_json_safe(raw) == {"a": 1}

def test_parse_json_safe_null_raises():
    with pytest.raises(ValueError):
        coach._parse_json_safe("null")

def test_parse_json_safe_truncated_raises():
    with pytest.raises(ValueError):
        coach._parse_json_safe('{"incomplete": 123')
