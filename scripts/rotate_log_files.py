#!/usr/bin/env python3
"""Bound launchd log growth using safe copy-and-truncate rotation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

DEFAULT_LOG_NAMES = (
    "stdout.log",
    "stderr.log",
    "recovery-drill.stdout.log",
    "recovery-drill.stderr.log",
)


def rotate_file(path: Path, max_bytes: int, keep: int) -> str:
    """Rotate one exact regular file without replacing its active inode."""
    if not path.exists():
        return "missing"
    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"refusing non-regular log file: {path}")
    if file_stat.st_size <= max_bytes:
        return "within-limit"

    oldest = path.with_name(f"{path.name}.{keep}")
    oldest.unlink(missing_ok=True)
    for number in range(keep - 1, 0, -1):
        archived_source = path.with_name(f"{path.name}.{number}")
        if archived_source.exists():
            os.replace(archived_source, path.with_name(f"{path.name}.{number + 1}"))

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as target, path.open("rb") as source:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary_path, stat.S_IMODE(file_stat.st_mode))
        os.replace(temporary_path, path.with_name(f"{path.name}.1"))
        with path.open("r+b") as active:
            active.truncate(0)
            active.flush()
            os.fsync(active.fileno())
        return "rotated"
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=Path.home() / "Library/Logs/notebook-bot")
    parser.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--keep", type=int, default=7)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_bytes < 1 or args.keep < 1:
        raise ValueError("--max-bytes and --keep must be positive")
    results = {
        name: rotate_file(args.log_dir / name, args.max_bytes, args.keep)
        for name in DEFAULT_LOG_NAMES
    }
    print(json.dumps({"log_dir": str(args.log_dir), "results": results}, sort_keys=True))


if __name__ == "__main__":
    main()
