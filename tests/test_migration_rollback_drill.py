import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from scripts import run_migration_rollback_drill as driver

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("returncode,output,accepted", [
    (1, b"Migration mismatch", True),
    (0, b"Migration mismatch", False),
    (1, b"Unrelated error", False),
])
def test_expected_failure_requires_correct_reason(returncode, output, accepted):
    result = SimpleNamespace(returncode=returncode, stdout=output, stderr=b"")
    with patch.object(driver.subprocess, "run", return_value=result):
        if accepted:
            driver.run(["synthetic"], expected_error="Migration mismatch")
        else:
            with pytest.raises(RuntimeError, match="not reproduced"):
                driver.run(["synthetic"], expected_error="Migration mismatch")


@pytest.mark.parametrize("database_url,flag", [
    ("postgresql+asyncpg://drill:synthetic@127.0.0.1/production", "1"),
    ("postgresql+asyncpg://drill:synthetic@remote.invalid/migration_drill", "1"),
    ("postgresql+asyncpg://drill:synthetic@127.0.0.1/migration_drill", "0"),
])
def test_probe_rejects_non_disposable_database_before_import(database_url, flag):
    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "scripts/migration_rollback_probe.py",
         "--project", "/nonexistent-release", "--mode", "seed"],
        cwd=ROOT, env={**os.environ, "DATABASE_URL": database_url, "DAILYPLANNER_MIGRATION_DRILL": flag},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "Only the disposable" in result.stderr


def test_real_rollback_drill_is_a_required_reusable_ci_job():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = ci["jobs"]["migration-rollback"]
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    assert any("run_migration_rollback_drill.py" in step.get("run", "") for step in job["steps"])
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert release["jobs"]["evidence"]["needs"] == "quality"
    assert release["jobs"]["quality"]["uses"] == "./.github/workflows/ci.yml"
