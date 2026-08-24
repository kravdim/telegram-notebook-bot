import asyncio
import json
import os
import time

import pytest

from bot.runtime.readiness import RuntimeReadiness, validate_readiness_file


@pytest.mark.asyncio
async def test_runtime_readiness_publishes_and_removes_heartbeat(tmp_path):
    path = tmp_path / "ready.json"
    readiness = RuntimeReadiness(path, interval_seconds=0.01)
    await readiness.start()
    await asyncio.sleep(0.02)
    payload = validate_readiness_file(path, max_age_seconds=1)
    assert payload["pid"] == os.getpid()
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
