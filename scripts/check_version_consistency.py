#!/usr/bin/env python3
"""Fail when package, changelog, or release-tag versions diverge."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\s+—\s+\d{4}-\d{2}-\d{2}$", re.MULTILINE)


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    package_version = str(project["version"])
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = VERSION_HEADING.search(changelog)
    if match is None:
        print("CHANGELOG has no dated semantic-version heading", file=sys.stderr)
        return 1
    changelog_version = match.group(1)
    if changelog_version != package_version:
        print(
            f"version mismatch: pyproject={package_version} changelog={changelog_version}",
            file=sys.stderr,
        )
        return 1

    ref_type = os.environ.get("GITHUB_REF_TYPE")
    ref_name = os.environ.get("GITHUB_REF_NAME")
    if ref_type == "tag" and ref_name != f"v{package_version}":
        print(
            f"tag mismatch: expected v{package_version}, got {ref_name or '<empty>'}",
            file=sys.stderr,
        )
        return 1

    print(f"version consistency ok: {package_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
