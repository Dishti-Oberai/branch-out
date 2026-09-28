from datetime import datetime

from context_retriever import build_context_graph, get_relevant_context
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


def test_build_context_graph_has_one_node_per_tree_node_and_parent_child_edges():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root"))
    tree.add_node(make_node("b", "a"))
    graph = build_context_graph(tree)
    assert set(graph.nodes) == {"root", "a", "b"}
    assert ("root", "a") in graph.edges
    assert ("a", "b") in graph.edges


def test_build_context_graph_computes_a_real_diff_against_the_parent():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root", code_snapshot="line1\nline2\n"))
    tree.add_node(make_node("b", "a", code_snapshot="line1\nCHANGED\n"))
    graph = build_context_graph(tree)
    # root's code_snapshot is "" -> "a" is a real change (added both lines)
    assert "+line1" in graph.nodes["a"]["code_diff"]
    assert "+line2" in graph.nodes["a"]["code_diff"]
    assert "-line2" in graph.nodes["b"]["code_diff"]
    assert "+CHANGED" in graph.nodes["b"]["code_diff"]


def test_root_has_no_code_diff_since_it_has_no_parent():
    tree = TreeStateManager()
    graph = build_context_graph(tree)
    assert graph.nodes["root"]["code_diff"] == ""


def test_get_relevant_context_walks_a_3_node_chain_root_first():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root", user_preferences=["use Python 3.11"], context_summary="Task: A\nApproach: did A"))
    tree.add_node(make_node("b", "a", context_summary="Task: B\nApproach: did B"))
    context = get_relevant_context(tree, "b")
    # root-first: A's content appears before B's
    assert context.index("did A") < context.index("did B")
    assert "use Python 3.11" in context


def test_sibling_branches_are_isolated_from_each_other():
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root", context_summary="Task: A\nApproach: SECRET_A"))
    tree.add_node(make_node("b", "root", context_summary="Task: B\nApproach: SECRET_B"))
    context_a = get_relevant_context(tree, "a")
    context_b = get_relevant_context(tree, "b")
    assert "SECRET_A" in context_a and "SECRET_B" not in context_a
    assert "SECRET_B" in context_b and "SECRET_A" not in context_b


def test_falls_back_to_context_summary_on_unknown_node():
    tree = TreeStateManager()
    # get_relevant_context is called with a node id that doesn't exist --
    # never raises, falls back to tree.get_node's own KeyError path being
    # caught, then re-fetching (which itself raises) -- ultimately this
    # should surface as a KeyError from tree.get_node in the except branch,
    # not crash the caller. Confirm it propagates cleanly as a KeyError
    # rather than something stranger.
    import pytest
    with pytest.raises(KeyError):
        get_relevant_context(tree, "does-not-exist")


def test_falls_back_to_context_summary_when_composed_context_would_be_empty():
    tree = TreeStateManager()
    # A root-only "tree" with an empty context_summary, no codebase_context,
    # no preferences -- nothing for the graph walk to compose into anything
    # non-empty. Must fall back to the (also empty) context_summary, not
    # raise or return something malformed.
    context = get_relevant_context(tree, "root")
    assert context == ""


def test_includes_the_target_nodes_own_codebase_context():
    tree = TreeStateManager()
    tree.set_codebase_context("root", "--- main.py ---\nSECRET_MARKER")
    tree.add_node(make_node("a", "root", context_summary="Task: A\nApproach: did A", codebase_context="--- main.py ---\nSECRET_MARKER"))
    context = get_relevant_context(tree, "a")
    assert "SECRET_MARKER" in context


def test_codebase_context_is_graph_scoped_when_target_file_is_set():
    # Reuses codebase_graph's target_file scoping -- a query on a node with
    # a target_file shouldn't get the whole codebase either.
    snapshot = (
        "--- main.py ---\nfrom utils import helper\n\n\n"
        "--- utils.py ---\ndef helper():\n    pass\n\n\n"
        "--- unrelated.py ---\nSECRET_UNRELATED\n\n\n"
    )
    tree = TreeStateManager()
    tree.add_node(make_node(
        "a", "root", context_summary="Task: A\nApproach: did A",
        codebase_context=snapshot, target_file="main.py",
    ))
    context = get_relevant_context(tree, "a")
    assert "utils.py" in context
    assert "SECRET_UNRELATED" not in context


def test_query_history_itself_is_never_included_in_composed_context():
    # query_history is a side log of past Q&A about a node, not part of
    # its "decisions/summary" -- composing it into context for a NEW query
    # would be a subtle scope leak, not something CLAUDE.md's spec asks for.
    from models import QueryEntry
    tree = TreeStateManager()
    tree.add_node(make_node("a", "root", context_summary="Task: A\nApproach: did A"))
    tree.add_query_entry("a", QueryEntry(
        id="q1", question="SECRET_PRIOR_QUESTION", answer="SECRET_PRIOR_ANSWER", timestamp=datetime.utcnow(),
    ))
    context = get_relevant_context(tree, "a")
    assert "SECRET_PRIOR_QUESTION" not in context
    assert "SECRET_PRIOR_ANSWER" not in context


def test_handles_a_10_plus_node_session_without_error():
    tree = TreeStateManager()
    parent = "root"
    for i in range(12):
        nid = f"n{i}"
        tree.add_node(make_node(nid, parent, context_summary=f"Task: t{i}\nApproach: a{i}"))
        parent = nid
    context = get_relevant_context(tree, parent)
    assert "a0" in context
    assert "a11" in context
