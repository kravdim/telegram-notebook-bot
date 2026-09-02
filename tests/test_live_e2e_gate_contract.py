from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_live_e2e_gate.sh"


def test_live_gate_binds_evidence_to_exact_production_sha():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'TESTED_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"' in source
    assert 'DEPLOYED_SHA="$(head -1 "$CURRENT_RELEASE_FILE")"' in source
    assert "Live E2E SHA mismatch" in source
    assert "Production SHA changed during live E2E" in source
    assert "tested_sha=$TESTED_SHA" in source
    assert "tests/live/run_memoir_gate.py" in source


def test_live_gate_requires_state_and_cleanup_oracles():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "State oracle:" in source
    assert "Cleanup oracle:" in source
