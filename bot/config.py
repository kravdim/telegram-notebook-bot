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
    allow_all_users: bool = False

    # LLM
    gemini_api_key: str = ""
    minimax_api_key: str = ""
    zhipu_api_key: str = ""
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

    @property
    def access_control_configured(self) -> bool:
        return bool(self.allowed_telegram_ids or self.admin_telegram_ids or self.allow_all_users)

    def runtime_config_errors(self) -> list[str]:
        """Критичные ошибки конфигурации, при которых запуск небезопасен."""
        errors: list[str] = []
        if not self.database_url.startswith("postgresql+asyncpg://"):
            errors.append("DATABASE_URL must use postgresql+asyncpg://")
        try:
            import pendulum
            pendulum.now(self.default_timezone)
        except Exception:
            errors.append(f"invalid default timezone: {self.default_timezone}")

        main = self.yaml_config.get("llm", {}).get("main", {})
        provider = main.get("provider", "minimax")
        provider_keys = {
            "gemini": self.gemini_api_key,
            "minimax": self.minimax_api_key,
            "zhipu": self.zhipu_api_key,
            "openai": self.openai_api_key,
        }
        if provider not in provider_keys:
            errors.append(f"unsupported LLM provider: {provider}")
        elif not provider_keys[provider]:
            errors.append(f"API key is missing for LLM provider: {provider}")
        if not main.get("model"):
            errors.append("LLM model is empty")

        dimensions = self.yaml_config.get("embedding", {}).get("dimensions", 768)
        if dimensions != 768:
            errors.append("embedding dimensions must match Vector(768)")
        return errors


settings = Settings()
