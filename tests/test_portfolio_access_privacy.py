"""Critical access, consent, privacy and export boundary scenarios."""

from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User

import bot.handlers.commands as commands
import bot.handlers.onboarding as onboarding
import bot.handlers.privacy as privacy_handlers
import bot.handlers.trip as trip
import bot.middleware as middleware
from bot.middleware import PrivateChatMiddleware, RateLimitMiddleware, WhitelistMiddleware
from bot.privacy import provider_fingerprint
from bot.services.export import ExportTooLargeError
from tests.fakes import FakeCallback, FakeMessage, FakeSessionContext


class FakeState:
    def __init__(self, data=None, current_state=None):
        self.data = data or {}
        self.current_state = current_state
        self.cleared = False

    async def get_data(self):
        return self.data.copy()

    async def update_data(self, *args, **values):
        for value in args:
            self.data.update(value)
        self.data.update(values)

    async def set_state(self, state):
        self.current_state = state

    async def get_state(self):
        return self.current_state

    async def clear(self):
        self.cleared = True
        self.current_state = None


def aiogram_message(chat_type=ChatType.PRIVATE, *, with_user=True):
    return Message(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=Chat(id=42, type=chat_type),
        from_user=User(id=42, is_bot=False, first_name="Test") if with_user else None,
        text="/export",
    )


@pytest.mark.asyncio
async def test_private_callback_without_chat_is_rejected_before_handler(monkeypatch):
    alerts = []
    called = False
    callback = CallbackQuery(
        id="callback-without-chat",
        from_user=User(id=42, is_bot=False, first_name="Test"),
        chat_instance="instance",
        data="privacy:enable",
    )

    async def answer(self, text=None, **kwargs):
        alerts.append((text, kwargs))

    async def handler(event, data):
        nonlocal called
        called = True

    monkeypatch.setattr(CallbackQuery, "answer", answer)
    assert await PrivateChatMiddleware()(handler, callback, {}) is None
    assert called is False
    assert alerts == [("Эта кнопка доступна только в личном чате с ботом.", {"show_alert": True})]


@pytest.mark.asyncio
async def test_whitelist_rejects_unknown_message_and_never_calls_handler(monkeypatch):
    replies = []
    called = False

    async def answer(self, text, **kwargs):
        replies.append(text)

    async def handler(event, data):
        nonlocal called
        called = True

    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(
        middleware,
        "settings",
        SimpleNamespace(allow_all_users=False, allowed_telegram_ids=[], admin_telegram_ids=[]),
    )
    assert await WhitelistMiddleware()(handler, aiogram_message(), {}) is None
    assert called is False
    assert "нет доступа" in replies[0]


@pytest.mark.asyncio
async def test_whitelist_administrator_is_allowed(monkeypatch):
    monkeypatch.setattr(
        middleware,
        "settings",
        SimpleNamespace(allow_all_users=False, allowed_telegram_ids=[], admin_telegram_ids=[42]),
    )

    async def handler(event, data):
        return event.from_user.id

    assert await WhitelistMiddleware()(handler, aiogram_message(), {}) == 42


@pytest.mark.asyncio
async def test_rate_limit_blocks_callback_and_discards_expired_timestamps(monkeypatch):
    times = iter((100.0, 101.0, 170.0))
    answers = []
    callback = CallbackQuery(
        id="rate-limit",
        from_user=User(id=42, is_bot=False, first_name="Test"),
        chat_instance="instance",
        message=aiogram_message(),
        data="privacy:disable",
    )

    async def answer(self, text=None, **kwargs):
        answers.append(text)

    async def handler(event, data):
        return "handled"

    monkeypatch.setattr(middleware, "time", SimpleNamespace(monotonic=lambda: next(times)))
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    limiter = RateLimitMiddleware(window=60, max_requests=1)
    assert await limiter(handler, callback, {}) == "handled"
    assert await limiter(handler, callback, {}) is None
    assert answers == ["Слишком частые нажатия. Подожди минутку ⏳"]
    assert await limiter(handler, callback, {}) == "handled"


@pytest.mark.asyncio
async def test_privacy_screen_displays_persisted_choice(monkeypatch):
    async def get_user(session, user_id):
        assert user_id == 42
        return SimpleNamespace(cloud_processing_enabled=False)

    monkeypatch.setattr(privacy_handlers, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(privacy_handlers, "get_user", get_user)
    message = FakeMessage("/privacy", user_id=42)
    await privacy_handlers.cmd_privacy(message)
    text, kwargs = message.answers[0]
    assert "Текущий выбор: отключена" in text
    assert kwargs["reply_markup"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, expected", [("privacy:enable", True), ("privacy:disable", False)])
async def test_privacy_choice_persists_decision_and_refreshes_notice(
    monkeypatch, payload, expected
):
    updated = []

    async def update(session, user_id, **values):
        updated.append((user_id, values))
        return SimpleNamespace(id=user_id)

    monkeypatch.setattr(privacy_handlers, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(privacy_handlers, "update_user_settings", update)
    callback = FakeCallback(user_id=42, data=f"{payload}:{provider_fingerprint()}" if expected else payload)
    await privacy_handlers.cb_privacy_choice(callback)
    assert updated == [
        (42, {"privacy_notice_version": 1, "cloud_processing_enabled": expected,
              "privacy_provider_fingerprint": provider_fingerprint() if expected else None})
    ]
    assert callback.answered == [("Настройка сохранена", {})]
    assert f"Текущий выбор: {'включена' if expected else 'отключена'}" in callback.message.edits[0][0]


@pytest.mark.asyncio
async def test_privacy_choice_for_missing_user_reports_onboarding(monkeypatch):
    async def update(session, user_id, **values):
        return None

    monkeypatch.setattr(privacy_handlers, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(privacy_handlers, "update_user_settings", update)
    callback = FakeCallback(user_id=42, data=f"privacy:enable:{provider_fingerprint()}")
    await privacy_handlers.cb_privacy_choice(callback)
    assert callback.answered == [("Сначала выполни /start", {})]
    assert "Текущий выбор: не выбрана" in callback.message.edits[0][0]


@pytest.mark.asyncio
async def test_delete_data_command_explains_safe_operator_orchestration():
    message = FakeMessage("/delete_data", user_id=42)
    await privacy_handlers.cmd_delete_data(message)
    assert "/export" in message.answers[0][0]
    assert "подтверждаемую процедуру" in message.answers[0][0]


@pytest.mark.asyncio
async def test_completed_user_start_with_declined_consent_stays_local(monkeypatch):
    async def get_user(session, user_id):
        return SimpleNamespace(
            username="Ира",
            onboarding_completed=True,
            privacy_notice_version=1,
            cloud_processing_enabled=False,
        )

    monkeypatch.setattr(onboarding, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(onboarding, "get_user", get_user)
    message = FakeMessage("/start", user_id=42)
    await onboarding.cmd_start(message, FakeState())
    assert "Cloud AI отключён" in message.answers[0][0]
    assert "/privacy" in message.answers[0][0]


@pytest.mark.asyncio
async def test_returning_onboarding_privacy_choice_persists_and_finishes(monkeypatch):
    updates = []

    async def get_user(session, user_id):
        return SimpleNamespace(id=user_id)

    async def update(session, user_id, **values):
        updates.append((user_id, values))

    monkeypatch.setattr(onboarding, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(onboarding, "get_user", get_user)
    monkeypatch.setattr(onboarding, "update_user_settings", update)
    state = FakeState({"privacy_return": "completed"})
    callback = FakeCallback(user_id=42, data="onb_privacy_decline")
    await onboarding.onb_privacy_choice(callback, state)
    assert updates == [(42, {"privacy_notice_version": 1, "cloud_processing_enabled": False,
                             "privacy_provider_fingerprint": None})]
    assert state.cleared is True
    assert "slash-команды" in callback.message.edits[0][0]


@pytest.mark.asyncio
async def test_new_onboarding_privacy_choice_sends_name_step_without_creating_user(monkeypatch):
    sent_names = []

    async def no_user(session, user_id):
        return None

    async def send_name(message, state, name):
        sent_names.append((message, state, name))

    monkeypatch.setattr(onboarding, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(onboarding, "get_user", no_user)
    monkeypatch.setattr(onboarding, "_send_name_step", send_name)
    state = FakeState({"suggested_name": "Лена", "privacy_return": "onboarding",
                       "privacy_offered_fingerprint": provider_fingerprint()})
    callback = FakeCallback(user_id=42, data=f"onb_privacy_accept:{provider_fingerprint()}")
    await onboarding.onb_privacy_choice(callback, state)
    assert state.data["cloud_processing_enabled"] is True
    assert sent_names == [(callback.message, state, "Лена")]


@pytest.mark.asyncio
async def test_finish_onboarding_keeps_declined_cloud_path_and_persists_settings(monkeypatch):
    updates = []

    async def update(session, user_id, **values):
        updates.append((user_id, values))

    monkeypatch.setattr(onboarding, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(onboarding, "update_user_settings", update)
    state = FakeState(
        {
            "username": "Лена",
            "timezone": "Asia/Omsk",
            "digest_morning": "07:30",
            "cloud_processing_enabled": False,
        }
    )
    message = FakeMessage(user_id=42)
    await onboarding._finish_onboarding(message, 42, state)
    assert updates[0][1]["onboarding_completed"] is True
    assert updates[0][1]["digest_morning_time"].strftime("%H:%M") == "07:30"
    assert "свободный текст и голос не обрабатываются" in message.answers[0][0]
    assert state.cleared is True


@pytest.mark.asyncio
async def test_export_builds_archive_and_sends_versioned_document(monkeypatch):
    calls = []

    async def build_sections(session, user_id, data_dir, *, max_bytes):
        calls.append(("build", user_id, data_dir.name, max_bytes))
        return {"manifest.json": b"{}"}

    def write_archive(path, sections, *, max_bytes):
        calls.append(("write", path.name, sections, max_bytes))
        path.write_bytes(b"zip")

    async def answer_document(self, document, **kwargs):
        calls.append(("document", document.filename, kwargs["caption"]))

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "build_user_export_sections", build_sections)
    monkeypatch.setattr(commands, "write_export_archive", write_archive)
    monkeypatch.setattr(
        commands, "settings", SimpleNamespace(yaml_config={"export": {"max_bytes": 123}})
    )
    monkeypatch.setattr(FakeMessage, "answer_document", answer_document, raising=False)
    message = FakeMessage("/export", user_id=42)
    await commands.cmd_export(message)
    assert message.bot.actions == [(42, "typing")]
    assert calls[0] == ("build", 42, "data", 123)
    assert calls[1] == ("write", "export.zip", {"manifest.json": b"{}"}, 123)
    assert calls[2][0] == "document"
    assert calls[2][1].startswith("dailyplanner_export_v1_")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, expected_text",
    [
        (ExportTooLargeError("too large"), "Экспорт слишком большой"),
        (RuntimeError("storage unavailable"), "Не удалось подготовить экспорт"),
    ],
)
async def test_export_errors_are_safe_and_do_not_send_document(monkeypatch, failure, expected_text):
    async def build_sections(session, user_id, data_dir, *, max_bytes):
        raise failure

    async def answer_document(self, document, **kwargs):
        raise AssertionError("a failed export must not be sent")

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "build_user_export_sections", build_sections)
    monkeypatch.setattr(FakeMessage, "answer_document", answer_document, raising=False)
    message = FakeMessage("/export", user_id=42)
    await commands.cmd_export(message)
    assert expected_text in message.answers[0][0]


@pytest.mark.asyncio
async def test_help_lists_access_and_privacy_controls():
    message = FakeMessage("/help", user_id=42)
    await commands.cmd_help(message)
    text, kwargs = message.answers[0]
    assert all(command in text for command in ("/export", "/privacy", "/delete_data"))
    assert kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
@pytest.mark.parametrize("argument, expected_route", [
    ("frogs", "frogs"),
    ("productivity", "productivity"),
    ("values", "values"),
])
async def test_stats_routes_only_to_requested_aggregate(monkeypatch, argument, expected_route):
    routes = []

    async def route(name):
        routes.append(name)

    monkeypatch.setattr(commands, "_stats_frogs", lambda message: route("frogs"))
    monkeypatch.setattr(
        commands, "_stats_productivity", lambda message: route("productivity")
    )
    monkeypatch.setattr(commands, "_stats_values", lambda message: route("values"))
    await commands.cmd_stats(FakeMessage("/stats", user_id=42), SimpleNamespace(args=argument))
    assert routes == [expected_route]


@pytest.mark.asyncio
async def test_stats_unknown_argument_shows_supported_options():
    message = FakeMessage("/stats", user_id=42)
    await commands.cmd_stats(message, SimpleNamespace(args="everything"))
    assert "/stats frogs" in message.answers[0][0]
    assert message.answers[0][1]["parse_mode"] == "HTML"


def settings_user(**overrides):
    values = {
        "timezone": "Europe/Moscow",
        "digest_morning_time": time(8),
        "digest_evening_time": time(21),
        "memoir_prompt_time": time(20, 45),
        "work_start_time": time(9),
        "work_end_time": time(18),
        "work_days": [1, 2, 3, 4, 5],
        "chronometry_enabled": True,
        "chronometry_interval_min": 60,
        "digest_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_settings_handles_absent_user_and_renders_full_controls(monkeypatch):
    async def absent(session, user_id):
        return None

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "get_user", absent)
    missing = FakeMessage("/settings", user_id=42)
    await commands.cmd_settings(missing)
    assert "/start" in missing.answers[0][0]

    async def present(session, user_id):
        return settings_user(digest_enabled=False)

    monkeypatch.setattr(commands, "get_user", present)
    configured = FakeMessage("/settings", user_id=42)
    await commands.cmd_settings(configured)
    text, kwargs = configured.answers[0]
    assert "Дайджесты: ❌" in text
    assert len(kwargs["reply_markup"].inline_keyboard) == 9


@pytest.mark.asyncio
async def test_custom_timezone_validation_does_not_persist_invalid_value(monkeypatch):
    updated = []

    async def update(session, user_id, **values):
        updated.append(values)

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "update_user_settings", update)
    state = FakeState()
    invalid = FakeMessage("Not/AZone", user_id=42)
    await commands.settings_timezone_text(invalid, state)
    assert "Неизвестный timezone" in invalid.answers[0][0]
    assert updated == []
    assert state.cleared is False

    valid = FakeMessage("Asia/Omsk", user_id=42)
    await commands.settings_timezone_text(valid, state)
    assert updated == [{"timezone": "Asia/Omsk"}]
    assert state.cleared is True


@pytest.mark.asyncio
async def test_workday_toggle_preserves_at_least_one_day(monkeypatch):
    updates = []
    refreshed = []

    async def get_user(session, user_id):
        return settings_user(work_days=[1])

    async def update(session, user_id, **values):
        updates.append(values)

    async def refresh(callback):
        refreshed.append(callback.data)

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "get_user", get_user)
    monkeypatch.setattr(commands, "update_user_settings", update)
    monkeypatch.setattr(commands, "cb_work_days", refresh)
    callback = FakeCallback(user_id=42, data="settings:toggle_day:1")
    await commands.cb_toggle_day(callback)
    assert updates == []
    assert callback.answered == [("Нужен хотя бы один рабочий день.", {"show_alert": True})]
    assert refreshed == []


@pytest.mark.asyncio
async def test_workday_toggle_persists_sorted_days_and_refreshes(monkeypatch):
    updates = []
    refreshed = []

    async def get_user(session, user_id):
        return settings_user(work_days=[1, 3])

    async def update(session, user_id, **values):
        updates.append(values)

    async def refresh(callback):
        refreshed.append(callback.data)

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "get_user", get_user)
    monkeypatch.setattr(commands, "update_user_settings", update)
    monkeypatch.setattr(commands, "cb_work_days", refresh)
    callback = FakeCallback(user_id=42, data="settings:toggle_day:2")
    await commands.cb_toggle_day(callback)
    assert updates == [{"work_days": [1, 2, 3]}]
    assert callback.answered == [(None, {})]
    assert refreshed == ["settings:toggle_day:2"]


@pytest.mark.asyncio
async def test_projects_empty_and_progress_views(monkeypatch):
    async def no_projects(session, user_id):
        return []

    monkeypatch.setattr(commands, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(commands, "get_user_projects", no_projects)
    empty = FakeMessage("/projects", user_id=42)
    await commands.cmd_projects(empty)
    assert "Слонов пока нет" in empty.answers[0][0]

    project = SimpleNamespace(id=7, title="<Migration>")

    async def projects(session, user_id):
        return [project]

    async def progress(session, project_ids):
        assert project_ids == [7]
        return {7: {"percent": 40, "done": 2, "total": 5}}

    from bot.db.crud import projects as projects_crud

    monkeypatch.setattr(commands, "get_user_projects", projects)
    monkeypatch.setattr(projects_crud, "batch_project_progress", progress)
    populated = FakeMessage("/projects", user_id=42)
    await commands.cmd_projects(populated)
    assert "&lt;Migration&gt;" in populated.answers[0][0]
    assert "40% (2/5)" in populated.answers[0][0]


@pytest.mark.asyncio
async def test_trip_command_validates_unknown_and_off_without_active_trip(monkeypatch):
    unknown = FakeMessage("/trip", user_id=42)
    await trip.cmd_trip(unknown, SimpleNamespace(args="maybe"))
    assert "/trip on" in unknown.answers[0][0]

    async def get_user(session, user_id):
        return SimpleNamespace(timezone="Europe/Moscow")

    async def no_trip(session, user_id, current_date):
        return None

    monkeypatch.setattr(trip, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(trip, "get_user", get_user)
    monkeypatch.setattr(trip, "get_open_trip", no_trip)
    off = FakeMessage("/trip off", user_id=42)
    await trip.cmd_trip(off, SimpleNamespace(args="off"))
    assert off.answers == [("Нет активной командировки.", {})]


@pytest.mark.asyncio
async def test_onboarding_digest_validates_before_advancing_and_keeps_defaults(monkeypatch):
    advanced = []

    async def next_step(message, state):
        advanced.append(state.data.copy())

    monkeypatch.setattr(onboarding, "_send_work_schedule_step", next_step)
    state = FakeState()
    invalid = FakeMessage("утро=25:00", user_id=42)
    await onboarding.onb_digest_text(invalid, state)
    assert "Не понял время утра" in invalid.answers[0][0]
    assert advanced == []

    valid = FakeMessage("утро=07:30 memoir=19:00", user_id=42)
    await onboarding.onb_digest_text(valid, state)
    assert advanced == [
        {
            "digest_morning": "07:30",
            "digest_evening": "21:00",
            "memoir_prompt": "19:00",
        }
    ]


@pytest.mark.asyncio
async def test_onboarding_work_schedule_rejects_reversed_time_then_advances(monkeypatch):
    advanced = []

    async def next_step(message, state):
        advanced.append(state.data.copy())

    monkeypatch.setattr(onboarding, "_send_concepts_step", next_step)
    state = FakeState()
    reversed_schedule = FakeMessage("дни=пн,вт начало=18:00 конец=09:00", user_id=42)
    await onboarding.onb_work_text(reversed_schedule, state)
    assert "позже начала" in reversed_schedule.answers[0][0]
    assert advanced == []

    valid_schedule = FakeMessage("days=mon,wed start=09:00 end=17:30", user_id=42)
    await onboarding.onb_work_text(valid_schedule, state)
    assert advanced == [{"work_days": [1, 3], "work_start": "09:00", "work_end": "17:30"}]


@pytest.mark.asyncio
async def test_first_onboarding_task_rejects_empty_and_finishes_after_create(monkeypatch):
    finished = []

    async def create(session, user_id, *, title):
        return SimpleNamespace(title=title)

    async def finish(message, user_id, state):
        finished.append((message, user_id, state))

    monkeypatch.setattr(onboarding, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(onboarding, "create_task", create)
    monkeypatch.setattr(onboarding, "_finish_onboarding", finish)
    state = FakeState()
    blank = FakeMessage("", user_id=42)
    await onboarding.onb_first_task(blank, state)
    assert "Не понял задачу" in blank.answers[0][0]
    assert finished == []

    task = FakeMessage("Закончить проверку", user_id=42)
    await onboarding.onb_first_task(task, state)
    assert task.answers == [("Задача создана: Закончить проверку ✅", {})]
    assert finished == [(task, 42, state)]
