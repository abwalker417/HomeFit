from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_updater_waits_for_gunicorn_before_declaring_failure():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    assert "for _ in $(seq 1 30)" in installer
    assert 'if [[ "$healthy" != "1" ]]' in installer
    assert '"http://127.0.0.1:${HOMEFIT_PORT}/healthz"' in installer
    assert '"http://127.0.0.1:${HOMEFIT_PORT}/profiles"' not in installer


def test_first_install_does_not_rollback_to_current_itself():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    assert 'resolved_previous" != "/opt/homefit/current"' in installer
    assert "systemctl stop homefit" in installer


def test_release_activation_replaces_a_broken_destination_symlink():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    assert 'ln -sfnT "$release_dir" /opt/homefit/current' in installer
    assert 'ln -sfnT "$previous" /opt/homefit/current' in installer


def test_virtualenv_is_not_moved_after_creation():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    assert 'release_tmp="$release_dir"' in installer
    assert 'mv "$release_tmp" "$release_dir"' not in installer
    assert 'release_tmp=""' in installer
