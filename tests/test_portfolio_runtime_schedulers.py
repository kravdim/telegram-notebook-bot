"""Focused runtime and scheduler contracts without real DB or Telegram I/O."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pendulum
import pytest

from bot import main, observability
from bot.scheduler import (
    backup,
    digest,
    healthcheck,
    log_rotation,
    reminders,
    sweep,
    task_reminders,
    weekly_review,
)
from tests.fakes import FakeSessionContext


class RecordingSession:
    def __init__(self, results=()):
        self.execute = AsyncMock(side_effect=list(results))
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


@pytest.mark.asyncio
async def test_runtime_cleanup_continues_after_a_failed_resource(monkeypatch):
    calls = []

    class Queue:
        async def stop(self):
            calls.append("queue")
            raise RuntimeError("already stopped")

    class Stt:
        async def close(self):
            calls.append("stt")

    class Engine:
        async def dispose(self):
            calls.append("engine")

    monkeypatch.setattr(main, "engine", Engine())
    bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock(side_effect=lambda: calls.append("telegram"))))
    lease = SimpleNamespace(release=AsyncMock(side_effect=lambda: calls.append("lease")))

    await main._cleanup_runtime_resources(lease, bot, Queue(), Stt())

    assert calls == ["queue", "stt", "telegram", "lease", "engine"]


def test_backup_due_handles_pre_slot_and_previous_day():
    before_slot = pendulum.datetime(2026, 8, 3, 2, tz="Europe/Moscow")
    after_slot = before_slot.add(hours=2)

    assert backup.is_backup_due(None, before_slot, 3) is False
    assert backup.is_backup_due(None, after_slot, 3) is True
    assert backup.is_backup_due(after_slot, after_slot, 3) is False
    assert backup.is_backup_due(after_slot.subtract(days=1), after_slot, 3) is True


@pytest.mark.asyncio
async def test_backup_if_due_skips_or_runs_from_persisted_marker(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 4, tz="Europe/Moscow")
    monkeypatch.setattr(backup, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(
        backup, "settings", SimpleNamespace(yaml_config={"scheduler": {"backup_hour": 3}})
    )
    run = AsyncMock(return_value=__file__)
    monkeypatch.setattr(backup, "run_backup", run)

    async def fresh(*_):
        return SimpleNamespace(updated_at=now)

    monkeypatch.setattr(backup, "get_operational_state", fresh)
    assert await backup.run_backup_if_due(now) is None
    run.assert_not_awaited()

    async def stale(*_):
        return SimpleNamespace(updated_at=now.subtract(days=1))

    monkeypatch.setattr(backup, "get_operational_state", stale)
    assert await backup.run_backup_if_due(now) == __file__
    run.assert_awaited_once()


def test_backup_portability_filter_and_missing_binary(monkeypatch):
    assert backup._is_portable_dump_line(b"SET transaction_timeout = 0;\n") is False
    assert backup._is_portable_dump_line(b"SET client_encoding = 'UTF8';\n") is True
    monkeypatch.delenv("PG_DUMP_BIN", raising=False)
    monkeypatch.setattr(backup.shutil, "which", lambda _: None)
    monkeypatch.setattr(backup.Path, "exists", lambda _: False)
    assert backup._find_pg_dump() is None


def test_backup_artifact_status_rejects_path_traversal_and_validates_checksum(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    digest_value = "a" * 64
    assert observability.backup_artifact_status({"file": "../bad", "bytes": 1, "sha256": digest_value}) == (False, "invalid-marker")
    archive = tmp_path / "notebook_bot.sql.gz"
    archive.write_bytes(b"backup")
    archive.with_suffix(".gz.sha256").write_text(f"{digest_value}  {archive.name}\n", encoding="ascii")
    assert observability.backup_artifact_status({"file": archive.name, "bytes": 6, "sha256": digest_value}) == (True, "metadata-ok")


@pytest.mark.asyncio
async def test_observe_job_records_success_and_failure_metrics(monkeypatch):
    monkeypatch.setattr(observability, "metrics", observability.MetricsRegistry())
    async with observability.observe_job("unit"):
        pass
    with pytest.raises(ValueError):
        async with observability.observe_job("unit"):
            raise ValueError("boom")
    snapshot = observability.metrics.snapshot()
    assert snapshot["counters"] == {"scheduler.unit.success": 1, "scheduler.unit.error": 1}
    assert snapshot["observations"]["scheduler.unit.duration_seconds"]["count"] == 2


@pytest.mark.asyncio
async def test_digest_only_delivers_due_unsent_period_and_marks_it(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 9, 30, tz="Europe/Moscow")
    user = SimpleNamespace(telegram_id=7, digest_enabled=True, timezone="Europe/Moscow", digest_morning_time=now.time(), digest_evening_time=now.set(hour=20).time(), digest_sent_date=None, digest_evening_sent_date=None)
    marked = []
    monkeypatch.setattr(digest, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(digest, "get_all_users", AsyncMock(return_value=[user]))
    monkeypatch.setattr(digest.pendulum, "now", lambda _: now)
    monkeypatch.setattr(digest, "_send_morning", AsyncMock(return_value=SimpleNamespace(completed=True)))
    monkeypatch.setattr(digest, "_send_evening", AsyncMock())

    async def claim(_, user_id, marker, today):
        marked.append((user_id, marker, today))

    monkeypatch.setattr(digest, "claim_date_marker", claim)
    await digest.send_digests(object())
    assert marked == [(7, "digest_sent_date", now.date())]
    digest._send_evening.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_reminder_releases_claim_when_telegram_fails(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 11, 5, tz="Europe/Moscow")
    user = SimpleNamespace(telegram_id=7, timezone="Europe/Moscow", work_days=[1, 2, 3, 4, 5, 6, 7], tasks_reminder_last_date=None, tasks_reminder_last_hour=None)
    released = []
    monkeypatch.setattr(task_reminders, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(task_reminders, "get_all_users", AsyncMock(return_value=[user]))
    monkeypatch.setattr(task_reminders.pendulum, "now", lambda _: now)
    monkeypatch.setattr(task_reminders, "get_today_tasks", AsyncMock(return_value=[SimpleNamespace(title="x", is_frog=False, priority="high", due_time=None, due_date=now.date())]))
    monkeypatch.setattr(task_reminders, "get_completed_today", AsyncMock(return_value=[]))
    monkeypatch.setattr(task_reminders, "get_frog", AsyncMock(return_value=None))
    monkeypatch.setattr(task_reminders, "claim_task_reminder_slot", AsyncMock(return_value=True))
    monkeypatch.setattr(task_reminders, "split_html_message", lambda text: [text])

    async def release(_, *args):
        released.append(args)

    monkeypatch.setattr(task_reminders, "release_task_reminder_slot", release)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("offline")))
    await task_reminders.send_task_reminders(bot)
    assert released == [(7, now.date(), 11)]


@pytest.mark.asyncio
async def test_task_reminder_skips_non_working_hour_before_db_work(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 10, tz="Europe/Moscow")
    user = SimpleNamespace(telegram_id=7, timezone="Europe/Moscow", work_days=list(range(1, 8)))
    monkeypatch.setattr(task_reminders, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(task_reminders, "get_all_users", AsyncMock(return_value=[user]))
    monkeypatch.setattr(task_reminders.pendulum, "now", lambda _: now)
    tasks = AsyncMock()
    monkeypatch.setattr(task_reminders, "get_today_tasks", tasks)
    await task_reminders.send_task_reminders(object())
    tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_review_claims_then_releases_if_delivery_fails(monkeypatch):
    now = pendulum.datetime(2026, 8, 2, 21, tz="Europe/Moscow")  # Sunday
    user = SimpleNamespace(telegram_id=7, digest_enabled=True, timezone="Europe/Moscow", weekly_review_sent_date=None)
    released = []
    monkeypatch.setattr(weekly_review, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(weekly_review, "get_all_users", AsyncMock(return_value=[user]))
    monkeypatch.setattr(weekly_review.pendulum, "now", lambda _: now)
    monkeypatch.setattr(weekly_review, "claim_date_marker", AsyncMock(return_value=True))
    monkeypatch.setattr(weekly_review, "_send_review", AsyncMock(side_effect=RuntimeError("telegram")))

    async def release(_, *args):
        released.append(args)

    monkeypatch.setattr(weekly_review, "release_date_marker", release)
    await weekly_review.send_weekly_review(object())
    assert released == [(7, "weekly_review_sent_date", now.date())]


@pytest.mark.asyncio
async def test_sweep_marks_success_and_records_terminal_delivery_failure(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 12, tz="UTC")
    reminders = [
        SimpleNamespace(id=1, user_id=7, message="<safe>", remind_at=now.subtract(minutes=2)),
        SimpleNamespace(id=2, user_id=8, message="bad", remind_at=now),
    ]
    session = RecordingSession()
    monkeypatch.setattr(sweep, "async_session", lambda: FakeSessionContext(session))
    monkeypatch.setattr(sweep, "get_pending_reminders", AsyncMock(return_value=reminders))
    monkeypatch.setattr(sweep.pendulum, "now", lambda _: now)
    monkeypatch.setattr(sweep, "build_snooze_keyboard", lambda _: SimpleNamespace(as_markup=lambda: "kb"))
    monkeypatch.setattr(sweep, "mark_sent", AsyncMock())
    failures = []

    async def failure(_, *args, **kwargs):
        failures.append((args, kwargs))

    monkeypatch.setattr(sweep, "record_delivery_failure", failure)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[None, sweep.TelegramForbiddenError(Mock(), "blocked")]))
    await sweep.sweep_missed_reminders(bot)
    sweep.mark_sent.assert_awaited_once_with(session, 1)
    assert failures == [((2, "TelegramForbiddenError"), {"terminal": True})]
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_reminders_deliver_and_record_terminal_failure(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 12, tz="UTC")
    pending = [
        SimpleNamespace(id=1, user_id=7, message="<safe>", remind_at=now.subtract(minutes=3)),
        SimpleNamespace(id=2, user_id=8, message="blocked", remind_at=now),
    ]
    session = RecordingSession()
    metrics = observability.MetricsRegistry()
    failures = []
    monkeypatch.setattr(reminders, "async_session", lambda: FakeSessionContext(session))
    monkeypatch.setattr(reminders, "get_pending_reminders", AsyncMock(return_value=pending))
    monkeypatch.setattr(reminders.pendulum, "now", lambda _: now)
    monkeypatch.setattr(reminders, "build_snooze_keyboard", lambda _: SimpleNamespace(as_markup=lambda: "kb"))
    monkeypatch.setattr(reminders, "mark_sent", AsyncMock())
    monkeypatch.setattr(reminders, "metrics", metrics)

    async def failure(_, *args, **kwargs):
        failures.append((args, kwargs))

    monkeypatch.setattr(reminders, "record_delivery_failure", failure)
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=[None, reminders.TelegramForbiddenError(Mock(), "blocked")]
        )
    )

    await reminders.send_pending_reminders(bot)

    reminders.mark_sent.assert_awaited_once_with(session, 1)
    assert failures == [((2, "TelegramForbiddenError"), {"terminal": True})]
    assert metrics.snapshot()["counters"] == {
        "reminders.delivered": 1,
        "reminders.delivery_error": 1,
    }
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_reminders_empty_list_never_contacts_telegram(monkeypatch):
    monkeypatch.setattr(reminders, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(reminders, "get_pending_reminders", AsyncMock(return_value=[]))
    bot = SimpleNamespace(send_message=AsyncMock())

    await reminders.send_pending_reminders(bot)

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_reminder_sends_claimed_slot_with_tasks_and_completion(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 13, 5, tz="Europe/Moscow")
    user = SimpleNamespace(
        telegram_id=7,
        timezone="Europe/Moscow",
        work_days=list(range(1, 8)),
        tasks_reminder_last_date=None,
        tasks_reminder_last_hour=None,
    )
    task = SimpleNamespace(
        title="Ship <feature>",
        is_frog=False,
        priority="high",
        due_time=None,
        due_date=now.date(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(task_reminders, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(task_reminders, "get_all_users", AsyncMock(return_value=[user]))
    monkeypatch.setattr(task_reminders.pendulum, "now", lambda _: now)
    monkeypatch.setattr(task_reminders, "get_today_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(
        task_reminders, "get_completed_today", AsyncMock(return_value=[SimpleNamespace(title="Done")])
    )
    monkeypatch.setattr(task_reminders, "get_frog", AsyncMock(return_value=None))
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(task_reminders, "claim_task_reminder_slot", claim)
    monkeypatch.setattr(task_reminders, "split_html_message", lambda text: [text])

    await task_reminders.send_task_reminders(bot)

    claim.assert_awaited_once_with(ANY, 7, now.date(), 13)
    bot.send_message.assert_awaited_once()
    assert "Ship &lt;feature&gt;" in bot.send_message.await_args.kwargs["text"]


def test_task_reminder_formatter_handles_frog_overdue_and_long_completion_list():
    today = pendulum.date(2026, 8, 3)
    task = SimpleNamespace(
        title="Overdue",
        is_frog=False,
        priority="normal",
        due_time=pendulum.time(9, 30),
        due_date=today.subtract(days=1),
        scheduled_date=None,
    )
    completed = [SimpleNamespace(title=f"done {index}") for index in range(4)]
    frog = SimpleNamespace(title="<frog>")

    text = task_reminders._format_task_reminder([task], completed, frog, today, 17)

    assert "Финишная прямая" in text
    assert "... и ещё 1" in text
    assert "&lt;frog&gt;" in text
    assert "⏰ 09:30 ⚠️" in text


def test_main_tmux_guard_honors_explicit_recovery_override(monkeypatch):
    monkeypatch.setenv("TMUX", "socket")
    monkeypatch.delenv("DAILYPLANNER_ALLOW_TMUX", raising=False)
    assert main._tmux_runtime_disallowed() is True
    monkeypatch.setenv("DAILYPLANNER_ALLOW_TMUX", "1")
    assert main._tmux_runtime_disallowed() is False


@pytest.mark.asyncio
async def test_main_exits_before_startup_when_token_is_missing(monkeypatch):
    monkeypatch.setattr(main, "_tmux_runtime_disallowed", lambda: False)
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            bot_token="", access_control_configured=True, runtime_config_errors=lambda: []
        ),
    )
    exit_mock = Mock(side_effect=SystemExit(1))
    monkeypatch.setattr(main.sys, "exit", exit_mock)

    with pytest.raises(SystemExit):
        await main.main()

    exit_mock.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_main_releases_database_when_singleton_is_already_held(monkeypatch):
    engine = SimpleNamespace(dispose=AsyncMock())
    lease = SimpleNamespace(acquire=AsyncMock(return_value=False))
    monkeypatch.setattr(main, "_tmux_runtime_disallowed", lambda: False)
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            bot_token="token", access_control_configured=True, runtime_config_errors=lambda: []
        ),
    )
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SingletonLease", lambda _: lease)

    await main.main()

    lease.acquire.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_stops_polling_without_touching_bot_or_queue():
    dispatcher = SimpleNamespace(stop_polling=AsyncMock())
    await main._shutdown(dispatcher, object(), object())
    dispatcher.stop_polling.assert_awaited_once()


def test_conflict_handler_filters_noise_and_throttles_scheduling(monkeypatch):
    class Loop:
        def __init__(self):
            self.calls = []

        def call_soon_threadsafe(self, callback, coroutine):
            self.calls.append((callback, coroutine))
            coroutine.close()

    loop = Loop()
    registry = observability.MetricsRegistry()
    handler = observability.TelegramConflictAlertHandler(object(), loop)
    monkeypatch.setattr(observability, "metrics", registry)
    monotonic = Mock(side_effect=[100.0, 120.0, 161.0])
    monkeypatch.setattr(observability.time, "monotonic", monotonic)

    handler.emit(SimpleNamespace(getMessage=lambda: "unrelated dispatcher warning"))
    handler.emit(SimpleNamespace(getMessage=lambda: "TelegramConflictError"))
    handler.emit(SimpleNamespace(getMessage=lambda: "TelegramConflictError"))
    handler.emit(SimpleNamespace(getMessage=lambda: "TelegramConflictError"))

    assert registry.snapshot()["counters"] == {"telegram.polling_conflict": 3}
    assert len(loop.calls) == 2


@pytest.mark.asyncio
async def test_slo_alert_skips_healthy_and_persists_successful_delivery(monkeypatch):
    session = RecordingSession()
    sent = []
    monkeypatch.setattr(observability, "async_session", lambda: FakeSessionContext(session))
    monkeypatch.setattr(
        observability, "settings", SimpleNamespace(admin_telegram_ids=[1, 2])
    )
    monkeypatch.setattr(observability, "get_operational_state", AsyncMock(return_value=None))

    async def send_message(admin_id, message):
        sent.append((admin_id, message))

    set_marker = AsyncMock()
    monkeypatch.setattr(observability, "set_operational_state", set_marker)
    await observability.alert_slo_violations(
        SimpleNamespace(send_message=send_message),
        {"healthy": {"status": "ok"}, "backup": {"status": "error", "age_hours": 50}},
    )

    assert [admin_id for admin_id, _ in sent] == [1, 2]
    set_marker.assert_awaited_once_with(ANY, "slo.alert.backup", {"status": "error", "age_hours": 50})


@pytest.mark.asyncio
async def test_backup_returns_none_and_counts_error_for_invalid_database_config(monkeypatch, tmp_path):
    registry = observability.MetricsRegistry()
    monkeypatch.setattr(backup, "_BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup, "metrics", registry)
    monkeypatch.setattr(
        backup,
        "settings",
        SimpleNamespace(database_url="postgresql://localhost/", yaml_config={"scheduler": {}}),
    )

    assert await backup.run_backup() is None
    assert registry.snapshot()["counters"] == {"backup.error": 1}


def test_backup_rotation_removes_archive_and_companion_checksum(tmp_path, monkeypatch):
    archive = tmp_path / "notebook_bot_old.sql.gz"
    checksum = archive.with_suffix(".gz.sha256")
    archive.write_bytes(b"old")
    checksum.write_text("checksum", encoding="ascii")
    old_timestamp = 1
    archive.touch()
    import os

    os.utime(archive, (old_timestamp, old_timestamp))
    monkeypatch.setattr(backup, "_BACKUP_DIR", tmp_path)

    backup._rotate_backups(retention_days=1)

    assert not archive.exists()
    assert not checksum.exists()


@pytest.mark.asyncio
async def test_manual_weekly_review_claims_and_sends_once(monkeypatch):
    user = SimpleNamespace(telegram_id=7, timezone="Europe/Moscow")
    monkeypatch.setattr(weekly_review, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(weekly_review, "claim_date_marker", AsyncMock(return_value=True))
    send = AsyncMock()
    monkeypatch.setattr(weekly_review, "_send_review", send)

    assert await weekly_review.send_weekly_review_now(object(), user) is True
    send.assert_awaited_once_with(ANY, user, "Europe/Moscow")


def test_weekly_review_formatter_includes_all_populated_sections_and_escapes_titles():
    text = weekly_review._format_review(
        week_start=pendulum.date(2026, 8, 3),
        chrono_stats={"categories": {"work": 90, "focus": 30}, "entries_count": 2, "avg_productivity": 4},
        completed_tasks=[SimpleNamespace(title="Ship <feature>")],
        frogs_total=2,
        frogs_eaten=2,
        value_stats=[{"value": "семья", "count": 3}],
        project_progress={"<Elephant>": {"percent": 50, "done": 1, "total": 2}},
    )

    assert "Распределение времени" in text
    assert "Ship &lt;feature&gt;" in text
    assert "Все лягушки съедены" in text
    assert "Ценности недели" in text
    assert "&lt;Elephant&gt;" in text


@pytest.mark.asyncio
async def test_evaluate_slos_reports_lag_and_missing_backup_marker(monkeypatch):
    now = pendulum.datetime(2026, 8, 3, 12, tz="UTC")
    result = SimpleNamespace(one=lambda: (now.subtract(minutes=5), 2))
    session = RecordingSession([result])
    registry = observability.MetricsRegistry()
    monkeypatch.setattr(observability, "async_session", lambda: FakeSessionContext(session))
    monkeypatch.setattr(observability, "get_operational_state", AsyncMock(return_value=None))
    monkeypatch.setattr(observability, "metrics", registry)
    monkeypatch.setattr(observability.pendulum, "now", lambda _: now)
    monkeypatch.setattr(
        observability,
        "settings",
        SimpleNamespace(yaml_config={"slo": {"reminder_lag_seconds": 120, "backup_max_age_hours": 30}}),
    )

    evaluated = await observability.evaluate_slos()

    assert evaluated["reminders"] == {
        "status": "error",
        "lag_seconds": 300.0,
        "pending": 2,
        "target_seconds": 120,
    }
    assert evaluated["backup"]["status"] == "unknown"
    assert registry.snapshot()["gauges"]["reminders.pending"] == 2.0


@pytest.mark.asyncio
async def test_conflict_alert_delivers_and_persists_but_respects_recent_marker(monkeypatch):
    session = RecordingSession()
    marker = SimpleNamespace(updated_at=pendulum.now("UTC").subtract(hours=2))
    delivered = []
    monkeypatch.setattr(observability, "async_session", lambda: FakeSessionContext(session))
    monkeypatch.setattr(observability, "get_operational_state", AsyncMock(return_value=marker))
    monkeypatch.setattr(observability, "set_operational_state", AsyncMock())
    monkeypatch.setattr(observability, "settings", SimpleNamespace(admin_telegram_ids=[10, 11]))

    async def send_message(admin_id, _text):
        delivered.append(admin_id)

    await observability._alert_telegram_conflict(SimpleNamespace(send_message=send_message))

    assert delivered == [10, 11]
    observability.set_operational_state.assert_awaited_once()

    recent = SimpleNamespace(updated_at=pendulum.now("UTC"))
    monkeypatch.setattr(observability, "get_operational_state", AsyncMock(return_value=recent))
    delivered.clear()
    await observability._alert_telegram_conflict(SimpleNamespace(send_message=send_message))
    assert delivered == []


@pytest.mark.asyncio
async def test_weekly_review_collects_stats_formats_and_sends(monkeypatch):
    user = SimpleNamespace(telegram_id=7, timezone="Europe/Moscow")
    project = SimpleNamespace(id=1, title="Elephant")
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(weekly_review, "async_session", lambda: FakeSessionContext())
    monkeypatch.setattr(weekly_review, "get_week_stats", AsyncMock(return_value={"categories": {}, "entries_count": 0}))
    monkeypatch.setattr(weekly_review, "get_completed_in_range", AsyncMock(return_value=[SimpleNamespace(title="Done")]))
    monkeypatch.setattr(weekly_review, "get_frogs_in_range", AsyncMock(return_value=[SimpleNamespace(status="done")]))
    monkeypatch.setattr(weekly_review, "get_value_stats", AsyncMock(return_value=[]))
    monkeypatch.setattr(weekly_review, "get_user_projects", AsyncMock(return_value=[project]))
    monkeypatch.setattr(weekly_review, "get_project_progress", AsyncMock(return_value={"percent": 50, "done": 1, "total": 2}))

    await weekly_review._send_review(bot, user, "Europe/Moscow")

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 7
    assert "Выполнено задач:</b> 1" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_log_rotation_executes_all_retention_deletes_and_commits(monkeypatch):
    session = RecordingSession([SimpleNamespace(rowcount=value) for value in (2, 1, 3, 0, 4)])
    monkeypatch.setattr(log_rotation, "async_session", lambda: FakeSessionContext(session))
    monkeypatch.setattr(
        log_rotation,
        "settings",
        SimpleNamespace(
            yaml_config={
                "scheduler": {
                    "llm_log_retention_days": 7,
                    "transient_state_retention_days": 3,
                }
            }
        ),
    )
    await log_rotation.rotate_llm_logs()
    assert session.execute.await_count == 5
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthcheck_reports_degraded_stt_and_service_failures(monkeypatch):
    monkeypatch.setattr(healthcheck, "get_stt_client", lambda: SimpleNamespace(health_check=AsyncMock(return_value=True)))
    monkeypatch.setattr(healthcheck, "metrics", observability.MetricsRegistry())
    healthcheck.metrics.observe("stt.transcription_seconds", 31)
    monkeypatch.setattr(
        healthcheck,
        "settings",
        SimpleNamespace(yaml_config={"slo": {"stt_latency_seconds": 30}}),
    )
    assert (await healthcheck.check_stt_health())["status"] == "degraded"

    monkeypatch.setattr(healthcheck, "async_session", lambda: FakeSessionContext(RecordingSession()))
    monkeypatch.setattr(healthcheck, "get_embed_client", lambda: None)
    monkeypatch.setattr(healthcheck, "evaluate_slos", AsyncMock(side_effect=RuntimeError("db down")))
    broken_llm = SimpleNamespace(health_check=AsyncMock(side_effect=RuntimeError("offline")))
    result = await healthcheck.check_all_health(broken_llm)
    assert result["llm"]["status"] == "error"
    assert result["embedding"]["status"] == "not_configured"
    assert result["slo"]["status"] == "error"
