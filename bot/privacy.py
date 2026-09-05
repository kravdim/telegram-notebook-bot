"""User-facing privacy contract derived from active provider configuration."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings

PRIVACY_NOTICE_VERSION = 1


def provider_fingerprint() -> str:
    """Bind consent to recipients, not API secrets or model tuning parameters."""
    config = settings.yaml_config
    llm = config.get("llm", {})
    recipients = []
    for role in ("main", "fallback"):
        provider = llm.get(role) or {}
        if role == "fallback" and not provider:
            continue
        recipients.append((role, provider.get("provider", "minimax"),
                           _endpoint_identity(provider.get("base_url", "https://api.minimax.io/v1"))))
    embedding = config.get("embedding", {})
    recipients.append(("embedding", embedding.get("provider", "ollama"),
                       _endpoint_identity(getattr(settings, "embedding_base_url", "")
                                          or embedding.get("base_url", ""))))
    recipients.append(("stt", config.get("stt", {}).get("provider", "local"), ""))
    payload = json.dumps(recipients, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _endpoint_identity(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme.lower()}://{parsed.hostname}:{parsed.port}{parsed.path.rstrip('/')}"


def has_current_consent(user: object | None) -> bool:
    return bool(
        user is not None
        and getattr(user, "cloud_processing_enabled", False)
        and getattr(user, "privacy_notice_version", 0) >= PRIVACY_NOTICE_VERSION
        and getattr(user, "privacy_provider_fingerprint", None) == provider_fingerprint()
    )


def consent_display_state(user: object | None) -> bool | None:
    enabled = getattr(user, "cloud_processing_enabled", None)
    if enabled is True:
        return True if has_current_consent(user) else None
    return False if enabled is False else None

_PROVIDER_LABELS = {
    "minimax": "MiniMax",
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "zhipu": "Zhipu AI",
    "groq": "Groq",
}


def cloud_provider_labels() -> list[str]:
    """List active external recipients without exposing endpoints or credentials."""
    config = settings.yaml_config
    providers: set[str] = set()
    llm = config.get("llm", {})
    for role in ("main", "fallback"):
        provider = (llm.get(role) or {}).get("provider")
        if provider:
            providers.add(str(provider))
    embedding = config.get("embedding", {})
    if embedding.get("provider") == "cloud":
        providers.add("embedding")
    stt = config.get("stt", {})
    if stt.get("provider") in {"groq", "openai"}:
        providers.add(str(stt["provider"]))
    return sorted(_PROVIDER_LABELS.get(item, "внешний embedding-провайдер") for item in providers)


def privacy_notice_text(*, enabled: bool | None = None) -> str:
    """Render the current privacy notice before any cloud-assisted processing."""
    providers = ", ".join(cloud_provider_labels()) or "внешние провайдеры не настроены"
    retention = int(
        settings.yaml_config.get("scheduler", {}).get("llm_log_retention_days", 90)
    )
    status = (
        "включена" if enabled is True else "отключена" if enabled is False else "не выбрана"
    )
    return (
        "🔐 Privacy и облачная обработка\n\n"
        "Для понимания свободного текста DailyPlanner может передавать внешним "
        f"AI-получателям ({providers}) текст задач, заметок, дневника и текущий "
        "контекст диалога. Голос передаётся наружу только при выбранном cloud-STT; "
        "локальные Ollama/Whisper данные наружу не отправляют.\n\n"
        "При смене получателей или API-адресов согласие запрашивается повторно.\n\n"
        "Цель: распознать намерение, выполнить поиск или транскрипцию. Содержимое "
        "LLM-запросов в журнале приложения по умолчанию не хранится; технические "
        f"метаданные хранятся до {retention} дней, backups — по операторской "
        "политике. Экспорт: /export. Запрос полного удаления: /delete_data.\n\n"
        "Без согласия AI-свободный текст блокируется, но обычные slash-команды "
        f"остаются доступны. Текущий выбор: {status}."
    )


def privacy_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="✅ Разрешить cloud AI", callback_data=f"privacy:enable:{provider_fingerprint()}")
    keyboard.button(text="🚫 Отключить cloud AI", callback_data="privacy:disable")
    keyboard.adjust(1)
    return keyboard.as_markup()
