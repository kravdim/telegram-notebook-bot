#!/usr/bin/env python3
"""Reject every Ruff complexity suppression in product and operational code."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPLEXITY_CODES = {"C901", "PLR0911", "PLR0912", "PLR0915"}
FUNCTION_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\b")
NOQA_RE = re.compile(r"#\s*noqa:\s*([^#-]+)")


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


def main() -> None:
    declared, errors = _declared_exceptions()
    for relative, function in sorted(declared):
        errors.append(f"{relative}:{function}: new complexity exception")
    if errors:
        raise SystemExit("\n".join(errors))
    print("complexity ratchet ok: zero complexity exceptions")


if __name__ == "__main__":
    main()
