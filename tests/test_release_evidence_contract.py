import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.build_release_manifest import build_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_release_waits_for_same_sha_reusable_quality_gate():
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert "workflow_call" in ci[True]  # PyYAML YAML 1.1 parses 'on' as True.
    assert release["jobs"]["quality"]["uses"] == "./.github/workflows/ci.yml"
    evidence = release["jobs"]["evidence"]
    assert evidence["needs"] == "quality"
    checksums = next(s for s in evidence["steps"] if s.get("name") == "Create checksums")
    assert 'cp "$PROVENANCE_BUNDLE" attestation.json' in checksums["run"]
    assert "sha256sum --check SHA256SUMS" in checksums["run"]
    assert "release-manifest.json" in checksums["run"]
    assert "--extra stt" in (ROOT / ".github/workflows/ci.yml").read_text()


def test_manifest_identifies_commit_schema_and_evidence_limits(monkeypatch):
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    with patch("scripts.build_release_manifest.subprocess.check_output", return_value="a" * 40):
        manifest = build_manifest(ROOT)
    assert manifest["revision"] == "a" * 40
    assert manifest["schema_head"] == "d9f2a4b6c803"
    assert manifest["image_profile"] == "cloud"
    assert manifest["live_e2e"] == "not-attested-by-this-manifest"
    assert json.loads(json.dumps(manifest)) == manifest
    assert all(Path(asset).name == asset for asset in manifest["assets"])


@pytest.mark.parametrize("variable,value", [("GITHUB_SHA", "wrong"), ("GITHUB_REF_NAME", "v0.0.0")])
def test_manifest_rejects_mismatched_release_identity(monkeypatch, variable, value):
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        build_manifest(ROOT)
