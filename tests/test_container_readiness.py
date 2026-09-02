import asyncio
import json
import os
import time

import pytest

from bot.runtime.readiness import RuntimeReadiness, validate_readiness_file


@pytest.mark.asyncio
async def test_runtime_readiness_publishes_and_removes_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILYPLANNER_RELEASE_SHA", "abc123")
    path = tmp_path / "ready.json"
    readiness = RuntimeReadiness(path, interval_seconds=0.01)
    await readiness.start()
    await asyncio.sleep(0.02)
    payload = validate_readiness_file(
        path, max_age_seconds=1, expected_release_sha="abc123"
    )
    assert payload["pid"] == os.getpid()
    assert payload["release_sha"] == "abc123"
    await readiness.stop()
    assert not path.exists()


def test_runtime_readiness_rejects_stale_heartbeat(tmp_path):
    path = tmp_path / "ready.json"
    path.write_text(
        json.dumps(
            {"pid": os.getpid(), "ready": True, "heartbeat_epoch": time.time() - 60}
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="stale"):
        validate_readiness_file(path, max_age_seconds=10)


def test_runtime_readiness_rejects_wrong_release(tmp_path):
    path = tmp_path / "ready.json"
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "ready": True,
                "heartbeat_epoch": time.time(),
                "release_sha": "old",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="release mismatch"):
        validate_readiness_file(
            path, max_age_seconds=10, expected_release_sha="candidate"
        )
