"""Contracts for the provider-independent application layer."""

import pytest
from pydantic import ValidationError

from bot.application.command_bus import (
    CommandBus,
    CommandContext,
    CommandResult,
)
from bot.application.intents import (
    AddBirthdayIntent,
    ClarifyIntent,
    CreateTaskIntent,
    intent_from_parts,
)
from bot.application.normalizer import intent_normalizer


def test_provider_action_becomes_concrete_typed_intent():
    intent = intent_from_parts(
        "create_task",
        {"title": "Позвонить врачу", "priority": "high"},
    )
    assert isinstance(intent, CreateTaskIntent)
    assert intent.arguments() == {"title": "Позвонить врачу", "priority": "high"}


def test_birthday_argument_name_does_not_collide_with_command_name():
    intent = intent_from_parts(
        "add_birthday",
        {"name": "мама", "date": "1900-03-15", "year_known": False},
    )
    assert isinstance(intent, AddBirthdayIntent)
    assert intent.name == "add_birthday"
    assert intent.arguments()["name"] == "мама"


def test_unknown_command_argument_fails_closed():
    with pytest.raises(ValidationError):
        intent_from_parts("complete_task", {"search_query": "отчёт", "user_id": 1})


def test_clarification_is_a_dedicated_non_mutating_intent():
    intent = intent_from_parts(
        "clarify_request", {"question": "Когда напомнить?"}
    )
    assert isinstance(intent, ClarifyIntent)
    assert intent.arguments() == {"question": "Когда напомнить?"}


@pytest.mark.asyncio
async def test_command_bus_routes_typed_intent_without_provider_payload():
    bus = CommandBus()

    async def handler(context, intent):
        return CommandResult(f"{context.user_id}:{intent.arguments()['search_query']}")

    bus.register("complete_task", handler)
    result = await bus.execute(
        intent_from_parts("complete_task", {"search_query": "отчёт"}),
        CommandContext(user_id=42),
    )
    assert result.text == "42:отчёт"


def test_legacy_ui_protocol_is_contained_in_typed_result():
    result = CommandResult.from_legacy_text("CONFIRM_DELETE:abc:Старый отчёт")
    assert result.kind == "confirm_delete"
    assert result.payload == {"task_id": "abc", "title": "Старый отчёт"}


def test_normalizer_preserves_raw_text_and_opaque_marker():
    normalized = intent_normalizer.normalize(
        "напмни через полчаса про Б22-договор"
    )
    assert normalized.text == "напомни через 30 минут про Б22-договор"
    assert normalized.opaque_marker == "Б22-договор"
    assert normalized.raw_text.startswith("напмни")
