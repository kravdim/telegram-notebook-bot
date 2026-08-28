import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pendulum
import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User

import bot.handlers.admin as admin
import bot.handlers.commands as commands
import bot.handlers.messages as messages
import bot.llm.dispatcher as dispatcher
import bot.privacy as privacy
import bot.scheduler.chronometry as chronometry_scheduler
import bot.scheduler.reindex as reindex_scheduler
from bot.application.command_bus import CommandResult
from bot.db.crud import llm_logs
from bot.db.models import Base
from bot.llm import context
from bot.llm.functions import FUNCTIONS
from bot.middleware import PrivateChatMiddleware
from bot.scheduler.task_reminders import _REMINDER_HOURS
from bot.stt.local_whisper import LocalWhisperClient
from tests.fakes import FakeMessage, FakeSessionContext


def _message(chat_type: ChatType, *, with_user: bool = True) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=-100 if chat_type != ChatType.PRIVATE else 42, type=chat_type),
        from_user=User(id=42, is_bot=False, first_name="Test") if with_user else None,
        text="/export",
    )


async def _no_persisted_interaction(user_id, state_type):
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chat_type",
    [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL],
)
async def test_non_private_messages_never_reach_handlers(chat_type):
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    result = await PrivateChatMiddleware()(handler, _message(chat_type), {})

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_message_without_sender_never_reaches_handlers():
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    result = await PrivateChatMiddleware()(
        handler, _message(ChatType.PRIVATE, with_user=False), {}
    )

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_private_message_reaches_handler():
    async def handler(event, data):
        return "handled"

    result = await PrivateChatMiddleware()(
        handler, _message(ChatType.PRIVATE), {}
    )

    assert result == "handled"


@pytest.mark.asyncio
async def test_group_callback_never_reaches_handler(monkeypatch):
    called = False
    answers = []

    async def handler(event, data):
        nonlocal called
        called = True

    callback = CallbackQuery(
        id="cb-1",
        from_user=User(id=42, is_bot=False, first_name="Test"),
        chat_instance="chat-instance",
        message=_message(ChatType.SUPERGROUP),
        data="export:anything",
    )

    async def answer(self, text=None, **kwargs):
        answers.append((text, kwargs))

    monkeypatch.setattr(CallbackQuery, "answer", answer)
    result = await PrivateChatMiddleware()(handler, callback, {})

    assert result is None
    assert called is False
    assert answers and answers[0][1] == {"show_alert": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        '{not-json "LOG-CANARY-SECRET"',
        '{"title":"LOG-CANARY-SECRET","category":"forbidden"}',
    ],
)
async def test_invalid_tool_payload_never_reaches_logs(arguments, caplog):
    caplog.set_level("DEBUG")

    result = await dispatcher.dispatch_result(
        {"name": "create_task", "arguments": arguments},
        user_id=42,
    )

    assert result.kind == "error"
    assert "LOG-CANARY-SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_tool_exception_never_reaches_logs(monkeypatch, caplog):
    caplog.set_level("DEBUG")

    class FailingBus:
        async def execute(self, intent, context):
            raise RuntimeError("LOG-CANARY-SECRET")

    monkeypatch.setattr(dispatcher, "_get_command_bus", lambda: FailingBus())
    result = await dispatcher.dispatch_result(
        {"name": "create_task", "arguments": {"title": "safe"}},
        user_id=42,
    )

    assert result == CommandResult("Произошла ошибка при обработке. Попробуй ещё раз.", "error")
    assert "LOG-CANARY-SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_provider_exception_and_user_text_never_reach_logs(
    monkeypatch, caplog
):
    caplog.set_level("DEBUG")

    class Queue:
        async def submit(self, priority, request):
            request.close()
            raise RuntimeError("LOG-CANARY-SECRET")

    async def get_user(session, user_id):
        return type("UserRecord", (), {"timezone": "Europe/Moscow"})()

    async def get_prompt(session, prompt_key):
        return "prompt {now} {timezone}"

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", get_user)
    monkeypatch.setattr(messages, "get_prompt", get_prompt)
    monkeypatch.setattr(
        messages, "_get_persisted_interaction", _no_persisted_interaction
    )
    monkeypatch.setattr(messages, "llm_queue", Queue())
    monkeypatch.setattr(
        chronometry_scheduler, "is_awaiting_response", lambda user_id: False
    )

    message = FakeMessage("USER-TEXT-CANARY-SECRET", user_id=42)
    await messages.process_text_message(42, message.text, message)

    assert "LOG-CANARY-SECRET" not in caplog.text
    assert "USER-TEXT-CANARY-SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_llm_database_log_is_metadata_only_when_payload_storage_is_disabled(
    monkeypatch,
):
    class Session:
        added = None

        def add(self, value):
            self.added = value

        async def commit(self):
            return None

    session = Session()
    monkeypatch.setattr(
        llm_logs,
        "settings",
        SimpleNamespace(yaml_config={"privacy": {"store_llm_payloads": False}}),
    )
    canary = "DB-LOG-CANARY private diary text"

    log = await llm_logs.log_llm_request(
        session,
        user_id=42,
        prompt_key="intent_detection",
        model="test",
        input_messages=[{"role": "user", "content": canary}],
        output_content=canary,
        function_call={"name": "create_note", "arguments": {"content": canary}},
        error=canary,
    )

    assert session.added is log
    assert log.input_messages == []
    assert log.output_content is None
    assert log.function_call == {
        "name": "create_note",
        "argument_keys": ["content"],
    }
    assert log.error == "redacted_error"


def test_context_is_bounded_on_every_add_and_get(monkeypatch):
    user_id = 991
    monkeypatch.setattr(context, "_MAX_TOKENS", 80)
    monkeypatch.setattr(context, "_KEEP_RECENT_PAIRS", 3)
    context.clear_history(user_id)

    for index in range(300):
        context.add_message(user_id, "user", f"request-{index} " + "x" * 80)
        context.add_message(user_id, "assistant", f"response-{index} " + "y" * 80)
        assert context._count_tokens(context.get_history(user_id)) <= 80

    history = context.get_history(user_id)
    assert len(history) <= 6
    assert "299" in history[-1]["content"]


@pytest.mark.asyncio
async def test_declined_cloud_processing_blocks_text_before_llm(monkeypatch):
    async def get_user(session, user_id):
        return type(
            "UserRecord",
            (),
            {
                "timezone": "Europe/Moscow",
                "privacy_notice_version": 1,
                "cloud_processing_enabled": False,
            },
        )()

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", get_user)
    monkeypatch.setattr(
        messages, "_get_persisted_interaction", _no_persisted_interaction
    )
    monkeypatch.setattr(messages, "llm_client", None)
    monkeypatch.setattr(messages, "llm_queue", None)
    message = FakeMessage("PRIVATE-CONTENT-CANARY", user_id=42)

    outcome = await messages.process_text_message(42, message.text, message)

    assert outcome is messages.MessageOutcome.REJECTED
    assert "облачная обработка" in message.answers[0][0]


@pytest.mark.asyncio
async def test_missing_consent_attributes_fail_closed(monkeypatch):
    async def get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    monkeypatch.setattr(messages, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(messages, "get_user", get_user)
    monkeypatch.setattr(
        messages, "_get_persisted_interaction", _no_persisted_interaction
    )
    message = FakeMessage("PRIVATE-CONTENT-CANARY", user_id=42)

    outcome = await messages.process_text_message(42, message.text, message)

    assert outcome is messages.MessageOutcome.REJECTED
    assert "Текущий выбор: не выбрана" in message.answers[0][0]


@pytest.mark.asyncio
async def test_unsupported_attachment_gets_explicit_safe_reply():
    message = FakeMessage(user_id=42)
    message.text = None
    message.photo = [object()]

    await messages.handle_text(message)

    assert "не сохраняются" in message.answers[0][0]
    assert "текст и голосовые" in message.answers[0][0]


@pytest.mark.asyncio
async def test_settings_accepts_any_valid_iana_timezone(monkeypatch):
    updates = []

    async def update_user_settings(session, user_id, **values):
        updates.append((user_id, values))

    class State:
        cleared = False

        async def clear(self):
            self.cleared = True

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "update_user_settings", update_user_settings)
    message = FakeMessage("Pacific/Auckland", user_id=42)
    state = State()

    await commands.settings_timezone_text(message, state)

    assert updates == [(42, {"timezone": "Pacific/Auckland"})]
    assert state.cleared is True
    assert "Pacific/Auckland" in message.answers[0][0]


@pytest.mark.asyncio
async def test_cloud_reindex_queries_only_consented_users(monkeypatch):
    statements = []

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        async def execute(self, statement):
            statements.append(str(statement))
            return Result()

        async def commit(self):
            return None

    monkeypatch.setattr(
        reindex_scheduler,
        "settings",
        SimpleNamespace(yaml_config={"embedding": {"provider": "cloud"}}),
    )
    monkeypatch.setattr(
        reindex_scheduler,
        "async_session",
        lambda: FakeSessionContext(Session()),
    )
    monkeypatch.setattr(reindex_scheduler, "_embed_client", object())

    await reindex_scheduler.reindex_missing_embeddings()

    assert len(statements) == 3
    assert all("users.cloud_processing_enabled IS true" in sql for sql in statements)


def test_privacy_notice_lists_active_recipients_and_user_controls(monkeypatch):
    monkeypatch.setattr(
        privacy,
        "settings",
        SimpleNamespace(
            yaml_config={
            "llm": {"main": {"provider": "openai"}},
            "embedding": {"provider": "cloud"},
            "stt": {"provider": "groq"},
            "scheduler": {"llm_log_retention_days": 30},
            }
        ),
    )

    notice = privacy.privacy_notice_text(enabled=False)

    assert "OpenAI" in notice
    assert "Groq" in notice
    assert "embedding" in notice
    assert "30 дней" in notice
    assert "/export" in notice and "/delete_data" in notice


@pytest.mark.parametrize(
    ("timezone_name", "now", "expected"),
    [
        (
            "Asia/Tokyo",
            pendulum.datetime(2026, 8, 27, 12, tz="Asia/Tokyo"),
            pendulum.datetime(2026, 7, 27, 15, tz="UTC"),
        ),
        (
            "America/New_York",
            pendulum.datetime(2026, 3, 20, 12, tz="America/New_York"),
            pendulum.datetime(2026, 2, 18, 5, tz="UTC"),
        ),
    ],
)
def test_frog_stats_uses_local_midnight_across_offsets_and_dst(
    timezone_name, now, expected
):
    assert commands._frog_stats_since_utc(timezone_name, now) == expected


@pytest.mark.asyncio
async def test_local_stt_repeated_transcription_releases_generators_and_model():
    released = []

    class NativeModel:
        def unload_model(self):
            released.append("model")

    class Model:
        model = NativeModel()

        def transcribe(self, path, **kwargs):
            def segments():
                try:
                    yield SimpleNamespace(text=" test ")
                finally:
                    released.append("segments")

            return segments(), SimpleNamespace(duration=0.1)

    client = LocalWhisperClient.__new__(LocalWhisperClient)
    client.language = "ru"
    client._model = Model()
    client._load_lock = threading.Lock()

    for _ in range(25):
        assert client._transcribe_sync(Path("voice.ogg")) == "test"
    await client.close()

    assert released.count("segments") == 25
    assert released.count("model") == 1
    assert client._model is None


@pytest.mark.asyncio
async def test_whitelist_log_never_contains_telegram_ids(monkeypatch, caplog):
    async def no_write(*args, **kwargs):
        return None

    canary_id = 9_876_543_210
    caplog.set_level("INFO")
    monkeypatch.setattr(admin.settings, "allowed_telegram_ids", [canary_id])
    monkeypatch.setattr(admin.anyio.to_thread, "run_sync", no_write)

    await admin._persist_whitelist()

    assert str(canary_id) not in caplog.text
    assert "count=1" in caplog.text


def test_critical_database_constraints_are_declared_in_orm():
    expected = {
        "users": {
            "ck_users_chronometry_interval_positive",
            "ck_users_work_time_order",
            "ck_users_work_days",
        },
        "trips": {"ck_trips_date_order"},
        "tasks": {
            "ck_tasks_remind_before_nonnegative",
            "ck_tasks_version_positive",
        },
        "time_tracking_entries": {"ck_time_tracking_duration_positive"},
        "reminders": {
            "ck_reminders_snooze_count",
            "ck_reminders_delivery_attempts",
        },
    }
    for table_name, names in expected.items():
        actual = {
            constraint.name
            for constraint in Base.metadata.tables[table_name].constraints
            if constraint.name
        }
        assert names <= actual


def test_ci_and_container_supply_chain_refs_are_immutable():
    root = Path(__file__).resolve().parents[1]
    workflows = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / ".github" / "workflows").glob("*.yml"))
    )
    assert "uses: actions/checkout@v" not in workflows
    assert "uses: astral-sh/setup-uv@v" not in workflows
    assert "uses: gitleaks/gitleaks-action@v" not in workflows
    assert "anchore/sbom-action@" in workflows
    assert "aquasecurity/trivy-action@" in workflows

    dockerfile = (root / "platform/linux/Dockerfile").read_text(encoding="utf-8")
    compose = (root / "platform/linux/docker-compose.yml").read_text(encoding="utf-8")
    assert dockerfile.count("@sha256:") == 2
    assert "pgvector/pgvector:pg16@sha256:" in compose


def test_readme_tool_count_and_reminder_schedule_match_code():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    assert f"Бот использует {len(FUNCTIONS)} функций" in readme
    schedule = ", ".join(str(hour) for hour in _REMINDER_HOURS)
    assert f"рабочее время ({schedule})" in readme
    assert {item["name"] for item in FUNCTIONS} <= {
        line.split("`")[1]
        for line in readme.splitlines()
        if line.startswith("| `")
    }
