#!/usr/bin/env python3
"""Reject growth in every allowlisted Ruff complexity metric."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPLEXITY_CODES = {"C901", "PLR0911", "PLR0912", "PLR0915"}
BASELINES: dict[tuple[str, str], dict[str, int]] = {
    ("bot/config.py", "validate_runtime_config"): {"C901": 32, "PLR0912": 31},
    ("bot/formatters/__init__.py", "split_html_message"): {"C901": 18},
    ("bot/formatters/digest.py", "format_morning_digest"): {"C901": 17},
    ("bot/handlers/messages.py", "_extract_common_intent"): {
        "C901": 35,
        "PLR0911": 21,
        "PLR0912": 34,
        "PLR0915": 88,
    },
    ("bot/llm/dispatcher.py", "_handle_list_tasks"): {"C901": 16},
    ("bot/llm/dispatcher.py", "_handle_update_task"): {
        "C901": 22,
        "PLR0911": 15,
        "PLR0912": 21,
    },
    ("bot/scheduler/backup.py", "run_backup"): {"C901": 18, "PLR0915": 89},
    ("bot/scheduler/weekly_review.py", "_format_review"): {"C901": 17},
    ("scripts/delete_user_data.py", "run"): {"C901": 16},
}
FUNCTION_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\b")
NOQA_RE = re.compile(r"#\s*noqa:\s*([^#-]+)")
VALUE_RE = re.compile(r"\((\d+)\s*>\s*\d+\)")
INLINE_RATCHET_RE = re.compile(r"[ \t]+#\s*noqa:.*legacy ratchet[^\n]*")


def _declared_exceptions() -> tuple[dict[tuple[str, str], set[str]], list[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    errors: list[str] = []
    for scope in (ROOT / "bot", ROOT / "scripts"):
        for path in scope.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                noqa = NOQA_RE.search(line)
                if noqa is None:
                    continue
                risky = {
                    code.strip() for code in noqa.group(1).split(",")
                } & COMPLEXITY_CODES
                if not risky:
                    continue
                function = FUNCTION_RE.match(line)
                if function is None:
                    errors.append(
                        f"{relative}:{number}: complexity noqa must be on its def line"
                    )
                    continue
                found[(relative, function.group(1))] = risky
    return found, errors


def _ruff_measurements() -> dict[tuple[str, str], dict[str, int]]:
    ruff = shutil.which("ruff") or str(ROOT / ".venv/bin/ruff")
    if not Path(ruff).is_file():
        raise SystemExit("ruff executable is required for complexity ratchet")
    with tempfile.TemporaryDirectory(prefix="dailyplanner-complexity-") as tmp_name:
        tmp = Path(tmp_name)
        files: list[str] = []
        for relative, _function in BASELINES:
            source = ROOT / relative
            target = tmp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                INLINE_RATCHET_RE.sub("", source.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            files.append(str(target))
        result = subprocess.run(
            [
                ruff,
                "check",
                *files,
                "--config",
                str(ROOT / "pyproject.toml"),
                "--select",
                ",".join(sorted(COMPLEXITY_CODES)),
                "--output-format",
                "json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise SystemExit(result.stderr or result.stdout)
        diagnostics = json.loads(result.stdout)
        measured: dict[tuple[str, str], dict[str, int]] = {}
        for diagnostic in diagnostics:
            code = diagnostic["code"]
            if code not in COMPLEXITY_CODES:
                continue
            relative = Path(diagnostic["filename"]).relative_to(tmp).as_posix()
            row = int(diagnostic["location"]["row"])
            line = (tmp / relative).read_text(encoding="utf-8").splitlines()[row - 1]
            function_match = FUNCTION_RE.match(line)
            value_match = VALUE_RE.search(diagnostic["message"])
            if function_match is None or value_match is None:
                raise SystemExit(f"Cannot parse Ruff diagnostic: {diagnostic!r}")
            key = (relative, function_match.group(1))
            measured.setdefault(key, {})[code] = int(value_match.group(1))
        return measured


def main() -> None:
    declared, errors = _declared_exceptions()
    measured = _ruff_measurements()
    for key, baseline in BASELINES.items():
        expected_codes = set(baseline)
        actual_codes = declared.get(key)
        label = f"{key[0]}:{key[1]}"
        if actual_codes is None:
            errors.append(f"{label}: stale baseline; remove it with the retired noqa")
            continue
        if actual_codes != expected_codes:
            errors.append(
                f"{label}: exceptions changed: {sorted(actual_codes)} != {sorted(expected_codes)}"
            )
        current = measured.get(key, {})
        for code, ceiling in baseline.items():
            value = current.get(code)
            if value is None:
                errors.append(f"{label}: {code} is now below threshold; remove its noqa and baseline")
            elif value > ceiling:
                errors.append(f"{label}: {code} increased: {value} > {ceiling}")

    unexpected = set(declared) - set(BASELINES)
    for relative, function in sorted(unexpected):
        errors.append(f"{relative}:{function}: new complexity exception")
    if errors:
        raise SystemExit("\n".join(errors))
    metric_count = sum(len(metrics) for metrics in BASELINES.values())
    print(
        f"complexity ratchet ok: {metric_count} numeric metrics "
        f"across {len(BASELINES)} legacy functions"
    )


if __name__ == "__main__":
    main()
