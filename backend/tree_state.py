from __future__ import annotations

import copy
import uuid
from typing import Dict, List, Optional

from models import QueryEntry, TreeNode

ROOT_ID = "root"


class TreeStateManager:
    """Append-only, in-memory store of TreeNode objects.

    `status` and `children_ids` are the only fields that may change on an
    existing node once created. Every accessor returns a deep copy, so a
    caller mutating a returned node can never affect the stored one.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, TreeNode] = {}
        self.active_id: str = ROOT_ID
        self._add_root()

    def _add_root(self) -> None:
        self._nodes[ROOT_ID] = TreeNode(
            id=ROOT_ID,
            parent_id=None,
            children_ids=[],
            code_snapshot="",
            context_summary="",
            user_preferences=[],
            score=None,
            score_source=None,
            status="active",
            query_history=[],
            branch_label="Root",
        )

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:8]

    # -- internal, uncopied access -----------------------------------------
    def _raw(self, node_id: str) -> TreeNode:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise KeyError(f"Unknown node: {node_id}")

    # -- spec API -------------------------------------------------------
    def add_node(self, node: TreeNode) -> None:
        """Append only, never replace. Duplicate id raises ValueError."""
        if node.id in self._nodes:
            raise ValueError(f"Node {node.id} already exists")
        if node.parent_id is not None:
            parent = self._raw(node.parent_id)
            parent.children_ids.append(node.id)
        elif self._nodes:
            raise ValueError("Only the root may have no parent")
        self._nodes[node.id] = copy.deepcopy(node)

    def get_node(self, node_id: str) -> TreeNode:
        return copy.deepcopy(self._raw(node_id))

    def get_children(self, node_id: str) -> List[TreeNode]:
        node = self._raw(node_id)
        return [self.get_node(cid) for cid in node.children_ids]

    def get_ancestors(self, node_id: str) -> List[TreeNode]:
        """Root-first list of the node's ancestors (excludes the node itself)."""
        ancestors: List[TreeNode] = []
        current = self._raw(node_id).parent_id
        while current is not None:
            ancestors.append(self.get_node(current))
            current = self._raw(current).parent_id
        return list(reversed(ancestors))

    def get_descendants(self, node_id: str) -> List[TreeNode]:
        out: List[TreeNode] = []
        stack = list(self._raw(node_id).children_ids)
        while stack:
            cid = stack.pop()
            out.append(self.get_node(cid))
            stack.extend(self._raw(cid).children_ids)
        return out

    def set_active(self, node_id: str) -> None:
        """Updates the `status` field only, on the old and new active nodes."""
        target = self._raw(node_id)
        old = self._nodes.get(self.active_id)
        if old is not None and old.id != node_id and old.status == "active":
            old.status = "normal"
        target.status = "active"
        self.active_id = node_id

    def set_score(self, node_id: str, score: Optional[float], score_source: Optional[str]) -> None:
        """Phase 2: updates `score`/`score_source` only.

        These join `status` and `children_ids` as the mutable-metadata
        exception to the append-only rule — required for retroactive
        rescoring (CLAUDE.md's Evaluation discipline section), which by
        definition means updating an already-created node's score. Never
        touches `code_snapshot`, `context_summary`, or anything else that
        counts as the node's actual content.
        """
        node = self._raw(node_id)
        node.score = score
        node.score_source = score_source

    def set_codebase_context(self, node_id: str, codebase_context: Optional[str]) -> None:
        """Phase 3 (2026-09-27): updates `codebase_context` only.

        Joins `score`/`score_source` as mutable metadata — set once when a
        workspace is declared (or re-set on an explicit refresh), never as
        part of normal append-only node creation. New nodes get their own
        copy via inheritance in Orchestrator, not through this method.
        """
        self._raw(node_id).codebase_context = codebase_context

    def add_query_entry(self, node_id: str, entry: QueryEntry) -> None:
        """Phase 4 (2026-09-27): appends one `QueryEntry` to `node_id`'s own
        `query_history`. Joins `score`/`score_source`/`codebase_context` as
        mutable metadata -- a query answered about a node doesn't change
        that node's actual content (`code_snapshot`/`context_summary`),
        it's a side log that accumulates on the node itself. Never touches
        any other node, and never creates one -- the active node stays
        untouched by design (that's the whole point of Phase 4: query a
        past node without moving off the current one).
        """
        self._raw(node_id).query_history.append(entry)

    def mark_stale(self, node_ids: List[str]) -> None:
        for nid in node_ids:
            self._raw(nid).status = "stale"

    def mark_orphaned(self, node_id: str) -> None:
        self._raw(node_id).status = "orphaned"

    def revive(self, node_id: str) -> None:
        node = self._raw(node_id)
        if node.status == "orphaned":
            node.status = "normal"

    def all_nodes(self) -> List[TreeNode]:
        return [copy.deepcopy(n) for n in self._nodes.values()]

    def node_count(self) -> int:
        return len(self._nodes)

    # -- session support --------------------------------------------------
    def path_to(self, node_id: str) -> List[str]:
        path: List[str] = [node_id]
        current = self._raw(node_id).parent_id
        while current is not None:
            path.append(current)
            current = self._raw(current).parent_id
        return list(reversed(path))

    @classmethod
    def from_nodes(cls, nodes: List[TreeNode]) -> "TreeStateManager":
        store = cls.__new__(cls)
        store._nodes = {n.id: copy.deepcopy(n) for n in nodes}
        if ROOT_ID not in store._nodes:
            raise ValueError("Session has no root node")
        active = [n.id for n in nodes if n.status == "active"]
        store.active_id = active[0] if active else ROOT_ID
        return store


# Backwards-compatible alias used by earlier Phase 1 code/tests.
TreeStore = TreeStateManager
