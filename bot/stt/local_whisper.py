"""STT через faster-whisper (macOS, локально)."""

import asyncio
import logging
import os
import threading
from pathlib import Path

from bot.config import settings
from bot.stt.base import STTClient

logger = logging.getLogger(__name__)


class LocalWhisperClient(STTClient):
    """faster-whisper для локальной транскрибации."""

    def __init__(self):
        yaml_cfg = settings.yaml_config
        stt_cfg = yaml_cfg.get("stt", {})
        self.model_size = stt_cfg.get("model", "medium")
        self.language = stt_cfg.get("language", "ru")
        self.local_files_only = bool(stt_cfg.get("local_files_only", True))
        self.download_root = stt_cfg.get("download_root") or os.environ.get(
            "DAILYPLANNER_STT_CACHE"
        )
        self._model = None
        self._load_lock = threading.Lock()

    def _load_model(self):
        """Ленивая загрузка модели."""
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    local_files_only=self.local_files_only,
                    download_root=self.download_root,
                )
                logger.info("Whisper модель загружена: %s", self.model_size)
            except ImportError:
                logger.error("faster-whisper не установлен")
                raise

    async def transcribe(self, audio_path: Path) -> str:
        """Транскрибировать аудио через faster-whisper."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_path)

    def _transcribe_sync(self, audio_path: Path) -> str:
        """Синхронная транскрибация."""
        self._load_model()
        model = self._model
        if model is None:
            raise RuntimeError("Whisper model failed to initialize")
        segments, info = model.transcribe(
            str(audio_path),
            language=self.language,
            beam_size=5,
        )
        text = " ".join(segment.text.strip() for segment in segments)
        logger.info("Транскрибация: %.1f сек, %d символов", info.duration, len(text))
        return text

    async def health_check(self) -> bool:
        """Проверить доступность whisper."""
        if self._model is not None:
            return True
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._load_model)
            return self._model is not None
        except Exception as exc:
            logger.warning("Whisper health check failed: %s", exc, exc_info=True)
            return False
