"""Конфигурация приложения: Pydantic Settings + config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_yaml_config() -> dict[str, Any]:
    """Загрузка config.yaml."""
    config_path = BASE_DIR / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_yaml = _load_yaml_config()


class Settings(BaseSettings):
    """Настройки из .env + config.yaml."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str = ""

    # LLM
    deepseek_api_key: str = ""
    minimax_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://notebook:password@localhost:5432/notebook_bot"

    # Embedding
    embedding_api_key: str = ""
    embedding_base_url: str = ""

    # Из config.yaml
    admin_telegram_ids: list[int] = Field(
        default_factory=lambda: _yaml.get("bot", {}).get("admin_telegram_ids", [])
    )
    allowed_telegram_ids: list[int] = Field(
        default_factory=lambda: _yaml.get("bot", {}).get("allowed_telegram_ids", [])
    )
    default_timezone: str = Field(
        default_factory=lambda: _yaml.get("bot", {}).get("default_timezone", "Europe/Moscow")
    )

    @property
    def yaml_config(self) -> dict[str, Any]:
        """Доступ к полному config.yaml."""
        return _yaml


settings = Settings()
