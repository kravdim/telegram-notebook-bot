"""Абстрактный интерфейс для STT-провайдеров."""

from abc import ABC, abstractmethod
from pathlib import Path


class STTClient(ABC):
    """Абстрактный STT-клиент."""

    @abstractmethod
    async def transcribe(self, audio_path: Path) -> str:
        """Транскрибировать аудиофайл. Возвращает текст."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Проверить доступность провайдера."""
