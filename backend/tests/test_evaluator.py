import asyncio
import json

from evaluator import evaluate_branches, rescore_with_tests
from models import Branch, TreeNode
from tests.conftest import FakeLLM
from tree_state import TreeStateManager

PASSING_UNITTEST = """
import unittest

class T(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(1 + 1, 2)
"""

FAILING_UNITTEST = """
import unittest

class T(unittest.TestCase):
    def test_bad(self):
        self.assertEqual(1 + 1, 3)
"""

NO_TESTS = "def add(a, b):\n    return a + b\n"


def test_branches_with_tests_use_test_scoring():
    branches = [
        Branch(node_id="a", code=PASSING_UNITTEST, reasoning="r"),
        Branch(node_id="b", code=FAILING_UNITTEST, reasoning="r"),
    ]
    result = asyncio.run(evaluate_branches(branches, FakeLLM()))
    assert all(b.score_source == "tests" for b in result)
    by_id = {b.node_id: b for b in result}
    assert by_id["a"].score == 1.0
    assert by_id["b"].score < 1.0
    # sorted descending by score
    assert result[0].node_id == "a"


def test_branches_without_tests_use_llm_judge():
    branches = [
        Branch(node_id="a", code=NO_TESTS, reasoning="r"),
        Branch(node_id="b", code=NO_TESTS, reasoning="r"),
    ]
    valid = json.dumps([
        {"branch_index": 0, "rank": 1, "score": 0.8, "reason": "x"},
        {"branch_index": 1, "rank": 2, "score": 0.3, "reason": "x"},
    ])
    llm = FakeLLM(response_fn=lambda i: valid)
    result = asyncio.run(evaluate_branches(branches, llm))
    assert all(b.score_source == "llm_judge" for b in result)


def test_one_branchs_test_failure_does_not_prevent_others_from_scoring():
    branches = [
        Branch(node_id="a", code=PASSING_UNITTEST, reasoning="r"),
        Branch(node_id="b", code="import unittest\nraise RuntimeError('boom')", reasoning="r"),
    ]
    result = asyncio.run(evaluate_branches(branches, FakeLLM()))
    by_id = {b.node_id: b for b in result}
    assert by_id["a"].score == 1.0
    assert by_id["b"].score is not None  # scored, even if 0-ish, never crashed the batch


def test_empty_branch_list():
    result = asyncio.run(evaluate_branches([], FakeLLM()))
    assert result == []


def make_node(node_id, code):
    return TreeNode(
        id=node_id, parent_id="root", children_ids=[], code_snapshot=code,
        context_summary="", user_preferences=[], score=0.5, score_source="llm_judge",
        status="normal", query_history=[], branch_label="Branch A",
    )


def test_rescore_with_tests_updates_only_named_nodes():
    tree = TreeStateManager()
    tree.add_node(make_node("a", PASSING_UNITTEST))
    tree.add_node(make_node("b", PASSING_UNITTEST))

    updated = rescore_with_tests(["a"], tree)

    assert len(updated) == 1
    assert updated[0].id == "a"
    assert tree.get_node("a").score == 1.0
    assert tree.get_node("a").score_source == "tests"
    # untouched
    assert tree.get_node("b").score == 0.5
    assert tree.get_node("b").score_source == "llm_judge"
