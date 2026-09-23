import copy
from datetime import datetime, timedelta

from models import Branch, QueryEntry, TreeNode


def test_query_entry_fields():
    q = QueryEntry(id="q1", question="why?", answer="because", timestamp=datetime.utcnow())
    assert q.id == "q1"
    assert q.chained_from is None


def test_tree_node_fields_and_default_timestamp():
    before = datetime.utcnow()
    node = TreeNode(
        id="n1",
        parent_id=None,
        children_ids=[],
        code_snapshot="print(1)",
        context_summary="",
        user_preferences=[],
        score=None,
        score_source=None,
        status="active",
        query_history=[],
        branch_label="Root",
    )
    after = datetime.utcnow()
    assert isinstance(node.created_at, datetime)
    assert before - timedelta(seconds=1) <= node.created_at <= after + timedelta(seconds=1)


def test_branch_defaults():
    b = Branch(node_id="n1", code="x = 1", reasoning="simple")
    assert b.score is None
    assert b.rank is None


def test_dataclass_instances_are_independently_mutable_copies():
    # The dataclasses themselves are plain (not frozen) so the tree store is
    # responsible for immutability via deep copies — see test_tree_state.py.
    node = TreeNode(
        id="n1",
        parent_id=None,
        children_ids=[],
        code_snapshot="a",
        context_summary="",
        user_preferences=[],
        score=None,
        score_source=None,
        status="active",
        query_history=[],
        branch_label="Root",
    )
    clone = copy.deepcopy(node)
    clone.code_snapshot = "b"
    assert node.code_snapshot == "a"
