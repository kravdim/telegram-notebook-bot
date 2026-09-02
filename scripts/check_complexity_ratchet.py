#!/usr/bin/env python3
"""Reject new complexity suppressions while allowing the named legacy backlog."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPLEXITY_CODES = {"C901", "PLR0911", "PLR0912", "PLR0915"}
MAX_VIOLATIONS = 16
ALLOWED: dict[tuple[str, str], set[str]] = {
    ("bot/config.py", "validate_runtime_config"): {"C901", "PLR0912"},
    ("bot/formatters/__init__.py", "split_html_message"): {"C901"},
    ("bot/formatters/digest.py", "format_morning_digest"): {"C901"},
    ("bot/handlers/messages.py", "_extract_common_intent"): {
        "C901", "PLR0911", "PLR0912", "PLR0915",
    },
    ("bot/llm/dispatcher.py", "_handle_list_tasks"): {"C901"},
    ("bot/llm/dispatcher.py", "_handle_update_task"): {"C901", "PLR0911", "PLR0912"},
    ("bot/scheduler/backup.py", "run_backup"): {"C901", "PLR0915"},
    ("bot/scheduler/weekly_review.py", "_format_review"): {"C901"},
    ("scripts/delete_user_data.py", "run"): {"C901"},
}
FUNCTION_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\b")
NOQA_RE = re.compile(r"#\s*noqa:\s*([^#-]+)")


def main() -> None:
    found: dict[tuple[str, str], set[str]] = {}
    errors: list[str] = []
    for scope in (ROOT / "bot", ROOT / "scripts"):
        for path in scope.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                noqa = NOQA_RE.search(line)
                if noqa is None:
                    continue
                codes = {code.strip() for code in noqa.group(1).split(",")}
                risky = codes & COMPLEXITY_CODES
                if not risky:
                    continue
                function = FUNCTION_RE.match(line)
                if function is None:
                    errors.append(f"{relative}:{number}: complexity noqa must be on its def line")
                    continue
                key = (relative, function.group(1))
                found[key] = risky
                allowed = ALLOWED.get(key)
                if allowed is None:
                    errors.append(f"{relative}:{number}: new complexity exception {sorted(risky)}")
                elif not risky <= allowed:
                    errors.append(
                        f"{relative}:{number}: exception widened from {sorted(allowed)} "
                        f"to {sorted(risky)}"
                    )

    violation_count = sum(len(codes) for codes in found.values())
    if violation_count > MAX_VIOLATIONS:
        errors.append(f"complexity violations increased: {violation_count} > {MAX_VIOLATIONS}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"complexity ratchet ok: {violation_count}/{MAX_VIOLATIONS} violations "
        f"across {len(found)} legacy functions"
    )


if __name__ == "__main__":
    main()
