"""Static boundaries for provider-independent application modules."""

from __future__ import annotations

import ast
from pathlib import Path

APPLICATION = Path(__file__).resolve().parents[1] / "bot" / "application"
FORBIDDEN_PREFIXES = ("aiogram", "bot.handlers", "bot.db", "bot.llm", "bot.services")


def test_application_layer_does_not_import_transport_or_persistence() -> None:
    violations: list[str] = []
    for path in sorted(APPLICATION.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if module.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"{path.name}:{node.lineno}: {module}")

    assert violations == []
