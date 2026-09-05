#!/usr/bin/env python3
"""Emit a portable manifest bound to the actual checked-out release commit."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def build_manifest(root: Path) -> dict:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
    ).strip()
    expected = os.environ.get("GITHUB_SHA", revision)
    if expected != revision:
        raise ValueError("Release checkout does not match workflow SHA")
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    tag = os.environ.get("GITHUB_REF_NAME", f"v{version}")
    if tag != f"v{version}":
        raise ValueError("Release tag does not match package version")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "bot/db/migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise ValueError("Release requires one schema head")
    return {
        "manifest_version": 1,
        "revision": revision,
        "version": version,
        "tag": tag,
        "schema_head": heads[0],
        "image_profile": "cloud",
        "dependency_audit_profiles": ["cloud", "local-stt"],
        "quality_gate": "reusable-ci-at-release-sha",
        "assets": ["dailyplanner-image.tar", "dailyplanner.sbom.cdx.json", "attestation.json"],
        "live_e2e": "not-attested-by-this-manifest",
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = build_manifest(root)
    (root / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )


if __name__ == "__main__":
    main()
