import asyncio
import json
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from branch_generator import branch_label, generate_branches
from codebase_graph import codebase_context_for_prompt, list_known_files, match_known_file
from context_retriever import get_relevant_context
from git_engine import make_patch, merge_code
from models import DEFAULT_BEAM_WIDTH, Branch, PatchOutcome, QueryEntry, TreeNode
from tree_state import TreeStateManager

OnBranchReady = Callable[[Branch], Awaitable[None]]


def _resolve_target(
    intent: str, target_file: Optional[str], target_range: Optional[Dict[str, int]], branch: Branch,
) -> Tuple[Optional[str], Optional[Dict[str, int]]]:
    """Amendment (2026-10-17): decides the node's *actual* target_file/
    target_range from the requested `intent` and the model's own
    `is_code_change` classification -- the user's explicit intent always
    overrides the model's guess; "auto" defers to it.

    - "question": force no file, regardless of anything else.
    - "code_change": force the given target through, regardless of the
      model's classification -- an explicit override trusts the user.
    - "auto" (default): keep the given target only if the model classified
      this as a real code change; otherwise strip it, same as a plain
      question.
    """
    if intent == "question":
        return None, None
    if intent == "code_change":
        return target_file, target_range
    return (target_file, target_range) if branch.is_code_change else (None, None)


async def _maybe_redirect_target(
    llm_client,
    prompt: str,
    context_summary: str,
    user_preferences: List[str],
    codebase_context_full: Optional[str],
    candidate_target_file: Optional[str],
    include_codebase: bool,
    intent: str,
    branch: Branch,
) -> Tuple[Branch, Optional[str]]:
    """Phase 7 (2026-10-17): if the model declared a `target_file` different
    from the one it was actually shown content for, `branch.code` is
    untrusted for that file -- unseen content, same rule everywhere else in
    this codebase. Fires exactly one retry, re-scoped to the declared file,
    and returns `(that response, the matched file)` instead. Returns the
    original branch and `None` (no redirect) when there's nothing to
    redirect, or when the declared file isn't a real, known one
    (hallucinated path) -- conservative by default.
    """
    if not branch.target_file or not codebase_context_full or not include_codebase:
        return branch, None
    if not candidate_target_file:
        # No target_file was given at all -> codebase_context_for_prompt
        # never scopes down without one, so the model already saw the
        # FULL, unscoped snapshot (see its own docstring). Nothing was
        # hidden from it, so there's nothing to redirect to -- a retry
        # here would only narrow the context below what it already had.
        # Real bug, caught by code review (2026-10-17): this case used to
        # fall through to a retry whenever the model filled in a
        # `target_file` anyway (the prompt always asks for the field),
        # degrading a correctly-grounded answer into one scoped to a
        # single file, plus an unnecessary extra LLM call.
        return branch, None
    known = list_known_files(codebase_context_full)
    matched = match_known_file(branch.target_file, known)
    if matched is None:
        return branch, None  # hallucinated path -- don't trust it
    if match_known_file(candidate_target_file, known) == matched:
        return branch, None  # declared the same file it was already given -- no redirect
    retried = await generate_branches(
        prompt, context_summary, user_preferences, llm_client, beam_width=1,
        codebase_context=codebase_context_for_prompt(codebase_context_full, matched, include_codebase),
        intent=intent,
    )
    return (retried[0], matched) if retried else (branch, None)


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
        intent: str = "auto",
    ) -> List[Branch]:
        """Generate up to `beam_width` children of `node_id`. Never mutates existing code.

        `on_branch_ready`, if given, is awaited with each Branch (its
        `node_id` already set) the moment that branch's node is created —
        as each of the `beam_width` LLM calls completes, not after all of
        them do. Step 2.4's `branch.ready` WebSocket event is wired through
        this, not through a separate post-hoc loop.

        `target_file`/`target_range` (Phase 3): a *candidate* target for
        each node this call creates — whether it's actually kept depends
        on `intent` (2026-10-17 amendment): "code_change" forces it through,
        "question" forces it off, "auto" (default) keeps it only if the
        model classified its own response as a real code change. See
        `_resolve_target`.

        `include_codebase` (Phase 3, Step 3.7): every created node always
        *inherits* `parent.codebase_context` regardless of this flag — it's
        cheap, local, and future descendants should still have it available
        even if this particular turn opted out. What the flag controls is
        narrower: whether that inherited value is actually included in the
        prompt sent to the LLM for *this* call.
        """
        async with self._lock:
            return await self._branch_unlocked(
                node_id, prompt, beam_width, on_branch_ready, target_file, target_range, include_codebase, intent
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
        intent: str = "auto",
    ) -> List[Branch]:
        parent = self.tree.get_node(node_id)
        offset = len(parent.children_ids)
        results: List[Optional[Branch]] = [None] * beam_width

        async def on_result(i: int, b: Optional[Branch]) -> None:
            if b is None:
                return  # that call failed; already logged in branch_generator
            b, redirected_to = await _maybe_redirect_target(
                self.llm, prompt, parent.context_summary, parent.user_preferences,
                parent.codebase_context, target_file, include_codebase, intent, b,
            )
            effective_target_file = redirected_to or target_file
            effective_target_range = None if redirected_to else target_range
            node_target_file, node_target_range = _resolve_target(intent, effective_target_file, effective_target_range, b)
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
                target_file=node_target_file,
                target_range=node_target_range,
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
            codebase_context=codebase_context_for_prompt(parent.codebase_context, target_file, include_codebase),
            intent=intent,
        )
        return [b for b in results if b is not None]

    async def continue_from(
        self,
        node_id: str,
        prompt: str,
        target_file: Optional[str] = None,
        target_range: Optional[Dict[str, int]] = None,
        include_codebase: bool = True,
        intent: str = "auto",
    ) -> TreeNode:
        """Single LLM call, no branching: adds one new child of `node_id` and
        makes it the active node — a reply advances the conversation, the
        way any normal chat does. Without this, asking a second question
        from the same still-active node just fans out another sibling
        instead of continuing, which reads as unwanted branching.

        `edit_prompt` deliberately does NOT go through this: its new sibling
        stays inactive until the user chooses to make it active.

        `target_file`/`target_range` are a *candidate* target; `intent`
        (2026-10-17 amendment) decides whether it's actually kept — see
        `_resolve_target`.
        """
        async with self._lock:
            node = await self._continue_from_unlocked(
                node_id, prompt, target_file, target_range, include_codebase, intent
            )
            return self._activate_unlocked(node.id)

    async def _continue_from_unlocked(
        self,
        node_id: str,
        prompt: str,
        target_file: Optional[str] = None,
        target_range: Optional[Dict[str, int]] = None,
        include_codebase: bool = True,
        intent: str = "auto",
    ) -> TreeNode:
        parent = self.tree.get_node(node_id)
        branches = await generate_branches(
            prompt, parent.context_summary, parent.user_preferences, self.llm, beam_width=1,
            codebase_context=codebase_context_for_prompt(parent.codebase_context, target_file, include_codebase),
            intent=intent,
        )
        if not branches:
            raise LLMGenerationFailed(f"LLM failed to generate a continuation for node {node_id}")
        branch = branches[0]
        branch, redirected_to = await _maybe_redirect_target(
            self.llm, prompt, parent.context_summary, parent.user_preferences,
            parent.codebase_context, target_file, include_codebase, intent, branch,
        )
        effective_target_file = redirected_to or target_file
        effective_target_range = None if redirected_to else target_range
        node_target_file, node_target_range = _resolve_target(intent, effective_target_file, effective_target_range, branch)
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
            target_file=node_target_file,
            target_range=node_target_range,
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

    async def query(
        self, node_id: str, question: str, chain_from: Optional[str] = None
    ) -> QueryEntry:
        """Phase 4: ask `node_id` a question without touching the active
        node — the core value of a tree over a linear chat, querying a
        past decision doesn't require backtracking to it. Appends a
        `QueryEntry` to `node_id`'s own `query_history`; creates no new
        `TreeNode`, moves nothing.

        `chain_from`, if given, is the id of a prior `QueryEntry` on this
        SAME node — its question/answer is prepended so a follow-up is
        answered with that exchange already in view.
        """
        async with self._lock:
            return await self._query_unlocked(node_id, question, chain_from)

    async def _query_unlocked(
        self, node_id: str, question: str, chain_from: Optional[str] = None
    ) -> QueryEntry:
        node = self.tree.get_node(node_id)  # raises KeyError -> 404 at the API layer

        prior: Optional[QueryEntry] = None
        if chain_from is not None:
            prior = next((q for q in node.query_history if q.id == chain_from), None)
            if prior is None:
                raise KeyError(f"Unknown query entry: {chain_from}")

        context = get_relevant_context(self.tree, node_id)
        chained = f"Prior question: {prior.question}\nPrior answer: {prior.answer}\n\n" if prior else ""

        try:
            answer = await self.llm.complete(
                messages=[{"role": "user", "content": f"{chained}Question: {question}"}],
                system=context,
            )
        except Exception as e:
            # Fail-open (CLAUDE.md): the LLM call failing must never leave
            # a partial write -- no QueryEntry is appended, history is
            # exactly what it was before this call.
            raise LLMGenerationFailed(f"LLM failed to answer the query for node {node_id}: {e}")

        entry = QueryEntry(
            id=self.tree.new_id(),
            question=question,
            answer=answer,
            timestamp=datetime.utcnow(),
            chained_from=chain_from,
        )
        self.tree.add_query_entry(node_id, entry)
        return entry

    async def _incorporate_clarification(self, source: TreeNode, entry: QueryEntry) -> Branch:
        """Shared by `patch()` and `merge()`: one LLM call that revises
        `source`'s code to incorporate a clarification (a Q&A pair from its
        own `query_history`). Reuses `generate_branches` (beam_width=1) for
        its existing JSON {code, reasoning} parsing rather than a second,
        separately-parsed call shape.
        """
        prompt = (
            f"Revise this code to incorporate the following clarification.\n\n"
            f"Clarification Q: {entry.question}\nClarification A: {entry.answer}"
        )
        branches = await generate_branches(
            prompt, source.context_summary, source.user_preferences, self.llm, beam_width=1,
            codebase_context=codebase_context_for_prompt(source.codebase_context, source.target_file, True),
        )
        if not branches:
            raise LLMGenerationFailed(
                f"LLM failed to incorporate the clarification for node {source.id}"
            )
        return branches[0]

    def _find_query_entry(self, node: TreeNode, query_id: str) -> QueryEntry:
        entry = next((q for q in node.query_history if q.id == query_id), None)
        if entry is None:
            raise KeyError(f"Unknown query entry: {query_id}")
        return entry

    async def patch(
        self, source_node_id: str, query_id: str, target_node_id: str, force: bool = False
    ) -> PatchOutcome:
        """Phase 5.2, Option A: apply a clarification from `source_node_id`
        (a past node) onto `target_node_id` (typically the current/active
        node) — creates one new child of `target_node_id` with the
        clarification incorporated, and makes it active. `target_node_id`'s
        own history is otherwise untouched; `source_node_id`'s subtree is
        never touched at all.

        Always runs real conflict detection first (CLAUDE.md: non-
        negotiable for /patch, same as the spec's original /patch design).
        A conflict creates nothing — `PatchOutcome.conflict=True`,
        `node=None`, `diff_preview` carries the raw patch for the frontend's
        conflict banner (Step 5.4) — UNLESS `force=True`, which is that
        banner's "Apply anyway" action: bypass the merge and use the
        clarification's raw revised code directly, the same way the very
        first Phase 3 build always did (before conflict detection existed
        at all). Only ever reached after a real conflict was already shown
        to the user on a prior call — never the default.
        """
        async with self._lock:
            return await self._patch_unlocked(source_node_id, query_id, target_node_id, force)

    async def _patch_unlocked(
        self, source_node_id: str, query_id: str, target_node_id: str, force: bool = False
    ) -> PatchOutcome:
        source = self.tree.get_node(source_node_id)
        target = self.tree.get_node(target_node_id)
        entry = self._find_query_entry(source, query_id)

        clarified = await self._incorporate_clarification(source, entry)
        patch_text = make_patch(source.code_snapshot, clarified.code)
        result = merge_code(target.code_snapshot, patch_text)

        if not result.success and not force:
            return PatchOutcome(conflict=True, node=None, diff_preview=result.diff_preview)

        merged_code = result.merged_code if result.success else clarified.code

        node = TreeNode(
            id=self.tree.new_id(),
            parent_id=target.id,
            children_ids=[],
            code_snapshot=merged_code,
            context_summary=_extend_summary(
                target.context_summary,
                f"Clarification from {source.id}: {entry.question}",
                clarified.reasoning,
            ),
            user_preferences=list(target.user_preferences),
            score=None,
            score_source=None,
            status="normal",
            query_history=[],
            branch_label=f"Branch {branch_label(len(target.children_ids))}",
            target_file=target.target_file,
            target_range=target.target_range,
            codebase_context=target.codebase_context,
        )
        self.tree.add_node(node)
        self._activate_unlocked(node.id)
        return PatchOutcome(conflict=False, node=self.tree.get_node(node.id), diff_preview=None)

    async def _extract_preferences(self, path_context: str) -> List[str]:
        """Phase 5.3's own spec: "One LLM call extracts user preferences
        along the path." Asks for a short JSON list of concrete, reusable
        preferences/decisions implied by the given context (not prose).
        Fails open to an empty list on any LLM/parse failure — a merge
        with nothing extracted still proceeds with just `source`'s own
        existing preferences, rather than blocking Option C entirely on
        this one step (this call is informational input to the real code
        revision that follows, not load-bearing the way `/continue`'s
        single call is).
        """
        if not path_context.strip():
            return []
        prompt = (
            "From the following conversation history, extract a short JSON "
            'list of concrete, reusable preferences or decisions the user '
            'expressed (e.g. ["use type hints", "no external libraries"]). '
            "Return ONLY the JSON list, nothing else — no prose, no markdown fences.\n\n"
            f"{path_context}"
        )
        try:
            response = await self.llm.complete(messages=[{"role": "user", "content": prompt}], system="")
            data = json.loads(response.strip())
            if isinstance(data, list):
                return [str(p) for p in data]
        except Exception:
            pass
        return []

    async def merge(self, source_node_id: str, current_node_id: str) -> PatchOutcome:
        """Phase 5.3, Option C: branch fresh from `source_node_id` (a past
        node) with the PREFERENCES accumulated along the path from there
        down to `current_node_id` folded in — not a specific query
        clarification (that's Option A/`patch()`'s job), but whatever
        general decisions/constraints were established after diverging
        from `source_node_id` (e.g. "we started using type hints"). A new
        child OF `source_node_id`, not of `current_node_id`; the path that
        led to `current_node_id` is untouched in content (still there,
        just orphaned/dimmed by the activation switch — the exact same
        semantics every other `_activate_unlocked` call in this codebase
        already uses; nothing is ever deleted, and backtracking there
        again revives it). This is explicitly Option C, not Option B
        (patch-and-propagate forward through descendants), which is out of
        scope per CLAUDE.md's Scope discipline.

        No conflict detection here — unlike `patch()`, there's no
        divergent target to reconcile against; we're revising
        `source_node_id`'s own code with preferences distilled from its
        own descendant path and adding the result as its own child.
        `PatchOutcome.conflict` is always False.
        """
        async with self._lock:
            return await self._merge_unlocked(source_node_id, current_node_id)

    async def _merge_unlocked(self, source_node_id: str, current_node_id: str) -> PatchOutcome:
        source = self.tree.get_node(source_node_id)
        current = self.tree.get_node(current_node_id)  # raises KeyError -> 404 if unknown

        full_path = self.tree.path_to(current.id)  # root-first, includes current.id
        if source.id not in full_path:
            raise ValueError(f"{source.id} is not an ancestor of {current.id}")
        diverged_ids = full_path[full_path.index(source.id) + 1:]  # excludes source itself
        diverged_nodes = [self.tree.get_node(nid) for nid in diverged_ids]

        path_context = "\n---\n".join(n.context_summary for n in diverged_nodes if n.context_summary)
        extracted = await self._extract_preferences(path_context)
        # Dedup while preserving order — source's own preferences first,
        # then newly-extracted ones not already present.
        combined_preferences = list(source.user_preferences) + [
            p for p in extracted if p not in source.user_preferences
        ]

        branches = await generate_branches(
            "Revise this code to incorporate the preferences accumulated in downstream work.",
            source.context_summary, combined_preferences, self.llm, beam_width=1,
            codebase_context=codebase_context_for_prompt(source.codebase_context, source.target_file, True),
        )
        if not branches:
            raise LLMGenerationFailed(f"LLM failed to merge preferences into node {source_node_id}")
        revised = branches[0]

        node = TreeNode(
            id=self.tree.new_id(),
            parent_id=source.id,
            children_ids=[],
            code_snapshot=revised.code,
            context_summary=_extend_summary(
                source.context_summary,
                f"Merge: preferences from the path to {current.id}",
                revised.reasoning,
            ),
            user_preferences=combined_preferences,
            score=None,
            score_source=None,
            status="normal",
            query_history=[],
            branch_label=f"Branch {branch_label(len(source.children_ids))}",
            target_file=source.target_file,
            target_range=source.target_range,
            codebase_context=source.codebase_context,
        )
        self.tree.add_node(node)
        self._activate_unlocked(node.id)
        return PatchOutcome(conflict=False, node=self.tree.get_node(node.id), diff_preview=None)

    async def merge_siblings(self, node_a_id: str, node_b_id: str) -> TreeNode:
        """Phase 6.2: combine two sibling branches (two children of the same
        parent, typically from the same `/branch` fan-out) into one new
        node that synthesizes both approaches.

        Deliberately distinct from `patch()`/`merge()` above (Option A/C,
        both ancestor-descendant relationships with an existing conflict-
        detection or preference-extraction story) -- two siblings have no
        shared downstream history and no git-mergeable relationship to each
        other (they're independent attempts at the same prompt, not a base
        and a revision of it), so there's nothing for `git_engine.merge_code`
        to meaningfully diff. This is "LLM context synthesis" (the shape
        CLAUDE.md's deferred scope note already named): one call sees both
        branches' code and reasoning side by side and produces a single,
        coherent solution that combines what's good about each -- not a
        mechanical text merge.

        Always creates a new child of the shared parent (never of either
        sibling) and activates it, the same "new node, new active cursor"
        pattern every other merge/patch/continue call in this file uses.
        """
        async with self._lock:
            return await self._merge_siblings_unlocked(node_a_id, node_b_id)

    async def _merge_siblings_unlocked(self, node_a_id: str, node_b_id: str) -> TreeNode:
        a = self.tree.get_node(node_a_id)  # raises KeyError -> 404 if unknown
        b = self.tree.get_node(node_b_id)
        if node_a_id == node_b_id:
            raise ValueError("Cannot merge a node with itself")
        if a.parent_id is None or a.parent_id != b.parent_id:
            raise ValueError(f"{node_a_id} and {node_b_id} are not siblings (same parent required)")
        parent = self.tree.get_node(a.parent_id)

        prompt = (
            "Two alternative solutions were generated for the same task. "
            "Synthesize ONE combined solution that takes the best aspects of "
            "both -- do not just pick one; genuinely combine their strengths.\n\n"
            f"Approach A ({a.branch_label}):\n{a.code_snapshot}\n\n"
            f"Approach B ({b.branch_label}):\n{b.code_snapshot}"
        )
        branches = await generate_branches(
            prompt, parent.context_summary, parent.user_preferences, self.llm, beam_width=1,
            codebase_context=codebase_context_for_prompt(parent.codebase_context, parent.target_file, True),
        )
        if not branches:
            raise LLMGenerationFailed(f"LLM failed to synthesize a merge of {node_a_id} and {node_b_id}")
        synthesized = branches[0]

        node = TreeNode(
            id=self.tree.new_id(),
            parent_id=parent.id,
            children_ids=[],
            code_snapshot=synthesized.code,
            context_summary=_extend_summary(
                parent.context_summary,
                f"Merge of sibling branches {a.branch_label} and {b.branch_label}",
                synthesized.reasoning,
            ),
            user_preferences=list(parent.user_preferences),
            score=None,
            score_source=None,
            status="normal",
            query_history=[],
            branch_label=f"Branch {branch_label(len(parent.children_ids))}",
            target_file=parent.target_file,
            target_range=parent.target_range,
            codebase_context=parent.codebase_context,
        )
        self.tree.add_node(node)
        self._activate_unlocked(node.id)
        return self.tree.get_node(node.id)

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
