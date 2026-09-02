"""Process-local readiness heartbeat shared by deployment targets."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import suppress
from pathlib import Path


class RuntimeReadiness:
    """Publish an atomic heartbeat while the application event loop is alive."""

    def __init__(self, path: str | Path, interval_seconds: float = 5.0) -> None:
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._write()
        self._task = asyncio.create_task(self._heartbeat(), name="readiness-heartbeat")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        with suppress(FileNotFoundError):
            self.path.unlink()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = {
            "pid": os.getpid(),
            "ready": True,
            "heartbeat_epoch": time.time(),
            "release_sha": os.environ.get("DAILYPLANNER_RELEASE_SHA", "unknown"),
        }
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(self.path)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            self._write()


def validate_readiness_file(
    path: str | Path,
    max_age_seconds: float,
    expected_release_sha: str | None = None,
) -> dict:
    """Return the heartbeat payload or raise a diagnostic readiness error."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("ready") is not True:
        raise RuntimeError("runtime has not declared readiness")
    pid = payload.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("readiness heartbeat has an invalid pid")
    try:
        os.kill(pid, 0)
    except ProcessLookupError as exc:
        raise RuntimeError(f"runtime pid {pid} is not alive") from exc
    heartbeat = payload.get("heartbeat_epoch")
    if not isinstance(heartbeat, int | float):
        raise RuntimeError("readiness heartbeat has an invalid timestamp")
    age = time.time() - heartbeat
    if age < -5 or age > max_age_seconds:
        raise RuntimeError(f"runtime heartbeat is stale: age={age:.1f}s")
    if expected_release_sha and payload.get("release_sha") != expected_release_sha:
        raise RuntimeError(
            "runtime release mismatch: "
            f"expected={expected_release_sha} actual={payload.get('release_sha')}"
        )
    return payload
