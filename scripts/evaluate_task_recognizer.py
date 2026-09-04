#!/usr/bin/env python3
"""Exercise current deterministic code on utterances, not saved model responses."""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pendulum


def evaluate(corpus: dict, recognizer: Callable) -> list[str]:
    now = pendulum.parse(corpus["now"])
    failed = []
    with patch("pendulum.now", return_value=now):
        for case in corpus["cases"]:
            result = recognizer(case["utterance"], corpus["timezone"])
            if result != case["expected"]:
                failed.append(case["id"])
    return failed


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from bot.application.task_creation_recognizer import extract_task_request

    corpus = json.loads((root / "tests/fixtures/task_recognizer_cases.json").read_text())
    failed = evaluate(corpus, extract_task_request)
    print(f"deterministic_utterance_cases={len(corpus['cases'])} failed={len(failed)} scope=task-fast-path")
    if failed:
        raise SystemExit("Current recognizer regression: " + ", ".join(failed))


if __name__ == "__main__":
    main()
