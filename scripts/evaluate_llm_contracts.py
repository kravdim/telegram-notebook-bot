#!/usr/bin/env python3
"""Offline quality gate for anonymized saved LLM function-call cases."""

import json
import sys
from pathlib import Path


def arguments_match_schema(name: str, arguments: dict, functions: list[dict]) -> bool:
    """Check nested tool arguments, including arrays and explicit nullable fields."""
    schema = next((item["parameters"] for item in functions if item["name"] == name), None)
    if schema is None or not isinstance(arguments, dict):
        return False
    return _matches_schema(arguments, schema)


def _matches_schema(value: object, schema: dict) -> bool:
    allowed = schema.get("type")
    if allowed is not None:
        allowed = allowed if isinstance(allowed, list) else [allowed]
        types: dict[str, tuple[type, ...]] = {
            "string": (str,), "boolean": (bool,), "array": (list,), "object": (dict,),
            "integer": (int,), "number": (int, float), "null": (type(None),),
        }
        if not any(type(value) in types.get(kind, ()) for kind in allowed):
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        return all(key in value for key in schema.get("required", [])) and all(
            key in properties and _matches_schema(item, properties[key])
            for key, item in value.items()
        )
    if isinstance(value, list):
        return all(_matches_schema(item, schema.get("items", {})) for item in value)
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
        f"saved_response_cases={utterance_total} saved_response_contract_accuracy={utterance_accuracy:.3f} "
        f"invalid_saved_response_rate={invalid_rate:.3f} "
        "scope=saved-response-parsing-not-intent-recognition"
    )
    if parser_accuracy < 1.0 or utterance_accuracy < 1.0 or invalid_rate > 0:
        raise SystemExit("LLM contract regression")


if __name__ == "__main__":
    main()
