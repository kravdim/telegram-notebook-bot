"""Small typed command bus independent of Telegram and LLM providers."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from bot.application.intents import ApplicationIntent
from bot.llm.contracts import ToolName


@dataclass(frozen=True, slots=True)
class CommandContext:
    user_id: int
    timezone: str = "Europe/Moscow"


@dataclass(frozen=True, slots=True)
class CommandResult:
    text: str
    kind: Literal[
        "message",
        "confirm_delete",
        "choose_delete",
        "confirm_project_complete",
        "project_created",
    ] = "message"
    payload: dict[str, Any] | list[dict[str, Any]] | None = None

    @classmethod
    def from_legacy_text(cls, text: str) -> "CommandResult":
        """Contain legacy handler protocols at one application boundary."""
        if text.startswith("CONFIRM_DELETE:"):
            _, task_id, title = text.split(":", 2)
            return cls(text, "confirm_delete", {"task_id": task_id, "title": title})
        if text.startswith("CHOOSE_DELETE:"):
            choices = json.loads(text.split(":", 1)[1])
            return cls(text, "choose_delete", choices)
        if text.startswith("CONFIRM_PROJECT_COMPLETE:"):
            _, project_id, title, open_count = text.split(":", 3)
            return cls(
                text,
                "confirm_project_complete",
                {"project_id": project_id, "title": title, "open_count": open_count},
            )
        if text.startswith("PROJECT_CREATED:"):
            _, project_id, title = text.split(":", 2)
            return cls(text, "project_created", {"project_id": project_id, "title": title})
        return cls(text)


CommandHandler = Callable[[CommandContext, ApplicationIntent], Awaitable[CommandResult]]


class CommandBus:
    def __init__(self) -> None:
        self._handlers: dict[ToolName, CommandHandler] = {}

    def register(self, name: ToolName, handler: CommandHandler) -> None:
        if name in self._handlers:
            raise ValueError(f"Handler already registered: {name}")
        self._handlers[name] = handler

    async def execute(
        self, intent: ApplicationIntent, context: CommandContext
    ) -> CommandResult:
        handler = self._handlers.get(intent.name)
        if handler is None:
            raise LookupError(f"No handler registered for {intent.name}")
        return await handler(context, intent)
