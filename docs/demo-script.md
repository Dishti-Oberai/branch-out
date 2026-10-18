# YouTube demo script (5–8 min)

I can't record or narrate this myself — no camera/screen access — so this is a ready-to-shoot script instead. Follow the beats below; each one names exactly what to click and what to say. Record the VS Code Extension Development Host window (see README's "VS Code extension" section for the launch command) with a 2-3 file sample workspace already open (e.g. `constants.py` + `util.py` that don't import each other, so the multi-file beat later actually demonstrates something).

**Before recording:** have the backend running (`uvicorn main:app --port 8000 --reload`), the Extension Development Host open, and the Tree Panel already opened once so you're not fumbling with Command Palette on camera.

---

### 0:00–0:30 — Cold open, the problem

> "Every AI coding assistant I've used is a straight line — a chat log. Ask for two different approaches to the same problem, and you either run two separate conversations, or your second answer just overwrites your first. Branch-Out makes a coding session a tree instead."

Show the empty Tree Panel — just a root node.

### 0:30–1:30 — Branch into 3 approaches

Type a real prompt: *"write a rate limiter"*. Click **Explore 3 approaches**.

> "Three independent approaches, generated in parallel, each scored automatically once it's done — test-based when the code has tests, LLM-judged otherwise."

Point at the score badges populating live (they arrive async, after the branches are already visible — call that out, it's a real engineering detail: scores never block you from seeing the branches).

### 1:30–2:30 — Pick one, backtrack, nothing is lost

Click one branch → it opens in the inspector, click **Make active**. Ask a follow-up from it (single "Ask", not "Explore") — show exactly one new node appears, not a fan-out.

Click back to **Root**. Show the whole branch you just built dims (orphaned, dashed border) — not deleted. Click back into one of the dimmed nodes — it revives instantly.

> "Nothing is ever lost. Backtracking just moves where you are — every path is still there if you want it."

### 2:30–3:30 — Query a past node without disturbing current state

With a *different* node active, open an earlier, unrelated node and use **Ask this node a question** (not the main prompt bar). Ask something like *"why did you choose this approach?"*.

> "This is the actual point of a tree over a chat: I can ask a past decision a question, and whatever I'm currently working on doesn't move. In a linear chat, there's no such thing as 'ask the past something' without derailing the present."

### 3:30–4:30 — Inside VS Code: ask, apply, watch the real file change

Switch to `constants.py` in the editor. Run **Branch-Out: Ask about selection** (or use the panel's prompt bar with Auto intent). Ask for a real change — e.g. *"change MAX_RETRIES to 3"*. Show the file update on disk, live, no separate "Apply" click.

> "Activating a node is the write trigger — same semantics as a git checkout. If the file's moved on independently, you get a conflict banner, never a silent overwrite."

(Optional, if time: ask a change that's actually about `util.py` while `constants.py` is active — show Branch-Out correctly redirect to the right file instead of inventing an unused variable in the wrong one. This is a real, fixed bug worth 20 seconds if you have them.)

### 4:30–5:30 — Merge two sibling branches

Back in the tree, pick two of the three rate-limiter branches from earlier. Open one, switch to the **State** tab, pick the other as the merge target, click **Synthesize combined approach**.

> "This isn't picking one over the other — it's one LLM call seeing both approaches side by side and synthesizing a single combined solution."

### 5:30–6:00 — Close

> "Full source, the whole build log — every real bug found, every design decision and why — is in the repo. Link's in the description."

Show the GitHub repo URL on screen.

---

## B-roll / cutaway shot list (optional, if editing)

- The `ROADMAP.md` scrollback, showing the volume of real-bug entries — visual proof this wasn't a weekend toy.
- The benchmark table from the README.
- `pytest` output (247+ tests passing, green) — a quick flash is enough, don't dwell.
