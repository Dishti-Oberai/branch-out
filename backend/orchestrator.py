import asyncio
from typing import List, Optional, Tuple

from branch_generator import branch_label, generate_branches
from models import DEFAULT_BEAM_WIDTH, Branch, TreeNode
from tree_state import TreeStateManager


class LLMGenerationFailed(RuntimeError):
    """A single-call generation (continue_from / edit_prompt) produced nothing.

    Distinct from ValueError so callers can't accidentally conflate an LLM
    failure with a bad-request validation error (they need different HTTP
    statuses: 502 vs 400).
    """


class Orchestrator:
    """Turn loop: branch from any node, backtrack to any node, continue linearly.

    Single-session POC scope (see ROADMAP.md): one `asyncio.Lock` serializes
    every tree-mutating call (`branch`, `continue_from`, `edit_prompt`,
    `backtrack`) so two in-flight requests can't interleave mid-mutation —
    e.g. two `branch()` calls against the same parent both reading a stale
    `children_ids` offset before either commits, or a `backtrack()` running
    while a `branch()` is still awaiting its LLM call. This is not a
    per-session lock (out of scope: multi-session isn't a POC goal), just a
    single global one guarding this orchestrator's one tree.
    """

    def __init__(self, llm_client, tree: Optional[TreeStateManager] = None) -> None:
        self.llm = llm_client
        self.tree = tree or TreeStateManager()
        self._lock = asyncio.Lock()

    async def branch(
        self,
        node_id: str,
        prompt: str,
        beam_width: int = DEFAULT_BEAM_WIDTH,
    ) -> List[Branch]:
        """Generate up to `beam_width` children of `node_id`. Never mutates existing code."""
        async with self._lock:
            return await self._branch_unlocked(node_id, prompt, beam_width)

    async def _branch_unlocked(
        self, node_id: str, prompt: str, beam_width: int
    ) -> List[Branch]:
        parent = self.tree.get_node(node_id)
        branches = await generate_branches(
            prompt, parent.context_summary, parent.user_preferences, self.llm, beam_width
        )
        offset = len(parent.children_ids)
        for i, b in enumerate(branches):
            node = TreeNode(
                id=self.tree.new_id(),
                parent_id=parent.id,
                children_ids=[],
                code_snapshot=b.code,
                context_summary=_extend_summary(parent.context_summary, prompt, b.reasoning),
                user_preferences=list(parent.user_preferences),
                score=None,
                score_source=None,
                status="normal",
                query_history=[],
                branch_label=f"Branch {branch_label(offset + i)}",
            )
            self.tree.add_node(node)
            b.node_id = node.id
        return branches

    async def continue_from(self, node_id: str, prompt: str) -> TreeNode:
        """Single LLM call, no branching: adds one new child of `node_id` and
        makes it the active node — a reply advances the conversation, the
        way any normal chat does. Without this, asking a second question
        from the same still-active node just fans out another sibling
        instead of continuing, which reads as unwanted branching.

        `edit_prompt` deliberately does NOT go through this: its new sibling
        stays inactive until the user chooses to make it active.
        """
        async with self._lock:
            node = await self._continue_from_unlocked(node_id, prompt)
            return self._activate_unlocked(node.id)

    async def _continue_from_unlocked(self, node_id: str, prompt: str) -> TreeNode:
        parent = self.tree.get_node(node_id)
        branches = await generate_branches(
            prompt, parent.context_summary, parent.user_preferences, self.llm, beam_width=1
        )
        if not branches:
            raise LLMGenerationFailed(f"LLM failed to generate a continuation for node {node_id}")
        branch = branches[0]
        node = TreeNode(
            id=self.tree.new_id(),
            parent_id=parent.id,
            children_ids=[],
            code_snapshot=branch.code,
            context_summary=_extend_summary(parent.context_summary, prompt, branch.reasoning),
            user_preferences=list(parent.user_preferences),
            score=None,
            score_source=None,
            status="normal",
            query_history=[],
            branch_label=f"Branch {branch_label(len(parent.children_ids))}",
        )
        self.tree.add_node(node)
        return self.tree.get_node(node.id)

    async def edit_prompt(self, node_id: str, new_prompt: str) -> Tuple[TreeNode, List[str]]:
        """Revise the prompt that produced `node_id`.

        TreeNode is append-only, so this never rewrites `node_id` itself: it
        generates a new sibling from the same parent using `new_prompt`, and
        marks `node_id` and everything built on top of it `stale` — they were
        generated against instructions that no longer hold. Nothing is
        deleted; the user decides whether to keep, discard, or regenerate
        the stale subtree.
        """
        async with self._lock:
            node = self.tree.get_node(node_id)
            if node.parent_id is None:
                raise ValueError("Cannot edit the root node's prompt")
            # Uses the unlocked helper directly: asyncio.Lock isn't
            # reentrant, and we're already holding it.
            new_node = await self._continue_from_unlocked(node.parent_id, new_prompt)
            stale_ids = [node_id] + [d.id for d in self.tree.get_descendants(node_id)]
            self.tree.mark_stale(stale_ids)
            return new_node, stale_ids

    async def backtrack(self, node_id: str) -> TreeNode:
        """Move the active cursor to any node.

        Nodes that are neither ancestors of `node_id`, `node_id` itself, nor
        its descendants are orphaned (dimmed, never deleted). Anything on the
        new path that was previously orphaned is revived.
        """
        async with self._lock:
            return self._activate_unlocked(node_id)

    def _activate_unlocked(self, node_id: str) -> TreeNode:
        keep = set(self.tree.path_to(node_id))
        keep.update(d.id for d in self.tree.get_descendants(node_id))
        for node in self.tree.all_nodes():
            if node.id in keep:
                self.tree.revive(node.id)
            elif node.status != "orphaned":
                self.tree.mark_orphaned(node.id)
        self.tree.set_active(node_id)
        return self.tree.get_node(node_id)

    def all_nodes(self) -> List[TreeNode]:
        return self.tree.all_nodes()


def _extend_summary(parent_summary: str, prompt: str, reasoning: str) -> str:
    step = f"Task: {prompt}\nApproach: {reasoning}"
    return f"{parent_summary}\n---\n{step}" if parent_summary else step
