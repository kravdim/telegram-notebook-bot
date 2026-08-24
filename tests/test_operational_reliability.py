import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.llm.contracts import Action
from bot.llm.dispatcher import parse_function_call
from bot.llm.queue import PRIORITY_INTENT, LLMQueue
from bot.observability import MetricsRegistry
from bot.runtime.singleton import SingletonLease
from bot.scheduler.backup import _is_portable_dump_line


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeConnection:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.closed = False
        self.executed = []

    async def execute(self, statement, params):
        self.executed.append((str(statement), params))
        if "pg_try_advisory_lock" in str(statement):
            return FakeResult(self.acquired)
        return FakeResult(True)

    async def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    async def connect(self):
        return self.connection


@pytest.mark.asyncio
async def test_singleton_rejects_second_runtime_and_closes_connection():
    connection = FakeConnection(acquired=False)
    lease = SingletonLease(FakeEngine(connection), "test-runtime")
    assert await lease.acquire() is False
    assert connection.closed is True
    assert lease.acquired is False


@pytest.mark.asyncio
async def test_singleton_release_unlocks_before_closing():
    connection = FakeConnection(acquired=True)
    lease = SingletonLease(FakeEngine(connection), "test-runtime")
    assert await lease.acquire() is True
    await lease.release()
    assert "pg_advisory_unlock" in connection.executed[-1][0]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_graceful_queue_shutdown_drains_accepted_work():
    completed = asyncio.Event()

    async def work():
        await asyncio.sleep(0)
        completed.set()
        return "done"

    queue = LLMQueue()
    queue.start()
    future = asyncio.create_task(queue.submit(PRIORITY_INTENT, work()))
    await asyncio.sleep(0)
    await queue.stop()
    assert completed.is_set()
    assert await future == "done"


def test_llm_golden_contract_fixtures():
    fixture = Path(__file__).parent / "fixtures" / "llm_intent_cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    for case in cases:
        name, arguments = parse_function_call(case["raw"])
        assert name == case["name"], case["description"]
        if "scheduled_date" in case:
            scheduled = arguments.get("scheduled_date") or arguments["updates"]["scheduled_date"]
            assert scheduled == case["scheduled_date"]
        if "repeat_rule" in case:
            assert arguments["repeat_rule"] == case["repeat_rule"]


def test_llm_utterance_contract_fixtures():
    fixture = Path(__file__).parent / "fixtures" / "llm_utterance_cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    for case in cases:
        name, arguments = parse_function_call(case["raw"])
        assert name == case["name"], case["utterance"]
        for key, value in case["expected_arguments"].items():
            assert arguments.get(key) == value, case["utterance"]


def test_unknown_tool_fails_closed():
    with pytest.raises(ValidationError):
        Action.model_validate({"name": "drop_database", "arguments": {}})


def test_metrics_snapshot_exposes_percentile_and_counters():
    registry = MetricsRegistry()
    registry.increment("jobs.ok")
    registry.gauge("queue.depth", 2)
    registry.observe("job.seconds", 1.0)
    registry.observe("job.seconds", 3.0)
    snapshot = registry.snapshot()
    assert snapshot["counters"]["jobs.ok"] == 1
    assert snapshot["gauges"]["queue.depth"] == 2
    assert snapshot["observations"]["job.seconds"]["max"] == 3.0


def test_pg17_transaction_timeout_is_removed_for_older_restore_targets():
    assert not _is_portable_dump_line(b"SET transaction_timeout = 0;\n")
    assert _is_portable_dump_line(b"SET statement_timeout = 0;\n")
