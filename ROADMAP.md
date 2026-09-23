# Branch-Out Roadmap

Checklist of phases, steps and substeps. `[x]` done, `[ ]` to do, `[~]` built but differs from the spec (see notes).
Rules: finish and verify Phase N before starting N+1. Phase 3 (VS Code extension, inserted 2026-09-26) only needs Phases 1–2. Phases 4 (cross-node querying) and Phase 3 are independent of each other; Phase 5 (patches) needs both 2 and 4, and upgrades Phase 3's file-apply; Phase 6 needs all.
Fixed constraints: append-only nodes (only `status` and `children_ids` may change), `anthropic` imported only in `claude_client.py`, `asyncio.gather` for generation, async evaluation, selective subgraph context, save on session end only, beam width default defined once, `/patch` always runs conflict detection. Every module gets a test file; every fail-open path gets a deliberately-broken test; unit tests mock the LLM and need no API key.

---

## Phase 1 — Core tree  (complete: all steps 1.1–1.11 implemented, milestone verified against the real API)

**1.1 Data models** (`models.py`)
- [x] `QueryEntry`, `TreeNode`, `Branch` exactly as specified
- [x] `tests/test_models.py`: field types, `created_at` defaults to now
- [x] Immutability test (at the store layer — see 1.3; the dataclasses themselves are plain, immutability is enforced by `TreeStateManager` returning deep copies)

**1.2 LLM abstraction** (`llm_client.py`, `claude_client.py`)
- [~] `LLMClient` ABC + `ClaudeClient` — uses `AsyncAnthropic`, not the sync client in the spec snippet. Documented as a deliberate deviation in `CLAUDE.md`: the sync client blocks the event loop and would serialise the `asyncio.gather()` calls, which CLAUDE.md's own async-discipline section forbids.
- [x] `tests/test_llm_client.py`: ABC cannot be instantiated; AST scan of `backend/` confirms only `claude_client.py` imports `anthropic`; mocked `complete()` returns a string

**1.3 Tree state manager** (`tree_state.py`)
- [x] Renamed to `TreeStateManager` with `add_node`, `get_node`, `get_children`, `get_ancestors`, `get_descendants`, `set_active`, `mark_stale`, `mark_orphaned`, `revive`, `all_nodes`, `node_count`
- [x] `get_node`/`get_children`/`get_ancestors`/`all_nodes` return deep copies; mutating a returned node cannot affect the store
- [x] `tests/test_tree_state.py`: add/get, ancestors, children, duplicate id raises `ValueError`, immutability, `set_active` and `mark_stale` touch only `status`, `node_count`

**1.4 Branch generator** (`branch_generator.py`)
- [x] Parallel via `asyncio.gather(..., return_exceptions=True)`; takes `user_preferences`
- [x] A failed branch is logged and dropped; the rest are returned (fail-open)
- [x] Labels "Branch A/B/C..." via `branch_label()`
- [x] `tests/test_branch_generator.py`: N branches returned; one failure gives N-1; wall-time check proves concurrency; malformed JSON keeps raw output; label sequence

**1.5 Orchestrator** (`orchestrator.py`)
- [x] `branch`, `backtrack`, `continue_from(node_id, prompt)` — one LLM call, one new child
- [x] `backtrack` orphans everything off the new active path (not an ancestor/descendant of the target) and revives anything on the new path that was orphaned; nothing is ever deleted
- [x] Takes `TreeStateManager` + `LLMClient` per spec
- [x] `tests/test_orchestrator.py`: N children with correct `parent_id`; backtrack activates target and deletes nothing; continue adds one child; siblings orphaned not deleted; revisiting an orphaned node revives it

**1.6 Session persistence** (`session.py`)
- [x] Save/load JSON round trip, keyed `nodes` (see 1.7 decision)
- [x] Save failure (bad path) logs and re-raises `OSError` — caller decides how to surface it, tree is untouched and the session keeps running
- [x] Malformed/missing file raises `ValueError` with a clear message
- [x] `tests/test_session.py`: 5-node tree with branch + orphan round-trips identically incl. `active_id`; bad path; malformed file

**1.7 FastAPI backend** (`main.py`)
- [x] `/branch`, `/backtrack`, `/continue`, `/session`, `/session/save`, `/session/load`, `/ws`
- [x] **Decision:** `/session` and `/session/load` respond with `{"nodes": [...]}` — roadmap wins over the handoff brief's `{"tree": [...]}, per "treat roadmap as source of truth." Frontend updated to match.
- [x] `/ws` accepts connections and does nothing else yet (Phase 2 adds events)
- [x] `tests/test_api.py` with `httpx`/`TestClient`: branch, backtrack, 404 on unknown node, session, save/load round trip, continue, websocket connect

**1.8 React frontend**
- [~] `TreeCanvas`, `StatusBar`, `NodeInspector` (State tab) — built
- [ ] `Rail.tsx`: still a stub. Icons for Queries, State, Branches, History, Settings not built; inspector currently opens directly on node click instead of via rail icons
- [ ] Inspector Queries tab with inert "Ask this node anything" input — not built (State tab only, as scoped)
- [ ] Node "evaluating..." score placeholder — not built (no evaluator exists yet; deferred to Phase 2 since there's nothing to placeholder for)
- [x] Orphaned nodes: dashed border, 30% opacity (`.bo-node.orphaned` in `styles.css`); clicking one calls `backtrack`, which revives it
- [~] "Explore alternatives" is functional (opens a prompt dialog, calls `/branch`) rather than inert. **Decision:** kept functional — the milestone below requires "click Explore alternatives → 3 branches appear," which directly contradicts step 1.8's "click does nothing" line. Treated the milestone as the authoritative acceptance test since it's the only testable form of the requirement.
- [ ] Empty-state UI — not built

**UX fix (2026-09-25): the prompt bar was calling `/branch` for every submission.** The user reported that a simple question triggered a 3-way branch instead of a single answer — correct: the prompt bar had only one action, wired to `/branch` (beam_width=3), even though `/continue` (single LLM call, no branching) has existed and been tested since 1.5. Fixed: the prompt bar's default action (**Ask**, Enter-to-submit) now calls `/continue` — one answer, one new child, no fan-out. A second, explicit button (**Explore 3 approaches**) calls `/branch`. The hover "Explore alternatives" button on past nodes is unchanged (branching from history is still branching, correctly). `useTree.ts` gained `continueFrom`.

**Visual redesign, v1 (2026-09-25).** Full rewrite of `styles.css` on user feedback ("looks really bad... focus on designing it neatly, professionally, vibrant/colourful"): dark theme with gradient-brand accents (violet → pink), glass-blur status/prompt bars, card-style tree nodes with a color-coded status dot (green glow = active, amber dashed = stale, dim dashed = orphaned), pill buttons (primary/accent/ghost), Inter + JetBrains Mono fonts. `StatusBar.tsx`, `TreeCanvas.tsx`, `NodeInspector.tsx` updated to the new class structure — no functional change, markup only.

**Visual redesign, v2 — "Editorial Paper" (2026-09-25), superseded v1.** User called v1 "a classic Copilot prototype." Explored 4 original directions as a Design-canvas artifact (Botanical Canopy, Terminal Brutalist, Editorial Paper, Neon Circuit) before writing any code, to avoid guessing through several full rewrites — see `https://claude.ai/artifact/LBAKTdJuniNHm9AJC3MPKY`. User picked Editorial Paper, then iterated on it directly in the artifact (colorized, added a real branch-colored tree structure, tried a punchier palette that read "too girly," rolled back one step) before signing off. **Ported into the real app:** warm paper background (`#f7f3ec`) replacing the dark theme entirely; Newsreader (serif) + Space Grotesk (sans) + JetBrains Mono, replacing Inter; flat print-style borders/rules instead of glass-blur and gradients; a 3-color system — indigo `#4f46e5` (active), amber `#f59e0b` (stale), rose `#e11d48` (orphaned) — applied consistently to node card background/border, React Flow edge color (edges are now `step`-type and colored by the *target* node's status, so the branch structure itself is visibly color-coded, not just the cards), buttons, and the inspector's stale banner. Small dev-tool touches: a mac-style traffic-light bar on the code snapshot block, a tiny 4-dot branch-mark next to the wordmark. `index.html`'s font `<link>` and `color-scheme` meta updated to match. No functional/logic change — markup and CSS only, not covered by automated tests; confirm by eye in the browser per the testing steps below.

**Phase 1 milestone — verified against the real API (2026-09-24), via curl rather than the browser (see note below)**
- [x] Prompt → 3 branch nodes appear (`is_even`, 3 distinct real implementations)
- [x] Backtrack into a branch → becomes active
- [x] Prompt from the active node → single child appears (`/continue`, docstring added)
- [x] Backtrack to an earlier node → works (root; correctly orphans nothing, since root is an ancestor of everything)
- [x] Save, load → identical node count (5) before and after
- [x] Backtrack past a branch → siblings orphaned (verified with a leaf node, where orphaning actually applies); backtracking into an orphaned node revives it (verified twice, including on an already-orphaned node)

**How it was run:** 4 real LLM calls total (1 `/branch` call with `beam_width=3`, 1 `/continue` call), everything else (backtrack, save, load, revive) made zero API calls. Driven via `curl` against a locally running `uvicorn` + the real key, not the React UI — this verifies the backend contract end-to-end but not the actual browser rendering (node dimming, stale banner, etc. — those were verified by code review + the earlier `styles.css`/`NodeInspector` work, not by eye in a browser). Key was added to `backend/.env` (gitignored, confirmed with `git check-ignore`) for this run only, per the user's instruction to use it minimally and only with explicit permission each time. Server was stopped immediately after; no further calls follow without asking again.

**Decisions made on the open items (2026-09-23):**
- **Single session for the POC.** One global orchestrator is correct scope, not a gap. Multi-session/multi-tab support moved to Out of scope → future scope below.
- **Tokenetics compaction — implemented.** `claude_client.py` wraps every completion through Tokenetics' frozen Tier 0 API: `tk.prepare(messages=, model=, max_tokens=, system=)` before the call, `tk.finalize(response)` after — all 11 stages (dedup, near-dup, task classification, schema minification, knapsack context scheduling, delta compression, cache reorder/optimize, structured output, brevity, adaptive budget on the request side; post-hoc trim on the response side) run through that one call pair, since Tier 0's API is frozen and the individual stages aren't meant to be called separately. **Deviation, documented in `claude_client.py`:** `prepare()`/`finalize()` are synchronous and make their own blocking HTTP calls (token counting, via Tokenetics' own internal `anthropic.Anthropic` sync client, separate from `self.client`); both are run through `asyncio.to_thread` so they don't block the event loop and serialize the concurrent branch-generation calls. Checked `PostHocTrimStage` and `StructuredOutputStage` specifically before wiring this in: the former only strips end-anchored conversational sign-offs (safe for our JSON-only responses), the latter is a no-op whenever no `tools` are registered (we never pass any). Not unit-tested directly (would require a real API key, which the test suite must not need); covered by the existing AST-scan + mocked-`complete()` tests, and by the real-API milestone run below, which used this code path.
- **`node.stale` now has a trigger — implemented.** `POST /edit-prompt {node_id, new_prompt}` (`orchestrator.edit_prompt`): generates a new sibling from `node_id`'s parent with the revised prompt (via `continue_from`, one LLM call), and marks `node_id` plus its entire descendant subtree `stale` — dashed border, 60% opacity, per the roadmap's node-state spec. The original node is never rewritten, only its `status` field changes, which the append-only rule already permits. Root has no prompt to edit, so editing it raises 400. Frontend: `NodeInspector` gets an "Edit prompt" button (hidden on root) that opens a textarea, plus a stale banner when viewing a stale node. Tests: `test_orchestrator.py` (sibling created, original untouched, whole subtree marked, root rejected), `test_api.py` (endpoint round trip, 400 on root).
- **Cancel in-flight branch generation — implemented.** Frontend: `useTree.ts` passes an `AbortController` signal through every mutating `fetch`, exposed as `tree.cancel()`; `App.tsx` shows a Cancel button while `busy`. Backend: `main.py`'s `_run_cancelable` helper runs the orchestrator call as a task, polls `request.is_disconnected()` every 50ms, and cancels the task on disconnect — the cancellation propagates into the underlying `asyncio.gather` in `branch_generator.py`, which cancels its still-running sub-tasks too, so nothing keeps running for a client that's gone. Wired into `/branch`, `/continue`, and `/edit-prompt` (all three LLM-calling endpoints, not just `/branch`, for consistency). Tests: `test_orchestrator.py` proves a cancelled `branch()` leaves zero partial nodes and that the lock (below) is released afterward — not testable end-to-end through `TestClient`'s disconnect simulation, so the polling loop itself is code-reviewed rather than integration-tested.
- **Concurrency guardrail — implemented.** `Orchestrator` now holds a single `asyncio.Lock`; `branch`, `continue_from`, `edit_prompt`, and `backtrack` (now `async def`, was sync) all acquire it. `edit_prompt` calls an unlocked internal helper (`_continue_from_unlocked`) rather than the public `continue_from`, since `asyncio.Lock` isn't reentrant and it's already holding the lock. Tests: two concurrent `branch()` calls against the same parent produce no label/id collisions; a `backtrack()` fired mid-`branch()` can't interleave and see a half-built tree.
- **Benchmark baselines (Phase 2.5 / 5.1 update).** `runner.py` runs two baselines alongside Branch-Out in the same pass: a **generic prompt** baseline (no engineering — the headline, intuitively-understood comparison) and a **matched-prompt** baseline (same context/detail Branch-Out receives, single-pass, no branching — preempts "isn't this just prompting?"). Flags become `--baseline-generic`, `--baseline-matched`, `--branchout`.
- **Context graph persistence (Phase 3 decision, recorded now so Phase 1's session format doesn't need to change later).** No separate networkx JSON. `context_retriever.py`'s graph is rebuilt in-memory from the loaded `TreeNode` tree on session load — every field the graph needs (`context_summary`, `user_preferences`, `parent_id`, `children_ids`) is already serialized by `session.py`. One source of truth, no drift.
- **Linear baseline agent (Phase 5) — still open.** Superseded by the two-baseline decision above; "linear baseline" is now specifically the matched-prompt baseline. No longer a gap.

**Environment (2026-09-24):** `.venv` rebuilt on **Python 3.11.16** (via `brew install python@3.11`), all requirements reinstalled and the full suite reverified (still passing). `pyproject.toml`'s `requires-python` bumped to `>=3.10`. Tokenetics installs cleanly from `git+https://github.com/Dishti-Oberai/tokenetics.git` and imports (`from tokenetics import Tokenetics`) — confirmed as an environment smoke test only, not wired into `claude_client.py` yet. Integration code for Step 1.9 is still pending your go-ahead.

**Bug found and fixed during this review (2026-09-24):** `continue_from` required exactly one branch (`[branch] = await generate_branches(...)`), but `generate_branches` fails open to `[]` when its one call fails. That made the unpacking raise a bare `ValueError`, which `/edit-prompt`'s `except ValueError → 400` handler would then misreport as "you tried to edit root" instead of an upstream LLM failure — since `edit_prompt` calls `continue_from` internally. Fixed by raising a dedicated `LLMGenerationFailed` (in `orchestrator.py`) when `continue_from`'s single call produces nothing; `main.py` now maps it to 502 on both `/continue` and `/edit-prompt`, distinct from the 400 used for the root-edit rejection. Covered by two new orchestrator tests and one API test. All 43 tests pass.

**Bug found and fixed, reported by user (2026-09-25): "prompting hi to root node again opens a new branch instead of replying to it."** Real bug, not user error. `continue_from` (the "Ask" action) created a new child but never moved the active pointer to it — `backtrack` was the only place that called `set_active`. So asking a second question from the same still-active node just fanned out another sibling under it, which visually reads exactly like unwanted branching, even though each individual call really was a single non-branching continuation. **Fix:** `continue_from` now activates the node it just created, running the same orphan/revive sweep `backtrack` does (factored into a shared `_activate_unlocked` helper, since `asyncio.Lock` isn't reentrant and `continue_from` is already holding it). A reply now advances the conversation the way a normal chat does. **Deliberately NOT applied to `edit_prompt`:** its new sibling still stays inactive — the whole point of that flow is offering a revised alternative without silently switching what's "current"; the user picks it via "Make active" if they want it. Regression tests added for both: `continue_from` activates and demotes the old active node (`test_continue_from_activates_the_new_node`, `test_asking_again_continues_the_thread_instead_of_fanning_out_from_root`), `edit_prompt`'s sibling stays inactive (`test_edit_prompt_new_sibling_is_not_activated`), plus an API-level end-to-end check (`test_asking_twice_without_reselecting_continues_the_thread`). All 51 tests pass.

**Bug found and fixed, reported by user (2026-09-25): "clicking on Explore alternatives is not working."** Root cause: `App.tsx`'s `explore` handler (and the Load-session button) used the browser's native `window.prompt()`. That call is silently blocked with no error in a lot of real contexts — cross-origin iframes (many embedded dev-tool previews), some webviews, Chrome's throttling after repeated dialogs — so the click visibly does nothing, no console error, nothing. Same latent bug existed on the "Load" button (`window.prompt("Session path", ...)`), just not yet reported. **Fix:** removed `window.prompt()` from the app entirely. "Explore alternatives" now selects the hovered node (reusing the same orphan-revival logic as a normal node click) and, if a prompt is already typed in the bottom bar, branches it immediately — otherwise the user types into the (already-working, dialog-free) prompt bar and hits "Explore 3 approaches" from there. "Load" now just loads the default session path (`sessions/session.json`) directly, matching "Save"'s existing default-path behavior, rather than prompting for a custom path — arbitrary-path loading dropped from the UI for now (still available via `POST /session/load` directly, e.g. with curl). No backend change. Not unit-testable (native-dialog removal, UI-only); frontend type-checks and builds clean, backend's 51 tests unaffected.

**Follow-up, same day: user confirmed the button "still not working" after the above — and specifically that clicking registers nothing at all (no inspector opens, nothing).** So the `window.prompt()` theory wasn't the (whole) story; something about a React Flow custom node's click handling was intercepting or racing the nested button's click before it could fire — a known unreliable spot in graph/canvas libraries (capture-phase or pointerdown-based selection listeners on the node wrapper). Rather than keep guessing at React Flow's internals with no browser to inspect, removed every plausible cause at once: the button (1) is no longer hover-revealed via local JS state (`onMouseEnter`/`onMouseLeave` + conditional render) — always rendered now; (2) no longer positioned outside the node's own measured box via a negative offset (`bottom: -14px`) — now in normal document flow, fully inside the card; (3) stops the click at `onMouseDown`, `onPointerDown`, and `onClick`, calling both `stopPropagation()` and the underlying native `stopImmediatePropagation()`, not just one. Any one of these three could plausibly have been the actual cause; fixing all three at once beats a slow one-at-a-time guessing loop with no way to observe the DOM directly. Frontend type-checks and builds clean; not unit-testable (React Flow interaction, not logic) — needs a browser check.

**Second follow-up, same day: user confirmed click still registers nothing, and asked for hover-only visibility back.** Inspected React Flow 11.11.4's own shipped `style.css` directly rather than guessing further (`node_modules/reactflow/dist/style.css`): `.react-flow__node` sets `pointer-events: all` and `cursor: grab` — React Flow uses `d3-drag`/`d3-zoom` internally for node and pane interaction, which can register their own native listeners on ancestor elements. If any of those are capture-phase, they fire before any handler on our nested button ever runs — no `stopPropagation()` called from *inside* the button, no matter how many event types it's attached to, can win that race, since capture-phase ancestor listeners execute before the event even reaches a descendant. That's the mechanism `test`ing so far couldn't rule out. **Fix:** moved the click handler off the button entirely and onto a capture-phase (`onClickCapture`) listener on `tree-canvas-wrapper`, a plain `<div>` that's an ancestor of React Flow's whole rendered tree (wraps `<ReactFlow>` itself). A capture listener on an ancestor always runs before any listener further down — including whatever React Flow attaches internally — so this one fires first regardless of what's happening deeper in the tree, reads which node via a `data-explore-node-id` attribute (event delegation, `closest()`), calls `onExplore` directly, then `stopPropagation()`+`preventDefault()` to stop the event from ever reaching React Flow's own node/pane handling. The button itself now carries no `onClick` at all — it's inert, existing only as the visible/clickable target the wrapper's listener detects. Hover-only visibility restored via pure CSS `:hover` (opacity/pointer-events toggle, no JS state, no timing to get wrong), still fully inside the node's own box (no overflow). If this *still* doesn't work, the next step needs the user's browser devtools: open the Elements panel, click the button, and check the Event Listeners tab on `.tree-canvas-wrapper` and `.react-flow__node` directly, since at that point guessing further without seeing the actual attached listeners stops being productive. Frontend type-checks and builds clean; backend untouched, 51 tests still pass.

**Real-API milestone check (2026-09-25):** ran a genuine coding problem (palindrome checker) through `/branch` with `beam_width=3` via curl — 3 distinct real synthesized solutions landed as real nodes (two similar two-pointer approaches, one with self-written test cases). Backtracked into one and `continue`'d it — confirmed on real output, not just mocks, that the reply extended that exact node's code (added a docstring + 5 doctests) using real accumulated context, and correctly became the new active node. Also surfaced and explained a real gap while doing this: the frontend has no live sync (no WebSocket events yet — Phase 2), so a browser tab only re-fetches after its own actions; nodes created from another source (like these curl calls) don't appear until the page is reloaded. Not a bug, just an undocumented-until-now consequence of Phase 1's scope; told the user to refresh to pull in new state.

**UI polish, same day — two more user reports.** (1) *"UI looks weird now"*: root cause was the hover-reveal fix two entries up — using `opacity: 0` to hide the Explore-alternatives button still reserves its box, so every card had a permanent dead gap at the bottom even when not hovered. Fixed by collapsing it to `max-height: 0` (plus margin/padding to 0) when hidden, expanding on `:hover`, so hidden cards are compact and the reveal animates instead of just fading into pre-reserved space. (2) *"why are nodes just called Branch A, B, C"*: `branch_label` is deliberately locked to that generic scheme (`models.py`'s own comment: `# "Branch A", "Branch B" etc.`) and load-bearing — the orchestrator's offset math depends on the same letter sequence staying stable and collision-free across repeated `/branch` calls on one parent (confirmed working correctly in the real-API check above: new branches picked up as C/D/E, not colliding with existing A/B). Rather than silently repurpose or extend that locked field, asked the user how a more descriptive name should be sourced; they picked frontend-only derivation from existing data, no schema/backend change. **Implemented:** new `frontend/src/nodeTitle.ts`, `deriveNodeTitle()` — `TreeNode` has no dedicated `reasoning` field (only `Branch`, the transient `/branch` response type, does); it's folded into `context_summary` as `"Task: ...\nApproach: {reasoning}"` per turn, so the util parses the most recent `"Approach:"` segment and trims it to a short title. `branch_label` itself is untouched everywhere (API, tests, status bar, orchestrator) — this only changes what's *displayed* as the primary heading on tree-canvas cards and the inspector header, with the real `branch_label` demoted to a small caption tag (new `.branch-tag` CSS class, shared between both). Root and any node with no reasoning yet (empty `context_summary`) falls back to showing `branch_label` as before. Card `max-width` and canvas column spacing (`X_GAP`) bumped to fit the longer titles without crowding. Not unit-testable (presentation-only, derived from a hand-verified real-data trace, not from automated tests); frontend type-checks and builds clean, backend's 51 tests unaffected (no backend change).

**Follow-up, same day: user reported "nodes colliding into each other."** Direct consequence of the naming change just above: adding the `branch-tag` line and a longer derived title made cards taller than before, but `TreeCanvas.tsx`'s layout positions rows at a fixed `Y_GAP` per depth (150→ was 110) with no awareness of actual rendered card height, so taller cards started visually overlapping the row below. Fixed on both sides at once rather than just cranking the gap further and hoping: (1) the title is now capped to 2 lines via CSS `-webkit-line-clamp: 2` (`display: -webkit-box` + `-webkit-box-orient: vertical` + `overflow: hidden`), so a card's height has a hard ceiling no matter how long the derived text is — previously an unbounded title could wrap to 3+ lines in the ~180px content width; (2) the branch-tag line was folded back onto the `sub` row (`Branch C · 676e6cba` instead of its own line) to remove a whole row of height; (3) `Y_GAP` raised to 150px, comfortably above the new worst-case card height (~75-80px) rather than the old height-matched 110px. `X_GAP` (230px, from the earlier naming change) was already wide enough horizontally — this was purely a vertical/height problem. Not unit-testable (layout/CSS, not logic); frontend type-checks and builds clean, backend's 51 tests unaffected (no backend change).

**Steps 1.9, 1.10, 1.11 — all implemented (2026-09-24).** See the entries above. Full suite: 47/47 passing.

**Out of scope — future scope (good-to-have, not POC)**
- Multi-session / multi-tab support (moved here from "open item" — confirmed out of scope for the POC by the user).

---

## Phase 2 — Hybrid evaluator  (implemented 2026-09-26; 2.5's task set is partial — see note)

**2.1 Test runner** (`test_runner.py`)
- [x] `TestResult` dataclass; `run_tests(code, timeout=10.0)` in a temp file via subprocess
- [x] Detects unittest vs pytest via import regex; parses pass/fail/error counts from output
- [x] Never raises: syntax error, crash, or timeout all return `errors=1, pass_rate=0.0`
- [x] `tests/test_test_runner.py`: passing, failing, syntax error, no tests, timeout, `has_test_imports`
- **Bug found and fixed by its own test:** `subprocess.TimeoutExpired`'s captured `stdout`/`stderr` can come back as `bytes` even with `text=True` — string-concatenating it raised `TypeError` inside the *fail-open path itself*, the one place that must never raise. Fixed with a small `_as_text()` decoder.

**2.2 Score normaliser** (`score_normaliser.py`)
- [x] `rank_branches`: one LLM call for all branches, JSON `[{branch_index, rank, score, reason}]`
- [x] Failure, malformed JSON, or a missing `branch_index` leaves every branch's rank/score/score_source `None`, never raises
- [x] `tests/test_score_normaliser.py`: valid, malformed, missing index, exactly one LLM call regardless of beam width, empty list
- **Schema addition, flagged:** `Branch` gained `score_source: Optional[Literal["tests","llm_judge"]]`. Not in Phase 1's locked shape — but the roadmap's own Step 2.3 snippet assigns `branch.score_source` directly, so main.py has no way to know which value to persist onto the `TreeNode` without it. Mirrors `TreeNode.score_source` exactly.

**2.3 Hybrid evaluator** (`evaluator.py`)
- [x] Test-gate if any branch has `import unittest`/`pytest` (`score_source="tests"`), else LLM judge
- [x] One branch's test-run failure scores it `0.0` (logged); the rest still score normally
- [x] `rescore_with_tests(node_ids, tree)` — synchronous, re-runs tests, updates `score`/`score_source` in place, returns the updated nodes
- [x] `tests/test_evaluator.py`: both scoring paths, one-branch-failure isolation, empty list, retroactive rescore touches only the named nodes
- **Real spec conflict, resolved and documented:** retroactive rescoring requires mutating `score`/`score_source` on already-created nodes. The append-only section's literal wording ("only `status` and `children_ids` may change") didn't list them — but CLAUDE.md's own Evaluation-discipline section requires retroactive rescoring unambiguously. Resolved by treating `score`/`score_source` as mutable metadata alongside `status` (new `TreeStateManager.set_score()`, same pattern as `mark_stale`/`mark_orphaned`) — never touching `code_snapshot`, `context_summary`, or anything that counts as a node's actual content.

**2.4 Wire into FastAPI + WebSocket**
- [x] `branch.ready` fires *per branch* as each of the `beam_width` LLM calls completes — required restructuring `branch_generator.generate_branches` to take an `on_result` callback fired per-completion (not after the whole `asyncio.gather`), and `Orchestrator._branch_unlocked` to build each `TreeNode` and call `on_branch_ready` from inside that callback, so the real `node_id` is known before the event fires. `continue_from`/`edit_prompt` pass no callback — zero behavior change there.
- [x] `_evaluate_and_notify` via `asyncio.create_task` (fire-and-forget, not awaited by `/branch`); `score.ready` fires per node after; scores persisted via `tree.set_score`
- [x] `/session/load` (now `async def`, needed for `asyncio.create_task`) triggers retroactive rescoring for nodes where `score_source == "llm_judge"` **and** the code now contains test imports — never touches already-tests-scored nodes or nodes without tests
- [x] Frontend: `useWebSocket.ts` (connects to `/ws`, reconnects on drop, typed events), `useTree.ts` tracks a `pendingScoreIds` set (branch.ready → add, score.ready → remove + patch the node's score in place, no full refetch needed), pulsing CSS skeleton (`.score-pending`) shown on `TreeCanvas` node cards and in the inspector while pending — distinct from "not scored yet" so a `continue`/`edit-prompt` node (never evaluated at all) doesn't look stuck
- [x] `tests/test_api.py`: `branch.ready` proven to fire before `score.ready` (real WebSocket, real ordering assertion) with matching node ids; test-scoring path verified over the wire; retroactive rescore-on-load proven end-to-end (polls `/session` for the background task to land); a companion test proves already-`tests`-scored and no-test nodes are correctly left untouched

**2.5 Benchmark task set — partial, NOT locked, needs a decision**
- [x] `runner.py`: `--baseline-generic`, `--baseline-matched`, `--branchout`, `--dry-run`; loads tasks, runs each mode, scores against the task's own `test_suite`, writes `results.json`, prints a per-mode-per-category summary (never a flat number, per the honesty requirement)
- [x] Task JSON shape exactly as specified: `id`, `description`, `prompt`, `test_suite`, `category`
- [~] **15 tasks written (10 standard + 5 custom), not 50 + 30.** Hand-authoring 80 genuinely non-filler tasks in one pass wasn't something I was willing to fake — every one of these 15 was individually verified by writing a real reference solution and running it against the task's own `test_suite` via the actual `test_runner.py` (caught one real bug this way: `custom_003`'s own test asserted a tick sequence that contradicted its own stated spec — fixed both the test and confirmed against a corrected reference). **Not locked.** Your call: I expand this to the full 50+30 now, we lock 15 as the working set, or you hand me a task list to use instead.
- [x] `benchmark/tests/test_benchmark_runner.py`: task loading, field validation, per-category summarization (renamed from `test_runner.py` — collided with `backend/test_runner.py`'s own module name under pytest's rootless collection)
- [x] `pyproject.toml`'s `testpaths` extended to include `benchmark/tests`, so one `pytest` run covers everything

**Milestone:** scores populate asynchronously after branches appear (verified over a real WebSocket connection); a retroactively-added-tests scenario correctly rescores the affected node without touching others; all 15 tasks (not yet 80) load cleanly with `--dry-run`.

**Real-API verification (2026-09-26), 10 real LLM calls total.** Two real `/branch` calls over a real WebSocket connection: one prompt asking for embedded tests, one plain. Confirmed `branch.ready` × 3 then `score.ready` × 3 for each call, correct ordering, matching node ids; test-gate path (`score_source="tests"`) and LLM-judge path (`score_source="llm_judge"`, real ranks 1/2/3) both fired correctly on real model output.

**Real bug found and fixed by this check.** 2 of the 3 branches in the first call scored `0.0` — not from bad code, but because `branch_generator._parse()` failed to extract the code at all. Root cause, confirmed by reproducing the exact `JSONDecodeError`: the model occasionally drops the `n` in a `\n` escape inside its JSON response, emitting a bare `\` followed by whitespace (invalid JSON) instead of `\n    ` (newline + indent) — a real, reproducible Claude output quirk, not something preventable on our side. The existing fallback made it worse: on any parse failure it stored the *entire unparsed JSON wrapper* — literal backslashes, quotes, the `{"code": ...}` structure itself — as the branch's "code", which is guaranteed to fail every test run regardless of whether the actual solution was correct. **Fix:** `_parse()` now attempts one repair pass (`\` immediately followed by whitespace → `\n` followed by that whitespace) before falling back to raw-text storage. Verified against the exact captured failure (recovered valid, `compile()`-clean Python from both broken branches) before touching production code, then re-ran the identical real prompt afterward — got 3 cleanly-decoded branches, all scored `1.0` via real test execution. Regression test added using the real captured malformed response as a fixture (`test_parse_repairs_a_dropped_n_in_a_json_newline_escape`), plus a test confirming truly-unparseable responses still fall back safely. 80/80 tests pass.

---

## Phase 3 — VS Code extension  (inserted 2026-09-26 — user requirement: Branch-Out driven from inside the IDE, Copilot-like, tree instead of one linear thread)

**Why this slot in the sequence:** only needs Phase 1's `/branch`/`/continue`/`/session` endpoints and Phase 2's score badges — nothing here depends on cross-node querying or the git-merge/conflict machinery. Building it now means Phases 4–6 get IDE exposure automatically as they land, instead of retrofitting IDE support after the fact. The one deliberate deferral: "apply to file" starts as a naive full-file write in this phase (no conflict detection) and gets upgraded in Phase 5 to reuse `git_engine.py`'s real conflict detection against the live file, once that exists.

**Decisions on record (2026-09-26):**
- IDE target: **VS Code only** for now (richest extension API, webview support for reusing the existing React app; JetBrains explicitly out of scope — see CLAUDE.md).
- Interaction model: **both** — a full embedded tree panel (webview hosting the existing canvas/inspector/prompt-bar) *and* a lightweight inline quick-pick command for fast asks without opening the panel.
- Relationship to the standalone web app: **complements it, doesn't replace it** — the web app stays for the portfolio demo/YouTube video/full-tree-at-a-glance use case; the extension is a second, more practical client for real day-to-day coding, talking to the exact same local FastAPI backend.

**3.1 Schema: file context on TreeNode**
- [ ] Add `target_file: Optional[str]` and `target_range: Optional[dict]` (e.g. `{"start_line": int, "end_line": int}`) to `TreeNode`. Flagged schema addition, same pattern as `Branch.score_source` in Phase 2 — nodes created via the existing web UI simply leave these `None`; nodes created via the extension always set them.
- [ ] `node_to_dict`/`node_from_dict` round-trip the new fields; existing saved sessions without them still load (default `None`)
- [ ] Tests: round-trip with and without file context set; old-format session files (missing the new keys) still load

**3.2 Backend: apply-to-file endpoint**
- [ ] `POST /apply-to-file {node_id}` — writes that node's `code_snapshot` to its `target_file` (full-file replace; no conflict detection yet, that's Phase 5's upgrade)
- [ ] Refuses (400) a node with no `target_file` set
- [ ] Path safety: refuses (400) any `target_file` outside the workspace root the extension declares at connect time — never a path-traversal write
- [ ] Tests: correct content written, missing-target-file rejection, path-traversal guard

**3.3 VS Code extension shell**
- [ ] New top-level `vscode-extension/` directory — TypeScript, VS Code Extension API, `package.json` manifest and activation events
- [ ] Talks to the same local FastAPI backend already running on `localhost:8000` — no second backend process, just a new client alongside the React web app
- [ ] Command **"Branch-Out: Open Tree Panel"** — opens a Webview panel hosting the existing React app (same build, same components, no fork)

**3.4 Inline quick-pick command**
- [ ] Command **"Branch-Out: Ask about selection"** — reads the active file path + selected text (or cursor position) as context, prompts for a task description via VS Code's input box
- [ ] Quick-pick: "Just answer" (→ `/continue`) vs "Explore 3 approaches" (→ `/branch`)
- [ ] Results shown in a VS Code QuickPick list (approach summaries); selecting one calls `/apply-to-file`
- [ ] Nodes created this way always set `target_file`/`target_range` — distinguishing IDE-originated nodes from web-UI ones

**3.5 Webview ↔ extension bridge**
- [ ] The embedded webview knows it's running inside VS Code (vs. a standalone browser) and receives the current file context from the extension
- [ ] `NodeInspector` gains an "Apply to file" action, visible only when the node has `target_file` set and the app is running inside the VS Code bridge

**Milestone:** inside VS Code, select code (or place the cursor), run "Branch-Out: Ask about selection", type a prompt, see a quick-pick of approaches, choose one, watch it land in the real file. Separately: open the full tree panel inside VS Code and interact with it exactly as the standalone web app.

---

## Phase 4 — Cross-node querying  (renumbered from Phase 3 on 2026-09-26; content unchanged)

**4.1 Context retriever** (`context_retriever.py`)
- [ ] networkx `DiGraph`; nodes store `decisions`, `code_diff`, `summary`, `preferences`
- [ ] `get_relevant_context(node_id)` walks ancestors only; falls back to `context_summary` on failure or empty subgraph
- [ ] Populate the graph whenever a `TreeNode` is created; serialise with the session
- [ ] Tests: 3-node chain, forced failure fallback, sibling branches isolated
- [ ] Test with 10+ node sessions (risk register)

**4.2 `POST /query`**
- [ ] Reconstruct context, prepend chained prior Q&A if `chain_from`, call LLM, append `QueryEntry`
- [ ] Active node untouched; 404 for unknown node; LLM failure returns 500 with history unchanged
- [ ] Tests for each

**4.3 Frontend wiring**
- [ ] Node click opens the Queries tab by default
- [ ] Textarea submits to `/query` with loading state; history persists across open/close
- [ ] Follow-ups auto-send `chain_from`

**Milestone:** query node 2 while on node 5; follow-up references the first; history persists; status bar unchanged.

---

## Phase 5 — Clarify-and-apply and clarify-and-branch  (needs 2 and 4; renumbered from Phase 4 on 2026-09-26)

**5.1 Git engine** (`git_engine.py`)
- [ ] `merge_code(base, patch) -> MergeResult` in a temp git repo, always cleaned up
- [ ] Conflict returns `success=False` with `diff_preview`; invalid patch never raises
- [ ] Tests: clean, conflict, invalid, cleanup on failure

**5.2 `POST /patch` (Option A)**
- [ ] Always run conflict detection first
- [ ] Conflict: fire `conflict.detected`, return conflict object, create nothing
- [ ] No conflict: new child with merged code, async re-score
- [ ] Tests: no-conflict, conflict, WebSocket event

**5.3 `POST /merge` (Option C)**
- [ ] One LLM call extracts user preferences along the path
- [ ] Patch source node's code; new child of `source_node_id` with preferences injected; set active
- [ ] Original path untouched; tests

**5.4 Conflict banner**
- [ ] Inline warning in Queries tab (never a modal): description, +/- diff, Apply anyway / Cancel / Spawn branch
- [ ] Cancel dismisses with no action

**5.5 Upgrade Phase 3's file-apply (new step, added 2026-09-26)**
- [ ] `POST /apply-to-file` now runs the same conflict detection as `/patch` against the real file's *current* on-disk content (which may have changed since the node's code was generated), not just a blind overwrite
- [ ] Conflict: same inline banner pattern as 5.4, surfaced in the VS Code extension
- [ ] Tests: clean apply, conflict against modified file, WebSocket event fires in the extension context too

**Milestone:** clarify from node 2 at node 5, conflict banner, spawn branch rooted at node 2, original path untouched. Plus: editing the real file after a node was generated, then applying that node, correctly surfaces a conflict instead of silently overwriting.

---

## Phase 6 — Benchmark and polish  (needs all; renumbered from Phase 5 on 2026-09-26)

**6.1 Run the benchmark**
- [ ] Baseline and Branch-Out on all 80 tasks
- [ ] Report pass@1, pass@3, mean score, branch utilisation, backtracks per session
- [ ] Ranges per category, not one number; document losses and the auto-pick methodology; no task-set tuning

**6.2 UX polish**
- [ ] 50-node soft warning toast
- [ ] Stale-node flagging via `node.stale`
- [ ] Shortcuts: Cmd+B branch, Cmd+Z backtrack, Esc close inspector
- [ ] "Explore alternatives" fully wired
- [ ] Branch-merge UI for sibling branches
- [ ] Empty state; test performance at 50 nodes
- [ ] Playwright E2E tests

**6.3 Deliverables**
- [ ] README: description, architecture diagram, benchmark table, install/run, demo GIF
- [ ] YouTube demo (5–8 min)
- [ ] Medium post

---

## Out of scope (do not build, do not stub)
Option B (propagate patches through descendants), multi-user, cloud hosting, JetBrains/non-VS-Code IDE integration, auto-branching classifier, PyPI packaging, MCP wrapper before Phase 6 is complete. Model-agnostic benchmarking is future scope only. **VS Code extension is no longer out of scope — see Phase 3 above (decided 2026-09-26).**

---

## How to test Phase 1

**Automated (no API key needed — mocks the LLM):**
```
cd backend
../.venv/bin/python -m pytest tests -q
```
47 tests should pass, covering models, the LLM abstraction, tree state, branch generation, the orchestrator (including edit-prompt, its fail-open path, the concurrency lock, and cancellation), session persistence, and the API.

**Manual, against the real API (needs `ANTHROPIC_API_KEY` in `backend/.env`):**
1. `cd backend && ../.venv/bin/uvicorn main:app --port 8000 --reload`
2. `cd frontend && npm run dev` — open http://localhost:5173
3. Walk the milestone checklist:
   - Type a prompt in the bottom bar, submit → 3 branch nodes should appear in the tree within a few seconds.
   - Click one of the branches → it should become bordered (active), and the status bar's "Active" and "Path" fields should update.
   - Type another prompt from that active node → a single child node appears.
   - Click an earlier node in the tree → it becomes active (status bar updates), and the previously-active branch's now-abandoned siblings should turn dashed/dim (orphaned).
   - Click Save session → check that `backend/sessions/session.json` was written.
   - Reload the browser page (state resets to empty), click Load, enter `sessions/session.json` → the tree should come back identical, same active node.
   - Click one of the dimmed/orphaned nodes → it should un-dim and become active again.
   - Click a non-root node → Edit prompt → type a revised prompt, Save. A new sibling node appears; the original node and everything under it turns dashed/dim (stale), and opening the original node shows the stale banner. Its code snapshot must be unchanged from before the edit.
   - Try Edit prompt on the root node — it should not be offered (or, via curl, `POST /edit-prompt` on `root` should 400).
4. Optional API-only spot check without the browser:
   ```
   curl -s localhost:8000/session | python3 -m json.tool
   curl -s -X POST localhost:8000/branch -H 'content-type: application/json' \
     -d '{"node_id":"root","prompt":"write a binary search"}' | python3 -m json.tool
   ```
