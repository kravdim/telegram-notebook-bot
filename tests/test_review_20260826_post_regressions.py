from types import SimpleNamespace

import pytest
import yaml

import bot.handlers.callbacks as callbacks
import bot.handlers.messages as messages
import bot.scheduler.chronometry as chronometry_scheduler
import scripts.delete_user_data as deletion_script
from tests.fakes import FakeCallback, FakeMessage, FakeSession, FakeSessionContext


class _Response:
    def __init__(self, content: str):
        self.content = content
        self.function_call = None
        self.function_calls = []
        self.model = "non-compliant-provider"
        self.total_tokens = 1
        self.latency_ms = 1


def test_failed_live_reminder_phrase_has_deterministic_mutation_path():
    text = (
        "слушай напомни через 2 минуты "
        "DP-20260826T164140-62c608-чай попить, а то забуду"
    )

    tool, arguments = messages._extract_common_mutation(text, "Europe/Moscow")

    assert tool == "create_reminder"
    assert arguments["message"] == "DP-20260826T164140-62c608-чай попить"
    assert arguments["remind_at"]


def test_explicit_diary_phrase_has_deterministic_mutation_path():
    tool, arguments = messages._extract_common_mutation(
        "запиши в дневник: сегодня был странный день",
        "Europe/Moscow",
    )

    assert tool == "create_diary_entry"
    assert arguments == {"content": "сегодня был странный день"}


@pytest.mark.parametrize(
    ("text", "expected_fragment"),
    [
        ("поставь задачу на вчера: сдать отчёт", "прошлом"),
        ("напомни через 0 минут проверить духовку", "0 минут"),
        ("напомни вчера позвонить маме", "прошлом"),
        ("запомни день рождения кота Барсика 32 января", "такой даты нет"),
        ("у мамы день рождения был вчера", "точную дату"),
    ],
)
def test_invalid_dates_have_deterministic_clarification(
    text, expected_fragment
):
    tool, arguments = messages._extract_common_mutation(text, "Europe/Moscow")

    assert tool == "respond_to_user"
    assert expected_fragment in arguments["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_claim",
    [
        "Готово! Через 2 минуты напомню. ☕",
        "Я напомню через 2 минуты.",
        "Готово, поставил напоминание.",
    ],
)
async def test_mutation_without_typed_result_fails_closed(
    provider_claim, monkeypatch
):
    class Client:
        async def chat(self, *args, **kwargs):
            return _Response(provider_claim)

    class Queue:
        calls = 0

        async def submit(self, priority, request):
            self.calls += 1
            return await request

    queue = Queue()
    finished = []

    async def get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    async def no_state(*args):
        return None

    async def no_claim(*args):
        return None

    async def finish(key, status):
        finished.append(status)

    async def forbidden_dispatch(*args):
        raise AssertionError("no mutation may be dispatched without a tool call")

    monkeypatch.setattr(messages, "llm_client", Client())
    monkeypatch.setattr(messages, "llm_queue", queue)
    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", get_user)
    monkeypatch.setattr(messages, "get_prompt", get_prompt)
    monkeypatch.setattr(messages, "_get_persisted_interaction", no_state)
    monkeypatch.setattr(messages, "_claim_request", no_claim)
    monkeypatch.setattr(messages, "_finish_request", finish)
    monkeypatch.setattr(messages, "dispatch_result", forbidden_dispatch)
    monkeypatch.setattr(
        chronometry_scheduler, "is_awaiting_response", lambda user_id: False
    )

    text = "поставь пожалуйста напоминание про чай через 2 минуты"
    message = FakeMessage(text, user_id=42)
    outcome = await messages.process_text_message(42, text, message)

    assert queue.calls == 2
    assert outcome == messages.MessageOutcome.RETRYABLE_ERROR
    assert finished == ["failed"]
    assert "Не удалось выполнить изменение" in message.answers[-1][0]
    assert provider_claim not in [answer[0] for answer in message.answers]


@pytest.mark.asyncio
async def test_memoir_skip_does_not_confirm_when_state_clear_fails(monkeypatch):
    state = SimpleNamespace(
        state_type="memoir",
        payload={"session_token": "session-a", "message_id": 999},
    )

    async def get_state(*args):
        return state

    async def fail_clear(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(callbacks.interaction_service, "get", get_state)
    monkeypatch.setattr(callbacks.interaction_service, "clear", fail_clear)
    callback = FakeCallback(user_id=42, data="memoir_skip:session-a")

    await callbacks.cb_memoir_skip(callback)

    assert callback.answered == [
        ("Не удалось пропустить. Попробуй ещё раз.", {"show_alert": True})
    ]
    assert callback.message.edits == []


@pytest.mark.asyncio
async def test_completed_privacy_journal_reopens_after_reonboarding(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"bot": {"allowed_telegram_ids": [42]}}),
        encoding="utf-8",
    )
    journal = {
        "privacy.deletion.42": {
            "operation_id": "old-operation",
            "phase": "completed",
            "telegram_id": 42,
            "deleted_counts": {"users": 1},
        }
    }
    count_results = iter([{"users": 1, "tasks": 1}, {"users": 0, "tasks": 0}])
    deleted = []

    class Lease:
        def __init__(self, engine):
            pass

        async def acquire(self):
            return True

        async def release(self):
            return None

    async def get_operation(session, key):
        return SimpleNamespace(value=journal[key]) if key in journal else None

    async def set_operation(session, key, value, *, commit=True):
        journal[key] = value
        return SimpleNamespace(value=value)

    async def counts(session, user_id):
        return next(count_results)

    async def delete_data(session, user_id):
        deleted.append(user_id)
        return {"users": 1, "tasks": 1}

    monkeypatch.setattr(
        deletion_script, "async_session", lambda: FakeSessionContext(FakeSession())
    )
    monkeypatch.setattr(deletion_script, "SingletonLease", Lease)
    monkeypatch.setattr(deletion_script, "get_operational_state", get_operation)
    monkeypatch.setattr(deletion_script, "set_operational_state", set_operation)
    monkeypatch.setattr(deletion_script, "user_data_counts", counts)
    monkeypatch.setattr(deletion_script, "delete_user_data", delete_data)
    monkeypatch.setattr(deletion_script.settings, "admin_telegram_ids", [])
    monkeypatch.setattr(deletion_script.settings, "allow_all_users", False)
    monkeypatch.setattr(deletion_script.settings, "allowed_telegram_ids", [42])

    result = await deletion_script.run(
        SimpleNamespace(
            telegram_id=42,
            execute=True,
            confirm="DELETE-42",
            config=config_path,
        )
    )

    assert result["mode"] == "executed"
    assert result["verification_counts"] == {"users": 0, "tasks": 0}
    assert deleted == [42]
    assert journal["privacy.deletion.42"]["phase"] == "completed"
    assert journal["privacy.deletion.42"]["operation_id"] != "old-operation"
