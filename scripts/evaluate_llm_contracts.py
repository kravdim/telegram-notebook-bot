#!/usr/bin/env python3
"""Offline quality gate for anonymized saved LLM function-call cases."""

import json
import sys
from pathlib import Path


def arguments_match_schema(name: str, arguments: dict, functions: list[dict]) -> bool:
    """Check required fields, primitive types and enums in the tool schema."""
    schema = next((item["parameters"] for item in functions if item["name"] == name), None)
    if schema is None or not isinstance(arguments, dict):
        return False
    if any(key not in arguments for key in schema.get("required", [])):
        return False
    python_types = {"string": str, "boolean": bool, "array": list, "object": dict}
    for key, value in arguments.items():
        field = schema.get("properties", {}).get(key)
        if field is None:
            return False
        expected_type = python_types.get(field.get("type"))
        if expected_type and not isinstance(value, expected_type):
            return False
        if "enum" in field and value not in field["enum"]:
            return False
    return True


def evaluate_cases(cases: list[dict], parser, functions: list[dict]) -> tuple[int, int]:
    """Return correct and invalid counts for saved provider responses."""
    correct = 0
    invalid = 0
    for case in cases:
        try:
            name, arguments = parser(case["raw"])
            expected = case.get("expected_arguments", {})
            matches = arguments_match_schema(name, arguments, functions) and name == case["name"] and all(
                arguments.get(key) == value for key, value in expected.items()
            )
            correct += int(matches)
        except Exception:
            invalid += 1
    return correct, invalid


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot.llm.dispatcher import parse_function_call
    from bot.llm.functions import FUNCTIONS

    parser_cases = json.loads(
        Path("tests/fixtures/llm_intent_cases.json").read_text(encoding="utf-8")
    )
    utterance_cases = json.loads(
        Path("tests/fixtures/llm_utterance_cases.json").read_text(encoding="utf-8")
    )
    parser_correct, parser_invalid = evaluate_cases(
        parser_cases, parse_function_call, FUNCTIONS
    )
    utterance_correct, utterance_invalid = evaluate_cases(
        utterance_cases, parse_function_call, FUNCTIONS
    )
    parser_total = len(parser_cases)
    utterance_total = len(utterance_cases)
    parser_accuracy = parser_correct / parser_total if parser_total else 0.0
    utterance_accuracy = utterance_correct / utterance_total if utterance_total else 0.0
    invalid = parser_invalid + utterance_invalid
    total = parser_total + utterance_total
    invalid_rate = invalid / total if total else 1.0
    print(
        f"parser_cases={parser_total} tool_call_parser_accuracy={parser_accuracy:.3f} "
        f"utterance_cases={utterance_total} utterance_contract_accuracy={utterance_accuracy:.3f} "
        f"invalid_tool_rate={invalid_rate:.3f}"
    )
    if parser_accuracy < 1.0 or utterance_accuracy < 1.0 or invalid_rate > 0:
        raise SystemExit("LLM contract regression")


if __name__ == "__main__":
    main()
