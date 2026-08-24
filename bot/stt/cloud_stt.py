"""STT через облачный API (Groq / OpenAI Whisper)."""

import logging
from pathlib import Path

import anyio
from openai import AsyncOpenAI

from bot.config import settings
from bot.stt.base import STTClient

logger = logging.getLogger(__name__)


class CloudSTTClient(STTClient):
    """STT через Groq или OpenAI Whisper API."""

    def __init__(self):
        yaml_cfg = settings.yaml_config
        stt_cfg = yaml_cfg.get("stt", {})
        provider = stt_cfg.get("provider", "groq")
        self.model = stt_cfg.get("model", "whisper-large-v3")
        self.language = stt_cfg.get("language", "ru")

        if provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
            api_key = settings.groq_api_key
        else:
            base_url = "https://api.openai.com/v1"
            api_key = settings.openai_api_key

        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=60,
        )

    async def transcribe(self, audio_path: Path) -> str:
        """Транскрибировать через API."""
        async with await anyio.open_file(audio_path, "rb") as f:
            audio = await f.read()
        response = await self.client.audio.transcriptions.create(
            model=self.model,
            file=(audio_path.name, audio, "application/octet-stream"),
            language=self.language,
        )
        return response.text

    async def health_check(self) -> bool:
        """Проверить доступность API."""
        try:
            await self.client.models.list(timeout=5)
            return True
        except Exception:
            return False
