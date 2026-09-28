"""Phase 4: cross-node querying's context reconstruction.

CLAUDE.md's "Context graph discipline" is a non-negotiable constraint: a
networkx DiGraph walk is the only permitted way to reconstruct a past
node's context -- never a raw replay of message history. This module is
that walk.

Reuses rather than reimplements: the graph's structure (edges, ordering)
comes straight from `TreeStateManager`'s own parent_id chain -- there is
no second, independently-maintained tree here, which would risk drifting
from the real one. The graph is rebuilt fresh from the tree on every call
rather than persisted separately, since the tree is already the single
source of truth (session persistence is on session end only, per CLAUDE.md
-- a second serialized copy of the same structure would just be another
thing that could go stale). The codebase_context portion of composed
context reuses `codebase_graph.codebase_context_for_prompt` -- the exact
same target_file-scoped selection Phase 3 already uses for /branch and
/continue -- so a query gets the same relevance-scoped codebase context a
continuation would, not a second, separately-decided policy.
"""
from __future__ import annotations

import difflib
from typing import Optional

import networkx as nx

from codebase_graph import codebase_context_for_prompt
from tree_state import TreeStateManager


def build_context_graph(tree: TreeStateManager) -> nx.DiGraph:
    """One node per TreeNode, edges parent->child. Node attributes match
    CLAUDE.md's "Context graph discipline" spec: `decisions`, `code_diff`
    (a real unified diff against the parent's code, via stdlib `difflib` --
    lightweight enough not to need Phase 5's git_engine.py just for this),
    `summary`, `preferences`. `codebase_context` and `target_file` are also
    stored, since Phase 3's codebase-context work needs them too (the
    2026-09-27 design amendment -- a query needs the same real-code
    grounding a continuation gets, not just conversational history).
    """
    graph = nx.DiGraph()
    all_nodes = {n.id: n for n in tree.all_nodes()}
    for node in all_nodes.values():
        parent = all_nodes.get(node.parent_id) if node.parent_id else None
        code_diff = (
            "".join(difflib.unified_diff(
                (parent.code_snapshot or "").splitlines(keepends=True),
                (node.code_snapshot or "").splitlines(keepends=True),
                lineterm="",
            ))
            if parent is not None else ""
        )
        graph.add_node(
            node.id,
            decisions=list(node.user_preferences),
            code_diff=code_diff,
            summary=node.context_summary,
            preferences=list(node.user_preferences),
            codebase_context=node.codebase_context,
            target_file=node.target_file,
        )
        if node.parent_id is not None:
            graph.add_edge(node.parent_id, node.id)
    return graph


def get_relevant_context(tree: TreeStateManager, node_id: str) -> str:
    """Composes a structured context string for `node_id`: its ancestors'
    decisions/summary/code_diff (root-first, via the graph -- never raw
    message replay) plus `node_id`'s own relevance-scoped codebase_context.

    Fails open to the node's own `context_summary` on any failure or an
    empty subgraph (CLAUDE.md's Conservative-by-default: "Context graph
    walk produces empty subgraph? -> Use context_summary, never an empty
    context."). A node with no ancestors (root itself) legitimately has an
    empty *ancestor* subgraph but still composes its own attributes fine --
    "empty subgraph" here means nothing usable came out of the walk at all.
    """
    try:
        graph = build_context_graph(tree)
        if node_id not in graph:
            raise KeyError(node_id)

        # nx.ancestors() is the actual graph walk (CLAUDE.md's literal
        # example); ordering comes from the tree's own already-root-first
        # get_ancestors() rather than re-deriving it from the graph, since
        # the graph's structure is guaranteed identical to the tree's (one
        # is built directly from the other, every call).
        ancestor_ids = nx.ancestors(graph, node_id)
        ordered_ancestor_ids = [a.id for a in tree.get_ancestors(node_id)]
        assert set(ordered_ancestor_ids) == ancestor_ids  # structural sanity check, always true by construction

        parts = []
        for nid in [*ordered_ancestor_ids, node_id]:
            attrs = graph.nodes[nid]
            # Skip a node's contribution entirely when it has nothing real
            # to say -- root, typically. A "Decisions: []\nSummary: "
            # label with no actual content is still non-empty *text*, which
            # would make the empty-subgraph fallback below unreachable for
            # exactly the case it exists for (nothing useful was found).
            if not (attrs["decisions"] or attrs["summary"] or attrs["code_diff"]):
                continue
            lines = []
            if attrs["decisions"]:
                lines.append(f"Decisions: {attrs['decisions']}")
            if attrs["summary"]:
                lines.append(f"Summary: {attrs['summary']}")
            if attrs["code_diff"]:
                lines.append(f"Diff:\n{attrs['code_diff']}")
            parts.append("\n".join(lines))
        composed = "\n---\n".join(parts)

        target_attrs = graph.nodes[node_id]
        scoped_codebase = codebase_context_for_prompt(
            target_attrs["codebase_context"], target_attrs["target_file"], include_codebase=True,
        )
        if scoped_codebase:
            composed = f"Codebase:\n{scoped_codebase}\n\n{composed}" if composed else f"Codebase:\n{scoped_codebase}"

        if not composed.strip():
            raise ValueError("empty subgraph")
        return composed
    except Exception:
        node = tree.get_node(node_id)
        return node.context_summary or ""


__all__ = ["build_context_graph", "get_relevant_context"]
