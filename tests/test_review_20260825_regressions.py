from types import SimpleNamespace

import pendulum
import pytest
import yaml

import bot.config as config_module
import scripts.delete_user_data as deletion_script
from bot.config import Settings
from bot.db.models import Task
from bot.observability import MetricsRegistry, backup_artifact_status
from bot.scheduler.backup import is_backup_due
from bot.services.access_config import read_allowed_telegram_ids
from bot.services.tasks import _next_reminder_for_occurrence
from tests.fakes import FakeSessionContext


def _runtime_config_with_groq() -> dict:
    return {
        "llm": {
            "main": {
                "provider": "minimax",
                "model": "model",
                "timeout_sec": 10,
                "max_retries": 2,
            }
        },
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "base_url": "http://localhost:11434",
            "dimensions": 768,
        },
        "stt": {"provider": "groq", "model": "whisper-large-v3"},
    }


def test_task_metadata_preserves_unique_open_frog_index():
    indexes = {index.name: index for index in Task.__table__.indexes}
    assert indexes["uq_tasks_one_open_frog_per_user"].unique is True


def test_settings_runtime_validation_passes_groq_key(monkeypatch):
    monkeypatch.setattr(config_module, "_yaml", _runtime_config_with_groq())
    configured = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:pass@localhost/db",
        minimax_api_key="main-key",
        groq_api_key="groq-key",
    )
    assert "API key is missing for STT provider: groq" not in (
        configured.runtime_config_errors()
    )


def test_recurring_reminder_uses_due_offset_and_catches_up_immediately():
    anchor = pendulum.datetime(2026, 8, 24, 9, 0, tz="Europe/Moscow")
    reminder = anchor.subtract(minutes=15)
    next_at = pendulum.datetime(2026, 8, 25, 9, 0, tz="Europe/Moscow")
    now = pendulum.datetime(2026, 8, 25, 8, 50, tz="Europe/Moscow")

    next_reminder = _next_reminder_for_occurrence(anchor, reminder, next_at, now)

    assert next_reminder == now
    assert next_reminder <= next_at


def test_p95_uses_nearest_rank_for_small_samples():
    registry = MetricsRegistry()
    registry.observe("latency", 1.0)
    registry.observe("latency", 3.0)
    assert registry.snapshot()["observations"]["latency"]["p95"] == 3.0


def test_backup_artifact_requires_archive_size_and_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    archive = tmp_path / "notebook_bot_2026-08-25_030000.sql.gz"
    archive.write_bytes(b"backup")
    marker = {"file": archive.name, "bytes": len(b"backup")}

    assert backup_artifact_status(marker) == (False, "checksum-missing")
    archive.with_suffix(".gz.sha256").write_text(
        f"{'0' * 64}  {archive.name}\n", encoding="ascii"
    )
    assert backup_artifact_status(marker) == (True, "ok")
    assert backup_artifact_status({**marker, "bytes": 999}) == (
        False,
        "size-mismatch",
    )


def test_backup_slot_catches_up_after_missed_window_once_per_day():
    now = pendulum.datetime(2026, 8, 25, 10, tz="Europe/Moscow")
    assert is_backup_due(now.subtract(days=1), now, 3) is True
    assert is_backup_due(now.start_of("day").add(hours=4), now, 3) is False
    assert is_backup_due(None, now.start_of("day").add(hours=2), 3) is False


@pytest.mark.asyncio
async def test_privacy_deletion_restores_whitelist_when_database_fails(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"bot": {"allowed_telegram_ids": [42, 77]}}),
        encoding="utf-8",
    )
    rolled_back = []

    class Session:
        async def rollback(self):
            rolled_back.append(True)

        async def commit(self):
            raise AssertionError("commit must not run after deletion failure")

    class Lease:
        def __init__(self, engine):
            pass

        async def acquire(self):
            return True

        async def release(self):
            return None

    async def fail_delete(session, user_id):
        raise RuntimeError("database failed")

    monkeypatch.setattr(
        deletion_script, "async_session", lambda: FakeSessionContext(Session())
    )
    monkeypatch.setattr(deletion_script, "SingletonLease", Lease)
    monkeypatch.setattr(deletion_script, "delete_user_data", fail_delete)
    monkeypatch.setattr(deletion_script.settings, "admin_telegram_ids", [])
    monkeypatch.setattr(deletion_script.settings, "allow_all_users", False)
    monkeypatch.setattr(deletion_script.settings, "allowed_telegram_ids", [42, 77])
    args = SimpleNamespace(
        telegram_id=42,
        execute=True,
        confirm="DELETE-42",
        config=config_path,
    )

    with pytest.raises(RuntimeError, match="database failed"):
        await deletion_script.run(args)

    assert rolled_back == [True]
    assert read_allowed_telegram_ids(config_path) == [42, 77]
