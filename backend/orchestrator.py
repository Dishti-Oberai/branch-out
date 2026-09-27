import asyncio
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from branch_generator import branch_label, generate_branches
from codebase_graph import select_relevant_subset
from models import DEFAULT_BEAM_WIDTH, Branch, TreeNode
from tree_state import TreeStateManager

# How many import-graph hops out from `target_file` to include (2026-09-27
# follow-up to the Phase 1 amendment). 1 = target_file plus files it
# directly imports or is imported by. Not exposed as an API parameter --
# not a decision worth a whole request-schema field yet; a fixed, documented
# constant is enough for a POC and easy to bump later if it proves too
# narrow in practice.
_CODEBASE_GRAPH_DEPTH = 1


def _codebase_context_for_prompt(
    codebase_context: Optional[str], target_file: Optional[str], include_codebase: bool
) -> Optional[str]:
    """Decides what codebase context (if any) actually goes in the LLM
    prompt for one call. The *node's own* `codebase_context` (the full
    snapshot) is untouched by this -- it's still what gets inherited by
    children regardless of what any single call chose to send.

    - `include_codebase=False` -> None, always (existing behavior).
    - No `target_file` -> the full snapshot (existing behavior) -- nothing
      to scope a subgraph around (e.g. a tree-panel question with no file
      focus).
    - `target_file` set -> a relevance-scoped subset (target_file plus its
      import-graph neighbors) instead of the whole codebase. Fails open to
      the full snapshot if `target_file` isn't found in it (outside the
      workspace, excluded as binary/oversized, etc.) rather than silently
      sending nothing.
    """
    if not include_codebase:
        return None
    if not codebase_context or not target_file:
        return codebase_context
    scoped = select_relevant_subset(codebase_context, target_file, depth=_CODEBASE_GRAPH_DEPTH)
    return scoped if scoped is not None else codebase_context

OnBranchReady = Callable[[Branch], Awaitable[None]]


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
        on_branch_ready: Optional[OnBranchReady] = None,
        target_file: Optional[str] = None,
        target_range: Optional[Dict[str, int]] = None,
        include_codebase: bool = True,
    ) -> List[Branch]:
        """Generate up to `beam_width` children of `node_id`. Never mutates existing code.

        `on_branch_ready`, if given, is awaited with each Branch (its
        `node_id` already set) the moment that branch's node is created —
        as each of the `beam_width` LLM calls completes, not after all of
        them do. Step 2.4's `branch.ready` WebSocket event is wired through
        this, not through a separate post-hoc loop.

        `target_file`/`target_range` (Phase 3): set on every node this call
        creates. The web UI never passes these (stays None); the VS Code
        extension always does.

        `include_codebase` (Phase 3, Step 3.7): every created node always
        *inherits* `parent.codebase_context` regardless of this flag — it's
        cheap, local, and future descendants should still have it available
        even if this particular turn opted out. What the flag controls is
        narrower: whether that inherited value is actually included in the
        prompt sent to the LLM for *this* call.
        """
        async with self._lock:
            return await self._branch_unlocked(
                node_id, prompt, beam_width, on_branch_ready, target_file, target_range, include_codebase
            )

    async def _branch_unlocked(
        self,
        node_id: str,
        prompt: str,
        beam_width: int,
        on_branch_ready: Optional[OnBranchReady] = None,
        target_file: Optional[str] = None,
        target_range: Optional[Dict[str, int]] = None,
        include_codebase: bool = True,
    ) -> List[Branch]:
        parent = self.tree.get_node(node_id)
        offset = len(parent.children_ids)
        results: List[Optional[Branch]] = [None] * beam_width

        async def on_result(i: int, b: Optional[Branch]) -> None:
            if b is None:
                return  # that call failed; already logged in branch_generator
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
                target_file=target_file,
                target_range=target_range,
                codebase_context=parent.codebase_context,
            )
            self.tree.add_node(node)
            b.node_id = node.id
            results[i] = b
            if on_branch_ready:
                await on_branch_ready(b)

        await generate_branches(
            prompt, parent.context_summary, parent.user_preferences, self.llm, beam_width,
            on_result=on_result,
            codebase_context=_codebase_context_for_prompt(parent.codebase_context, target_file, include_codebase),
        )
        return [b for b in results if b is not None]

    async def continue_from(
        self,
        node_id: str,
        prompt: str,
        target_file: Optional[str] = None,
        target_range: Optional[Dict[str, int]] = None,
        include_codebase: bool = True,
    ) -> TreeNode:
        """Single LLM call, no branching: adds one new child of `node_id` and
        makes it the active node — a reply advances the conversation, the
        way any normal chat does. Without this, asking a second question
        from the same still-active node just fans out another sibling
        instead of continuing, which reads as unwanted branching.

        `edit_prompt` deliberately does NOT go through this: its new sibling
        stays inactive until the user chooses to make it active.
        """
        async with self._lock:
            node = await self._continue_from_unlocked(node_id, prompt, target_file, target_range, include_codebase)
            return self._activate_unlocked(node.id)

    async def _continue_from_unlocked(
        self,
        node_id: str,
        prompt: str,
        target_file: Optional[str] = None,
        target_range: Optional[Dict[str, int]] = None,
        include_codebase: bool = True,
    ) -> TreeNode:
        parent = self.tree.get_node(node_id)
        branches = await generate_branches(
            prompt, parent.context_summary, parent.user_preferences, self.llm, beam_width=1,
            codebase_context=_codebase_context_for_prompt(parent.codebase_context, target_file, include_codebase),
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
            target_file=target_file,
            target_range=target_range,
            codebase_context=parent.codebase_context,
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
