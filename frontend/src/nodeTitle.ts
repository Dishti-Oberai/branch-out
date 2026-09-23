import type { TreeNode } from "./types";

// TreeNode has no dedicated "reasoning" field — it's folded into
// context_summary as "Task: ...\nApproach: {reasoning}" per turn (see
// backend/orchestrator.py's _extend_summary). Derive a short display title
// from the most recent "Approach:" segment rather than showing the generic
// "Branch A/B/C" label as the only name. branch_label itself stays exactly
// as-is elsewhere (status bar, API, tests) — this is presentation only.
export function deriveNodeTitle(node: TreeNode): string | null {
  if (!node.context_summary) return null;
  const lastSegment = node.context_summary.split("\n---\n").pop() ?? "";
  const match = lastSegment.match(/Approach:\s*([\s\S]+)/);
  if (!match) return null;
  const text = match[1].trim();
  if (!text) return null;

  const firstSentence = text.split(/(?<=[.!?])\s+/)[0] || text;
  const words = firstSentence.split(/\s+/);
  const short = words.slice(0, 6).join(" ");
  return short.length < firstSentence.length || words.length > 6 ? `${short}…` : short;
}
