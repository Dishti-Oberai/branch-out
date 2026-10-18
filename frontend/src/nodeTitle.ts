import type { TreeNode } from "./types";

// TreeNode has no dedicated "reasoning" field — it's folded into
// context_summary as "Task: ...\nApproach: {reasoning}" per turn (see
// backend/orchestrator.py's _extend_summary). Derive a short display title
// from the most recent "Approach:" segment rather than showing the generic
// "Branch A/B/C" label as the only name. branch_label itself stays exactly
// as-is elsewhere (status bar, API, tests) — this is presentation only.
//
// Real bug (2026-09-27, found via a manual test): this used to split the
// whole context_summary on "\n---\n" (the turn separator) and take the
// last chunk. A long question-mode answer routinely contains its own
// markdown horizontal rules -- also "\n---\n" -- as section dividers; one
// real answer had 5 of them. Splitting on that collided with the model's
// own formatting, so ".pop()" grabbed whatever came after the LAST
// coincidental "---" inside the answer's own prose, which usually has no
// "Approach:" label left in it at all -- match failed, silently fell back
// to the generic branch_label as if this were the real title. Fixed by
// never splitting on "\n---\n" at all: find the LAST "\nApproach: " marker
// directly (our own template guarantees exactly one per real turn,
// immediately after "Task: ...") and take everything after it, which is
// robust to however much "---" the model's own answer happens to contain.
export function deriveNodeTitle(node: TreeNode): string | null {
  if (!node.context_summary) return null;
  const marker = "\nApproach: ";
  const idx = node.context_summary.lastIndexOf(marker);
  if (idx === -1) return null;
  const text = node.context_summary.slice(idx + marker.length).trim();
  if (!text) return null;

  const firstSentence = text.split(/(?<=[.!?])\s+/)[0] || text;
  const words = firstSentence.split(/\s+/);
  const short = words.slice(0, 6).join(" ");
  return short.length < firstSentence.length || words.length > 6 ? `${short}…` : short;
}

// 2026-10-17: a dedicated "just the answer" section for a node's response,
// separate from "Context" (the full accumulated Task/Approach history,
// which gets long and noisy across many turns). Same extraction as
// deriveNodeTitle's marker-finding above, but returns the full text
// instead of a truncated title.
export function deriveLatestAnswer(node: TreeNode): string | null {
  if (!node.context_summary) return null;
  const marker = "\nApproach: ";
  const idx = node.context_summary.lastIndexOf(marker);
  if (idx === -1) return null;
  const text = node.context_summary.slice(idx + marker.length).trim();
  return text || null;
}
