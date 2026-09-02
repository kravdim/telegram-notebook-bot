#!/usr/bin/env python3
"""Render the macOS LaunchAgent without embedding site credentials or defaults."""

from __future__ import annotations

import argparse
import plistlib
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_PROXY_SCHEMES = {
    "http": {"http", "https"},
    "all": {"socks5", "socks5h"},
}


def validate_proxy_url(value: str, kind: str) -> str:
    """Validate a credential-free proxy URL before it reaches launchd."""
    parsed = urlsplit(value)
    if parsed.scheme not in _PROXY_SCHEMES[kind]:
        raise ValueError(f"unsupported {kind} proxy scheme: {parsed.scheme or 'missing'}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError(f"{kind} proxy must include host and port")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("proxy credentials/query/fragment must not be stored in LaunchAgent")
    return value


def _replace(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        return value
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, replacements) for key, item in value.items()}
    return value


def render_launchagent(
    template: Path,
    output: Path,
    project: Path,
    home: Path,
    *,
    http_proxy: str | None = None,
    all_proxy: str | None = None,
    readiness_file: Path | None = None,
    release_sha: str | None = None,
) -> None:
    """Render one valid plist atomically; direct networking is the default."""
    with template.open("rb") as source:
        payload = plistlib.load(source)
    payload = _replace(
        payload,
        {"__PROJECT_PATH__": str(project.resolve()), "__HOME__": str(home.resolve())},
    )
    environment = payload.setdefault("EnvironmentVariables", {})
    if http_proxy:
        environment["HTTP_PROXY"] = validate_proxy_url(http_proxy, "http")
        environment["HTTPS_PROXY"] = http_proxy
    if all_proxy:
        environment["ALL_PROXY"] = validate_proxy_url(all_proxy, "all")
    if readiness_file:
        environment["READINESS_FILE"] = str(readiness_file.resolve())
    if release_sha:
        environment["DAILYPLANNER_RELEASE_SHA"] = release_sha

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=output.parent, delete=False) as temporary:
        plistlib.dump(payload, temporary, sort_keys=False)
        temporary_path = Path(temporary.name)
    temporary_path.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--http-proxy")
    parser.add_argument("--all-proxy")
    parser.add_argument("--readiness-file", type=Path)
    parser.add_argument("--release-sha")
    args = parser.parse_args()
    render_launchagent(
        args.template,
        args.output,
        args.project,
        args.home,
        http_proxy=args.http_proxy,
        all_proxy=args.all_proxy,
        readiness_file=args.readiness_file,
        release_sha=args.release_sha,
    )


if __name__ == "__main__":
    main()
