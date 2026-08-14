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


def test_cleanup_runs_only_after_release_passes_health_check():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    health_passed = installer.index('release_tmp=""')
    release_cleanup = installer.index("stale_releases")
    backup_cleanup = installer.index("stale_backups")

    assert health_passed < release_cleanup < backup_cleanup
    assert "NR > 5" in installer
    assert "NR > 14" in installer


def test_fresh_install_defaults_to_production_environment():
    installer = (ROOT / "install" / "homefit-install.sh").read_text()

    assert "HOMEFIT_ENV=production" in installer
    assert "HOMEFIT_ENV=development" not in installer


def test_versioned_updater_is_self_refreshing_and_retains_rollbacks():
    updater = (ROOT / "install" / "homefit-update.sh").read_text()

    assert "HOMEFIT_UPDATER_V2=1" in updater
    assert '"$release_dir/install/homefit-update.sh" /usr/local/sbin/homefit-update' in updater
    assert '"http://127.0.0.1:${HOMEFIT_PORT}/healthz"' in updater
    assert "NR > 5" in updater
    assert "NR > 14" in updater


def test_lxc_helper_seeds_the_versioned_updater():
    helper = (ROOT / "scripts" / "homefit-v2-lxc.sh").read_text()

    assert "UPDATER_URL=" in helper
    assert "HOMEFIT_UPDATER_V2=1" in helper
    assert "/root/homefit-update.sh" in helper
