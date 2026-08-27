"""Opt-in native STT resource drill for a host with the production model."""

import asyncio
import os
import threading
from pathlib import Path

import pytest

from bot.stt.local_whisper import LocalWhisperClient

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_STT_RESOURCE_TESTS") != "1",
    reason="requires local faster-whisper model and STT_RESOURCE_AUDIO",
)


@pytest.mark.asyncio
async def test_repeated_local_transcription_releases_model_and_worker_resources():
    audio = Path(os.environ.get("STT_RESOURCE_AUDIO", ""))
    if not audio.is_file():
        pytest.fail("STT_RESOURCE_AUDIO must point to a readable audio fixture")

    baseline_threads = {thread.ident for thread in threading.enumerate()}
    client = LocalWhisperClient()
    try:
        for _ in range(20):
            assert await client.transcribe(audio)
    finally:
        await client.close()

    await asyncio.sleep(0)
    remaining_new_threads = {
        thread.ident for thread in threading.enumerate()
    } - baseline_threads
    assert client._model is None
    assert len(remaining_new_threads) <= 2
