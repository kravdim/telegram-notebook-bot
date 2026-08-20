#!/usr/bin/env python3
"""Offline quality gate for anonymized saved LLM function-call cases."""

import json
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot.llm.dispatcher import parse_function_call

    fixture = Path("tests/fixtures/llm_intent_cases.json")
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    correct = 0
    invalid = 0
    for case in cases:
        try:
            name, _ = parse_function_call(case["raw"])
            correct += int(name == case["name"])
        except Exception:
            invalid += 1
    total = len(cases)
    accuracy = correct / total if total else 0.0
    invalid_rate = invalid / total if total else 1.0
    print(
        f"golden_cases={total} intent_accuracy={accuracy:.3f} "
        f"invalid_tool_rate={invalid_rate:.3f}"
    )
    if accuracy < 1.0 or invalid_rate > 0:
        raise SystemExit("LLM contract regression")


if __name__ == "__main__":
    main()
