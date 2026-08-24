"""Tests for privacy-deletion and log-maintenance operator workflows."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
import yaml

from bot.formatters.stats import format_frog_stats, format_productivity_stats
from bot.handlers.commands import _current_frog_streak
from bot.services.access_config import (
    read_allowed_telegram_ids,
    remove_allowed_telegram_id,
    write_allowed_telegram_ids,
)
from bot.services.user_deletion import confirmation_phrase
from scripts.rotate_log_files import rotate_file


def test_atomic_whitelist_update_preserves_config_and_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "bot:\n  allowed_telegram_ids: [1, 2]\nllm:\n  main:\n    model: test\n",
        encoding="utf-8",
    )
    config_path.chmod(0o640)

    assert remove_allowed_telegram_id(config_path, 1) is True
    assert remove_allowed_telegram_id(config_path, 9) is False
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["bot"]["allowed_telegram_ids"] == [2]
    assert config["llm"]["main"]["model"] == "test"
    assert config_path.stat().st_mode & 0o777 == 0o640


def test_whitelist_update_deduplicates_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_allowed_telegram_ids(config_path, [4, 4, 5])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["bot"]["allowed_telegram_ids"] == [4, 5]
    assert read_allowed_telegram_ids(config_path) == [4, 5]


def test_whitelist_reader_rejects_non_integer_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("bot:\n  allowed_telegram_ids: nope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="list of integers"):
        read_allowed_telegram_ids(config_path)


def test_deletion_confirmation_is_bound_to_target() -> None:
    assert confirmation_phrase(123) == "DELETE-123"
    assert confirmation_phrase(123) != confirmation_phrase(124)


def test_frog_stats_zero_bar_localized_trend_and_real_streak() -> None:
    assert "[░░░░░░░░░░░░░░░░░░░░]" in format_frog_stats(0, 3, 0)
    assert "растёт" in format_productivity_stats(4.0, 3.0, "up")
    dates = {date(2026, 8, 21), date(2026, 8, 22), date(2026, 8, 23)}
    assert _current_frog_streak(dates, date(2026, 8, 24)) == 3


def test_log_rotation_copy_truncates_and_keeps_history(tmp_path: Path) -> None:
    active = tmp_path / "stdout.log"
    active.write_bytes(b"new-content")
    active.with_name("stdout.log.1").write_bytes(b"older")
    inode = active.stat().st_ino

    assert rotate_file(active, max_bytes=4, keep=2) == "rotated"
    assert active.read_bytes() == b""
    assert active.stat().st_ino == inode
    assert active.with_name("stdout.log.1").read_bytes() == b"new-content"
    assert active.with_name("stdout.log.2").read_bytes() == b"older"


def test_log_rotation_noop_and_rejects_symlink(tmp_path: Path) -> None:
    active = tmp_path / "stderr.log"
    active.write_bytes(b"ok")
    assert rotate_file(active, max_bytes=2, keep=2) == "within-limit"
    assert rotate_file(tmp_path / "missing.log", max_bytes=2, keep=2) == "missing"

    link = tmp_path / "linked.log"
    os.symlink(active, link)
    with pytest.raises(ValueError, match="non-regular"):
        rotate_file(link, max_bytes=1, keep=2)
