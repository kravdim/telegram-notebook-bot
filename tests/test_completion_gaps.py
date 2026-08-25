import zipfile
from types import SimpleNamespace

import pytest

from bot.config import validate_runtime_config
from bot.handlers.messages import (
    _extract_common_mutation,
    _normalize_common_intent_text,
    _preserve_user_marker_in_call,
)
from bot.llm.dispatcher import _select_confident_task
from bot.observability import MetricsRegistry
from bot.scheduler import healthcheck
from bot.services.export import ExportTooLargeError, write_export_archive


def _valid_config():
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
        "stt": {"provider": "local_whisper", "model": "medium"},
    }


def test_runtime_validation_accepts_complete_config():
    assert validate_runtime_config(_valid_config(), {"minimax": "key"}) == []


def test_runtime_validation_reports_all_unsafe_provider_settings():
    config = _valid_config()
    config["llm"]["fallback"] = {
        "provider": "unknown",
        "model": "",
        "timeout_sec": 0,
        "max_retries": -1,
    }
    config["embedding"]["dimensions"] = 1536
    config["stt"] = {"provider": "groq", "model": "", "timeout_sec": 0}
    config["scheduler"] = {"backup_hour": 24, "backup_retention_days": 0}
    errors = validate_runtime_config(config, {"minimax": "key"})
    assert "unsupported LLM fallback provider: unknown" in errors
    assert "embedding dimensions must match Vector(768)" in errors
    assert "API key is missing for STT provider: groq" in errors
    assert "scheduler backup_hour must be an integer from 0 to 23" in errors


def test_runtime_validation_handles_malformed_yaml_sections():
    errors = validate_runtime_config(
        {"llm": "bad", "embedding": [], "stt": None, "export": "large"}, {}
    )
    assert "llm config must be a mapping" in errors
    assert "embedding config must be a mapping" in errors
    assert "stt config must be a mapping" in errors
    assert "export config must be a mapping" in errors


def test_disk_export_contains_utf8_markdown(tmp_path):
    target = tmp_path / "export.zip"
    write_export_archive(
        target,
        [("notes.md", ["# Заметки\n", "Привет\n"])],
        max_bytes=1024,
    )
    with zipfile.ZipFile(target) as archive:
        assert archive.read("notes.md").decode() == "# Заметки\nПривет\n"


def test_disk_export_stops_at_raw_size_limit(tmp_path):
    with pytest.raises(ExportTooLargeError):
        write_export_archive(
            tmp_path / "export.zip",
            [("notes.md", ["я" * 20])],
            max_bytes=10,
        )


def test_metrics_latest_is_safe_for_missing_and_present_samples():
    registry = MetricsRegistry()
    assert registry.latest("stt.transcription_seconds") is None
    registry.observe("stt.transcription_seconds", 1.25)
    assert registry.latest("stt.transcription_seconds") == 1.25


@pytest.mark.asyncio
async def test_stt_health_exposes_last_latency_and_slo(monkeypatch):
    class STT:
        async def health_check(self):
            return True

    registry = MetricsRegistry()
    registry.observe("stt.transcription_seconds", 35.0)
    monkeypatch.setattr(healthcheck, "get_stt_client", lambda: STT())
    monkeypatch.setattr(healthcheck, "metrics", registry)
    monkeypatch.setattr("bot.config._yaml", {"slo": {"stt_latency_seconds": 30}})
    result = await healthcheck.check_stt_health()
    assert result["status"] == "degraded"
    assert result["last_transcription_ms"] == 35000


@pytest.mark.parametrize(
    ("text", "tool"),
    [
        ("напмни через 15 минут тест-вода", "create_reminder"),
        ("забей в задачи разобрать завалы в гараже", "create_task"),
        ("самое противное на сегодня — заполнить налоги, это лягушка", "create_task"),
        ("слушай, это прям слон: ремонт балкона, нарежь пожалуйста", "create_project"),
        ("create task marker-english tomorrow at 3pm call John", "create_task"),
    ],
)
def test_messy_mutations_have_deterministic_safe_path(text, tool):
    normalized = _normalize_common_intent_text(text)
    name, arguments = _extract_common_mutation(normalized, "Europe/Moscow")
    assert name == tool
    assert arguments
    if "лягушка" in text:
        assert arguments["is_frog"] is True


def test_live_run_marker_is_preserved_in_reminder_payload():
    marker = "DP-20260825T052726-5668b0-чай"
    call = _preserve_user_marker_in_call(
        f"напомни через 2 минуты {marker} попить",
        {"name": "create_reminder", "arguments": {"message": "чай попить"}},
    )
    assert marker in call["arguments"]["message"]


def test_opaque_task_marker_never_fuzzy_matches_another_artifact():
    existing = SimpleNamespace(
        title="Заполнить DP-20260825T091725-157b49-налоги"
    )
    assert (
        _select_confident_task(
            "DP-20260825T091725-157b49-неттакойвообще", [existing]
        )
        is None
    )
    assert (
        _select_confident_task("DP-20260825T091725-157b49-налоги", [existing])
        is existing
    )
