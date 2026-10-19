# CLAUDE.md

Guidance for Claude Code when working in this repository. This file governs *how* to implement Branch-Out. All architectural decisions are final — this file is about implementation discipline, not redesign.

---

## What this project is

Branch-Out is a full-stack coding agent that makes AI coding sessions a tree instead of a linear conversation. Users can branch into multiple approaches at any point, backtrack to any past node, and query past decisions without losing current state.

**Stack:** React (frontend) · Python / FastAPI (backend) · Claude API (`claude-sonnet-4-6`) · networkx (context graph) · SQLite (session persistence) · gitpython (code merge) · TypeScript / VS Code Extension API (IDE integration, Phase 3 — decided 2026-09-26, see Scope discipline below)

**Portfolio context:** This is a solo portfolio project. Every engineering decision has a reason — understand it before changing it.

---

## Working style

- **Ask before assuming.** If a request doesn't map cleanly onto something decided in `ROADMAP.md` or this file, ask rather than guess. Wrong assumptions in the tree state manager or evaluator are expensive to unwind.
- **Push back on conflicts.** If an instruction would violate a non-negotiable constraint below, say so and explain the conflict instead of quietly complying. The decisions are final on *what* to build; that doesn't mean every instruction about *how* is automatically compatible.
- **Plan before touching code.** Before editing, state which module is affected, which existing tests interact with it, and which phase it belongs to. If the plan jumps ahead of an uncleared phase gate, flag it before writing code.
- **Re-evaluate after the change.** Once a change is made, check it back against this file: append-only node store preserved, LLMClient abstraction intact, parallel generation still async, evaluation still async, no node mutation. Don't mark work done on "it runs" alone.
- **Never build two phases simultaneously.** Complete and verify Phase N before touching Phase N+1.

---

## Non-negotiable constraints

- **TreeNode is append-only.** Never mutate an existing node. Patches, clarifications, and branches always create new child nodes. A node written once is immutable forever.
- **LLMClient is always abstract.** The Anthropic SDK is only ever called inside `claude_client.py`. Nothing else in the codebase imports `anthropic` directly. This is the model-agnostic interface — respect it.
- **Branch generation is always parallel.** `asyncio.gather()` only — never a sequential loop over LLM calls. This is load-bearing for UX and the benchmark story.
- **Evaluation is always async.** Branches appear in the UI before scores. The evaluator fires after branch generation completes, not before. Never block the branch response on evaluation.
- **Context reconstruction uses selective graph retrieval only.** Never replay the full message history to reconstruct a past node's context. The networkx subgraph walk is the only permitted reconstruction path.
- **Session persistence is on session end only.** No continuous writes to SQLite during a session. One serialisation call when the session closes.
- **Beam width default is 3.** Configurable via request body only — never hardcoded in more than one place.
- **The `POST /patch` endpoint always runs conflict detection.** Never skip it, even when the caller claims there is no conflict.

---

## Fail-open discipline

Every component that can fail must fail open — degrade gracefully, never crash the session:

1. **LLM call failure** → log at error level, return the successfully generated branches (even if fewer than beam width). Never fail all branches because one failed.
2. **Evaluator failure** → log at error level, mark the node's score as `None` with `score_source=None`. The node is still usable — evaluation is informational, not load-bearing.
3. **Test runner failure** (subprocess crash, syntax error in code) → fall back to LLM judge immediately. Log the test failure reason.
4. **Context retrieval failure** → fall back to the node's stored `context_summary` string. Never fail a query because the graph walk failed.
5. **Git merge failure** → surface the conflict to the user. Never silently apply a broken merge.
6. **Session serialisation failure** → log at error level, tell the user. Never silently lose a session.

Every fail-open path requires a test proving the system continues functioning after the failure.

---

## Conservative-by-default

Wherever there is a choice between aggressive and safe:

- **Conflict ambiguous?** → Always fire `conflict.detected`, never silently apply.
- **Test coverage low?** → Fall back to LLM judge, not partial test scores.
- **Context graph walk produces empty subgraph?** → Use `context_summary`, never an empty context.
- **Branch generation partially failed?** → Return what succeeded, never block on the failed branch.
- **Score normalisation call failed?** → Leave all scores as `None`, never guess a ranking.
- **Git merge produced conflicts?** → Block and surface, never auto-resolve.

---

## Append-only node discipline

This is the most important invariant in the entire codebase:

```python
# CORRECT — always
new_node = TreeNode(parent_id=current_node.id, ...)
tree.add_node(new_node)

# WRONG — never
current_node.code_snapshot = new_code  # mutating an existing node
current_node.status = "active"          # mutating an existing node
```

The only field that may be updated on an existing node is `status` (active / stale / orphaned / normal) and `children_ids` (when a new child is added). Everything else is immutable after creation.

---

## LLM client discipline

```python
# CORRECT — the only permitted pattern
from claude_client import ClaudeClient
client = ClaudeClient()
response = await client.complete(messages, system)

# WRONG — never do this anywhere except claude_client.py
import anthropic
client = anthropic.Anthropic()
```

If a new LLM call type is needed (streaming, tool use, etc.), extend `LLMClient`'s abstract interface first, then implement in `ClaudeClient`. Never add Anthropic SDK calls outside `claude_client.py`.

**Deviation on record:** `claude_client.py` uses `anthropic.AsyncAnthropic`, not the sync `anthropic.Anthropic` shown in early spec snippets. The sync client blocks the event loop and would serialise `asyncio.gather()` calls, which violates the async-generation constraint above and the concurrency tests it requires. This was flagged and treated as a spec conflict this file itself resolves in favor of the async constraint.

---

## Async discipline

Branch generation must use `asyncio.gather()`:

```python
# CORRECT
branches = await asyncio.gather(*[
    _generate_single_branch(prompt, context, client, i)
    for i in range(beam_width)
], return_exceptions=True)

# WRONG — sequential, defeats the point
branches = []
for i in range(beam_width):
    branch = await _generate_single_branch(...)
    branches.append(branch)
```

FastAPI endpoints that call branch generation must be `async def`. WebSocket events must fire as each branch completes, not after all have completed. Use `return_exceptions=True` in `asyncio.gather()` and handle exceptions per-branch (see fail-open discipline).

---

## Evaluation discipline

Evaluation fires async after branch generation — branches appear in the UI first:

```python
# CORRECT ordering in the orchestrator
branches = await generate_branches(...)                    # branches sent to UI immediately
asyncio.create_task(evaluate_and_notify(branches))          # evaluation fires in background

# WRONG — blocks UI on evaluation
branches = await generate_branches(...)
scores = await evaluate_branches(branches)                  # user waits for scores before seeing branches
```

Retroactive re-scoring: when tests are first added to a session (detected by the presence of `import unittest` or `import pytest` in a node's code that didn't appear in any ancestor), trigger `asyncio.create_task(retroactive_rescore(session))` immediately. This re-runs the test runner on all ancestor nodes that have scores from the LLM judge.

---

## WebSocket event discipline

Every WebSocket event must fire the moment its data is available — not batched:

| Event | When to fire |
|---|---|
| `branch.ready` | The moment one branch's LLM call completes — not when all N complete |
| `score.ready` | The moment the evaluator finishes ranking — after all branches complete |
| `conflict.detected` | The moment conflict detection runs in `POST /patch` |
| `node.stale` | The moment a node's status is set to `stale` |

**Deviation on record (2026-10-19, caught by a second code-review pass — the first pass only left a local source comment, which this file's own precedent above requires be promoted here instead):** Phase 7's `orchestrator._maybe_redirect_target` can add a second, sequential LLM call for ONE branch in a fan-out (when that branch's response declares a redirect to a different target_file) — `on_branch_ready`, and therefore `branch.ready`, waits on it. This delays that one branch's event past "the moment its first LLM call completes," which is otherwise unconditional. Flagged and treated as a spec conflict this file resolves in favor of correctness: firing `branch.ready` before the redirect resolves would mean announcing a branch as ready with code that's still unverified for any real file — exactly what the redirect mechanism exists to prevent (CLAUDE.md's own fail-open discipline: "conflict ambiguous? never silently apply"). Only a redirecting branch pays this cost; every branch that doesn't redirect still fires on its own first call's completion, unaffected.

---

## Context graph discipline

The networkx DiGraph is the source of truth for context reconstruction. Each node in the graph stores:

- `decisions`: list of key decisions made
- `code_diff`: delta from parent node's code
- `summary`: compressed summary of conversation up to this node
- `preferences`: user-stated constraints (e.g. "use Python 3.11", "no external libraries")

When reconstructing a past node's context for a query:

1. Walk ancestors via `nx.ancestors(graph, node_id)`
2. Pull `decisions`, `summary`, `preferences` from each ancestor node
3. Compose a structured context — never replay raw message history
4. If the walk fails or produces an empty graph, fall back to `TreeNode.context_summary`

The graph is in-memory during a session and serialised as part of `session.py`'s JSON output.

---

## Testing requirements

- Every new module requires its own test file before it is considered done.
- Every fail-open path requires a deliberately-broken test proving the system continues.
- Every WebSocket event requires an integration test proving it fires at the right moment.
- The append-only invariant requires a test: create a node, attempt to mutate it, assert the mutation raises or is ignored.
- The LLM abstraction requires a test: assert no file except `claude_client.py` imports `anthropic`.
- The parallel generation requirement requires a test: mock the LLM client, assert all N calls are fired concurrently (timing-based or using `asyncio` inspection).
- Tests use `pytest` for async tests via `pytest-asyncio`, or plain `asyncio.run()` wrappers where that's simpler for a synchronous test body. No blocking synchronous SDK calls inside async code.

---

## Build order

Follow ROADMAP.md exactly. The phase gates are real — do not start Phase N+1 before Phase N's milestone is verified working end-to-end:

**Phase 1 (core tree)** → **Phase 2 (evaluator)** → **Phase 3 (VS Code extension)**, which only needs Phases 1–2's endpoints → **Phase 4 (cross-node querying)** can be built independently of Phase 3, any time after Phase 2 → **Phase 5 (patches)** requires both 2 and 4, and upgrades Phase 3's naive file-apply to use its conflict-detection machinery → **Phase 6 (benchmark + polish)** requires all.

Phase 3 was inserted ahead of the original Phase 3 (cross-node querying, now Phase 4) on 2026-09-26 — see ROADMAP.md's Phase 3 section for the reasoning. It only depends on already-built Phase 1/2 endpoints, so it doesn't block on anything later in the sequence.

The specific build order within each phase is in ROADMAP.md. Don't reshuffle it.

---

## Incremental runnability

The project must be demoable against the real Claude API from Phase 1 onward:

- Phase 1 milestone: a working branching session in the browser — branch, pick, backtrack, save, reload.
- Phase 2 milestone: scores populate async after branching. Retroactive re-scoring fires when tests are added.
- Phase 3 milestone: inside VS Code, select code (or place cursor), ask a question, pick from a quick-pick of approaches (or open the full tree panel), watch the result land in the real file.
- Phase 4 milestone: click a past node, ask it a question, get a coherent answer, current node untouched.
- Phase 5 milestone: clarify from a past node, apply to current (Option A), and spawn a new branch (Option C) — plus Phase 3's file-apply upgraded to use the same conflict detection.
- Phase 6 milestone: benchmark runner produces real numbers comparing linear baseline vs. Branch-Out.

Never let the project go dark — it should be runnable at every phase boundary.

---

## Scope discipline

**Out of scope — do not build:**
- Option B (patch any node and propagate forward through descendants)
- Multi-user collaboration
- Cloud deployment or hosting
- JetBrains or any non-VS-Code IDE integration (VS Code was the explicit choice — see Phase 3)
- Auto-branching classifier (proactive branch suggestions)
- Any MCP server wrapper until Phase 6 is complete

**Explicitly deferred to future scope only:**
- Model-agnostic benchmarking (swap `ClaudeClient` for GPT-4 implementation)

**Decision on record (2026-10-18): branch merge is no longer deferred.** Built in Phase 6.2 as `Orchestrator.merge_siblings` — LLM context synthesis of two sibling branches (same parent) into one new node, not a git merge (two independent attempts at the same prompt have no shared history for `git_engine` to diff against). See ROADMAP.md's Phase 6.2.

**Decision on record (2026-09-26): VS Code / IDE extension is no longer out of scope.** It was on this list; the user explicitly asked for it (Branch-Out driven from inside the IDE, Copilot-like, applying chosen branches to real files) and it's now a real, scheduled phase — see ROADMAP.md's Phase 3. Removing it from "do not build" here rather than leaving a stale rule that contradicts a phase that actually exists in the roadmap.

Do not scope-creep. If a feature is not in ROADMAP.md's current phase, do not build it.

---

## Environment

```
ANTHROPIC_API_KEY=your_key_here
```

Required for all LLM calls. The test suite must not require this key — mock `ClaudeClient` in all unit tests.
