"""Content-free metadata helpers for application logging."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError


def error_type(error: BaseException) -> str:
    """Return a bounded exception class name without its potentially sensitive text."""
    return type(error).__name__[:80]


def payload_size(payload: Any) -> int:
    """Return serialized size without exposing payload values."""
    if isinstance(payload, bytes):
        return len(payload)
    if isinstance(payload, str):
        return len(payload.encode("utf-8", errors="replace"))
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def field_names(payload: Any) -> str:
    """Return only public field names, never values."""
    if not isinstance(payload, dict):
        return ""
    return ",".join(sorted(str(key)[:80] for key in payload)[:30])


def validation_codes(error: ValidationError) -> str:
    """Return bounded Pydantic error types and field paths without input values."""
    codes: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part)[:40] for part in item.get("loc", ()))
        code = str(item.get("type", "validation_error"))[:80]
        codes.append(f"{location}:{code}" if location else code)
    return ",".join(codes[:20])


def safe_error_code(value: str | None) -> str | None:
    """Keep only short machine-style codes; discard human/provider messages."""
    if value is None:
        return None
    if value in {
        "provider_error",
        "timeout",
        "tool_error",
        "unavailable",
        "validation_error",
    }:
        return value
    return "redacted_error"
