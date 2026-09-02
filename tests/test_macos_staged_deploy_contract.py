from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "platform/macos/install.sh"


def _script() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_candidate_checks_happen_before_launchagent_switch():
    script = _script()
    switch = script.index('echo "Switching LaunchAgent to candidate..."')
    for required in (
        'scripts/preflight.py',
        'scripts/check_telegram_credentials.py',
        'scripts/prefetch_stt_model.py',
        'plutil -lint "$CANDIDATE_PLIST"',
    ):
        assert script.index(required) < switch


def test_deploy_uses_versioned_release_and_bounded_readiness():
    script = _script()
    assert 'RELEASE_ROOT=' in script
    assert 'git archive "$revision"' in script
    move_source = script.index('mv "$staging_dir" "$release_dir"')
    create_venv = script.index('uv sync --project "$release_dir"')
    mark_ready = script.index('touch "$ready_marker"')
    assert move_source < create_venv < mark_ready
    assert '--expected-release "$CANDIDATE_SHA"' not in script
    assert 'wait_for_release "$CANDIDATE_DIR" "$CANDIDATE_SHA"' in script
    assert 'READINESS_TIMEOUT=90' in script


def test_failed_candidate_restores_previous_release_and_writes_report():
    script = _script()
    rollback = script[script.index("rollback() {") :]
    assert 'cp "$ROLLBACK_PLIST" "$PLIST_DST"' in rollback
    assert 'wait_for_release "$PREVIOUS_DIR"' in rollback
    assert "status=rolled_back" in rollback
    assert "status=rollback_failed" in rollback


def test_installer_serializes_concurrent_deploys_and_rejects_dirty_tree():
    script = _script()
    assert 'mkdir "$DEPLOY_LOCK"' in script
    assert "Refusing deploy from a dirty tracked worktree" in script
