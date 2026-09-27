import copy
import json
from datetime import datetime, timedelta

from models import Branch, QueryEntry, TreeNode, node_from_dict, node_to_dict


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
    # Phase 3 fields default to None for web-UI-created nodes
    assert node.target_file is None
    assert node.target_range is None


def _make_node(**overrides):
    defaults = dict(
        id="n1", parent_id=None, children_ids=[], code_snapshot="print(1)",
        context_summary="", user_preferences=[], score=None, score_source=None,
        status="active", query_history=[], branch_label="Root",
    )
    defaults.update(overrides)
    return TreeNode(**defaults)


def test_node_dict_round_trip_with_file_context():
    node = _make_node(target_file="/workspace/src/foo.py", target_range={"start_line": 3, "end_line": 9})
    restored = node_from_dict(json.loads(json.dumps(node_to_dict(node))))
    assert restored.target_file == "/workspace/src/foo.py"
    assert restored.target_range == {"start_line": 3, "end_line": 9}


def test_node_dict_round_trip_without_file_context():
    node = _make_node()
    restored = node_from_dict(json.loads(json.dumps(node_to_dict(node))))
    assert restored.target_file is None
    assert restored.target_range is None


def test_old_format_session_dict_without_file_context_keys_still_loads():
    # Simulates a session.json saved before Phase 3 existed: no
    # target_file/target_range keys at all, not even as null.
    node = _make_node()
    d = node_to_dict(node)
    del d["target_file"]
    del d["target_range"]
    restored = node_from_dict(d)
    assert restored.target_file is None
    assert restored.target_range is None


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
