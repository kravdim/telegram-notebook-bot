"""Типизированный контракт между LLM intent detection и dispatcher."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ToolName = Literal[
    "create_task", "complete_task", "create_note", "create_diary_entry",
    "create_reminder", "list_tasks", "add_birthday", "get_advice",
    "respond_to_user", "search", "update_task", "delete_task",
    "create_project", "complete_project",
]


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    choices: list[str] = Field(default_factory=list, max_length=10)


class IntentResult(BaseModel):
    """Нормализованный результат: действия либо уточнение, но не оба сразу."""

    model_config = ConfigDict(extra="forbid")

    actions: list[Action] = Field(default_factory=list, max_length=20)
    clarification: Clarification | None = None

    def is_actionable(self) -> bool:
        return bool(self.actions) and self.clarification is None
