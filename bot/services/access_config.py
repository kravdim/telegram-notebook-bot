"""Atomic updates of the YAML access-control configuration."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import yaml


def read_allowed_telegram_ids(config_path: Path) -> list[int]:
    """Read the persisted YAML whitelist used by operator mutations."""
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config = yaml.safe_load(content) or {}
    values = config.get("bot", {}).get("allowed_telegram_ids", [])
    if not isinstance(values, list) or any(not isinstance(value, int) for value in values):
        raise ValueError("bot.allowed_telegram_ids must be a list of integers")
    return values


def write_allowed_telegram_ids(config_path: Path, user_ids: list[int]) -> None:
    """Persist the whitelist atomically while preserving unrelated settings."""
    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    config = yaml.safe_load(content) or {}
    config.setdefault("bot", {})["allowed_telegram_ids"] = list(dict.fromkeys(user_ids))
    rendered = yaml.safe_dump(config, default_flow_style=False, allow_unicode=True)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = stat.S_IMODE(config_path.stat().st_mode) if config_path.exists() else 0o600
    fd, temporary_name = tempfile.mkstemp(prefix=f".{config_path.name}.", dir=config_path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, old_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(rendered)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, config_path)
        directory_fd = os.open(config_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def remove_allowed_telegram_id(config_path: Path, user_id: int) -> bool:
    """Remove one ID, returning whether the persisted list changed."""
    current = read_allowed_telegram_ids(config_path)
    updated = [value for value in current if value != user_id]
    if updated == current:
        return False
    write_allowed_telegram_ids(config_path, updated)
    return True
