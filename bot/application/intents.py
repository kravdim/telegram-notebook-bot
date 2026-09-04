"""Typed commands shared by deterministic and LLM intent adapters."""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from bot.application.contracts import Action, ToolName


class _Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ToolName

    def arguments(self) -> dict[str, Any]:
        return self.model_dump(exclude={"name"}, exclude_none=True)


class CreateTaskIntent(_Intent):
    name: Literal["create_task"] = "create_task"
    title: str = Field(min_length=1, max_length=500)
    category: Literal["work", "personal"] | None = None
    priority: Literal["high", "medium", "normal"] | None = None
    is_frog: bool | None = None
    scheduled_date: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    remind_at: str | None = None
    remind_before_min: int | None = Field(default=None, ge=0)
    repeat_rule: str | None = None


class TaskUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    priority: Literal["high", "medium", "normal"] | None = None
    is_frog: bool | None = None
    scheduled_date: str | None = None
    due_date: str | None = None
    due_time: str | None = None
    status: Literal["open", "done", "cancelled"] | None = None


class UpdateTaskIntent(_Intent):
    name: Literal["update_task"] = "update_task"
    search_query: str = Field(min_length=1)
    updates: TaskUpdates

    def arguments(self) -> dict[str, Any]:
        return {
            "search_query": self.search_query,
            "updates": self.updates.model_dump(exclude_unset=True),
        }


class _TaskQueryIntent(_Intent):
    search_query: str = Field(min_length=1)


class CompleteTaskIntent(_TaskQueryIntent):
    name: Literal["complete_task"] = "complete_task"


class DeleteTaskIntent(_TaskQueryIntent):
    name: Literal["delete_task"] = "delete_task"


class CreateNoteIntent(_Intent):
    name: Literal["create_note"] = "create_note"
    title: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1)
    tags: list[str] | None = None


class CreateDiaryIntent(_Intent):
    name: Literal["create_diary_entry"] = "create_diary_entry"
    content: str = Field(min_length=1)


class CreateReminderIntent(_Intent):
    name: Literal["create_reminder"] = "create_reminder"
    message: str = Field(min_length=1)
    remind_at: str
    repeat_rule: str | None = None


class ListTasksIntent(_Intent):
    name: Literal["list_tasks"] = "list_tasks"
    scope: Literal["today", "all", "overdue", "done_today"] | None = None


class SearchIntent(_Intent):
    name: Literal["search"] = "search"
    query: str = Field(min_length=1)
    scope: Literal["all", "tasks", "notes", "diary", "memoir"] | None = None


class CreateProjectIntent(_Intent):
    name: Literal["create_project"] = "create_project"
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    category: Literal["work", "personal"] | None = None


class CompleteProjectIntent(_TaskQueryIntent):
    name: Literal["complete_project"] = "complete_project"


class AddBirthdayIntent(_Intent):
    name: Literal["add_birthday"] = "add_birthday"
    person_name: str = Field(min_length=1)
    date: str
    year_known: bool | None = None
    note: str | None = None

    def arguments(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"name"}, exclude_none=True)
        data["name"] = data.pop("person_name")
        return data


class GetAdviceIntent(_Intent):
    name: Literal["get_advice"] = "get_advice"
    query: str = Field(min_length=1)


class RespondIntent(_Intent):
    name: Literal["respond_to_user"] = "respond_to_user"
    message: str = Field(min_length=1, max_length=4000)


class ClarifyIntent(_Intent):
    name: Literal["clarify_request"] = "clarify_request"
    question: str = Field(min_length=1, max_length=1000)


ApplicationIntent: TypeAlias = (
    CreateTaskIntent
    | UpdateTaskIntent
    | CompleteTaskIntent
    | DeleteTaskIntent
    | CreateNoteIntent
    | CreateDiaryIntent
    | CreateReminderIntent
    | ListTasksIntent
    | SearchIntent
    | CreateProjectIntent
    | CompleteProjectIntent
    | AddBirthdayIntent
    | GetAdviceIntent
    | RespondIntent
    | ClarifyIntent
)


_INTENT_MODELS: dict[ToolName, type[_Intent]] = {
    "create_task": CreateTaskIntent,
    "update_task": UpdateTaskIntent,
    "complete_task": CompleteTaskIntent,
    "delete_task": DeleteTaskIntent,
    "create_note": CreateNoteIntent,
    "create_diary_entry": CreateDiaryIntent,
    "create_reminder": CreateReminderIntent,
    "list_tasks": ListTasksIntent,
    "search": SearchIntent,
    "create_project": CreateProjectIntent,
    "complete_project": CompleteProjectIntent,
    "add_birthday": AddBirthdayIntent,
    "get_advice": GetAdviceIntent,
    "respond_to_user": RespondIntent,
    "clarify_request": ClarifyIntent,
}


def intent_from_action(action: Action) -> ApplicationIntent:
    """Validate generic provider output as a concrete application command."""
    model = _INTENT_MODELS[action.name]
    arguments = dict(action.arguments)
    if action.name == "add_birthday" and "name" in arguments:
        arguments["person_name"] = arguments.pop("name")
    payload = {"name": action.name, **arguments}
    return model.model_validate(payload)  # type: ignore[return-value]


def intent_from_parts(name: ToolName, arguments: dict[str, Any]) -> ApplicationIntent:
    return intent_from_action(Action(name=name, arguments=arguments))
