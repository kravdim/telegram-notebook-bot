"""Static boundaries for provider-independent application modules."""

from __future__ import annotations

import ast
from pathlib import Path

APPLICATION = Path(__file__).resolve().parents[1] / "bot" / "application"
TRANSPORT_PREFIXES = ("aiogram", "bot.handlers")


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
            forbidden = TRANSPORT_PREFIXES
            if path.name.endswith("_recognizer.py"):
                forbidden += ("bot.db",)
            for module in modules:
                if module.startswith(forbidden):
                    violations.append(f"{path.name}:{node.lineno}: {module}")

    assert violations == []
