import json
from pathlib import Path

import pytest

from bot.llm.functions import FUNCTIONS
from scripts.evaluate_llm_contracts import arguments_match_schema
from scripts.evaluate_task_recognizer import evaluate


@pytest.mark.parametrize("updates,accepted", [
    ({"due_date": None}, True),
    ({"due_time": None, "scheduled_date": None}, True),
    ({"due_date": 42}, False),
    ({"priority": "impossible"}, False),
    ({"is_frog": "false"}, False),
    ({"is_frog": False}, True),
    ({"unexpected_field": "value"}, False),
])
def test_nested_updates_validated(updates, accepted):
    assert arguments_match_schema(
        "update_task", {"search_query": "report", "updates": updates}, FUNCTIONS,
    ) is accepted


@pytest.mark.parametrize("tags,accepted", [(["work"], True), ([1], False), ("work", False)])
def test_array_items_validated(tags, accepted):
    assert arguments_match_schema("create_note", {"content": "test", "tags": tags}, FUNCTIONS) is accepted


def test_real_recognizer_corpus_detects_broken_implementation():
    from bot.application.task_creation_recognizer import extract_task_request

    corpus = json.loads((Path(__file__).parent / "fixtures/task_recognizer_cases.json").read_text())
    assert evaluate(corpus, extract_task_request) == []
    assert evaluate(corpus, lambda text, timezone: None)
    assert evaluate(corpus, lambda text, timezone: {"title": text})
