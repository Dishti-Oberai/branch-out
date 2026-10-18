# I Gave My AI Coding Assistant a Tree Instead of a Chat Log

Every AI coding assistant I've used — Copilot, Cursor, the chat panel in my IDE — has the same shape underneath: a linear conversation. Every new message replaces the context of the last one. That's fine until you want to explore two different approaches to the same problem. Then you're stuck re-explaining context in two separate chats, or watching your first attempt get silently overwritten by your second.

So I built **Branch-Out**: a coding agent where a session is a tree, not a chat log. Branch into multiple approaches at once, backtrack to any past node without losing where you are now, and ask a past decision a question without disturbing your current state. It's a full-stack project — React, FastAPI, the Claude API, a real git-based merge engine — and it's also a VS Code extension, so it works the way Copilot does: select code, ask, watch the change land in your real file.

## The problem with "just scroll up"

Say you ask an assistant to implement a rate limiter two different ways — token bucket vs. sliding window — so you can compare them. In a linear chat, you either run two separate conversations (and lose the shared context between them) or you ask twice in the same thread and the second answer buries the first. Neither gives you what you actually want: both approaches, side by side, with the freedom to pick one, go back to the other, or ask either of them a follow-up without disturbing what you're currently looking at.

A tree solves this for free. Branch-Out's `TreeNode` is append-only — once a node exists, it's never rewritten. A branch creates new children; backtracking just moves a cursor. Nothing is ever lost, it's just dimmed (orphaned) until you click back to it.

## What actually surprised me

I expected the hard part to be the branching UI. It wasn't — React Flow handles tree layout fine once you fix node sizing (more on that below). The hard parts were all about **trust**: once a tool can write to your real files, every shortcut you'd normally take becomes a liability.

**The model needs to know what it doesn't know.** Early on, a quick-pick command asked the model to "summarize this file" without ever actually showing it the file's content — just a file path as metadata. The model, having nothing real to work from, hallucinated a destructive rewrite onto an unrelated file. The fix was obvious in hindsight (always send the real selected text, not just its coordinates) but the bug itself was a good reminder: an LLM asked to act on something it was never actually shown will happily make something up rather than say "I can't see that."

**Your own stated intent beats inferred intent.** I built an auto-classifier that lets the model decide for itself whether a prompt is a question or a code change. A user caught a real bug: the UI's intent toggle defaulted to "Question" and forced that framing even when the model itself noticed the mismatch ("this sounds like a code change request... however the system is treating this as a question"). The fix wasn't to trust the model's judgment unconditionally — it was a three-way toggle: Auto (model classifies itself, no bias either way), or an explicit Question/Code-change override that always wins. The user's own stated intent is cheaper and more reliable than anything inferred from text, even when the model is "right."

**A pixel-perfect bug can hide in plain sight.** The tree's connecting lines looked slanted even between a parent and its single child — which should always be a straight vertical line by the layout math. The actual cause: node cards had a content-dependent width (150–210px), so two logically-centered nodes still had visually different centers once React Flow's connector handles centered on their *rendered* width. The fix was a flat, fixed card width. Nothing about the tree logic was wrong; the pixels just didn't match the math.

## The benchmark finding I didn't expect

I ran a real benchmark comparing three things against the same 15 coding tasks: a raw, unguided prompt; the same context Branch-Out itself gets but as one single call (no branching); and the full branch-and-auto-pick-the-best flow. The surprising result wasn't that Branch-Out won — it was *why* the single-call baseline lost so badly on trivial tasks like binary search.

The single-call baseline asks for a compact, single-line JSON response: `{"code": "...", "reasoning": "..."}`. Forcing the model to cram multi-line code into an escaped JSON string measurably degraded its own code quality — I captured a real response where the entire function body collapsed to uniform one-space indentation regardless of nesting depth, a plain `IndentationError`. The same model, asked the same question with a free-form markdown response, got it right every time.

That's the honest case for branching: it's not that one LLM call is "worse" at coding. It's that any single attempt is fragile to failure modes that have nothing to do with the model's actual coding ability — a formatting quirk, an off day, a misread instruction — and having three independent attempts, scored automatically, recovers from exactly that kind of failure. Branch-Out matched the free-form baseline's ceiling and beat the matched one by 4x, on a task set with nothing to do with JSON formatting at all.

## What's next

The project has an explicit paper trail — every phase, every real bug, every design decision with its reasoning is logged as it happened, not reconstructed after the fact. If you're curious about the mechanics (the append-only tree store, the async branch generation, the git-based conflict detection for file writes, the live VS Code ↔ backend session sharing), the repo's `ROADMAP.md` has the whole build, warts included.

---

*Branch-Out is open source at [repo link]. Try it as a VS Code extension, or run the standalone web app locally — both talk to the same backend, same tree, same session.*
