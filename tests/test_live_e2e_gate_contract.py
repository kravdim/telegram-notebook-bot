import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.verify_live_runner import verify_runner

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_live_e2e_gate.sh"


def test_live_gate_binds_evidence_to_exact_production_sha():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'TESTED_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"' in source
    assert 'DEPLOYED_SHA="$(head -1 "$CURRENT_RELEASE_FILE")"' in source
    assert "Live E2E SHA mismatch" in source
    assert "Production SHA changed during live E2E" in source
    assert "tested_sha=$TESTED_SHA" in source
    assert "tests/live/run_memoir_gate.py" in source


def test_live_gate_requires_state_and_cleanup_oracles():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "State oracle:" in source
    assert "Cleanup oracle:" in source


def test_memoir_gate_uses_the_task_domain_completion_contract():
    source = (ROOT / "tests/live/run_memoir_gate.py").read_text(encoding="utf-8")

    assert 'tasks[0].status == "done"' in source
    assert 'tasks[0].resolution == "completed"' in source


@pytest.mark.parametrize("argument", ["--only=A1", "--only", "--skip-voice"])
def test_full_gate_rejects_filtered_run_before_external_calls(argument):
    result = subprocess.run(["bash", str(SCRIPT), argument], capture_output=True, text=True)
    assert result.returncode == 2
    assert "forbids corpus filters" in result.stderr


def test_runner_lock_checks_content_and_unique_corpus(tmp_path):
    relative = "tests_dailyplanner/run_messy_human.py"
    runner = tmp_path / relative
    runner.parent.mkdir()
    runner.write_text('Case("A1", "test")\nCase("A2", "test")\n')
    lock = {
        "repository_commit": "a" * 40, "expected_cases": 2,
        "files": {relative: hashlib.sha256(runner.read_bytes()).hexdigest()},
    }
    with patch("scripts.verify_live_runner.subprocess.check_output", return_value="a" * 40):
        assert verify_runner(tmp_path, lock) == 2
        runner.write_text('Case("A1", "changed")\n')
        with pytest.raises(ValueError, match="file differs"):
            verify_runner(tmp_path, lock)
        lock["files"][relative] = hashlib.sha256(runner.read_bytes()).hexdigest()
        with pytest.raises(ValueError, match="count or IDs"):
            verify_runner(tmp_path, lock)
