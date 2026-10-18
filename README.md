# Branch-Out

A coding agent that turns an AI coding session into a **tree, not a chat log**: branch into multiple approaches at any point, backtrack to any past node without losing where you are now, and query a past decision without disturbing current state. Available as a standalone web app and as a VS Code extension (a Copilot-like, in-editor experience with a few extra features — a visualized, navigable history of every change).

> 📷 **Demo GIF goes here.** Record a ~15s loop of: open the Tree Panel in VS Code → ask a question → branch into 3 approaches → pick one, watch the file update → backtrack to an earlier node, watch the file revert. Drop it in `docs/demo.gif` and reference it here.

---

## Why a tree, not a chat

A normal AI coding assistant is a straight line: every new message replaces the context of the last. Explore three different approaches to the same problem, and you're stuck re-explaining context in three separate conversations, or silently overwriting whichever one you tried first. Branch-Out keeps every approach as its own node — branch, compare, pick one, and the others are never lost, just dimmed until you want them again.

## What it actually does

- **Branch**: ask for 3 (or N) approaches to the same prompt in parallel; each becomes its own node, scored automatically (test-based when the code has tests, LLM-judged otherwise).
- **Backtrack**: jump to any past node — the active cursor moves there, everything off that path dims (orphaned, never deleted), and anything revisited revives instantly.
- **Query**: ask a past node a question without touching the currently active node — the core thing a tree gives you over a linear chat.
- **Clarify-and-apply / clarify-and-branch**: answer a clarifying question on a past node, then either apply that clarification onto the current node (with real git-based conflict detection) or spawn a fresh branch from it with accumulated preferences folded in.
- **Merge sibling branches**: pick two of the branches from one fan-out and have the model synthesize a single combined solution — not a pick, a real synthesis of both.
- **File-grounded, VS Code-aware**: inside the IDE, Branch-Out knows your active file and selection live; activating a node (backtrack, continue, merge) writes straight to the real file on disk, with a conflict banner instead of a silent overwrite when the file has moved on independently.
- **Auto-classifying intent**: a plain "Ask" doesn't force you to pre-declare whether you're asking a question or requesting a code change — the model classifies itself, and an explicit override always wins over its guess.

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

- **Backend** (`backend/`): FastAPI app, one in-memory `TreeStateManager` (append-only — a node is never mutated after creation, only `status`/`children_ids` change), `Orchestrator` serializing all tree-mutating calls behind a single `asyncio.Lock`. Branch generation is always parallel (`asyncio.gather`); evaluation always fires after branches are already visible, never blocking on it.
- **Frontend** (`frontend/`): React + React Flow for the tree canvas, a node inspector (Queries/State tabs), a WebSocket for live `branch.ready`/`score.ready`/`active_editor.changed` events.
- **VS Code extension** (`vscode-extension/`): embeds the same frontend build in a webview, plus a quick-pick command for a fast, inline ask-or-branch flow without opening the full panel. Reports the real active editor/selection into the backend's shared session state, so the embedded webview and a standalone browser tab both see the same "currently targeting" file.
- **Benchmark** (`benchmark/`): a runner comparing two linear baselines (generic prompt, and the same context/prompt-engineering Branch-Out itself gets, single call) against the full branch-and-pick flow, scored against each task's own test suite.

## Benchmark

Real run against the locked 15-task set (10 standard + 5 custom), real Claude API calls, scored against each task's own `test_suite` — never a single flat percentage, always per mode and per category. Full methodology and the two real bugs found while getting honest numbers (an auth-loading gap in the runner, and a code-extraction bug that only showed up once the model answered in markdown prose instead of a fenced block) are in `ROADMAP.md`'s Phase 6.1.

| Mode | standard pass@1 | custom pass@1 |
|---|---|---|
| baseline-generic (raw prompt) | 1.0 | 0.8 |
| baseline-matched (same context, single call) | 0.2 | 0.6 |
| **Branch-Out** (branch + auto-pick best of 3) | **1.0** | **0.8** |

The interesting result isn't that Branch-Out wins outright — on this task set, auto-picking the best of 3 branches matches `baseline-generic`'s ceiling and clears `baseline-matched` by 4x. The gap between the two baselines is itself a real, reproducible finding: forcing code through a compact, single-line JSON-escaped response measurably degrades this model's correctness even on trivial tasks (confirmed via a captured real response with collapsed indentation causing a plain `IndentationError`) — exactly the kind of single-attempt fragility that branching exists to recover from.

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
# Backend + benchmark (247+ tests, no API key needed — everything mocks the LLM client)
python3 -m pytest

# Frontend typecheck/build
cd frontend && npm run build

# E2E (real browser, real server, zero API cost — see backend/tests/e2e_server.py)
cd frontend && npm run e2e

# Benchmark against the real API (costs real money — see benchmark/runner.py)
cd backend && python3 ../benchmark/runner.py --baseline-generic --baseline-matched --branchout
```

## Project discipline

This is a solo portfolio project built with an unusually explicit paper trail: every phase, every real bug found via manual or automated testing, and every architectural decision (with the reasoning behind it) is recorded in `ROADMAP.md`. `CLAUDE.md` holds the non-negotiable constraints (append-only tree, async branch generation, fail-open discipline, etc.) that every change is checked against.
