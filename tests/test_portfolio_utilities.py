"""Hermetic unit coverage for formatting, provider adapters, and operator scripts."""

import asyncio
import importlib
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.embeddings import indexer
from bot.embeddings.cloud import CloudEmbeddingClient
from bot.embeddings.ollama import OllamaEmbeddingClient
from bot.formatters import split_html_message, split_message
from bot.formatters.chronometry import format_day_photo, format_day_timeline, format_week_summary
from bot.formatters.digest import format_evening_digest, format_morning_digest
from bot.formatters.evening_review import format_evening_review
from bot.formatters.memoir import format_memoir_entries, format_value_stats, format_weekly_review
from bot.formatters.stats import format_frog_stats, format_productivity_stats
from bot.formatters.telegram import escape_dynamic
from bot.stt.cloud_stt import CloudSTTClient
from bot.stt.local_whisper import LocalWhisperClient


class _EmbedClient:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    async def embed(self, text):
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_embedding_indexer_handles_unconfigured_and_failed_client():
    indexer._client = None
    assert await indexer.get_embedding("text") is None
    indexer.init(_EmbedClient(result=[0.1]))
    assert indexer.get_client() is not None
    assert await indexer.get_embedding("text") == [0.1]
    indexer.init(_EmbedClient(error=RuntimeError("offline")))
    assert await indexer.get_embedding("text") is None
    indexer._client = None


@pytest.mark.asyncio
async def test_cloud_embedding_validates_response_and_health(monkeypatch):
    module = importlib.import_module("bot.embeddings.cloud")
    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(
            yaml_config={"embedding": {"dimensions": 2}},
            embedding_base_url="",
            embedding_api_key="key",
        ),
    )
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.embeddings = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 2.0])])

    monkeypatch.setattr(module, "AsyncOpenAI", FakeOpenAI)
    client = CloudEmbeddingClient()
    assert await client.embed("hello") == [1.0, 2.0]
    assert captured["request"]["dimensions"] == 2
    assert await client.health_check() is True
    client.client.embeddings.create = lambda **_: asyncio.sleep(
        0, result=SimpleNamespace(data=[SimpleNamespace(embedding=[1.0])])
    )
    with pytest.raises(ValueError, match="expected 2, got 1"):
        await client.embed("bad")
    assert await client.health_check() is False


@pytest.mark.asyncio
async def test_ollama_embedding_handles_missing_vector_and_unavailable_health(monkeypatch):
    module = importlib.import_module("bot.embeddings.ollama")
    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(
            yaml_config={"embedding": {"base_url": "http://unit", "model": "m", "dimensions": 2}}
        ),
    )
    responses = [
        SimpleNamespace(json=lambda: {"embedding": [3.0, 4.0]}, raise_for_status=lambda: None),
        SimpleNamespace(json=lambda: {}, raise_for_status=lambda: None),
    ]

    class FakeHTTP:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            return responses.pop(0)

        async def get(self, url):
            raise RuntimeError("down")

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeHTTP)
    client = OllamaEmbeddingClient()
    assert await client.embed("one") == [3.0, 4.0]
    with pytest.raises(ValueError, match="без embedding"):
        await client.embed("two")
    assert await client.health_check() is False


@pytest.mark.asyncio
async def test_cloud_stt_reads_audio_and_reports_health_error(monkeypatch, tmp_path):
    module = importlib.import_module("bot.stt.cloud_stt")
    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(
            yaml_config={"stt": {"provider": "other", "model": "unit", "language": "en"}},
            groq_api_key="groq",
            openai_api_key="openai",
        ),
    )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.create))
            self.models = SimpleNamespace(list=self.list)

        async def create(self, **kwargs):
            assert kwargs["file"] == ("voice.ogg", b"sound", "application/octet-stream")
            return SimpleNamespace(text="words")

        async def list(self, **kwargs):
            raise RuntimeError("down")

    monkeypatch.setattr(module, "AsyncOpenAI", FakeOpenAI)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"sound")
    client = CloudSTTClient()
    assert await client.transcribe(audio) == "words"
    assert await client.health_check() is False


@pytest.mark.asyncio
async def test_local_whisper_sync_health_and_close_are_hermetic(monkeypatch):
    module = importlib.import_module("bot.stt.local_whisper")
    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(yaml_config={"stt": {"model": "tiny", "language": "en"}}),
    )
    client = LocalWhisperClient()
    unloaded = []
    model = SimpleNamespace(
        model=SimpleNamespace(unload_model=lambda: unloaded.append(True)),
        transcribe=lambda *args, **kwargs: (
            [SimpleNamespace(text=" hi "), SimpleNamespace(text="there")],
            SimpleNamespace(duration=1.2),
        ),
    )
    client._model = model
    assert client._transcribe_sync("audio.wav") == "hi there"
    assert await client.health_check() is True
    await client.close()
    assert unloaded == [True] and client._model is None


def test_formatters_escape_sort_limit_and_zero_paths():
    assert split_message("abc", 2) == ["ab", "c"]
    chunks = split_html_message("<b>hello world</b>", 10)
    assert all(len(part) <= 10 for part in chunks)
    assert "".join(part.replace("<b>", "").replace("</b>", "") for part in chunks) == "hello world"
    assert escape_dynamic("<x>&") == "&lt;x&gt;&amp;"
    assert "нет" in format_day_photo({}) and "нет" in format_week_summary({})
    entries = [
        SimpleNamespace(
            timestamp=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
            category="work",
            activity_text="<plan>",
        )
    ]
    assert "&lt;plan&gt;" in format_day_timeline(entries)
    tasks = [
        SimpleNamespace(
            title="<urgent>",
            priority="high",
            category="work",
            due_time=None,
            due_date=date(2026, 1, 1),
            scheduled_date=None,
            status="open",
        )
    ]
    morning = format_morning_digest(date(2026, 1, 2), tasks, None, [], {}, False)
    assert "&lt;urgent&gt;" in morning and "Просроченных: 1" in morning
    assert "не съедена" in format_evening_digest(date.today(), [], [], False, "frog")
    assert "10." in format_evening_review([SimpleNamespace(title=str(i)) for i in range(12)])
    assert "100%" in format_frog_stats(3, 3, 2)
    assert "снижается" in format_productivity_stats(3, 2, "down")


def test_memoir_formatters_escape_multiline_and_order_values():
    entries = [
        SimpleNamespace(event_date=date(2026, 1, 2), value_tag="семья", content="<one>\ntwo"),
        SimpleNamespace(event_date=date(2026, 1, 1), value_tag=None, content="first"),
    ]
    assert "&lt;one&gt;" in format_memoir_entries(entries)
    weekly = format_weekly_review(entries)
    assert weekly.index("01.01") < weekly.index("02.01")
    assert "50%" in format_value_stats(
        [{"value": "<x>", "count": 1}, {"value": "работа", "count": 1}]
    )


def test_evaluate_llm_contract_schema_and_invalid_cases():
    script = importlib.import_module("scripts.evaluate_llm_contracts")
    functions = [
        {
            "name": "todo",
            "parameters": {
                "required": ["title"],
                "properties": {"title": {"type": "string"}, "priority": {"enum": ["high"]}},
            },
        }
    ]
    assert script.arguments_match_schema("todo", {"title": "x", "priority": "high"}, functions)
    assert not script.arguments_match_schema("todo", {"title": 3}, functions)
    correct, invalid = script.evaluate_cases(
        [
            {"raw": "ok", "name": "todo", "expected_arguments": {"title": "x"}},
            {"raw": "bad", "name": "todo"},
        ],
        lambda raw: (
            ("todo", {"title": "x"}) if raw == "ok" else (_ for _ in ()).throw(ValueError())
        ),
        functions,
    )
    assert (correct, invalid) == (1, 1)


def test_prefetch_secret_scan_and_postgres_drill_guards(monkeypatch, tmp_path, capsys):
    prefetch = importlib.import_module("scripts.prefetch_stt_model")
    monkeypatch.setattr(
        prefetch, "settings", SimpleNamespace(yaml_config={"stt": {"provider": "cloud"}})
    )
    prefetch.main()
    assert "prefetch skipped" in capsys.readouterr().out
    scanner = importlib.import_module("scripts.secret_scan")
    clean = tmp_path / "clean.txt"
    clean.write_text("safe")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scanner.subprocess, "check_output", lambda *args, **kwargs: "clean.txt\n")
    scanner.main()
    assert "1 present tracked" in capsys.readouterr().out
    drill = importlib.import_module("scripts.run_postgres_drill")
    monkeypatch.delenv("OPERATOR_DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="OPERATOR_DATABASE_URL is required"):
        drill.main()
    monkeypatch.setattr(drill.shutil, "which", lambda _: None)
    monkeypatch.setattr(drill.Path, "glob", lambda *args: [])
    with pytest.raises(SystemExit, match="client tool is missing"):
        drill._pg_tool("createdb")


@pytest.mark.asyncio
async def test_container_checks_fail_before_database_and_verify_e2e_validation(monkeypatch):
    health = importlib.import_module("scripts.container_healthcheck")
    preflight = importlib.import_module("scripts.preflight")
    smoke = importlib.import_module("scripts.container_smoke")
    from bot.config import settings

    monkeypatch.setattr(type(settings), "runtime_config_errors", lambda self: ["missing key"])
    for script in (health, preflight, smoke):
        with pytest.raises(SystemExit, match="Invalid runtime configuration: missing key"):
            await script.main()
    e2e = importlib.import_module("scripts.verify_e2e_state")
    with pytest.raises(ValueError, match="invalid E2E identity"):
        await e2e.run(0, "wrong", "cleanup")
    monkeypatch.setattr(
        e2e, "settings", SimpleNamespace(yaml_config={"testing": {"e2e_user_ids": [7]}})
    )
    monkeypatch.setattr(
        e2e,
        "verify_cleanup",
        lambda user_id: asyncio.sleep(0, result={"ok": True, "phase": "cleanup"}),
    )
    assert await e2e.run(7, "DP-20260101T010101-abcdef", "cleanup") == {
        "ok": True,
        "phase": "cleanup",
    }


@pytest.mark.asyncio
async def test_seed_skips_populated_database_without_embedding_or_writes(monkeypatch, capsys):
    seed = importlib.import_module("scripts.seed_knowledge")
    events = []

    class Session:
        async def scalar(self, query):
            return 2

    class Context:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            events.append("closed")

    monkeypatch.setattr(seed, "async_session", lambda: Context())
    await seed.seed(force=False)
    assert "уже содержит 2" in capsys.readouterr().out and events == ["closed"]


def test_chronometry_and_digest_cover_populated_weekend_and_birthday_branches():
    photo = format_day_photo(
        {
            "categories": {"work": 90, "focus": 30, "waste": 30},
            "total_minutes": 150,
            "avg_productivity": 4.5,
            "entries_count": 3,
        }
    )
    week = format_week_summary(
        {"categories": {"rest": 60, "unknown": 30}, "avg_productivity": 3, "entries_count": 2}
    )
    assert "1ч 30м (60%)" in photo and "Средняя продуктивность: 4.5/5" in photo
    assert "Отдых:" in week and "Не разобрано:" in week
    personal = SimpleNamespace(
        title="личное <дело>",
        priority="normal",
        category="personal",
        due_time=None,
        due_date=date(2026, 1, 3),
        scheduled_date=None,
        status="open",
    )
    work = SimpleNamespace(**{**personal.__dict__, "title": "work", "category": "work"})
    birthday = SimpleNamespace(
        name="<Аня>", note="<позвонить>", birth_date=date(2000, 1, 3), year_known=True
    )
    message = format_morning_digest(
        date(2026, 1, 3),
        [personal, work],
        work,
        [SimpleNamespace(id="p", title="project")],
        {"p": {"percent": 25}},
        True,
        active_trip="<trip>",
        birthdays=[birthday],
    )
    assert "&lt;trip&gt;" in message and "&lt;Аня&gt; (26 лет)" in message
    assert "личное &lt;дело&gt;" in message and "work" in message
    assert "Лягушка дня" not in message and "Слоны:" not in message


@pytest.mark.asyncio
async def test_preflight_accepts_only_explicit_compatible_newer_head(monkeypatch, capsys):
    preflight = importlib.import_module("scripts.preflight")
    import bot.db.engine as db_engine
    from bot.config import settings

    class Result:
        def scalar_one_or_none(self):
            return "new-head"

    class Connection:
        async def execute(self, statement):
            return Result()

    class Connect:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *args):
            return None

    fake_engine = SimpleNamespace(connect=lambda: Connect(), dispose=AsyncMock())
    monkeypatch.setattr(db_engine, "engine", fake_engine)
    monkeypatch.setattr(type(settings), "runtime_config_errors", lambda self: [])
    monkeypatch.setattr(preflight, "Config", lambda *_: object())
    monkeypatch.setattr(
        preflight.ScriptDirectory,
        "from_config",
        lambda *_: SimpleNamespace(
            get_current_head=lambda: "old-head",
            walk_revisions=lambda: (),
        ),
    )

    with pytest.raises(SystemExit, match="Migration mismatch"):
        await preflight.main()
    await preflight.main(compatible_database_head="new-head")

    assert "state=newer-compatible" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_container_scripts_success_paths_use_only_fake_database(monkeypatch, capsys):
    health = importlib.import_module("scripts.container_healthcheck")
    preflight = importlib.import_module("scripts.preflight")
    smoke = importlib.import_module("scripts.container_smoke")
    import bot.db.engine as db_engine
    import bot.runtime.readiness as readiness_module
    import bot.runtime.singleton as singleton_module
    from bot.config import settings

    class Result:
        def scalar_one_or_none(self):
            return "head"

    class Connection:
        async def execute(self, statement):
            return Result()

    class Connect:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *args):
            return None

    disposed = []

    async def dispose():
        disposed.append(True)

    fake_engine = SimpleNamespace(connect=lambda: Connect(), dispose=dispose)
    monkeypatch.setattr(db_engine, "engine", fake_engine)
    monkeypatch.setattr(type(settings), "runtime_config_errors", lambda self: [])
    monkeypatch.setattr(health, "Config", lambda *_: object())
    monkeypatch.setattr(
        health.ScriptDirectory,
        "from_config",
        lambda *_: SimpleNamespace(get_current_head=lambda: "head"),
    )
    monkeypatch.setattr(preflight, "Config", lambda *_: object())
    monkeypatch.setattr(
        preflight.ScriptDirectory,
        "from_config",
        lambda *_: SimpleNamespace(get_current_head=lambda: "head"),
    )
    monkeypatch.setattr(readiness_module, "validate_readiness_file", lambda *_: {"pid": 42})
    await health.main()
    await preflight.main()
    assert "container ready: pid=42, migration=head" in capsys.readouterr().out
    assert disposed == [True, True]

    lifecycle = []

    class Lease:
        def __init__(self, engine):
            lifecycle.append("created")

        async def acquire(self):
            lifecycle.append("acquired")
            return True

        async def release(self):
            lifecycle.append("released")

    class Readiness:
        def __init__(self, *args, **kwargs):
            lifecycle.append("readiness")

        async def start(self):
            lifecycle.append("started")

        async def stop(self):
            lifecycle.append("stopped")

    monkeypatch.setattr(singleton_module, "SingletonLease", Lease)
    monkeypatch.setattr(readiness_module, "RuntimeReadiness", Readiness)
    monkeypatch.setattr(
        smoke, "exercise_schema", lambda: asyncio.sleep(0, result=lifecycle.append("schema"))
    )
    await smoke.main(once=True)
    assert lifecycle == ["created", "acquired", "readiness", "schema", "stopped", "released"]
    assert "container smoke ok" in capsys.readouterr().out


def test_script_mains_evaluate_and_verify_e2e_emit_success_and_failure(monkeypatch, capsys):
    evaluator = importlib.import_module("scripts.evaluate_llm_contracts")
    monkeypatch.setattr(
        evaluator, "evaluate_cases", lambda cases, parser, functions: (len(cases), 0)
    )
    evaluator.main()
    assert "invalid_saved_response_rate=0.000" in capsys.readouterr().out
    monkeypatch.setattr(evaluator, "evaluate_cases", lambda *args: (0, 1))
    with pytest.raises(SystemExit, match="LLM contract regression"):
        evaluator.main()

    e2e = importlib.import_module("scripts.verify_e2e_state")
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify", "--user-id", "7", "--run-id", "DP-20260101T010101-abcdef", "--phase", "cleanup"],
    )

    def successful_run(coroutine):
        coroutine.close()
        return {"phase": "cleanup", "ok": True}

    monkeypatch.setattr(e2e.asyncio, "run", successful_run)
    e2e.main()
    assert '"ok": true' in capsys.readouterr().out

    def failed_run(coroutine):
        coroutine.close()
        return {"phase": "cleanup", "ok": False}

    monkeypatch.setattr(e2e.asyncio, "run", failed_run)
    with pytest.raises(SystemExit) as error:
        e2e.main()
    assert error.value.code == 1


def test_postgres_and_restore_helpers_are_local_and_validate_inputs(monkeypatch, tmp_path):
    drill = importlib.import_module("scripts.run_postgres_drill")
    monkeypatch.setenv("OPERATOR_DATABASE_URL", "sqlite:///not-postgres")
    with pytest.raises(SystemExit, match="PostgreSQL operator URL"):
        drill.main()
    calls = []
    monkeypatch.setattr(
        drill.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append((command, kwargs)) or SimpleNamespace(stdout=" done\n")
        ),
    )
    assert drill._run(["unit"], {"X": "1"}, capture=True) == "done"
    assert calls[0][1]["check"] is True and calls[0][1]["stdout"] is not None

    restore = importlib.import_module("scripts.restore_drill")
    backup = tmp_path / "notebook_bot_unit.sql.gz"
    backup.write_bytes(b"backup")
    checksum = backup.with_suffix(".gz.sha256")
    checksum.write_text("wrong checksum", encoding="ascii")
    with pytest.raises(ValueError, match="checksum mismatch"):
        restore._verify_checksum(backup)
    checksum.unlink()
    with pytest.raises(ValueError, match="checksum sidecar is missing"):
        restore._verify_checksum(backup)


@pytest.mark.asyncio
async def test_container_smoke_exercises_schema_and_removes_its_fake_records(monkeypatch):
    smoke = importlib.import_module("scripts.container_smoke")
    import bot.db.engine as db_engine

    calls = []

    class Extensions:
        def scalars(self):
            return ["vector", "pg_trgm", "pgcrypto"]

    class Session:
        def add(self, item):
            calls.append(("add", type(item).__name__))

        async def flush(self):
            calls.append(("flush",))

        async def commit(self):
            calls.append(("commit",))

        async def execute(self, statement, params=None):
            calls.append(("execute", str(statement), params))
            if "pg_extension" in str(statement):
                return Extensions()
            return SimpleNamespace()

        async def scalar(self, statement, params=None):
            calls.append(("scalar", str(statement), params))
            if "vector_dims" in str(statement):
                return 768
            return "smoke task"

    class Context:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            calls.append(("closed",))

    monkeypatch.setattr(db_engine, "async_session", lambda: Context())
    await smoke.exercise_schema()
    statements = "\n".join(call[1] for call in calls if call[0] == "execute")
    assert "INSERT INTO knowledge_base" in statements
    assert "DELETE FROM knowledge_base" in statements
    assert calls.count(("commit",)) == 2 and calls[-1] == ("closed",)


@pytest.mark.asyncio
async def test_verify_e2e_acceptance_and_cleanup_report_all_check_semantics(monkeypatch):
    e2e = importlib.import_module("scripts.verify_e2e_state")
    fragments = {
        "пет": 1,
        "справк": 1,
        "созвон": 1,
        "DP-20260101T010101-abcdef-wifi": 1,
        "DP-20260101T010101-abcdef-кран": 1,
        "DP-20260101T010101-abcdef-чай": 2,
        "DP-20260101T010101-abcdef-дневник": 1,
        "DP-20260101T010101-abcdef-заметка": 1,
        "пап": 1,
        "DP-20260101T010101-abcdef-вчерашний": 0,
        "DP-20260101T010101-abcdef-ноль": 0,
        "барсик": 0,
    }
    seen = []

    async def marker_count(session, model, user_id, columns, fragment):
        seen.append((model.__name__, user_id, fragment, len(columns)))
        return fragments[fragment]

    class Context:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(e2e, "async_session", lambda: Context())
    monkeypatch.setattr(e2e, "_marker_count", marker_count)
    accepted = await e2e.verify_acceptance(7, "DP-20260101T010101-abcdef")
    assert accepted["ok"] is True and len(accepted["checks"]) == 12
    assert {item[0] for item in seen} == {"Task", "Reminder", "Note", "DiaryEntry", "Birthday"}

    async def counts(session, user_id):
        return {"users": 1, "tasks": 2, "notes": 0}

    monkeypatch.setattr(e2e, "user_data_counts", counts)
    cleanup = await e2e.verify_cleanup(7)
    assert cleanup == {
        "phase": "cleanup",
        "ok": False,
        "residual": {"tasks": 2},
        "registration_rows": 1,
    }
