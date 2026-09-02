import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
    migration = script.index('"$CANDIDATE_DIR/.venv/bin/alembic" upgrade head')
    assert script.index('scripts/check_telegram_credentials.py') < migration < switch
    assert script.index('scripts/prefetch_stt_model.py') < migration < switch


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
    assert 'wait_for_release "$PREVIOUS_DIR" "$PREVIOUS_EXPECTED_SHA"' in rollback
    assert '"$release_dir/scripts/preflight.py"' in script
    assert '--compatible-database-head "$compatible_head"' in script
    assert 'write_report "rolled_back"' in rollback
    assert 'write_report "rollback_failed"' in rollback


def test_installer_serializes_concurrent_deploys_and_rejects_dirty_tree():
    script = _script()
    assert 'mkdir "$DEPLOY_LOCK"' in script
    assert "Refusing deploy from a dirty tracked worktree" in script
    assert 'write_report "pre_switch_failed"' in script
    assert 'write_report "failed" "unexpected failure' in script


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _build_installer_harness(tmp_path: Path) -> tuple[Path, dict[str, str], str, str]:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    repo.mkdir()
    fake_bin.mkdir()
    home.mkdir()
    (repo / "platform/macos").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "bot/runtime").mkdir(parents=True)
    shutil.copy2(INSTALLER, repo / "platform/macos/install.sh")
    for relative in (
        "platform/macos/com.notebook-bot.plist",
        "platform/macos/render_launchagent.py",
        "scripts/preflight.py",
        "scripts/check_telegram_credentials.py",
        "scripts/prefetch_stt_model.py",
        "scripts/check_runtime_readiness.py",
        "scripts/get_migration_head.py",
    ):
        (repo / relative).write_text("fixture\n", encoding="utf-8")
    (repo / "bot/db/migrations").mkdir(parents=True)
    (repo / "bot/db/migrations/rollback_compatible_heads.txt").write_text(
        "# test fixture\n", encoding="utf-8"
    )
    (repo / "bot/runtime/readiness.py").write_text("release_sha = True\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "previous"], cwd=repo, check=True)
    previous_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    candidate_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    runtime_python = tmp_path / "runtime-python"
    _write_executable(
        runtime_python,
        """#!/bin/bash
set -eu
target="$1"
shift
case "$target" in
  *render_launchagent.py)
    output=""; release=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --output) output="$2"; shift 2 ;;
        --release-sha) release="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    printf 'release=%s\\n' "$release" > "$output"
    ;;
  *check_telegram_credentials.py)
    [ "${FAIL_PHASE:-}" != telegram ]
    ;;
  *prefetch_stt_model.py)
    [ "${FAIL_PHASE:-}" != stt ]
    ;;
  *check_runtime_readiness.py)
    expected=""
    while [ "$#" -gt 0 ]; do
      case "$1" in --expected-release) expected="$2"; shift 2 ;; *) shift ;; esac
    done
    if [ "${FAIL_PHASE:-}" = readiness ] && [ "$expected" = "$TEST_CANDIDATE_SHA" ]; then exit 1; fi
    if [ "${FAIL_PHASE:-}" = rollback_readiness ]; then exit 1; fi
    ;;
  *get_migration_head.py)
    project=""
    while [ "$#" -gt 0 ]; do
      case "$1" in --project) project="$2"; shift 2 ;; *) shift ;; esac
    done
    if [ -f "$project/candidate.txt" ]; then
      printf '%s\n' "${TEST_CANDIDATE_DB_HEAD:-test-db-head}"
    else
      printf '%s\n' "${TEST_PREVIOUS_DB_HEAD:-test-db-head}"
    fi
    ;;
  *preflight.py)
    case " $* " in
      *' --allow-pending-migration '*) [ "${FAIL_PHASE:-}" != database ] ;;
      *) [ "${FAIL_PHASE:-}" != post_migration ] ;;
    esac
    ;;
esac
""",
    )
    fake_alembic = tmp_path / "fake-alembic"
    _write_executable(
        fake_alembic,
        "#!/bin/bash\n[ \"${FAIL_PHASE:-}\" != migration ]\n",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/bin/bash
set -eu
project=""
while [ "$#" -gt 0 ]; do
  case "$1" in --project) project="$2"; shift 2 ;; *) shift ;; esac
done
[ "${FAIL_PHASE:-}" != dependencies ]
mkdir -p "$project/.venv/bin"
cp "$FAKE_RUNTIME_PYTHON" "$project/.venv/bin/python"
cp "$FAKE_ALEMBIC" "$project/.venv/bin/alembic"
chmod +x "$project/.venv/bin/python" "$project/.venv/bin/alembic"
""",
    )
    _write_executable(
        fake_bin / "plutil",
        "#!/bin/bash\n[ \"${FAIL_PHASE:-}\" != plist ]\n",
    )
    _write_executable(
        fake_bin / "sleep",
        "#!/bin/bash\nexit 0\n",
    )
    _write_executable(
        fake_bin / "launchctl",
        """#!/bin/bash
set -eu
command="$1"; plist="$2"
if [ "$command" = unload ]; then exit 0; fi
release=$(sed -n 's/^release=//p' "$plist")
printf '%s\\n' "$release" >> "$LAUNCH_LOG"
if { [ "${FAIL_PHASE:-}" = candidate_load ] || [ "${FAIL_PHASE:-}" = rollback_load ]; } && [ "$release" = "$TEST_CANDIDATE_SHA" ]; then exit 1; fi
if [ "${FAIL_PHASE:-}" = rollback_load ] && [ "$release" = "$TEST_PREVIOUS_SHA" ]; then exit 1; fi
""",
    )

    state = home / "state"
    state.mkdir()
    (state / "current-release").write_text(previous_sha + "\n", encoding="utf-8")
    plist = home / "agent.plist"
    plist.write_text(f"release={previous_sha}\n", encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "DAILYPLANNER_PLIST_DST": str(plist),
        "DAILYPLANNER_LOG_DIR": str(home / "logs"),
        "DAILYPLANNER_RELEASE_ROOT": str(home / "releases"),
        "DAILYPLANNER_STATE_DIR": str(state),
        "FAKE_RUNTIME_PYTHON": str(runtime_python),
        "FAKE_ALEMBIC": str(fake_alembic),
        "LAUNCH_LOG": str(home / "launch.log"),
        "TEST_CANDIDATE_SHA": candidate_sha,
        "TEST_PREVIOUS_SHA": previous_sha,
    }
    return repo, env, previous_sha, candidate_sha


def test_migration_rollback_requires_explicit_compatibility_and_uses_previous_code(
    tmp_path: Path,
):
    repo, env, previous_sha, candidate_sha = _build_installer_harness(tmp_path)
    env["TEST_PREVIOUS_DB_HEAD"] = "old-head"
    env["TEST_CANDIDATE_DB_HEAD"] = "new-head"
    env["FAIL_PHASE"] = "readiness"

    rejected = subprocess.run(
        [str(repo / "platform/macos/install.sh"), "--readiness-timeout", "1"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    assert rejected.returncode == 1
    assert "phase=migration_compatibility\n" in (
        Path(env["DAILYPLANNER_STATE_DIR"]) / "last-deploy-report.txt"
    ).read_text()

    manifest = repo / "bot/db/migrations/rollback_compatible_heads.txt"
    manifest.write_text("new-head\n", encoding="utf-8")
    subprocess.run(["git", "add", str(manifest)], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "declare compatible migration"], cwd=repo, check=True)
    compatible_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    env["TEST_CANDIDATE_SHA"] = compatible_sha

    rolled_back = subprocess.run(
        [str(repo / "platform/macos/install.sh"), "--readiness-timeout", "1"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    assert rolled_back.returncode == 1
    report = (
        Path(env["DAILYPLANNER_STATE_DIR"]) / "last-deploy-report.txt"
    ).read_text()
    assert "status=rolled_back\n" in report
    assert f"active={previous_sha}\n" in report
    assert f"candidate={compatible_sha}\n" in report


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_phase", "launches"),
    (
        ("dependencies", "pre_switch_failed", "prepare_candidate", 0),
        ("database", "pre_switch_failed", "database_preflight", 0),
        ("telegram", "pre_switch_failed", "telegram_credentials", 0),
        ("stt", "pre_switch_failed", "stt_warmup", 0),
        ("plist", "pre_switch_failed", "render_candidate", 0),
        ("migration", "pre_switch_failed", "migration", 0),
        ("post_migration", "pre_switch_failed", "migration", 0),
        ("candidate_load", "rolled_back", "rollback", 2),
        ("readiness", "rolled_back", "rollback", 2),
        ("rollback_load", "rollback_failed", "rollback", 2),
        ("rollback_readiness", "rollback_failed", "rollback", 2),
    ),
)
def test_executable_failure_injection_matrix(
    tmp_path: Path,
    failure: str,
    expected_status: str,
    expected_phase: str,
    launches: int,
):
    repo, env, previous_sha, candidate_sha = _build_installer_harness(tmp_path)
    env["FAIL_PHASE"] = failure

    result = subprocess.run(
        [str(repo / "platform/macos/install.sh"), "--readiness-timeout", "1"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    report = (Path(env["DAILYPLANNER_STATE_DIR"]) / "last-deploy-report.txt").read_text()
    assert f"status={expected_status}\n" in report
    assert f"candidate={candidate_sha}\n" in report
    assert f"previous={previous_sha}\n" in report
    assert f"phase={expected_phase}\n" in report
    launch_log = Path(env["LAUNCH_LOG"])
    launch_count = len(launch_log.read_text().splitlines()) if launch_log.exists() else 0
    assert launch_count == launches
    assert (Path(env["DAILYPLANNER_STATE_DIR"]) / "current-release").read_text().strip() == previous_sha
