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

**Amendment implemented 2026-09-27 (deliberately reopening a "complete" phase): codebase-aware context.** Root cause of the VS Code extension's README-overwrite incident (see Phase 3): every node's context is purely conversational (`context_summary` — an accumulating "Task/Approach" log starting empty at root); nothing in Phase 1 ever grounded a node in the actual project it's supposed to be helping with. Real Copilot Chat starts every conversation already aware of your workspace; Branch-Out started aware of nothing — a regression below baseline Copilot-parity, not just a missing extra. User's framing, confirmed: Branch-Out adds tree/branch/backtrack *on top of* Copilot-equivalent behavior, it doesn't reinvent Copilot or strip out what it already does well (context awareness). Implemented:
- [x] **New `TreeNode` field, `codebase_context: Optional[str] = None`** — deliberately separate from `context_summary`. Different kind of content: static reference material (a codebase snapshot) vs. an accumulating per-turn decision log. Conflating them would make Tokenetics' compaction (which schedules by conversational recency) mismanage a large static blob sitting inside a growing turn-by-turn string.
- [x] **`Orchestrator.branch()`/`continue_from()` inherit `codebase_context` parent→child by default**, unconditionally — same existing pattern as `user_preferences` already uses. Both gained an `include_codebase: bool = True` parameter that controls only whether it's *included in the LLM prompt for that call*; the created node inherits it either way (Phase 3's per-request toggle, Step 3.7, controls this parameter — it's implemented here because it has to be, not deferred).
- [x] **`branch_generator.generate_branches`/`_generate_single_branch` include `codebase_context` in the prompt** as its own labeled section (`Codebase:\n{...}\n\n`), when present and requested.
- [x] New `TreeStateManager.set_codebase_context(node_id, value)` mutator — same mutable-metadata pattern as `set_score`, never touches `code_snapshot`/`context_summary`.
- [x] Tests: `test_tree_state.py::test_set_codebase_context_updates_only_that_node`; `test_orchestrator.py`'s 6 new tests covering inheritance (with and without a value set), the LLM-prompt on/off toggle for both `branch` and `continue_from`, and that `include_codebase=False` still leaves the *node's own* inherited copy intact even though that call's prompt excluded it. `conftest.py`'s `FakeLLM` gained a `received` list so tests can assert on actual prompt content, not just call counts.
- Where the content actually comes from, and the per-request on/off toggle's UI, are Phase 3 concerns (workspace root is a Phase 3 concept) — see Steps 3.6/3.7 below, now also implemented.

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

## Phase 3 — VS Code extension  (implemented 2026-09-26; real bugs found and fixed in a live Extension Development Host session on 2026-09-27 — see note below)

**Why this slot in the sequence:** only needs Phase 1's `/branch`/`/continue`/`/session` endpoints and Phase 2's score badges — nothing here depends on cross-node querying or the git-merge/conflict machinery. Building it now means Phases 4–6 get IDE exposure automatically as they land, instead of retrofitting IDE support after the fact. The one deliberate deferral: "apply to file" starts as a naive full-file write in this phase (no conflict detection) and gets upgraded in Phase 5 to reuse `git_engine.py`'s real conflict detection against the live file, once that exists.

**Decisions on record (2026-09-26):**
- IDE target: **VS Code only** for now (richest extension API, webview support for reusing the existing React app; JetBrains explicitly out of scope — see CLAUDE.md).
- Interaction model: **both** — a full embedded tree panel (webview hosting the existing canvas/inspector/prompt-bar) *and* a lightweight inline quick-pick command for fast asks without opening the panel.
- Relationship to the standalone web app: **complements it, doesn't replace it** — the web app stays for the portfolio demo/YouTube video/full-tree-at-a-glance use case; the extension is a second, more practical client for real day-to-day coding, talking to the exact same local FastAPI backend.

**3.1 Schema: file context on TreeNode**
- [x] Added `target_file: Optional[str] = None` and `target_range: Optional[Dict[str, int]] = None` to `TreeNode` (after `created_at`, so dataclass field ordering with defaults stays valid). Same flagged-addition pattern as `Branch.score_source` in Phase 2.
- [x] `node_to_dict`/`node_from_dict` round-trip both fields via `asdict`/`TreeNode(**d)` — no special-case code needed, the defaults handle missing keys on their own
- [x] `tests/test_models.py`: round-trip with and without file context, and a dict with the keys deleted entirely (simulating a pre-Phase-3 session) still loads; `tests/test_session.py`: a full pre-Phase-3 session *file* (not just a dict) still loads

**3.2 Backend: apply-to-file endpoint**
- [x] `POST /apply-to-file {node_id}` — writes `code_snapshot` to `target_file` (full-file replace, no conflict detection yet)
- [x] `POST /workspace {root}` (new, not originally listed as its own step but required to make path-safety possible) — the extension declares its workspace root here on activation; rejects a non-directory `root`
- [x] Refuses (400) a node with no `target_file`; refuses (400) when no workspace root has been declared yet
- [x] Path safety: resolves the target against the workspace root and checks containment via `Path.relative_to`, refusing (400) anything outside it — including `../` traversal
- [x] `/branch` and `/continue` extended to accept optional `target_file`/`target_range` and thread them through `Orchestrator.branch`/`continue_from` into every node they create (needed for 3.4 below — not spelled out in the original step list, but 3.4 can't set `target_file` on IDE-created nodes without it)
- [x] `tests/test_api.py`: correct write, missing-target rejection, no-workspace rejection, path-traversal guard, unknown-node 404, plus the `/branch`/`/continue` extension itself (sets file context, and confirms it stays `None` when not passed — web-app behavior unchanged)

**3.3 VS Code extension shell**
- [x] `vscode-extension/` — TypeScript, VS Code Extension API 1.85+, `package.json` manifest, `tsconfig.json`. `npm install && npx tsc -p ./` compiles clean (verified — real compile, not just written and assumed)
- [x] `src/api.ts` — typed HTTP client mirroring `main.py`'s contracts exactly
- [x] Talks to the same `localhost:8000` backend, no second backend process
- [x] Command **"Branch-Out: Open Tree Panel"** (`src/treePanel.ts`) — a webview loading `frontend/dist/index.html` unmodified, rewriting only its root-relative asset `src`/`href` attributes to webview-resource URIs (external URLs, e.g. Google Fonts, left untouched by the same regex). Shows a clear error instead of a blank panel if `frontend/dist` doesn't exist yet.
- **Real bug caught before it shipped:** the webview's CSP was written narrower than the app actually needs — checked against the *real* built `index.html` (not assumed) and found it loads Newsreader/Space Grotesk/JetBrains Mono from `fonts.googleapis.com`/`fonts.gstatic.com`; the first CSP draft would have silently blocked all of it. Fixed by explicitly allowing those two hosts in `style-src`/`font-src` alongside `connect-src http://localhost:8000 ws://localhost:8000` for the backend calls.

**3.4 Inline quick-pick command**
- [x] Command **"Branch-Out: Ask about selection"** (`src/quickPick.ts`) — reads active file path + selection (converted to 1-indexed `start_line`/`end_line`) or cursor position
- [x] Quick-pick: "Just answer" → `/continue`, "Explore 3 approaches" → `/branch`, via `vscode.window.withProgress` so the UI shows it's working
- [x] Results in a VS Code QuickPick (`reasoning` as label, first two code lines as detail); picking one — or the single "Just answer" result — calls `/apply-to-file` after an explicit confirm step
- [x] Nodes created this way always pass `target_file`/`target_range` through to `/branch`/`/continue` (see 3.2)

**3.5 Webview ↔ extension bridge**
- [x] `frontend/src/vscodeBridge.ts`: `isVsCodeWebview()` detects the injected `acquireVsCodeApi` global VS Code puts in every webview and nothing else does
- [x] `NodeInspector` gains an "Apply to file" action, shown only when `isVsCodeWebview() && node.target_file !== null`
- **Second bug caught before it shipped:** the apply-result handler originally used `window.alert()` for success/failure. That's the *exact* failure mode already fixed once this session for `window.prompt()` (native dialogs silently blocked in embedded/iframe-like contexts) — and this action runs specifically inside a VS Code webview, the most likely place for it to bite. Replaced with an inline `.apply-status` banner (new `useTree.applyToFile`, new `App.tsx` state) instead of any native dialog.

**Milestone:** inside VS Code, select code (or place the cursor), run "Branch-Out: Ask about selection", type a prompt, see a quick-pick of approaches, choose one, watch it land in the real file. Separately: open the full tree panel inside VS Code and interact with it exactly as the standalone web app.

**Verification status:** backend (96/96 tests passing, including 3.1/3.2's new coverage) and the frontend bridge (`tsc`/`vite build` clean) are verified the same way every other phase has been. The extension itself is verified by real compilation (`tsc -p ./`, zero errors) and by checking its CSP/asset-rewrite logic against the actual built `frontend/dist/index.html` rather than an assumed shape.

**Real bug found by the user, same day: F5 did nothing.** Root cause: I'd built the extension without `.vscode/launch.json`/`.vscode/tasks.json` — standard boilerplate for any real VS Code extension project, which I skipped. Without a launch config, VS Code has no defined action for F5 on this folder; at best it needs an easy-to-miss "select environment" prompt, at worst it silently no-ops, which is what happened. **Fixed:** added `.vscode/launch.json` (an `extensionHost` launch config with `--extensionDevelopmentPath`) and `.vscode/tasks.json` (the default build task running `npm run compile` as `preLaunchTask`), so F5 now compiles and launches an Extension Development Host deterministically.

**Real bug found by the user, same day: F5 (via Fn+F5, since plain F5 hit macOS's Dictation shortcut) launched the Extension Development Host correctly, "Open Tree Panel" opened and rendered (CSP/font fix from earlier held up), but showed an empty tree with "Failed to fetch."** Root cause, confirmed (not backend-down — checked, it was reachable via curl the whole time): `main.py`'s CORS policy allowlisted only `http://localhost:5173` (the standalone web app's Vite dev-server origin). A VS Code webview's actual origin is `vscode-webview://<random-id>` — nothing like that was ever allowlisted, so every fetch from the embedded panel was silently CORS-rejected, which browsers surface as an opaque "Failed to fetch" with no explanation. **Fixed:** `allow_origins=["*"]` — this is a local-only, single-user dev tool with no cookies/credentials, so a wildcard is the correct fix rather than trying to pattern-match VS Code's webview origin format (not guaranteed stable across versions). Regression test added (`test_cors_allows_arbitrary_origins_including_vscode_webview`, sends a `vscode-webview://` `Origin` header, asserts it's accepted) — 97/97 tests pass. Verified live: the already-running `--reload` server picked up the fix automatically, confirmed via a real `curl` with that exact Origin header.

**Also reported, not yet root-caused: "Ask about selection" doesn't appear in the Command Palette**, even though it's declared in `package.json` identically to "Open Tree Panel" (which does appear and works) and Command Palette entries are sourced from that static manifest regardless of activation state. Resolved itself without a code change — the command did run in the user's next attempt, so nothing further tracked here.

**Serious real incident, same day: "Ask about selection" overwrote the user's real `README.md` with placeholder garbage.** Root cause, confirmed: `quickPick.ts` (Step 3.4) captured `target_file`/`target_range` as bookkeeping metadata only — it never actually read the selected text or file content and included it in the prompt sent to the backend. So the LLM call carried a bare task description with **zero real code attached**; the model correctly replied "No file content was provided to summarize," and `/apply-to-file`'s naive full-file overwrite (Step 3.2, no conflict detection by design until Phase 5) then wrote that placeholder response straight over the real file. Two compounding gaps, not one: no real context was ever sent, and there was no safeguard stopping a contextless response from blindly clobbering real content.

This escalated into a scope discussion the user resolved directly (2026-09-27): Branch-Out's actual value proposition is Copilot Chat's linear conversation replaced with a tree — amend or branch from any point without disturbing what's already built on top — and that only means anything if the tree is grounded in the real project, the way Copilot already is by default. The current build regressed *below* that baseline (zero context) rather than adding to it. Decisions made:
- User gets an explicit **choice**, per request, whether a given ask pulls in the whole codebase (not silently always-on, not silently always-off).
- The **backend** gathers codebase content itself (recommended over having the extension transmit file trees over HTTP) — it already knows the workspace root via `POST /workspace`, so this centralizes "what counts as the codebase" (`.gitignore`, size limits, binary exclusion) as one piece of logic instead of two, and avoids shipping potentially large content through request bodies.
- Root gets it by default (see Phase 1's amendment above); "give the user a choice" applies to *ad hoc* inclusion/exclusion per request and to explicit refreshes, not to whether root starts grounded at all.

**New steps, all implemented 2026-09-27:**

**3.6 Backend: gather codebase context**
- [x] New `backend/codebase_context.py`, `gather_codebase_context(root: Path) -> str`. `POST /workspace` (already existed from 3.2, now `async def`) calls it via `asyncio.to_thread` and writes the result onto the root node via `set_codebase_context`.
- [x] Skips `.git`, `node_modules`, `__pycache__`, `.venv`/`venv`/`env`, `dist`/`build`/`out`, and 9 more common build/tooling dirs; skips images/archives/fonts/media/executables/`.pyc`/`.db`/`.lock` by extension; respects a root-level `.gitignore` (fnmatch-based, no negation, no nested `.gitignore` files — documented limitation, not a full git-aware indexer); skips any file over 100KB; hard-caps the whole snapshot at 300K chars with a truncation marker.
- [x] `tests/test_codebase_context.py`: 8 tests — real fixture directory in, expected chunk headers/content out; build/VCS dirs excluded; binaries excluded; `.gitignore` respected (both a filename pattern and a whole ignored directory); empty directory; unreadable/non-UTF-8 file never raises; oversized file skipped while a normal sibling still appears; total-size cap enforced with the truncation marker present.
- **Real bug caught by its own test:** `test_respects_gitignore` initially asserted the bare string `"utils.py"` wasn't in the snapshot — but `.gitignore`'s own file content (which necessarily *lists* the pattern `utils.py`) got included in the snapshot as an ordinary text file, so the substring appeared anyway and the test failed for the wrong reason. Fixed the test to assert on the specific chunk header (`"--- utils.py ---"`) and real file content instead of a bare filename substring.

**3.7 Per-request choice + refresh**
- [x] `/branch` and `/continue` gained `include_codebase: bool = True` on their request bodies, threaded through to `Orchestrator.branch`/`continue_from` (see Phase 1 amendment above) — off excludes it from that one prompt without touching the node's own inherited value.
- [x] Refresh: no new endpoint needed — calling `POST /workspace` again re-walks the (same or a new) root and updates root's `codebase_context` going forward via the same `set_codebase_context` call. Never rewrites already-generated descendant nodes (append-only holds — this only changes what *new* nodes inherit from root from that point on).
- [x] Tests (`test_api.py`): `test_include_codebase_false_leaves_prompt_without_it_but_node_still_inherits`; `test_calling_workspace_again_refreshes_codebase_context`; plus `test_workspace_gathers_codebase_context_onto_root` and `test_new_nodes_inherit_codebase_context_from_root` covering the base gather/inherit path.

**3.8 Fix quickPick.ts: send real content**
- [x] `askAboutSelection()` now reads the real selected text via `editor.document.getText(selection)` (or the whole file's text via `editor.document.getText()` when there's no selection) and folds it directly into the composed prompt string sent to `/continue`/`/branch` — this is the direct fix for the README incident: the LLM now always receives real code content, never just path/line metadata.
- [x] A third quick-pick step, "How much context should Branch-Out use?" (*Just this file* vs. *Include the whole codebase*), surfaced right after the answer-mode choice; its boolean result is passed straight through as `include_codebase` to `api.branch()`/`api.continueFrom()`, both of which gained that parameter (default `true`) and now send `include_codebase` in their request bodies. `api.ts`'s `TreeNodeData` also gained `codebase_context: string | null` for schema consistency with the backend.
- [x] Verified by real compilation (`npm run compile` / `tsc -p ./`, zero errors) — not yet re-run through a live Extension Development Host session against the real API (that's a user-driven manual step, same as the rest of Phase 3's real-world verification).

**3.9 Apply-to-file safety net — pulled forward from Phase 5, not deferred any further**
- [x] New ephemeral, in-memory `_source_hashes: Dict[str, Optional[str]]` in `main.py` — deliberately **not** part of the persisted `TreeNode` schema, since it's a stopgap meant to be deleted once Phase 5's real git-merge conflict detection lands. A node's target file's hash is captured (`hashlib.sha256`) the moment the node is created (in `/branch`'s `on_branch_ready` callback and in `/continue`).
- [x] `/apply-to-file` now checks, for any node with a recorded hash, whether the file's current on-disk hash still matches; a mismatch raises `HTTPException(409, ...)` with a clear message rather than silently overwriting. A node with no recorded hash (e.g. loaded from an older session, or never had a target file at creation) fails open — no history to check against, so it's allowed through, consistent with CLAUDE.md's fail-open discipline.
- [x] On a successful apply, `_source_hashes[node_id]` is updated to the newly-written file's hash, so re-applying the same node again immediately still works.
- [x] Tests (`test_api.py`): `test_apply_to_file_refuses_when_file_changed_since_generation` (409 + message); `test_apply_to_file_succeeds_when_file_unchanged_since_generation`; `test_apply_to_file_allows_reapplying_after_a_successful_apply`; `test_apply_to_file_allows_writes_for_nodes_with_no_hash_history` (fail-open case).
- Frontend: existing generic error-surfacing (`App.tsx` already extracts `.detail` from any non-2xx JSON response) displays the 409's message correctly with no special-casing needed — confirmed by reading that code path, not just assumed.

**Verification (2026-09-27):** full backend suite at 120/120 passing (up from 96 before this batch); `vscode-extension`'s `tsc -p ./` compiles clean; `frontend`'s `tsc && vite build` builds clean. Not yet re-run against the real Claude API or a live Extension Development Host — that remains a user-driven manual verification step, consistent with how the rest of Phase 3 was verified.

---

**Real bugs found by the user in a live Extension Development Host session, same day (2026-09-27), and fixed:**

**Bug 1 — stale workspace root across VS Code windows.** `extension.ts` only ever called `setWorkspace()` once, at cold `activate()`. The backend is a single shared process across every VS Code window (deliberate single-session POC scope, see CLAUDE.md), so whichever window activated most recently silently controlled `_workspace_root` for every window, forever after. Symptom: `/apply-to-file` rejected a real file in the user's actually-open `tokenetics` folder with `target_file '...' is outside the workspace root` — confirmed via `AskUserQuestion` that `tokenetics` genuinely was the open folder, ruling out the alternate (correct-behavior) explanation of navigating outside the workspace via "Go to Definition". **Fix:** new `api.ensureWorkspaceDeclared()`, which re-reads `vscode.workspace.workspaceFolders?.[0]` and re-declares it fresh; called at the top of both `askAboutSelection()` and `openTreePanel()`, so whichever window a command actually runs from always wins for that command, rather than trusting a one-time declaration made by whichever window happened to activate last. Reproduced the exact failure and the fix directly against the real backend (declared a fake "other window" root, confirmed a real file under a different folder got rejected identically to the user's report; then re-declared the real folder and confirmed the same path resolved correctly, while the stale folder's path still correctly failed).

**Bug 2 — a plain question mutated the file.** User report: *"the answer changes the file even if it is just a question about what the file is."* Root cause: every quick-pick mode sent the same prompt shape, forcing the model to always reply with `{"code": ..., "reasoning": ...}`, and every reply — including a plain question's prose explanation — was offered through the same "Apply this to the file?" flow. A question like "what is this file?" had its answer stored as `code` and dangled in front of the user as something to apply over their real file: the same failure shape as the original README incident, just one step further from triggering it. **Fix:** `quickPick.ts` now has three modes instead of two — **"Ask a question"** (new), **"Change the code"**, **"Explore 3 approaches"**. Question mode sends no `target_file`/`target_range` at all (so the resulting node can never be applied, from the quick-pick or from the tree panel's Apply button), instructs the model via an explicit prompt suffix to put its answer in `reasoning` and leave `code` unchanged, and opens the answer in a read-only side tab instead of an apply-confirmation dialog. Verified against the real API: prompt sent, `code_snapshot` came back byte-identical to the original input, and the real explanation landed correctly in `reasoning`/`context_summary`'s `Approach:` line.

**Bug 3 — "Change the code" on a selection silently destroyed the rest of the file.** Deeper root cause behind Bug 2's symptom, found while fixing it: `/apply-to-file` always wrote `code_snapshot` as a full-file replace (`main.py`'s original `target.write_text(node.code_snapshot)`), even for a node created from a *selection*. A node created from a selection only ever holds that selection's code — the model is never shown the rest of the file — so applying it as a full-file overwrite silently destroyed everything outside the selection. This is the same class of damage as the README incident, just triggered by "Change the code" on a partial selection instead of by a contextless question. **Fix:** new `_splice_range()` in `main.py` — when a node carries a `target_range` (1-indexed, inclusive, exactly as `quickPick.ts` derives it from a VS Code `Selection`), `/apply-to-file` reads the current file, splices `code_snapshot` into just those lines, and writes the rest back untouched; a node with no `target_range` (e.g. "whole file, no selection" or a web-app node) still gets the original full-file replace. Tests: `test_apply_to_file_with_target_range_splices_instead_of_overwriting` (pins exact spliced output against a 5-line fixture), `test_apply_to_file_without_target_range_still_overwrites_whole_file` (confirms the no-range path is unchanged). Verified against the real API too: a 3-function file, selecting only the middle (unimplemented) function, asking to implement it — the model correctly returned just that function, `/apply-to-file` spliced it in, and the other two functions came back on disk byte-for-byte untouched. 122/122 backend tests passing (up from 120).

**Known remaining gap, not yet fixed (deliberately deferred, see CLAUDE.md's Scope discipline):** only `vscode.workspace.workspaceFolders?.[0]` is ever considered — a multi-root VS Code workspace with more than one folder open is not supported; the extension always treats the first folder as *the* workspace. Not building this now: out of scope for the POC (single-session, single-workspace-root, same as the backend's own `_workspace_root` global). If genuine multi-root usage becomes a real workflow, this would need either a per-folder workspace declaration or a folder-picker in the quick-pick — worth a real decision then, not a silent assumption now.

---

**Token-cost finding and fix (2026-09-27): `codebase_context` was being resent, uncached, on every single call.** User flagged that sending the whole codebase as context per node would be too token-heavy — investigated whether Tokenetics (already wired since Phase 1 specifically to manage this) was actually helping. It wasn't, for two independent, verified reasons:
1. **Wrong field.** Tokenetics' `cache_breakpoint_optimizer` stage can only ever anchor a `cache_control` marker on `request.system` or `request.tools` (confirmed by reading the stage itself) — but `codebase_context` was folded into the per-call **message** content (`branch_generator.py`'s `codebase_section` prefix), a field this stage has no mechanism to cache at all.
2. **Missing usage history.** Even with correct placement, the stage refuses to place a breakpoint without a caller-supplied `cache_usage_history` (≥2 prior calls with a matching stable-prefix hash) — conservative by design, won't guess without evidence. `claude_client.py` never passed this config at all, so the stage always hit `insufficient_usage_history` and bailed.

**Verdict on Tokenetics itself: not broken.** Both behaviors are exactly what its own (extensive) docstrings describe as deliberate, conservative design — including a documented real bug it caught and fixed in *itself* around anchor placement. The gap was entirely on Branch-Out's integration side, not in Tokenetics.

**Fix, implemented and verified:**
- [x] `branch_generator.py`: `codebase_context`, when present, now goes into `system` (the only cacheable field) instead of the message. The per-call-varying "approach N of M" instruction moved the *other* way, into the message — it changes every call anyway and was never a valid caching candidate; mixing it into `system` would have broken the exact-prefix match caching depends on.
- [x] `claude_client.py`: new `self._cache_usage_history: list[dict]`, held and mutated in place (appended to after every `complete()` call, using the same `stable_prefix_hash` Tokenetics' stage uses internally), passed into `Tokenetics(stage_config={"cache_breakpoint_optimizer": {"cache_usage_history": ...}})` at construction so the stage sees it grow across the client's whole lifetime.
- [x] Tests: `tests/test_orchestrator.py`'s existing `include_codebase` tests updated to assert on `system` instead of `messages` content; new `tests/test_claude_client.py` (5 tests, real Anthropic SDK and Tokenetics' network-calling `prepare()`/`finalize()` both mocked, per CLAUDE.md's "must not require the API key" rule) covering: history starts empty, a call appends one correctly-hashed entry, repeated identical `system` hashes identically (the exact condition the stage needs), `system=""` and `system=None` hash the same (both mean "no codebase context"), and the stage_config list is the *same* object (not a copy) so it actually grows live. 127/127 backend tests passing (up from 122).
- **Real-API verification, 4 calls, ~18KB shared fixture (comfortably over `claude-sonnet-4-6`'s 1024-token minimum cacheable prefix):** raw `response.usage` from each real call —

  | Call | Breakpoint placed | `cache_creation_input_tokens` | `cache_read_input_tokens` | `input_tokens` |
  |---|---|---|---|---|
  | 1 | no | 0 | 0 | 5291 |
  | 2 | no | 0 | 0 | 5291 |
  | 3 | **yes** | 5271 (write) | 0 | 20 |
  | 4 | **yes** | 0 | 5271 (read) | 20 |

  Exactly the intended mechanism: calls 1–2 build history (no guessing without evidence), call 3 pays the one-time write premium, call 4 reads it back at 0.1x the base price instead of paying full price a third time. Real, measured proof — not a structural inference.

**What this fix does and doesn't solve, honestly:** it eliminates the *repeat* cost of an unchanged `codebase_context` within a session (after the first couple of calls, most of the token volume is billed at 0.1x). It does **not** reduce the *first-call* cost, and a cold cache (session restart, or `codebase_context` changed via a `/workspace` refresh) pays the write premium again. The deeper fix — not resending the whole codebase at all, only a relevance-scoped subset — is proposed separately below as a design direction, not yet built.

**Graph-based context selection — implemented and verified (2026-09-27).** A static import/dependency graph over the codebase's own files (no embeddings, no external services — same deterministic, dependency-free style as `codebase_context.py` itself), selecting a relevant *subgraph* per request instead of the whole snapshot. Deliberately scoped smaller than full GraphRAG (no indexing/embedding/summarization pipeline over the whole corpus — a genuinely larger feature, arguably its own phase); this is graph-based in the sense that matters here — relevant-subset selection via real structural relationships — without the ML/infra overhead.

- [x] New `backend/codebase_graph.py`: `parse_snapshot_chunks()` splits a gathered snapshot back into `{relpath: content}` (round-trips `gather_codebase_context()`'s exact chunk format); `build_import_graph()` walks each file's real imports into an undirected adjacency map (either direction — importer or imported — indicates relevance); `select_relevant_subset(snapshot, target_file, depth)` returns `target_file` plus its graph-neighbors out to `depth` hops, in the same chunk format, or `None` if `target_file` isn't found in the snapshot at all (fails open to the full snapshot rather than sending nothing).
- [x] Python imports resolved via `ast` (absolute dotted, package `__init__.py`, relative `from . import x`, and this project's own flat single-file style like `from models import X`) — robust to real code, not regex-guessed. JS/TS relative imports (`from './x'`, `require('./x')`) resolved via a conservative regex with extension-guessing (`.ts`/`.tsx`/`.js`/`.jsx`/`index` variants) — bare package imports (`import requests`, `from 'react'`) are never matched against local files, since a substring match there would be a real correctness bug (confirmed with a dedicated test: `requests` doesn't spuriously match `requests_helper.py`).
- [x] Wired into `orchestrator.py`: new `_codebase_context_for_prompt()` helper, used by both `_branch_unlocked` and `_continue_from_unlocked`. `include_codebase=False` → still `None` (unchanged). No `target_file` → still the full snapshot (unchanged; nothing to scope a subgraph around, e.g. a tree-panel question with no file focus). `target_file` set → the graph-scoped subset. Depth fixed at 1 hop (`_CODEBASE_GRAPH_DEPTH`, a documented constant, not an API parameter — not worth a request-schema field for a POC yet). Critically: the **node's own** `codebase_context` (what children inherit) is untouched by this — always the full snapshot, exactly as the Phase 1 amendment established; only what one call's *prompt* sees is scoped.
- [x] Tests: `tests/test_codebase_graph.py` (13 tests — snapshot round-trip, truncation-marker skipping, Python absolute/dotted/relative import resolution, JS relative imports, bare-package-never-matches-local-file, an unparseable file fails open without breaking edges *into* it from other files, depth-1 vs depth-2 subgraph selection, absolute-path `target_file` matching, unknown-`target_file` fail-open, output round-trips through the parser cleanly); `tests/test_orchestrator.py` (5 new tests — scoped subset reaches the real LLM call via `system`, no-`target_file` still sends everything, unknown `target_file` falls back to full snapshot, every parallel `/branch` call gets scoped consistently, the node's own inherited `codebase_context` stays full even when that call's prompt was scoped). 145/145 backend tests passing (up from 127).
- **Real bug caught by its own test:** the first draft of Python import resolution only handled `ImportFrom.module` (e.g. `from pkg import x` → resolves `pkg`), missing the case where the module itself is empty and the imported *name* is the actual submodule file (`from . import b`, common in real relative-import style) — `test_python_relative_import_resolves` failed until `_extract_python_imports` was changed to also try `module.name` (and bare `name` when module is empty) as its own path candidate per imported name, not just the module path alone.
- **Real-API verification:** a 3-file fixture — `main.py` imports `utils.py` (defines `CONNECTED_MARKER`), `unrelated.py` (defines a different, unimported `ISOLATED_MARKER`) has no import relationship to either. Asked the real model, with `target_file=main.py`, for both markers' values. Real response: correctly reported `CONNECTED_MARKER`'s actual value (its graph neighbor, correctly included) and explicitly stated `unrelated.py` "was not provided" (correctly excluded, not hallucinated) — real, measured proof the scoping changes what the model can actually see, not just a structural inference from the code.
- **Honest limits, not yet addressed:** depth is fixed at 1 and not currently tunable per request; a request with no `target_file` (most tree-panel questions today) still gets the full snapshot — this only helps the IDE-originated, file-scoped case, not the general one. A file that imports very broadly (a hub/index file) could still pull in a large neighbor set at depth 1 — no size cap on the scoped subset itself, only whatever `gather_codebase_context`'s existing per-file/total caps already impose upstream.

---

**Real UX finding from a manual test (2026-09-27), fixed: the tree panel's inspector made a question-mode node look like a code change.** User ran two real "Ask a question" turns via the quick-pick and inspected the resulting nodes in the embedded tree panel. Two things reported, checked directly against real session data and the actual frontend code rather than guessed at:
1. *"Node 1 says Branch A instead of being labeled by the question"* — first pass concluded "working as designed" (the tag vs. the derived title), but the user then confirmed it was genuinely the **primary title** on the tree card showing "Branch A", not the small tag. Re-investigated and found a real bug: `deriveNodeTitle()` split `context_summary` on `"\n---\n"` (the turn separator) and took the last chunk — but a long question-mode answer routinely contains its own markdown horizontal rules, also `"\n---\n"`, as section dividers. The real stored data for this exact node had **5** such occurrences inside the model's own answer; splitting on it grabbed whatever came after the *last* coincidental `"---"`, landing inside the answer's "## Summary" section with no "Approach:" label left in it — the regex failed to match, and it silently fell back to `branch_label` as if that were the real title. **Fix:** `deriveNodeTitle()` no longer splits on `"\n---\n"` at all — it finds the *last* `"\nApproach: "` marker directly (our own `_extend_summary` template guarantees exactly one per real turn, immediately after `"Task: ..."`) and takes everything after it, which is robust no matter how much `"---"` the model's own prose happens to contain. Verified by replaying the exact buggy stored `context_summary` through both the old and new logic: old logic → no match, fell back to "Branch A"; new logic → `"This file is a Phase 8…"` / `"Phase 8 is the assembly and…"`, correctly derived for both real nodes.
2. *"Node 2 shows code — that wasn't the question"* — real finding, confirmed: `code_snapshot` was correctly left byte-identical to the parent's (question mode's fix from earlier this session working correctly), but `NodeInspector.tsx` unconditionally rendered a full "Code snapshot" block for every node regardless, making an untouched file look like a code change happened.

**Fix:** `NodeInspector` now takes the full `nodes` list (mirroring `TreeCanvas`'s existing pattern) to look up the selected node's parent and compare `code_snapshot`s. When they're byte-identical, the "Code snapshot" section starts collapsed with a "· unchanged from parent" note and a show/hide toggle, instead of always being fully expanded — still reachable, just not visually dominant on a node where nothing code-related happened. `App.tsx` passes `key={tree.selected.id}` on the inspector so the collapse state resets cleanly per node instead of leaking across selections. Presentation-only (both fixes), no backend change; `frontend`'s `tsc && vite build` builds clean twice over (once per fix), backend's 145 tests unaffected. Not unit-testable (no JS test runner set up in this project, same as every prior frontend-only fix) — verified instead by replaying the real buggy stored data through the old and new logic side by side, same standard as the rest of this session's real-API checks.

---

## Phase 4 — Cross-node querying  (renumbered from Phase 3 on 2026-09-26; design amended 2026-09-27, not yet built)

**Design amendment (2026-09-27), decided before this phase starts:** `get_relevant_context` was scoped to `decisions`/`code_diff`/`summary`/`preferences` — conversational history only, the same gap that caused Phase 3's codebase-context work. A query answered about a node without its `codebase_context` inherits the same "no real code context" failure mode that overwrote the user's README, just via a different code path (a query instead of a continue). `get_relevant_context` must include the queried node's inherited `codebase_context` alongside its conversational history, not just the ancestor decision log.

**4.1 Context retriever** (`context_retriever.py`)
- [ ] networkx `DiGraph`; nodes store `decisions`, `code_diff`, `summary`, `preferences`, and `codebase_context` (2026-09-27 addition — see above)
- [ ] `get_relevant_context(node_id)` walks ancestors only; falls back to `context_summary` on failure or empty subgraph; includes the node's `codebase_context` in the composed result
- [ ] Populate the graph whenever a `TreeNode` is created; serialise with the session
- [ ] Tests: 3-node chain, forced failure fallback, sibling branches isolated
- [ ] Test with 10+ node sessions (risk register)
- [ ] Test: a query answered about a node with real `codebase_context` set actually reflects that content in its answer (mock assertion on what reaches the LLM call)

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

**5.5 Upgrade Phase 3's file-apply (new step, added 2026-09-26; superseded 2026-09-27 — see note)**
- [ ] `POST /apply-to-file` now runs the same conflict detection as `/patch` against the real file's *current* on-disk content (which may have changed since the node's code was generated), not just a blind overwrite
- [ ] Conflict: same inline banner pattern as 5.4, surfaced in the VS Code extension
- [ ] Tests: clean apply, conflict against modified file, WebSocket event fires in the extension context too

**Note (2026-09-27):** Phase 3's naive apply-to-file actually overwrote a user's real file (see Phase 3's incident log) before this phase existed to protect it. Phase 3's Step 3.9 adds a minimal interim safeguard (hash/mtime-based change detection) now, rather than leaving the gap open for the full duration of Phases 4–5's build. This step remains the *real* fix — full git-merge conflict detection with a proper diff preview — Step 3.9 is a stopgap, not a replacement for it.

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
