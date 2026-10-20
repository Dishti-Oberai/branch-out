# Branch-Out Roadmap

Checklist of phases, steps and substeps — what was implemented, not the process of getting there. `[x]` done, `[ ]` to do, `[~]` built but differs from the original spec.

---

## Phase 1 — Core tree (complete)

**1.1 Data models** (`models.py`) — `QueryEntry`, `TreeNode`, `Branch` dataclasses.

**1.2 LLM abstraction** (`llm_client.py`, `claude_client.py`) — `LLMClient` ABC + `ClaudeClient`, using `AsyncAnthropic` (async, not sync — required for parallel branch generation). `anthropic` imported only in this file.

**1.3 Tree state manager** (`tree_state.py`) — `TreeStateManager`: `add_node`, `get_node`, `get_children`, `get_ancestors`, `get_descendants`, `set_active`, `mark_stale`, `mark_orphaned`, `revive`, `all_nodes`. Accessors return deep copies — append-only enforced at the store layer.

**1.4 Branch generator** (`branch_generator.py`) — `generate_branches()`, parallel via `asyncio.gather`; a failed branch is dropped, the rest still return. `branch_label()` assigns "Branch A/B/C...".

**1.5 Orchestrator** (`orchestrator.py`) — `branch`, `backtrack`, `continue_from`. Backtrack orphans everything off the new active path and revives anything on it that was orphaned; nothing is ever deleted.

**1.6 Session persistence** (`session.py`) — save/load JSON round trip, keyed `nodes`.

**1.7 FastAPI backend** (`main.py`) — `/branch`, `/backtrack`, `/continue`, `/session`, `/session/save`, `/session/load`, `/ws`.

**1.8 React frontend** — `TreeCanvas`, `StatusBar`, `NodeInspector`, prompt bar (Ask → `/continue`, Explore 3 approaches → `/branch`), orphaned-node dimming, empty-state UI (loading / unreachable-backend messages).

**1.9 Codebase-aware context** — `TreeNode.codebase_context: Optional[str]`, inherited parent→child, included in the LLM prompt as its own labeled section. `include_codebase: bool` controls per-call inclusion without affecting what a node inherits.

**1.10 Cancellation** — `AbortController` end to end; backend's `_run_cancelable` polls for client disconnect and cancels the in-flight orchestrator call, propagating into `asyncio.gather`.

**1.11 Concurrency guardrail** — a single `asyncio.Lock` on the `Orchestrator`, held by every tree-mutating call.

**Tokenetics integration** — `claude_client.py` wraps every completion through Tokenetics' Tier 0 API (`prepare()`/`finalize()`), run via `asyncio.to_thread` to stay non-blocking.

---

## Phase 2 — Hybrid evaluator (complete)

**2.1 Test runner** (`test_runner.py`) — `run_tests(code)` in a subprocess; never raises (syntax error/crash/timeout all score `0.0`).

**2.2 Score normaliser** (`score_normaliser.py`) — `rank_branches`: one LLM call ranks all branches at once; any failure leaves rank/score `None`.

**2.3 Hybrid evaluator** (`evaluator.py`) — test-gate when any branch has test imports, else LLM judge. `rescore_with_tests()` for retroactive rescoring.

**2.4 WebSocket wiring** — `branch.ready` fires per branch as each completes; `score.ready` fires per node after async evaluation. `/session/load` triggers retroactive rescoring where test imports were added since the original score. Frontend: `useWebSocket.ts`, `pendingScoreIds` tracking, "evaluating…" state.

**2.5 Benchmark runner** (`benchmark/runner.py`) — `--baseline-generic`, `--baseline-matched`, `--branchout`, `--dry-run`; scores against each task's own `test_suite`; reports per-mode-per-category, never a flat number. 15 tasks (10 standard + 5 custom) written and individually verified against real reference solutions — later expanded (see Phase 6.1).

---

## Phase 3 — VS Code extension (complete)

Interaction model: a full embedded tree panel (webview hosting the same React app) + a lightweight inline quick-pick command. Complements the standalone web app rather than replacing it — both talk to the same local backend.

**3.1 Schema** — `TreeNode.target_file`/`target_range`.

**3.2 Backend: apply-to-file** — `POST /apply-to-file`, `POST /workspace` (declares the workspace root); path-safety check refuses anything outside it. `/branch`/`/continue` accept `target_file`/`target_range`.

**3.3 Extension shell** (`vscode-extension/`) — `src/api.ts` (typed HTTP client), **"Branch-Out: Open Tree Panel"** command (webview hosting `frontend/dist`).

**3.4 Inline quick-pick** — **"Branch-Out: Ask about selection"**: reads active file/selection, quick-pick of "Just answer" / "Explore 3 approaches", applies the chosen result.

**3.5 Webview ↔ extension bridge** — `vscodeBridge.ts`'s `isVsCodeWebview()`; `NodeInspector`'s "Apply to file" action, shown only inside the webview for nodes with a `target_file`.

**3.6 Backend: gather codebase context** (`codebase_context.py`) — `gather_codebase_context(root)`: respects `.gitignore`, skips build/VCS dirs and binaries, caps file/total size.

**3.7 Per-request choice + refresh** — `include_codebase: bool` on `/branch`/`/continue`; calling `/workspace` again refreshes root's codebase context for new nodes going forward.

**3.8 Real content in quick-pick prompts** — `askAboutSelection()` reads real selected/file text via `editor.document.getText()` and folds it into the prompt. Added a context-scope choice ("Just this file" / "Include the whole codebase").

**3.9 Apply-to-file safety net** — real conflict detection via `git_engine.merge_code` (Phase 5.1) on apply, instead of a blind overwrite.

**Codebase-context token efficiency** — moved into `system` (the only cacheable field); `claude_client.py` tracks `cache_usage_history` so Tokenetics' cache-breakpoint optimizer can anchor on it.

**Graph-based context scoping** (`codebase_graph.py`) — `select_relevant_subset(snapshot, target_file, depth)`: a static import/dependency graph (Python via `ast`, JS/TS via regex) scopes the prompt to a target file's neighbors instead of the whole codebase. `codebase_context_for_prompt()` is the shared entry point, reused by every call site that needs it.

**Question-mode safety** — question-type nodes never set `target_file`/`target_range` (so they can never be applied) and the model is instructed to leave `code` unchanged; `/apply-to-file` splices into just a node's `target_range` instead of overwriting the whole file when the node came from a selection.

---

## Phase 4 — Cross-node querying (complete)

**4.1 Context retriever** (`context_retriever.py`) — a real `networkx` `DiGraph`, one node per `TreeNode` with `decisions`/`code_diff`/`summary`/`preferences`/`codebase_context` attributes; `get_relevant_context()` walks ancestors via `nx.ancestors()`, falls back to `context_summary` on any failure. Rebuilt fresh from the tree on every call (not separately persisted).

**4.2 `POST /query`** — `Orchestrator.query(node_id, question, chain_from=None)`: answers from the queried node's own context without touching the active node; appends a `QueryEntry` via `TreeStateManager.add_query_entry()`.

**4.3 Frontend wiring** — `NodeInspector`'s Queries/State tab switcher (opens on Queries by default); query submission runs outside the global `busy` flag so it never blocks the active node; follow-ups auto-chain off the most recent entry.

---

## Phase 5 — Clarify-and-apply and clarify-and-branch (complete)

**5.1 Git engine** (`git_engine.py`) — `make_patch(original, revised)` / `merge_code(base, patch)` via a real temporary `git apply`; conflict → `success=False` + `diff_preview`, never raises.

**5.2 `POST /patch` (Option A)** — `Orchestrator.patch(source_node_id, query_id, target_node_id, force=False)`: incorporates a clarification into revised code, merges it onto the target's current code. Always runs conflict detection; `force=True` is the conflict banner's "Apply anyway."

**5.3 `POST /merge` (Option C)** — `Orchestrator.merge(source_node_id, current_node_id)`: extracts preferences from the path between the two nodes, folds them into a fresh revision of `source`'s code as its new child. No conflict detection (nothing divergent to reconcile against).

**5.4 Conflict banner** — inline in the Queries tab, scoped to the triggering `QueryEntry`; "Apply to current" / "Spawn branch from here" per entry; conflict shows the raw diff with Apply anyway / Cancel / Spawn branch.

**5.5 Upgraded file-apply** — `_source_snapshots` (full original content, not just a hash) lets `/apply-to-file` attempt a real merge against independent on-disk changes instead of blindly refusing.

**Query-entry actionability** (Phase 9, folded in here) — `QueryEntry.actionable: bool`, self-classified by the model; "Apply to current"/"Spawn branch" only shown for actionable (not purely informational) answers.

**Clarification file-awareness** (Phase 9.1, folded in here) — `_incorporate_clarification` checks the model's declared target file against the node actually being patched; a genuine mismatch raises `ClarificationFileMismatch` (400) instead of producing a nonsense diff.

**Spawn-branch guard** — `hasSpawnedBranch`/`appliedQueryIds` guards prevent accidental repeat-firing of `/merge`/`/patch` from the same session.

---

## Phase 5.6 — File-grounded tree, shared live session (amends Phase 3 and 5; complete)

Decisions: intent (not client) decides file-linking; activation (not a separate Apply button) is the write trigger — picking a branch, backtracking, or auto-activating on continue all sync the real file via conflict-aware merge; one shared backend session serves both the embedded webview and a standalone browser tab; only VS Code can know the real active file, so the backend holds that as shared live state; "Code change" intent is disabled until a real active file is known (no fallback picker).

**5.6.1 Backend: live active-editor-context state** — `POST`/`GET /active-editor-context`, broadcasts `active_editor.changed` over the WebSocket.

**5.6.2 Extension: report the active editor** — debounced 300ms, reports on every active-editor/selection change. Falls back to `vscode.window.visibleTextEditors` when the active editor is `undefined` (focus on a non-editor surface, e.g. the Tree Panel itself) rather than reporting "no file."

**5.6.3 Backend: activation triggers a real write** — `main._sync_active_file_to_disk(node)`, called after every activation. Three-way merge against `_file_last_synced` (the last content *this mechanism* wrote to that path), not the activating node's own snapshot — correct for backtrack as well as continue/patch/merge. Activation always succeeds at the tree level; a sync conflict fires `conflict.detected` without blocking the switch.

**5.6.4 Frontend: intent-aware prompt bar** — shared `active_editor.changed` subscription; prompt bar shows the current target file; Question/Code-change toggle, Code-change disabled with no known file. "Explore 3 approaches" stays outside the toggle (file-grounding is opportunistic for it, not required).

**Automatic intent classification** — `Branch.is_code_change: bool`, self-classified with no steering bias; three-way **Auto / Question / Code change** toggle (Auto is default) — an explicit override always wins over the model's own classification. `orchestrator._resolve_target(intent, target_file, target_range, branch)` implements the resolution rule.

---

## Phase 6 — Benchmark and polish (complete)

**6.1 Run the benchmark** — real Claude API calls, scored per mode per category. Task set: 15 tasks (10 standard + 5 custom) initially, expanded 2026-10-20 to the full 80 (50 standard + 30 custom) — every one individually verified with a real reference solution against its own `test_suite`.

**6.2 UX polish** — 50-node dismissible warning toast; `node.stale` flagging (via `edit_prompt`, already in place from Phase 1); Cmd/Ctrl+B branch, Cmd/Ctrl+Z backtrack, Esc close inspector (ignored while typing in a form field); `Orchestrator.merge_siblings(node_a_id, node_b_id)` — LLM synthesis of two sibling branches into one new node, `POST /merge-siblings`, State-tab UI; backend performance test for a 50-node tree; Playwright E2E suite (`frontend/e2e/`) against a real browser + a real, FakeLLM-backed backend (`backend/tests/e2e_server.py`, on its own port so it never touches a live manual-testing session).

**6.3 Deliverables** — README (architecture, benchmark table, install/run); Medium post draft (`docs/medium-post.md`).

---

## Phase 7 — Multi-file awareness (needs Phase 5.6 + Phase 1's codebase_context; complete)

When a request is actually about a different file than the one currently targeted, the model can redirect to the correct one instead of failing or editing the wrong file.

- [x] `codebase_context_for_prompt` prepends a names-only manifest of files left out of scope (`codebase_graph.list_known_files`).
- [x] `Branch`/the model's JSON response gain `target_file: Optional[str]` — which file its `code` actually concerns.
- [x] `orchestrator._maybe_redirect_target`: a declared target_file different from the one actually shown triggers exactly one verification retry, re-scoped to that file, before the content is trusted. A hallucinated path (not in the manifest) is ignored.
- [x] A redirect also clears `target_range` (doesn't carry over to a different file).

Known limitation, addressed in Phase 8: a single request needing *two* files changed in one turn only applied to one of them (the model has no way to express "additional" files in this schema).

---

## Phase 8 — Multi-file-per-node support (needs Phase 7; complete)

- [x] `TreeNode.additional_edits: Dict[str, str]` — files beyond the primary `target_file`, purely additive; every existing node/test/UI path is unaffected.
- [x] `Branch`/the model's JSON response gain `additional_files: {"path": "content", ...}`.
- [x] `orchestrator._resolve_additional_edits`: each declared file is trusted directly if it was already in the scoped context sent for that call; if named but not shown, one verification call scoped to that file; hallucinated paths dropped; capped at `_MAX_ADDITIONAL_FILES = 3`. Only resolved when the primary target itself is a trusted code change.
- [x] `main._sync_active_file_to_disk`'s per-file logic factored into a shared `_sync_one_file` helper, called once for the primary file and once per additional file — independent conflict handling. `_additional_source_snapshots` gives each extra file the same generation-time baseline the primary file has.
- [x] `/apply-to-file` also writes `additional_edits` (fail-open, distinct from the primary file's strict conflict contract).
- [x] Frontend: `NodeInspector` renders one labeled block per additional file.

---

## Phase 9 / 9.1 — Query actionability and clarification file-awareness

Folded into Phase 5 above (actionability classification and the `ClarificationFileMismatch` safety check both live there now, since they amend `/query` and `/patch`'s existing behavior rather than introducing a new capability).

---

## Out of scope (do not build, do not stub)
Option B (propagate patches through descendants), multi-user, cloud hosting, JetBrains/non-VS-Code IDE integration, auto-branching classifier, PyPI packaging, MCP wrapper before Phase 6. Model-agnostic benchmarking is future scope only.

---

## How to test

**Automated (no API key needed):**
```
python3 -m pytest        # backend + benchmark
cd frontend && npm run build && npm run e2e
cd vscode-extension && npm run compile
```

**Manual, against the real API** (`ANTHROPIC_API_KEY` in `backend/.env`):
1. `cd backend && uvicorn main:app --port 8000 --reload`
2. `cd frontend && npm run dev` — open http://localhost:5173
3. Branch from a prompt, pick one, backtrack, save/load, edit a prompt (stale subtree), query a past node, patch/merge a clarification, merge two siblings.
4. Inside VS Code: open the Tree Panel or run "Branch-Out: Ask about selection"; confirm activation writes the real file and backtracking reverts it.
