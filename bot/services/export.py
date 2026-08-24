"""Bounded, disk-backed user data export."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path


class ExportTooLargeError(ValueError):
    """The export exceeded the configured safe size."""


def write_export_archive(
    destination: Path,
    sections: Iterable[tuple[str, Iterable[str]]],
    *,
    max_bytes: int,
) -> Path:
    """Write UTF-8 Markdown entries without assembling them in memory."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    raw_bytes = 0
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, chunks in sections:
            with archive.open(filename, "w") as entry:
                for chunk in chunks:
                    encoded = chunk.encode("utf-8")
                    raw_bytes += len(encoded)
                    if raw_bytes > max_bytes:
                        raise ExportTooLargeError(
                            f"export content exceeds {max_bytes} bytes"
                        )
                    entry.write(encoded)

    if destination.stat().st_size > max_bytes:
        raise ExportTooLargeError(f"export archive exceeds {max_bytes} bytes")
    return destination
