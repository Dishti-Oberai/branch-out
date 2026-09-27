# Branch-Out — VS Code extension

Phase 3. Drives the same local Branch-Out backend the standalone web app
uses — this is a second client, not a second backend. See
`../ROADMAP.md`'s Phase 3 section for the design decisions and step list.

## Running it during development

1. **Backend must be running** on `localhost:8000` (`cd ../backend && ../.venv/bin/uvicorn main:app --port 8000 --reload`).
2. **Frontend must be built** (`cd ../frontend && npm run build`) — the tree panel loads `../frontend/dist/index.html` directly; it doesn't bundle or fork the React app. Nothing shows up until this exists.
3. `npm install && npm run compile` in this directory.
4. Open this `vscode-extension/` folder in VS Code and press F5. `.vscode/launch.json` + `.vscode/tasks.json` (added 2026-09-26 — F5 did nothing without them, a real gap in the initial build) define the "Run Branch-Out Extension" launch config and its build task, so F5 compiles and launches an Extension Development Host window automatically — no manual "select environment" prompt to notice.

## Commands

- **Branch-Out: Open Tree Panel** — the full tree UI (canvas, inspector, prompt bar) in a webview beside your editor. Same app as the standalone site.
- **Branch-Out: Ask about selection** — select code (or just place your cursor), run the command, type a prompt, choose "Just answer" or "Explore 3 approaches," pick a result if there were several, apply it to the file.

## What's NOT here yet

- Conflict detection on apply — `POST /apply-to-file` is a naive full-file overwrite for now. Phase 5 upgrades this to reuse `git_engine.py`'s real conflict detection against the file's current on-disk content.
- JetBrains or any other IDE — VS Code only, by design (see `CLAUDE.md`).
