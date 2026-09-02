#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"
exec uv run pytest -q \
    tests/test_macos_staged_deploy_contract.py::test_executable_failure_injection_matrix
