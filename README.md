# Branch-Out

**A coding agent where exploring three approaches costs nothing — each one keeps its own history, and the other two are never lost.**

Every normal AI coding assistant is a straight line: each new message overwrites the context of the last. Try three approaches to the same problem and you're stuck re-explaining context across separate chats, or silently clobbering whichever one you tried first. Branch-Out keeps every approach as its own node in a tree — branch, compare, pick one — the rest just dim until you want them back.

Available as a standalone web app and as a VS Code extension (Copilot-like, in-editor, with a visualized navigable history of every change).

![Branch-Out demo — branching, picking, and backtracking inside VS Code](docs/demo.gif)

---

## What it does

- **Branch**: ask for 3 (or N) approaches to the same prompt in parallel; each becomes its own node, scored automatically (test-based when the code has tests, LLM-judged otherwise).
- **Backtrack**: jump to any past node — the active cursor moves there, everything off that path dims (orphaned, never deleted), and anything revisited revives instantly.
- **Query**: ask a past node a question without touching the currently active node — the one thing a tree gives you that a linear chat can't.
- **Clarify-and-apply / clarify-and-branch**: answer a clarifying question on a past node, then either apply it onto the current node (with real git-based conflict detection) or spawn a fresh branch with the accumulated preferences folded in.
- **Merge sibling branches**: pick two branches from one fan-out and have the model synthesize a single combined solution — a real synthesis, not a pick.
- **File-grounded, VS Code-aware**: inside the IDE, Branch-Out knows your active file and selection live; activating a node (backtrack, continue, merge) writes straight to disk, with a conflict banner instead of a silent overwrite if the file moved on independently.
- **Auto-classifying intent**: a plain "Ask" doesn't force you to pre-declare question vs. code-change — the model classifies itself, and an explicit override always wins over its guess.

<details>
<summary><b>Screenshots</b> (click to expand — one per feature above)</summary>

**Branch** — one prompt fans out into 3 scored approaches, each its own node:
![Branching into 3 approaches](docs/screenshots/01-branch.png)

**Node inspector** — pick a branch and its code, score, and query box open on the side; the other two dim:
![Node inspector on a picked branch](docs/screenshots/02-inspector.png)

**Continue** — a single follow-up grows the active path by one child node, not a new fan-out:
![A follow-up creates one child node](docs/screenshots/03-continue.png)

**Backtrack** — activating a different branch swaps which subtree is dimmed; nothing is ever deleted:
![Backtracking dims the other branch's subtree](docs/screenshots/04-backtrack.png)

**Query** — ask a past, dimmed node something without touching whatever's currently active:
![Querying a past node without disturbing the active one](docs/screenshots/05-query.png)

**Merge siblings** — combine two branches from the same fan-out into one synthesized node:
![Merging two sibling branches into one synthesized node](docs/screenshots/06-merge.png)

</details>

## What it is — and isn't

A tree-structured session manager that sits between you and the Claude API. It is **not** a multi-agent framework (one model, one call per branch), not a git wrapper (git is used internally only for conflict detection on file-apply, never exposed as a workflow), and branching isn't speculative execution of code — it's parallel *generation*, scored and shown to you before anything touches a file.

## Architecture

```
┌─────────────────┐        ┌──────────────────┐        ┌───────────────────┐
│   React (Vite)   │◄──────►│  FastAPI backend  │◄──────►│   Claude API       │
│  Tree canvas,     │  HTTP/ │  Orchestrator,    │        │  (claude-sonnet-   │
│  node inspector   │   WS   │  TreeStateManager │        │   4-6)             │
└──────┬───────────┘        └─────────┬─────────┘        └────────────────────┘
       │                               │
       │ same page, embedded           │  in-memory tree (networkx-based
       │ in a webview                  │  context graph + append-only
       ▼                               │  node store); git-based merge/
┌──────────────────┐                   │  conflict detection (gitpython);
│  VS Code          │                   │  SQLite session persistence
│  extension         │───────────────────┘  (save/load, not continuous)
│  (active file/     │
│   selection →       │
│   backend state)    │
└──────────────────┘
```

<details>
<summary><b>What each piece actually does</b> (click to expand)</summary>

- **Backend** (`backend/`): FastAPI app, one in-memory `TreeStateManager` (append-only — a node is never mutated after creation, only `status`/`children_ids` change), `Orchestrator` serializing all tree-mutating calls behind a single `asyncio.Lock`. Branch generation is always parallel (`asyncio.gather`); evaluation always fires after branches are already visible, never blocking on it.
- **Frontend** (`frontend/`): React + React Flow for the tree canvas, a node inspector (Queries/State tabs), a WebSocket for live `branch.ready`/`score.ready`/`active_editor.changed` events.
- **VS Code extension** (`vscode-extension/`): embeds the same frontend build in a webview, plus a quick-pick command for a fast, inline ask-or-branch flow without opening the full panel. Reports the real active editor/selection into the backend's shared session state, so the embedded webview and a standalone browser tab both see the same "currently targeting" file.
- **Benchmark** (`benchmark/`): a runner comparing two linear baselines (generic prompt, and the same context/prompt-engineering Branch-Out itself gets, single call) against the full branch-and-pick flow, scored against each task's own test suite.

</details>

## Benchmark

Every number below is **measured** against the real Claude API, scored against each task's own `test_suite` — never a single flat percentage, always per mode and per category. See `ROADMAP.md`'s Phase 6.1 for the task set and methodology.

| Mode | standard pass@1 (n=50) | custom pass@1 (n=30) |
|---|---|---|
| baseline-generic (raw prompt) | 0.94 | 0.967 |
| baseline-matched (same context, single call) | 0.38 | 0.767 |
| **Branch-Out** (branch + auto-pick best of 3) | **0.94** | **0.9** |

The interesting finding isn't that Branch-Out wins outright on `standard` — auto-picking the best of 3 branches exactly matches `baseline-generic`'s ceiling (0.94) and clears `baseline-matched` by nearly 2.5x. The gap *between* the two baselines is itself real and reproducible: forcing code through a compact, single-line JSON-escaped response measurably degrades this model's correctness even on trivial tasks (confirmed via a captured response whose collapsed indentation caused a plain `IndentationError`) — exactly the single-attempt fragility branching exists to recover from. On `custom` (the harder, hand-written tasks), Branch-Out lands between the two baselines rather than matching the generic ceiling — a real, reported result, not smoothed over.

## Install & run

Requires Python ≥3.10, Node ≥18, and an `ANTHROPIC_API_KEY`.

```bash
# Backend
cd backend
python3 -m venv ../.venv && source ../.venv/bin/activate
pip install -r ../requirements.txt
echo "ANTHROPIC_API_KEY=your_key_here" > .env
uvicorn main:app --port 8000 --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev   # open http://localhost:5173
```

### VS Code extension

```bash
cd vscode-extension
npm install
npm run compile
```

Then launch an Extension Development Host:

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
  --extensionDevelopmentPath=/absolute/path/to/vscode-extension \
  /path/to/a/workspace/to/test/in
```

(F5-based debugging has been unreliable in this project's own testing — the command above is the one path that's consistently worked.) Inside that window, run **Branch-Out: Open Tree Panel** from the Command Palette, or select some code and run **Branch-Out: Ask about selection** for the fast inline quick-pick flow.

### Tests

```bash
# Backend + benchmark (no API key needed — everything mocks the LLM client)
python3 -m pytest

# Frontend typecheck/build
cd frontend && npm run build

# E2E (real browser, real server, zero API cost — see backend/tests/e2e_server.py)
cd frontend && npm run e2e

# Benchmark against the real API (costs real money — see benchmark/runner.py)
cd backend && python3 ../benchmark/runner.py --baseline-generic --baseline-matched --branchout
```

## Project discipline

This is a solo portfolio project. `ROADMAP.md` tracks what was implemented, phase by phase. `CLAUDE.md` holds the non-negotiable constraints (append-only tree, async branch generation, fail-open discipline, etc.) that every change is checked against.
