import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pwa_uses_builthere_identity():
    manifest = json.loads((ROOT / "static" / "manifest.json").read_text())

    assert manifest["name"] == "BuiltHere"
    assert manifest["short_name"] == "BuiltHere"
    assert manifest["description"] == "Training built around what you have."


def test_user_facing_templates_do_not_show_retired_names():
    templates = "\n".join(
        path.read_text()
        for path in (ROOT / "templates").glob("*.html")
    )

    assert "HomeFit" not in templates
    assert "APEX" not in templates
    assert "BuiltHere" in templates


def test_coach_identity_is_product_role_not_named_ai_persona():
    coach_source = (ROOT / "coach.py").read_text()

    assert "embedded in HomeFit" not in coach_source
    assert "You are APEX" not in coach_source
    assert "Users know you simply as “Coach”" in coach_source


def test_builthere_icons_exist_and_are_nonempty():
    expected = {
        "builthere-mark.svg": 500,
        "icon-192.png": 5_000,
        "icon-512.png": 10_000,
        "apple-touch-icon.png": 5_000,
    }

    for filename, minimum_size in expected.items():
        icon = ROOT / "static" / "icons" / filename
        assert icon.exists()
        assert icon.stat().st_size >= minimum_size
