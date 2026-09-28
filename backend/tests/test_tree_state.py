import pytest

from models import TreeNode
from tree_state import TreeStateManager


def make_node(id, parent_id, **overrides):
    defaults = dict(
        id=id,
        parent_id=parent_id,
        children_ids=[],
        code_snapshot="",
        context_summary="",
        user_preferences=[],
        score=None,
        score_source=None,
        status="normal",
        query_history=[],
        branch_label=id,
    )
    defaults.update(overrides)
    return TreeNode(**defaults)


def test_add_and_get_node():
    tree = TreeStateManager()
    node = make_node("a", "root")
    tree.add_node(node)
    fetched = tree.get_node("a")
    assert fetched.id == "a"
    assert fetched.parent_id == "root"


def test_get_ancestors():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    tree.add_node(make_node("b", "a"))
    ancestors = tree.get_ancestors("b")
    assert [n.id for n in ancestors] == ["root", "a"]


def test_get_children():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    tree.add_node(make_node("b", "root"))
    children = {n.id for n in tree.get_children("root")}
    assert children == {"a", "b"}


def test_add_node_duplicate_id_raises():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    with pytest.raises(ValueError):
        tree.add_node(make_node("a", "root"))


def test_mutating_a_retrieved_node_does_not_affect_the_store():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root", code_snapshot="original"))
    fetched = tree.get_node("a")
    fetched.code_snapshot = "mutated"
    assert tree.get_node("a").code_snapshot == "original"


def test_set_active_updates_only_status():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root", code_snapshot="x"))
    tree.set_active("a")
    assert tree.get_node("a").status == "active"
    assert tree.get_node("root").status == "normal"
    assert tree.get_node("a").code_snapshot == "x"


def test_set_codebase_context_updates_only_that_node():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    tree.set_codebase_context("root", "--- main.py ---\nprint(1)")
    assert tree.get_node("root").codebase_context == "--- main.py ---\nprint(1)"
    assert tree.get_node("a").codebase_context is None


def test_add_query_entry_appends_only_to_that_node():
    from datetime import datetime
    from models import QueryEntry

    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    entry = QueryEntry(id="q1", question="what?", answer="this.", timestamp=datetime.utcnow())
    tree.add_query_entry("a", entry)
    assert tree.get_node("a").query_history == [entry]
    assert tree.get_node("root").query_history == []


def test_add_query_entry_accumulates_multiple_entries_in_order():
    from datetime import datetime
    from models import QueryEntry

    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    e1 = QueryEntry(id="q1", question="first?", answer="a1", timestamp=datetime.utcnow())
    e2 = QueryEntry(id="q2", question="second?", answer="a2", timestamp=datetime.utcnow(), chained_from="q1")
    tree.add_query_entry("a", e1)
    tree.add_query_entry("a", e2)
    assert [q.id for q in tree.get_node("a").query_history] == ["q1", "q2"]


def test_mark_stale_marks_only_specified_nodes():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    tree.add_node(make_node("b", "root"))
    tree.mark_stale(["a"])
    assert tree.get_node("a").status == "stale"
    assert tree.get_node("b").status == "normal"


def test_node_count():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    assert tree.node_count() == 2
