"""STT через faster-whisper (macOS, локально)."""

import asyncio
import gc
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from pathlib import Path

from bot.config import settings
from bot.logging_safety import error_type
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
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        self._work: Future | None = None
        self._closed = False

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
        return await self._run_native(partial(self._transcribe_sync, audio_path))

    async def _run_native(self, operation):
        """Не ставить новые native jobs в очередь после timeout предыдущей."""
        if self._closed or (self._work is not None and not self._work.done()):
            raise RuntimeError("Local transcription worker is busy or closed")
        self._work = self._executor.submit(operation)
        # Cancellation of the Telegram request must not mark the native work done.
        wrapped = asyncio.wrap_future(self._work)
        wrapped.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        return await asyncio.shield(wrapped)

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
            await self._run_native(self._load_model)
            return self._model is not None
        except Exception as exc:
            logger.warning("Whisper health check failed: error_type=%s", error_type(exc))
            return False

    async def close(self) -> None:
        """Release the native CTranslate2 model and collect adapter resources."""
        self._closed = True
        executor = getattr(self, "_executor", None)
        if executor is None:
            await asyncio.to_thread(self._close_sync)
            return
        # Teardown is queued behind the sole native job, never concurrent with it.
        cleanup = asyncio.wrap_future(executor.submit(self._close_sync))
        executor.shutdown(wait=False)
        try:
            await asyncio.wait_for(asyncio.shield(cleanup), timeout=10)
        except TimeoutError:
            logger.warning("Whisper still busy; native teardown remains scheduled")

    def _close_sync(self) -> None:
        try:
            with self._load_lock:
                model = self._model
                self._model = None
                native_model = getattr(model, "model", None)
                unload = getattr(native_model, "unload_model", None)
                if callable(unload):
                    unload()
        finally:
            gc.collect()
