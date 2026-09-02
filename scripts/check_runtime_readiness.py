#!/usr/bin/env python3
"""Validate one deployed runtime heartbeat without contacting Telegram."""

from __future__ import annotations

import argparse
from pathlib import Path

from bot.runtime.readiness import validate_readiness_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=float, default=15)
    parser.add_argument("--expected-release")
    args = parser.parse_args()
    payload = validate_readiness_file(
        args.file,
        args.max_age_seconds,
        expected_release_sha=args.expected_release,
    )
    print(
        "runtime readiness ok: "
        f"pid={payload['pid']} release={payload.get('release_sha', 'legacy')}"
    )


if __name__ == "__main__":
    main()
