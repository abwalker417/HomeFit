from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_updater_waits_for_gunicorn_before_declaring_failure():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    assert "for _ in $(seq 1 30)" in installer
    assert 'if [[ "$healthy" != "1" ]]' in installer


def test_first_install_does_not_rollback_to_current_itself():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    assert 'resolved_previous" != "/opt/homefit/current"' in installer
    assert "systemctl stop homefit" in installer
