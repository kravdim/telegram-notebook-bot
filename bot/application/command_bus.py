"""Small typed command bus independent of Telegram and LLM providers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from bot.application.contracts import ToolName
from bot.application.intents import ApplicationIntent


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
        "error",
    ] = "message"
    payload: dict[str, Any] | list[dict[str, Any]] | None = None

    def dict_payload(self) -> dict[str, Any]:
        return self.payload if isinstance(self.payload, dict) else {}

    def list_payload(self) -> list[dict[str, Any]]:
        return self.payload if isinstance(self.payload, list) else []

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
