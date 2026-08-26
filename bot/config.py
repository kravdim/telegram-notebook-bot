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


def _is_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def validate_runtime_config(
    config: dict[str, Any], provider_keys: dict[str, str]
) -> list[str]:
    """Validate provider and operational settings before starting workers."""
    errors: list[str] = []
    supported_llm = {"gemini", "minimax", "zhipu", "openai"}
    llm = config.get("llm", {})
    if not isinstance(llm, dict):
        errors.append("llm config must be a mapping")
        llm = {}
    if not _is_positive(llm.get("total_timeout_sec", 45)):
        errors.append("LLM total_timeout_sec must be positive")
    for role in ("main", "fallback"):
        provider_cfg = llm.get(role)
        if role == "fallback" and not provider_cfg:
            continue
        if not isinstance(provider_cfg, dict):
            errors.append(f"LLM {role} config must be a mapping")
            continue
        provider = provider_cfg.get("provider", "minimax")
        if provider not in supported_llm:
            errors.append(f"unsupported LLM {role} provider: {provider}")
        elif not provider_keys.get(provider):
            errors.append(f"API key is missing for LLM {role} provider: {provider}")
        if not provider_cfg.get("model"):
            errors.append(f"LLM {role} model is empty")
        if not _is_positive(provider_cfg.get("timeout_sec", 15)):
            errors.append(f"LLM {role} timeout_sec must be positive")
        retries = provider_cfg.get("max_retries", 2 if role == "main" else 1)
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 0:
            errors.append(f"LLM {role} max_retries must be a non-negative integer")

    embedding = config.get("embedding", {})
    if not isinstance(embedding, dict):
        errors.append("embedding config must be a mapping")
        embedding = {}
    embed_provider = embedding.get("provider", "ollama")
    if embed_provider not in {"ollama", "cloud"}:
        errors.append(f"unsupported embedding provider: {embed_provider}")
    if not embedding.get("model"):
        errors.append("embedding model is empty")
    if embed_provider == "ollama" and not embedding.get("base_url"):
        errors.append("embedding base_url is empty")
    if embedding.get("dimensions", 768) != 768:
        errors.append("embedding dimensions must match Vector(768)")
    if embed_provider == "cloud" and not provider_keys.get("embedding"):
        errors.append("API key is missing for cloud embedding provider")

    stt = config.get("stt", {})
    if not isinstance(stt, dict):
        errors.append("stt config must be a mapping")
        stt = {}
    stt_provider = stt.get("provider", "local_whisper")
    if stt_provider not in {"local_whisper", "groq", "openai"}:
        errors.append(f"unsupported STT provider: {stt_provider}")
    if not stt.get("model"):
        errors.append("STT model is empty")
    if not _is_positive(stt.get("timeout_sec", 90)):
        errors.append("STT timeout_sec must be positive")
    if not _is_positive(stt.get("warmup_timeout_sec", 120)):
        errors.append("STT warmup_timeout_sec must be positive")
    if stt_provider in {"groq", "openai"} and not provider_keys.get(stt_provider):
        errors.append(f"API key is missing for STT provider: {stt_provider}")

    scheduler = config.get("scheduler", {})
    if not isinstance(scheduler, dict):
        errors.append("scheduler config must be a mapping")
        scheduler = {}
    backup_hour = scheduler.get("backup_hour", 3)
    if not isinstance(backup_hour, int) or isinstance(backup_hour, bool) or not 0 <= backup_hour <= 23:
        errors.append("scheduler backup_hour must be an integer from 0 to 23")
    for key in (
        "healthcheck_interval_min",
        "sweep_interval_min",
        "backup_retention_days",
        "llm_log_retention_days",
    ):
        if not _is_positive(scheduler.get(key, 1)):
            errors.append(f"scheduler {key} must be positive")

    slo = config.get("slo", {})
    if not isinstance(slo, dict):
        errors.append("slo config must be a mapping")
        slo = {}
    for key, default in (
        ("reminder_lag_seconds", 120),
        ("backup_max_age_hours", 30),
        ("stt_latency_seconds", 30),
    ):
        if not _is_positive(slo.get(key, default)):
            errors.append(f"slo {key} must be positive")
    export = config.get("export", {})
    if not isinstance(export, dict):
        errors.append("export config must be a mapping")
        export = {}
    if not _is_positive(export.get("max_bytes", 45 * 1024 * 1024)):
        errors.append("export max_bytes must be positive")
    return errors


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

        provider_keys = {
            "gemini": self.gemini_api_key,
            "minimax": self.minimax_api_key,
            "zhipu": self.zhipu_api_key,
            "groq": self.groq_api_key,
            "openai": self.openai_api_key,
            "embedding": self.embedding_api_key,
        }
        errors.extend(validate_runtime_config(self.yaml_config, provider_keys))
        return errors


settings = Settings()
