#!/usr/bin/env python3
"""Real previous-release/new-schema and snapshot-restore drill, Docker-only.

No production URL is accepted. Every database operation targets a newly created
isolated container. No Telegram, launchctl or external AI calls are performed.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "pgvector/pgvector:pg16@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b"
PREVIOUS_RELEASE = "27ce9e0620a18b00e199584cf013351bb9b8040b"  # v0.5.0, not a mutable tag lookup


def run(command: list[str], *, cwd: Path = ROOT, env: dict | None = None,
        data: bytes | None = None, expected_error: str | None = None) -> bytes:
    result = subprocess.run(command, cwd=cwd, env=env, input=data, capture_output=True, timeout=180)
    output = result.stdout + result.stderr
    if expected_error is not None:
        if result.returncode == 0 or expected_error.encode() not in output:
            raise RuntimeError(f"Expected failure was not reproduced: {expected_error}")
    elif result.returncode:
        # The drill passes only synthetic credentials, but never dump subprocess env/output.
        raise RuntimeError(f"Drill command failed: {Path(command[0]).name}, exit={result.returncode}")
    return result.stdout


def export_previous(previous: str, directory: Path) -> str:
    revision = run(["git", "rev-parse", "--verify", "--end-of-options", f"{previous}^{{commit}}"]).decode().strip()
    if len(revision) != 40:
        raise ValueError("Expected an exact Git commit")
    archived = run(["git", "archive", revision])
    with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
        archive.extractall(directory, filter="data")
    shutil.copyfile(directory / "config.yaml.example", directory / "config.yaml")
    run(["uv", "sync", "--project", str(directory), "--frozen", "--no-dev", "--extra", "stt"])
    return revision


def drill(previous: str) -> dict:
    started_at = datetime.now(timezone.utc).isoformat()
    run(["git", "diff", "--exit-code", "HEAD", "--", "bot", "pyproject.toml", "uv.lock"])
    if run(["git", "ls-files", "--others", "--exclude-standard", "--", "bot"]).strip():
        raise RuntimeError("Commit untracked runtime code before recording exact-SHA evidence")
    python = str(ROOT / ".venv/bin/python")
    container = f"dailyplanner-migration-drill-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="dailyplanner-migration-drill-") as temporary:
        old = Path(temporary) / "previous"
        old.mkdir()
        old_sha = export_previous(previous, old)
        started = False
        try:
            run(["docker", "run", "--rm", "-d", "--name", container,
                 "-e", "POSTGRES_USER=drill", "-e", "POSTGRES_PASSWORD=synthetic-drill",
                 "-e", "POSTGRES_DB=migration_drill", "-p", "127.0.0.1::5432", IMAGE])
            started = True
            port = run(["docker", "port", container, "5432/tcp"]).decode().strip().rsplit(":", 1)[1]
            env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(old),
                   "BOT_TOKEN": "synthetic-drill", "MINIMAX_API_KEY": "synthetic-drill",
                   "ALLOW_ALL_USERS": "true", "DAILYPLANNER_MIGRATION_DRILL": "1",
                   "DATABASE_URL": f"postgresql+asyncpg://drill:synthetic-drill@127.0.0.1:{port}/migration_drill"}
            wait_database(container)
            report = exercise(python, old, env, container)
            report.update(previous_sha=old_sha, candidate_sha=run(["git", "rev-parse", "HEAD"]).decode().strip(),
                          lock_sha256=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
                          previous_lock_sha256=hashlib.sha256((old / "uv.lock").read_bytes()).hexdigest(),
                          production_touched=False, automatic_code_rollback_safe=False,
                          mode="maintenance-snapshot-restore", accepted_user_writes_during_drill=False)
            report.update(started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
                          probe_sha256=hashlib.sha256((ROOT / "scripts/migration_rollback_probe.py").read_bytes()).hexdigest(),
                          driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            return report
        finally:
            if started:
                run(["docker", "rm", "-f", container])


def wait_database(container: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        ready = subprocess.run(["docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "drill"],
                               capture_output=True, timeout=10)
        if ready.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("Disposable PostgreSQL did not become ready")


def exercise(python: str, old: Path, env: dict, container: str) -> dict:
    probe = str(ROOT / "scripts/migration_rollback_probe.py")
    old_python = str(old / ".venv/bin/python")

    def check(mode: str, project: Path, runtime: dict) -> dict:
        interpreter = old_python if project == old else python
        return json.loads(run([interpreter, probe, "--project", str(project), "--mode", mode], cwd=project, env=runtime))

    run([old_python, "-m", "alembic", "upgrade", "head"], cwd=old, env=env)
    run([old_python, "scripts/preflight.py"], cwd=old, env=env)
    before = check("seed", old, env)
    snapshot = run(["docker", "exec", container, "pg_dump", "-U", "drill", "-d", "migration_drill", "-Fc"])
    candidate_env = {**env, "PYTHONPATH": str(ROOT)}
    run([python, "-m", "alembic", "upgrade", "head"], env=candidate_env)
    candidate = check("candidate", ROOT, candidate_env)
    run([old_python, "scripts/preflight.py"], cwd=old, env=env, expected_error="Migration mismatch")
    run([old_python, "scripts/preflight.py", "--compatible-database-head", candidate["schema_head"]], cwd=old, env=env)
    hazard = check("legacy-hazard", old, env)
    declarations = (ROOT / "bot/db/migrations/rollback_compatible_heads.txt").read_text().splitlines()
    if candidate["schema_head"] in {line.strip() for line in declarations}:
        raise RuntimeError("Unsafe candidate head was declared code-rollback compatible")
    run([python, "-m", "alembic", "downgrade", before["schema_head"]], env=candidate_env,
        expected_error="Finish pending action plans")
    after_rejected_downgrade = check("quarantine", ROOT, candidate_env)
    if after_rejected_downgrade["schema_head"] != candidate["schema_head"]:
        raise RuntimeError("Rejected downgrade partially changed the schema")
    run(["docker", "exec", container, "createdb", "-U", "drill", "migration_restored"])
    run(["docker", "exec", "-i", container, "pg_restore", "-U", "drill", "-d", "migration_restored",
         "--exit-on-error", "--no-owner"], data=snapshot)
    restored_env = {**env, "DATABASE_URL": env["DATABASE_URL"].replace("/migration_drill", "/migration_restored")}
    run([old_python, "scripts/preflight.py"], cwd=old, env=restored_env)
    run([old_python, "scripts/container_smoke.py", "--once"], cwd=old, env=restored_env)
    restored = check("restored", old, restored_env)
    check("quarantine", ROOT, candidate_env)
    return {"ok": True, "before": before, "candidate": candidate, "hazard": hazard, "restored": restored,
            "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
            "failed_candidate_database_preserved_until_drill_cleanup": True,
            "downgrade_with_pending_plan_blocked": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", default=PREVIOUS_RELEASE)
    args = parser.parse_args()
    print(json.dumps(drill(args.previous), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
